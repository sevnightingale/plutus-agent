"""query_trades — paginated trade history with thesis context."""

from __future__ import annotations

from typing import Any, Dict

from trading.lifecycle.db import get_lifecycle_db
from trading.lifecycle.queries._helpers import rows_to_dicts
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_trades",
    "description": (
        "Query the trade history with optional filters (symbol, venue, side, "
        "date range, conviction range). Returns trades joined to the linked "
        "decision and thesis (action, conviction, thesis text snippet)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol":            {"type": "string"},
            "venue":             {"type": "string"},
            "side":              {"type": "string", "enum": ["long", "short", "close"]},
            "since_ts":          {"type": "number", "description": "Unix epoch."},
            "until_ts":          {"type": "number", "description": "Unix epoch."},
            "min_conviction":    {"type": "number"},
            "max_conviction":    {"type": "number"},
            "limit":             {"type": "integer", "default": 50},
        },
    },
}


def _query_trades(args: Dict[str, Any]) -> str:
    where, params = ["1=1"], []
    if args.get("symbol"):
        where.append("t.symbol = ?"); params.append(args["symbol"])
    if args.get("venue"):
        where.append("t.venue = ?"); params.append(args["venue"])
    if args.get("side"):
        where.append("t.side = ?"); params.append(args["side"])
    if args.get("since_ts") is not None:
        where.append("t.ts >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("t.ts <= ?"); params.append(float(args["until_ts"]))
    if args.get("min_conviction") is not None:
        where.append("d.conviction >= ?"); params.append(float(args["min_conviction"]))
    if args.get("max_conviction") is not None:
        where.append("d.conviction <= ?"); params.append(float(args["max_conviction"]))
    limit = max(1, min(int(args.get("limit") or 50), 500))

    sql = (
        "SELECT t.id AS trade_id, t.ts, t.venue, t.symbol, t.side, t.size, "
        "t.fill_price, t.slippage_bp, "
        "d.id AS decision_id, d.action, d.conviction, "
        "th.id AS thesis_id, substr(th.text_md, 1, 200) AS thesis_snippet "
        "FROM trades t JOIN decisions d ON d.id = t.decision_id "
        "JOIN theses th ON th.id = d.thesis_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY t.ts DESC LIMIT ?"
    )
    params.append(limit)

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    return tool_result({"count": len(rows), "trades": rows_to_dicts(rows)})


registry.register(
    name="query_trades",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_trades(args),
    description="Trade history with thesis context.",
    emoji="📈",
)
