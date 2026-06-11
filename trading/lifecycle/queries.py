"""Named read surface over lifecycle.db v2.

Compact, prompt-friendly dicts (no embeddings, no blobs). These functions are
the substance behind the desk's read toolset AND the spawn mechanism's
``lifecycle:<name>`` context blocks — one implementation, two consumers.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional


def _rows(cur) -> list:
    return [dict(r) for r in cur.fetchall()]


def open_predictions(conn: sqlite3.Connection, limit: int = 50) -> list:
    return _rows(conn.execute(
        """SELECT id, ts, horizon_ts, timescale, symbol, claim_md, conviction,
                  strategy_name, kind, regime_tag, risk_tolerance
           FROM predictions WHERE resolved_at IS NULL
           ORDER BY horizon_ts ASC LIMIT ?""", (limit,)))


def open_slot_counts(conn: sqlite3.Connection) -> dict:
    """The slot ecology at a glance: open predictions by timescale + strategy.

    Returned by register_prediction so predict sees the budget as it spends
    it (the 10-slot target and per-timescale quotas live in its recipe).
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL"
    ).fetchone()[0]
    by_timescale = dict(conn.execute(
        "SELECT timescale, COUNT(*) FROM predictions "
        "WHERE resolved_at IS NULL GROUP BY timescale"
    ).fetchall())
    by_strategy = dict(conn.execute(
        "SELECT strategy_name, COUNT(*) FROM predictions "
        "WHERE resolved_at IS NULL AND strategy_name IS NOT NULL "
        "GROUP BY strategy_name"
    ).fetchall())
    return {"open_total": total, "by_timescale": by_timescale,
            "by_strategy": by_strategy}


def due_predictions(conn: sqlite3.Connection, now: Optional[float] = None) -> list:
    now = now if now is not None else time.time()
    return _rows(conn.execute(
        """SELECT id, ts, horizon_ts, timescale, symbol, claim_md,
                  success_criteria_json, failure_criteria_json, conviction,
                  strategy_name, kind
           FROM predictions WHERE resolved_at IS NULL AND horizon_ts <= ?
           ORDER BY horizon_ts ASC""", (now,)))


def prediction(conn: sqlite3.Connection, prediction_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT id, session_name, agent, ts, horizon_ts, timescale, symbol,
                  claim_md, success_criteria_json, failure_criteria_json,
                  invalidation_criteria_json, risk_tolerance, conviction,
                  strategy_name, kind, regime_tag, resolved_at, outcome,
                  resolved_by, resolution_notes_md, realized_value_json
           FROM predictions WHERE id=?""", (prediction_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["support_scores"] = _rows(conn.execute(
        """SELECT data_point, score, kind, weight, normalizer, reasoning_md
           FROM support_scores WHERE prediction_id=? ORDER BY data_point""",
        (prediction_id,)))
    return out


def open_position(conn: sqlite3.Connection) -> Optional[dict]:
    """The one open position (single-position law) with its thesis chain."""
    pos = conn.execute(
        """SELECT id, venue, symbol, side, size, opening_trade_id, opened_at
           FROM positions WHERE status='open' ORDER BY opened_at DESC LIMIT 1"""
    ).fetchone()
    if pos is None:
        return None
    out = dict(pos)
    thesis = conn.execute(
        """SELECT t.id, t.prediction_id, t.text_md, t.strategy_name,
                  t.sl_price, t.sl_rationale_md
           FROM theses t
           JOIN decisions d ON d.thesis_id = t.id
           JOIN trades tr ON tr.decision_id = d.id
           WHERE tr.id = ? LIMIT 1""", (out["opening_trade_id"],)).fetchone()
    if thesis:
        out["thesis"] = dict(thesis)
        out["prediction"] = prediction(conn, thesis["prediction_id"])
    last_eval = conn.execute(
        """SELECT ts, conviction, thesis_status, recommended_action
           FROM position_evaluations WHERE position_id=?
           ORDER BY ts DESC LIMIT 1""", (out["id"],)).fetchone()
    out["last_evaluation"] = dict(last_eval) if last_eval else None
    return out


def recent_outcomes(conn: sqlite3.Connection, limit: int = 10) -> list:
    return _rows(conn.execute(
        """SELECT p.id AS position_id, p.symbol, p.side, p.opened_at, p.closed_at,
                  o.realized_pnl_usd, o.realized_pnl_pct, o.r_multiple,
                  o.exit_reason, o.mae_pct, o.mfe_pct,
                  t.strategy_name, t.prediction_id
           FROM positions p
           JOIN outcomes o ON o.position_id = p.id
           LEFT JOIN trades tr ON tr.id = p.opening_trade_id
           LEFT JOIN decisions d ON d.id = tr.decision_id
           LEFT JOIN theses t ON t.id = d.thesis_id
           WHERE p.status='closed'
           ORDER BY p.closed_at DESC LIMIT ?""", (limit,)))


def calibration(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    regime_tag: Optional[str] = None,
    timescale: Optional[str] = None,
    bucket_width: float = 0.1,
) -> dict:
    """Conviction-vs-hit-rate curve over RESOLVED predictions.

    Uniform at the prediction level (locked): correct / (correct + wrong);
    ambiguous and unresolvable are reported but excluded from the rate.
    """
    where = ["resolved_at IS NOT NULL"]
    args: list = []
    if strategy_name is not None:
        where.append("strategy_name = ?")
        args.append(strategy_name)
    if regime_tag is not None:
        where.append("regime_tag = ?")
        args.append(regime_tag)
    if timescale is not None:
        where.append("timescale = ?")
        args.append(timescale)

    rows = conn.execute(
        f"SELECT conviction, outcome FROM predictions WHERE {' AND '.join(where)}",
        args,
    ).fetchall()

    buckets: dict = {}
    excluded = 0
    for r in rows:
        if r["outcome"] not in ("correct", "wrong"):
            excluded += 1
            continue
        b = min(int(r["conviction"] / bucket_width), int(1 / bucket_width) - 1)
        lo = round(b * bucket_width, 2)
        key = f"{lo:.1f}-{lo + bucket_width:.1f}"
        slot = buckets.setdefault(key, {"n": 0, "correct": 0})
        slot["n"] += 1
        slot["correct"] += 1 if r["outcome"] == "correct" else 0
    for slot in buckets.values():
        slot["hit_rate"] = round(slot["correct"] / slot["n"], 3)
    return {
        "buckets": dict(sorted(buckets.items())),
        "n_resolved": len(rows),
        "n_excluded_ambiguous": excluded,
        "filters": {
            "strategy_name": strategy_name,
            "regime_tag": regime_tag,
            "timescale": timescale,
        },
    }


def strategy_stats(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT name, status, timescale, mechanism_family, parent_strategy,
                  n_resolved, n_correct, n_wrong, n_ambiguous, last_resolved_at,
                  created_at, retired_at, retirement_reason
           FROM strategies WHERE name=?""", (name,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    decided = out["n_correct"] + out["n_wrong"]
    out["win_rate"] = round(out["n_correct"] / decided, 3) if decided else None
    return out


def strategy_book(conn: sqlite3.Connection, statuses: tuple = ("test", "active", "dormant")) -> list:
    marks = ",".join("?" * len(statuses))
    rows = _rows(conn.execute(
        f"""SELECT name, status, timescale, mechanism_family, parent_strategy,
                   n_resolved, n_correct, n_wrong
            FROM strategies WHERE status IN ({marks})
            ORDER BY status, name""", statuses))
    for r in rows:
        decided = r["n_correct"] + r["n_wrong"]
        r["win_rate"] = round(r["n_correct"] / decided, 3) if decided else None
    return rows


def support_score_performance(
    conn: sqlite3.Connection, strategy_name: Optional[str] = None
) -> list:
    """Per-data-point predictiveness: avg score on correct vs wrong outcomes.

    Reflect's raw material — which data points, at what support levels, led
    to correct calls (narrative DPs included via their recorded scores).
    """
    where = "p.resolved_at IS NOT NULL AND p.outcome IN ('correct','wrong')"
    args: list = []
    if strategy_name is not None:
        where += " AND p.strategy_name = ?"
        args.append(strategy_name)
    return _rows(conn.execute(
        f"""SELECT s.data_point, s.kind,
                   COUNT(*) AS n,
                   AVG(CASE WHEN p.outcome='correct' THEN s.score END) AS avg_score_correct,
                   AVG(CASE WHEN p.outcome='wrong' THEN s.score END) AS avg_score_wrong
            FROM support_scores s
            JOIN predictions p ON p.id = s.prediction_id
            WHERE {where}
            GROUP BY s.data_point, s.kind
            ORDER BY n DESC""", args))


def last_action_runs(conn: sqlite3.Connection) -> dict:
    """Latest SUCCESSFUL run per action type — the staleness watchdog's
    source. ok=0 rows are history, not floor satisfaction: a failed
    perception run must not silence the perception floor."""
    rows = conn.execute(
        """SELECT action_type, MAX(ts) AS last_ts
           FROM action_runs WHERE ok = 1 GROUP BY action_type""").fetchall()
    return {r["action_type"]: r["last_ts"] for r in rows}


def timescale_mix(conn: sqlite3.Connection, since_ts: float) -> dict:
    """Prediction counts per timescale since a moment — quota enforcement."""
    rows = conn.execute(
        """SELECT timescale, COUNT(*) AS n FROM predictions
           WHERE ts >= ? GROUP BY timescale""", (since_ts,)).fetchall()
    return {r["timescale"]: r["n"] for r in rows}


def sizing_performance(conn: sqlite3.Connection) -> list:
    """Closed-position performance per conviction band (floored to 0.1 — the
    sizing dial's input), with the leverage actually realized at entry —
    reflect's evidence for retuning the conviction→leverage bands. Rows with
    no opening-decision conviction group under NULL; shown, never dropped."""
    return _rows(conn.execute(
        """SELECT CAST(d.conviction * 10 AS INT) / 10.0 AS conviction_band,
                  COUNT(*) AS n,
                  SUM(CASE WHEN o.realized_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
                  ROUND(AVG(p.leverage), 2) AS avg_leverage,
                  ROUND(MAX(p.leverage), 2) AS max_leverage,
                  ROUND(SUM(o.realized_pnl_usd), 4) AS sum_pnl_usd,
                  ROUND(AVG(o.r_multiple), 3) AS avg_r_multiple,
                  ROUND(MIN(o.r_multiple), 3) AS worst_r,
                  ROUND(AVG(o.mae_pct), 3) AS avg_mae_pct
           FROM positions p
           JOIN outcomes o ON o.position_id = p.id
           LEFT JOIN trades tr ON tr.id = p.opening_trade_id
           LEFT JOIN decisions d ON d.id = tr.decision_id
           WHERE p.status='closed'
           GROUP BY conviction_band ORDER BY conviction_band"""))
