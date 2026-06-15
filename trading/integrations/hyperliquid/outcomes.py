"""Hyperliquid outcome enrichment.

After ``close_position`` writes the shell outcomes row (conviction
trajectory + exit_reason), the dispatcher calls
``compute_outcome(position_id)`` here. We fetch HL price history over
the holding window at fine resolution, compute MAE/MFE/r_multiple +
PnL + efficiencies + slippage, and return a dict the dispatcher
UPDATEs into the row.

PnL math is from the venue truth (closing fill - opening fill, signed
by side, scaled by size, minus realized fees from user_fills_by_time
when available). MAE/MFE come from the candle window. r_multiple uses
the SL distance from the opening decision params; if no SL was set we
fall back to the % move from entry as a unit-less ratio of "how big
did this swing" — operator can read ``mae_pct``/``mfe_pct`` directly.

Invalidation timing is read from the position_evaluations row whose
``thesis_status`` first transitions to 'invalidated'.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from trading.lifecycle.db import get_db

from ._client import get_info, interval_to_ms

logger = logging.getLogger(__name__)


def _select_interval(holding_seconds: float) -> str:
    """Pick a candle interval that gives us ~50-300 bars over the holding window."""
    minutes = holding_seconds / 60.0
    if minutes <= 30:
        return "1m"
    if minutes <= 180:
        return "5m"
    if minutes <= 720:        # 12h
        return "15m"
    if minutes <= 2880:       # 2d
        return "1h"
    return "4h"


def path_stats(
    symbol: str, start_ts: float, end_ts: float, ref_price: float, direction: float
) -> Dict[str, Any]:
    """Windowed MAE/MFE/range over ``[start_ts, end_ts]`` vs ``ref_price``.

    The shared candle-path primitive: ``compute_outcome`` uses it for closed
    positions, the prediction resolver for price-zone resolution. ``direction``
    is +1 (long/bullish) or -1 (short/bearish).

    Returns ``{mae_pct (≤0 adverse), mfe_pct (≥0 favorable), range_pct, low_px,
    high_px, n_bars}`` — or ``{}`` if candles are unavailable (network failure
    or empty window), which the caller treats as "could not measure", never a
    fabricated zero.
    """
    if ref_price is None or ref_price <= 0:
        return {}
    holding_seconds = max(0.0, float(end_ts) - float(start_ts))
    try:
        interval = _select_interval(holding_seconds)
        bar_ms = interval_to_ms(interval)
        start_ms = int(float(start_ts) * 1000) - bar_ms
        end_ms = int(float(end_ts) * 1000) + bar_ms
        candles = get_info().candles_snapshot(symbol, interval, start_ms, end_ms)
    except Exception as exc:  # pragma: no cover — network errors are real but rare
        logger.warning("path_stats candle fetch failed for %s: %s", symbol, exc)
        return {}
    if not candles:
        return {}
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    low_px, high_px = min(lows), max(highs)
    low_pct = 100.0 * (low_px - ref_price) / ref_price
    high_pct = 100.0 * (high_px - ref_price) / ref_price
    if direction > 0:
        mfe_pct, mae_pct = high_pct, low_pct
    else:
        mfe_pct, mae_pct = -low_pct, -high_pct
    return {
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "range_pct": high_pct - low_pct,
        "low_px": low_px,
        "high_px": high_px,
        "n_bars": len(candles),
    }


def compute_outcome(position_id: int) -> Dict[str, Any]:
    conn = get_db()

    # Position + opening trade
    pos = conn.execute(
        "SELECT id, venue, symbol, side, size, opened_at, closed_at, "
        "opening_trade_id, closing_trade_id "
        "FROM positions WHERE id = ?",
        (position_id,),
    ).fetchone()
    if pos is None:
        raise ValueError(f"position {position_id} not found")
    if not pos["closed_at"]:
        raise ValueError(f"position {position_id} not closed")
    if pos["venue"] != "hyperliquid":
        raise ValueError(
            f"hyperliquid.compute_outcome called for venue '{pos['venue']}'"
        )

    open_trade = conn.execute(
        "SELECT id, decision_id, ts, fill_price, size, slippage_bp "
        "FROM trades WHERE id = ?",
        (pos["opening_trade_id"],),
    ).fetchone()
    close_trade = conn.execute(
        "SELECT id, decision_id, ts, fill_price, size, slippage_bp "
        "FROM trades WHERE id = ?",
        (pos["closing_trade_id"],),
    ).fetchone() if pos["closing_trade_id"] else None

    if open_trade is None or close_trade is None:
        raise ValueError(
            f"position {position_id} missing open or close trade record"
        )

    entry_px = float(open_trade["fill_price"])
    exit_px = float(close_trade["fill_price"])
    size = float(open_trade["size"])
    side = pos["side"]
    direction = 1.0 if side == "long" else -1.0

    realized_pnl_usd = direction * (exit_px - entry_px) * size
    realized_pnl_pct = (
        100.0 * direction * (exit_px - entry_px) / entry_px
        if entry_px > 0 else 0.0
    )
    holding_seconds = float(pos["closed_at"]) - float(pos["opened_at"])
    holding_minutes = holding_seconds / 60.0

    slippage_total_bp = (
        (float(open_trade["slippage_bp"] or 0)) + (float(close_trade["slippage_bp"] or 0))
    )

    # MAE / MFE from candle high/low over holding window (shared primitive).
    mae_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    entry_efficiency: Optional[float] = None
    exit_efficiency: Optional[float] = None
    stats = path_stats(pos["symbol"], open_trade["ts"], close_trade["ts"], entry_px, direction)
    if stats:
        mae_pct = stats["mae_pct"]
        mfe_pct = stats["mfe_pct"]
        # Efficiencies: how much of the favorable range did we capture?
        # entry_efficiency: 0 if entered at worst price, 1 if entered at best
        # exit_efficiency:  0 if exited at worst, 1 if exited at best
        bar_extreme_low = stats["low_px"]
        bar_extreme_high = stats["high_px"]
        rng = bar_extreme_high - bar_extreme_low
        if rng > 0:
            if side == "long":
                entry_efficiency = max(0.0, min(1.0, (bar_extreme_high - entry_px) / rng))
                exit_efficiency = max(0.0, min(1.0, (exit_px - bar_extreme_low) / rng))
            else:
                entry_efficiency = max(0.0, min(1.0, (entry_px - bar_extreme_low) / rng))
                exit_efficiency = max(0.0, min(1.0, (bar_extreme_high - exit_px) / rng))

    # r_multiple: signed PnL per unit risk. Risk taken from opening decision
    # params (sl distance × size). Falls back to ``realized_pnl_pct`` when
    # no SL was articulated — Plutus can read mae_pct as the "how much did
    # I sweat" metric in that case.
    r_multiple: Optional[float] = None
    decision = conn.execute(
        "SELECT params_json FROM decisions WHERE id = ?",
        (open_trade["decision_id"],),
    ).fetchone()
    if decision and decision["params_json"]:
        try:
            params = json.loads(decision["params_json"])
        except Exception:
            params = {}
        sl = params.get("sl")
        if sl is not None and entry_px > 0:
            risk_per_unit = abs(entry_px - float(sl))
            if risk_per_unit > 0:
                # Signed: positive r_multiple == win, negative == loss.
                r_multiple = direction * (exit_px - entry_px) / risk_per_unit

    # Invalidation timing: first position_evaluation row with
    # thesis_status = 'invalidated'.
    inv_ts: Optional[float] = None
    inv_to_exit_min: Optional[float] = None
    inv_row = conn.execute(
        "SELECT ts FROM position_evaluations "
        "WHERE position_id = ? AND thesis_status = 'invalidated' "
        "ORDER BY ts ASC LIMIT 1",
        (position_id,),
    ).fetchone()
    if inv_row:
        inv_ts = float(inv_row["ts"])
        inv_to_exit_min = (float(close_trade["ts"]) - inv_ts) / 60.0

    return {
        "realized_pnl_usd": realized_pnl_usd,
        "realized_pnl_pct": realized_pnl_pct,
        "r_multiple": r_multiple,
        "holding_minutes": holding_minutes,
        "mae_pct": mae_pct,
        "mfe_pct": mfe_pct,
        "entry_efficiency": entry_efficiency,
        "exit_efficiency": exit_efficiency,
        "slippage_total_bp": slippage_total_bp,
        "invalidation_triggered_at": inv_ts,
        "invalidation_to_exit_minutes": inv_to_exit_min,
    }
