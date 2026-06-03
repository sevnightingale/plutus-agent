"""dgclaw integration — steady-state ops + env-bridging + alerts.

Setup tools were dropped in the Phase 4 polish pass (replaced by the
vendored skills/dgclaw/SKILL.md procedure). Tests for those tools
are also dropped.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tools.integrations.dgclaw import _cli, _env, data_points, operations, alerts


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    home = tmp_path / "plutus-agent"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import importlib, plutus_constants
    importlib.reload(plutus_constants)
    return home


@pytest.fixture
def fake_dgclaw_root(tmp_path, monkeypatch):
    """Provide a fake dgclaw-skill root that 'is_installed' will accept."""
    root = tmp_path / "dgclaw-skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "dgclaw.sh").write_text("#!/bin/bash\necho '{}'\n")
    (root / "scripts" / "dgclaw.sh").chmod(0o755)
    (root / "scripts" / "trade.ts").write_text("")
    (root / "scripts" / "activate-unified.ts").write_text("")
    (root / "scripts" / "add-api-wallet.ts").write_text("")
    (root / "node_modules").mkdir()
    monkeypatch.setenv("DGCLAW_SKILL_ROOT", str(root))
    return root


@pytest.fixture
def mock_dgclaw_runner(monkeypatch):
    """Patch the subprocess runners so tests don't actually invoke shell."""
    dg_runner = MagicMock(return_value={})
    trade_runner = MagicMock(return_value={})
    script_runner = MagicMock(return_value={})
    monkeypatch.setattr(_cli, "dgclaw", dg_runner)
    monkeypatch.setattr(_cli, "dgclaw_trade", trade_runner)
    monkeypatch.setattr(_cli, "dgclaw_script", script_runner)
    monkeypatch.setattr(data_points, "_cli", _cli)
    monkeypatch.setattr(operations._cli, "dgclaw", dg_runner)
    monkeypatch.setattr(operations._cli, "dgclaw_trade", trade_runner)
    monkeypatch.setattr(alerts._cli, "dgclaw", dg_runner)
    return {
        "dgclaw": dg_runner,
        "dgclaw_trade": trade_runner,
        "dgclaw_script": script_runner,
    }


# ─── data points ──────────────────────────────────────────────────────────


def test_dgclaw_leaderboard(mock_dgclaw_runner, fake_dgclaw_root):
    mock_dgclaw_runner["dgclaw"].return_value = {"data": [{"rank": 1}]}
    res = data_points.dgclaw_leaderboard()
    assert res == {"data": [{"rank": 1}]}
    # Real dgclaw.sh signature: leaderboard <limit> <offset> (positional)
    mock_dgclaw_runner["dgclaw"].assert_called_once_with("leaderboard", "20", "0")


def test_dgclaw_forum_posts(mock_dgclaw_runner, fake_dgclaw_root):
    mock_dgclaw_runner["dgclaw"].return_value = {"data": []}
    data_points.dgclaw_forum_posts(agent_id="a1", thread_id="t1")
    # Real dgclaw.sh signature: posts <agentId> <threadId> (positional)
    mock_dgclaw_runner["dgclaw"].assert_called_once_with("posts", "a1", "t1")


# ─── operations ───────────────────────────────────────────────────────────


def test_forum_create_post_requires_args():
    # Renamed from test_forum_reply_requires_args — the dgclaw.sh CLI has
    # no separate 'reply' subcommand; replying IS creating a post.
    res = json.loads(operations._dgclaw_forum_create_post({"agent_id": "a1"}))
    assert res["error"]


def test_trade_open_calls_correct_args(mock_dgclaw_runner, fake_dgclaw_root):
    mock_dgclaw_runner["dgclaw_trade"].return_value = {"order_id": "o1"}
    res = json.loads(operations._dgclaw_trade_open({
        "pair": "BTC", "side": "long", "size": 0.01, "leverage": 5,
    }))
    assert res["order_id"] == "o1"
    mock_dgclaw_runner["dgclaw_trade"].assert_called_once_with(
        "open", "--pair", "BTC", "--side", "long", "--size", "0.01", "--leverage", "5"
    )


def test_trade_close(mock_dgclaw_runner, fake_dgclaw_root):
    mock_dgclaw_runner["dgclaw_trade"].return_value = {"closed": True}
    res = json.loads(operations._dgclaw_trade_close({"pair": "BTC"}))
    assert res["closed"] is True
    mock_dgclaw_runner["dgclaw_trade"].assert_called_once_with("close", "--pair", "BTC")


# ─── env-bridging ─────────────────────────────────────────────────────────


def test_persist_from_dgclaw_dotenv(temp_home, fake_dgclaw_root):
    # Write DGCLAW_API_KEY into dgclaw-skill .env
    dg_env = fake_dgclaw_root / ".env"
    dg_env.write_text("DGCLAW_API_KEY=sk_abc123\nHL_API_WALLET_KEY=0xfeed\n")
    persisted = _env.persist_from_dgclaw_dotenv("DGCLAW_API_KEY", "HL_API_WALLET_KEY")
    assert persisted == ["DGCLAW_API_KEY", "HL_API_WALLET_KEY"]
    # Verify written to ~/.plutus-agent/.env
    hermes_env = temp_home / ".env"
    body = hermes_env.read_text()
    assert "DGCLAW_API_KEY=sk_abc123" in body
    assert "HL_API_WALLET_KEY=0xfeed" in body


def test_persist_skips_missing_keys(temp_home, fake_dgclaw_root):
    (fake_dgclaw_root / ".env").write_text("DGCLAW_API_KEY=sk1\n")
    persisted = _env.persist_from_dgclaw_dotenv("DGCLAW_API_KEY", "MISSING_KEY")
    assert persisted == ["DGCLAW_API_KEY"]


# ─── alerts ───────────────────────────────────────────────────────────────


def test_rank_change_fires_on_diff(mock_dgclaw_runner, fake_dgclaw_root, monkeypatch):
    monkeypatch.setenv("HL_PUBLIC_ADDRESS", "0xfeedface")
    mock_dgclaw_runner["dgclaw"].return_value = {
        "standings": [
            {"rank": 5, "address": "0xfeedface", "name": "Plutus"},
            {"rank": 3, "address": "0xother"},
        ]
    }
    fired, new = alerts.poll_dgclaw_rank_change(state={"rank": 7})
    assert len(fired) == 1
    assert fired[0]["previous_rank"] == 7
    assert fired[0]["current_rank"] == 5
    assert new["rank"] == 5


def test_rank_change_no_fire_on_first_observation(mock_dgclaw_runner, fake_dgclaw_root, monkeypatch):
    monkeypatch.setenv("HL_PUBLIC_ADDRESS", "0xfeedface")
    mock_dgclaw_runner["dgclaw"].return_value = {
        "standings": [{"rank": 5, "address": "0xfeedface"}]
    }
    fired, new = alerts.poll_dgclaw_rank_change(state={})
    assert fired == []
    assert new["rank"] == 5  # remembered for next poll


def test_perp_deposit_completed_no_watch_id_returns_empty():
    fired, state = alerts.poll_perp_deposit_completed(state={})
    assert fired == []
    assert state == {}
