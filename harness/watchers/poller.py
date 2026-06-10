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

from harness.constants import get_hermes_home

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


def schedule_wake_session(events: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Enqueue ONE wake for plutus-main covering the batched events (rebuild R4).

    Watchers never spawn sessions or create cron jobs anymore — they enqueue
    into the serialized wake queue; the gateway ticker drains it into main's
    persistent session (multiple triggers collapse into one turn by design).
    """
    if not events:
        return None
    from harness.wake_queue import enqueue

    parts: List[str] = []
    price_events = [e for e in events if e.get("alert") == "hl_price_range"]
    other_events = [e for e in events if e.get("alert") != "hl_price_range"]
    if price_events:
        parts.append("price alert: " + "; ".join(
            f"{e.get('coin', '?')} at {e.get('price', '?')} (inside configured range)"
            for e in price_events))
    if other_events:
        parts.append("watcher events: " + "; ".join(
            f"{e.get('alert')}/{e.get('kind') or 'fired'}"
            + (f"({e.get('coin')})" if e.get("coin") else "")
            for e in other_events))
    detail = ". ".join(parts) + ". Full payloads: ~/.plutus-agent/wake_events.ndjson"
    return enqueue("watcher", detail, source="plutus-watchers")
