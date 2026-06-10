"""Tests for agent/active_thesis_monitors.py — V2 active-thesis-monitors.json."""

import json
import threading
import time
from pathlib import Path

import pytest

from harness.agent import active_thesis_monitors as atm


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert atm._hermes_home() == tmp_path
    yield tmp_path


def _make_kwargs(thesis_id=9, position_id=7, **overrides):
    base = dict(
        thesis_id=thesis_id,
        position_id=position_id,
        symbol="BTC",
        side="long",
        data_points_to_watch=["hl_price", "hl_cvd", "ta_rsi"],
        invalidation_rules=[
            {"rule": "price < 75200", "action": "exit"},
            {"rule": "ta_rsi > 75", "action": "exit"},
        ],
        horizon_ts=time.time() + 86400,
        added_by_session_id="test-session",
    )
    base.update(overrides)
    return base


# =========================================================================
# read_active_monitors on missing / empty / corrupt
# =========================================================================

class TestReadActiveMonitors:
    def test_missing_file_returns_empty_list(self, tmp_home):
        assert atm.read_active_monitors() == []

    def test_corrupt_json_returns_empty_list(self, tmp_home):
        atm._monitors_path().write_text("{not json", encoding="utf-8")
        assert atm.read_active_monitors() == []

    def test_missing_monitors_key_returns_empty(self, tmp_home):
        atm._monitors_path().write_text(json.dumps({"version": 1}), encoding="utf-8")
        assert atm.read_active_monitors() == []


# =========================================================================
# add / remove / update / get round-trip
# =========================================================================

class TestAddRemoveUpdate:
    def test_add_persists_entry(self, tmp_home):
        atm.add_monitor(**_make_kwargs())
        monitors = atm.read_active_monitors()
        assert len(monitors) == 1
        m = monitors[0]
        assert m["thesis_id"] == 9
        assert m["position_id"] == 7
        assert m["symbol"] == "BTC"
        assert m["side"] == "long"
        assert m["data_points_to_watch"] == ["hl_price", "hl_cvd", "ta_rsi"]
        assert len(m["invalidation_rules"]) == 2
        assert m["added_by_session_id"] == "test-session"
        assert m["added_at"] > 0

    def test_add_with_same_thesis_id_replaces(self, tmp_home):
        atm.add_monitor(**_make_kwargs(thesis_id=9, symbol="BTC"))
        # Re-add with same thesis_id but different symbol — should REPLACE not duplicate.
        atm.add_monitor(**_make_kwargs(thesis_id=9, symbol="ETH"))
        monitors = atm.read_active_monitors()
        assert len(monitors) == 1
        assert monitors[0]["symbol"] == "ETH"

    def test_remove_returns_true_on_hit(self, tmp_home):
        atm.add_monitor(**_make_kwargs(thesis_id=9))
        atm.add_monitor(**_make_kwargs(thesis_id=10))
        assert atm.remove_monitor(9) is True
        remaining = {m["thesis_id"] for m in atm.read_active_monitors()}
        assert remaining == {10}

    def test_remove_returns_false_on_miss(self, tmp_home):
        atm.add_monitor(**_make_kwargs(thesis_id=9))
        assert atm.remove_monitor(999) is False
        # State unchanged.
        assert len(atm.read_active_monitors()) == 1

    def test_update_partial_fields(self, tmp_home):
        atm.add_monitor(**_make_kwargs(thesis_id=9))
        new_horizon = time.time() + 172800
        assert atm.update_monitor(
            9, horizon_ts=new_horizon,
            data_points_to_watch=["hl_price", "ta_atr"],
        ) is True
        m = atm.get_monitor(9)
        assert m["horizon_ts"] == new_horizon
        assert m["data_points_to_watch"] == ["hl_price", "ta_atr"]
        # Untouched fields preserved.
        assert m["symbol"] == "BTC"
        assert m["side"] == "long"

    def test_update_returns_false_on_miss(self, tmp_home):
        atm.add_monitor(**_make_kwargs(thesis_id=9))
        assert atm.update_monitor(999, horizon_ts=time.time()) is False

    def test_get_monitor_returns_entry_or_none(self, tmp_home):
        atm.add_monitor(**_make_kwargs(thesis_id=9))
        assert atm.get_monitor(9)["thesis_id"] == 9
        assert atm.get_monitor(999) is None


# =========================================================================
# Atomic write integrity
# =========================================================================

class TestAtomicWrite:
    def test_concurrent_adds_stay_well_formed(self, tmp_home):
        """Many threads adding distinct thesis_ids must end with a valid file.
        Last-writer-wins per file is the contract (single-writer assumption
        for the production case); this just verifies no torn writes."""
        N_THREADS = 6
        ADDS_PER_THREAD = 10

        def writer(tid):
            for i in range(ADDS_PER_THREAD):
                atm.add_monitor(**_make_kwargs(
                    thesis_id=tid * 1000 + i,
                    position_id=tid * 1000 + i + 500,
                ))

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # File must parse cleanly.
        with open(atm._monitors_path(), "r") as f:
            state = json.load(f)
        assert isinstance(state["monitors"], list)
        # No torn writes — file is valid JSON.

    def test_no_tmp_file_left_on_success(self, tmp_home):
        atm.add_monitor(**_make_kwargs())
        tmp_files = [p for p in tmp_home.iterdir() if p.name.endswith(".tmp")]
        assert tmp_files == []


class TestClearMonitors:
    def test_clear_removes_file(self, tmp_home):
        atm.add_monitor(**_make_kwargs())
        assert atm._monitors_path().exists()
        atm.clear_monitors()
        assert not atm._monitors_path().exists()
        assert atm.read_active_monitors() == []

    def test_clear_on_missing_is_noop(self, tmp_home):
        atm.clear_monitors()
