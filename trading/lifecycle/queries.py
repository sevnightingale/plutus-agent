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


def _percentile(sorted_vals: list, q: float):
    """Nearest-rank percentile of a pre-sorted list (no numpy)."""
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return round(sorted_vals[idx], 4)


def _median(vals: list):
    """Median of an unsorted list (no numpy). None on empty."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def strategy_rr(conn: sqlite3.Connection, name: str):
    """Reward:risk among a strategy's WINS — the graduation trade-worthiness gate.

    ``median(mfe) / median(|mae|)`` over the strategy's resolved CORRECT
    price-zone predictions: the favorable excursion the strategy produces vs the
    adverse excursion the stop must survive. RR > 1 means that when the strategy
    is right, the move pays more than the stop distance we'd actually risk — so
    ``win_rate × RR`` is positive expectancy. Ratio of medians (not mean of
    per-prediction ratios) so a single near-zero MAE can't blow it up. ``None``
    until there's a win with path stats (or, in the degenerate case, a zero
    median adverse move) — never fabricated."""
    rows = conn.execute(
        """SELECT realized_value_json AS rv FROM predictions
           WHERE strategy_name=? AND outcome='correct'
             AND resolved_at IS NOT NULL AND realized_value_json IS NOT NULL""",
        (name,)).fetchall()
    mfes, maes = [], []
    for r in rows:
        try:
            d = json.loads(r["rv"])
        except Exception:
            continue
        if d.get("mfe_pct") is not None:
            mfes.append(abs(float(d["mfe_pct"])))
        if d.get("mae_pct") is not None:
            maes.append(abs(float(d["mae_pct"])))
    med_mfe, med_mae = _median(mfes), _median(maes)
    if med_mfe is None or not med_mae:
        return None
    return round(med_mfe / med_mae, 2)


def open_predictions(conn: sqlite3.Connection, limit: int = 50) -> list:
    return _rows(conn.execute(
        """SELECT id, ts, horizon_ts, timescale, symbol, claim_md, conviction,
                  strategy_name, kind, regime_tag, risk_tolerance
           FROM predictions WHERE resolved_at IS NULL
           ORDER BY horizon_ts ASC LIMIT ?""", (limit,)))


def open_slot_counts(conn: sqlite3.Connection) -> dict:
    """Open predictions by timescale + strategy — population at a glance.

    Returned by register_prediction so predict sees the live counts as it
    registers. The population is governed by per-(timescale × regime) strategy
    cell caps (see ``strategies_by_timescale``) plus the per-strategy open cap,
    NOT a global prediction budget — prediction volume is deliberately cheap.
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


def strategies_by_timescale(
    conn: sqlite3.Connection,
    timescale: str,
    statuses: tuple = ("test", "active"),
) -> list:
    """Strategies at a timescale, with their regime cells + counters — the
    population-visibility query behind the per-(timescale × regime) cell cap
    that predict/reflect enforce as a reasoning guardrail (a strategy is
    applicable in the SET of regime labels it declares)."""
    marks = ",".join("?" * len(statuses))
    rows = _rows(conn.execute(
        f"""SELECT name, status, timescale, mechanism_family,
                   regime_applicability_json, n_resolved, n_correct, n_wrong
            FROM strategies WHERE timescale = ? AND status IN ({marks})
            ORDER BY status, name""", [timescale, *statuses]))
    for r in rows:
        decided = r["n_correct"] + r["n_wrong"]
        r["win_rate"] = round(r["n_correct"] / decided, 3) if decided else None
        r["rr"] = strategy_rr(conn, r["name"])
        raw = r.pop("regime_applicability_json", None)
        r["regime_applicability"] = json.loads(raw) if raw else {}
    return rows


def open_predictions_by_cell(conn: sqlite3.Connection) -> list:
    """Open prediction counts per (timescale, regime_tag) — cell occupancy
    predict reads alongside the strategy population."""
    rows = conn.execute(
        """SELECT timescale, regime_tag, COUNT(*) AS n FROM predictions
           WHERE resolved_at IS NULL
           GROUP BY timescale, regime_tag ORDER BY timescale, regime_tag""").fetchall()
    return [{"timescale": r["timescale"], "regime_tag": r["regime_tag"], "n": r["n"]}
            for r in rows]


def predictions_due_for_rescore(
    conn: sqlite3.Connection, now: Optional[float] = None
) -> list:
    """Open predictions whose last conviction re-score is older than their
    timescale's cadence — the conviction-trajectory schedule ops reads each
    tick. intraday 30m · swing 4h · position 1d. Strategyless predictions
    (stress/adhoc) have no conviction model and are excluded.
    """
    now = now if now is not None else time.time()
    cadence = {"intraday": 1800.0, "swing": 14400.0, "position": 86400.0}
    rows = _rows(conn.execute(
        """SELECT p.id, p.strategy_name, p.timescale, p.regime_tag,
                  (SELECT MAX(e.ts) FROM prediction_evaluations e
                   WHERE e.prediction_id = p.id) AS last_eval_ts
           FROM predictions p
           WHERE p.resolved_at IS NULL AND p.strategy_name IS NOT NULL"""))
    due = []
    for r in rows:
        interval = cadence.get(r["timescale"], 1800.0)
        last = r["last_eval_ts"]
        if last is None or (now - float(last)) >= interval:
            due.append(r)
    return due


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
                  claim_md, entry_ref_price, near_edge_pct, far_edge_pct,
                  reached_near_at, reached_far_at,
                  success_criteria_json, failure_criteria_json,
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


def mae_envelope(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    timescale: Optional[str] = None,
    regime_tag: Optional[str] = None,
    percentile: float = 0.8,
) -> dict:
    """Empirical adverse-excursion envelope for STOP placement.

    The magnitude of MAE among CORRECT outcomes — how far a *winning* setup
    typically retraced before reaching its target. The trade agent sets the SL
    just beyond a high percentile so a typical winner isn't stopped out, then
    floors/caps with the venue. Replaces the old ATR-times-a-vibe guess with
    evidence specific to the strategy/timescale/regime.

    Two sources, same filter: resolved price-zone predictions
    (``realized_value_json.mae_pct``) and closed winning positions
    (``outcomes.mae_pct``). All magnitudes are positive %; ``suggested_sl_pct``
    is the percentile (None until evidence exists — never fabricated)."""
    pred_where = ["p.resolved_at IS NOT NULL", "p.outcome='correct'",
                  "p.realized_value_json IS NOT NULL"]
    pos_where = ["po.status='closed'", "o.realized_pnl_usd > 0", "o.mae_pct IS NOT NULL"]
    pred_args: list = []
    pos_args: list = []
    for col, val in (("strategy_name", strategy_name), ("timescale", timescale),
                     ("regime_tag", regime_tag)):
        if val is not None:
            pred_where.append(f"p.{col}=?")
            pred_args.append(val)
            pos_where.append(f"pr.{col}=?")
            pos_args.append(val)

    mags: list = []
    n_pred = 0
    for r in conn.execute(
            f"SELECT realized_value_json AS rv FROM predictions p "
            f"WHERE {' AND '.join(pred_where)}", pred_args).fetchall():
        try:
            m = json.loads(r["rv"]).get("mae_pct")
        except Exception:
            m = None
        if m is not None:
            mags.append(abs(float(m)))
            n_pred += 1

    n_pos = 0
    for r in conn.execute(
            f"""SELECT o.mae_pct AS m FROM positions po
                JOIN outcomes o ON o.position_id = po.id
                LEFT JOIN trades tr ON tr.id = po.opening_trade_id
                LEFT JOIN decisions d ON d.id = tr.decision_id
                LEFT JOIN theses t ON t.id = d.thesis_id
                LEFT JOIN predictions pr ON pr.id = t.prediction_id
                WHERE {' AND '.join(pos_where)}""", pos_args).fetchall():
        if r["m"] is not None:
            mags.append(abs(float(r["m"])))
            n_pos += 1

    mags.sort()
    return {
        "n": len(mags), "n_predictions": n_pred, "n_positions": n_pos,
        "percentile": percentile,
        "suggested_sl_pct": _percentile(mags, percentile),
        "p50_mae_pct": _percentile(mags, 0.5),
        "max_mae_pct": round(mags[-1], 4) if mags else None,
        "filters": {"strategy_name": strategy_name, "timescale": timescale,
                    "regime_tag": regime_tag},
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
    out["rr"] = strategy_rr(conn, name)
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
        r["rr"] = strategy_rr(conn, r["name"])
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


def sizing_performance(conn: sqlite3.Connection, fix_ts: Optional[float] = None) -> list:
    """Closed-position performance per conviction band (floored to 0.1 — the
    sizing dial's input), with the leverage actually realized at entry —
    reflect's evidence for retuning the conviction→leverage bands. Rows with
    no opening-decision conviction group under NULL; shown, never dropped.

    ``fix_ts`` (epoch seconds, Issue 4 cutover) excludes positions whose opening
    decision predates the conviction-render fix — those convictions were scored
    on a byte-truncated (blinded) reading substrate and would pollute the sizing
    review. None (the default) includes all history. Decision-less positions
    (NULL d.ts) are always kept, matching the NULL-band-shown contract."""
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
                 AND (:fix_ts IS NULL OR d.ts IS NULL OR d.ts >= :fix_ts)
           GROUP BY conviction_band ORDER BY conviction_band""",
        {"fix_ts": fix_ts}))
