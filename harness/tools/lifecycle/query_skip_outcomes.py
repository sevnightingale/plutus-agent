"""query_skip_outcomes — counterfactual on skip decisions.

Returns decisions where action='skip' along with the linked thesis. Computing
"what the price actually did" relative to the skipped thesis requires venue
price history — that lookup lands with the HL execution wire-up in Phase 4l
(via fetch_data_point auto-snapshots over the relevant window). For Phase 4a
this tool just surfaces the skipped decisions so they remain queryable.
"""

from __future__ import annotations

from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_skip_outcomes",
    "description": (
        "List skip decisions with their linked thesis (action='skip' or 'hold'). "
        "Skipped opportunities are valuable counterfactual data — they tell you "
        "whether your filter was correct. Date range and limit available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts": {"type": "number"},
            "until_ts": {"type": "number"},
            "limit":    {"type": "integer", "default": 50},
        },
    },
}


def _query_skip_outcomes(args: Dict[str, Any]) -> str:
    where, params = ["d.action IN ('skip', 'hold')"], []
    if args.get("since_ts") is not None:
        where.append("d.ts >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("d.ts <= ?"); params.append(float(args["until_ts"]))
    limit = max(1, min(int(args.get("limit") or 50), 500))

    sql = f"""
        SELECT d.id AS decision_id, d.ts, d.action, d.conviction,
               th.id AS thesis_id, th.symbol,
               substr(th.text_md, 1, 200) AS thesis_snippet,
               th.invalidation_criteria_json
        FROM decisions d JOIN theses th ON th.id = d.thesis_id
        WHERE {' AND '.join(where)}
        ORDER BY d.ts DESC LIMIT ?
    """
    params.append(limit)

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    return tool_result({"count": len(rows), "skips": [dict(r) for r in rows]})


registry.register(
    name="query_skip_outcomes",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_skip_outcomes(args),
    description="Skip / hold decisions for counterfactual review.",
    emoji="↪️",
)
