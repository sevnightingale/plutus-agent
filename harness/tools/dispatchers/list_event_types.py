"""list_event_types — discovery tool for the event registry."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from harness.tools.core import event_registry
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "list_event_types",
    "description": (
        "List registered event types (thesis, reflection, decision, "
        "position_evaluation, capital_movement, strategy_open, ...). "
        "Returns name, description, and fields_schema for each — use this "
        "to pick a type and shape your record_event params."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


def _list_event_types(args: Dict[str, Any]) -> str:
    entries = event_registry.list_all()
    return tool_result({
        "count": len(entries),
        "entries": [
            {k: v for k, v in asdict(e).items() if k != "fn"}
            for e in entries
        ],
    })


registry.register(
    name="list_event_types",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _list_event_types(args),
    description="Enumerate registered event types.",
    emoji="📚",
)
