"""list_identity_systems — discovery tool for the identity-system registry."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from trading.perception.core import identity_registry
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "list_identity_systems",
    "description": (
        "List registered identity systems (ACP, etc.). Each system contributes "
        "its own admin tools (e.g., acp_whoami, acp_signer_add) — this tool "
        "is the catalog of WHICH identity systems are wired in."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _list_identity_systems(args: Dict[str, Any]) -> str:
    entries = identity_registry.list_all()
    return tool_result({
        "count": len(entries),
        "entries": [asdict(e) for e in entries],
    })


registry.register(
    name="list_identity_systems",
    toolset="identity",
    schema=SCHEMA,
    handler=lambda args, **kw: _list_identity_systems(args),
    description="Enumerate registered identity systems.",
    emoji="🆔",
)
