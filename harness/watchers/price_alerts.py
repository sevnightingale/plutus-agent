"""Price alert registry — standalone JSON file for per-symbol price ranges.

Used by the watcher daemon's ``hl_price_range`` alert. Each entry declares
a coin, a low/high range, and whether the alert is currently enabled.
Alerts auto-disable after firing, and respect a per-symbol cooldown so
they don't spam when price stays inside the range.

Storage:
    ~/.plutus-agent/price_alerts.json
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

_REGISTRY_PATH: Path = get_hermes_home() / "price_alerts.json"


def _default_entry() -> Dict[str, Any]:
    return {
        "enabled": False,
        "low": 0.0,
        "high": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_fired_at": None,
        "auto_disable": True,
        "cooldown_min": 30,
    }


def load_registry() -> Dict[str, Dict[str, Any]]:
    """Return the full price alert registry dict keyed by coin."""
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with _LOCK:
            return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("price_alerts registry corrupt at %s: %s — resetting", _REGISTRY_PATH, exc)
        return {}


def save_registry(registry: Dict[str, Dict[str, Any]]) -> None:
    """Persist the registry to disk atomically."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _REGISTRY_PATH.with_suffix(".json.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        os.replace(tmp, _REGISTRY_PATH)


def get_alert(coin: str) -> Optional[Dict[str, Any]]:
    """Return a single alert entry, or None if not configured."""
    registry = load_registry()
    return registry.get(coin.upper())


def set_alert(coin: str, low: float, high: float, enabled: bool = True, cooldown_min: int = 30) -> Dict[str, Any]:
    """Add or update a price alert for ``coin``."""
    registry = load_registry()
    coin = coin.upper()
    entry = registry.get(coin, _default_entry())
    entry["low"] = float(low)
    entry["high"] = float(high)
    entry["enabled"] = bool(enabled)
    entry["cooldown_min"] = int(cooldown_min)
    entry["created_at"] = datetime.now(timezone.utc).isoformat()
    # Don't reset last_fired_at or auto_disable on an update
    entry.setdefault("auto_disable", True)
    registry[coin] = entry
    save_registry(registry)
    logger.info("Price alert set: %s [%.2f, %.2f] enabled=%s", coin, low, high, enabled)
    return entry


def remove_alert(coin: str) -> bool:
    """Remove a price alert for ``coin``. Returns True if it existed."""
    registry = load_registry()
    coin = coin.upper()
    if coin in registry:
        del registry[coin]
        save_registry(registry)
        logger.info("Price alert removed: %s", coin)
        return True
    return False


def enable_alert(coin: str) -> Optional[Dict[str, Any]]:
    """Re-enable an alert after it was auto-disabled."""
    registry = load_registry()
    coin = coin.upper()
    entry = registry.get(coin)
    if not entry:
        return None
    entry["enabled"] = True
    save_registry(registry)
    logger.info("Price alert enabled: %s", coin)
    return entry


def disable_alert(coin: str) -> Optional[Dict[str, Any]]:
    """Manually disable an alert."""
    registry = load_registry()
    coin = coin.upper()
    entry = registry.get(coin)
    if not entry:
        return None
    entry["enabled"] = False
    save_registry(registry)
    logger.info("Price alert disabled: %s", coin)
    return entry


def auto_disable_after_fire(coin: str) -> None:
    """Auto-disable an alert immediately after it fires."""
    registry = load_registry()
    coin = coin.upper()
    entry = registry.get(coin)
    if not entry:
        return
    if entry.get("auto_disable", True):
        entry["enabled"] = False
    entry["last_fired_at"] = datetime.now(timezone.utc).isoformat()
    save_registry(registry)
    logger.info("Price alert auto-disabled after fire: %s", coin)


def _minutes_since(dt_iso: Optional[str]) -> float:
    """Return minutes since an ISO datetime string, or inf if missing."""
    if not dt_iso:
        return float("inf")
    try:
        dt = datetime.fromisoformat(dt_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return float("inf")


def should_fire(coin: str, price: float) -> bool:
    """Check whether an alert should fire given a current price.

    Criteria:
    - Alert exists and is enabled
    - Price is within [low, high] range
    - Per-symbol cooldown has elapsed since last_fired_at
    """
    entry = get_alert(coin)
    if not entry:
        return False
    if not entry.get("enabled"):
        return False
    low = float(entry.get("low", 0))
    high = float(entry.get("high", 0))
    if not (low <= price <= high):
        return False
    cooldown = int(entry.get("cooldown_min", 30))
    last_fired = entry.get("last_fired_at")
    if _minutes_since(last_fired) < cooldown:
        return False
    return True


def list_alerts() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the entire registry."""
    return load_registry()
