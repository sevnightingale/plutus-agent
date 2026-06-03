"""query_conviction_trajectory — time-series of conviction for one position."""

from __future__ import annotations

from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.lifecycle._helpers import safe_json_loads
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "query_conviction_trajectory",
    "description": (
        "Return the conviction curve for one position: every position_evaluation "
        "in time order, with thesis_status, recommended_action, and rationale. "
        "Use this to inspect HOW Plutus thought during a hold — the basis for "
        "post-trade reflection on whether re-evaluation cadence and signal-"
        "responsiveness were calibrated."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "position_id": {"type": "integer"},
        },
        "required": ["position_id"],
    },
}


def _query_conviction_trajectory(args: Dict[str, Any]) -> str:
    position_id = args.get("position_id")
    if not position_id:
        return tool_error("query_conviction_trajectory requires position_id")

    db = get_lifecycle_db()
    pos = db.conn().execute(
        "SELECT id, venue, symbol, side, status, opened_at, closed_at "
        "FROM positions WHERE id = ?",
        (int(position_id),),
    ).fetchone()
    if pos is None:
        return tool_error(f"position {position_id} not found")

    rows = db.conn().execute(
        "SELECT id, ts, conviction, thesis_status, active_thesis_id, "
        "rationale_md, snapshot_ids_json, recommended_action, "
        "action_taken_decision_id "
        "FROM position_evaluations WHERE position_id = ? ORDER BY ts ASC",
        (int(position_id),),
    ).fetchall()

    trajectory = []
    for r in rows:
        item = dict(r)
        item["snapshot_ids"] = safe_json_loads(item.pop("snapshot_ids_json"))
        trajectory.append(item)

    return tool_result({
        "position": dict(pos),
        "count": len(trajectory),
        "trajectory": trajectory,
    })


registry.register(
    name="query_conviction_trajectory",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_conviction_trajectory(args),
    description="Conviction time-series for one position.",
    emoji="📉",
)
