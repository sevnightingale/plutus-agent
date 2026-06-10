"""modify_order — execution dispatcher (thin venue dispatch)."""

from __future__ import annotations

from typing import Any, Dict

from trading.perception.core import venue_registry
from harness.tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "modify_order",
    "description": (
        "Modify an existing order at a venue (change SL, TP, size, price). "
        "Pass venue + order_id + the fields to update under 'updates'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "venue":    {"type": "string"},
            "order_id": {"type": "string"},
            "updates":  {"type": "object", "additionalProperties": True},
        },
        "required": ["venue", "order_id", "updates"],
    },
}


def _modify_order(args: Dict[str, Any]) -> str:
    venue = args.get("venue")
    order_id = args.get("order_id")
    updates = args.get("updates") or {}
    if not venue or not order_id:
        return tool_error("modify_order requires venue + order_id")

    try:
        v = venue_registry.lookup(venue)
    except KeyError as exc:
        return tool_error(str(exc))
    if not v.modify_order_fn:
        return tool_error(f"venue '{venue}' has no modify_order_fn registered")

    try:
        result = v.modify_order_fn(order_id=order_id, **updates)
    except Exception as exc:
        return tool_error(f"venue '{venue}' modify_order_fn raised: {exc}")

    return tool_result({"venue": venue, "order_id": order_id, "result": result})


registry.register(
    name="modify_order",
    toolset="execution",
    schema=SCHEMA,
    handler=lambda args, **kw: _modify_order(args),
    description="Modify an existing order at a venue.",
    emoji="✏️",
)
