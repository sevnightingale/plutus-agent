"""Unit tests for the shared trade-readiness verdict (readiness.py)."""

import time
from unittest.mock import MagicMock, patch

from eth_account import Account

from trading.integrations.hyperliquid.readiness import check_registration

# Deterministic throwaway keypair for tests — NOT a real wallet.
TEST_KEY = "0x" + "11" * 32
TEST_ADDR = Account.from_key(TEST_KEY).address.lower()
MASTER = "0x" + "aa" * 20


def _fake_info(extra_agents):
    info = MagicMock()
    info.post.return_value = extra_agents
    return info


def _check(extra_agents, **kw):
    with patch(
        "trading.integrations.hyperliquid._client.get_info",
        return_value=_fake_info(extra_agents),
    ):
        return check_registration(MASTER, TEST_ADDR, TEST_KEY, **kw)


def test_missing_master_is_could_not_determine():
    r = check_registration("", TEST_ADDR, TEST_KEY)
    assert r["ready"] is False
    assert r["_exit"] == 2
    assert "ACP_AGENT_WALLET" in r["reason"]


def test_key_address_mismatch_is_not_ready():
    r = check_registration(MASTER, "0x" + "bb" * 20, TEST_KEY)
    assert r["ready"] is False
    assert r["_exit"] == 1
    assert "mismatch" in r["reason"]


def test_empty_extra_agents_is_the_dead_registration():
    r = _check([])
    assert r["ready"] is False
    assert r["_exit"] == 1
    assert "extraAgents=[]" in r["reason"]


def test_registered_unexpired_is_ready():
    valid_until = (time.time() + 90 * 86400) * 1000
    r = _check([{"name": "plutus-trader", "address": TEST_ADDR,
                 "validUntil": valid_until}])
    assert r["ready"] is True
    assert r["_exit"] == 0
    assert r["warn_expiring_soon"] is False
    assert r["matched_agent"]["address"] == TEST_ADDR


def test_expired_registration_is_not_ready():
    valid_until = (time.time() - 86400) * 1000
    r = _check([{"name": "plutus-trader", "address": TEST_ADDR,
                 "validUntil": valid_until}])
    assert r["ready"] is False
    assert r["_exit"] == 1
    assert "EXPIRED" in r["reason"]


def test_expiring_soon_is_ready_with_warning():
    valid_until = (time.time() + 3 * 86400) * 1000
    r = _check([{"name": "plutus-trader", "address": TEST_ADDR,
                 "validUntil": valid_until}], warn_days=7)
    assert r["ready"] is True
    assert r["warn_expiring_soon"] is True
    assert "re-register soon" in r["reason"]


def test_data_point_reads_env_and_strips_exit(monkeypatch):
    from trading.integrations.hyperliquid.data_points import hl_trade_readiness

    monkeypatch.setenv("ACP_AGENT_WALLET", MASTER)
    monkeypatch.setenv("HL_API_WALLET_ADDRESS", TEST_ADDR)
    monkeypatch.setenv("HL_API_WALLET_KEY", TEST_KEY)
    valid_until = (time.time() + 90 * 86400) * 1000
    with patch(
        "trading.integrations.hyperliquid._client.get_info",
        return_value=_fake_info([{"name": "plutus-trader", "address": TEST_ADDR,
                                  "validUntil": valid_until}]),
    ):
        r = hl_trade_readiness()
    assert r["ready"] is True
    assert "_exit" not in r
