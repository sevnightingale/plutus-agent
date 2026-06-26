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


# --- Profitability gate (graduation + entry), one model two scales -----------
# The mechanical trade geometry the gate simulates. Provisional — Reflect governs
# per strategy in the geometry layer (Push B). See
# PLANNING-trade-execution-collapse.md.
HARD_SL_PERCENTILE = 0.75   # all-resolutions MAE percentile = the hard stop
HARD_SL_MIN_N = 6           # below this, the stop can't be trusted → ATR fallback
GRADUATION_MIN_N = 15       # resolved predictions required to graduate
ACTIONABLE_MAX_AGE_S = 1200.0  # 20 min — NEVER fund a prediction older than this
                               # (entry conditions drift; only a fresh beat trades)


def hard_stop_pct(
    conn: sqlite3.Connection, strategy_name: str, *,
    timescale: Optional[str] = None, regime_tag: Optional[str] = None,
):
    """The mechanical SL distance (% from entry) for a strategy — all-resolutions
    MAE at ``HARD_SL_PERCENTILE`` (catches losers, spares typical winners). None
    when too thin (``< HARD_SL_MIN_N``); the live tool then falls back to ATR."""
    return mae_envelope(
        conn, strategy_name=strategy_name, timescale=timescale,
        regime_tag=regime_tag, population="all_resolutions",
        statistic="percentile", percentile=HARD_SL_PERCENTILE,
        min_n=HARD_SL_MIN_N,
    )["suggested_sl_pct"]


def strategy_expectancy(
    conn: sqlite3.Connection, strategy_name: str, *,
    cost_margin: float = 0.0, min_n: int = GRADUATION_MIN_N,
) -> dict:
    """Simulated net expectancy — the profitability gate (graduation + entry).

    Runs the strategy's resolved price-zone predictions through the mechanical
    trade geometry (TP = far edge, SL = ``hard_stop_pct``) and returns mean PnL
    per trade. The win signal is ``reached_far_at`` (the trade actually tagged
    TP) — a floor/horizon 'correct' that never reached far is NOT a trade win.
    Pessimistic on path-dependence: a path that tagged BOTH target and stop
    counts as a loss (intrabar ordering is unrecoverable). Conviction-independent
    (pure outcome geometry), so the conviction-render cutover doesn't touch it.

    Replaces the survivorship-biased ``strategy_rr`` as the graduation gate
    (rr measured median MFE/MAE on WINNERS only; this runs the whole book through
    the actual rules). ``tradeable`` iff ``expectancy > cost_margin`` AND
    ``n >= min_n`` AND the stop is estimable."""
    stop = hard_stop_pct(conn, strategy_name)
    rows = conn.execute(
        """SELECT near_edge_pct, far_edge_pct, reached_near_at, reached_far_at,
                  realized_value_json AS rv
           FROM predictions WHERE strategy_name=? AND resolved_at IS NOT NULL
             AND realized_value_json IS NOT NULL""", (strategy_name,)).fetchall()

    def _sim(edge_col, reached_col):
        """Run the book through one exit target (far edge or near edge)."""
        wins = losses = scratch = 0
        win_pnls: list = []
        sum_pnl = 0.0
        n = 0
        if not stop:
            return None
        for r in rows:
            try:
                mae = abs(float(json.loads(r["rv"]).get("mae_pct")))
            except Exception:
                continue
            edge = r[edge_col]
            if edge is None:
                continue
            reward = abs(float(edge))
            reached = r[reached_col] is not None
            hit_sl = mae >= stop
            n += 1
            if reached and not hit_sl:          # tagged the target, never stopped → win
                wins += 1
                win_pnls.append(reward)
                sum_pnl += reward
            elif hit_sl:                        # stopped (incl. pessimistic path-dep)
                losses += 1
                sum_pnl -= stop
            else:                               # neither — rode to horizon ~flat
                scratch += 1
        decided = wins + losses
        return {
            "n": n, "wins": wins, "losses": losses, "scratch": scratch,
            "win_rate": round(wins / decided, 3) if decided else None,
            "avg_win_pct": round(sum(win_pnls) / len(win_pnls), 4) if win_pnls else None,
            "expectancy_pct": round(sum_pnl / n, 4) if n else None,
        }

    # Two exit targets — TP at the far edge, or take-profit at the near edge (the
    # alert-up exit). The strategy's geometry favours whichever has higher
    # expectancy; graduate on the BEST (a high-win-rate / near-reaching strategy
    # can be profitable on near even when far never pays).
    far = _sim("far_edge_pct", "reached_far_at")
    near = _sim("near_edge_pct", "reached_near_at")
    fe = far["expectancy_pct"] if far else None
    ne = near["expectancy_pct"] if near else None
    if fe is None and ne is None:
        best, best_target = None, None
    elif ne is None or (fe is not None and fe >= ne):
        best, best_target = far, "far"
    else:
        best, best_target = near, "near"
    expectancy = best["expectancy_pct"] if best else None
    n = best["n"] if best else 0
    return {
        "strategy_name": strategy_name,
        "best_target": best_target,
        "n": n,
        "wins": best["wins"] if best else 0,
        "losses": best["losses"] if best else 0,
        "scratch": best["scratch"] if best else 0,
        "win_rate": best["win_rate"] if best else None,
        "avg_win_pct": best["avg_win_pct"] if best else None,
        "stop_pct": stop,
        "expectancy_pct": expectancy,
        "expectancy_far": fe,
        "expectancy_near": ne,
        "tradeable": bool(stop) and expectancy is not None
        and expectancy > cost_margin and n >= min_n,
    }


def best_actionable_prediction(
    conn: sqlite3.Connection, *,
    max_age_s: float = ACTIONABLE_MAX_AGE_S, now: Optional[float] = None,
) -> Optional[dict]:
    """The single best fundable prediction right now — main's mechanical
    selection (replaces predict's prose argmax; no handoff payload to drop).

    THREE gates. RECENCY gate: the prediction must be younger than ``max_age_s``
    (default 20 min) — a prediction made hours ago is never funded, its entry
    conditions have drifted; only a fresh beat trades. STRATEGY gate: the strategy
    must currently clear the expectancy bar (``strategy_expectancy(...).tradeable``)
    — funding is tied to live profitability, and this blocks cherry-picking a
    wide-far prediction on a net-negative book. SETUP gate: among the survivors,
    EV = p·reward − (1−p)·stop > 0 (RR > (1−p)/p). Returns the argmax-EV one
    (tiebreak: earliest). None when nothing qualifies — zero active/tradeable
    strategies or nothing fresh — so the desk correctly stays idle. The live tool
    re-checks EV against the actual fill price; this only picks the candidate id."""
    now = now if now is not None else time.time()
    cutoff = now - max_age_s
    rows = _rows(conn.execute(
        """SELECT pr.id, pr.strategy_name, pr.symbol, pr.conviction,
                  pr.near_edge_pct, pr.far_edge_pct, pr.timescale, pr.regime_tag,
                  pr.ts
           FROM predictions pr
           JOIN strategies s ON s.name = pr.strategy_name
           WHERE pr.resolved_at IS NULL AND s.status = 'active' AND pr.ts >= ?
           ORDER BY pr.ts ASC""", (cutoff,)))
    best = None
    cache: dict = {}
    for r in rows:
        name = r["strategy_name"]
        if name not in cache:
            cache[name] = strategy_expectancy(conn, name)
        stats = cache[name]
        if not stats["tradeable"]:          # strategy gate: live profitability
            continue
        p, stop = stats["win_rate"], stats["stop_pct"]
        if not p or not stop:
            continue
        # Reward = the edge of the strategy's BEST exit target (near vs far).
        target = stats["best_target"]
        edge = r["near_edge_pct"] if target == "near" else r["far_edge_pct"]
        reward = abs(float(edge))
        ev = p * reward - (1.0 - p) * stop
        if ev <= 0:
            continue
        cand = {**r, "ev_pct": round(ev, 4), "win_rate": p, "stop_pct": stop,
                "reward_pct": reward, "target": target}
        if best is None or ev > best["ev_pct"]:
            best = cand
    return best


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


# MAE populations — which outcomes' adverse excursions inform a stop.
#   winners                 outcome='correct' preds + profitable positions
#                           (legacy default; the "don't get shaken out of a
#                            winner" envelope, kept for visibility / strategy_rr)
#   all_resolutions         every resolved pred (correct+wrong) + every closed
#                           position — catches losers; the HARD SL population
#   reached_target_winners  preds that tagged the far edge + profitable positions
#                           — trade-success (not floor/horizon "wins"); the
#                            alert-down population (Push B)
_MAE_PRED_WHERE = {
    "winners": "p.outcome='correct'",
    "all_resolutions": "p.outcome IN ('correct','wrong')",
    "reached_target_winners": "p.reached_far_at IS NOT NULL",
}
_MAE_POS_WHERE = {
    "winners": "o.realized_pnl_usd > 0",
    "all_resolutions": "1=1",
    "reached_target_winners": "o.realized_pnl_usd > 0",
}


def mae_envelope(
    conn: sqlite3.Connection,
    *,
    strategy_name: Optional[str] = None,
    timescale: Optional[str] = None,
    regime_tag: Optional[str] = None,
    percentile: float = 0.8,
    population: str = "winners",
    statistic: str = "percentile",
    median_multiplier: float = 3.0,
    min_n: int = 0,
) -> dict:
    """Empirical adverse-excursion envelope for STOP placement.

    Two sources, one filter: resolved price-zone predictions
    (``realized_value_json.mae_pct``) and closed positions (``outcomes.mae_pct``).
    All magnitudes are positive %; ``suggested_sl_pct`` is None until evidence
    exists — never fabricated.

    ``population`` selects whose adverse excursions count (see ``_MAE_PRED_WHERE``):
    ``winners`` (legacy default), ``all_resolutions`` (the hard SL — catches
    losers), or ``reached_target_winners`` (the alert-down level).

    ``statistic`` is ``percentile`` (nearest-rank at ``percentile``) or
    ``median_anchored`` (``median_multiplier × p50`` — robust on fat-tailed,
    low-n samples where a raw high percentile is just "the second-highest
    sample"). ``min_n``: below it, ``suggested_sl_pct`` is None so the caller
    falls back (e.g. ATR) rather than trusting a thin estimate."""
    if population not in _MAE_PRED_WHERE:
        raise ValueError(f"unknown MAE population {population!r}")
    pred_where = ["p.resolved_at IS NOT NULL", "p.realized_value_json IS NOT NULL",
                  _MAE_PRED_WHERE[population]]
    pos_where = ["po.status='closed'", "o.mae_pct IS NOT NULL",
                 _MAE_POS_WHERE[population]]
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
    n = len(mags)
    if n < min_n:
        suggested = None
    elif statistic == "median_anchored":
        med = _median(mags)
        suggested = round(median_multiplier * med, 4) if med is not None else None
    elif statistic == "percentile":
        suggested = _percentile(mags, percentile)
    else:
        raise ValueError(f"unknown MAE statistic {statistic!r}")
    return {
        "n": n, "n_predictions": n_pred, "n_positions": n_pos,
        "population": population, "statistic": statistic,
        "percentile": percentile, "median_multiplier": median_multiplier,
        "min_n": min_n,
        "suggested_sl_pct": suggested,
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
    out["rr"] = strategy_rr(conn, name)  # legacy visibility; NOT the gate
    exp = strategy_expectancy(conn, name)
    out["expectancy_pct"] = exp["expectancy_pct"]  # the gate
    out["tradeable"] = exp["tradeable"]
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
        r["rr"] = strategy_rr(conn, r["name"])  # legacy visibility; NOT the gate
        exp = strategy_expectancy(conn, r["name"])
        r["expectancy_pct"] = exp["expectancy_pct"]  # the gate
        r["tradeable"] = exp["tradeable"]
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
    reflect's evidence for retuning the conviction→RISK-BUDGET bands (realized
    risk per trade ≈ leverage × stop-distance ≈ the R-multiples reported here).
    Rows with no opening-decision conviction group under NULL; shown, never dropped.

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
