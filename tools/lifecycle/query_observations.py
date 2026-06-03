"""query_observations — pull from the journal stream.

Slice by kind / symbol / strategy / time. Includes FTS5 search via
the optional 'search' parameter (matches against text_md).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from agent.lifecycle_db import get_lifecycle_db
from tools.lifecycle._helpers import safe_json_loads
from tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_observations",
    "description": (
        "Read from the journal stream. Filter by kind (noticed/watching/"
        "almost_traded/mental_model/pattern_candidate/edge_claim/"
        "edge_revoked/operator_input/regime_shift), symbol, strategy_name, "
        "time window, or text search. Defaults to last 30 entries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind":           {"type": "string"},
            "symbol":         {"type": "string"},
            "strategy_name":  {"type": "string"},
            "since_ts":       {"type": "number"},
            "until_ts":       {"type": "number"},
            "search":         {"type": "string",
                               "description": "FTS5 query against text_md."},
            "limit":          {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
        },
    },
}


def _fmt(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _query_observations(args: Dict[str, Any]) -> str:
    where: List[str] = []
    params: List[Any] = []

    if args.get("kind"):
        where.append("o.kind = ?")
        params.append(args["kind"])
    if args.get("symbol"):
        where.append("o.symbol = ?")
        params.append(args["symbol"])
    if args.get("strategy_name"):
        where.append("o.strategy_name = ?")
        params.append(args["strategy_name"])
    if args.get("since_ts") is not None:
        where.append("o.ts >= ?")
        params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("o.ts <= ?")
        params.append(float(args["until_ts"]))

    limit = int(args.get("limit") or 30)

    if args.get("search"):
        sql = (
            "SELECT o.id, o.ts, o.symbol, o.kind, o.text_md, o.strategy_name, "
            "o.related_thesis_ids_json, o.related_prediction_ids_json, "
            "o.snapshot_ids_json, o.structured_tags_json "
            "FROM observations_fts f JOIN observations o ON f.rowid = o.id "
            "WHERE f.observations_fts MATCH ?"
        )
        params = [args["search"]] + params
        if where:
            sql += " AND " + " AND ".join(where)
        sql += " ORDER BY o.ts DESC LIMIT ?"
    else:
        sql = (
            "SELECT id, ts, symbol, kind, text_md, strategy_name, "
            "related_thesis_ids_json, related_prediction_ids_json, "
            "snapshot_ids_json, structured_tags_json "
            "FROM observations o"
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
            "ts": _fmt(r["ts"]),
            "symbol": r["symbol"],
            "kind": r["kind"],
            "text_md": r["text_md"],
            "strategy_name": r["strategy_name"],
            "related_thesis_ids": safe_json_loads(r["related_thesis_ids_json"]),
            "related_prediction_ids": safe_json_loads(r["related_prediction_ids_json"]),
            "snapshot_ids": safe_json_loads(r["snapshot_ids_json"]),
            "structured_tags": safe_json_loads(r["structured_tags_json"]),
        })

    return tool_result({"count": len(out), "observations": out})


registry.register(
    name="query_observations",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_observations(args),
    description="Read journal entries (filter by kind/symbol/search/time).",
    emoji="📓",
)
