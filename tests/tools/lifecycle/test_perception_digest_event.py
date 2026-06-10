"""Tests for V2.1 perception_digest event type.

The plutus-perception sub-agent writes ONE perception_digest observation
at the end of its run. plutus-main reads it via query_latest_perception_digest.
This module verifies the event handler and the query dispatcher.
"""

import json
import time

import pytest

from trading.lifecycle.db import get_lifecycle_db, reset_lifecycle_db_singleton
from trading.perception.core import event_registry
from harness.tools.registry import registry as tool_registry

# Force registration.
import trading.lifecycle.queries.event_types               # noqa: F401
import trading.lifecycle.queries.query_latest_perception_digest  # noqa: F401


@pytest.fixture()
def db(tmp_path):
    # Re-register events if a sibling test (test_dispatchers._isolated)
    # cleared the registry. See test_compaction_visibility.py for the
    # same pattern.
    import importlib
    import trading.lifecycle.queries.event_types as _et
    from trading.perception.core import event_registry
    try:
        event_registry.lookup("perception_digest")
    except KeyError:
        importlib.reload(_et)

    reset_lifecycle_db_singleton()
    yield get_lifecycle_db(db_path=tmp_path / "lifecycle.db")
    reset_lifecycle_db_singleton()


def _call(tool_name: str, args: dict) -> dict:
    entry = tool_registry.get_entry(tool_name)
    assert entry is not None, f"tool '{tool_name}' not registered"
    return json.loads(entry.handler(args))


# =========================================================================
# Event registration + write
# =========================================================================

class TestPerceptionDigestEventType:
    def test_event_type_is_registered(self, db):
        evt = event_registry.lookup("perception_digest")
        assert evt is not None
        assert evt.fn is not None

    def test_minimal_write(self, db):
        evt = event_registry.lookup("perception_digest")
        result = evt.fn(
            for_main_beat_at_unix=1779300000.0,
            scope="standard",
            text_md="# digest\n\nfindings here",
        )
        assert "observation_id" in result

        # Verify it landed as an observation row.
        row = db.conn().execute(
            "SELECT session_id, kind, text_md, structured_tags_json "
            "FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert row["kind"] == "noticed"
        assert "findings here" in row["text_md"]
        tags = json.loads(row["structured_tags_json"])
        assert tags["event_type"] == "perception_digest"
        assert tags["source_tier"] == "perception"
        assert tags["scope"] == "standard"
        assert tags["for_main_beat_at_unix"] == 1779300000.0

    def test_full_write_with_all_metadata(self, db):
        evt = event_registry.lookup("perception_digest")
        result = evt.fn(
            for_main_beat_at_unix=1779300000.0,
            scope="weekly",
            text_md="# weekly digest",
            watchlist_covered=["BTC", "HYPE"],
            strategies_perceived=["support-hold"],
            fresh_count=92,
            failed_dps=["ta_trix"],
            broken_list_retest_results={"ta_adx": "now_working"},
            snapshot_ids_by_dp={"hl_price:BTC": 5001, "ta_rsi:BTC": 5023},
            duration_s=187.5,
            session_id_perception="subagent_plutus-perception_20260520_134000",
        )
        row = db.conn().execute(
            "SELECT session_id, structured_tags_json FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        # session_id_perception should land in session_id column
        assert row["session_id"] == "subagent_plutus-perception_20260520_134000"
        tags = json.loads(row["structured_tags_json"])
        assert tags["watchlist_covered"] == ["BTC", "HYPE"]
        assert tags["strategies_perceived"] == ["support-hold"]
        assert tags["fresh_count"] == 92
        assert tags["failed_dps"] == ["ta_trix"]
        assert tags["broken_list_retest_results"] == {"ta_adx": "now_working"}
        assert tags["snapshot_ids_by_dp"] == {"hl_price:BTC": 5001, "ta_rsi:BTC": 5023}
        assert abs(tags["duration_s"] - 187.5) < 1e-6

    def test_rejects_invalid_scope(self, db):
        evt = event_registry.lookup("perception_digest")
        with pytest.raises(ValueError, match="scope must be"):
            evt.fn(
                for_main_beat_at_unix=1779300000.0,
                scope="bogus",
                text_md="x",
            )


# =========================================================================
# Dispatcher: query_latest_perception_digest
# =========================================================================

class TestQueryLatestPerceptionDigest:
    def _record(self, db, **kwargs):
        evt = event_registry.lookup("perception_digest")
        defaults = dict(
            for_main_beat_at_unix=time.time(),
            scope="standard",
            text_md="# digest",
            watchlist_covered=["BTC"],
            strategies_perceived=["support-hold"],
            fresh_count=80,
        )
        defaults.update(kwargs)
        return evt.fn(**defaults)

    def test_empty_returns_not_found(self, db):
        result = _call("query_latest_perception_digest", {})
        assert result["found"] is False
        assert "reason" in result

    def test_returns_latest_by_default(self, db):
        first = self._record(db, text_md="# first")
        time.sleep(0.01)
        second = self._record(db, text_md="# second")
        result = _call("query_latest_perception_digest", {})
        assert result["found"] is True
        assert result["observation_id"] == second["observation_id"]
        assert "second" in result["text_md"]

    def test_filter_by_for_main_beat(self, db):
        self._record(db, for_main_beat_at_unix=1779300000.0)
        self._record(db, for_main_beat_at_unix=1779307200.0)
        result = _call("query_latest_perception_digest",
                       {"for_main_beat_at_unix": 1779300000.0})
        assert result["found"] is True
        assert result["structured_tags"]["for_main_beat_at_unix"] == 1779300000.0

    def test_filter_by_scope(self, db):
        self._record(db, scope="standard")
        self._record(db, scope="weekly")
        result_std = _call("query_latest_perception_digest", {"scope": "standard"})
        assert result_std["found"] is True
        assert result_std["structured_tags"]["scope"] == "standard"
        result_wk = _call("query_latest_perception_digest", {"scope": "weekly"})
        assert result_wk["found"] is True
        assert result_wk["structured_tags"]["scope"] == "weekly"

    def test_max_age_filters_out_old_digest(self, db):
        # Write a digest with current ts; then look for one max 1s old after a wait.
        self._record(db)
        time.sleep(1.5)
        result = _call("query_latest_perception_digest", {"max_age_s": 0.5})
        assert result["found"] is False

    def test_structured_tags_pass_through(self, db):
        self._record(
            db,
            snapshot_ids_by_dp={"hl_price:BTC": 9999},
            failed_dps=["ta_trix"],
            broken_list_retest_results={"ta_ema": "now_working"},
            duration_s=200.0,
        )
        result = _call("query_latest_perception_digest", {})
        tags = result["structured_tags"]
        assert tags["snapshot_ids_by_dp"] == {"hl_price:BTC": 9999}
        assert tags["failed_dps"] == ["ta_trix"]
        assert tags["broken_list_retest_results"] == {"ta_ema": "now_working"}
        assert tags["duration_s"] == 200.0

    def test_age_s_computed(self, db):
        self._record(db)
        time.sleep(0.5)
        result = _call("query_latest_perception_digest", {})
        assert 0.4 <= result["age_s"] <= 2.0
