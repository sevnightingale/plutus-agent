"""The single deterministic prediction resolver.

Shared by the ops sweep (safety net) and the watcher alert (event-driven). It
is PURE ORCHESTRATION over injected IO — ``mids`` (symbol → current price),
``path_stats_fn`` (windowed candle stats), and ``fetch_fn`` (a data-point
reader for invalidation leaves). No venue or registry imports, so it unit-tests
with plain fakes.

Per open prediction it decides (floor-correct, target-accelerated, horizon-
backstopped):
  - far edge reached → CORRECT early (mode 'target'); a full target beats a
    simultaneous invalidation;
  - near edge reached but not far, horizon not passed → the win is LOCKED:
    stamp ``reached_near_at`` and STAY OPEN (no candle fetch — the cheap path);
  - horizon passed with the near edge reached → CORRECT (mode 'horizon');
  - horizon passed without it → WRONG (mode 'expired');
  - invalidation tripped BEFORE the near edge → WRONG (mode 'invalidated').

Candles are pulled only when something is actually resolving; near-touch
detection and the stay-open stamp run off the live mid, so a 5-second watcher
tick stays a single ``all_mids()`` call most of the time.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, Dict, Optional

from trading.lifecycle import criteria as criteria_mod
from trading.lifecycle import price_zone, write

logger = logging.getLogger(__name__)


def resolve_open_predictions(
    conn,
    *,
    mids: Dict[str, float],
    path_stats_fn: Callable[..., dict],
    fetch_fn: Optional[Callable] = None,
    fetch_extreme_fn: Optional[Callable] = None,
    now: Optional[float] = None,
    deep: bool = False,
) -> dict:
    """Resolve every open price-zone prediction that has met its terms.

    Returns ``{"resolved": [...], "marked_near": [...], "open_count": N}``.
    Each resolved entry is a compact dict suitable for a wake event. Idempotent
    across callers: the race-safe ``write.resolve_prediction`` ensures one
    winner per prediction.

    After any resolution, runs the deterministic test↔active status sync
    (``graduation.sync_strategy_statuses``) so both the watcher path and the
    ops safety-net path share one code-owned graduation flip — sync failure
    is logged and never blocks the resolution result.

    ``deep=False`` (the watcher's per-tick fast path) detects edge touches off
    the live ``mids`` only — one ``all_mids()`` call, no candle pull unless
    something is already resolving. ``deep=True`` (the ops safety-net sweep)
    additionally pulls the windowed candle path for each still-open prediction
    and re-checks the near/far edges against the path MFE — catching a
    favorable wick that touched an edge and recovered *between* the 5s mid
    polls (the near edge is the correctness floor, so a missed near-touch would
    otherwise expire WRONG a prediction that genuinely hit its floor).
    """
    now = now if now is not None else time.time()
    rows = conn.execute(
        """SELECT id, symbol, ts, horizon_ts, entry_ref_price, near_edge_pct,
                  far_edge_pct, reached_near_at, invalidation_criteria_json,
                  strategy_name
           FROM predictions WHERE resolved_at IS NULL"""
    ).fetchall()

    resolved, marked, unreadable = [], [], []
    for r in rows:
        action, outcome, mode, stats, near_ts, far_ts, inv_unreadable = _decide(
            r, mids, path_stats_fn, fetch_fn, fetch_extreme_fn, now, deep)
        if inv_unreadable:
            unreadable.append({
                "prediction_id": r["id"], "symbol": r["symbol"],
                "strategy_name": r["strategy_name"],
            })
        if action == "open":
            continue
        if action == "mark_near":
            if write.mark_reached_near(conn, r["id"], near_ts):
                marked.append({
                    "prediction_id": r["id"], "symbol": r["symbol"],
                    "strategy_name": r["strategy_name"],
                })
            continue
        realized = dict(stats or {})
        realized["resolution_mode"] = mode
        won = write.resolve_prediction(
            conn, r["id"], outcome, resolved_by="resolver",
            notes_md=f"price-zone {mode}", realized_value=realized,
            reached_near_at=near_ts, reached_far_at=far_ts, ts=now)
        if won:
            resolved.append({
                "prediction_id": r["id"], "outcome": outcome, "mode": mode,
                "symbol": r["symbol"], "strategy_name": r["strategy_name"],
            })
    # Books just changed — the only moment tradeable can flip. Both callers
    # (watcher + ops resolve_due) share this path, so status cannot lag when
    # one daemon is down. Failure must never block resolution events.
    if resolved:
        try:
            from trading.lifecycle.graduation import sync_strategy_statuses
            sync_strategy_statuses(conn)
        except Exception as exc:
            logger.warning("status sync after resolution failed: %s", exc)
    # unresolvable_invalidations is NOT decoration: a thesis-break nothing can
    # read is a prediction the desk cannot judge, and it looks exactly like a
    # thesis that is holding. Reported so ops carries it and the
    # agent_escalations reader can see it.
    return {"resolved": resolved, "marked_near": marked,
            "unresolvable_invalidations": unreadable, "open_count": len(rows)}


def _decide(r, mids, path_stats_fn, fetch_fn, fetch_extreme_fn, now, deep=False):
    """-> (action, outcome|None, mode|None, stats|None, near_ts|None, far_ts|None,
    invalidation_unreadable).

    ``action`` ∈ {'open', 'mark_near', 'resolve'}. The cheap path (no candle
    fetch) covers 'open' and 'mark_near'; candles are pulled only to resolve.
    When ``deep`` is set and the live mid shows nothing, a still-developing
    prediction (near not yet locked, horizon not passed) ALSO gets a candle
    look-back so a favorable wick the mid poll missed still locks the floor.
    """
    near, far = r["near_edge_pct"], r["far_edge_pct"]
    entry, symbol = r["entry_ref_price"], r["symbol"]
    horizon_passed = now >= r["horizon_ts"]
    near_already = r["reached_near_at"] is not None

    # Malformed/legacy row with no zone — never crash the sweep; expire at horizon.
    if near is None or far is None or not entry:
        if horizon_passed:
            return ("resolve", "wrong", "expired", None, None, None, False)
        return ("open", None, None, None, None, None, False)

    direction = price_zone.direction_of(near, far)
    price = mids.get(symbol) if mids else None
    mid_mfe = (
        (100.0 * (float(price) - entry) / entry) * (1 if direction > 0 else -1)
        if price and entry > 0 else None
    )

    # Cheap classify on the live mid — no candle pull, no invalidation fetch yet.
    state = price_zone.classify(
        near, far, mid_mfe, horizon_passed, near_already_reached=near_already)

    # Invalidation matters only BEFORE the near edge and only if nothing else
    # fired — evaluate it lazily (it costs a data-point fetch).
    unresolvable_inv = False
    if state == "open" and not near_already and not horizon_passed:
        inv_json = r["invalidation_criteria_json"]
        if inv_json and fetch_fn is not None:
            try:
                # Bind the prediction's own symbol into any leaf that omits
                # it — the same binder registration uses, so the 20 legacy
                # rows written before that check become readable without a
                # single hand-edited row.
                criteria = criteria_mod.bind_symbol(json.loads(inv_json), symbol)
                verdict = criteria_mod.resolve(
                    criteria, fetch_fn, fetch_extreme_fn)
                if verdict == "correct":
                    state = "invalidated"
                elif verdict == "unresolvable":
                    # NOT the same as "not met", and the difference is the
                    # whole defect: an invalidation nothing can read let three
                    # broken theses ride to horizon unseen (2026-08-25).
                    unresolvable_inv = True
                    logger.warning(
                        "prediction %s: invalidation criteria unresolvable "
                        "(%s) — thesis-break cannot be evaluated",
                        r["id"], inv_json[:200])
            except Exception as exc:
                unresolvable_inv = True
                logger.warning(
                    "prediction %s: invalidation evaluation raised (%s)",
                    r["id"], exc)

    if state == "mark_near":
        return ("mark_near", None, None, None, now, None, unresolvable_inv)

    # Cheap fast path: the live mid shows nothing and this isn't the deep sweep
    # — leave it open (the watcher's per-tick behaviour, no candle pull).
    if state == "open" and not deep:
        return ("open", None, None, None, None, None, unresolvable_inv)

    # Either something is resolving on the live mid, OR this is the ops deep
    # sweep taking a candle look-back over [birth, now] to catch a favorable
    # wick the 5s mid poll missed between ticks. Pull windowed stats for an
    # accurate mfe/mae and (re-)classify against the precise number.
    stats = path_stats_fn(symbol, r["ts"], now, entry, direction) or {}
    mfe = stats.get("mfe_pct")
    if mfe is None:
        mfe = mid_mfe
    state = price_zone.classify(
        near, far, mfe, horizon_passed,
        invalidation_tripped=(state == "invalidated"),
        near_already_reached=near_already)

    if state in ("target", "horizon"):
        stats["profit_score"] = price_zone.profit_score(near, far, mfe)
        far_ts = now if state == "target" else None
        return ("resolve", "correct", state, stats, now, far_ts, unresolvable_inv)
    if state == "expired":
        return ("resolve", "wrong", "expired", stats, None, None, unresolvable_inv)
    if state == "invalidated":
        return ("resolve", "wrong", "invalidated", stats, None, None, unresolvable_inv)
    if state == "mark_near":
        # The precise read says near (not far) — stay open, stamp near.
        return ("mark_near", None, None, None, now, None, unresolvable_inv)
    return ("open", None, None, None, None, None, unresolvable_inv)
