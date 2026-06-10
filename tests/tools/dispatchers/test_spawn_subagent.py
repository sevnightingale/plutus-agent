"""Tests for spawn_subagent dispatcher (V2.1 orchestration).

The dispatcher delegates to agent.subagent_spawn.spawn_subagent_blocking
which actually constructs an AIAgent and runs it in a worker thread.
That's too heavy for unit tests (real LLM, real workspace). We mock
the helper and verify the dispatcher's validation + parameter
threading.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from harness.tools.registry import registry as tool_registry

# Force registration.
import harness.tools.dispatchers.spawn_subagent  # noqa: F401


def _call(args: dict) -> dict:
    entry = tool_registry.get_entry("spawn_subagent")
    assert entry is not None, "spawn_subagent not registered"
    return json.loads(entry.handler(args))


class TestSpawnSubagentValidation:
    def test_requires_skill(self):
        result = _call({"expected_event_type": "perception_digest"})
        assert "error" in result
        assert "skill" in result["error"].lower()

    def test_requires_expected_event_type(self):
        result = _call({"skill": "plutus-perception"})
        assert "error" in result
        assert "expected_event_type" in result["error"].lower()

    def test_unknown_skill_without_toolsets_errors(self):
        result = _call({
            "skill": "made-up-skill",
            "expected_event_type": "made_up_event",
        })
        assert "error" in result
        assert "no default toolset" in result["error"].lower()

    def test_unknown_skill_with_toolsets_proceeds(self):
        # Should NOT hit the no-default-toolset error; should reach the
        # actual spawn (which we mock to avoid real AIAgent construction).
        with patch("harness.tools.dispatchers.spawn_subagent.spawn_subagent_blocking") as mock_spawn:
            mock_spawn.return_value = {
                "ok": True,
                "observation_id": 1,
                "session_id": "test",
                "duration_s": 0.1,
                "final_response": "",
                "error": None,
                "timed_out": False,
            }
            result = _call({
                "skill": "made-up-skill",
                "expected_event_type": "made_up_event",
                "enabled_toolsets": ["perception"],
                "model": "test-model",
            })
            assert "error" not in result or result.get("ok") is True
            mock_spawn.assert_called_once()


class TestSpawnSubagentDelegation:
    def test_plutus_perception_uses_defaults(self):
        with patch("harness.tools.dispatchers.spawn_subagent.spawn_subagent_blocking") as mock_spawn:
            mock_spawn.return_value = {
                "ok": True,
                "observation_id": 42,
                "session_id": "subagent_plutus-perception_20260520",
                "duration_s": 180.0,
                "final_response": "Digest recorded as obs #42",
                "error": None,
                "timed_out": False,
            }
            result = _call({
                "skill": "plutus-perception",
                "expected_event_type": "perception_digest",
                "scope": "standard",
                "for_main_beat_at_unix": 1779300000.0,
            })
            assert result["ok"] is True
            assert result["observation_id"] == 42
            # Verify defaults were applied (kimi-k2.6 + perception/reflection/skills/search toolsets)
            kwargs = mock_spawn.call_args.kwargs
            assert kwargs["skill_name"] == "plutus-perception"
            assert kwargs["expected_event_type"] == "perception_digest"
            assert kwargs["model"] == "kimi-k2.6"
            assert "perception" in kwargs["enabled_toolsets"]
            assert "reflection" in kwargs["enabled_toolsets"]
            assert "search" in kwargs["enabled_toolsets"]
            assert kwargs["scope"] == "standard"
            assert kwargs["for_main_beat_at_unix"] == 1779300000.0

    def test_explicit_model_overrides_default(self):
        with patch("harness.tools.dispatchers.spawn_subagent.spawn_subagent_blocking") as mock_spawn:
            mock_spawn.return_value = {
                "ok": True, "observation_id": 1, "session_id": "x",
                "duration_s": 1.0, "final_response": "", "error": None,
                "timed_out": False,
            }
            _call({
                "skill": "plutus-perception",
                "expected_event_type": "perception_digest",
                "model": "deepseek-v4-pro",  # override
            })
            assert mock_spawn.call_args.kwargs["model"] == "deepseek-v4-pro"

    def test_explicit_toolsets_override_default(self):
        with patch("harness.tools.dispatchers.spawn_subagent.spawn_subagent_blocking") as mock_spawn:
            mock_spawn.return_value = {
                "ok": True, "observation_id": 1, "session_id": "x",
                "duration_s": 1.0, "final_response": "", "error": None,
                "timed_out": False,
            }
            _call({
                "skill": "plutus-perception",
                "expected_event_type": "perception_digest",
                "enabled_toolsets": ["perception"],  # narrowed
            })
            assert mock_spawn.call_args.kwargs["enabled_toolsets"] == ["perception"]

    def test_subagent_failure_returns_ok_false(self):
        with patch("harness.tools.dispatchers.spawn_subagent.spawn_subagent_blocking") as mock_spawn:
            mock_spawn.return_value = {
                "ok": False,
                "observation_id": None,
                "session_id": "x",
                "duration_s": 600.0,
                "final_response": "",
                "error": "inactivity timeout after 600s",
                "timed_out": True,
            }
            result = _call({
                "skill": "plutus-perception",
                "expected_event_type": "perception_digest",
            })
            # The dispatcher returns the full result dict — caller decides how to react.
            assert result["ok"] is False
            assert result["timed_out"] is True
            assert "timeout" in result["error"].lower()

    def test_dispatcher_internal_exception_returns_error(self):
        with patch("harness.tools.dispatchers.spawn_subagent.spawn_subagent_blocking") as mock_spawn:
            mock_spawn.side_effect = RuntimeError("provider resolution failed")
            result = _call({
                "skill": "plutus-perception",
                "expected_event_type": "perception_digest",
            })
            assert "error" in result
            assert "provider resolution failed" in result["error"]
