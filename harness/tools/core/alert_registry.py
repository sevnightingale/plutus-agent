"""Alert registry — declarative catalog of conditions watched by the daemon.

An *alert* is a condition the watcher daemon polls for and, when it fires,
triggers a Plutus session via the cron/trigger infrastructure. Plutus does
not invoke alerts itself — they are observed.

Examples: position_status_change, price_threshold_breach, funding_spike,
account_balance_change. Integrations contribute alert entries via
``@register_alert``; the watcher daemon enumerates them on startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


class RegistryError(RuntimeError):
    """Raised on registry collisions or invalid registrations."""


@dataclass(frozen=True)
class AlertEntry:
    name: str
    source: str                     # 'hyperliquid' | 'acp' | 'dgclaw' | ...
    poll_fn: Callable[..., Any]     # called periodically; returns list of fired alert events
    throttle_seconds: int = 60      # minimum between successive firings of the same alert+key
    description: str = ""


_REGISTRY: Dict[str, AlertEntry] = {}


def register_alert(
    *,
    name: str,
    source: str,
    throttle_seconds: int = 60,
    description: str = "",
):
    """Decorator: register a function as an alert poller.

    Usage:
        @register_alert(
            name="position_status_change",
            source="hyperliquid",
            throttle_seconds=30,
            description="Fires when a tracked HL position opens, closes, or partial-fills.",
        )
        def poll_position_changes() -> list[dict]:
            ...

    The poll function returns a list of "fired" event dicts; each dict's
    shape is alert-defined and is consumed by the watcher daemon's trigger
    code (which posts a wake event to the agent).
    """
    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise RegistryError(f"Alert '{name}' already registered")
        _REGISTRY[name] = AlertEntry(
            name=name,
            source=source,
            poll_fn=fn,
            throttle_seconds=throttle_seconds,
            description=description,
        )
        return fn
    return _decorator


def lookup(name: str) -> AlertEntry:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"alert '{name}' not registered") from None


def list_all(source: Optional[str] = None) -> List[AlertEntry]:
    out = list(_REGISTRY.values())
    if source is not None:
        out = [a for a in out if a.source == source]
    return sorted(out, key=lambda a: a.name)


def reset() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
