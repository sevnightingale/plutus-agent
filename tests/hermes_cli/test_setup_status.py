"""plutus-agent setup-status — dashboard rendering + check shape."""

from __future__ import annotations

import io
import sys

import pytest


def test_setup_status_runs_without_crash(capsys, monkeypatch):
    """End-to-end smoke: runs the dashboard and prints the readiness line."""
    # Force a clean state so checks behave deterministically
    monkeypatch.delenv("HL_API_WALLET_KEY", raising=False)
    monkeypatch.delenv("HL_PUBLIC_ADDRESS", raising=False)

    from harness.cli.setup_status import setup_status_command
    rc = setup_status_command(None)
    assert rc == 0

    out = capsys.readouterr().out
    assert "plutus-agent setup status" in out
    assert "Live trading" in out
    assert "ACP CLI installed" in out
    assert "lifecycle.db initialised" in out


def test_setup_status_ready_when_keys_present(capsys, monkeypatch):
    monkeypatch.setenv("HL_API_WALLET_KEY", "0xfake")
    monkeypatch.setenv("HL_PUBLIC_ADDRESS", "0xfeedface")

    from harness.cli.setup_status import setup_status_command
    setup_status_command(None)
    out = capsys.readouterr().out
    assert "Live trading READY" in out


def test_individual_check_helpers_return_tuples():
    """The internal _check_* helpers all return (ok_bool_or_None, detail_str)."""
    from harness.cli import setup_status as ss

    helpers = [
        ss._check_acp_installed,
        ss._check_acp_configured,
        ss._check_dgclaw_installed,
        ss._check_dgclaw_joined,
        ss._check_hl_api_wallet,
        ss._check_hl_public_address,
        ss._check_voyage_key,
        ss._check_holographic_memory,
        ss._check_lifecycle_db,
        ss._check_cron_jobs,
        ss._check_pm2_processes,
    ]
    for fn in helpers:
        result = fn()
        assert isinstance(result, tuple)
        assert len(result) == 2
        ok, detail = result
        assert ok is None or isinstance(ok, bool)
        assert isinstance(detail, str)
