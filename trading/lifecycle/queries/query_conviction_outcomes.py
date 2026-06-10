"""query_conviction_outcomes — aggregate trajectory shapes vs outcomes.

Buckets each closed position by the shape of its conviction trajectory and
reports outcome stats per bucket. The shape classification is intentionally
simple in 4a: compares the linear regression slope of conviction-vs-time to
a small threshold, with a 'volatile' bucket for high-stdev trajectories.
This is enough signal for "do losers show declining conviction" /
"do winners show stable conviction" without inventing a heavier classifier.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List

from trading.lifecycle.db import get_lifecycle_db
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_conviction_outcomes",
    "description": (
        "Group closed positions by conviction-trajectory shape (rising | "
        "steady | declining | volatile) and report outcome stats (n, mean R, "
        "win rate, avg PnL) per bucket. Reveals whether conviction shape "
        "predicts outcome — a key calibration signal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts":          {"type": "number"},
            "until_ts":          {"type": "number"},
            "slope_threshold":   {
                "type": "number", "default": 0.02,
                "description": "Per-evaluation conviction change threshold for rising/declining.",
            },
            "volatility_threshold": {
                "type": "number", "default": 0.15,
                "description": "Stdev threshold above which the shape is classified as 'volatile'.",
            },
        },
    },
}


def _classify(convictions: List[float], slope_threshold: float,
              volatility_threshold: float) -> str:
    if len(convictions) < 2:
        return "insufficient_data"
    if statistics.stdev(convictions) >= volatility_threshold:
        return "volatile"
    # OLS slope vs index.
    n = len(convictions)
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = statistics.mean(convictions)
    num = sum((xs[i] - mean_x) * (convictions[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    if slope > slope_threshold:
        return "rising"
    if slope < -slope_threshold:
        return "declining"
    return "steady"


def _query_conviction_outcomes(args: Dict[str, Any]) -> str:
    slope_threshold = float(args.get("slope_threshold") or 0.02)
    volatility_threshold = float(args.get("volatility_threshold") or 0.15)

    where, params = ["p.status = 'closed'"], []
    if args.get("since_ts") is not None:
        where.append("p.closed_at >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("p.closed_at <= ?"); params.append(float(args["until_ts"]))

    db = get_lifecycle_db()
    positions = db.conn().execute(
        "SELECT p.id, o.realized_pnl_usd, o.r_multiple "
        "FROM positions p JOIN outcomes o ON o.position_id = p.id "
        f"WHERE {' AND '.join(where)}",
        params,
    ).fetchall()

    buckets: Dict[str, Dict[str, Any]] = {}
    for pos in positions:
        convictions = [
            row["conviction"] for row in db.conn().execute(
                "SELECT conviction FROM position_evaluations "
                "WHERE position_id = ? ORDER BY ts",
                (pos["id"],),
            ).fetchall()
        ]
        shape = _classify(convictions, slope_threshold, volatility_threshold)
        bucket = buckets.setdefault(shape, {"n_positions": 0, "pnl": [], "rs": []})
        bucket["n_positions"] += 1
        if pos["realized_pnl_usd"] is not None:
            bucket["pnl"].append(pos["realized_pnl_usd"])
        if pos["r_multiple"] is not None:
            bucket["rs"].append(pos["r_multiple"])

    summary = []
    for shape in ("rising", "steady", "declining", "volatile", "insufficient_data"):
        b = buckets.get(shape)
        if not b:
            continue
        summary.append({
            "shape": shape,
            "n_positions": b["n_positions"],
            "avg_pnl_usd": statistics.mean(b["pnl"]) if b["pnl"] else None,
            "win_rate": (sum(1 for x in b["pnl"] if x > 0) / len(b["pnl"]))
                        if b["pnl"] else None,
            "avg_r": statistics.mean(b["rs"]) if b["rs"] else None,
        })

    return tool_result({
        "n_positions_total": sum(b["n_positions"] for b in buckets.values()),
        "slope_threshold": slope_threshold,
        "volatility_threshold": volatility_threshold,
        "buckets": summary,
    })


registry.register(
    name="query_conviction_outcomes",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_conviction_outcomes(args),
    description="Trajectory-shape buckets vs outcome stats.",
    emoji="📐",
)
