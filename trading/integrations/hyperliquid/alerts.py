"""Hyperliquid alerts polled by the watcher daemon.

Each poll returns a list of *fired event* dicts. The watcher daemon
emits each as a wake event into ``~/.plutus-agent/wake_events.ndjson``;
the gateway tails that file and turns wake events into messages routed
to Plutus.

State (last-seen position set, last-seen equity) lives in
``watchers/state.py`` (``~/.plutus-agent/watcher_state.json``). The
poll fn is given the previous state in ``state`` and returns a tuple
``(events, new_state)`` so the watcher can persist on success.

Three alerts: position-status-change, account-balance-change (on TOTAL
equity — the unified-account measure), and price-range. More alerts
Plutus can author later as it learns what's worth watching.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from trading.perception.core.alert_registry import register_alert

from harness.watchers.price_alerts import (
    auto_disable_after_fire,
    should_fire,
)

from ._client import get_info, HLConfigError

logger = logging.getLogger(__name__)


def _get_address() -> Optional[str]:
    return os.getenv("ACP_AGENT_WALLET") or None


@register_alert(
    name="hl_position_status_change",
    source="hyperliquid",
    throttle_seconds=300,
    description=(
        "Fires when the set of open Hyperliquid positions changes (open / "
        "close / partial fill). Used by reconcile-and-reflect to detect "
        "perceived position changes and trigger reflection."
    ),
)
def poll_hl_position_status_change(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    addr = _get_address()
    if not addr:
        return [], state or {}

    try:
        s = get_info().user_state(addr)
    except Exception as exc:
        logger.warning("hl_position_status_change poll failed: %s", exc)
        return [], state or {}

    current = {}
    for ap in s.get("assetPositions", []):
        pos = ap.get("position", {}) or {}
        szi = float(pos.get("szi") or 0)
        if szi == 0:
            continue
        current[pos.get("coin")] = {
            "szi": szi,
            "entry_px": pos.get("entryPx"),
        }

    prev = (state or {}).get("positions", {})
    fired: List[Dict[str, Any]] = []
    all_keys = set(prev.keys()) | set(current.keys())
    for k in sorted(all_keys):
        if k not in prev and k in current:
            fired.append({
                "alert": "hl_position_status_change",
                "kind": "opened",
                "coin": k,
                "current_szi": current[k]["szi"],
            })
        elif k in prev and k not in current:
            fired.append({
                "alert": "hl_position_status_change",
                "kind": "closed",
                "coin": k,
                "previous_szi": prev[k]["szi"],
            })
        elif k in prev and k in current:
            if abs(prev[k]["szi"] - current[k]["szi"]) > 1e-12:
                fired.append({
                    "alert": "hl_position_status_change",
                    "kind": "size_changed",
                    "coin": k,
                    "previous_szi": prev[k]["szi"],
                    "current_szi": current[k]["szi"],
                })

    return fired, {"positions": current}


@register_alert(
    name="hl_account_balance_change",
    source="hyperliquid",
    throttle_seconds=300,
    description=(
        "Fires when TOTAL equity (equity_usd = spot USDC + perp "
        "accountValue — the unified-account measure, TRADING.md money "
        "glossary) changes by more than 1% or $0.50, whichever is larger. "
        "Surfaces deposits, withdrawals, and material PnL swings. Watching "
        "the perp-side accountValue alone would miss spot deposits and "
        "fire on margin display moves at every open/close."
    ),
)
def poll_hl_account_balance_change(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    addr = _get_address()
    if not addr:
        return [], state or {}
    try:
        from .data_points import equity_breakdown
        current = equity_breakdown(addr)["equity_usd"]
    except Exception as exc:
        logger.warning("hl_account_balance_change poll failed: %s", exc)
        return [], state or {}

    prev = (state or {}).get("equity_usd")
    fired: List[Dict[str, Any]] = []

    if prev is not None:
        delta = current - prev
        threshold_abs = max(0.50, abs(prev) * 0.01)
        if abs(delta) >= threshold_abs:
            fired.append({
                "alert": "hl_account_balance_change",
                "previous_equity_usd": prev,
                "current_equity_usd": current,
                "delta": delta,
            })

    return fired, {"equity_usd": current}


@register_alert(
    name="hl_prediction_resolution",
    source="hyperliquid",
    throttle_seconds=5,
    description=(
        "Event-driven prediction resolution. Every tick: read open price-zone "
        "predictions, fetch all_mids once, and deterministically advance any "
        "whose favorable move reached the far edge (correct early), whose near "
        "edge just locked the win (stamped, stays open), whose horizon expired "
        "(correct if the near edge was reached, else wrong), or whose "
        "invalidation tripped before the near edge (wrong) — writing path stats "
        "and bumping strategy counters. Candles are pulled only at the moment of "
        "resolution. Wakes main ONLY when the prediction backing the OPEN "
        "position resolves; routine paper resolutions and near-locks are silent "
        "(ops reports them on its tick)."
    ),
)
def poll_hl_prediction_resolution(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        from trading.lifecycle import resolver
        from trading.lifecycle.db import get_db
        from trading.dispatchers.resolution import _fetch, _fetch_extreme
        from .outcomes import path_stats

        conn = get_db()
        n_open = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL"
        ).fetchone()[0]
        if not n_open:
            return [], state or {}

        # The prediction backing the open position — the only resolution main
        # needs woken for (take-profit reached, or thesis broken → exit).
        funded = conn.execute(
            """SELECT t.prediction_id FROM theses t
               JOIN decisions d ON d.thesis_id = t.id
               JOIN trades tr ON tr.decision_id = d.id
               JOIN positions p ON p.opening_trade_id = tr.id
               WHERE p.status = 'open' LIMIT 1"""
        ).fetchone()
        funded_pid = funded["prediction_id"] if funded else None

        raw = get_info().all_mids()
        mids = {k: float(v) for k, v in raw.items()}
        res = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=path_stats,
            fetch_fn=_fetch, fetch_extreme_fn=_fetch_extreme)
    except Exception as exc:
        logger.warning("hl_prediction_resolution poll failed: %s", exc)
        return [], state or {}

    fired: List[Dict[str, Any]] = []
    for r in res["resolved"]:
        if r["prediction_id"] == funded_pid:
            fired.append({
                "alert": "hl_prediction_resolution",
                "kind": r["outcome"],            # correct | wrong
                "mode": r["mode"],               # target | horizon | expired | invalidated
                "coin": r["symbol"],
                "prediction_id": r["prediction_id"],
                "strategy_name": r.get("strategy_name"),
                "funded": True,
            })
    return fired, state or {}


def _opening_decision_params(conn, position_id: int) -> Dict[str, Any]:
    """Free-form params on the position's opening decision (sl/tp/alert levels)."""
    row = conn.execute(
        "SELECT d.params_json FROM positions p "
        "JOIN trades t ON t.id = p.opening_trade_id "
        "JOIN decisions d ON d.id = t.decision_id WHERE p.id = ?",
        (position_id,)).fetchone()
    if not row or not row["params_json"]:
        return {}
    try:
        return json.loads(row["params_json"])
    except Exception:
        return {}


@register_alert(
    name="hl_position_alert",
    source="hyperliquid",
    throttle_seconds=5,
    description=(
        "The 4-target judgment triggers for the OPEN position — the two alert "
        "levels INSIDE the mechanical SL/TP bounds. Fires a wake when price "
        "crosses the near edge (alert-up: take profit, or hold for far?) or the "
        "winners'-MAE level (alert-down: normal wobble, or thesis breaking — cut "
        "early before the hard SL?). Each level fires once per position; main "
        "re-scores conviction on the wake and decides (take-profit / cut / hold)."
    ),
)
def poll_hl_position_alert(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        from trading.lifecycle import queries
        from trading.lifecycle.db import get_db

        conn = get_db()
        pos = queries.open_position(conn)
        if pos is None:
            return [], {}        # flat → clear fired state
        params = _opening_decision_params(conn, pos["id"])
        near_px = params.get("alert_near_px")
        adverse_px = params.get("alert_adverse_px")
        if near_px is None and adverse_px is None:
            return [], state or {}
        side, symbol = pos["side"], pos["symbol"]
        price = float(get_info().all_mids()[symbol])
    except Exception as exc:
        logger.warning("hl_position_alert poll failed: %s", exc)
        return [], state or {}

    prev = state or {}
    # Reset the per-level fired flags when the open position changes.
    already = set(prev.get("fired", [])) if prev.get("position_id") == pos["id"] else set()

    def _crossed(level: Optional[float], kind: str) -> bool:
        if level is None:
            return False
        if kind == "near":      # favorable: long tags above entry, short below
            return price >= level if side == "long" else price <= level
        return price <= level if side == "long" else price >= level  # adverse

    fired: List[Dict[str, Any]] = []
    for kind, level in (("near", near_px), ("adverse", adverse_px)):
        if kind in already or not _crossed(level, kind):
            continue
        fired.append({
            "alert": "hl_position_alert", "kind": kind, "coin": symbol,
            "price": price, "level": level, "position_id": pos["id"],
        })
        already.add(kind)

    return fired, {"position_id": pos["id"], "fired": sorted(already)}


@register_alert(
    name="hl_price_range",
    source="hyperliquid",
    throttle_seconds=300,
    description=(
        "Fires when a configured price range is entered for a tracked coin. "
        "Auto-disables after trigger; 30-minute cooldown before re-enable."
    ),
)
def poll_hl_price_range(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Poll for price-range alerts.

    Reads ``watchers/price_alerts.json`` and checks each enabled coin against
    the current Hyperliquid mid price. Returns events for any coin whose price
    is inside its configured range and whose cooldown has elapsed.
    """
    try:
        mids = get_info().all_mids()
    except Exception as exc:
        logger.warning("hl_price_range poll failed: %s", exc)
        return [], state or {}

    fired: List[Dict[str, Any]] = []
    for coin, raw_price in mids.items():
        try:
            price = float(raw_price)
        except (ValueError, TypeError):
            continue
        if should_fire(coin, price):
            fired.append({
                "alert": "hl_price_range",
                "kind": "in_range",
                "coin": coin,
                "price": price,
            })
            auto_disable_after_fire(coin)

    return fired, state or {}
