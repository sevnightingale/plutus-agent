"""Per-alert poll cycle — execute the registered poll fn, persist state, return fired events.

Wake events are NDJSON lines appended to ``~/.plutus-agent/wake_events.ndjson``
for audit. The watcher daemon ALSO routes them to Plutus via a one-shot
cron job so the existing cron scheduler handles session creation rather
than the watcher poking the gateway directly.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from plutus_constants import get_hermes_home

from . import state as watcher_state

logger = logging.getLogger(__name__)


def wake_events_path() -> Path:
    return get_hermes_home() / "wake_events.ndjson"


def emit_wake_events(events: List[Dict[str, Any]]) -> int:
    """Append wake events to the NDJSON sink (audit trail)."""
    if not events:
        return 0
    p = wake_events_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for e in events:
            payload = dict(e)
            payload.setdefault("emitted_at", time.time())
            f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(events)


def poll_once(alert_entry) -> List[Dict[str, Any]]:
    """Run one poll cycle for ``alert_entry``. Respects throttle. Returns fired events."""
    name = alert_entry.name

    last_fired = watcher_state.get_last_fired_ts(name)
    if last_fired and (time.time() - last_fired) < alert_entry.throttle_seconds:
        return []

    prior_state = watcher_state.get_alert_state(name) or {}
    try:
        events, new_state = alert_entry.poll_fn(state=prior_state)
    except Exception as exc:
        logger.warning("alert '%s' poll_fn raised: %s", name, exc)
        return []

    fired_ts = time.time() if events else None
    watcher_state.update_alert_state(name, new_state, fired_ts=fired_ts)

    if events:
        for e in events:
            e.setdefault("alert", name)
            e.setdefault("source", alert_entry.source)
        emit_wake_events(events)
    return events


# Map alert names to the skill Plutus should load when the alert fires.
# Falls back to the heartbeat skill when no specific routing is registered.
_ALERT_SKILL_ROUTES: Dict[str, str] = {
    "hl_position_status_change":     "trading/reconcile-and-reflect",
    "hl_account_balance_change":     "trading/reconcile-and-reflect",
    "dgclaw_perp_deposit_completed": "trading/bootstrap-setup",
    "dgclaw_leaderboard_rank_change": "trading/heartbeat",
}


def _route_skill_for(events: List[Dict[str, Any]]) -> str:
    """Pick the most-specific skill name to load for a batch of events."""
    skills = {_ALERT_SKILL_ROUTES.get(e.get("alert", ""), "trading/heartbeat") for e in events}
    # Most-specific skill wins; fall back to heartbeat.
    if "trading/reconcile-and-reflect" in skills:
        return "trading/reconcile-and-reflect"
    if "trading/bootstrap-setup" in skills:
        return "trading/bootstrap-setup"
    return next(iter(skills), "trading/heartbeat")


def schedule_wake_session(events: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Create a one-shot cron job that wakes Plutus for the batched events.

    Returns the created job dict (or None when no events). The cron
    scheduler picks up the job within ~60s and runs it as a fresh
    Plutus session with the chosen skill loaded.
    """
    if not events:
        return None
    from cron.jobs import create_job

    skill = _route_skill_for(events)
    summary = "; ".join(
        f"{e.get('alert')}/{e.get('kind') or 'fired'}"
        + (f"({e.get('coin')})" if e.get("coin") else "")
        for e in events
    )
    prompt = (
        f"Wake event(s) from watcher daemon: {summary}. "
        "Load the indicated skill, examine the wake events in "
        "~/.plutus-agent/wake_events.ndjson if more detail is needed, "
        "and act."
    )
    return create_job(
        prompt=prompt,
        schedule="1m",       # one-shot, fires within ~60s
        name=f"plutus-wake-{int(time.time())}",
        skill=skill,
        enabled_toolsets=["plutus-agent-cli"],
        repeat=1,
    )
