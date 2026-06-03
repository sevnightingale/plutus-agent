"""record_event — registry-dispatched reflection tool.

Dispatches to the appropriate handler in ``tools.core.event_registry``. Each
event handler is responsible for its own lifecycle.db write semantics — for
thesis and reflection events the handler embeds the text via ``get_embedder()``
and writes both the row (with embedding BLOB) and the vec0 row in the same
transaction, so the textual record and its searchable vector exist or do not
exist together.

The dispatcher itself is intentionally dumb — registry lookup + call. Adding
a new event type means one new entry in an integration module, not a new tool.
"""

from __future__ import annotations

from typing import Any, Dict

from tools.core import event_registry
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "record_event",
    "description": (
        "Record a lifecycle event (thesis, decision, reflection, position_evaluation, "
        "capital_movement, strategy_open|pause|retire, ...). Use list_event_types "
        "to discover what's available; pass the event-specific fields under 'params'. "
        "Returns the inserted row id (and embedding metadata for thesis/reflection)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "Event type name (e.g., 'thesis', 'reflection').",
            },
            "params": {
                "type": "object",
                "description": "Keyword arguments passed to the event handler.",
                "additionalProperties": True,
            },
        },
        "required": ["type"],
    },
}


def _record_event(args: Dict[str, Any]) -> str:
    event_type = (args.get("type") or "").strip()
    params = args.get("params") or {}
    if not event_type:
        return tool_error("record_event requires 'type'")

    try:
        entry = event_registry.lookup(event_type)
    except KeyError as exc:
        return tool_error(str(exc))

    try:
        result = entry.fn(**params)
    except TypeError as exc:
        return tool_error(
            f"event '{event_type}' handler rejected params: {exc}"
        )
    except Exception as exc:
        return tool_error(f"event '{event_type}' handler raised: {exc}")

    if not isinstance(result, dict):
        return tool_error(
            f"event '{event_type}' handler must return a dict; "
            f"got {type(result).__name__}"
        )
    return tool_result({"event_type": event_type, **result})


registry.register(
    name="record_event",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _record_event(args),
    description="Append a typed event to the lifecycle store.",
    emoji="📝",
)
