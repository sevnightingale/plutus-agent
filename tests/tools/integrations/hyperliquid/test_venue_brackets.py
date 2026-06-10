"""Atomic SL/TP bracket placement on the Hyperliquid venue layer.

Bracket entries use ``bulk_orders(grouping="normalTpsl")`` so the entry
order + protective trigger orders submit in one signed action — no naked
window between fill and protection. Tests stub the HL Exchange so we can
verify payload shape without hitting the network.

What we cover:
- entry-only call (no sl/tp) goes through the lean ``market_open`` path
- entry + SL: bulk_orders with two orders, SL on the close side, isMarket
- entry + TP: bulk_orders with two orders, TP on the close side
- entry + SL + TP: three orders, TP first then SL (matches normalize order)
- short-position bracket sides (SL above entry → buy-side close)
- price validation (rejects SL on profit side, TP on loss side)
- partial bracket failure surfaces in ``bracket_warnings`` (entry still
  succeeds; Plutus decides what to do)
- limit-entry + brackets falls back to entry-only with a warning
- close_position cancels tracked SL/TP order IDs before market_close
- close_position tolerates "already canceled / filled" benign errors
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from trading.integrations.hyperliquid import _client, venue


@pytest.fixture(autouse=True)
def _reset_singletons():
    _client.reset_singletons_for_tests()
    yield
    _client.reset_singletons_for_tests()


def _make_filled(avg_px, size, oid=12345):
    return {
        "filled": {
            "avgPx": str(avg_px),
            "totalSz": str(size),
            "oid": oid,
        }
    }


def _make_resting(oid):
    return {"resting": {"oid": oid}}


def _make_error(msg):
    return {"error": msg}


def _wrap_statuses(statuses):
    return {"response": {"data": {"statuses": statuses}}}


def _patch_slippage_price(mock_ex, returned_px):
    mock_ex._slippage_price.return_value = returned_px


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class TestValidateBracketPrices:
    def test_long_sl_below_entry_ok(self):
        venue._validate_bracket_prices(side="long", entry_px=100.0, sl=95.0, tp=None)

    def test_long_sl_above_entry_rejected(self):
        with pytest.raises(ValueError, match="must be BELOW"):
            venue._validate_bracket_prices(side="long", entry_px=100.0, sl=105.0, tp=None)

    def test_long_tp_above_entry_ok(self):
        venue._validate_bracket_prices(side="long", entry_px=100.0, sl=None, tp=110.0)

    def test_long_tp_below_entry_rejected(self):
        with pytest.raises(ValueError, match="must be ABOVE"):
            venue._validate_bracket_prices(side="long", entry_px=100.0, sl=None, tp=95.0)

    def test_short_sl_above_entry_ok(self):
        venue._validate_bracket_prices(side="short", entry_px=100.0, sl=105.0, tp=None)

    def test_short_sl_below_entry_rejected(self):
        with pytest.raises(ValueError, match="must be ABOVE"):
            venue._validate_bracket_prices(side="short", entry_px=100.0, sl=95.0, tp=None)

    def test_short_tp_below_entry_ok(self):
        venue._validate_bracket_prices(side="short", entry_px=100.0, sl=None, tp=90.0)

    def test_short_tp_above_entry_rejected(self):
        with pytest.raises(ValueError, match="must be BELOW"):
            venue._validate_bracket_prices(side="short", entry_px=100.0, sl=None, tp=105.0)

    def test_equal_to_entry_rejected(self):
        with pytest.raises(ValueError):
            venue._validate_bracket_prices(side="long", entry_px=100.0, sl=100.0, tp=None)

    def test_none_passes(self):
        venue._validate_bracket_prices(side="long", entry_px=100.0, sl=None, tp=None)


# ---------------------------------------------------------------------------
# bracket_limit_px direction
# ---------------------------------------------------------------------------

class TestBracketLimitPx:
    def test_sell_close_lowers_limit(self):
        # Long entry → close is sell → accept LOWER prices than trigger.
        px = venue._bracket_limit_px(trigger_px=100.0, is_buy_close=False, slippage=0.05)
        assert px == 95.0

    def test_buy_close_raises_limit(self):
        # Short entry → close is buy → accept HIGHER prices than trigger.
        px = venue._bracket_limit_px(trigger_px=100.0, is_buy_close=True, slippage=0.05)
        assert px == 105.0


# ---------------------------------------------------------------------------
# Entry-only path stays on market_open (no behavior change)
# ---------------------------------------------------------------------------

class TestEntryOnlyPath:
    def test_market_no_brackets_uses_market_open(self, monkeypatch):
        mock_ex = MagicMock()
        mock_ex.market_open.return_value = _wrap_statuses([_make_filled(80000.0, 0.01)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_place_order(symbol="BTC", side="long", size=0.01)
        assert res["fill_price"] == 80000.0
        # Critical: bulk_orders was NOT called (entry-only stays on market_open)
        mock_ex.bulk_orders.assert_not_called()
        mock_ex.market_open.assert_called_once()


# ---------------------------------------------------------------------------
# Atomic bracket placement via bulk_orders
# ---------------------------------------------------------------------------

class TestBracketBulkOrders:
    def test_long_with_sl_and_tp(self, monkeypatch):
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        mock_ex.bulk_orders.return_value = _wrap_statuses([
            _make_filled(80000.0, 0.01, oid=1001),
            _make_resting(1002),  # TP
            _make_resting(1003),  # SL
        ])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_place_order(
            symbol="BTC", side="long", size=0.01, sl=78000.0, tp=82000.0,
        )
        assert res["fill_price"] == 80000.0
        assert res["order_id"] == "1001"
        assert res["tp_order_id"] == "1002"
        assert res["sl_order_id"] == "1003"
        assert res["bracket_warnings"] == []

        mock_ex.bulk_orders.assert_called_once()
        orders, = mock_ex.bulk_orders.call_args.args
        kwargs = mock_ex.bulk_orders.call_args.kwargs
        assert kwargs["grouping"] == "normalTpsl"
        assert len(orders) == 3
        # Order 0: entry (long, not reduce_only)
        assert orders[0]["coin"] == "BTC"
        assert orders[0]["is_buy"] is True
        assert orders[0]["reduce_only"] is False
        assert "limit" in orders[0]["order_type"]
        # Order 1: TP (sell-side close, reduce_only, isMarket trigger).
        # limit_px is the worst-acceptable sell price — for a sell that's
        # the LOWER bound, so it sits below triggerPx.
        tp_order = orders[1]
        assert tp_order["is_buy"] is False
        assert tp_order["reduce_only"] is True
        assert tp_order["order_type"]["trigger"]["tpsl"] == "tp"
        assert tp_order["order_type"]["trigger"]["isMarket"] is True
        assert tp_order["order_type"]["trigger"]["triggerPx"] == 82000.0
        assert tp_order["limit_px"] < tp_order["order_type"]["trigger"]["triggerPx"]
        # Order 2: SL (sell-side close, reduce_only, isMarket trigger).
        # Same direction as TP — sell-side close → limit_px below trigger.
        sl_order = orders[2]
        assert sl_order["is_buy"] is False
        assert sl_order["reduce_only"] is True
        assert sl_order["order_type"]["trigger"]["tpsl"] == "sl"
        assert sl_order["order_type"]["trigger"]["isMarket"] is True
        assert sl_order["order_type"]["trigger"]["triggerPx"] == 78000.0
        assert sl_order["limit_px"] < sl_order["order_type"]["trigger"]["triggerPx"]

    def test_short_with_sl_and_tp(self, monkeypatch):
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        mock_ex.bulk_orders.return_value = _wrap_statuses([
            _make_filled(80000.0, 0.01),
            _make_resting(2001),  # TP
            _make_resting(2002),  # SL
        ])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        venue.hl_place_order(
            symbol="BTC", side="short", size=0.01, sl=82000.0, tp=78000.0,
        )
        orders, = mock_ex.bulk_orders.call_args.args
        # Entry: short → is_buy=False, not reduce_only
        assert orders[0]["is_buy"] is False
        assert orders[0]["reduce_only"] is False
        # TP closes a short → buy-side
        assert orders[1]["is_buy"] is True
        assert orders[1]["reduce_only"] is True
        # SL closes a short → buy-side
        assert orders[2]["is_buy"] is True
        assert orders[2]["reduce_only"] is True

    def test_only_sl_no_tp(self, monkeypatch):
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        mock_ex.bulk_orders.return_value = _wrap_statuses([
            _make_filled(80000.0, 0.01),
            _make_resting(3001),
        ])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_place_order(
            symbol="BTC", side="long", size=0.01, sl=78000.0,
        )
        orders, = mock_ex.bulk_orders.call_args.args
        assert len(orders) == 2
        # Index 0: entry, Index 1: SL (no TP)
        assert orders[1]["order_type"]["trigger"]["tpsl"] == "sl"
        assert res["sl_order_id"] == "3001"
        assert res["tp_order_id"] is None

    def test_only_tp_no_sl(self, monkeypatch):
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        mock_ex.bulk_orders.return_value = _wrap_statuses([
            _make_filled(80000.0, 0.01),
            _make_resting(4001),
        ])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_place_order(
            symbol="BTC", side="long", size=0.01, tp=82000.0,
        )
        orders, = mock_ex.bulk_orders.call_args.args
        assert len(orders) == 2
        assert orders[1]["order_type"]["trigger"]["tpsl"] == "tp"
        assert res["tp_order_id"] == "4001"
        assert res["sl_order_id"] is None

    def test_invalid_sl_side_rejected_pre_submission(self, monkeypatch):
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        with pytest.raises(ValueError, match="must be BELOW"):
            venue.hl_place_order(
                symbol="BTC", side="long", size=0.01, sl=82000.0,
            )
        # Did NOT submit (validation refused)
        mock_ex.bulk_orders.assert_not_called()

    def test_partial_bracket_failure_surfaces_warning(self, monkeypatch):
        """Entry filled but SL placement was rejected — entry stands, warning surfaces."""
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        mock_ex.bulk_orders.return_value = _wrap_statuses([
            _make_filled(80000.0, 0.01),
            _make_resting(5001),                    # TP placed
            _make_error("tick size violation"),     # SL rejected
        ])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_place_order(
            symbol="BTC", side="long", size=0.01, sl=78000.0, tp=82000.0,
        )
        # Entry still good
        assert res["fill_price"] == 80000.0
        # TP good
        assert res["tp_order_id"] == "5001"
        # SL failed — captured in warnings, no order_id
        assert res["sl_order_id"] is None
        assert any("SL" in w and "tick size" in w for w in res["bracket_warnings"])

    def test_entry_failure_raises(self, monkeypatch):
        """Entry rejected → no point inspecting brackets; raise."""
        mock_ex = MagicMock()
        _patch_slippage_price(mock_ex, 80000.0)
        mock_ex.bulk_orders.return_value = _wrap_statuses([
            _make_error("insufficient margin"),
            _make_resting(6001),
            _make_resting(6002),
        ])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        with pytest.raises(RuntimeError, match="insufficient margin"):
            venue.hl_place_order(
                symbol="BTC", side="long", size=0.01, sl=78000.0, tp=82000.0,
            )


# ---------------------------------------------------------------------------
# Limit-entry + brackets: graceful degradation
# ---------------------------------------------------------------------------

class TestLimitEntryWithBrackets:
    def test_limit_with_brackets_skips_auto_placement(self, monkeypatch):
        mock_ex = MagicMock()
        mock_ex.order.return_value = _wrap_statuses([_make_resting(7001)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        # Resting limit → _normalize_response would normally raise, but since
        # we want to hit the limit-entry-with-brackets path we need a fill.
        mock_ex.order.return_value = _wrap_statuses([_make_filled(80000.0, 0.01, oid=7001)])

        res = venue.hl_place_order(
            symbol="BTC", side="long", size=0.01, sl=78000.0,
            order_type="limit", limit_px=80000.0,
        )
        # Entry fills via ex.order (not bulk_orders)
        mock_ex.bulk_orders.assert_not_called()
        mock_ex.order.assert_called_once()
        assert res["fill_price"] == 80000.0
        # Bracket NOT auto-placed — warning surfaces
        assert res["sl_order_id"] is None
        assert res["tp_order_id"] is None
        assert any("limit-entry brackets" in w for w in res["bracket_warnings"])


# ---------------------------------------------------------------------------
# close_position cancels tracked brackets before market_close
# ---------------------------------------------------------------------------

class _FakeLifecycleDB:
    """Stub the lifecycle DB so we can hand-feed bracket order IDs."""
    def __init__(self, params_json):
        self._params_json = params_json
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        # Build minimal schema for the join _lookup_bracket_order_ids needs
        self._conn.executescript("""
            CREATE TABLE positions(id INTEGER PRIMARY KEY, opening_trade_id INTEGER);
            CREATE TABLE trades(id INTEGER PRIMARY KEY, decision_id INTEGER);
            CREATE TABLE decisions(id INTEGER PRIMARY KEY, params_json TEXT);
        """)
        # Seed: position 42 → trade 100 → decision 200 with our params_json
        self._conn.execute("INSERT INTO decisions(id, params_json) VALUES(200, ?)", (params_json,))
        self._conn.execute("INSERT INTO trades(id, decision_id) VALUES(100, 200)")
        self._conn.execute("INSERT INTO positions(id, opening_trade_id) VALUES(42, 100)")
        self._conn.commit()

    def conn(self):
        return self._conn


class TestClosePositionCancelsBrackets:
    def test_cancels_both_brackets_then_market_close(self, monkeypatch):
        params = json.dumps({
            "symbol": "BTC", "size": 0.01,
            "sl_order_id": "9001", "tp_order_id": "9002",
        })
        fake_db = _FakeLifecycleDB(params)
        monkeypatch.setattr(venue, "get_lifecycle_db", lambda: fake_db)

        mock_ex = MagicMock()
        mock_ex.cancel.return_value = _wrap_statuses([{"status": "ok"}])
        mock_ex.market_close.return_value = _wrap_statuses([_make_filled(80100.0, 0.01)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_close_position(symbol="BTC", position_id=42)
        # Both brackets cancelled
        assert mock_ex.cancel.call_count == 2
        cancel_args = [c.args for c in mock_ex.cancel.call_args_list]
        assert ("BTC", 9001) in cancel_args  # SL
        assert ("BTC", 9002) in cancel_args  # TP
        # Then market_close fired
        mock_ex.market_close.assert_called_once()
        # No warnings
        assert "cancel_warnings" not in res

    def test_no_brackets_tracked_skips_cancel(self, monkeypatch):
        params = json.dumps({"symbol": "BTC", "size": 0.01})  # no bracket IDs
        fake_db = _FakeLifecycleDB(params)
        monkeypatch.setattr(venue, "get_lifecycle_db", lambda: fake_db)

        mock_ex = MagicMock()
        mock_ex.market_close.return_value = _wrap_statuses([_make_filled(80100.0, 0.01)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        venue.hl_close_position(symbol="BTC", position_id=42)
        mock_ex.cancel.assert_not_called()
        mock_ex.market_close.assert_called_once()

    def test_benign_already_canceled_error_swallowed(self, monkeypatch):
        params = json.dumps({"sl_order_id": "9001", "tp_order_id": None})
        fake_db = _FakeLifecycleDB(params)
        monkeypatch.setattr(venue, "get_lifecycle_db", lambda: fake_db)

        mock_ex = MagicMock()
        # HL returns "Order was never placed, already canceled, or filled"
        # when the bracket already triggered.
        mock_ex.cancel.return_value = _wrap_statuses([
            {"error": "Order was never placed, already canceled, or filled."}
        ])
        mock_ex.market_close.return_value = _wrap_statuses([_make_filled(80100.0, 0.01)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_close_position(symbol="BTC", position_id=42)
        # Benign — no warning surfaced
        assert "cancel_warnings" not in res or not res.get("cancel_warnings")
        mock_ex.market_close.assert_called_once()

    def test_real_cancel_error_surfaces_warning(self, monkeypatch):
        params = json.dumps({"sl_order_id": "9001"})
        fake_db = _FakeLifecycleDB(params)
        monkeypatch.setattr(venue, "get_lifecycle_db", lambda: fake_db)

        mock_ex = MagicMock()
        mock_ex.cancel.return_value = _wrap_statuses([
            {"error": "Some unexpected wire error"}
        ])
        mock_ex.market_close.return_value = _wrap_statuses([_make_filled(80100.0, 0.01)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_close_position(symbol="BTC", position_id=42)
        assert "cancel_warnings" in res
        assert any("9001" in w for w in res["cancel_warnings"])
        # Close still proceeds despite the cancel issue
        mock_ex.market_close.assert_called_once()

    def test_cancel_exception_surfaces_warning(self, monkeypatch):
        params = json.dumps({"sl_order_id": "9001"})
        fake_db = _FakeLifecycleDB(params)
        monkeypatch.setattr(venue, "get_lifecycle_db", lambda: fake_db)

        mock_ex = MagicMock()
        mock_ex.cancel.side_effect = ConnectionError("RPC down")
        mock_ex.market_close.return_value = _wrap_statuses([_make_filled(80100.0, 0.01)])
        monkeypatch.setattr(venue, "get_exchange", lambda: mock_ex)

        res = venue.hl_close_position(symbol="BTC", position_id=42)
        assert any("RPC down" in w for w in res.get("cancel_warnings", []))
        # Close still proceeds
        mock_ex.market_close.assert_called_once()
