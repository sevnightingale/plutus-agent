"""list_venues — discovery tool for the venue registry."""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.core import venue_registry
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "list_venues",
    "description": (
        "List registered trading venues. Each entry includes the venue name, "
        "description, and which execution functions it supports "
        "(place_order, close_position, modify_order, cancel_order, account_state)."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _list_venues(args: Dict[str, Any]) -> str:
    entries = venue_registry.list_all()
    return tool_result({
        "count": len(entries),
        "entries": [
            {
                "name": e.name,
                "description": e.description,
                "supports": {
                    "place_order":    e.place_order_fn is not None,
                    "close_position": e.close_position_fn is not None,
                    "modify_order":   e.modify_order_fn is not None,
                    "cancel_order":   e.cancel_order_fn is not None,
                    "account_state":  e.account_state_fn is not None,
                },
            }
            for e in entries
        ],
    })


registry.register(
    name="list_venues",
    toolset="execution",
    schema=SCHEMA,
    handler=lambda args, **kw: _list_venues(args),
    description="Enumerate registered trading venues.",
    emoji="🏛️",
)
