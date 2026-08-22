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

    def _strategy_file(self, name, timescale):
        """A minimal on-disk strategy file so the freshness backstop engages
        (it is skipped when no file exists)."""
        from trading.strategies import files as strat_files
        d = strat_files.strategies_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(
            "---\n"
            f"name: {name}\nstatus: test\ntimescale: {timescale}\n"
            "mechanism_family: flow\nsymbol: BTC\n"
            "data_points:\n  - name: hl_cvd\n    params: {symbol: BTC}\n"
            "---\n\n## Hypothesis\nx\n", encoding="utf-8")

    def test_freshness_backstop_is_timescale_aware(self, monkeypatch):
        import trading.perception.cache as cache_mod
        from trading.perception import cache as c
        conn = get_db()
        now = time.time()
        state = {"data_points": {
            c._canonical_key("hl_cvd", {"symbol": "BTC"}):
                {"fetched_at": now - 2000}}}   # 33 min old
        monkeypatch.setattr(cache_mod, "read_perception_state", lambda: state)
        self._patch(monkeypatch)
        for name, ts in (("fi", "intraday"), ("fs", "swing")):
            conn.execute(
                "INSERT INTO strategies (name,file_path,status,timescale,"
                "mechanism_family,created_at,updated_at) VALUES "
                "(?,?,'test',?,'flow',0,0)", (name, f"{name}.md", ts))
            conn.commit()
            self._strategy_file(name, ts)
        # 33-min-old reading: refused for the intraday book (floor 30 min) —
        # and the refusal instructs a narrow refresh + re-draft, not a sweep
        r = _call("register_prediction", {**self._ARGS, "strategy_name": "fi"})
        assert "stale perception data" in r["error"]
        assert "force_fresh" in r["error"] and "RE-DRAFT" in r["error"]
        # the same reading is FRESH for a swing book (floor 4 h) — registers
        r = _call("register_prediction", {**self._ARGS, "strategy_name": "fs"})
        assert r.get("ok"), r

    def test_pilot_test_strategy_enqueues_keyed_wake(self, monkeypatch):
        from tests.trading.conftest import arm_pilot
        arm_pilot()
        conn = get_db()
        conn.execute(
            "INSERT INTO strategies (name,file_path,status,timescale,"
            "mechanism_family,created_at,updated_at) VALUES "
            "('pw','pw.md','test','intraday','flow',0,0)")
        conn.commit()
        wakes = self._patch(monkeypatch)
        res = _call("register_prediction", {**self._ARGS, "strategy_name": "pw"})
        assert res["ok"] and res["fundable_wake"] is True
        assert len(wakes) == 1
        # the pilot lane's wake opts into keyed backoff — a beat can register
        # ten of these, and main needs one nudge, not ten
        assert wakes[0]["key"] == "fundable:pilot"


class TestSupportScoreCanonicalization:
    """Repairs from the 2026-07-16 audit: canonical DP keys, declared weights
    pinned server-side, conviction recomputed by the engine (never the
    agent's transcription)."""

    _ARGS = {"claim": "x", "symbol": "BTC", "horizon_hours": 12,
             "near_edge_pct": 5.0, "far_edge_pct": 10.0, "conviction": 0.99}

    def _seed_file(self, conn):
        from trading.strategies import loader
        from trading.strategies.files import Strategy, strategies_dir
        s = Strategy(
            name="canon", status="test", timescale="intraday",
            mechanism_family="flow", file_path=strategies_dir() / "canon.md",
            data_points=[
                {"name": "hl_cvd", "params": {"interval": "1h", "symbol": "BTC"},
                 "weight": 0.6},
                {"name": "ta_rsi", "params": {"interval": "1h", "symbol": "BTC"},
                 "weight": 0.4},
            ],
            body_md="\n# Hypothesis\nh\n\n# Mechanism\nm\n",
        )
        loader.write_strategy(s, conn)

    def test_bare_keys_canonicalized_and_conviction_recomputed(self, monkeypatch):
        monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100000.0)
        conn = get_db()
        self._seed_file(conn)
        res = _call("register_prediction", {
            **self._ARGS, "strategy_name": "canon",
            "support_scores": [
                {"data_point": "hl_cvd", "score": 0.9, "kind": "numerical"},
                {"data_point": "ta_rsi(1h)", "score": 0.5, "kind": "narrative",
                 "reasoning_md": "neutral RSI"},
            ]})
        assert res["ok"], res
        # engine: (0.6*0.9 + 0.4*0.5) / 1.0 = 0.74 — NOT the stated 0.99
        assert res["conviction"] == pytest.approx(0.74)
        assert res["conviction_source"] == "engine"
        rows = conn.execute(
            "SELECT data_point, weight FROM support_scores WHERE prediction_id=?",
            (res["prediction_id"],)).fetchall()
        stored = {r["data_point"]: r["weight"] for r in rows}
        assert stored == {"hl_cvd(interval=1h,symbol=BTC)": 0.6,
                          "ta_rsi(interval=1h,symbol=BTC)": 0.4}
        conv = conn.execute("SELECT conviction FROM predictions WHERE id=?",
                            (res["prediction_id"],)).fetchone()[0]
        assert conv == pytest.approx(0.74)

    def test_unresolvable_key_refused(self, monkeypatch):
        monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100000.0)
        conn = get_db()
        self._seed_file(conn)
        res = _call("register_prediction", {
            **self._ARGS, "strategy_name": "canon",
            "support_scores": [
                {"data_point": "ta_nope", "score": 0.9, "kind": "numerical"}]})
        assert "error" in res and "ta_nope" in res["error"]

    def test_variants_collapsing_to_same_key_refused(self, monkeypatch):
        monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100000.0)
        conn = get_db()
        self._seed_file(conn)
        res = _call("register_prediction", {
            **self._ARGS, "strategy_name": "canon",
            "support_scores": [
                {"data_point": "hl_cvd", "score": 0.9, "kind": "numerical"},
                {"data_point": "hl_cvd(1h)", "score": 0.7, "kind": "numerical"}]})
        assert "error" in res and "duplicate" in res["error"]

    def test_no_scores_keeps_stated_conviction(self, monkeypatch):
        monkeypatch.setattr(RP, "_capture_entry_ref", lambda symbol: 100000.0)
        conn = get_db()
        self._seed_file(conn)
        res = _call("register_prediction", {**self._ARGS, "strategy_name": "canon"})
        assert res["ok"]
        assert res["conviction"] == pytest.approx(0.99)
        assert res["conviction_source"] == "as-stated"
