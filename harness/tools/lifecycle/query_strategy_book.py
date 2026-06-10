"""query_strategy_book — active strategies + per-strategy performance + edge-decay flag."""

from __future__ import annotations

import time
from typing import Any, Dict

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.lifecycle._helpers import safe_json_loads
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_strategy_book",
    "description": (
        "Show the strategy book: each strategy with its lifetime + last-30d "
        "performance, plus an edge_decay flag when the recent win rate or "
        "average R has dropped meaningfully versus lifetime. Filter by status "
        "(active | paused | retired)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status":               {"type": "string"},
            "edge_decay_win_rate_drop": {
                "type": "number", "default": 0.10,
                "description": "Win-rate drop (lifetime → 30d) that triggers the flag.",
            },
            "edge_decay_avg_r_drop": {
                "type": "number", "default": 0.20,
                "description": "Avg-R drop that triggers the flag.",
            },
        },
    },
}


_PERF_SQL = """
    SELECT COUNT(*) AS n_trades,
           SUM(o.realized_pnl_usd) AS total_pnl_usd,
           AVG(o.r_multiple) AS avg_r,
           SUM(CASE WHEN o.realized_pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS win_rate
    FROM outcomes o
    JOIN positions p ON p.id = o.position_id
    JOIN trades t ON t.id = p.opening_trade_id
    JOIN decisions d ON d.id = t.decision_id
    JOIN theses th ON th.id = d.thesis_id
    WHERE p.status = 'closed' AND th.strategy_id = ?
    {extra}
"""


def _query_strategy_book(args: Dict[str, Any]) -> str:
    win_drop = float(args.get("edge_decay_win_rate_drop") or 0.10)
    r_drop = float(args.get("edge_decay_avg_r_drop") or 0.20)
    db = get_lifecycle_db()
    where, params = ["1=1"], []
    if args.get("status"):
        where.append("status = ?"); params.append(args["status"])

    strategies = db.conn().execute(
        "SELECT id, name, description_md, hypothesis_md, regime_conditions_json, "
        "status, created_at, retired_at, retirement_reason FROM strategies "
        f"WHERE {' AND '.join(where)} ORDER BY status, name",
        params,
    ).fetchall()

    cutoff_30d = time.time() - 30 * 24 * 3600
    out = []
    for s in strategies:
        lifetime = db.conn().execute(_PERF_SQL.format(extra=""), (s["id"],)).fetchone()
        recent = db.conn().execute(
            _PERF_SQL.format(extra="AND p.closed_at >= ?"),
            (s["id"], cutoff_30d),
        ).fetchone()

        edge_decay = False
        if (lifetime["win_rate"] is not None and recent["win_rate"] is not None
                and lifetime["win_rate"] - recent["win_rate"] >= win_drop):
            edge_decay = True
        if (lifetime["avg_r"] is not None and recent["avg_r"] is not None
                and lifetime["avg_r"] - recent["avg_r"] >= r_drop):
            edge_decay = True

        out.append({
            "id": s["id"],
            "name": s["name"],
            "status": s["status"],
            "description_md": s["description_md"],
            "hypothesis_md": s["hypothesis_md"],
            "regime_conditions": safe_json_loads(s["regime_conditions_json"]),
            "created_at": s["created_at"],
            "retired_at": s["retired_at"],
            "retirement_reason": s["retirement_reason"],
            "lifetime": dict(lifetime),
            "last_30d": dict(recent),
            "edge_decay": edge_decay,
        })

    return tool_result({"count": len(out), "strategies": out})


registry.register(
    name="query_strategy_book",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_strategy_book(args),
    description="Strategy book with lifetime/30d perf + edge-decay flag.",
    emoji="📔",
)
