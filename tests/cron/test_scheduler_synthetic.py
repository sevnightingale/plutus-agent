"""Tests for the unified-session synthetic-injection path in cron.scheduler.

When the gateway is provided AND a primary session is resolvable, run_job
routes the cron tick into the operator's persistent platform session via
gateway.deliver_synthetic_message. Otherwise it falls back to the legacy
fresh-session-per-tick path.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _resolve_origin contract preservation + _resolve_origin_for_injection
# ---------------------------------------------------------------------------

class TestResolveOriginForInjection:
    def setup_method(self):
        # Force the primary-session cache to re-load each test.
        import harness.cron.scheduler as s
        s._PRIMARY_SESSION_CACHE = {"loaded": False, "value": None}
        # Clear env vars that would seep into the fallback.
        import os
        self._saved_env = {
            k: os.environ.get(k)
            for k in (
                "TELEGRAM_HOME_CHANNEL", "DISCORD_HOME_CHANNEL",
                "SLACK_HOME_CHANNEL", "MATRIX_HOME_ROOM",
            )
        }
        for k in self._saved_env:
            os.environ.pop(k, None)

    def teardown_method(self):
        import os
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import harness.cron.scheduler as s
        s._PRIMARY_SESSION_CACHE = {"loaded": False, "value": None}

    def test_resolve_origin_stays_pure_no_fallback(self):
        """The original _resolve_origin contract — no fallback — is preserved."""
        from harness.cron.scheduler import _resolve_origin
        assert _resolve_origin({}) is None
        assert _resolve_origin({"origin": None}) is None
        assert _resolve_origin({"origin": {"platform": "telegram"}}) is None
        assert _resolve_origin(
            {"origin": {"platform": "telegram", "chat_id": "999"}}
        ) == {"platform": "telegram", "chat_id": "999"}

    def test_injection_falls_back_to_telegram_home_channel(self):
        """When job has no origin AND no notifications.primary_session,
        TELEGRAM_HOME_CHANNEL env unlocks unified injection."""
        import os
        os.environ["TELEGRAM_HOME_CHANNEL"] = "1054536871"
        from harness.cron.scheduler import _resolve_origin_for_injection
        result = _resolve_origin_for_injection({})
        assert result is not None
        assert result["platform"] == "telegram"
        assert result["chat_id"] == "1054536871"
        # Telegram DM convention: user_id == chat_id.
        assert result["user_id"] == "1054536871"

    def test_injection_returns_none_when_no_fallback_and_no_origin(self):
        from harness.cron.scheduler import _resolve_origin_for_injection
        assert _resolve_origin_for_injection({}) is None

    def test_injection_prefers_job_origin_over_fallback(self):
        import os
        os.environ["TELEGRAM_HOME_CHANNEL"] = "fallback-chat"
        from harness.cron.scheduler import _resolve_origin_for_injection
        result = _resolve_origin_for_injection(
            {"origin": {"platform": "discord", "chat_id": "explicit-chat"}}
        )
        assert result["platform"] == "discord"
        assert result["chat_id"] == "explicit-chat"


# ---------------------------------------------------------------------------
# run_job dispatches to synthetic injection when gateway+origin present
# ---------------------------------------------------------------------------

class TestRunJobDispatch:
    def setup_method(self):
        import harness.cron.scheduler as s
        s._PRIMARY_SESSION_CACHE = {"loaded": False, "value": None}

    def teardown_method(self):
        import harness.cron.scheduler as s
        s._PRIMARY_SESSION_CACHE = {"loaded": False, "value": None}

    def test_run_job_falls_back_to_legacy_when_gateway_missing(self):
        """No gateway → legacy fresh-session path."""
        from harness.cron import scheduler

        captured = {}

        def fake_legacy(job, prompt, origin):
            captured["called"] = True
            captured["origin"] = origin
            return (True, "doc", "response", None)

        job = {
            "id": "job1", "name": "test",
            "origin": {"platform": "telegram", "chat_id": "111"},
        }

        with patch("harness.cron.scheduler._legacy_run_job", side_effect=fake_legacy):
            success, _, response, error = scheduler.run_job(job, gateway=None)

        assert success
        assert response == "response"
        assert error is None
        assert captured["called"] is True

    def test_run_job_uses_synthetic_when_gateway_and_origin(self):
        """Gateway + injection-eligible origin → unified-session path."""
        from harness.cron import scheduler

        captured = {}

        def fake_synthetic(job, prompt, origin, gw, loop, timeout):
            captured["called"] = True
            captured["origin"] = origin
            captured["gateway"] = gw
            return (True, "doc", "response", None)

        job = {
            "id": "job1", "name": "test",
            "origin": {"platform": "telegram", "chat_id": "111"},
        }
        # Real loop so getattr fallback resolves
        loop = asyncio.new_event_loop()
        try:
            mock_gateway = MagicMock()
            mock_gateway._event_loop = loop

            with patch("harness.cron.scheduler._run_job_via_synthetic",
                       side_effect=fake_synthetic), \
                 patch("harness.cron.scheduler._legacy_run_job") as mock_legacy:
                success, _, response, _ = scheduler.run_job(job, gateway=mock_gateway)
        finally:
            loop.close()

        assert success
        assert captured["called"] is True
        assert captured["origin"]["chat_id"] == "111"
        assert captured["gateway"] is mock_gateway
        mock_legacy.assert_not_called()

    def test_run_job_uses_synthetic_with_primary_session_fallback(self):
        """Job without origin but with TELEGRAM_HOME_CHANNEL set → still synthetic."""
        from harness.cron import scheduler
        import os

        os.environ["TELEGRAM_HOME_CHANNEL"] = "1054536871"
        try:
            scheduler._PRIMARY_SESSION_CACHE = {"loaded": False, "value": None}
            captured = {}

            def fake_synthetic(job, prompt, origin, gw, loop, timeout):
                captured["origin"] = origin
                return (True, "doc", "ok", None)

            job = {"id": "noorigin", "name": "n"}
            loop = asyncio.new_event_loop()
            try:
                mock_gateway = MagicMock()
                mock_gateway._event_loop = loop

                with patch("harness.cron.scheduler._run_job_via_synthetic",
                           side_effect=fake_synthetic):
                    scheduler.run_job(job, gateway=mock_gateway)
            finally:
                loop.close()

            assert captured["origin"]["chat_id"] == "1054536871"
        finally:
            os.environ.pop("TELEGRAM_HOME_CHANNEL", None)

    def test_run_job_falls_back_to_legacy_when_no_injection_origin(self):
        """Gateway provided but no origin, no fallback → legacy path."""
        from harness.cron import scheduler

        captured = {}

        def fake_legacy(job, prompt, origin):
            captured["called"] = True
            return (True, "doc", "ok", None)

        job = {"id": "n", "name": "n"}
        loop = asyncio.new_event_loop()
        try:
            mock_gateway = MagicMock()
            mock_gateway._event_loop = loop

            with patch("harness.cron.scheduler._legacy_run_job", side_effect=fake_legacy), \
                 patch("harness.cron.scheduler._run_job_via_synthetic") as mock_synth:
                scheduler.run_job(job, gateway=mock_gateway)
        finally:
            loop.close()

        assert captured["called"]
        mock_synth.assert_not_called()

    def test_run_job_routes_to_legacy_when_model_override_set(self):
        """V2: jobs with `model` set (plutus-ops on deepseek-v4-flash, per-thesis
        Flavor B crons, one-shot future-checks) MUST route to legacy fresh-session.

        Synthetic injection runs against the operator chat's persistent AIAgent
        whose model was bound at session creation. Routing a model override
        through synthetic would silently use the operator session's model
        instead of what the cron requested — silent wrong-model bug. Routing
        to legacy gives the cron its own fresh AIAgent built with the correct
        model.
        """
        from harness.cron import scheduler

        captured = {}

        def fake_legacy(job, prompt, origin):
            captured["called"] = True
            captured["model_in_job"] = job.get("model")
            return (True, "doc", "ok", None)

        # Job with explicit deepseek-v4-flash override (mimics V2 plutus-ops).
        job = {
            "id": "plutus-ops",
            "name": "plutus-ops",
            "model": "deepseek-v4-flash",
            "provider": "opencode-go",
            "origin": {"platform": "telegram", "chat_id": "111"},
        }
        loop = asyncio.new_event_loop()
        try:
            mock_gateway = MagicMock()
            mock_gateway._event_loop = loop

            with patch("harness.cron.scheduler._legacy_run_job", side_effect=fake_legacy), \
                 patch("harness.cron.scheduler._run_job_via_synthetic") as mock_synth:
                scheduler.run_job(job, gateway=mock_gateway)
        finally:
            loop.close()

        # Even with gateway + valid origin available, the override forces legacy.
        assert captured["called"], "legacy path must be used when model is overridden"
        assert captured["model_in_job"] == "deepseek-v4-flash"
        mock_synth.assert_not_called()

    def test_run_job_uses_synthetic_when_no_model_override(self):
        """V2: jobs WITHOUT `model` set (plutus-main on operator-session default,
        plutus-macro-cache, plutus-daily-check-in) route to unified-session
        synthetic injection. Operator session's model carries them."""
        from harness.cron import scheduler

        captured = {}

        def fake_synthetic(job, prompt, origin, gw, loop, timeout):
            captured["called"] = True
            captured["model"] = job.get("model")
            return (True, "doc", "ok", None)

        # plutus-main shape: explicit origin, NO model override.
        job = {
            "id": "plutus-main",
            "name": "plutus-main",
            "origin": {"platform": "telegram", "chat_id": "111"},
            # No model field — inherits operator session's model.
        }
        loop = asyncio.new_event_loop()
        try:
            mock_gateway = MagicMock()
            mock_gateway._event_loop = loop

            with patch("harness.cron.scheduler._run_job_via_synthetic", side_effect=fake_synthetic), \
                 patch("harness.cron.scheduler._legacy_run_job") as mock_legacy:
                scheduler.run_job(job, gateway=mock_gateway)
        finally:
            loop.close()

        assert captured["called"], "synthetic path must be used when no model override"
        assert captured["model"] is None
        mock_legacy.assert_not_called()


# ---------------------------------------------------------------------------
# _wrap_synthetic_prompt formatting
# ---------------------------------------------------------------------------

class TestWrapSyntheticPrompt:
    def test_wraps_with_marker(self):
        from harness.cron.scheduler import _wrap_synthetic_prompt
        from datetime import datetime
        ts = datetime(2026, 5, 9, 18, 0, 0)
        out = _wrap_synthetic_prompt("run heartbeat", kind="cron:plutus-heartbeat", ts=ts)
        assert out.startswith("[SYSTEM TICK — cron:plutus-heartbeat — 2026-05-09T18:00:00Z]")
        assert "run heartbeat" in out


# ---------------------------------------------------------------------------
# _run_job_via_synthetic uses run_coroutine_threadsafe and returns properly
# ---------------------------------------------------------------------------

class TestRunJobViaSynthetic:
    def test_routes_through_deliver_synthetic_message(self):
        """End-to-end: prompt is wrapped, kind is set, response surfaced."""
        from harness.cron.scheduler import _run_job_via_synthetic

        loop = asyncio.new_event_loop()
        # Drive the loop in a background thread so run_coroutine_threadsafe
        # can post to it.
        import threading
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            mock_gateway = MagicMock()
            captured = {}

            async def fake_deliver(*, platform, chat_id, text, kind, **kw):
                captured["platform"] = platform
                captured["chat_id"] = chat_id
                captured["text"] = text
                captured["kind"] = kind
                return "Plutus response text"

            mock_gateway.deliver_synthetic_message = fake_deliver

            job = {
                "id": "plutus-heartbeat", "name": "plutus-heartbeat",
                "schedule_display": "0 * * * *",
            }
            origin = {
                "platform": "telegram", "chat_id": "1054536871",
                "user_id": "1054536871", "chat_type": "dm",
            }
            success, doc, response, error = _run_job_via_synthetic(
                job, "run heartbeat", origin, mock_gateway, loop, 30.0,
            )
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()

        assert success is True
        assert error is None
        assert response == "Plutus response text"
        assert captured["chat_id"] == "1054536871"
        assert captured["kind"] == "cron:plutus-heartbeat"
        assert captured["text"].startswith("[SYSTEM TICK — cron:plutus-heartbeat —")
        assert "run heartbeat" in captured["text"]
        assert "## Response" in doc
        assert "Plutus response text" in doc
        assert "synthetic injection" in doc

    def test_returns_failure_on_unknown_platform(self):
        from harness.cron.scheduler import _run_job_via_synthetic

        loop = asyncio.new_event_loop()
        try:
            success, doc, response, error = _run_job_via_synthetic(
                {"id": "j", "name": "j"},
                "prompt",
                {"platform": "INVALID_PLATFORM_XYZ", "chat_id": "1"},
                MagicMock(),
                loop,
                30.0,
            )
        finally:
            loop.close()

        assert success is False
        assert response == ""
        assert "unknown origin platform" in error
        assert "FAILED" in doc
