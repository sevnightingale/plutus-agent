"""desk_integrity_check (toolset: resolution) — is the desk itself well?

Ops already asks whether the MARKET needs attention. This asks whether the
desk does. It is a set of deterministic assertions, not a judgement call, so
it belongs on the cheapest mind on the desk and costs nothing when everything
holds.

The tool returns violations; it does not fix them and it does not decide what
they mean. Ops escalates each one under a stable wake key so a standing
problem backs off instead of firing every half hour.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

DESK_INTEGRITY_SCHEMA = {
    "name": "desk_integrity_check",
    "description": (
        "Check the desk's own health: blackboard bloat and missing zones, "
        "Live State freshness, the lessons cap, staleness ceilings, tables "
        "that have a schema but no writer, unrecorded capital, wake loops, "
        "runtime disk. Deterministic and silent when healthy. Run it every "
        "tick; for each violation enqueue_wake(reason=escalation, "
        "key='integrity:<check>') with the detail verbatim. You do NOT "
        "diagnose or repair — main does."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _desk_integrity_check(args: Dict[str, Any]) -> str:
    from trading.lifecycle import integrity
    from trading.lifecycle.db import get_db

    try:
        result = integrity.check_integrity(get_db())
    except Exception as exc:
        # The checker failing is itself a finding — never report health here.
        return tool_error(
            f"desk_integrity_check could not run: {type(exc).__name__}: {exc}")
    return tool_result(result)


registry.register(
    name="desk_integrity_check",
    toolset="resolution",
    schema=DESK_INTEGRITY_SCHEMA,
    handler=lambda args, **kw: _desk_integrity_check(args),
    description="Deterministic health assertions about the desk's own state.",
    emoji="🩺",
)
