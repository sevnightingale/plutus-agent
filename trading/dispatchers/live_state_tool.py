"""sync_live_state — refresh PLUTUS.md's ``## Live State`` zone (Issue 2).

A deterministic, no-arg tool in ops's ``resolution`` toolset. ops calls it
(gated) after the POSITION step so the Live State block — equity snapshot, open
position, strategy counts — stops being frozen at install. The heavy lifting is
in ``trading.lifecycle.live_state``; this is the thin agent-facing wrapper.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result


def _sync_live_state(args: Dict[str, Any]) -> str:
    from trading.lifecycle.db import get_db
    from trading.lifecycle.live_state import write_live_state

    try:
        result = write_live_state(get_db())
    except Exception as exc:  # noqa: BLE001 — surface any write failure
        return tool_error(f"sync_live_state failed: {type(exc).__name__}: {exc}")
    if not result["ok"]:
        return tool_error(result["error"])
    return tool_result(result)


registry.register(
    name="sync_live_state",
    toolset="resolution",
    schema={
        "name": "sync_live_state",
        "description": (
            "Recompute and rewrite PLUTUS.md's '## Live State' zone from "
            "lifecycle.db + the live equity read: equity_usd snapshot, the open "
            "position, and strategy counts by status. Surgical — leaves the "
            "Doctrine and Lessons zones untouched. A failed equity read writes "
            "'unavailable', never a stale number. No arguments."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: _sync_live_state(args),
    description="Rewrite PLUTUS.md ## Live State from lifecycle.db + equity.",
    emoji="🪧",
)
