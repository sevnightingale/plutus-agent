"""acp_auth_readiness — liveness + age verdict for the ACP OAuth session.

Born 2026-07-02: the desk needed auth-expiry warnings, but ops has no shell
tools, so the check lives here as a data point (the hl_trade_readiness
pattern) and ops fetches it. All paths and the CLI are mocked.
"""

from __future__ import annotations

import json
import time

import pytest

from trading.integrations.acp import _cli, data_points


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Fake config.json + state path; returns helpers to shape the scenario."""
    cfg = tmp_path / "config.json"
    state = tmp_path / "acp_auth_state.json"
    monkeypatch.setattr(data_points, "_acp_config_path", lambda: cfg)
    monkeypatch.setattr(data_points, "_auth_state_path", lambda: state)
    return {"cfg": cfg, "state": state}


def _touch(path, age_days: float):
    path.write_text("{}", encoding="utf-8")
    ts = time.time() - age_days * 86400
    import os
    os.utime(path, (ts, ts))


def test_missing_config_is_dead(auth_env, monkeypatch):
    res = data_points.acp_auth_readiness()
    assert res["alive"] is False
    assert "acp configure" in res["reason"]


def test_fresh_auth_alive(auth_env, monkeypatch):
    _touch(auth_env["cfg"], age_days=0.01)
    monkeypatch.setattr(_cli, "acp", lambda *a, **k: {"address": "0xabc"})
    res = data_points.acp_auth_readiness()
    assert res["alive"] is True
    assert res["warn_reauth_soon"] is False and res["critical"] is False
    # Self-healed state file recorded the refresh.
    state = json.loads(auth_env["state"].read_text())
    assert state["acp_auth_refreshed_at_epoch"] == pytest.approx(
        auth_env["cfg"].stat().st_mtime)


def test_whoami_failure_is_dead_even_when_fresh(auth_env, monkeypatch):
    _touch(auth_env["cfg"], age_days=0.01)

    def boom(*a, **k):
        raise _cli.ACPCLIError("exited with code 1: not authenticated")
    monkeypatch.setattr(_cli, "acp", boom)
    res = data_points.acp_auth_readiness()
    assert res["alive"] is False
    assert "acp configure" in res["reason"]


def test_warn_at_45_days(auth_env, monkeypatch):
    _touch(auth_env["cfg"], age_days=50)
    monkeypatch.setattr(_cli, "acp", lambda *a, **k: {"address": "0xabc"})
    res = data_points.acp_auth_readiness()
    assert res["alive"] is True
    assert res["warn_reauth_soon"] is True and res["critical"] is False
    assert "proactively" in res["reason"]


def test_critical_at_60_days(auth_env, monkeypatch):
    _touch(auth_env["cfg"], age_days=61)
    monkeypatch.setattr(_cli, "acp", lambda *a, **k: {"address": "0xabc"})
    res = data_points.acp_auth_readiness()
    assert res["critical"] is True
    assert "NOW" in res["reason"]


def test_out_of_band_reauth_self_heals(auth_env, monkeypatch):
    """config.json newer than the recorded epoch → state silently updated."""
    _touch(auth_env["cfg"], age_days=1)
    stale_epoch = time.time() - 55 * 86400
    auth_env["state"].write_text(json.dumps(
        {"acp_auth_refreshed_at_epoch": stale_epoch}), encoding="utf-8")
    monkeypatch.setattr(_cli, "acp", lambda *a, **k: {"address": "0xabc"})
    res = data_points.acp_auth_readiness()
    assert res["alive"] is True
    # Ages from the mtime (1d), not the stale recorded epoch (55d).
    assert res["days_since_refresh"] < 2
    assert res["warn_reauth_soon"] is False
    state = json.loads(auth_env["state"].read_text())
    assert state["acp_auth_refreshed_at_epoch"] == pytest.approx(
        auth_env["cfg"].stat().st_mtime)


def test_recorded_epoch_newer_than_mtime_wins(auth_env, monkeypatch):
    """Operator said 'configure done' and main recorded a fresher epoch."""
    _touch(auth_env["cfg"], age_days=50)
    fresh_epoch = time.time() - 10
    auth_env["state"].write_text(json.dumps(
        {"acp_auth_refreshed_at_epoch": fresh_epoch}), encoding="utf-8")
    monkeypatch.setattr(_cli, "acp", lambda *a, **k: {"address": "0xabc"})
    res = data_points.acp_auth_readiness()
    assert res["days_since_refresh"] < 1
    assert res["warn_reauth_soon"] is False


class TestCliSigabrtSalvage:
    """The node acp CLI intermittently SIGABRTs (exit -6) on teardown AFTER
    printing a complete valid JSON result. First live ops tick on
    2026-07-02 falsely escalated 'auth dead' because of it."""

    def _run_with(self, monkeypatch, returncode, stdout, stderr=""):
        import subprocess
        from unittest.mock import MagicMock
        proc = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
        monkeypatch.setattr(_cli, "is_installed", lambda: True)
        return _cli.acp("agent", "whoami")

    def test_sigabrt_with_valid_json_is_salvaged(self, monkeypatch):
        out = self._run_with(monkeypatch, -6, '{"walletAddress": "0xabc"}')
        assert out == {"walletAddress": "0xabc"}

    def test_sigabrt_with_error_json_still_raises(self, monkeypatch):
        with pytest.raises(_cli.ACPCLIError):
            self._run_with(monkeypatch, -6, '{"error": "not authenticated"}')

    def test_nonzero_without_output_still_raises(self, monkeypatch):
        with pytest.raises(_cli.ACPCLIError):
            self._run_with(monkeypatch, 1, "", stderr="boom")
