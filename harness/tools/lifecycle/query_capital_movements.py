"""query_capital_movements — deposits, withdrawals, internal/venue transfers."""

from __future__ import annotations

from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.lifecycle._helpers import rows_to_dicts
from tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_capital_movements",
    "description": (
        "Query capital movements (deposits, withdrawals, internal_transfer, "
        "venue_transfer). Filter by date range, movement_type, account "
        "(matches either from_account or to_account), or token."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts":      {"type": "number"},
            "until_ts":      {"type": "number"},
            "movement_type": {
                "type": "string",
                "enum": ["deposit", "withdrawal", "internal_transfer", "venue_transfer"],
            },
            "account":       {"type": "string"},
            "token":         {"type": "string"},
            "limit":         {"type": "integer", "default": 100},
        },
    },
}


def _query_capital_movements(args: Dict[str, Any]) -> str:
    where, params = ["1=1"], []
    if args.get("since_ts") is not None:
        where.append("ts >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("ts <= ?"); params.append(float(args["until_ts"]))
    if args.get("movement_type"):
        where.append("movement_type = ?"); params.append(args["movement_type"])
    if args.get("token"):
        where.append("token = ?"); params.append(args["token"])
    if args.get("account"):
        where.append("(from_account = ? OR to_account = ?)")
        params.extend([args["account"], args["account"]])
    limit = max(1, min(int(args.get("limit") or 100), 1000))

    sql = (
        "SELECT id, ts, from_account, to_account, token, amount_token, "
        "amount_usd_at_time, movement_type, tx_hash, note FROM capital_movements "
        f"WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?"
    )
    params.append(limit)

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    return tool_result({"count": len(rows), "movements": rows_to_dicts(rows)})


registry.register(
    name="query_capital_movements",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_capital_movements(args),
    description="Capital movement history with filters.",
    emoji="🔄",
)
