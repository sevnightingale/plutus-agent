"""Tests for the six core registries (data_point, event, venue, account,
alert, identity).

Each registry is small and uniform; one test class per registry covering:
register, lookup, list_all (with filters where applicable), duplicate raises,
reset clears.
"""

import pytest

from harness.tools.core import (
    account_registry,
    alert_registry,
    data_point_registry,
    event_registry,
    identity_registry,
    venue_registry,
)


# Each test starts from a clean per-registry state.
@pytest.fixture(autouse=True)
def _reset_all():
    data_point_registry.reset()
    event_registry.reset()
    venue_registry.reset()
    account_registry.reset()
    alert_registry.reset()
    identity_registry.reset()
    yield
    data_point_registry.reset()
    event_registry.reset()
    venue_registry.reset()
    account_registry.reset()
    alert_registry.reset()
    identity_registry.reset()


# =========================================================================
# data_point_registry
# =========================================================================

class TestDataPointRegistry:
    def test_register_decorator_returns_fn_unchanged(self):
        @data_point_registry.register_data_point(
            name="x_price",
            category="market",
            source="x_source",
            description="X price",
        )
        def get_x_price():
            return {"price": 1.0}

        assert get_x_price() == {"price": 1.0}

    def test_lookup_after_register(self):
        @data_point_registry.register_data_point(
            name="hl_price",
            category="market",
            source="hyperliquid",
            description="Mark price",
            params_schema={"symbol": {"type": "string", "required": True}},
            returns_schema={"price": "float"},
            tags=["perp"],
        )
        def _fn(symbol):
            return {"price": 70_000.0, "symbol": symbol}

        entry = data_point_registry.lookup("hl_price")
        assert entry.name == "hl_price"
        assert entry.category == "market"
        assert entry.source == "hyperliquid"
        assert "perp" in entry.tags
        assert entry.fn("BTC") == {"price": 70_000.0, "symbol": "BTC"}

    def test_lookup_missing_raises(self):
        with pytest.raises(KeyError):
            data_point_registry.lookup("nonexistent")

    def test_duplicate_raises(self):
        @data_point_registry.register_data_point(
            name="dup", category="market", source="src", description="d"
        )
        def _a(): pass

        with pytest.raises(data_point_registry.RegistryError):
            @data_point_registry.register_data_point(
                name="dup", category="market", source="src", description="d2"
            )
            def _b(): pass

    def test_list_all_filters(self):
        @data_point_registry.register_data_point(
            name="p1", category="market", source="hyperliquid", description=""
        )
        def _p1(): pass

        @data_point_registry.register_data_point(
            name="p2", category="on_chain", source="hyperliquid", description=""
        )
        def _p2(): pass

        @data_point_registry.register_data_point(
            name="p3", category="market", source="acp", description=""
        )
        def _p3(): pass

        assert {e.name for e in data_point_registry.list_all()} == {"p1", "p2", "p3"}
        assert {e.name for e in data_point_registry.list_all(category="market")} == {"p1", "p3"}
        assert {e.name for e in data_point_registry.list_all(source="hyperliquid")} == {"p1", "p2"}
        assert {e.name for e in data_point_registry.list_all(category="market", source="acp")} == {"p3"}


# =========================================================================
# event_registry
# =========================================================================

class TestEventRegistry:
    def test_register_and_lookup(self):
        @event_registry.register_event(
            name="thesis",
            description="A market thesis.",
            fields_schema={"text": {"type": "string", "required": True}},
        )
        def _record(session_id, text):
            return {"id": 1, "text": text}

        entry = event_registry.lookup("thesis")
        assert entry.name == "thesis"
        assert "text" in entry.fields_schema
        assert entry.fn(session_id=None, text="hi") == {"id": 1, "text": "hi"}

    def test_duplicate_raises(self):
        @event_registry.register_event(name="x", description="")
        def _a(): pass
        with pytest.raises(event_registry.RegistryError):
            @event_registry.register_event(name="x", description="")
            def _b(): pass

    def test_list_all_sorted(self):
        for n in ("zebra", "alpha", "mango"):
            event_registry.register_event(name=n, description="")(lambda: None)
        names = [e.name for e in event_registry.list_all()]
        assert names == ["alpha", "mango", "zebra"]


# =========================================================================
# venue_registry
# =========================================================================

class TestVenueRegistry:
    def test_register_and_lookup(self):
        def _po(**kw): return {"order_id": "o1"}
        def _cp(**kw): return {"closed": True}

        venue_registry.register_venue(
            name="hyperliquid",
            description="HL perps",
            place_order_fn=_po,
            close_position_fn=_cp,
        )
        entry = venue_registry.lookup("hyperliquid")
        assert entry.name == "hyperliquid"
        assert entry.place_order_fn is _po
        assert entry.close_position_fn is _cp
        assert entry.modify_order_fn is None

    def test_duplicate_raises(self):
        venue_registry.register_venue(name="v", description="")
        with pytest.raises(venue_registry.RegistryError):
            venue_registry.register_venue(name="v", description="")

    def test_list_all_sorted(self):
        venue_registry.register_venue(name="bybit", description="")
        venue_registry.register_venue(name="aaa", description="")
        venue_registry.register_venue(name="hyperliquid", description="")
        assert [v.name for v in venue_registry.list_all()] == ["aaa", "bybit", "hyperliquid"]


# =========================================================================
# account_registry
# =========================================================================

class TestAccountRegistry:
    def test_register_and_lookup(self):
        account_registry.register_account(
            name="hl_trading",
            purpose="trading_capital",
            venue="hyperliquid",
            description="The $25 risk capital account",
        )
        a = account_registry.lookup("hl_trading")
        assert a.purpose == "trading_capital"
        assert a.venue == "hyperliquid"

    def test_invalid_purpose_raises(self):
        with pytest.raises(account_registry.RegistryError):
            account_registry.register_account(name="x", purpose="not_a_real_purpose")

    def test_duplicate_raises(self):
        account_registry.register_account(name="dup", purpose="treasury")
        with pytest.raises(account_registry.RegistryError):
            account_registry.register_account(name="dup", purpose="treasury")

    def test_list_filters(self):
        account_registry.register_account(name="a1", purpose="trading_capital", venue="hyperliquid")
        account_registry.register_account(name="a2", purpose="treasury", chain="base")
        account_registry.register_account(name="a3", purpose="cold_storage", chain="ethereum")
        account_registry.register_account(name="a4", purpose="trading_capital", venue="bybit")

        assert {a.name for a in account_registry.list_all(purpose="trading_capital")} == {"a1", "a4"}
        assert {a.name for a in account_registry.list_all(venue="hyperliquid")} == {"a1"}
        assert {a.name for a in account_registry.list_all(chain="base")} == {"a2"}


# =========================================================================
# alert_registry
# =========================================================================

class TestAlertRegistry:
    def test_register_and_lookup(self):
        @alert_registry.register_alert(
            name="position_status_change",
            source="hyperliquid",
            throttle_seconds=30,
            description="HL position open/close fires",
        )
        def _poll():
            return [{"alert": "position_status_change"}]

        e = alert_registry.lookup("position_status_change")
        assert e.source == "hyperliquid"
        assert e.throttle_seconds == 30
        assert e.poll_fn() == [{"alert": "position_status_change"}]

    def test_duplicate_raises(self):
        @alert_registry.register_alert(name="x", source="src")
        def _a(): return []
        with pytest.raises(alert_registry.RegistryError):
            @alert_registry.register_alert(name="x", source="src")
            def _b(): return []

    def test_list_all_filter_by_source(self):
        @alert_registry.register_alert(name="a", source="hyperliquid")
        def _f1(): return []
        @alert_registry.register_alert(name="b", source="acp")
        def _f2(): return []
        @alert_registry.register_alert(name="c", source="hyperliquid")
        def _f3(): return []

        assert {e.name for e in alert_registry.list_all(source="hyperliquid")} == {"a", "c"}


# =========================================================================
# identity_registry
# =========================================================================

class TestIdentityRegistry:
    def test_register_and_lookup(self):
        identity_registry.register_identity_system(
            name="acp", description="Virtuals ACP"
        )
        e = identity_registry.lookup("acp")
        assert e.description == "Virtuals ACP"

    def test_duplicate_raises(self):
        identity_registry.register_identity_system(name="x", description="")
        with pytest.raises(identity_registry.RegistryError):
            identity_registry.register_identity_system(name="x", description="")

    def test_list_all_sorted(self):
        for n in ("acp", "abc", "zee"):
            identity_registry.register_identity_system(name=n, description="")
        assert [e.name for e in identity_registry.list_all()] == ["abc", "acp", "zee"]
