"""query_strategy_stats — per-strategy performance from theses.strategy_name.

Replaces the legacy ``query_strategy_book`` model where strategies lived
in their own table. Strategies now live as files under
``~/.plutus-agent/strategies/{active,trial,observation,proposed,retired}/``;
performance is a derived view over theses tagged with strategy_name JOIN
outcomes.

Returns: lifetime + last-N-days perf, per-conviction-bucket calibration
within the strategy, regime breakdown, and an edge_decay flag (last-10
trades vs prior). Optional include_predictions=true also reports
prediction-only calibration for the strategy (useful when in observation
stage with few/no trades).
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_strategy_stats",
    "description": (
        "Per-strategy performance summary derived from theses.strategy_name. "
        "Returns lifetime + recent-window stats, per-conviction calibration, "
        "regime breakdown, edge-decay flag. Optional include_predictions adds "
        "prediction-only calibration for strategies in observation stage."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "strategy_name": {
                "type": "string",
                "description": "If omitted, returns stats for ALL distinct strategy_names.",
            },
            "recent_days": {
                "type": "integer", "default": 30, "minimum": 1, "maximum": 365,
                "description": "Window for 'recent' stats (default 30d).",
            },
            "include_predictions": {
                "type": "boolean", "default": True,
                "description": "Also report prediction-only calibration.",
            },
            "edge_decay_win_rate_drop": {
                "type": "number", "default": 0.10,
                "description": "Win-rate drop (lifetime → recent) that flags edge decay.",
            },
            "edge_decay_avg_r_drop": {
                "type": "number", "default": 0.20,
                "description": "Avg-R drop that flags edge decay.",
            },
        },
    },
}


_TRADE_PERF_SQL = """
    SELECT COUNT(*)                                                         AS n_trades,
           SUM(o.realized_pnl_usd)                                          AS total_pnl_usd,
           AVG(o.r_multiple)                                                AS avg_r,
           SUM(CASE WHEN o.realized_pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0
               / NULLIF(COUNT(*), 0)                                        AS win_rate,
           AVG(CASE WHEN o.r_multiple IS NOT NULL THEN o.conviction_at_entry END) AS avg_conviction_at_entry
    FROM theses th
    JOIN decisions d ON d.thesis_id = th.id
    JOIN trades t    ON t.decision_id = d.id
    JOIN positions p ON p.opening_trade_id = t.id
    JOIN outcomes  o ON o.position_id = p.id
    WHERE p.status = 'closed'
      AND th.strategy_name = ?
      {extra}
"""

_REGIME_SQL = """
    SELECT th.regime_tag,
           COUNT(*)                                                         AS n_trades,
           AVG(o.r_multiple)                                                AS avg_r,
           SUM(CASE WHEN o.realized_pnl_usd > 0 THEN 1 ELSE 0 END) * 1.0
               / NULLIF(COUNT(*), 0)                                        AS win_rate
    FROM theses th
    JOIN decisions d ON d.thesis_id = th.id
    JOIN trades t    ON t.decision_id = d.id
    JOIN positions p ON p.opening_trade_id = t.id
    JOIN outcomes  o ON o.position_id = p.id
    WHERE p.status = 'closed' AND th.strategy_name = ?
    GROUP BY th.regime_tag
"""

_PRED_CALIB_SQL = """
    SELECT conviction, outcome
    FROM predictions
    WHERE strategy_name = ? AND outcome IS NOT NULL
"""

_TRADE_CALIB_SQL = """
    SELECT o.conviction_at_entry AS c, o.r_multiple AS r
    FROM theses th
    JOIN decisions d ON d.thesis_id = th.id
    JOIN trades t    ON t.decision_id = d.id
    JOIN positions p ON p.opening_trade_id = t.id
    JOIN outcomes  o ON o.position_id = p.id
    WHERE p.status = 'closed' AND th.strategy_name = ?
      AND o.conviction_at_entry IS NOT NULL AND o.r_multiple IS NOT NULL
"""


def _bucketize(rows: List[Dict[str, float]], key: str, bucket: float = 0.1) -> List[Dict[str, Any]]:
    """Generic conviction bucketization."""
    buckets: Dict[float, List[Dict[str, float]]] = {}
    for row in rows:
        c = row.get(key)
        if c is None:
            continue
        k = round((c // bucket) * bucket, 4)
        buckets.setdefault(k, []).append(row)
    out = []
    for k in sorted(buckets.keys()):
        out.append({
            "bucket_low": k,
            "bucket_high": round(k + bucket, 4),
            "n": len(buckets[k]),
            "members": buckets[k],
        })
    return out


def _strategy_stats_one(db, name: str, recent_cutoff: float,
                        include_predictions: bool,
                        win_drop: float, r_drop: float) -> Dict[str, Any]:
    lifetime = dict(db.conn().execute(_TRADE_PERF_SQL.format(extra=""), (name,)).fetchone())
    recent = dict(db.conn().execute(
        _TRADE_PERF_SQL.format(extra="AND p.closed_at >= ?"),
        (name, recent_cutoff),
    ).fetchone())

    # Regime breakdown
    regime_rows = db.conn().execute(_REGIME_SQL, (name,)).fetchall()
    regime_breakdown = {(r["regime_tag"] or "(unspecified)"): {
        "n_trades": r["n_trades"],
        "win_rate": r["win_rate"],
        "avg_r": r["avg_r"],
    } for r in regime_rows}

    # Edge decay
    edge_decay = False
    decay_reasons: List[str] = []
    if (lifetime["win_rate"] is not None and recent["win_rate"] is not None
            and lifetime["win_rate"] - recent["win_rate"] >= win_drop):
        edge_decay = True
        decay_reasons.append(
            f"win_rate dropped {lifetime['win_rate']:.2f} → {recent['win_rate']:.2f}"
        )
    if (lifetime["avg_r"] is not None and recent["avg_r"] is not None
            and lifetime["avg_r"] - recent["avg_r"] >= r_drop):
        edge_decay = True
        decay_reasons.append(
            f"avg_r dropped {lifetime['avg_r']:.3f} → {recent['avg_r']:.3f}"
        )

    # Trade-conviction calibration
    trade_rows = [
        {"c": r["c"], "r": r["r"], "won": r["r"] > 0}
        for r in db.conn().execute(_TRADE_CALIB_SQL, (name,)).fetchall()
    ]
    trade_calib = []
    for b in _bucketize(trade_rows, "c"):
        members = b.pop("members")
        rs = [m["r"] for m in members]
        trade_calib.append({
            **b,
            "win_rate": sum(1 for m in members if m["won"]) / len(members),
            "mean_r": statistics.mean(rs),
        })

    out: Dict[str, Any] = {
        "name": name,
        "lifetime": lifetime,
        "recent": recent,
        "regime_breakdown": regime_breakdown,
        "edge_decay": edge_decay,
        "decay_reasons": decay_reasons,
        "trade_conviction_calibration": trade_calib,
    }

    if include_predictions:
        pred_rows = db.conn().execute(_PRED_CALIB_SQL, (name,)).fetchall()
        pred_data = [
            {"c": r["conviction"], "won": r["outcome"] == "correct",
             "outcome": r["outcome"]}
            for r in pred_rows
        ]
        pred_calib = []
        for b in _bucketize(pred_data, "c"):
            members = b.pop("members")
            resolved = [m for m in members if m["outcome"] in ("correct", "wrong")]
            pred_calib.append({
                **b,
                "win_rate": (sum(1 for m in resolved if m["won"]) / len(resolved)
                             if resolved else None),
                "n_resolved": len(resolved),
            })
        out["prediction_count"] = len(pred_data)
        out["prediction_calibration"] = pred_calib

    return out


def _query_strategy_stats(args: Dict[str, Any]) -> str:
    db = get_lifecycle_db()
    recent_days = int(args.get("recent_days") or 30)
    cutoff = time.time() - recent_days * 86400.0
    include_preds = bool(args.get("include_predictions", True))
    win_drop = float(args.get("edge_decay_win_rate_drop") or 0.10)
    r_drop = float(args.get("edge_decay_avg_r_drop") or 0.20)

    if args.get("strategy_name"):
        name = args["strategy_name"]
        return tool_result({
            "strategy": _strategy_stats_one(
                db, name, cutoff, include_preds, win_drop, r_drop,
            )
        })

    # All distinct strategy_names from theses + predictions
    names = sorted({
        r[0] for r in db.conn().execute(
            "SELECT DISTINCT strategy_name FROM theses WHERE strategy_name IS NOT NULL "
            "UNION SELECT DISTINCT strategy_name FROM predictions WHERE strategy_name IS NOT NULL"
        ).fetchall()
    })
    out = [
        _strategy_stats_one(db, n, cutoff, include_preds, win_drop, r_drop)
        for n in names
    ]
    return tool_result({"count": len(out), "strategies": out})


registry.register(
    name="query_strategy_stats",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_strategy_stats(args),
    description="Per-strategy performance + calibration + edge-decay flag.",
    emoji="📊",
)
