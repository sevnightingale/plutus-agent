"""Tests for V2 compaction visibility — record_event('compaction') + query_compaction_history.

Both gateway pre-compress and agent mid-conversation compress emit a
`compaction` event via record_event when they succeed. The event lands in
observations with structured_tags marking it. query_compaction_history
pulls them back out with optional filters.
"""

import json
import time

import pytest

from agent.lifecycle_db import LifecycleDB, get_lifecycle_db, reset_lifecycle_db_singleton
from tools.core import event_registry
from tools.registry import registry as tool_registry

# Force registration.
import tools.lifecycle.event_types               # noqa: F401
import tools.lifecycle.query_compaction_history  # noqa: F401


@pytest.fixture()
def db(tmp_path):
    # Re-import event_types in case a parallel xdist sibling test
    # (test_dispatchers._isolated) called event_registry.reset() since
    # this worker's initial module load. Event registration only happens
    # at module import time; without re-registration we'd hit
    # "event type 'compaction' not registered".
    import importlib
    import tools.lifecycle.event_types as _et
    from tools.core import event_registry
    try:
        event_registry.lookup("compaction")
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

class TestCompactionEventType:
    def test_event_type_is_registered(self, db):
        evt = event_registry.lookup("compaction")
        assert evt is not None
        assert evt.fn is not None

    def test_records_into_observations(self, db):
        evt = event_registry.lookup("compaction")
        result = evt.fn(
            layer="agent_mid_conversation",
            pre_token_count=100_000,
            post_token_count=15_000,
            pre_message_count=80,
            post_message_count=12,
            session_id_before="20260520_120000_abc",
            session_id_after="20260520_130000_def",
            model="kimi-k2.6",
        )
        assert "observation_id" in result
        assert abs(result["compression_ratio"] - 0.15) < 1e-6

        # Verify it landed as an observation row with structured_tags.
        row = db.conn().execute(
            "SELECT session_id, kind, text_md, structured_tags_json "
            "FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert row["kind"] == "noticed"
        assert row["session_id"] == "20260520_130000_def"
        tags = json.loads(row["structured_tags_json"])
        assert tags["event_type"] == "compaction"
        assert tags["layer"] == "agent_mid_conversation"
        assert tags["pre_token_count"] == 100_000
        assert tags["post_token_count"] == 15_000
        assert abs(tags["compression_ratio"] - 0.15) < 1e-6
        assert tags["model"] == "kimi-k2.6"

    def test_rejects_unknown_layer(self, db):
        evt = event_registry.lookup("compaction")
        with pytest.raises(ValueError, match="layer must be"):
            evt.fn(
                layer="bogus_layer",
                pre_token_count=10, post_token_count=5,
            )

    def test_zero_pre_tokens_yields_zero_ratio(self, db):
        """Edge case: pre=0 (would be div-by-zero). Must not crash."""
        evt = event_registry.lookup("compaction")
        result = evt.fn(
            layer="gateway_pre_compress",
            pre_token_count=0,
            post_token_count=0,
        )
        assert result["compression_ratio"] == 0.0


# =========================================================================
# Dispatcher: query_compaction_history
# =========================================================================

class TestQueryCompactionHistory:
    def _record_compaction(self, db, **kwargs):
        evt = event_registry.lookup("compaction")
        defaults = dict(
            layer="agent_mid_conversation",
            pre_token_count=50_000,
            post_token_count=10_000,
            model="kimi-k2.6",
        )
        defaults.update(kwargs)
        return evt.fn(**defaults)

    def test_empty_returns_zero_count(self, db):
        result = _call("query_compaction_history", {})
        assert result["count"] == 0
        assert result["compactions"] == []

    def test_returns_all_compactions_default(self, db):
        self._record_compaction(db, layer="gateway_pre_compress")
        self._record_compaction(db, layer="agent_mid_conversation")
        result = _call("query_compaction_history", {})
        assert result["count"] == 2

    def test_filter_by_layer(self, db):
        self._record_compaction(db, layer="gateway_pre_compress")
        self._record_compaction(db, layer="gateway_pre_compress")
        self._record_compaction(db, layer="agent_mid_conversation")
        result = _call("query_compaction_history", {"layer": "gateway_pre_compress"})
        assert result["count"] == 2
        assert all(c["layer"] == "gateway_pre_compress" for c in result["compactions"])

    def test_filter_by_session_id_matches_either_side(self, db):
        """A compaction rotates session_id; filter should match either side."""
        self._record_compaction(
            db, session_id_before="A", session_id_after="B",
        )
        self._record_compaction(
            db, session_id_before="C", session_id_after="D",
        )
        # Filter by 'A' (before) — should match the first.
        result_a = _call("query_compaction_history", {"session_id": "A"})
        assert result_a["count"] == 1
        # Filter by 'D' (after) — should match the second.
        result_d = _call("query_compaction_history", {"session_id": "D"})
        assert result_d["count"] == 1
        # Filter by 'X' (neither) — empty.
        result_x = _call("query_compaction_history", {"session_id": "X"})
        assert result_x["count"] == 0

    def test_results_ordered_ts_desc(self, db):
        first = self._record_compaction(db, pre_token_count=10_000)
        time.sleep(0.01)
        second = self._record_compaction(db, pre_token_count=20_000)
        result = _call("query_compaction_history", {})
        ids = [c["observation_id"] for c in result["compactions"]]
        assert ids[0] == second["observation_id"]
        assert ids[1] == first["observation_id"]

    def test_limit_caps_results(self, db):
        for _ in range(5):
            self._record_compaction(db)
        result = _call("query_compaction_history", {"limit": 2})
        assert result["count"] == 2

    def test_compactions_include_metadata_fields(self, db):
        self._record_compaction(
            db,
            layer="agent_mid_conversation",
            pre_token_count=100_000,
            post_token_count=15_000,
            pre_message_count=80,
            post_message_count=12,
            model="kimi-k2.6",
            focus_topic="post-CPI position review",
        )
        result = _call("query_compaction_history", {})
        c = result["compactions"][0]
        assert c["layer"] == "agent_mid_conversation"
        assert c["pre_token_count"] == 100_000
        assert c["post_token_count"] == 15_000
        assert c["pre_message_count"] == 80
        assert c["post_message_count"] == 12
        assert abs(c["compression_ratio"] - 0.15) < 1e-6
        assert c["model"] == "kimi-k2.6"
        assert c["focus_topic"] == "post-CPI position review"
