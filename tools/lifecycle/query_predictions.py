"""query_predictions — find pending or resolved predictions.

The prediction-tracker skill calls this with status='due' to find
predictions whose horizon_ts has passed and need resolution. The
calibration-review skill calls with status='resolved' to compute the
calibration curve over predictions only (separate from trade calibration).
Plutus calls it interactively to review what was predicted vs what
happened.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from agent.lifecycle_db import get_lifecycle_db
from tools.lifecycle._helpers import safe_json_loads
from tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_predictions",
    "description": (
        "List predictions, filtered by status. status values: "
        "'pending' (not yet at horizon, not resolved), "
        "'due' (horizon passed, not resolved — Plutus owes a resolution call), "
        "'resolved' (already resolved), "
        "'all' (everything). Optional symbol/strategy_name/regime_tag filters."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status":         {"type": "string",
                               "enum": ["pending", "due", "resolved", "all"],
                               "default": "due"},
            "symbol":         {"type": "string"},
            "strategy_name":  {"type": "string"},
            "regime_tag":     {"type": "string"},
            "limit":          {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
        },
    },
}


def _fmt_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _query_predictions(args: Dict[str, Any]) -> str:
    status = (args.get("status") or "due").lower()
    limit = int(args.get("limit") or 50)

    where: List[str] = []
    params: List[Any] = []
    now = time.time()

    if status == "pending":
        where.append("resolved_at IS NULL AND horizon_ts > ?")
        params.append(now)
    elif status == "due":
        where.append("resolved_at IS NULL AND horizon_ts <= ?")
        params.append(now)
    elif status == "resolved":
        where.append("resolved_at IS NOT NULL")
    # 'all' adds nothing

    for col in ("symbol", "strategy_name", "regime_tag"):
        if args.get(col):
            where.append(f"{col} = ?")
            params.append(args[col])

    sql = (
        "SELECT id, ts, horizon_ts, symbol, claim_md, conviction, "
        "strategy_name, regime_tag, success_criteria_json, failure_criteria_json, "
        "resolved_at, outcome, resolution_notes_md "
        "FROM predictions"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    db = get_lifecycle_db()
    rows = db.conn().execute(sql, params).fetchall()

    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "made_at": _fmt_ts(r["ts"]),
            "horizon": _fmt_ts(r["horizon_ts"]),
            "overdue_hours": (
                round((now - r["horizon_ts"]) / 3600.0, 2)
                if r["resolved_at"] is None and r["horizon_ts"] < now else None
            ),
            "symbol": r["symbol"],
            "claim": r["claim_md"],
            "conviction": r["conviction"],
            "strategy_name": r["strategy_name"],
            "regime_tag": r["regime_tag"],
            "success_criteria": safe_json_loads(r["success_criteria_json"]),
            "failure_criteria": safe_json_loads(r["failure_criteria_json"]),
            "resolved_at": _fmt_ts(r["resolved_at"]),
            "outcome": r["outcome"],
            "resolution_notes": r["resolution_notes_md"],
        })

    return tool_result({"count": len(out), "status_filter": status, "predictions": out})


registry.register(
    name="query_predictions",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_predictions(args),
    description="List predictions by status (pending/due/resolved/all).",
    emoji="🔮",
)
