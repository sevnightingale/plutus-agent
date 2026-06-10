"""End-to-end tests for the Phase 4a dispatcher tools.

Each test stubs the relevant registry, points the lifecycle.db singleton at
a temp file, dispatches via the registered tool's handler, and verifies the
resulting side effects in the temp DB.
"""

import json
import time

import pytest

from trading.lifecycle.db import LifecycleDB, get_lifecycle_db, reset_lifecycle_db_singleton
from trading.perception.core import (
    account_registry,
    data_point_registry,
    event_registry,
    identity_registry,
    venue_registry,
)
from harness.tools.registry import registry as tool_registry

# Ensure dispatcher modules are imported so they self-register at module top level.
import trading.dispatchers.account_state              # noqa: F401
import trading.dispatchers.cancel_order               # noqa: F401
import trading.dispatchers.close_position             # noqa: F401
import trading.dispatchers.fetch_data_point           # noqa: F401
import trading.dispatchers.list_accounts              # noqa: F401
import trading.dispatchers.list_data_points           # noqa: F401
import trading.dispatchers.list_event_types           # noqa: F401
import trading.dispatchers.list_identity_systems      # noqa: F401
import trading.dispatchers.list_venues                # noqa: F401
import trading.dispatchers.modify_order               # noqa: F401
import trading.dispatchers.place_order                # noqa: F401
import trading.dispatchers.record_event               # noqa: F401
import trading.dispatchers.record_observation         # noqa: F401


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Fresh registries + fresh lifecycle.db + fresh perception cache per test.

    HERMES_HOME is pointed at tmp_path so perception_state.json writes
    (V2: fetch_data_point now populates the cache) don't pollute the
    operator's real ~/.plutus-agent/perception_state.json.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    data_point_registry.reset()
    event_registry.reset()
    venue_registry.reset()
    account_registry.reset()
    identity_registry.reset()

    reset_lifecycle_db_singleton()
    db = get_lifecycle_db(db_path=tmp_path / "lifecycle.db")
    yield db
    reset_lifecycle_db_singleton()

    data_point_registry.reset()
    event_registry.reset()
    venue_registry.reset()
    account_registry.reset()
    identity_registry.reset()


def _call(tool_name: str, args: dict) -> dict:
    """Dispatch through the tool registry and parse the JSON tool result."""
    entry = tool_registry.get_entry(tool_name)
    assert entry is not None, f"tool '{tool_name}' not registered"
    raw = entry.handler(args)
    return json.loads(raw)


# =========================================================================
# perception
# =========================================================================

class TestFetchDataPoint:
    def test_fetches_and_auto_snapshots(self, _isolated):
        @data_point_registry.register_data_point(
            name="hl_funding", category="market", source="hyperliquid",
            description="funding rate", params_schema={"symbol": {"type": "string"}},
        )
        def fn(symbol):
            return {"rate": 0.0001, "symbol": symbol}

        result = _call("fetch_data_point", {"name": "hl_funding", "params": {"symbol": "BTC"}})
        assert result["snapshot_id"] >= 1
        assert result["value"] == {"rate": 0.0001, "symbol": "BTC"}
        assert result["source"] == "hyperliquid"

        row = _isolated.conn().execute(
            "SELECT name, source, params_json, value_json FROM data_point_snapshots WHERE id = ?",
            (result["snapshot_id"],),
        ).fetchone()
        assert row["name"] == "hl_funding"
        assert row["source"] == "hyperliquid"
        assert json.loads(row["params_json"]) == {"symbol": "BTC"}
        assert json.loads(row["value_json"]) == {"rate": 0.0001, "symbol": "BTC"}

    def test_unknown_data_point_returns_error(self, _isolated):
        result = _call("fetch_data_point", {"name": "no_such"})
        assert "error" in result or "not registered" in str(result)

    def test_missing_name_returns_error(self, _isolated):
        result = _call("fetch_data_point", {})
        assert "error" in result

    def test_cache_miss_then_hit_short_circuits(self, _isolated):
        """V2: second fetch within staleness budget reads the cache, not the source."""
        call_count = {"n": 0}

        @data_point_registry.register_data_point(
            name="hl_price", category="market", source="hyperliquid",
            description="price", params_schema={"symbol": {"type": "string"}},
        )
        def fn(symbol):
            call_count["n"] += 1
            return {"price": 70_000.0, "symbol": symbol}

        # First fetch — cache miss, fetcher called.
        first = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})
        assert first.get("cache") == "miss"
        assert call_count["n"] == 1
        assert first["value"]["price"] == 70_000.0

        # Second fetch within budget — cache hit, fetcher NOT called.
        second = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})
        assert second.get("cache") == "hit"
        assert call_count["n"] == 1, "fetcher should NOT be called on cache hit"
        assert second["value"]["price"] == 70_000.0
        assert second["source"] == "perception_cache:hyperliquid"
        assert second["age_s"] >= 0

    def test_force_fresh_bypasses_cache(self, _isolated):
        """V2: regime-detection and similar set force_fresh=True so they
        always read live state, even if a cache entry is fresh enough."""
        call_count = {"n": 0}

        @data_point_registry.register_data_point(
            name="hl_price", category="market", source="hyperliquid",
            description="price", params_schema={"symbol": {"type": "string"}},
        )
        def fn(symbol):
            call_count["n"] += 1
            return {"price": 70_000.0 + call_count["n"], "symbol": symbol}

        # Populate cache.
        _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})
        assert call_count["n"] == 1

        # force_fresh=True → bypass cache, fetcher called again.
        result = _call("fetch_data_point", {
            "name": "hl_price", "params": {"symbol": "BTC"}, "force_fresh": True,
        })
        assert result.get("cache") == "bypass"
        assert call_count["n"] == 2, "fetcher MUST be called when force_fresh=True"
        # Value reflects the second call's increment.
        assert result["value"]["price"] == 70_002.0

    def test_different_params_dont_collide_in_cache(self, _isolated):
        """Cache key uses canonical params — BTC and ETH cache separately."""
        call_count = {"n": 0}

        @data_point_registry.register_data_point(
            name="hl_price", category="market", source="hyperliquid",
            description="price", params_schema={"symbol": {"type": "string"}},
        )
        def fn(symbol):
            call_count["n"] += 1
            return {"price": {"BTC": 70_000.0, "ETH": 3_500.0}[symbol], "symbol": symbol}

        btc = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})
        eth = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "ETH"}})
        assert btc["value"]["price"] == 70_000.0
        assert eth["value"]["price"] == 3_500.0
        # Both should be misses (different params).
        assert btc.get("cache") == "miss"
        assert eth.get("cache") == "miss"
        assert call_count["n"] == 2

        # Re-reading BTC should hit cache.
        btc2 = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})
        assert btc2.get("cache") == "hit"
        assert call_count["n"] == 2

    def test_snapshot_recorded_on_both_hit_and_miss(self, _isolated):
        """Auto-snapshot is unconditional — cache hit still records a snapshot
        (with `source='perception_cache:<orig>'` so lifecycle queries can
        distinguish fresh vs cached fetches)."""
        @data_point_registry.register_data_point(
            name="hl_price", category="market", source="hyperliquid",
            description="price", params_schema={"symbol": {"type": "string"}},
        )
        def fn(symbol):
            return {"price": 70_000.0, "symbol": symbol}

        first = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})
        second = _call("fetch_data_point", {"name": "hl_price", "params": {"symbol": "BTC"}})

        rows = _isolated.conn().execute(
            "SELECT id, source FROM data_point_snapshots ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["source"] == "hyperliquid"
        assert rows[1]["source"] == "perception_cache:hyperliquid"


class TestListDataPoints:
    def test_lists_after_register(self, _isolated):
        @data_point_registry.register_data_point(
            name="a", category="market", source="hl", description="A"
        )
        def _a(): return None

        @data_point_registry.register_data_point(
            name="b", category="on_chain", source="hl", description="B"
        )
        def _b(): return None

        result = _call("list_data_points", {})
        assert result["count"] == 2
        names = {e["name"] for e in result["entries"]}
        assert names == {"a", "b"}

    def test_filter_by_category(self, _isolated):
        @data_point_registry.register_data_point(
            name="m", category="market", source="hl", description=""
        )
        def _m(): return None

        @data_point_registry.register_data_point(
            name="oc", category="on_chain", source="hl", description=""
        )
        def _oc(): return None

        result = _call("list_data_points", {"category": "market"})
        assert {e["name"] for e in result["entries"]} == {"m"}


class TestAccountState:
    def test_dispatches_to_venue(self, _isolated):
        venue_registry.register_venue(
            name="hyperliquid", description="HL",
            account_state_fn=lambda: {"positions": [], "balances": {"USDC": 25.0}},
        )
        result = _call("account_state", {"venue": "hyperliquid"})
        assert result["venue"] == "hyperliquid"
        assert result["state"]["balances"]["USDC"] == 25.0

    def test_aggregates_when_no_venue_specified(self, _isolated):
        venue_registry.register_venue(
            name="v1", description="", account_state_fn=lambda: {"x": 1}
        )
        venue_registry.register_venue(
            name="v2", description="", account_state_fn=lambda: {"y": 2}
        )
        result = _call("account_state", {})
        assert set(result["venues"]) == {"v1", "v2"}
        assert result["states"]["v1"] == {"x": 1}
        assert result["states"]["v2"] == {"y": 2}

    def test_no_venues_registered(self, _isolated):
        result = _call("account_state", {})
        assert "error" in result


# =========================================================================
# reflection
# =========================================================================

class TestRecordEvent:
    def test_dispatches_to_handler(self, _isolated):
        @event_registry.register_event(
            name="fake_event", description="test",
            fields_schema={"x": {"type": "integer"}},
        )
        def fn(x):
            return {"id": 42, "stored_x": x}

        result = _call("record_event", {"type": "fake_event", "params": {"x": 7}})
        assert result["event_type"] == "fake_event"
        assert result["id"] == 42
        assert result["stored_x"] == 7

    def test_unknown_event_returns_error(self, _isolated):
        result = _call("record_event", {"type": "nope"})
        assert "error" in result

    def test_handler_must_return_dict(self, _isolated):
        @event_registry.register_event(name="bad", description="")
        def fn():
            return "not a dict"

        result = _call("record_event", {"type": "bad"})
        assert "error" in result
        assert "must return a dict" in result["error"]


class TestRecordObservation:
    """The dispatcher captures session_id from gateway contextvars so V2's
    sync handshake (plutus-main reads ops observations since-last-beat by
    session_id) actually has a key to filter on. Pre-fix, every observation
    was written with NULL session_id."""

    def test_writes_observation_with_null_session_outside_gateway(self, _isolated):
        # CLI / tests / scripts have no gateway context — sid is None,
        # observation still writes, session_id column is NULL.
        result = _call("record_observation", {
            "kind": "noticed", "text_md": "BTC reclaimed 70k",
        })
        assert result["observation_id"] >= 1
        assert result["kind"] == "noticed"
        row = _isolated.conn().execute(
            "SELECT session_id, kind, text_md FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert row["session_id"] is None
        assert row["kind"] == "noticed"
        assert row["text_md"] == "BTC reclaimed 70k"

    def test_captures_session_id_from_context(self, _isolated):
        from harness.gateway.session_context import set_session_vars, clear_session_vars

        tokens = set_session_vars(session_key="tg:plutus_chat:operator")
        try:
            result = _call("record_observation", {
                "kind": "watching",
                "text_md": "Setup forming on ETH",
                "symbol": "ETH",
                "strategy_name": "momentum-breakout",
                "structured_tags": {"source_tier": "ops"},
            })
        finally:
            clear_session_vars(tokens)

        row = _isolated.conn().execute(
            "SELECT session_id, symbol, strategy_name, structured_tags_json "
            "FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert row["session_id"] == "tg:plutus_chat:operator"
        assert row["symbol"] == "ETH"
        assert row["strategy_name"] == "momentum-breakout"
        assert json.loads(row["structured_tags_json"]) == {"source_tier": "ops"}

    def test_rejects_empty_text(self, _isolated):
        result = _call("record_observation", {"kind": "noticed", "text_md": ""})
        assert "error" in result
        assert "text_md" in result["error"]

    def test_rejects_unknown_kind(self, _isolated):
        result = _call("record_observation", {
            "kind": "nonsense", "text_md": "x",
        })
        assert "error" in result
        assert "kind" in result["error"]


class TestListEventTypes:
    def test_lists_after_register(self, _isolated):
        @event_registry.register_event(name="thesis", description="A thesis.")
        def _t(): return {"id": 1}

        @event_registry.register_event(name="reflection", description="A reflection.")
        def _r(): return {"id": 1}

        result = _call("list_event_types", {})
        assert result["count"] == 2
        assert {e["name"] for e in result["entries"]} == {"thesis", "reflection"}


# =========================================================================
# execution
# =========================================================================

class TestPlaceOrder:
    def _seed_thesis(self, db, *, with_invalidation: bool, strategy_name: str = None):
        def w(c):
            return c.execute(
                "INSERT INTO theses(ts, symbol, text_md, strategy_name, invalidation_criteria_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), "BTC", "test thesis", strategy_name,
                 '["BTC closes below 60k"]' if with_invalidation else None),
            ).lastrowid
        return db._execute_write(w)

    def _seed_strategy_file(self, tmp_home, name, strategy_conviction=0.6, stage="trial"):
        """Drop a strategy file with the given strategy_conviction frontmatter."""
        path = tmp_home / "strategies" / stage / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\nstage: {stage}\nstrategy_conviction: {strategy_conviction}\n---\n\nbody\n",
            encoding="utf-8",
        )
        return path

    def test_refuses_thesis_without_invalidation(self, _isolated):
        thesis_id = self._seed_thesis(_isolated, with_invalidation=False)
        venue_registry.register_venue(
            name="hl", description="",
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": 0.01},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 0.6,
            "side": "long", "symbol": "BTC", "size": 0.01,
        })
        assert "error" in result
        assert "invalidation" in result["error"]

    def test_refuses_unknown_thesis(self, _isolated):
        venue_registry.register_venue(
            name="hl", description="",
            place_order_fn=lambda **kw: {"fill_price": 1.0},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": 99999, "conviction": 0.5,
            "side": "long", "symbol": "BTC", "size": 0.01,
        })
        assert "error" in result
        assert "does not exist" in result["error"]

    def test_no_venue_returns_error(self, _isolated):
        thesis_id = self._seed_thesis(_isolated, with_invalidation=True)
        result = _call("place_order", {
            "venue": "no_such_venue", "thesis_id": thesis_id, "conviction": 0.5,
            "side": "long", "symbol": "BTC", "size": 0.01,
        })
        assert "error" in result

    def test_full_open_writes_decision_trade_position(self, _isolated):
        thesis_id = self._seed_thesis(_isolated, with_invalidation=True)
        captured = {}

        def fake_place(**kw):
            captured.update(kw)
            return {"fill_price": 70_100.0, "size": 0.01,
                    "slippage_bp": 1.4, "order_id": "o1", "fill_id": "f1"}

        venue_registry.register_venue(
            name="hl", description="", place_order_fn=fake_place,
        )

        # Explicit size: override path. V2 sizing math runs but doesn't drive size.
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 0.7,
            "side": "long", "symbol": "BTC", "size": 0.01,
            "sl": 68_000.0, "tp": 73_000.0,
        })

        assert result["decision_id"] >= 1
        assert result["trade_id"] >= 1
        assert result["position_id"] >= 1
        assert result["fill_price"] == 70_100.0
        assert captured["symbol"] == "BTC" and captured["side"] == "long"
        assert captured["sl"] == 68_000.0 and captured["tp"] == 73_000.0
        # V2: result surfaces composite + multiplier even on override path.
        assert result["thesis_conviction"] == 0.7
        # No strategy_name on thesis → default strategy_conviction 0.5
        assert result["strategy_conviction"] == 0.5
        assert result["sizing_path"] == "explicit_size_override"
        # composite = sqrt(0.5 * 0.7) ≈ 0.5916
        assert abs(result["composite_conviction"] - 0.5916079783) < 1e-6
        # multiplier = 20 ** 0.5916 ≈ 8.04
        assert abs(result["multiplier"] - 20.0 ** 0.5916079783) < 1e-6

        decision = _isolated.conn().execute(
            "SELECT thesis_id, action, conviction, params_json FROM decisions WHERE id = ?",
            (result["decision_id"],),
        ).fetchone()
        assert decision["thesis_id"] == thesis_id
        assert decision["action"] == "open_long"
        assert decision["conviction"] == 0.7

        # V2: decision.params_json captures full conviction provenance for ML postmortem.
        params = json.loads(decision["params_json"])
        assert params["strategy_conviction_at_entry"] == 0.5
        assert params["thesis_conviction_at_entry"] == 0.7
        assert "composite_conviction" in params
        assert "multiplier" in params
        assert params["sizing_path"] == "explicit_size_override"

        trade = _isolated.conn().execute(
            "SELECT venue, symbol, side, fill_price, slippage_bp FROM trades WHERE id = ?",
            (result["trade_id"],),
        ).fetchone()
        assert trade["venue"] == "hl"
        assert trade["fill_price"] == 70_100.0
        assert trade["slippage_bp"] == 1.4

        position = _isolated.conn().execute(
            "SELECT status, size, opening_trade_id FROM positions WHERE id = ?",
            (result["position_id"],),
        ).fetchone()
        assert position["status"] == "open"
        assert position["opening_trade_id"] == result["trade_id"]

    # =====================================================================
    # V2 composite-conviction multiplier sizing
    # =====================================================================

    def test_composite_conviction_with_strategy_file(self, _isolated, tmp_path):
        """When the thesis has strategy_name and a strategy file with declared
        strategy_conviction exists, the dispatcher reads it for composite math."""
        self._seed_strategy_file(tmp_path, "support-hold", strategy_conviction=0.6)
        thesis_id = self._seed_thesis(
            _isolated, with_invalidation=True, strategy_name="support-hold",
        )

        venue_registry.register_venue(
            name="hl", description="",
            account_state_fn=lambda: {"total_equity_usd": 100.0, "positions": []},
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": kw["size"]},
        )

        # No `size` → multiplier path. Must provide ref_price.
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 1.0,
            "side": "long", "symbol": "BTC", "ref_price": 70_000.0,
            "sl": 68_000.0,
        })

        assert "error" not in result, f"unexpected error: {result}"
        # composite = sqrt(0.6 * 1.0) = 0.7746
        # multiplier = 20 ** 0.7746 ≈ 10.46
        # notional = 100 * 10.46 = $1046
        # size = 1046 / 70000 ≈ 0.01494
        import math
        expected_composite = math.sqrt(0.6 * 1.0)
        expected_multiplier = 20.0 ** expected_composite
        assert abs(result["strategy_conviction"] - 0.6) < 1e-9
        assert abs(result["thesis_conviction"] - 1.0) < 1e-9
        assert abs(result["composite_conviction"] - expected_composite) < 1e-9
        assert abs(result["multiplier"] - expected_multiplier) < 1e-9
        assert result["sizing_path"] == "composite_multiplier"
        expected_size = (100.0 * expected_multiplier) / 70_000.0
        assert abs(result["size"] - expected_size) < 1e-9

    def test_conviction_zero_yields_one_x_multiplier(self, _isolated, tmp_path):
        """At thesis_conviction=0, composite=0, multiplier=20^0=1x — risk floor."""
        self._seed_strategy_file(tmp_path, "support-hold", strategy_conviction=1.0)
        thesis_id = self._seed_thesis(
            _isolated, with_invalidation=True, strategy_name="support-hold",
        )
        venue_registry.register_venue(
            name="hl", description="",
            account_state_fn=lambda: {"total_equity_usd": 100.0},
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": kw["size"]},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 0.0,
            "side": "long", "symbol": "BTC", "ref_price": 70_000.0,
        })
        assert result["composite_conviction"] == 0.0
        assert result["multiplier"] == 1.0
        # notional = 100 * 1 = $100; size = 100/70000
        assert abs(result["size"] - (100.0 / 70_000.0)) < 1e-9

    def test_conviction_one_yields_twenty_x_multiplier(self, _isolated, tmp_path):
        """At both conviction layers = 1.0, composite=1.0, multiplier=20x — risk ceiling."""
        self._seed_strategy_file(tmp_path, "max-conv", strategy_conviction=1.0)
        thesis_id = self._seed_thesis(
            _isolated, with_invalidation=True, strategy_name="max-conv",
        )
        venue_registry.register_venue(
            name="hl", description="",
            account_state_fn=lambda: {"total_equity_usd": 100.0},
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": kw["size"]},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 1.0,
            "side": "long", "symbol": "BTC", "ref_price": 70_000.0,
        })
        assert result["composite_conviction"] == 1.0
        assert result["multiplier"] == 20.0
        # notional = 100 * 20 = $2000; size = 2000 / 70000
        assert abs(result["size"] - (2000.0 / 70_000.0)) < 1e-9

    def test_missing_ref_price_and_size_returns_error(self, _isolated):
        thesis_id = self._seed_thesis(
            _isolated, with_invalidation=True, strategy_name="support-hold",
        )
        venue_registry.register_venue(
            name="hl", description="",
            account_state_fn=lambda: {"total_equity_usd": 100.0},
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": kw["size"]},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 0.7,
            "side": "long", "symbol": "BTC",
            # No size, no ref_price.
        })
        assert "error" in result
        assert "ref_price" in result["error"]

    def test_no_account_state_fn_returns_error_for_multiplier_path(self, _isolated, tmp_path):
        self._seed_strategy_file(tmp_path, "support-hold", strategy_conviction=0.6)
        thesis_id = self._seed_thesis(
            _isolated, with_invalidation=True, strategy_name="support-hold",
        )
        venue_registry.register_venue(
            name="hl", description="",
            # No account_state_fn — multiplier path can't resolve balance.
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": kw["size"]},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 0.7,
            "side": "long", "symbol": "BTC", "ref_price": 70_000.0,
        })
        assert "error" in result
        assert "account_state_fn" in result["error"] or "balance" in result["error"]

    def test_unknown_strategy_uses_default_conviction(self, _isolated):
        """thesis.strategy_name set but no file exists → default to 0.5
        (loud-warning-but-don't-fail; lets ad-hoc strategies trade at minimum
        risk while the operator gets time to author/repair the file)."""
        thesis_id = self._seed_thesis(
            _isolated, with_invalidation=True, strategy_name="not-on-disk",
        )
        venue_registry.register_venue(
            name="hl", description="",
            account_state_fn=lambda: {"total_equity_usd": 100.0},
            place_order_fn=lambda **kw: {"fill_price": 70_000.0, "size": kw["size"]},
        )
        result = _call("place_order", {
            "venue": "hl", "thesis_id": thesis_id, "conviction": 1.0,
            "side": "long", "symbol": "BTC", "ref_price": 70_000.0,
        })
        assert "error" not in result
        assert result["strategy_conviction"] == 0.5


class TestClosePosition:
    def _open_position(self, db):
        ts = time.time()

        def w(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, invalidation_criteria_json) "
                "VALUES (?, ?, ?, ?)",
                (ts, "BTC", "thesis", '["x"]'),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, ts, "open_long", 0.7),
            ).lastrowid
            trade_id = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, ts, "hl", "BTC", "long", 0.01, 70_000.0),
            ).lastrowid
            pos_id = c.execute(
                "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?, 'open', ?)",
                ("hl", "BTC", "long", 0.01, trade_id, ts),
            ).lastrowid
            return tid, pos_id

        return db._execute_write(w)

    def test_close_writes_trade_outcome_and_trajectory_stats(self, _isolated):
        thesis_id, pos_id = self._open_position(_isolated)

        # Seed a few position_evaluations so trajectory stats compute non-trivially.
        def add_evals(c):
            for conv in (0.7, 0.65, 0.55, 0.45):
                c.execute(
                    "INSERT INTO position_evaluations(ts, position_id, conviction) "
                    "VALUES (?, ?, ?)",
                    (time.time(), pos_id, conv),
                )

        _isolated._execute_write(add_evals)

        venue_registry.register_venue(
            name="hl", description="",
            close_position_fn=lambda **kw: {"fill_price": 71_500.0, "size": 0.01,
                                            "order_id": "o2", "fill_id": "f2"},
        )

        result = _call("close_position", {
            "venue": "hl", "position_id": pos_id, "thesis_id": thesis_id,
            "conviction": 0.4, "exit_reason": "thesis_invalidated",
        })

        assert result["close_trade_id"] >= 1
        assert result["fill_price"] == 71_500.0

        position = _isolated.conn().execute(
            "SELECT status, closing_trade_id, closed_at, perceived_at FROM positions WHERE id = ?",
            (pos_id,),
        ).fetchone()
        assert position["status"] == "closed"
        assert position["closing_trade_id"] == result["close_trade_id"]
        assert position["closed_at"] is not None
        assert position["perceived_at"] is not None

        outcome = _isolated.conn().execute(
            "SELECT exit_reason, conviction_at_entry, conviction_at_exit, "
            "conviction_min_during_hold, conviction_max_during_hold, "
            "conviction_evaluations_count FROM outcomes WHERE position_id = ?",
            (pos_id,),
        ).fetchone()
        assert outcome["exit_reason"] == "thesis_invalidated"
        assert outcome["conviction_at_entry"] == 0.7
        assert outcome["conviction_at_exit"] == 0.4
        assert outcome["conviction_min_during_hold"] == 0.45
        assert outcome["conviction_max_during_hold"] == 0.7
        assert outcome["conviction_evaluations_count"] == 4

    def test_refuses_already_closed(self, _isolated):
        thesis_id, pos_id = self._open_position(_isolated)

        def w(c):
            c.execute("UPDATE positions SET status = 'closed' WHERE id = ?", (pos_id,))
        _isolated._execute_write(w)

        venue_registry.register_venue(
            name="hl", description="",
            close_position_fn=lambda **kw: {"fill_price": 71_000.0},
        )

        result = _call("close_position", {
            "venue": "hl", "position_id": pos_id, "thesis_id": thesis_id,
            "conviction": 0.4, "exit_reason": "thesis_invalidated",
        })
        assert "error" in result
        assert "already closed" in result["error"]


class TestModifyAndCancelOrder:
    def test_modify_dispatches_to_venue(self, _isolated):
        seen = {}

        def mod(order_id, **kw):
            seen["order_id"] = order_id
            seen.update(kw)
            return {"ok": True}

        venue_registry.register_venue(name="hl", description="", modify_order_fn=mod)
        result = _call("modify_order", {
            "venue": "hl", "order_id": "o1", "updates": {"sl": 68_500.0},
        })
        assert result["result"] == {"ok": True}
        assert seen == {"order_id": "o1", "sl": 68_500.0}

    def test_cancel_dispatches_to_venue(self, _isolated):
        seen = {}

        def cancel(order_id):
            seen["order_id"] = order_id
            return {"cancelled": True}

        venue_registry.register_venue(name="hl", description="", cancel_order_fn=cancel)
        result = _call("cancel_order", {"venue": "hl", "order_id": "o42"})
        assert result["result"] == {"cancelled": True}
        assert seen == {"order_id": "o42"}


class TestListVenues:
    def test_lists_with_supports_flags(self, _isolated):
        venue_registry.register_venue(
            name="hl", description="HL",
            place_order_fn=lambda **kw: {},
            account_state_fn=lambda: {},
        )
        result = _call("list_venues", {})
        assert result["count"] == 1
        entry = result["entries"][0]
        assert entry["name"] == "hl"
        assert entry["supports"]["place_order"] is True
        assert entry["supports"]["close_position"] is False
        assert entry["supports"]["account_state"] is True


# =========================================================================
# identity
# =========================================================================

class TestListAccounts:
    def test_lists_with_filters(self, _isolated):
        account_registry.register_account(
            name="hl_trading", purpose="trading_capital", venue="hyperliquid",
        )
        account_registry.register_account(
            name="cold", purpose="cold_storage", chain="ethereum",
        )

        result = _call("list_accounts", {})
        assert result["count"] == 2
        result_filtered = _call("list_accounts", {"purpose": "trading_capital"})
        assert {e["name"] for e in result_filtered["entries"]} == {"hl_trading"}


class TestListIdentitySystems:
    def test_lists(self, _isolated):
        identity_registry.register_identity_system(name="acp", description="Virtuals ACP")
        result = _call("list_identity_systems", {})
        assert result["count"] == 1
        assert result["entries"][0]["name"] == "acp"
