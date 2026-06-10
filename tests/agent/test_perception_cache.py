"""Tests for agent/perception_cache.py — V2 Stratum 1.7.

Verifies read/write contract, per-DP staleness budgets, atomic rename,
and concurrent writer safety. Tests use HERMES_HOME pointed at tmp_path
so production ~/.plutus-agent/perception_state.json is never touched.
"""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.agent import perception_cache


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Point hermes home at tmp_path so cache writes are isolated."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Confirm helper resolves correctly.
    assert perception_cache._hermes_home() == tmp_path
    yield tmp_path


# =========================================================================
# get_staleness_budget
# =========================================================================

class TestGetStalenessBudget:
    def test_exact_name_match(self):
        assert perception_cache.get_staleness_budget("hl_price") == 60.0
        assert perception_cache.get_staleness_budget("hl_universe") == 3600.0

    def test_prefix_match(self):
        # ta_ prefix → 300s
        assert perception_cache.get_staleness_budget("ta_rsi") == 300.0
        assert perception_cache.get_staleness_budget("ta_macd") == 300.0
        # macro_ prefix → 4h
        assert perception_cache.get_staleness_budget("macro_vix") == 14400.0
        # coingecko_ prefix → 30min
        assert perception_cache.get_staleness_budget("coingecko_global") == 1800.0

    def test_strips_params_from_key(self):
        """Key like 'hl_price:BTC' falls back to the bare name lookup."""
        assert perception_cache.get_staleness_budget("hl_price:BTC") == 60.0
        assert perception_cache.get_staleness_budget("ta_rsi:BTC:15m") == 300.0

    def test_unknown_falls_back_to_default(self):
        assert perception_cache.get_staleness_budget("totally_unknown_dp") == 300.0

    def test_longest_prefix_wins(self):
        """If both 'foo' and 'foo_bar' were configured, longest wins.
        We don't have that case in defaults — synthetic test via monkeypatch."""
        with patch.dict(
            perception_cache._PREFIX_STALENESS_BUDGETS,
            {"foo": 100.0, "foo_specific": 999.0},
            clear=False,
        ):
            assert perception_cache.get_staleness_budget("foo_specific") == 999.0
            assert perception_cache.get_staleness_budget("foo_other") == 100.0


# =========================================================================
# canonical_key
# =========================================================================

class TestCanonicalKey:
    def test_no_params_returns_name(self):
        assert perception_cache._canonical_key("hl_price") == "hl_price"

    def test_params_appended_as_json(self):
        key = perception_cache._canonical_key("hl_price", {"symbol": "BTC"})
        assert key.startswith("hl_price:")
        assert "BTC" in key

    def test_param_ordering_stable(self):
        """Logically-equal param dicts produce identical keys regardless of
        Python dict iteration order."""
        a = perception_cache._canonical_key("hl_candles", {"symbol": "BTC", "interval": "15m"})
        b = perception_cache._canonical_key("hl_candles", {"interval": "15m", "symbol": "BTC"})
        assert a == b


# =========================================================================
# read_perception_state — missing / corrupt / valid
# =========================================================================

class TestReadPerceptionState:
    def test_missing_file_returns_empty_well_formed(self, tmp_home):
        state = perception_cache.read_perception_state()
        assert state["version"] == perception_cache.PERCEPTION_CACHE_VERSION
        assert state["updated_at"] == 0.0
        assert state["data_points"] == {}

    def test_corrupt_json_returns_empty_well_formed(self, tmp_home):
        cache_path = tmp_home / "perception_state.json"
        cache_path.write_text("{not valid json", encoding="utf-8")
        state = perception_cache.read_perception_state()
        assert state["data_points"] == {}

    def test_missing_data_points_key_returns_empty(self, tmp_home):
        cache_path = tmp_home / "perception_state.json"
        cache_path.write_text(json.dumps({"version": 1, "updated_at": 0.0}), encoding="utf-8")
        state = perception_cache.read_perception_state()
        assert state["data_points"] == {}


# =========================================================================
# write_data_point + read_data_point round-trip
# =========================================================================

class TestWriteReadRoundTrip:
    def test_write_then_read_returns_entry(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price",
            {"price": 70_000.0},
            source="hyperliquid",
            params={"symbol": "BTC"},
            fetched_by_tier="ops",
        )
        entry = perception_cache.read_data_point("hl_price", {"symbol": "BTC"})
        assert entry is not None
        assert entry["value"] == {"price": 70_000.0}
        assert entry["source"] == "hyperliquid"
        assert entry["fetched_by_tier"] == "ops"
        assert entry["ttl_s"] == 60.0  # hl_price exact match

    def test_read_miss_returns_none(self, tmp_home):
        assert perception_cache.read_data_point("never_written") is None

    def test_different_params_cache_independently(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price", {"price": 70_000.0},
            source="hyperliquid", params={"symbol": "BTC"},
        )
        perception_cache.write_data_point(
            "hl_price", {"price": 3500.0},
            source="hyperliquid", params={"symbol": "ETH"},
        )
        btc = perception_cache.read_data_point("hl_price", {"symbol": "BTC"})
        eth = perception_cache.read_data_point("hl_price", {"symbol": "ETH"})
        assert btc["value"]["price"] == 70_000.0
        assert eth["value"]["price"] == 3500.0

    def test_overwrite_updates_value_and_fetched_at(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price", {"price": 70_000.0},
            source="hyperliquid", params={"symbol": "BTC"},
        )
        first = perception_cache.read_data_point("hl_price", {"symbol": "BTC"})
        time.sleep(0.01)
        perception_cache.write_data_point(
            "hl_price", {"price": 70_500.0},
            source="hyperliquid", params={"symbol": "BTC"},
        )
        second = perception_cache.read_data_point("hl_price", {"symbol": "BTC"})
        assert second["value"]["price"] == 70_500.0
        assert second["fetched_at"] > first["fetched_at"]


# =========================================================================
# Staleness eviction
# =========================================================================

class TestStaleness:
    def test_stale_entry_returns_none_with_explicit_budget(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price", {"price": 70_000.0},
            source="hyperliquid", params={"symbol": "BTC"},
        )
        # 0-second budget → instantly stale.
        assert perception_cache.read_data_point(
            "hl_price", {"symbol": "BTC"}, max_age_s=0.0,
        ) is None

    def test_fresh_entry_within_budget_returns(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price", {"price": 70_000.0},
            source="hyperliquid", params={"symbol": "BTC"},
        )
        # 1 hour budget — entry just written is well within.
        entry = perception_cache.read_data_point(
            "hl_price", {"symbol": "BTC"}, max_age_s=3600.0,
        )
        assert entry is not None
        assert entry["value"]["price"] == 70_000.0

    def test_default_budget_used_when_max_age_none(self, tmp_home):
        """When max_age_s is None, the per-DP budget (60s for hl_price)
        is applied. We rewrite the entry's fetched_at to be 30s old to
        confirm it's still considered fresh."""
        perception_cache.write_data_point(
            "hl_price", {"price": 70_000.0},
            source="hyperliquid", params={"symbol": "BTC"},
        )
        # Manually age the entry to 30s old via direct file write.
        state = perception_cache.read_perception_state()
        key = perception_cache._canonical_key("hl_price", {"symbol": "BTC"})
        state["data_points"][key]["fetched_at"] = time.time() - 30
        perception_cache._atomic_write_json(perception_cache._cache_path(), state)
        # 30s < 60s budget → still fresh.
        entry = perception_cache.read_data_point("hl_price", {"symbol": "BTC"})
        assert entry is not None

        # Now age to 120s — past budget.
        state = perception_cache.read_perception_state()
        state["data_points"][key]["fetched_at"] = time.time() - 120
        perception_cache._atomic_write_json(perception_cache._cache_path(), state)
        assert perception_cache.read_data_point("hl_price", {"symbol": "BTC"}) is None


# =========================================================================
# Atomic write + concurrent writers
# =========================================================================

class TestAtomicWrite:
    def test_concurrent_writers_dont_corrupt(self, tmp_home):
        """Multiple threads writing different keys must end with a
        valid JSON file — no partial-write torn states. Last-writer-wins
        per key is the contract; the test asserts file integrity."""
        N_THREADS = 8
        WRITES_PER_THREAD = 30

        def writer(thread_id):
            for i in range(WRITES_PER_THREAD):
                perception_cache.write_data_point(
                    f"dp_{thread_id}",
                    {"iter": i, "tid": thread_id},
                    source="test",
                    params={"k": str(i)},
                )

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # File must parse cleanly.
        state = perception_cache.read_perception_state()
        # Each thread's LAST write per param should be retrievable (no
        # guarantee all writes survive since they share the cache file —
        # this just asserts the file is valid JSON and contains some
        # of the writes).
        assert isinstance(state["data_points"], dict)
        assert len(state["data_points"]) >= 1
        # No torn writes — file is well-formed JSON.
        json.dumps(state)  # would raise on circular refs etc.

    def test_no_tmp_file_left_on_success(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price", {"price": 1.0},
            source="t", params={"symbol": "BTC"},
        )
        tmp_files = [p for p in tmp_home.iterdir() if p.name.endswith(".tmp")]
        assert tmp_files == [], f"leftover tmp files: {tmp_files}"


# =========================================================================
# clear_cache
# =========================================================================

class TestClearCache:
    def test_clear_removes_file(self, tmp_home):
        perception_cache.write_data_point(
            "hl_price", {"x": 1}, source="t", params={"symbol": "BTC"},
        )
        assert perception_cache._cache_path().exists()
        perception_cache.clear_cache()
        assert not perception_cache._cache_path().exists()

    def test_clear_on_missing_is_noop(self, tmp_home):
        # Should not raise.
        perception_cache.clear_cache()
