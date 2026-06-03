"""query_equity_curve — time-series of total_equity from data_point_snapshots."""

from __future__ import annotations

import json
from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.lifecycle._helpers import safe_json_loads
from tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_equity_curve",
    "description": (
        "Return the equity curve as a time-series of total_equity snapshots. "
        "Reads from data_point_snapshots WHERE name='total_equity', so this "
        "depends on a 'total_equity' data point being registered and fetched "
        "(typically by the heartbeat skill). Optional date range + max points."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts":   {"type": "number"},
            "until_ts":   {"type": "number"},
            "max_points": {"type": "integer", "default": 500},
        },
    },
}


def _query_equity_curve(args: Dict[str, Any]) -> str:
    where, params = ["name = 'total_equity'"], []
    if args.get("since_ts") is not None:
        where.append("ts >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("ts <= ?"); params.append(float(args["until_ts"]))
    max_points = max(1, min(int(args.get("max_points") or 500), 5000))

    sql = (
        "SELECT id, ts, value_json, source FROM data_point_snapshots "
        f"WHERE {' AND '.join(where)} ORDER BY ts ASC LIMIT ?"
    )
    params.append(max_points)

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    points = []
    for r in rows:
        v = safe_json_loads(r["value_json"]) or {}
        equity = v.get("usd") if isinstance(v, dict) else v
        points.append({
            "snapshot_id": r["id"],
            "ts": r["ts"],
            "equity_usd": equity,
            "source": r["source"],
        })

    return tool_result({"count": len(points), "points": points})


registry.register(
    name="query_equity_curve",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_equity_curve(args),
    description="Time-series of total_equity snapshots.",
    emoji="💰",
)
