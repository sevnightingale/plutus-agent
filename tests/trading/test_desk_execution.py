"""desk_open_position / desk_close_position / rescore_position — the money path,
venue mocked. Execution is one deterministic derive path: prediction_id + thesis
→ gated, sized, placed, verified, naked-aborted."""

import json
import time

import pytest

import trading.dispatchers.desk_execution  # noqa: F401 — registers on import
from harness.tools.registry import registry as tool_registry
from trading.lifecycle import queries, write
from trading.lifecycle.db import get_db


def _call(name, args):
    return json.loads(tool_registry.get_entry(name).handler(args))


def _seed_strategy(name, status, book):
    """Insert a strategy with a resolved book + return a fresh OPEN prediction.

    book: list of (far, outcome, mae, reached_far). reached_far = tagged TP (win).
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO strategies (name,file_path,status,timescale,"
        "mechanism_family,created_at,updated_at) VALUES "
        "(?,?,?,'intraday','flow',0,0)", (name, f"{name}.md", status))
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
    open_pid = write.record_prediction(conn, write.PredictionDraft(
        claim_md="live", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
        near_edge_pct=1.5, far_edge_pct=3.0, conviction=0.72,
        agent="plutus-predict", symbol="BTC", strategy_name=name, kind="strategy"))
    conn.commit()
    return open_pid


_TRADEABLE_BOOK = [(3.0, "correct", -0.3, True)] * 12 + [(3.0, "wrong", -2.0, False)] * 4
_MIRAGE_BOOK = [(1.0, "correct", -3.0, False)] * 16   # floor-correct, never tags far


@pytest.fixture()
def mock_venue(monkeypatch):
    """Mocks the venue + live price so the derive path runs end to end."""
    calls = {}

    def fake_place(**kw):
        calls["place"] = kw
        return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                "fill_id": "f1", "slippage_bp": 4.0,
                "sl_order_id": "sl9", "tp_order_id": "tp9", "bracket_warnings": []}

    def fake_close(**kw):
        calls["close"] = kw
        return {"fill_price": 101_000.0, "size": 0.035, "order_id": "o2",
                "slippage_bp": 3.0, "cancel_warnings": []}

    import trading.dispatchers.desk_execution as mod
    import trading.integrations.hyperliquid.venue as venue
    monkeypatch.setattr(venue, "hl_place_order", fake_place)
    monkeypatch.setattr(venue, "hl_close_position", fake_close)
    monkeypatch.setattr(venue, "hl_account_state", lambda **k: {"equity_usd": 1000.0})
    monkeypatch.setattr(mod, "_fresh_price", lambda symbol: 100_000.0)
    return calls


class TestOpen:
    def test_full_chain_derives_and_places(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "flow long"})
        assert r["ok"], r
        assert r["sizing"]["mode"] == "risk_based"
        assert r["sl"]["price"] < 100_000.0 < r["tp"]["price"]   # long: SL below, TP (far) above
        assert r["sl"]["on_venue"] is True
        assert r["sizing"]["risk_budget"] == 0.07                # conviction 0.72 band
        assert mock_venue["place"]["slippage"] == 0.003          # ±0.3% cap
        assert r["sizing"]["leverage"] is not None
        pos = queries.open_position(get_db())
        assert pos["id"] == r["position_id"]
        assert pos["thesis"]["prediction_id"] == pid

    def test_one_position_law(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        assert _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})["ok"]
        second = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "one at a time" in second["error"]

    def test_requires_live_prediction(self, mock_venue):
        r = _call("desk_open_position", {"prediction_id": 9999, "thesis_md": "t"})
        assert "does not exist" in r["error"]

    def test_resolved_prediction_refused(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        write.resolve_prediction(get_db(), pid, "correct", resolved_by="plutus-ops")
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "already resolved" in r["error"]

    def test_stale_prediction_refused(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        conn = get_db()
        conn.execute("UPDATE predictions SET ts=? WHERE id=?", (time.time() - 3600, pid))
        conn.commit()
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and "stale" in r["refused"]

    def test_equity_failure_refuses(self, mock_venue, monkeypatch):
        # Risk-based sizing NEEDS equity — a failed read blocks the open (honest absence).
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.integrations.hyperliquid.venue as venue

        def boom(**kw):
            raise RuntimeError("hl down")

        monkeypatch.setattr(venue, "hl_account_state", boom)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "cannot size" in r["error"] and "hl down" in r["error"]


class TestExpectancyGate:
    def test_refuses_non_tradeable_strategy(self, mock_venue):
        pid = _seed_strategy("mirage", "active", _MIRAGE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "x"})
        assert r["ok"] is False and r["refused"] == "strategy not tradeable"
        assert queries.open_position(get_db()) is None


class TestNakedAbort:
    def test_naked_position_auto_closes(self, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue
        closed = {}

        def fake_place(**kw):                 # bracket leg rejected: NO sl_order_id
            return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                    "fill_id": "f1", "slippage_bp": 4.0, "sl_order_id": None,
                    "tp_order_id": None, "bracket_warnings": ["sl leg rejected"]}

        def fake_close(**kw):
            closed.update(kw)
            return {"fill_price": 100_400.0, "size": 0.035, "cancel_warnings": []}

        monkeypatch.setattr(venue, "hl_place_order", fake_place)
        monkeypatch.setattr(venue, "hl_close_position", fake_close)
        monkeypatch.setattr(venue, "hl_account_state", lambda **k: {"equity_usd": 1000.0})
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)

        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False
        assert "naked_position" in r["aborted_reason"]
        assert closed                                    # auto-close fired
        assert queries.open_position(get_db()) is None   # flat after abort


class TestClose:
    def test_close_computes_outcome(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        opened = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        pos_id = opened["position_id"]
        write.record_evaluation(get_db(), position_id=pos_id, conviction=0.7,
                                agent="plutus-main")
        closed = _call("desk_close_position", {"position_id": pos_id, "exit_reason": "tp"})
        assert closed["ok"], closed
        assert closed["outcome"]["realized_pnl_usd"] is not None
        assert queries.open_position(get_db()) is None
        bands = queries.sizing_performance(get_db())
        assert bands[0]["conviction_band"] == pytest.approx(0.7)   # opened at conviction 0.72
        assert bands[0]["n"] == 1

    def test_close_wrong_position_refused(self, mock_venue):
        result = _call("desk_close_position", {"position_id": 777, "exit_reason": "tp"})
        assert "not the open position" in result["error"]


class TestRescore:
    def _open(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)   # entry conviction 0.72
        return _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})["position_id"]

    def test_exit_on_conviction_decay(self, mock_venue, monkeypatch):
        pos_id = self._open(mock_venue)
        import trading.dispatchers.predict_tools as pt
        monkeypatch.setattr(pt, "score_strategy",
                            lambda name, regime=None: {"conviction": 0.30, "support_scores": []})
        r = _call("rescore_position", {"position_id": pos_id})
        assert r["ok"] and r["recommended_action"] == "exit_now"

    def test_hold_when_conviction_holds(self, mock_venue, monkeypatch):
        pos_id = self._open(mock_venue)
        import trading.dispatchers.predict_tools as pt
        monkeypatch.setattr(pt, "score_strategy",
                            lambda name, regime=None: {"conviction": 0.75, "support_scores": []})
        r = _call("rescore_position", {"position_id": pos_id})
        assert r["ok"] and r["recommended_action"] == "hold"
