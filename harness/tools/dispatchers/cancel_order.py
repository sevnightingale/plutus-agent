"""cancel_order — execution dispatcher (thin venue dispatch)."""

from __future__ import annotations

from typing import Any, Dict

from tools.core import venue_registry
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "cancel_order",
    "description": "Cancel an order at a venue.",
    "parameters": {
        "type": "object",
        "properties": {
            "venue":    {"type": "string"},
            "order_id": {"type": "string"},
        },
        "required": ["venue", "order_id"],
    },
}


def _cancel_order(args: Dict[str, Any]) -> str:
    venue = args.get("venue")
    order_id = args.get("order_id")
    if not venue or not order_id:
        return tool_error("cancel_order requires venue + order_id")

    try:
        v = venue_registry.lookup(venue)
    except KeyError as exc:
        return tool_error(str(exc))
    if not v.cancel_order_fn:
        return tool_error(f"venue '{venue}' has no cancel_order_fn registered")

    try:
        result = v.cancel_order_fn(order_id=order_id)
    except Exception as exc:
        return tool_error(f"venue '{venue}' cancel_order_fn raised: {exc}")

    return tool_result({"venue": venue, "order_id": order_id, "result": result})


registry.register(
    name="cancel_order",
    toolset="execution",
    schema=SCHEMA,
    handler=lambda args, **kw: _cancel_order(args),
    description="Cancel an order at a venue.",
    emoji="❌",
)
