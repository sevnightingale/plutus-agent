"""query_performance_attribution — performance grouped by strategy / venue / symbol."""

from __future__ import annotations

from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "query_performance_attribution",
    "description": (
        "Attribute PnL/R/win-rate to a dimension: strategy_id, venue, or "
        "symbol. Joins outcomes → positions → decisions → theses so the "
        "strategy_id grouping uses the FK link articulated at thesis time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "group_by":   {"type": "string", "enum": ["strategy_id", "venue", "symbol"]},
            "since_ts":   {"type": "number"},
            "until_ts":   {"type": "number"},
        },
        "required": ["group_by"],
    },
}


_GROUP_COLUMN = {
    "strategy_id": ("th.strategy_id", "strategy_id"),
    "venue":       ("p.venue", "venue"),
    "symbol":      ("p.symbol", "symbol"),
}


def _query_performance_attribution(args: Dict[str, Any]) -> str:
    group_by = args.get("group_by")
    if group_by not in _GROUP_COLUMN:
        return tool_error(
            f"group_by must be one of {sorted(_GROUP_COLUMN)}; got {group_by!r}"
        )
    column, alias = _GROUP_COLUMN[group_by]

    where, params = ["p.status = 'closed'"], []
    if args.get("since_ts") is not None:
        where.append("p.closed_at >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("p.closed_at <= ?"); params.append(float(args["until_ts"]))

    sql = f"""
        SELECT {column} AS {alias},
               COUNT(*) AS n_trades,
               SUM(o.realized_pnl_usd) AS total_pnl_usd,
               AVG(o.r_multiple) AS avg_r,
               SUM(CASE WHEN o.realized_pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate,
               AVG(o.holding_minutes) AS avg_holding_minutes
        FROM outcomes o
        JOIN positions p ON p.id = o.position_id
        JOIN trades t ON t.id = p.opening_trade_id
        JOIN decisions d ON d.id = t.decision_id
        JOIN theses th ON th.id = d.thesis_id
        WHERE {' AND '.join(where)}
        GROUP BY {column}
        ORDER BY total_pnl_usd DESC NULLS LAST
    """

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    return tool_result({"group_by": group_by, "rows": [dict(r) for r in rows]})


registry.register(
    name="query_performance_attribution",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_performance_attribution(args),
    description="Attribute performance to strategy / venue / symbol.",
    emoji="🧭",
)
