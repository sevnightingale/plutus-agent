"""Issue 5 baseline — entry-delta recording, intrinsic RR, dropped-handoff query."""

import json
import sqlite3
import time

import pytest

import trading.dispatchers.desk_execution  # noqa: F401 — registers on import
import trading.dispatchers.register_prediction as RP
from harness.tools.registry import registry as tool_registry
from trading.lifecycle import queries
from trading.lifecycle.db import get_db


def _call(name, args):
    return json.loads(tool_registry.get_entry(name).handler(args))


@pytest.fixture()
def mock_venue(monkeypatch):
    def fake_place(**kw):
        return {"fill_price": 104100.0, "size": kw["size"], "order_id": "o1",
                "fill_id": "f1", "slippage_bp": 4.0,
                "sl_order_id": "sl9", "tp_order_id": None, "bracket_warnings": []}

    import trading.integrations.hyperliquid.venue as venue
    monkeypatch.setattr(venue, "hl_place_order", fake_place)
    monkeypatch.setattr(venue, "hl_account_state", lambda **kw: {"equity_usd": 1000.0})


class TestEntryDelta:
    def test_entry_delta_pct_on_decision_params(self, mock_venue):
        conn = get_db()
        # prediction with a known entry_ref_price; fill is 104100 (mock) → delta
        conn.execute(
            "INSERT INTO predictions(ts, horizon_ts, timescale, symbol, claim_md, "
            "entry_ref_price, near_edge_pct, far_edge_pct, success_criteria_json, "
            "conviction, risk_tolerance) VALUES (?, ?, 'intraday', 'BTC', 'c', "
            "104000.0, 5, 10, '{}', 0.72, 'med')",
            (time.time(), time.time() + 12 * 3600))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        res = _call("desk_open_position", {
            "prediction_id": pid, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 99000.0, "thesis": "t", "conviction": 0.72})
        assert res["ok"]
        params = json.loads(conn.execute(
            "SELECT params_json FROM decisions ORDER BY id DESC LIMIT 1").fetchone()[0])
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


class TestUnhandledActionable:
    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE predictions(id INT, strategy_name TEXT, symbol TEXT,
            timescale TEXT, conviction REAL, ts REAL, near_edge_pct REAL,
            far_edge_pct REAL, resolved_at REAL, kind TEXT);
        CREATE TABLE strategies(name TEXT, status TEXT);
        CREATE TABLE theses(prediction_id INT);
        CREATE TABLE observations(related_prediction_ids_json TEXT);
        INSERT INTO strategies VALUES ('sq','active'),('ts','test');
        """)
        now = time.time()
        old, recent = now - 7200, now - 60
        rows = [
            (1, 'sq', 'BTC', 'intraday', 0.62, old, 5, 10, None, 'strategy'),    # FLAG
            (2, 'sq', 'ETH', 'intraday', 0.62, old, 5, 10, None, 'strategy'),    # funded
            (3, 'sq', 'SOL', 'intraday', 0.62, old, 5, 10, None, 'strategy'),    # skipped
            (4, 'sq', 'BTC', 'intraday', 0.62, recent, 5, 10, None, 'strategy'),  # too recent
            (5, 'ts', 'BTC', 'intraday', 0.62, old, 5, 10, None, 'strategy'),    # not active
            (6, 'sq', 'BTC', 'intraday', 0.40, old, 5, 10, None, 'strategy'),    # low conviction
            (7, 'sq', 'BTC', 'intraday', 0.62, old, 5, 10, now, 'strategy'),     # resolved
        ]
        c.executemany("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        c.execute("INSERT INTO theses VALUES (2)")
        c.execute("INSERT INTO observations VALUES ('[3]')")
        return c

    def test_flags_only_dropped_handoff(self):
        res = queries.unhandled_actionable(self._conn(), min_age_s=5400, min_conviction=0.50)
        assert [r["id"] for r in res] == [1]

    def test_no_age_filter_still_excludes_handled(self):
        res = queries.unhandled_actionable(self._conn(), min_age_s=0, min_conviction=0.50)
        # ids 1 and 4 are both unfunded/unskipped/actionable; 2,3,5,6,7 excluded
        assert sorted(r["id"] for r in res) == [1, 4]
