"""enqueue_wake + check_staleness (toolset: resolution) — the ops watchdog.

Ops never spawns and never messages the operator: when something needs
judgment (escalation) or an action type is past its staleness floor, it
enqueues a wake for plutus-main. The gateway drains the queue into main's
persistent session one turn at a time.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

# Staleness floors (seconds) — §10/§16; ops enforces, main schedules ahead.
STALENESS_FLOORS = {
    "perception": 4 * 3600,
    "regime": 8 * 3600,
    "predict": 8 * 3600,
    "reflect": 7 * 86400,
    "generation": 7 * 86400,
}


def _enqueue_wake(args: Dict[str, Any]) -> str:
    from harness.wake_queue import enqueue
    try:
        record = enqueue(
            reason=args.get("reason", ""),
            detail=args.get("detail", ""),
            source=args.get("source") or "plutus-ops",
        )
    except ValueError as exc:
        return tool_error(str(exc))
    return tool_result({"ok": True, "enqueued": record})


def _check_staleness(args: Dict[str, Any]) -> str:
    from trading.lifecycle import queries
    from trading.lifecycle.db import get_db

    last = queries.last_action_runs(get_db())
    now = time.time()
    overdue = []
    report = {}
    for action, floor in STALENESS_FLOORS.items():
        last_ts = last.get(action)
        age = None if last_ts is None else round(now - last_ts)
        report[action] = {"last_ts": last_ts, "age_s": age, "floor_s": floor}
        if last_ts is None or (now - last_ts) > floor:
            overdue.append(action)
    return tool_result({"overdue": overdue, "report": report})


registry.register(
    name="enqueue_wake",
    toolset="resolution",
    schema={
        "name": "enqueue_wake",
        "description": (
            "Enqueue a wake for plutus-main (the ONLY way ops escalates — "
            "never message the operator). reason: staleness|watcher|"
            "escalation|schedule. detail: one-paragraph digest of why."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string",
                           "enum": ["staleness", "watcher", "escalation", "schedule"]},
                "detail": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["reason", "detail"],
        },
    },
    handler=lambda args, **kw: _enqueue_wake(args),
    description="Enqueue a wake for plutus-main via the serialized wake queue.",
    emoji="⏰",
)

registry.register(
    name="check_staleness",
    toolset="resolution",
    schema={
        "name": "check_staleness",
        "description": (
            "Compare each action type's last run (action_runs) against its "
            "staleness floor. Returns overdue list + per-action ages."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: _check_staleness(args),
    description="Watchdog: last action run per type vs staleness floors.",
    emoji="🐶",
)
