"""runtime_hygiene (toolset: resolution) — ops's janitorial sweep.

Self-gating in code, not in the recipe's prose: ops may call it every tick and
it does real work about once a day. That is deliberate. The Live State refresh
was gated by an instruction to the cheapest model on the desk, and a gate a
model can talk itself out of is the same shape as the staleness floor it
declined thirteen times running.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

RUNTIME_HYGIENE_SCHEMA = {
    "name": "runtime_hygiene",
    "description": (
        "Prune aged runtime files (sessions, spawned-agent transcripts, "
        "checkpoints, request dumps, cron output) per their retention "
        "windows. Self-gating and idempotent — call it every tick and it "
        "sweeps about once a day, returning skipped=true otherwise. Daily "
        "journals, blackboards, and the databases are never touched. Set "
        "dry_run to report what would go without removing anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean",
                "description": "Report what would be pruned; remove nothing.",
            },
        },
    },
}


def _runtime_hygiene(args: Dict[str, Any]) -> str:
    from trading.lifecycle import hygiene
    from trading.lifecycle.db import get_db

    try:
        result = hygiene.sweep(get_db(), dry_run=bool(args.get("dry_run")))
    except Exception as exc:
        return tool_error(f"runtime_hygiene failed: {type(exc).__name__}: {exc}")
    return tool_result(result)


registry.register(
    name="runtime_hygiene",
    toolset="resolution",
    schema=RUNTIME_HYGIENE_SCHEMA,
    handler=lambda args, **kw: _runtime_hygiene(args),
    description="Prune aged runtime files per retention; self-gating, journal-safe.",
    emoji="🧹",
)
