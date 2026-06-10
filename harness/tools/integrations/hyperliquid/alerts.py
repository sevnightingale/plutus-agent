"""Hyperliquid alerts polled by the watcher daemon.

Each poll returns a list of *fired event* dicts. The watcher daemon
emits each as a wake event into ``~/.plutus-agent/wake_events.ndjson``;
the gateway tails that file and turns wake events into messages routed
to Plutus.

State (last-seen position set, last-seen account value) lives in
``watchers/state.py`` (``~/.plutus-agent/watcher_state.json``). The
poll fn is given the previous state in ``state`` and returns a tuple
``(events, new_state)`` so the watcher can persist on success.

We register two alerts in v1: position-status-change and
account-balance-change. More alerts (price-threshold-breach,
funding-spike, etc.) Plutus can author later as it learns what's
worth watching.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from tools.core.alert_registry import register_alert

from watchers.price_alerts import (
    auto_disable_after_fire,
    should_fire,
)

from ._client import get_info, HLConfigError

logger = logging.getLogger(__name__)


def _get_address() -> Optional[str]:
    return os.getenv("HL_PUBLIC_ADDRESS") or None


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
        "Fires when the Hyperliquid account value changes by more than 1% "
        "or $0.50, whichever is larger. Used to surface deposits, "
        "withdrawals, and material PnL swings."
    ),
)
def poll_hl_account_balance_change(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    addr = _get_address()
    if not addr:
        return [], state or {}
    try:
        s = get_info().user_state(addr)
    except Exception as exc:
        logger.warning("hl_account_balance_change poll failed: %s", exc)
        return [], state or {}

    current = float(s.get("marginSummary", {}).get("accountValue", 0))
    prev = (state or {}).get("account_value")
    fired: List[Dict[str, Any]] = []

    if prev is not None:
        delta = current - prev
        threshold_abs = max(0.50, abs(prev) * 0.01)
        if abs(delta) >= threshold_abs:
            fired.append({
                "alert": "hl_account_balance_change",
                "previous_account_value": prev,
                "current_account_value": current,
                "delta": delta,
            })

    return fired, {"account_value": current}


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
