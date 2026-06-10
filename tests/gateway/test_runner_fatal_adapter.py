from unittest.mock import AsyncMock

import pytest

from harness.gateway.config import GatewayConfig, Platform, PlatformConfig
from harness.gateway.platforms.base import BasePlatformAdapter
from harness.gateway.run import GatewayRunner


class _FatalAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="token"), Platform.TELEGRAM)

    async def connect(self) -> bool:
        self._set_fatal_error(
            "telegram_token_lock",
            "Another local Hermes gateway is already using this Telegram bot token.",
            retryable=False,
        )
        return False

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


class _RuntimeRetryableAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="token"), Platform.TELEGRAM)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.mark.asyncio
async def test_runner_requests_clean_exit_for_nonretryable_startup_conflict(monkeypatch, tmp_path):
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _FatalAdapter())

    ok = await runner.start()

    assert ok is True
    assert runner.should_exit_cleanly is True
    assert "already using this Telegram bot token" in runner.exit_reason


@pytest.mark.asyncio
async def test_runner_queues_retryable_runtime_fatal_for_reconnection(monkeypatch, tmp_path):
    """Retryable runtime fatal errors queue the platform for reconnection
    instead of shutting down the gateway."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _RuntimeRetryableAdapter()
    adapter._set_fatal_error(
        "telegram_connection_lost",
        "Telegram connection dropped unexpectedly (code 1).",
        retryable=True,
    )

    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner.stop = AsyncMock()

    await runner._handle_adapter_fatal_error(adapter)

    # Should shut down with failure — systemd Restart=on-failure will restart
    runner.stop.assert_awaited_once()
    assert runner._exit_with_failure is True
    assert Platform.TELEGRAM in runner._failed_platforms
    assert runner._failed_platforms[Platform.TELEGRAM]["attempts"] == 0
