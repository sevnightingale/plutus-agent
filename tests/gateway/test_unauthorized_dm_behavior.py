from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness.gateway.config import GatewayConfig, Platform, PlatformConfig
from harness.gateway.platforms.base import MessageEvent
from harness.gateway.session import SessionSource


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "TELEGRAM_ALLOWED_USERS",
        "DISCORD_ALLOWED_USERS",
        "SLACK_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "TELEGRAM_ALLOW_ALL_USERS",
        "DISCORD_ALLOW_ALL_USERS",
        "SLACK_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_event(platform: Platform, user_id: str, chat_id: str) -> MessageEvent:
    return MessageEvent(
        text="hello",
        message_id="m1",
        source=SessionSource(
            platform=platform,
            user_id=user_id,
            chat_id=chat_id,
            user_name="tester",
            chat_type="dm",
        ),
    )


def _make_runner(platform: Platform, config: GatewayConfig):
    from harness.gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = config
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {platform: adapter}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    # Attributes required by _handle_message for the authorized-user path
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._update_prompts = {}
    runner.hooks = SimpleNamespace(dispatch=AsyncMock(return_value=None))
    runner._sessions = {}
    return runner, adapter


def test_star_wildcard_works_for_any_platform(monkeypatch):
    """The * wildcard should work generically as an allow-all."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "*")

    runner, _adapter = _make_runner(
        Platform.TELEGRAM,
        GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")}),
    )

    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="123456789",
        chat_id="123456789",
        user_name="stranger",
        chat_type="dm",
    )
    assert runner._is_user_authorized(source) is True


@pytest.mark.asyncio
async def test_unauthorized_dm_pairs_by_default(monkeypatch):
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)
    runner.pairing_store.generate_code.return_value = "ABC12DEF"

    result = await runner._handle_message(
        _make_event(
            Platform.TELEGRAM,
            "15551234567",
            "15551234567",
        )
    )

    assert result is None
    runner.pairing_store.generate_code.assert_called_once_with(
        "telegram",
        "15551234567",
        "tester",
    )
    adapter.send.assert_awaited_once()
    assert "ABC12DEF" in adapter.send.await_args.args[1]


@pytest.mark.asyncio
async def test_unauthorized_dm_can_be_ignored(monkeypatch):
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                token="t",
                extra={"unauthorized_dm_behavior": "ignore"},
            ),
        },
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)

    result = await runner._handle_message(
        _make_event(
            Platform.TELEGRAM,
            "15551234567",
            "15551234567",
        )
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limited_user_gets_no_response(monkeypatch):
    """When a user is already rate-limited, pairing messages are silently ignored."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)
    runner.pairing_store._is_rate_limited.return_value = True

    result = await runner._handle_message(
        _make_event(
            Platform.TELEGRAM,
            "15551234567",
            "15551234567",
        )
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejection_message_records_rate_limit(monkeypatch):
    """After sending a 'too many requests' rejection, rate limit is recorded
    so subsequent messages are silently ignored."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)
    runner.pairing_store.generate_code.return_value = None  # triggers rejection

    result = await runner._handle_message(
        _make_event(
            Platform.TELEGRAM,
            "15551234567",
            "15551234567",
        )
    )

    assert result is None
    adapter.send.assert_awaited_once()
    assert "Too many" in adapter.send.await_args.args[1]
    runner.pairing_store._record_rate_limit.assert_called_once_with(
        "telegram", "15551234567"
    )


@pytest.mark.asyncio
async def test_global_ignore_suppresses_pairing_reply(monkeypatch):
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        unauthorized_dm_behavior="ignore",
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)

    result = await runner._handle_message(
        _make_event(
            Platform.TELEGRAM,
            "12345",
            "12345",
        )
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Allowlist-configured platforms default to "ignore" for unauthorized users
# (#9337: gateway sends pairing spam when allowlist is configured)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telegram_with_allowlist_ignores_unauthorized_dm(monkeypatch):
    """Same behavior for Telegram: allowlist ⟹ ignore unauthorized DMs."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")

    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True)},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)

    result = await runner._handle_message(
        _make_event(Platform.TELEGRAM, "999999999", "999999999")
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_allowlist_ignores_unauthorized_dm(monkeypatch):
    """GATEWAY_ALLOWED_USERS also triggers the 'ignore' behavior."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", "111111111")

    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)

    result = await runner._handle_message(
        _make_event(Platform.TELEGRAM, "999999999", "999999999")
    )

    assert result is None
    runner.pairing_store.generate_code.assert_not_called()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_allowlist_still_pairs_by_default(monkeypatch):
    """Without any allowlist, pairing behavior is preserved (open gateway)."""
    _clear_auth_env(monkeypatch)
    # No TELEGRAM_ALLOWED_USERS, no GATEWAY_ALLOWED_USERS

    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, adapter = _make_runner(Platform.TELEGRAM, config)
    runner.pairing_store.generate_code.return_value = "PAIR1234"

    result = await runner._handle_message(
        _make_event(Platform.TELEGRAM, "999999999", "999999999")
    )

    assert result is None
    runner.pairing_store.generate_code.assert_called_once()
    adapter.send.assert_awaited_once()
    assert "PAIR1234" in adapter.send.await_args.args[1]


def test_explicit_pair_config_overrides_allowlist_default(monkeypatch):
    """Explicit unauthorized_dm_behavior='pair' overrides the allowlist default.

    Operators can opt back in to pairing even with an allowlist by setting
    unauthorized_dm_behavior: pair in their platform config.  We test the
    _get_unauthorized_dm_behavior resolver directly to avoid the full
    _handle_message pipeline which requires extensive runner state.
    """
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")

    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(
                enabled=True,
                token="t",
                extra={"unauthorized_dm_behavior": "pair"},  # explicit override
            ),
        },
    )
    runner, _adapter = _make_runner(Platform.TELEGRAM, config)

    # The per-platform explicit config should beat the allowlist-derived default
    behavior = runner._get_unauthorized_dm_behavior(Platform.TELEGRAM)
    assert behavior == "pair"


def test_allowlist_authorized_user_returns_ignore_for_unauthorized(monkeypatch):
    """_get_unauthorized_dm_behavior returns 'ignore' when allowlist is set.

    We test the resolver directly.  The full _handle_message path for
    authorized users is covered by the integration tests in this module.
    """
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111111111")

    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, _adapter = _make_runner(Platform.TELEGRAM, config)

    behavior = runner._get_unauthorized_dm_behavior(Platform.TELEGRAM)
    assert behavior == "ignore"


def test_get_unauthorized_dm_behavior_no_allowlist_returns_pair(monkeypatch):
    """Without any allowlist, 'pair' is still the default."""
    _clear_auth_env(monkeypatch)

    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")},
    )
    runner, _adapter = _make_runner(Platform.TELEGRAM, config)

    behavior = runner._get_unauthorized_dm_behavior(Platform.TELEGRAM)
    assert behavior == "pair"
