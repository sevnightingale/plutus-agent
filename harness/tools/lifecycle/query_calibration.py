"""query_calibration — does conviction predict realized R?"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_calibration",
    "description": (
        "Calibration check: does conviction-at-entry actually predict realized "
        "R-multiple? Returns mean/median R per conviction bucket, win rate per "
        "bucket, and the Pearson correlation between conviction and R. "
        "Optionally restrict to a date range, a strategy_name, or a regime_tag. "
        "include_predictions=true also reports prediction-only calibration "
        "(predictions accumulate faster than trades — better signal early on)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts":            {"type": "number"},
            "until_ts":            {"type": "number"},
            "bucket_width":        {"type": "number", "default": 0.1,
                                    "minimum": 0.05, "maximum": 0.5},
            "strategy_name":       {"type": "string"},
            "regime_tag":          {"type": "string"},
            "include_predictions": {"type": "boolean", "default": False},
        },
    },
}


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def _query_calibration(args: Dict[str, Any]) -> str:
    bucket = float(args.get("bucket_width") or 0.1)

    where, params = ["o.r_multiple IS NOT NULL", "o.conviction_at_entry IS NOT NULL"], []
    if args.get("since_ts") is not None:
        where.append("p.closed_at >= ?"); params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("p.closed_at <= ?"); params.append(float(args["until_ts"]))

    join = ""
    if args.get("strategy_name") or args.get("regime_tag"):
        join = (
            " JOIN trades t ON t.id = p.opening_trade_id "
            "JOIN decisions d ON d.id = t.decision_id "
            "JOIN theses th ON th.id = d.thesis_id "
        )
        if args.get("strategy_name"):
            where.append("th.strategy_name = ?"); params.append(args["strategy_name"])
        if args.get("regime_tag"):
            where.append("th.regime_tag = ?"); params.append(args["regime_tag"])

    sql = (
        "SELECT o.conviction_at_entry AS c, o.r_multiple AS r "
        "FROM outcomes o JOIN positions p ON p.id = o.position_id"
        + join +
        " WHERE " + " AND ".join(where)
    )
    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()

    out_trades: Dict[str, Any] = {
        "count": 0, "buckets": [], "pearson_r": None,
    }
    if rows:
        cs = [r["c"] for r in rows]
        rs = [r["r"] for r in rows]
        buckets: Dict[float, List[float]] = {}
        for c, r in zip(cs, rs):
            key = round((c // bucket) * bucket, 4)
            buckets.setdefault(key, []).append(r)
        out_buckets = []
        for key in sorted(buckets.keys()):
            vals = buckets[key]
            out_buckets.append({
                "bucket_low": key,
                "bucket_high": round(key + bucket, 4),
                "n_trades": len(vals),
                "mean_r": statistics.mean(vals),
                "median_r": statistics.median(vals),
                "win_rate": sum(1 for v in vals if v > 0) / len(vals),
            })
        out_trades = {
            "count": len(rows),
            "bucket_width": bucket,
            "pearson_r": _pearson(cs, rs),
            "buckets": out_buckets,
        }

    payload: Dict[str, Any] = {"trades": out_trades}

    if args.get("include_predictions"):
        pwhere, pparams = ["resolved_at IS NOT NULL"], []
        if args.get("since_ts") is not None:
            pwhere.append("resolved_at >= ?"); pparams.append(float(args["since_ts"]))
        if args.get("until_ts") is not None:
            pwhere.append("resolved_at <= ?"); pparams.append(float(args["until_ts"]))
        if args.get("strategy_name"):
            pwhere.append("strategy_name = ?"); pparams.append(args["strategy_name"])
        if args.get("regime_tag"):
            pwhere.append("regime_tag = ?"); pparams.append(args["regime_tag"])

        psql = (
            "SELECT conviction AS c, outcome FROM predictions "
            "WHERE " + " AND ".join(pwhere)
        )
        prows = get_lifecycle_db().conn().execute(psql, pparams).fetchall()
        out_pred: Dict[str, Any] = {"count": 0, "buckets": []}
        if prows:
            buckets_p: Dict[float, List[str]] = {}
            for r in prows:
                key = round((r["c"] // bucket) * bucket, 4)
                buckets_p.setdefault(key, []).append(r["outcome"])
            out_buckets = []
            for key in sorted(buckets_p.keys()):
                outs = buckets_p[key]
                resolved = [o for o in outs if o in ("correct", "wrong")]
                out_buckets.append({
                    "bucket_low": key,
                    "bucket_high": round(key + bucket, 4),
                    "n_total": len(outs),
                    "n_resolved": len(resolved),
                    "win_rate": (sum(1 for o in resolved if o == "correct") / len(resolved)
                                 if resolved else None),
                    "ambiguous_count": sum(1 for o in outs if o == "ambiguous"),
                })
            out_pred = {
                "count": len(prows),
                "bucket_width": bucket,
                "buckets": out_buckets,
            }
        payload["predictions"] = out_pred

    return tool_result(payload)


registry.register(
    name="query_calibration",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_calibration(args),
    description="Conviction-at-entry vs realized R correlation + buckets.",
    emoji="🎚️",
)
