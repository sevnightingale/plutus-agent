"""Watcher poller — state diff → NDJSON write → batched cron job creation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

import pytest


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "plutus-agent"
    home.mkdir()
    (home / "cron").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    import importlib
    import harness.constants as plutus_constants
    importlib.reload(plutus_constants)
    import harness.cron.jobs; import harness.cron as cron
    importlib.reload(cron.jobs)
    from harness.watchers import state, poller
    importlib.reload(state)
    importlib.reload(poller)
    return home


@dataclass(frozen=True)
class _StubAlert:
    name: str
    source: str
    poll_fn: Callable[..., Tuple[List[Dict[str, Any]], Dict[str, Any]]]
    throttle_seconds: int = 0
    description: str = ""


def test_poll_once_writes_events_and_persists_state(temp_hermes_home):
    """A poll that fires events writes them to NDJSON and updates state."""
    from harness.watchers import poller, state

    captured = {"calls": 0}

    def stub_poll(state=None):
        captured["calls"] += 1
        return (
            [{"kind": "opened", "coin": "BTC"}],
            {"positions": {"BTC": {"szi": 0.01}}},
        )

    alert = _StubAlert(name="hl_position_status_change", source="hyperliquid",
                       poll_fn=stub_poll)
    events = poller.poll_once(alert)

    assert len(events) == 1
    assert events[0]["coin"] == "BTC"
    assert events[0]["alert"] == "hl_position_status_change"
    assert events[0]["source"] == "hyperliquid"

    # NDJSON written
    nd = poller.wake_events_path()
    assert nd.exists()
    line = json.loads(nd.read_text().splitlines()[-1])
    assert line["kind"] == "opened"
    assert line["alert"] == "hl_position_status_change"

    # State persisted
    s = state.get_alert_state("hl_position_status_change")
    assert s == {"positions": {"BTC": {"szi": 0.01}}}


def test_poll_once_respects_throttle(temp_hermes_home):
    """A second poll within throttle_seconds returns no events."""
    from harness.watchers import poller

    def stub_poll(state=None):
        return ([{"kind": "opened", "coin": "BTC"}], {"x": 1})

    alert = _StubAlert(name="throttled_alert", source="hyperliquid",
                       poll_fn=stub_poll, throttle_seconds=300)
    first = poller.poll_once(alert)
    assert len(first) == 1

    second = poller.poll_once(alert)
    assert second == []


def test_schedule_wake_session_creates_cron(temp_hermes_home):
    """A batch of fired events creates one cron job with the right skill route."""
    from harness.watchers.poller import schedule_wake_session
    from harness.cron import jobs as cron_jobs

    events = [
        {"alert": "hl_position_status_change", "kind": "closed", "coin": "BTC"},
        {"alert": "hl_account_balance_change", "delta": 5.0},
    ]
    job = schedule_wake_session(events)
    assert job is not None
    assert job["skill"] == "trading/reconcile-and-reflect"
    assert job["enabled_toolsets"] == ["plutus-agent-cli"]
    assert "hl_position_status_change" in job["prompt"]
    assert "hl_account_balance_change" in job["prompt"]

    listed = cron_jobs.list_jobs()
    assert any(j["id"] == job["id"] for j in listed)


def test_schedule_wake_session_no_events_returns_none(temp_hermes_home):
    from harness.watchers.poller import schedule_wake_session
    assert schedule_wake_session([]) is None


def test_schedule_wake_session_unknown_alert_falls_back_to_heartbeat(temp_hermes_home):
    from harness.watchers.poller import schedule_wake_session

    events = [{"alert": "future_alert_name", "delta": 1.0}]
    job = schedule_wake_session(events)
    assert job["skill"] == "trading/heartbeat"


def test_emit_wake_events_appends_lines(temp_hermes_home):
    from harness.watchers.poller import emit_wake_events, wake_events_path

    n1 = emit_wake_events([{"alert": "a", "k": 1}, {"alert": "b", "k": 2}])
    n2 = emit_wake_events([{"alert": "c", "k": 3}])
    assert n1 == 2
    assert n2 == 1

    lines = wake_events_path().read_text().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["alert"] == "a"
    assert json.loads(lines[2])["alert"] == "c"
