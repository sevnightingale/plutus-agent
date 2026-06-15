"""The single deterministic prediction resolver.

Shared by the ops sweep (safety net) and the watcher alert (event-driven). It
is PURE ORCHESTRATION over injected IO — ``mids`` (symbol → current price),
``path_stats_fn`` (windowed candle stats), and ``fetch_fn`` (a data-point
reader for invalidation leaves). No venue or registry imports, so it unit-tests
with plain fakes.

Per open prediction it decides, in this precedence:
  1. the favorable excursion reached the near edge → CORRECT (success wins even
     if invalidation also tripped — you hit your target);
  2. invalidation criteria tripped → WRONG (mode 'invalidated');
  3. horizon passed → WRONG (mode 'expired');
  else still open (and, crucially, no candle fetch — the cheap path).

Candles are pulled only when something is actually resolving, so a 5-second
watcher tick stays a single ``all_mids()`` call most of the time.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Dict, Optional

from trading.lifecycle import criteria as criteria_mod
from trading.lifecycle import price_zone, write


def resolve_open_predictions(
    conn,
    *,
    mids: Dict[str, float],
    path_stats_fn: Callable[..., dict],
    fetch_fn: Optional[Callable] = None,
    fetch_extreme_fn: Optional[Callable] = None,
    now: Optional[float] = None,
) -> dict:
    """Resolve every open price-zone prediction that has met its terms.

    Returns ``{"resolved": [...], "open_count": N}``. Each resolved entry is a
    compact dict suitable for a wake event. Idempotent across callers: the
    race-safe ``write.resolve_prediction`` ensures one winner per prediction.
    """
    now = now if now is not None else time.time()
    rows = conn.execute(
        """SELECT id, symbol, ts, horizon_ts, entry_ref_price, near_edge_pct,
                  far_edge_pct, invalidation_criteria_json, strategy_name
           FROM predictions WHERE resolved_at IS NULL"""
    ).fetchall()

    resolved = []
    for r in rows:
        outcome, mode, stats = _decide(
            r, mids, path_stats_fn, fetch_fn, fetch_extreme_fn, now)
        if outcome is None:
            continue
        realized = dict(stats or {})
        realized["resolution_mode"] = mode
        won = write.resolve_prediction(
            conn, r["id"], outcome, resolved_by="resolver",
            notes_md=f"price-zone {mode}", realized_value=realized, ts=now)
        if won:
            resolved.append({
                "prediction_id": r["id"], "outcome": outcome, "mode": mode,
                "symbol": r["symbol"], "strategy_name": r["strategy_name"],
            })
    return {"resolved": resolved, "open_count": len(rows)}


def _decide(r, mids, path_stats_fn, fetch_fn, fetch_extreme_fn, now):
    """-> (outcome|None, mode|None, stats|None). None outcome = still open."""
    near, far = r["near_edge_pct"], r["far_edge_pct"]
    entry, symbol = r["entry_ref_price"], r["symbol"]
    horizon_passed = now >= r["horizon_ts"]

    # Malformed/legacy row with no zone — never crash the sweep; expire at horizon.
    if near is None or far is None or not entry:
        return ("wrong", "expired", None) if horizon_passed else (None, None, None)

    direction = price_zone.direction_of(near, far)
    price = mids.get(symbol) if mids else None
    mid_mfe = (
        (100.0 * (float(price) - entry) / entry) * (1 if direction > 0 else -1)
        if price and entry > 0 else None
    )
    touched_now = mid_mfe is not None and mid_mfe >= abs(near)

    invalidated = False
    inv_json = r["invalidation_criteria_json"]
    if not touched_now and inv_json and fetch_fn is not None:
        try:
            invalidated = criteria_mod.resolve(
                json.loads(inv_json), fetch_fn, fetch_extreme_fn) == "correct"
        except Exception:
            invalidated = False

    # Cheap path: nothing to do, don't touch the network.
    if not (touched_now or invalidated or horizon_passed):
        return (None, None, None)

    # Something's resolving — pull windowed stats (accurate; catches the wick a
    # 5s/30s poll missed for the horizon case). Fall back to the live mid.
    stats = path_stats_fn(symbol, r["ts"], now, entry, direction) or {}
    mfe = stats.get("mfe_pct")
    if mfe is None:
        mfe = mid_mfe

    # Success precedence: hitting the target beats a simultaneous invalidation.
    if mfe is not None and mfe >= abs(near):
        stats["profit_score"] = price_zone.profit_score(near, far, mfe)
        return ("correct", "touch", stats)
    if invalidated:
        return ("wrong", "invalidated", stats)
    if horizon_passed:
        return ("wrong", "expired", stats)
    # touched_now was a transient the windowed read didn't confirm — stay open.
    return (None, None, None)
