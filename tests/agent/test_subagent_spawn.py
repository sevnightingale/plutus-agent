"""Tests for agent.subagent_spawn helper.

Full AIAgent construction is too heavy for unit tests. We exercise:
- Input validation (enabled_toolsets required, skill_name required)
- The internal prompt builder
- The internal _query_result_observation finder
"""

import json
import time

import pytest

from harness.agent.lifecycle_db import get_lifecycle_db, reset_lifecycle_db_singleton
from harness.agent.subagent_spawn import (
    spawn_subagent_blocking,
    _build_subagent_prompt,
    _query_result_observation,
)

# Force registration so we can write observations.
import harness.tools.lifecycle.event_types  # noqa: F401


@pytest.fixture()
def db(tmp_path):
    reset_lifecycle_db_singleton()
    yield get_lifecycle_db(db_path=tmp_path / "lifecycle.db")
    reset_lifecycle_db_singleton()


class TestValidation:
    def test_requires_skill_name(self):
        with pytest.raises(ValueError, match="skill_name is required"):
            spawn_subagent_blocking(
                skill_name="",
                expected_event_type="perception_digest",
                enabled_toolsets=["perception"],
            )

    def test_requires_expected_event_type(self):
        with pytest.raises(ValueError, match="expected_event_type is required"):
            spawn_subagent_blocking(
                skill_name="plutus-perception",
                expected_event_type="",
                enabled_toolsets=["perception"],
            )

    def test_requires_enabled_toolsets_explicit(self):
        with pytest.raises(ValueError, match="enabled_toolsets"):
            spawn_subagent_blocking(
                skill_name="plutus-perception",
                expected_event_type="perception_digest",
                enabled_toolsets=None,
            )


class TestPromptBuilder:
    def test_minimal_prompt(self):
        prompt = _build_subagent_prompt(
            skill_name="plutus-perception",
            scope=None,
            extra_context_md=None,
            for_main_beat_at_unix=None,
        )
        assert "SUB-AGENT INVOCATION" in prompt
        assert "plutus-perception" in prompt
        assert "skill_view" in prompt
        assert "trading/plutus-perception" in prompt

    def test_with_scope_and_beat_ts(self):
        prompt = _build_subagent_prompt(
            skill_name="plutus-perception",
            scope="weekly",
            extra_context_md=None,
            for_main_beat_at_unix=1779300000.0,
        )
        assert "Scope parameter: **weekly**" in prompt
        assert "unix=1779300000" in prompt

    def test_with_extra_context(self):
        prompt = _build_subagent_prompt(
            skill_name="plutus-perception",
            scope="standard",
            extra_context_md="Focus on HYPE momentum this beat.",
            for_main_beat_at_unix=None,
        )
        assert "Additional context" in prompt
        assert "Focus on HYPE momentum this beat." in prompt

    def test_restricted_toolset_explicit_in_prompt(self):
        prompt = _build_subagent_prompt(
            skill_name="plutus-perception",
            scope=None,
            extra_context_md=None,
            for_main_beat_at_unix=None,
        )
        assert "restricted toolset" in prompt
        assert "trading, messaging" in prompt


class TestResultObservationFinder:
    def _write_obs(self, db, session_id, event_type, ts, extra_tags=None):
        tags = {"event_type": event_type, "scope": "standard"}
        if extra_tags:
            tags.update(extra_tags)
        return db._execute_write(
            lambda conn: conn.execute(
                "INSERT INTO observations(session_id, ts, kind, text_md, structured_tags_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    ts,
                    "noticed",
                    "test",
                    json.dumps(tags),
                ),
            ).lastrowid
        )

    def test_finds_matching_observation(self, db):
        spawn_ts = time.time()
        obs_id = self._write_obs(db, "sess1", "perception_digest", spawn_ts + 1)
        result = _query_result_observation(
            db, "sess1", "perception_digest", spawn_ts,
        )
        assert result is not None
        assert result["id"] == obs_id

    def test_ignores_other_event_types(self, db):
        spawn_ts = time.time()
        self._write_obs(db, "sess1", "compaction", spawn_ts + 1)
        result = _query_result_observation(
            db, "sess1", "perception_digest", spawn_ts,
        )
        assert result is None

    def test_ignores_other_sessions(self, db):
        spawn_ts = time.time()
        self._write_obs(db, "sess_other", "perception_digest", spawn_ts + 1)
        result = _query_result_observation(
            db, "sess1", "perception_digest", spawn_ts,
        )
        assert result is None

    def test_ignores_pre_spawn_observations(self, db):
        spawn_ts = time.time()
        # Write an observation BEFORE the spawn_ts (a stale one from prior session reuse).
        self._write_obs(db, "sess1", "perception_digest", spawn_ts - 100)
        result = _query_result_observation(
            db, "sess1", "perception_digest", spawn_ts,
        )
        assert result is None

    def test_returns_most_recent_when_multiple(self, db):
        spawn_ts = time.time()
        first = self._write_obs(db, "sess1", "perception_digest", spawn_ts + 1)
        second = self._write_obs(db, "sess1", "perception_digest", spawn_ts + 5)
        result = _query_result_observation(
            db, "sess1", "perception_digest", spawn_ts,
        )
        # Should return the most recent
        assert result["id"] == second

    # ─── Fix for 2026-05-21 observation #278 bug ─────────────────────────
    # The sub-agent made up a session_id_perception that didn't match the
    # spawn helper's sub_session_id, so the query missed it. The fix:
    # query matches by structured_tags.session_id_perception too.

    def test_matches_by_session_id_perception_tag_when_column_differs(self, db):
        """The observation has a different session_id column but the right session_id_perception tag."""
        spawn_ts = time.time()
        obs_id = self._write_obs(
            db, "made-up-name", "perception_digest", spawn_ts + 1,
            extra_tags={"session_id_perception": "real-sub-session"},
        )
        result = _query_result_observation(
            db, "real-sub-session", "perception_digest", spawn_ts,
        )
        assert result is not None
        assert result["id"] == obs_id

    def test_matches_by_tier_session_id_tag(self, db):
        """Forward-compat: other V2 events use tier_session_id."""
        spawn_ts = time.time()
        obs_id = self._write_obs(
            db, "made-up-name", "perception_digest", spawn_ts + 1,
            extra_tags={"tier_session_id": "real-sub-session"},
        )
        result = _query_result_observation(
            db, "real-sub-session", "perception_digest", spawn_ts,
        )
        assert result is not None
        assert result["id"] == obs_id


class TestPerceptionDigestSessionIdDefaulting:
    """Verify session_id_perception auto-populates from session_id_from_context()."""

    def test_defaults_session_id_perception_from_context(self, db, monkeypatch):
        # Simulate the spawn helper's set_session_vars by monkeypatching
        # session_id_from_context to return a known value.
        from harness.tools.dispatchers import _helpers
        monkeypatch.setattr(_helpers, "session_id_from_context",
                            lambda: "ctx-supplied-session")

        from harness.tools.core import event_registry
        evt = event_registry.lookup("perception_digest")
        result = evt.fn(
            for_main_beat_at_unix=time.time(),
            scope="standard",
            text_md="# test",
            # NOTE: session_id_perception NOT passed
        )

        row = db.conn().execute(
            "SELECT session_id, structured_tags_json FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert row["session_id"] == "ctx-supplied-session"
        tags = json.loads(row["structured_tags_json"])
        assert tags["session_id_perception"] == "ctx-supplied-session"

    def test_explicit_session_id_perception_overrides_context(self, db, monkeypatch):
        from harness.tools.dispatchers import _helpers
        monkeypatch.setattr(_helpers, "session_id_from_context",
                            lambda: "ctx-supplied-session")

        from harness.tools.core import event_registry
        evt = event_registry.lookup("perception_digest")
        result = evt.fn(
            for_main_beat_at_unix=time.time(),
            scope="standard",
            text_md="# test",
            session_id_perception="explicit-override",
        )

        row = db.conn().execute(
            "SELECT session_id FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert row["session_id"] == "explicit-override"
