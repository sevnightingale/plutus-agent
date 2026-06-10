"""query_performance — aggregate PnL / R / win-rate over closed trades."""

from __future__ import annotations

from typing import Any, Dict

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_performance",
    "description": (
        "Aggregate performance over closed positions: total realized PnL, "
        "average R-multiple, win rate, count. Optionally filter by venue, "
        "symbol, strategy_id, or date range. Group by symbol/venue/strategy_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts":    {"type": "number"},
            "until_ts":    {"type": "number"},
            "venue":       {"type": "string"},
            "symbol":      {"type": "string"},
            "strategy_id": {"type": "integer"},
            "group_by":    {"type": "string", "enum": ["symbol", "venue", "strategy_id"]},
        },
    },
}


def _query_performance(args: Dict[str, Any]) -> str:
    group_by = args.get("group_by")
    where, params = ["p.status = 'closed'"], []

    if args.get("since_ts") is not None:
        where.append("p.closed_at >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("p.closed_at <= ?"); params.append(float(args["until_ts"]))
    if args.get("venue"):
        where.append("p.venue = ?"); params.append(args["venue"])
    if args.get("symbol"):
        where.append("p.symbol = ?"); params.append(args["symbol"])
    if args.get("strategy_id") is not None:
        where.append("th.strategy_id = ?"); params.append(int(args["strategy_id"]))

    select_group = {
        "symbol":      "p.symbol AS group_key,",
        "venue":       "p.venue AS group_key,",
        "strategy_id": "th.strategy_id AS group_key,",
        None:          "'all' AS group_key,",
    }[group_by]
    group_clause = ""
    if group_by:
        column = {"symbol": "p.symbol", "venue": "p.venue",
                  "strategy_id": "th.strategy_id"}[group_by]
        group_clause = f"GROUP BY {column}"

    sql = f"""
        SELECT {select_group}
            COUNT(*) AS n_trades,
            SUM(o.realized_pnl_usd) AS total_pnl_usd,
            AVG(o.realized_pnl_usd) AS avg_pnl_usd,
            AVG(o.r_multiple) AS avg_r,
            SUM(CASE WHEN o.realized_pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
        FROM outcomes o
        JOIN positions p ON p.id = o.position_id
        JOIN trades t ON t.id = p.opening_trade_id
        JOIN decisions d ON d.id = t.decision_id
        JOIN theses th ON th.id = d.thesis_id
        WHERE {' AND '.join(where)}
        {group_clause}
        ORDER BY total_pnl_usd DESC NULLS LAST
    """

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    return tool_result({
        "count": len(rows),
        "group_by": group_by or "all",
        "rows": [dict(r) for r in rows],
    })


registry.register(
    name="query_performance",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_performance(args),
    description="Aggregate PnL/R/win-rate over closed trades.",
    emoji="📊",
)
