"""Named read surface over lifecycle.db v2.

Compact, prompt-friendly dicts (no embeddings, no blobs). These functions are
the substance behind the desk's read toolset AND the spawn mechanism's
``lifecycle:<name>`` context blocks — one implementation, two consumers.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from typing import Optional


def _rows(cur) -> list:
    return [dict(r) for r in cur.fetchall()]


def pilot_armed() -> bool:
    """Operator pilot mandate (Sev, 2026-08-22): while ``~/.plutus-agent/PILOT``
    exists, a TEST-book prediction above the global conviction threshold may
    fund — graduation gates the evidence-backed lane, the pilot gates
    existence. Armed by touching the file, disarmed by removing it (the HALT
    pattern, inverted). SINGLE OWNER of the sentinel probe: the funding gate,
    the selection lane, the fundable-window wake, and the status surfaces all
    call this — a second inline ``.exists()`` is the drift this function
    exists to prevent."""
    from harness.constants import get_hermes_home
    return (get_hermes_home() / "PILOT").exists()


def strategy_fundable(status: Optional[str], *, pilot: Optional[bool] = None) -> bool:
    """The fundability predicate every funded-trade surface shares: ACTIVE
    always; TEST under an armed pilot; nothing else, ever (retired books do
    not fund). Pass ``pilot`` when the caller already probed the sentinel."""
    if status == "active":
        return True
    armed = pilot_armed() if pilot is None else pilot
    return bool(armed) and status == "test"


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
HAZARD_WINDOW_N = 10        # trailing resolutions the decay check reads (one
                            # reflect-checkpoint's worth; < GRADUATION_MIN_N so
                            # every graduated book has a full window)
SERIOUS_TRIAL_MIN_N = HARD_SL_MIN_N  # a sibling counts toward multiplicity only
                            # once its book reaches this many resolutions — a
                            # one-resolution noise book is not an independent
                            # trial and must not raise the bar for leaders
CELL_MIN_N = 4              # resolutions a regime cell needs before its
                            # expectancy is evidence rather than noise. Below
                            # it the cell is reported but never judged on.
CELL_OCCUPANCY_CAP = 7      # test+active strategies admitted per regime cell.
                            # Admission control on generation, and the reason
                            # the multiplicity premium can no longer run away:
                            # M is now cell-scoped, so this bounds it by
                            # construction. Retired frees the slot AND leaves
                            # M — withdrawn is withdrawn. Generate reads the
                            # retired file so it does not re-author the same
                            # loser.
ACTIONABLE_MAX_AGE_S = 1200.0  # 20 min — NEVER fund a prediction older than this
                               # (entry conditions drift; only a fresh beat trades)
BASE_PREDICTION_OPEN_CAP = 3
INCUBATION_PREDICTION_OPEN_CAP = 5


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


# Rough round-trip execution cost, % of notional — the expectancy gate's
# default cost margin and the EV gates' haircut. Composition (BTC, base
# fee tier): taker 0.045% × 2 legs = 0.090%, plus marketable-limit slippage
# ≈ 0.03% × 2 legs = 0.060% (observed ~3-4 bp/leg). A strategy must clear
# this to be tradeable — paper expectancy below the round-trip cost is a
# slow bleed, not an edge. Rough by design; refine from realized fills once
# outcomes track fees.
ESTIMATED_ROUND_TRIP_COST_PCT = 0.15


def strategy_expectancy(
    conn: sqlite3.Connection, strategy_name: str, *,
    cost_margin: float = ESTIMATED_ROUND_TRIP_COST_PCT,
    min_n: int = GRADUATION_MIN_N,
    regime_tag: Optional[str] = None,
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
    the actual rules).

    Two trading-design imports harden the bar:

    MULTIPLICITY (was this the survivor of how many trials?): a positive book
    means little if it is the best of thirty siblings — with enough test
    strategies, some clear any fixed bar by luck. The hurdle is deflated by the
    expected best-of-M selection premium under the null,
    ``sqrt(2·ln(M)) · σ/√n`` (σ = per-trade simulated-PnL stdev, M =
    ``siblings_tried`` — SERIOUS trials at this timescale: strategies in any
    status EXCEPT ``retired`` whose book reached ``SERIOUS_TRIAL_MIN_N``
    resolutions; a one-resolution noise book was never an independent trial
    and does not raise the bar. Retired books were counted until 2026-07-27,
    on the reasoning that a trial cannot be un-tried; the price was a bar that
    only rose, and a gate that rises forever eventually forbids everything.
    Dormant — parked, still on the bar, never woken — was abolished
    2026-08-13: a withdrawn book is retired, and generate reads it so the
    desk does not re-author the same loser. Only test+active raise M).
    A lone strategy (M=1) pays zero premium — the original bar. ``n_to_clear``
    reports the book size at which the current exp/σ/M clears the hurdle
    (None = never at this expectancy: the edge is at/below cost).

    HAZARD (was this real? ≠ is it still?): a whole-book expectancy lets a dead
    edge coast on its historical wins. The trailing ``HAZARD_WINDOW_N``
    resolutions are re-simulated under the same geometry; a full window with
    negative expectancy sets ``decaying`` and blocks ``tradeable`` — funding
    stops before the lifetime average catches up. Decay never rewrites the
    book: the lifetime record stands.

    ``tradeable`` iff ``expectancy > hurdle`` (cost margin + multiplicity
    premium) AND ``n >= min_n`` AND the stop is estimable AND not ``decaying``."""
    stop = hard_stop_pct(conn, strategy_name)
    # ``regime_tag`` restricts the book to ONE regime cell. A blended book
    # averages conditions the strategy never trades together, and the average
    # describes none of them: ema20-pivot-swing measured -0.004 lifetime while
    # four of its five cells were positive and one (trending-up/compressed,
    # -0.429) dragged the whole thing under. Judging it on the blend was about
    # to retire a working mechanism. The stop stays lifetime-derived on
    # purpose — it is a property of the strategy's geometry, not of the cell,
    # and re-deriving it per cell on 8-15 rows would be noise.
    rows = conn.execute(
        f"""SELECT near_edge_pct, far_edge_pct, reached_near_at, reached_far_at,
                  realized_value_json AS rv
           FROM predictions WHERE strategy_name=? AND resolved_at IS NOT NULL
             AND realized_value_json IS NOT NULL
             {'AND regime_tag = ?' if regime_tag else ''}
           ORDER BY resolved_at, id""",
        (strategy_name, regime_tag) if regime_tag else (strategy_name,)).fetchall()

    def _sim(book, edge_col, reached_col):
        """Run a book of rows through one exit target (far edge or near edge)."""
        wins = losses = scratch = 0
        win_pnls: list = []
        pnls: list = []
        sum_pnl = 0.0
        n = 0
        if not stop:
            return None
        for r in book:
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
                pnls.append(reward)
            elif hit_sl:                        # stopped (incl. pessimistic path-dep)
                losses += 1
                sum_pnl -= stop
                pnls.append(-stop)
            else:                               # neither — rode to horizon ~flat
                scratch += 1
                pnls.append(0.0)
        decided = wins + losses
        mean = sum_pnl / n if n else None
        stdev = (math.sqrt(sum((x - mean) ** 2 for x in pnls) / (n - 1))
                 if n >= 2 else None)
        return {
            "n": n, "wins": wins, "losses": losses, "scratch": scratch,
            "win_rate": round(wins / decided, 3) if decided else None,
            "avg_win_pct": round(sum(win_pnls) / len(win_pnls), 4) if win_pnls else None,
            "expectancy_pct": round(mean, 4) if n else None,
            "pnl_stdev_pct": round(stdev, 4) if stdev is not None else None,
        }

    # Two exit targets — TP at the far edge, or take-profit at the near edge (the
    # alert-up exit). The strategy's geometry favours whichever has higher
    # expectancy; graduate on the BEST (a high-win-rate / near-reaching strategy
    # can be profitable on near even when far never pays).
    far = _sim(rows, "far_edge_pct", "reached_far_at")
    near = _sim(rows, "near_edge_pct", "reached_near_at")
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

    # Multiplicity: count SERIOUS sibling trials at this timescale — books of
    # at least SERIOUS_TRIAL_MIN_N resolutions (evidence filter matches the
    # book query above). None when the strategy row is missing — visible,
    # never guessed.
    #
    # RETIRED books are excluded (2026-07-27). They were counted until now, on
    # the reasoning that a trial cannot be un-tried — statistically the purer
    # position. The cost of that purity was a bar that could only ever rise:
    # measured on this desk, 81-94% of every hurdle was premium rather than
    # trading cost, and no strategy had ever graduated. A gate that rises
    # monotonically forever eventually forbids everything, which is a design
    # failure whatever its statistics.
    #
    # Excluding retired makes the bar responsive to cleaning the book.
    # Dormant was the attempted middle (parked, still on the bar) and
    # became a one-way tax: nothing ever woke it. Abolished 2026-08-13.
    #
    # SCOPE: the strategy's own REGIME CELL, not its whole timescale
    # (2026-07-27). The premium prices a best-of-M selection, and the
    # selection that actually happens is among the strategies declaring the
    # cell the tape is in — a strategy in trending-up/normal is not an
    # alternative to one in ranging/compressed and cannot be chosen instead of
    # it, so charging the winner for a competition that never occurred is
    # over-conservative. Measured: cells hold 3-6 serious trials against 13-22
    # per timescale, roughly halving the resolutions needed to graduate.
    #
    # Cell scope was considered and rejected on 2026-07-07 for a reason that
    # has since been removed: `regime_applicability` was self-declared and
    # set-valued, so a strategy could narrow its declared regime to shrink its
    # own M. Since 2026-07-27 `strategy_upsert` refuses a set-valued
    # declaration and the per-cell cap bounds occupancy, so M is bounded by
    # construction and cannot be narrowed into. The residual vector — authoring
    # into a sparsely populated cell — is self-limiting, because a sparse cell
    # is usually sparse for being rarely lit, and a book that cannot accrue
    # cannot graduate.
    #
    # A legacy multi-cell declaration counts toward EVERY cell it declares: it
    # genuinely competes in all of them. A strategy whose cell cannot be read
    # falls back to timescale scope, which is the conservative direction.
    srow = conn.execute(
        "SELECT symbol, timescale, regime_applicability_json "
        "FROM strategies WHERE name=?",
        (strategy_name,)).fetchone()
    siblings = None
    if srow:
        own = strategy_cells(srow["timescale"], srow["regime_applicability_json"])
        # BUCKET SCOPE (2026-08-08): siblings are serious trials in the same
        # CORRELATION BUCKET at this timescale — a BTC book and an ETH book
        # are alternatives in the same selection; a gold book is not chosen
        # instead of either and does not inherit crypto's premium.
        bucket = sorted(bucket_of(srow["symbol"]))
        marks_b = ",".join("?" * len(bucket))
        rows_s = conn.execute(
            f"""SELECT s.name, s.timescale, s.regime_applicability_json ra
               FROM strategies s
               WHERE s.timescale = ?
                 AND s.symbol IN ({marks_b})
                 AND s.status != 'retired'
                 AND (SELECT COUNT(*) FROM predictions p
                      WHERE p.strategy_name = s.name
                        AND p.resolved_at IS NOT NULL
                        AND p.realized_value_json IS NOT NULL) >= ?""",
            (srow["timescale"], *bucket, SERIOUS_TRIAL_MIN_N)).fetchall()
        if own:
            siblings = max(1, sum(
                1 for s in rows_s
                if strategy_cells(s["timescale"], s["ra"]) & own))
        else:                       # unreadable cell → conservative fallback
            siblings = max(1, len(rows_s))
    sigma = best["pnl_stdev_pct"] if best else None
    premium = (math.sqrt(2.0 * math.log(siblings)) * sigma / math.sqrt(n)
               if siblings and sigma is not None and n else None)
    hurdle = cost_margin + (premium or 0.0)

    # Hazard: re-simulate the trailing window under the SAME target the
    # lifetime book graduates on. A short book (< window) can't decay yet.
    recent = None
    if best_target:
        edge_col, reached_col = (("far_edge_pct", "reached_far_at")
                                 if best_target == "far"
                                 else ("near_edge_pct", "reached_near_at"))
        recent = _sim(rows[-HAZARD_WINDOW_N:], edge_col, reached_col)
        if recent:
            recent.pop("pnl_stdev_pct", None)
            recent.pop("avg_win_pct", None)
    decaying = bool(recent and recent["n"] >= HAZARD_WINDOW_N
                    and recent["expectancy_pct"] is not None
                    and recent["expectancy_pct"] < 0)

    # Path-to-clear (operator legibility): the book size at which the CURRENT
    # exp/σ/M clears the hurdle. The premium shrinks as the strategy's own √n
    # grows, so any real edge above the cost margin converges — while an edge
    # at or below cost NEVER clears (None): that is a structural problem
    # (scratch rate, geometry), not a patience problem. Assumes exp/σ/M hold.
    n_to_clear = None
    if (expectancy is not None and sigma is not None and siblings
            and expectancy > cost_margin):
        need = 2.0 * math.log(siblings) * (sigma / (expectancy - cost_margin)) ** 2
        n_to_clear = max(min_n, math.floor(need) + 1)   # strictly above the bar

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
        "cost_margin_pct": cost_margin,
        "pnl_stdev_pct": sigma,
        "siblings_tried": siblings,
        "multiplicity_premium_pct": round(premium, 4) if premium is not None else None,
        "hurdle_pct": round(hurdle, 4),
        "n_to_clear": n_to_clear,
        "recent": recent,
        "decaying": decaying,
        "tradeable": bool(stop) and expectancy is not None
        and expectancy > hurdle and n >= min_n and not decaying,
    }


def strategy_prediction_capacity(
    conn: sqlite3.Connection,
    strategy_name: str,
    *,
    open_count: Optional[int] = None,
) -> dict:
    """Return the effective undecided-prediction capacity for one strategy.

    The base lane stays deliberately narrow because simultaneous predictions
    from one strategy are correlated trials. A non-decaying book that is net
    positive above costs but has not yet cleared the graduation bar gets the
    existing incubation fast lane. Win-locked predictions do not consume a
    slot because their outcome is already decided.
    """
    if open_count is None:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM predictions "
            "WHERE strategy_name = ? AND resolved_at IS NULL "
            "AND reached_near_at IS NULL",
            (strategy_name,),
        ).fetchone()[0]

    exp = strategy_expectancy(conn, strategy_name)
    incubation = (
        exp["expectancy_pct"] is not None
        and exp["expectancy_pct"] > exp["cost_margin_pct"]
        and not exp["tradeable"]
        and not exp["decaying"]
    )
    cap = (INCUBATION_PREDICTION_OPEN_CAP
           if incubation else BASE_PREDICTION_OPEN_CAP)
    return {
        "strategy_name": strategy_name,
        "evidence_lane": "incubation" if incubation else "base",
        "open_predictions": int(open_count),
        "open_cap": cap,
        "open_slots_remaining": max(0, cap - int(open_count)),
    }


# One column list for both selection lanes — main consumes the result
# regardless of lane, so the two queries must return the same shape.
_ACTIONABLE_COLS = ("pr.id, pr.strategy_name, pr.symbol, pr.conviction, "
                    "pr.near_edge_pct, pr.far_edge_pct, pr.timescale, "
                    "pr.regime_tag, pr.ts")


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
    EV = p·reward − (1−p)·stop > 0 (RR > (1−p)/p), with p = wins/n — scratches
    count as non-wins, matching expectancy. Returns the argmax-EV one
    (tiebreak: earliest). None when nothing qualifies — zero active/tradeable
    strategies or nothing fresh — so the desk correctly stays idle. The live tool
    re-checks EV against the actual fill price; this only picks the candidate id.
    When the PILOT sentinel is armed and the graduated lane is empty, a second
    lane selects the highest-conviction fresh test-book prediction (see the
    pilot block below) — the result carries ``lane`` so main can say which."""
    now = now if now is not None else time.time()
    cutoff = now - max_age_s
    rows = _rows(conn.execute(
        f"""SELECT {_ACTIONABLE_COLS}
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
        # p counts scratches as non-wins (wins/n), consistent with expectancy
        # — the scratch-free win_rate would overstate the hit rate.
        p = (stats["wins"] / stats["n"]) if stats["n"] else None
        stop = stats["stop_pct"]
        if not p or not stop:
            continue
        # Reward = the edge of the strategy's BEST exit target (near vs far).
        target = stats["best_target"]
        edge = r["near_edge_pct"] if target == "near" else r["far_edge_pct"]
        reward = abs(float(edge))
        # Net of the estimated round-trip cost — same haircut as the
        # tradeable gate, applied per setup.
        ev = p * reward - (1.0 - p) * stop - ESTIMATED_ROUND_TRIP_COST_PCT
        if ev <= 0:
            continue
        cand = {**r, "ev_pct": round(ev, 4), "p_win": round(p, 3),
                "stop_pct": stop, "reward_pct": reward, "target": target,
                "lane": "graduated"}
        if best is None or ev > best["ev_pct"]:
            best = cand
    if best is not None:
        return best

    # PILOT lane: when no graduated candidate exists, the highest-CONVICTION
    # fresh prediction from a TEST book qualifies — argmax conviction,
    # tiebreak earliest. No EV gate here: evidence-empty books have no
    # calibration, and desk_open_position re-gates RR at the live price with
    # a neutral prior. The graduated lane always wins when it has a candidate.
    if not pilot_armed():
        return None
    from trading.conviction.engine import GLOBAL_CONVICTION_THRESHOLD
    pilot_rows = _rows(conn.execute(
        f"""SELECT {_ACTIONABLE_COLS}
           FROM predictions pr
           JOIN strategies s ON s.name = pr.strategy_name
           WHERE pr.resolved_at IS NULL AND s.status = 'test'
             AND pr.conviction >= ? AND pr.ts >= ?
           ORDER BY pr.conviction DESC, pr.ts ASC
           LIMIT 1""", (GLOBAL_CONVICTION_THRESHOLD, cutoff)))
    if pilot_rows:
        return {**pilot_rows[0], "lane": "pilot"}
    return None


def desk_gaps(conn: sqlite3.Connection, limit: int = 5) -> dict:
    """Deterministic 'broken vs patient' read — the cold-start legibility
    query. How far each live book is from the graduation bar (gap = hurdle −
    expectancy), status/tradeable mismatches (empty when the status sync is
    healthy), and what is fundable right now. The dispatcher-level
    ``desk_status`` wraps this with HALT, readiness, and the open position."""
    now = time.time()
    counts: dict = {}
    for status, ts, c in conn.execute(
            "SELECT status, timescale, COUNT(*) FROM strategies "
            "GROUP BY status, timescale"):
        counts.setdefault(status, {})[ts] = c
    books = []
    for r in conn.execute(
            "SELECT name, status FROM strategies WHERE status != 'retired'"):
        exp = strategy_expectancy(conn, r["name"])
        if exp["expectancy_pct"] is None:
            continue
        books.append({
            "strategy": r["name"], "status": r["status"], "n": exp["n"],
            "expectancy_pct": exp["expectancy_pct"],
            "hurdle_pct": exp["hurdle_pct"],
            "gap_pct": round(exp["hurdle_pct"] - exp["expectancy_pct"], 4),
            "siblings_tried": exp["siblings_tried"],
            "n_to_clear": exp["n_to_clear"],
            "decaying": exp["decaying"], "tradeable": exp["tradeable"]})
    books.sort(key=lambda b: b["gap_pct"])
    open_total = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL"
    ).fetchone()[0]
    # fundable_now must agree with best_actionable_prediction's lanes — with
    # the pilot armed, test books count too, or this surface reports 0 while
    # the desk funds a trade.
    fundable_statuses = ("active", "test") if pilot_armed() else ("active",)
    fresh = conn.execute(
        f"""SELECT COUNT(*), MIN(? - pr.ts) FROM predictions pr
           JOIN strategies s ON s.name = pr.strategy_name
           WHERE pr.resolved_at IS NULL
             AND s.status IN ({','.join('?' * len(fundable_statuses))})
             AND pr.ts >= ?""",
        (now, *fundable_statuses, now - ACTIONABLE_MAX_AGE_S)).fetchone()
    return {
        "strategy_counts": counts,
        "closest_to_tradeable": books[:limit],
        "status_mismatches": [b for b in books
                              if b["tradeable"] != (b["status"] == "active")],
        "open_predictions": open_total,
        "fundable_now": {"count": fresh[0],
                         "youngest_age_s": (round(fresh[1])
                                            if fresh[1] is not None else None)},
        "actionable_window_s": ACTIONABLE_MAX_AGE_S,
    }


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

    ``by_strategy`` counts only UNDECIDED open predictions — the same rows the
    per-strategy cap in ``write.record_prediction`` counts. Win-locked rows
    (``reached_near_at`` stamped, outcome already decided, awaiting far edge or
    horizon) are reported separately in ``win_locked_by_strategy`` so a hot
    strategy's slots read as free the moment its floor is hit.
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
        "WHERE resolved_at IS NULL AND reached_near_at IS NULL "
        "AND strategy_name IS NOT NULL "
        "GROUP BY strategy_name"
    ).fetchall())
    win_locked = dict(conn.execute(
        "SELECT strategy_name, COUNT(*) FROM predictions "
        "WHERE resolved_at IS NULL AND reached_near_at IS NOT NULL "
        "AND strategy_name IS NOT NULL "
        "GROUP BY strategy_name"
    ).fetchall())
    return {"open_total": total, "by_timescale": by_timescale,
            "by_strategy": by_strategy,
            "win_locked_by_strategy": win_locked}


# ── Correlation buckets (2026-08-08, the multi-asset turn) ──────────────────
# The multiplicity premium prices a best-of-M selection. Crypto majors are
# largely ONE trade in crypto beta — counting a BTC book and an ETH book as
# independent trials would quietly deflate the very premium the bar exists
# to charge — so M counts serious trials within the symbol's CORRELATION
# BUCKET, not per symbol. A symbol outside every bucket competes only with
# itself: honest, because no cross-selection actually occurs there.
CORRELATION_BUCKETS = {
    "crypto": {"BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP"},
    "metals": {"xyz:GOLD", "xyz:SILVER", "xyz:PLATINUM", "xyz:PALLADIUM"},
    "equities": {"xyz:SP500", "xyz:XYZ100", "xyz:NVDA", "xyz:TSLA",
                 "xyz:AAPL", "xyz:MSFT", "xyz:GOOGL", "xyz:AMZN",
                 "xyz:META", "xyz:SPCX"},
    "energy": {"xyz:CL", "xyz:BRENTOIL", "xyz:NATGAS"},
    "fx": {"xyz:EUR", "xyz:JPY", "xyz:GBP", "xyz:KRW", "xyz:DXY"},
}


def bucket_of(symbol: str) -> set:
    """The symbols whose books count as selection siblings of ``symbol``."""
    for members in CORRELATION_BUCKETS.values():
        if symbol in members:
            return members
    return {symbol}


def strategy_cells(timescale: str, regime_applicability_json) -> set:
    """The (timescale, direction, volatility, macro) cells a strategy occupies.

    Since 2026-07-27 a declaration names exactly one cell, so this returns a
    single entry for anything authored after the rule. Legacy set-valued
    declarations expand to their full cross-product: such a strategy really
    does compete in every cell it declares, so it counts in all of them for
    both the multiplicity premium and the per-cell cap.

    Returns an empty set when the declaration cannot be read — callers treat
    that as "unknown" and fall back to the conservative, wider scope rather
    than inventing a cell.
    """
    try:
        ra = regime_applicability_json
        if isinstance(ra, str):
            ra = json.loads(ra or "{}")
        axes = (ra or {}).get(timescale) or {}
    except Exception:
        return set()
    dirs = axes.get("direction") or [None]
    vols = axes.get("volatility") or [None]
    macros = axes.get("macro") or [None]
    if dirs == [None] and vols == [None] and macros == [None]:
        return set()
    return {(timescale, d, v, m) for d in dirs for v in vols for m in macros}


def cell_capacity(conn: sqlite3.Connection, cap: int = CELL_OCCUPANCY_CAP) -> list:
    """Occupancy of every populated regime cell, against the cap.

    The cap is admission control on generation, not a pruning suggestion. It
    existed as prose in reflect's brief since the rebuild ("cap ~2 active + 6
    test") and was never enforced anywhere, which is how 88 strategies came to
    sit across 34 cells with 23 of them over.

    Only `test` and `active` books occupy a slot. Retired occupies
    nothing and counts nothing — withdrawn is withdrawn. Generate reads
    the retired files so a drained book is memory, not a ghost on the bar.
    """
    rows = conn.execute(
        """SELECT name, symbol, timescale, status,
                  regime_applicability_json ra
           FROM strategies WHERE status IN ('test','active')""").fetchall()
    occ: dict = {}
    symbols = sorted({r["symbol"] for r in rows})
    for r in rows:
        for cell in strategy_cells(r["timescale"], r["ra"]):
            occ.setdefault((r["symbol"],) + cell, []).append(r["name"])
    # Lit is judged against each SYMBOL's own regime — a gold cell is lit by
    # gold's tape, not BTC's.
    lit = set()
    live_any = False
    for sym in symbols:
        live = current_regime(conn, symbol=sym)
        live_any = live_any or bool(live)
        for ts, c in live.items():
            lit.add((sym, ts, c.get("direction"), c.get("volatility"),
                     c.get("macro")))
    out = []
    for cell, names in sorted(occ.items(), key=lambda kv: -len(kv[1])):
        out.append({
            "cell": "/".join(x for x in cell if x),
            "symbol": cell[0],
            "timescale": cell[1],
            # Whether the tape is IN this cell right now. Generation's gap
            # report ("lit cells that are under-populated") was narrated from
            # the markdown board; it is computable now.
            "lit": (cell in lit) if live_any else None,
            "occupants": len(names),
            "cap": cap,
            "slots_remaining": max(0, cap - len(names)),
            "over_by": max(0, len(names) - cap),
        })
    return out


def strategy_cell_expectancy(
    conn: sqlite3.Connection, strategy_name: str, *,
    min_cell_n: int = CELL_MIN_N,
) -> dict:
    """Expectancy per REGIME CELL, and the verdict that depends on it.

    A blended book averages conditions the strategy never trades together, and
    the average describes none of them. Measured across the desk's twelve
    multi-regime books on 2026-07-27, splitting on ``regime_tag`` moved six of
    them from "never graduates" or four-figure sample requirements to between
    46 and 143 — a gap far larger than the multiplicity cost of counting each
    cell as its own trial.

    The case that forced this into code: ``ema20-pivot-swing`` blended to
    -0.004 and so met the retirement bar, while four of its five cells were
    positive (ranging/normal +0.684) and a single bad cell
    (trending-up/compressed, -0.429) sank the average. Retiring on the blend
    would have buried a working mechanism AND lowered the graduation hurdle
    for every sibling on a false premise.

    Returns ``{strategy_name, blended, cells[], best_cell, dead}`` where
    ``dead`` is True only when NO cell with at least ``min_cell_n``
    resolutions has positive expectancy. Cells below that threshold are
    reported with ``judged: False`` — visible, never counted. ``dead`` is None
    when nothing is judgeable yet, which is not the same as alive and must not
    be read as either.
    """
    blended = strategy_expectancy(conn, strategy_name)
    tags = [r[0] for r in conn.execute(
        """SELECT regime_tag FROM predictions
           WHERE strategy_name = ? AND resolved_at IS NOT NULL
             AND realized_value_json IS NOT NULL AND regime_tag IS NOT NULL
           GROUP BY regime_tag ORDER BY COUNT(*) DESC""", (strategy_name,))]
    cells = []
    for tag in tags:
        e = strategy_expectancy(conn, strategy_name, regime_tag=tag)
        n = e.get("n") or 0
        cells.append({
            "regime_tag": tag, "n": n,
            "expectancy_pct": e.get("expectancy_pct"),
            "pnl_stdev_pct": e.get("pnl_stdev_pct"),
            "win_rate": e.get("win_rate"),
            "judged": n >= min_cell_n,
        })
    judged = [c for c in cells
              if c["judged"] and c["expectancy_pct"] is not None]
    best = max(judged, key=lambda c: c["expectancy_pct"]) if judged else None
    return {
        "strategy_name": strategy_name,
        "blended_expectancy_pct": blended.get("expectancy_pct"),
        "blended_n": blended.get("n"),
        "cells": cells,
        "cells_judged": len(judged),
        "best_cell": best,
        "dead": (None if not judged
                 else not any(c["expectancy_pct"] > 0 for c in judged)),
    }


def strategies_by_timescale(
    conn: sqlite3.Connection,
    timescale: str,
    statuses: tuple = ("test", "active"),
) -> list:
    """Strategies at a timescale, with their regime cells + counters — the
    population-visibility query behind the per-(timescale × regime) cell cap
    that predict/reflect enforce as a reasoning guardrail (a strategy is
    applicable in the SET of regime labels it declares).

    Also carries the SAMPLING counters (2026-07-27). Predict selected on
    regime match, open slot and perception freshness only — nothing told it
    how long a strategy had gone unsampled, so books fell out of rotation
    silently: two sat at 17 and 23 days untouched while still `test`, neither
    proving nor disproving themselves. The counters are visibility, not a
    trigger; predict still needs a real reason to file.

    ``days_since_last_prediction`` is None for a book that has never been
    sampled — honestly absent rather than a sentinel age.

    ``is_serious_trial`` is the one that carries a cost. A book crossing
    SERIOUS_TRIAL_MIN_N resolutions becomes a multiplicity sibling and
    PERMANENTLY raises the hurdle for every strategy at its timescale, so
    sampling a stagnant book at n=9 is free — it is already paying — while
    sampling one at n=4 charges the whole timescale. Predict sees both and
    decides; the cost is stated, never hidden.
    """
    marks = ",".join("?" * len(statuses))
    rows = _rows(conn.execute(
        f"""SELECT name, status, symbol, timescale, mechanism_family,
                   regime_applicability_json, n_resolved, n_correct, n_wrong
            FROM strategies WHERE timescale = ? AND status IN ({marks})
            ORDER BY status, name""", [timescale, *statuses]))
    now = time.time()
    # Eligibility is judged against each strategy's OWN symbol's regime.
    lit_cell_by_symbol: dict = {}
    for sym in {r["symbol"] for r in rows}:
        lit = current_regime(conn, symbol=sym).get(timescale) or {}
        lit_cell_by_symbol[sym] = (
            (timescale, lit.get("direction"), lit.get("volatility"),
             lit.get("macro")) if lit else None)
    for r in rows:
        decided = r["n_correct"] + r["n_wrong"]
        r["win_rate"] = round(r["n_correct"] / decided, 3) if decided else None
        r["rr"] = strategy_rr(conn, r["name"])
        capacity = strategy_prediction_capacity(conn, r["name"])
        r.update({
            "evidence_lane": capacity["evidence_lane"],
            "open_predictions": capacity["open_predictions"],
            "open_cap": capacity["open_cap"],
            "open_slots_remaining": capacity["open_slots_remaining"],
        })
        last_ts = conn.execute(
            "SELECT MAX(ts) FROM predictions WHERE strategy_name = ?",
            (r["name"],)).fetchone()[0]
        r["last_prediction_ts"] = last_ts
        r["days_since_last_prediction"] = (
            round((now - last_ts) / 86400.0, 1) if last_ts else None)
        r["resolutions"] = conn.execute(
            """SELECT COUNT(*) FROM predictions
               WHERE strategy_name = ? AND resolved_at IS NOT NULL
                 AND realized_value_json IS NOT NULL""",
            (r["name"],)).fetchone()[0]
        r["is_serious_trial"] = r["resolutions"] >= SERIOUS_TRIAL_MIN_N
        # Eligibility is CODE's answer now (2026-07-27). Predict used to match
        # the declared cell against REGIME.md in its own reasoning, which meant
        # the rotation counters arrived unfiltered: a book silent 23 days
        # because its cell was dark is correctly idle, not a scheduling gap,
        # and telling the difference was left to judgement over a list code
        # could filter. None when the regime is unknown — absent, not False,
        # so a desk that has never assessed does not read as "nothing is
        # eligible".
        cells = strategy_cells(r["timescale"], r["regime_applicability_json"])
        _lit_cell = lit_cell_by_symbol.get(r["symbol"])
        r["regime_eligible"] = (None if _lit_cell is None
                                else _lit_cell in cells)
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


def strategy_book(conn: sqlite3.Connection, statuses: tuple = ("test", "active")) -> list:
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


def retired_book(conn: sqlite3.Connection) -> list:
    """The graveyard generate reads before authoring.

    Retired files stay on disk so the desk does not re-author a mechanism
    that already failed, or so a variant can name what went wrong and the
    one thing that is different. This is why the files exist — not to sit
    on the bar.
    """
    rows = _rows(conn.execute(
        """SELECT name, symbol, timescale, mechanism_family, parent_strategy,
                  regime_applicability_json, retirement_reason,
                  n_resolved, n_correct, n_wrong, retired_at
           FROM strategies WHERE status = 'retired'
           ORDER BY symbol, timescale, name"""))
    for r in rows:
        decided = r["n_correct"] + r["n_wrong"]
        r["win_rate"] = round(r["n_correct"] / decided, 3) if decided else None
        exp = strategy_expectancy(conn, r["name"])
        r["expectancy_pct"] = exp["expectancy_pct"]
        r["n"] = exp["n"]
        r["decaying"] = exp["decaying"]
        raw = r.pop("regime_applicability_json", None)
        try:
            r["regime_applicability"] = json.loads(raw or "{}")
        except Exception:
            r["regime_applicability"] = {}
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
    reflect's evidence for retuning the conviction→NOTIONAL bands (loss per
    stop-out ≈ notional-multiple × stop-distance; ``risk_at_stop_pct`` on the
    decision's sizing block records it per trade). Split by ``pilot`` so the
    operator-mandate lane never pollutes the graduated lane's evidence.
    Rows with no opening-decision conviction group under NULL; shown, never dropped.

    ``fix_ts`` (epoch seconds, Issue 4 cutover) excludes positions whose opening
    decision predates the conviction-render fix — those convictions were scored
    on a byte-truncated (blinded) reading substrate and would pollute the sizing
    review. None (the default) includes all history. Decision-less positions
    (NULL d.ts) are always kept, matching the NULL-band-shown contract.

    ``naked_position_abort`` closes are excluded — a 1-second plumbing abort
    is not sizing evidence (2026-07-03: five aborts at ~-0.002% each would
    have dragged the 0.7 band's stats)."""
    return _rows(conn.execute(
        """SELECT CAST(d.conviction * 10 AS INT) / 10.0 AS conviction_band,
                  COALESCE(json_extract(d.params_json, '$.pilot'), 0) AS pilot,
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
                 AND COALESCE(o.exit_reason, '') != 'naked_position_abort'
           GROUP BY conviction_band, pilot
           ORDER BY conviction_band, pilot""",
        {"fix_ts": fix_ts}))


def current_regime(conn: sqlite3.Connection, symbol: str = "BTC") -> dict:
    """The live regime per timescale — the latest observation of each.

    Until 2026-07-27 this had no code answer at all: the regime existed only
    as markdown in REGIME.md, so predict matched strategies against the tape
    inside its own reasoning and every cell-aware surface stopped at the
    prompt boundary.

    Returns ``{timescale: {direction, volatility, macro, ts, age_h}}``, empty
    for a timescale never assessed — absent, never guessed.
    """
    out = {}
    for ts_name in ("intraday", "swing", "position"):
        row = conn.execute(
            """SELECT ts, direction, volatility, macro, conviction, source
               FROM regime_observations
               WHERE symbol = ? AND timescale = ?
               ORDER BY ts DESC LIMIT 1""", (symbol, ts_name)).fetchone()
        if not row:
            continue
        out[ts_name] = {
            "direction": row["direction"], "volatility": row["volatility"],
            "macro": row["macro"], "conviction": row["conviction"],
            "source": row["source"], "ts": row["ts"],
            "age_h": round((time.time() - row["ts"]) / 3600.0, 2),
            "cell": "/".join(x for x in (ts_name, row["direction"],
                                         row["volatility"], row["macro"]) if x),
        }
    return out


def regime_occupancy(conn: sqlite3.Connection, since_ts: float,
                     symbol: str = "BTC") -> list:
    """How much of the window each cell held, by distinct observed days.

    The number the accrual arithmetic needs and could previously only
    approximate from ``predictions.regime_tag`` — which sees a cell only when
    the desk sampled it, so a cell nothing traded in reads as never lit.

    This is what makes cell GRANULARITY measurable: whether
    direction x volatility is too fine for the desk's data rate is a question
    about how often each cell is lit, and until now nothing could answer it.
    """
    rows = conn.execute(
        """SELECT timescale, direction, volatility, macro,
                  COUNT(DISTINCT date(ts,'unixepoch')) days, COUNT(*) n
           FROM regime_observations
           WHERE symbol = ? AND ts >= ?
           GROUP BY timescale, direction, volatility, macro""",
        (symbol, since_ts)).fetchall()
    total = {}
    for ts_name in ("intraday", "swing", "position"):
        total[ts_name] = conn.execute(
            """SELECT COUNT(DISTINCT date(ts,'unixepoch')) FROM
               regime_observations WHERE symbol = ? AND ts >= ? AND timescale = ?""",
            (symbol, since_ts, ts_name)).fetchone()[0]
    out = []
    for r in rows:
        denom = total.get(r["timescale"]) or 0
        out.append({
            "cell": "/".join(x for x in (r["timescale"], r["direction"],
                                         r["volatility"], r["macro"]) if x),
            "timescale": r["timescale"],
            "days_lit": r["days"],
            "days_observed": denom,
            "lit_fraction": round(r["days"] / denom, 3) if denom else None,
            "observations": r["n"],
        })
    return sorted(out, key=lambda x: -(x["lit_fraction"] or 0))
