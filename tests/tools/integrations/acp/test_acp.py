"""ACP integration — wrapper shape, subprocess calls, capital_movement side effect.

All tests use a mocked _cli.acp() runner so they pass without the real
binary installed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.integrations.acp import _cli, _env, setup as acp_setup, data_points, operations, identity, accounts, events
from tools.core import account_registry, identity_registry


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Redirect HERMES_HOME so .env writes / lifecycle.db live in tmp_path.

    CRITICAL: also resets the get_lifecycle_db() singleton — without this,
    if a previous test (or test collection) initialised the singleton against
    the production ~/.plutus-agent/lifecycle.db, this test's writes leak
    into production. Earlier runs of test_acp_wallet_send_writes_capital_movement
    polluted the operator's lifecycle.db with 6 fake `0xdeadbeef` rows
    before this fixture got hardened.
    """
    home = tmp_path / "plutus-agent"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import importlib, plutus_constants
    importlib.reload(plutus_constants)
    from agent.lifecycle_db import reset_lifecycle_db_singleton
    reset_lifecycle_db_singleton()
    yield home
    reset_lifecycle_db_singleton()


@pytest.fixture
def mock_acp(monkeypatch):
    """Replace _cli.acp + is_installed across modules that bound them at import."""
    runner = MagicMock(return_value={})
    is_installed = MagicMock(return_value=True)
    # Patch in _cli (canonical) AND in modules that bound them at import time.
    monkeypatch.setattr(_cli, "acp", runner)
    monkeypatch.setattr(_cli, "is_installed", is_installed)
    monkeypatch.setattr(data_points, "_cli", _cli)  # data_points uses _cli.acp dotted
    monkeypatch.setattr(operations._cli, "acp", runner)
    monkeypatch.setattr(operations._cli, "is_installed", is_installed)
    monkeypatch.setattr(identity._cli, "acp", runner)
    monkeypatch.setattr(identity._cli, "is_installed", is_installed)
    monkeypatch.setattr(accounts._cli, "acp", runner)
    monkeypatch.setattr(accounts._cli, "is_installed", is_installed)
    monkeypatch.setattr(acp_setup._cli, "acp", runner)
    monkeypatch.setattr(acp_setup._cli, "is_installed", is_installed)
    return runner


# ─── data points ──────────────────────────────────────────────────────────


def test_acp_wallet_balance_calls_correct_args(mock_acp):
    mock_acp.return_value = {"balances": []}
    res = data_points.acp_wallet_balance(chain_id="1")
    assert res == {"balances": []}
    mock_acp.assert_called_once_with("wallet", "balance", "--chain-id", "1")


def test_acp_browse_offerings(mock_acp):
    mock_acp.return_value = {"offerings": []}
    data_points.acp_browse_offerings("research", top_k=5, sort_by="price")
    mock_acp.assert_called_once_with("browse", "research", "--top-k", "5", "--sort-by", "price")


def test_acp_chain_list(mock_acp):
    mock_acp.return_value = {"chains": [{"chain_id": "1", "name": "ethereum"}]}
    res = data_points.acp_chain_list()
    assert "chains" in res
    mock_acp.assert_called_once_with("chain", "list")


# ─── operations ───────────────────────────────────────────────────────────


def test_acp_client_create_job_legacy_flag(mock_acp):
    mock_acp.return_value = {"job_id": "j1", "status": "created"}
    res_str = operations._acp_client_create_job({
        "provider": "0xd478a8B40372db16cA8045F28C6FE07228F3781A",
        "offering_name": "perp_deposit", "legacy": True,
        "requirements": {"amount": "25"},
    })
    res = json.loads(res_str)
    assert res["job_id"] == "j1"
    mock_acp.assert_called_once()
    assert "--legacy" in mock_acp.call_args.args


def test_acp_client_fund(mock_acp):
    mock_acp.return_value = {"funded": True}
    res_str = operations._acp_client_fund({"job_id": "j1", "amount": 25})
    res = json.loads(res_str)
    assert res["funded"] is True
    mock_acp.assert_called_once_with(
        "client", "fund",
        "--job-id", "j1", "--chain-id", "8453", "--amount", "25",
    )


# ─── identity ─────────────────────────────────────────────────────────────


def test_acp_whoami_registers_identity_system(mock_acp):
    identity_registry.reset()
    mock_acp.return_value = {"address": "0xfeedface", "name": "Plutus"}
    identity._acp_identity_registered = False  # reset module-level guard
    identity._acp_whoami({})
    # Identity system should now be registered
    entry = identity_registry.lookup("acp")
    assert entry.name == "acp"


def test_acp_agent_use_requires_id():
    res_str = identity._acp_agent_use({})
    res = json.loads(res_str)
    assert res["error"]


# ─── setup ────────────────────────────────────────────────────────────────


def test_acp_install_check_when_not_installed(mock_acp, monkeypatch):
    monkeypatch.setattr(_cli, "is_installed", lambda: False)
    monkeypatch.setattr(acp_setup._cli, "is_installed", lambda: False)
    res_str = acp_setup._acp_install_check({})
    res = json.loads(res_str)
    assert res["installed"] is False
    assert "npm install" in res["install_command"]


def test_acp_install_check_when_installed(mock_acp):
    mock_acp.return_value = {"raw_output": "v1.0.5\n"}
    # The version probe uses capture=False so it returns string. Mock-friendly.
    mock_acp.return_value = "v1.0.5"
    monkeypatch_targets = {}
    res_str = acp_setup._acp_install_check({})
    res = json.loads(res_str)
    assert res["installed"] is True


# ─── accounts (no-op when not configured) ─────────────────────────────────


def test_accounts_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(accounts._cli, "is_installed", lambda: False)
    # Re-running the discover should be a no-op (already ran at import)
    accounts._discover_and_register()
    # No exception is the success criterion


# ─── events ───────────────────────────────────────────────────────────────


def test_start_event_stream_noop_when_not_installed(monkeypatch):
    monkeypatch.setattr(events._cli, "is_installed", lambda: False)
    out = events.start_event_stream()
    assert out == {}


# ─── env helpers ──────────────────────────────────────────────────────────


def test_env_set_and_get_round_trip(temp_home):
    _env.set_env("TEST_KEY_XYZ", "value123")
    assert _env.get_env("TEST_KEY_XYZ") == "value123"
    # File-backed
    assert "TEST_KEY_XYZ=value123" in (temp_home / ".env").read_text()
