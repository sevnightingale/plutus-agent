"""query_unreflected_closes — positions that closed without a reflection.

The plutus-main beat handshake (Phase 0) calls this to discover trades that
closed between beats and still need interpretive postmortem work. plutus-ops
records the mechanical close (status='closed', outcome row); plutus-main
records the reflection (loss_postmortem / post_trade / weekly_review) covering
the closed position's id in `reflections.position_ids_json`.

A position is "unreflected" when no reflection of those three kinds contains
its id in its position_ids_json array.

The query uses SQLite JSON1's ``json_each`` rather than ``LIKE '%' || id || '%'``
because the LIKE variant has a subtle bug: position id 2 would match the substring
"2" inside "12", "20", "22", etc. ``json_each`` expands the JSON array into rows
of true integer values so the IN/NOT IN comparison is exact.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.lifecycle._helpers import rows_to_dicts
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_unreflected_closes",
    "description": (
        "Return positions that closed since `since_ts` and are not yet covered "
        "by any loss_postmortem / post_trade / weekly_review reflection. Used "
        "by plutus-main Phase 0 to discover pending interpretive work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts": {
                "type": "number",
                "description": "Unix epoch. Only positions with closed_at > since_ts are considered.",
            },
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["since_ts"],
    },
}


def _query_unreflected_closes(args: Dict[str, Any]) -> str:
    since_ts = float(args["since_ts"])
    limit = max(1, min(int(args.get("limit") or 20), 200))

    sql = (
        "SELECT p.id, p.venue, p.symbol, p.side, p.size, p.opened_at, p.closed_at, "
        "p.opening_trade_id, p.closing_trade_id "
        "FROM positions p "
        "WHERE p.status='closed' AND p.closed_at > ? "
        "AND p.id NOT IN ("
        "    SELECT json_each.value "
        "    FROM reflections r, json_each(r.position_ids_json) "
        "    WHERE r.reflection_kind IN ('loss_postmortem', 'post_trade', 'weekly_review') "
        "      AND r.position_ids_json IS NOT NULL"
        ") "
        "ORDER BY p.closed_at DESC LIMIT ?"
    )

    rows = get_lifecycle_db().conn().execute(sql, (since_ts, limit)).fetchall()
    return tool_result({"count": len(rows), "positions": rows_to_dicts(rows)})


registry.register(
    name="query_unreflected_closes",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_unreflected_closes(args),
    description="Positions closed since `since_ts` that have no covering reflection yet.",
    emoji="📭",
)
