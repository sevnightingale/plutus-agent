"""Tests for the synthetic-message injection path.

Cron ticks and watcher wake events route through ``deliver_synthetic_message``
in unified-session mode (PLUTUS architecture). The synthetic prompt MUST NOT
echo to chat; the busy-handler MUST queue without interrupting an active
operator turn; the approval policy MUST treat the turn as unattended.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so we can import gateway code without heavy deps
# ---------------------------------------------------------------------------
import sys, types

_tg = types.ModuleType("telegram")
_tg.constants = types.ModuleType("telegram.constants")
_ct = MagicMock()
_ct.SUPERGROUP = "supergroup"
_ct.GROUP = "group"
_ct.PRIVATE = "private"
_tg.constants.ChatType = _ct
sys.modules.setdefault("telegram", _tg)
sys.modules.setdefault("telegram.constants", _tg.constants)
sys.modules.setdefault("telegram.ext", types.ModuleType("telegram.ext"))

from harness.gateway.platforms.base import (
    MessageEvent,
    MessageType,
    SessionSource,
    build_session_key,
)


def _make_runner():
    from harness.gateway.run import GatewayRunner, _AGENT_PENDING_SENTINEL

    runner = object.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._busy_ack_ts = {}
    runner._draining = False
    runner.adapters = {}
    runner.config = MagicMock()
    runner.session_store = None
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    return runner, _AGENT_PENDING_SENTINEL


def _make_adapter(platform_val="telegram"):
    adapter = MagicMock()
    adapter._pending_messages = {}
    adapter._send_with_retry = AsyncMock()
    adapter.send = AsyncMock()
    adapter.handle_message = AsyncMock(return_value=None)
    adapter.config = MagicMock()
    adapter.config.extra = {}
    adapter.platform = MagicMock(value=platform_val)
    return adapter


def _make_synthetic_event(text="[SYSTEM TICK] heartbeat", kind="cron:plutus-heartbeat", chat_id="123"):
    source = SessionSource(
        platform=MagicMock(value="telegram"),
        chat_id=chat_id,
        chat_type="private",
        user_id="user1",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        message_id=None,
        internal=True,
        synthetic_kind=kind,
    )


# ---------------------------------------------------------------------------
# MessageEvent.synthetic_kind field
# ---------------------------------------------------------------------------

class TestMessageEventSyntheticKind:
    def test_default_is_none(self):
        ev = MessageEvent(text="hi")
        assert ev.synthetic_kind is None

    def test_set_at_construction(self):
        ev = MessageEvent(text="hi", synthetic_kind="cron:foo")
        assert ev.synthetic_kind == "cron:foo"

    def test_internal_flag_independent(self):
        ev = MessageEvent(text="hi", synthetic_kind="cron:foo", internal=True)
        assert ev.internal is True
        assert ev.synthetic_kind == "cron:foo"


# ---------------------------------------------------------------------------
# session_context ContextVar helpers
# ---------------------------------------------------------------------------

class TestSyntheticKindContextVar:
    def test_default_is_empty_string(self):
        from harness.gateway.session_context import get_synthetic_kind
        # No prior set in this context
        assert get_synthetic_kind() == ""

    def test_set_and_reset(self):
        from harness.gateway.session_context import (
            set_synthetic_kind,
            get_synthetic_kind,
            reset_synthetic_kind,
        )
        token = set_synthetic_kind("cron:plutus-heartbeat")
        try:
            assert get_synthetic_kind() == "cron:plutus-heartbeat"
        finally:
            reset_synthetic_kind(token)
        assert get_synthetic_kind() == ""

    def test_set_none_clears_to_empty(self):
        from harness.gateway.session_context import set_synthetic_kind, get_synthetic_kind
        set_synthetic_kind(None)
        assert get_synthetic_kind() == ""


# ---------------------------------------------------------------------------
# Approval policy: synthetic origin counts as unattended
# ---------------------------------------------------------------------------

class TestApprovalUnattended:
    def setup_method(self):
        import os
        self._saved = {
            k: os.environ.get(k)
            for k in ("HERMES_CRON_SESSION", "HERMES_GATEWAY_SESSION", "HERMES_INTERACTIVE")
        }
        for k in self._saved:
            os.environ.pop(k, None)
        from harness.gateway.session_context import set_synthetic_kind
        set_synthetic_kind(None)

    def teardown_method(self):
        import os
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from harness.gateway.session_context import set_synthetic_kind
        set_synthetic_kind(None)

    def test_helper_false_when_no_origin_set(self):
        from harness.tools.approval import _is_unattended_origin_turn
        assert _is_unattended_origin_turn() is False

    def test_helper_true_when_legacy_env_set(self):
        import os
        os.environ["HERMES_CRON_SESSION"] = "1"
        from harness.tools.approval import _is_unattended_origin_turn
        assert _is_unattended_origin_turn() is True

    def test_helper_true_when_synthetic_kind_set(self):
        from harness.gateway.session_context import set_synthetic_kind
        from harness.tools.approval import _is_unattended_origin_turn
        set_synthetic_kind("cron:plutus-heartbeat")
        try:
            assert _is_unattended_origin_turn() is True
        finally:
            set_synthetic_kind(None)

    def test_check_dangerous_blocks_synthetic_when_deny(self):
        """Cron-mode=deny + synthetic origin → BLOCKED with informative message."""
        from harness.gateway.session_context import set_synthetic_kind
        from harness.tools.approval import check_dangerous_command

        set_synthetic_kind("cron:plutus-heartbeat")
        try:
            with patch("harness.tools.approval._get_cron_approval_mode", return_value="deny"):
                result = check_dangerous_command("rm -rf /tmp/test", "local")
        finally:
            set_synthetic_kind(None)

        assert result["approved"] is False
        assert "cron tick or watcher wake" in result["message"]

    def test_check_dangerous_allows_synthetic_when_approve(self):
        """Cron-mode=approve + synthetic origin → allowed without prompting."""
        from harness.gateway.session_context import set_synthetic_kind
        from harness.tools.approval import check_dangerous_command

        set_synthetic_kind("wake:hl_position_status_change")
        try:
            with patch("harness.tools.approval._get_cron_approval_mode", return_value="approve"):
                result = check_dangerous_command("rm -rf /tmp/test", "local")
        finally:
            set_synthetic_kind(None)

        assert result["approved"] is True

    def test_normal_operator_turn_unaffected(self):
        """No synthetic kind, no cron env → falls through to normal flow."""
        from harness.tools.approval import check_dangerous_command

        # Non-dangerous command — sanity check that the early-return doesn't
        # hijack normal operator turns.
        result = check_dangerous_command("ls -la", "local")
        assert result["approved"] is True


# ---------------------------------------------------------------------------
# Busy handler: synthetic events queue without interrupting or acking
# ---------------------------------------------------------------------------

class TestBusySynthetic:
    @pytest.mark.asyncio
    async def test_synthetic_busy_does_not_interrupt(self):
        """Cron tick arriving while operator turn is in flight does NOT interrupt."""
        runner, _ = _make_runner()
        runner._busy_input_mode = "interrupt"
        adapter = _make_adapter()

        event = _make_synthetic_event()
        sk = build_session_key(event.source)
        runner.adapters[event.source.platform] = adapter

        agent = MagicMock()
        runner._running_agents[sk] = agent

        # Patch both the module-level and inner-scoped imports of
        # merge_pending_message_event so we can observe the queue call
        # without invoking the real merge logic.
        with patch("harness.gateway.run.merge_pending_message_event") as mock_outer, \
             patch("harness.gateway.platforms.base.merge_pending_message_event") as mock_inner:
            result = await runner._handle_active_session_busy_message(event, sk)

        assert result is True
        agent.interrupt.assert_not_called()
        adapter._send_with_retry.assert_not_called()
        # At least one of the patched names should have been invoked
        # (the function uses the inner import in the busy branch).
        assert mock_outer.called or mock_inner.called

    @pytest.mark.asyncio
    async def test_real_operator_turn_still_interrupts(self):
        """Sanity: synthetic-aware branch only kicks in for synthetic events."""
        runner, _ = _make_runner()
        runner._busy_input_mode = "interrupt"
        adapter = _make_adapter()

        # NOT synthetic
        source = SessionSource(
            platform=MagicMock(value="telegram"),
            chat_id="123",
            chat_type="private",
            user_id="user1",
        )
        event = MessageEvent(
            text="real operator turn",
            message_type=MessageType.TEXT,
            source=source,
            message_id="m1",
        )
        sk = build_session_key(event.source)
        runner.adapters[event.source.platform] = adapter

        import time as _t
        agent = MagicMock()
        agent.get_activity_summary.return_value = {
            "api_call_count": 5,
            "max_iterations": 60,
            "current_tool": "terminal",
            "last_activity_ts": _t.time(),
            "last_activity_desc": "tool",
            "seconds_since_activity": 0.1,
        }
        runner._running_agents[sk] = agent
        runner._running_agents_ts[sk] = _t.time() - 30

        with patch("harness.gateway.run.merge_pending_message_event"):
            await runner._handle_active_session_busy_message(event, sk)

        agent.interrupt.assert_called_once()


# ---------------------------------------------------------------------------
# deliver_synthetic_message: routes through adapter.handle_message, sets fields
# ---------------------------------------------------------------------------

