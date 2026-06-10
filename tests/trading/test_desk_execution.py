"""desk_open_position / desk_close_position — the money path, venue mocked."""

import json
import time

import pytest

import trading.dispatchers.desk_execution  # noqa: F401 — registers on import
from harness.tools.registry import registry as tool_registry
from trading.lifecycle import queries, write
from trading.lifecycle.db import get_db


def _call(name, args):
    return json.loads(tool_registry.get_entry(name).handler(args))


@pytest.fixture()
def funded_prediction():
    conn = get_db()
    conn.execute(
        "INSERT INTO predictions(ts, horizon_ts, timescale, symbol, claim_md, "
        "success_criteria_json, conviction, risk_tolerance) "
        "VALUES (?, ?, 'intraday', 'BTC', 'BTC to 110k', '{}', 0.72, 'med')",
        (time.time(), time.time() + 12 * 3600))
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return pid


@pytest.fixture()
def mock_venue(monkeypatch):
    calls = {}

    def fake_place(**kw):
        calls["place"] = kw
        return {"fill_price": 104100.0, "size": kw["size"], "order_id": "o1",
                "fill_id": "f1", "slippage_bp": 4.0,
                "sl_order_id": "sl9", "tp_order_id": None,
                "bracket_warnings": []}

    def fake_close(**kw):
        calls["close"] = kw
        return {"fill_price": 105200.0, "size": 0.001, "order_id": "o2",
                "slippage_bp": 3.0, "cancel_warnings": []}

    import trading.dispatchers.desk_execution as mod
    import trading.integrations.hyperliquid.venue as venue
    monkeypatch.setattr(venue, "hl_place_order", fake_place)
    monkeypatch.setattr(venue, "hl_close_position", fake_close)
    return calls


class TestOpen:
    def test_full_chain(self, funded_prediction, mock_venue):
        result = _call("desk_open_position", {
            "prediction_id": funded_prediction, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 102600.0, "thesis": "funding flush; stop 1 ATR",
            "conviction": 0.72,
        })
        assert result["ok"], result
        assert result["sl"]["on_venue"] is True
        assert mock_venue["place"]["sl"] == 102600.0

        pos = queries.open_position(get_db())
        assert pos["id"] == result["position_id"]
        assert pos["thesis"]["prediction_id"] == funded_prediction
        assert pos["thesis"]["sl_price"] == 102600.0

    def test_one_position_law(self, funded_prediction, mock_venue):
        first = _call("desk_open_position", {
            "prediction_id": funded_prediction, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 102600.0, "thesis": "t"})
        assert first["ok"]
        second = _call("desk_open_position", {
            "prediction_id": funded_prediction, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 102600.0, "thesis": "t"})
        assert "one at a time" in second["error"]

    def test_requires_live_prediction(self, mock_venue):
        result = _call("desk_open_position", {
            "prediction_id": 9999, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 1.0, "thesis": "t"})
        assert "does not exist" in result["error"]

    def test_resolved_prediction_refused(self, funded_prediction, mock_venue):
        write.resolve_prediction(get_db(), funded_prediction, "correct",
                                 resolved_by="plutus-ops")
        result = _call("desk_open_position", {
            "prediction_id": funded_prediction, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 1.0, "thesis": "t"})
        assert "already resolved" in result["error"]


class TestClose:
    def test_close_computes_outcome(self, funded_prediction, mock_venue):
        opened = _call("desk_open_position", {
            "prediction_id": funded_prediction, "symbol": "BTC", "side": "long",
            "size": 0.001, "sl": 102600.0, "thesis": "t", "conviction": 0.72})
        pos_id = opened["position_id"]
        write.record_evaluation(get_db(), position_id=pos_id, conviction=0.8,
                                agent="plutus-ops")

        closed = _call("desk_close_position", {
            "position_id": pos_id, "exit_reason": "tp"})
        assert closed["ok"], closed
        out = closed["outcome"]
        # long 0.001 from 104100 → 105200: +1.1/coin
        assert out["realized_pnl_usd"] == pytest.approx(1.1, abs=0.01)
        # R vs the 1500-wide stop: 1100/1500
        assert out["r_multiple"] == pytest.approx(0.733, abs=0.01)
        assert out["conviction_evaluations_count"] == 1
        assert queries.open_position(get_db()) is None

    def test_close_wrong_position_refused(self, mock_venue):
        result = _call("desk_close_position", {
            "position_id": 777, "exit_reason": "tp"})
        assert "not the open position" in result["error"]
