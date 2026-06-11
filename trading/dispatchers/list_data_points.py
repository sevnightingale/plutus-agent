"""list_data_points — discovery tool for the data-point registry."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from trading.perception.core import data_point_registry
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "list_data_points",
    "description": (
        "List registered data points. Optionally filter by category "
        "(market | on_chain | social | account | derived | ...) or source "
        "(hyperliquid | acp | dgclaw | coingecko | ...). Returns name, "
        "category, source, description, params/returns schema, tags, and "
        "resolvable (true = has a numeric value, usable as a prediction-"
        "criteria leaf) for each entry — use this to pick a name for "
        "fetch_data_point or register_prediction criteria."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Filter by category."},
            "source": {"type": "string", "description": "Filter by source."},
        },
    },
}


def _list_data_points(args: Dict[str, Any]) -> str:
    entries = data_point_registry.list_all(
        category=args.get("category"),
        source=args.get("source"),
    )
    return tool_result({
        "count": len(entries),
        "entries": [
            {**{k: v for k, v in asdict(e).items() if k != "fn"},
             "resolvable": bool(e.numeric_path)}
            for e in entries
        ],
    })


registry.register(
    name="list_data_points",
    toolset="perception",
    schema=SCHEMA,
    handler=lambda args, **kw: _list_data_points(args),
    description="Enumerate registered data points (filterable).",
    emoji="📇",
)
