"""Issue 5 baseline — entry-delta recording, intrinsic RR, dropped-handoff query."""

import json
import time

import pytest

import trading.dispatchers.desk_execution  # noqa: F401 — registers on import
import trading.dispatchers.register_prediction as RP
from harness.tools.registry import registry as tool_registry
from trading.lifecycle import write
from trading.lifecycle.db import get_db


def _call(name, args):
    return json.loads(tool_registry.get_entry(name).handler(args))


@pytest.fixture()
def mock_venue(monkeypatch):
    def fake_place(**kw):
        return {"fill_price": 104100.0, "size": kw["size"], "order_id": "o1",
                "fill_id": "f1", "slippage_bp": 4.0,
                "sl_order_id": "sl9", "tp_order_id": None, "bracket_warnings": []}

    import trading.dispatchers.desk_execution as mod
    import trading.integrations.hyperliquid.venue as venue
    monkeypatch.setattr(venue, "hl_place_order", fake_place)
    monkeypatch.setattr(venue, "hl_account_state", lambda **kw: {"equity_usd": 1000.0})
    monkeypatch.setattr(mod, "_fresh_price", lambda symbol: 104000.0)


def _tradeable(conn, name):
    """An active strategy with a +EV resolved book (so the gate passes)."""
    conn.execute(
        "INSERT INTO strategies (name,file_path,status,timescale,"
        "mechanism_family,created_at,updated_at) VALUES "
        "(?,?, 'active','intraday','flow',0,0)", (name, f"{name}.md"))
    book = [(10.0, "correct", -1.0, True)] * 12 + [(10.0, "wrong", -6.0, False)] * 4
    for far, outcome, mae, reached in book:
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="z", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
            near_edge_pct=far / 2.0, far_edge_pct=far, conviction=0.72,
            agent="plutus-predict", symbol="BTC", strategy_name=name, kind="strategy"))
        write.resolve_prediction(conn, pid, outcome, resolved_by="r",
                                 realized_value={"mae_pct": mae})
        if reached:
            conn.execute("UPDATE predictions SET reached_far_at=? WHERE id=?",
                         (time.time(), pid))
    conn.commit()


class TestEntryDelta:
    def test_entry_delta_pct_on_decision_params(self, mock_venue):
        conn = get_db()
        _tradeable(conn, "ed")
        # open prediction with a known entry_ref; mock fill 104100 → delta vs 104000
        opid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="c", horizon_ts=time.time() + 12 * 3600, entry_ref_price=104_000.0,
            near_edge_pct=5.0, far_edge_pct=10.0, conviction=0.72,
            agent="plutus-predict", symbol="BTC", strategy_name="ed", kind="strategy"))
        conn.commit()

        res = _call("desk_open_position", {"prediction_id": opid, "thesis_md": "t"})
        assert res["ok"], res
        params = json.loads(conn.execute(
            "SELECT params_json FROM decisions WHERE action LIKE 'open%' "
            "ORDER BY id DESC LIMIT 1").fetchone()[0])
        # (104100 - 104000) / 104000 * 100 = 0.0962
        assert params["entry_delta_pct"] == pytest.approx(0.0962, abs=1e-3)


class TestIntrinsicRR:
    def test_register_returns_intrinsic_rr(self, monkeypatch):
        monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100000.0)
        res = _call("register_prediction", {
            "claim": "BTC drifts up", "symbol": "BTC", "horizon_hours": 12,
            "near_edge_pct": 5.0, "far_edge_pct": 10.0, "conviction": 0.6,
            "kind": "adhoc"})
        assert res["ok"]
        assert res["intrinsic_rr"] == pytest.approx(2.0)  # |10| / |5|


class TestFundableWake:
    """register_prediction nudges main when an ACTIVE strategy registers —
    the 20-min actionable window must not die to in-turn deferral (item G)."""

    _ARGS = {"claim": "x", "symbol": "BTC", "horizon_hours": 12,
             "near_edge_pct": 5.0, "far_edge_pct": 10.0, "conviction": 0.7}

    def _patch(self, monkeypatch):
        monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100000.0)
        import harness.wake_queue as wq
        wakes = []
        monkeypatch.setattr(wq, "enqueue", lambda **kw: wakes.append(kw) or kw)
        return wakes

    def test_active_strategy_enqueues_fundable_wake(self, monkeypatch):
        conn = get_db()
        _tradeable(conn, "aw")
        wakes = self._patch(monkeypatch)
        res = _call("register_prediction", {**self._ARGS, "strategy_name": "aw"})
        assert res["ok"] and res["fundable_wake"] is True
        assert len(wakes) == 1
        assert wakes[0]["reason"] == "schedule" and "aw" in wakes[0]["detail"]

    def test_test_strategy_registers_silently(self, monkeypatch):
        conn = get_db()
        conn.execute(
            "INSERT INTO strategies (name,file_path,status,timescale,"
            "mechanism_family,created_at,updated_at) VALUES "
            "('tw','tw.md','test','intraday','flow',0,0)")
        conn.commit()
        wakes = self._patch(monkeypatch)
        res = _call("register_prediction", {**self._ARGS, "strategy_name": "tw"})
        assert res["ok"] and res["fundable_wake"] is False
        assert res["strategy_capacity"] == {
            "strategy_name": "tw", "evidence_lane": "base",
            "open_predictions": 1, "open_cap": write.MAX_OPEN_PER_STRATEGY,
            "open_slots_remaining": write.MAX_OPEN_PER_STRATEGY - 1,
        }
        assert wakes == []
