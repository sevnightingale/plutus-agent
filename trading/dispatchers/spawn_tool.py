"""spawn_desk_agent (toolset: spawn) — plutus-main's ONLY delegation surface.

Wraps harness.spawn.spawn_agent: deterministic context assembly from the
agent's AGENT.md, isolated run on its declared model/toolsets, automatic
ledger transcript, validated return contract. The spawn toolset is granted
to plutus-main alone — no-nesting is enforced by omission (every spawn
force-disables spawn/cron/messaging on the child).
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result


def _spawn(args: Dict[str, Any]) -> str:
    from harness.spawn import AGENTS_DIR, spawn_agent
    from trading.dispatchers._helpers import session_id_from_context

    name = args.get("agent", "")
    task = args.get("task", "")
    if not name or not task:
        return tool_error("spawn_desk_agent requires agent and task")
    roster = sorted(p.parent.name for p in AGENTS_DIR.glob("*/AGENT.md"))
    if name not in roster:
        return tool_error(f"unknown agent {name!r} — roster: {roster}")
    if name == "plutus-main":
        return tool_error("plutus-main is the persistent session — it is never spawned")

    result = spawn_agent(
        name,
        task,
        session_name=session_id_from_context() or "session",
        inactivity_timeout_s=float(args.get("timeout_s", 900)),
    )
    return tool_result({
        "ok": result["ok"],
        "payload": result.get("payload"),
        "problems": result.get("problems") or [],
        "transcript": result.get("transcript"),
        "duration_s": result.get("duration_s"),
    })


registry.register(
    name="spawn_desk_agent",
    toolset="spawn",
    schema={
        "name": "spawn_desk_agent",
        "description": (
            "Spawn a desk specialist synchronously and get its validated "
            "structured return. agent: plutus-perception | plutus-regime | "
            "plutus-predict | plutus-trade | plutus-ops | plutus-reflect. "
            "task: the spawn-time instruction (what THIS run is for — the "
            "agent's standing procedure comes from its AGENT.md). Heavy "
            "work happens in the child; you hold the book."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "task": {"type": "string"},
                "timeout_s": {"type": "number"},
            },
            "required": ["agent", "task"],
        },
    },
    handler=lambda args, **kw: _spawn(args),
    description="Spawn a desk agent (main-only; no-nesting by omission).",
    emoji="🎭",
)
