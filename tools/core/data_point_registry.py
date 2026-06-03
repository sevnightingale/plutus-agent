"""Data point registry — catalog of fetchable values (prices, funding, OI, ...).

A *data point* is anything Plutus can ask for that yields a value: a price,
a funding rate, an indicator, an on-chain stat, a leaderboard rank, a wallet
balance. Every fetch is auto-snapshotted to ``data_point_snapshots`` so the
agent's perception history is captured for free.

Integrations register entries via the ``@register_data_point`` decorator at
module load time. The agent dispatches via ``fetch_data_point(name, **params)``
(see ``tools/dispatchers/fetch_data_point.py``) and discovers via
``list_data_points()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class RegistryError(RuntimeError):
    """Raised on registry collisions or invalid registrations."""


@dataclass(frozen=True)
class DataPointEntry:
    name: str
    category: str            # 'market' | 'on_chain' | 'social' | 'account' | 'derived' | ...
    source: str              # 'hyperliquid' | 'acp' | 'dgclaw' | 'coingecko' | ...
    description: str
    params_schema: Dict[str, Any]
    returns_schema: Dict[str, Any]
    tags: tuple = field(default_factory=tuple)
    fn: Optional[Callable[..., Any]] = None


_REGISTRY: Dict[str, DataPointEntry] = {}


def register_data_point(
    *,
    name: str,
    category: str,
    source: str,
    description: str,
    params_schema: Optional[Dict[str, Any]] = None,
    returns_schema: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
):
    """Decorator: register a function as a data-point fetcher.

    Usage:
        @register_data_point(
            name="hl_funding_rate",
            category="market",
            source="hyperliquid",
            description="Current funding rate for a perp.",
            params_schema={"symbol": {"type": "string", "required": True}},
            returns_schema={"rate": "float", "next_funding_at": "iso8601"},
        )
        def get_funding_rate(symbol: str) -> dict: ...
    """
    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise RegistryError(
                f"Data point '{name}' already registered "
                f"(existing source={_REGISTRY[name].source})"
            )
        _REGISTRY[name] = DataPointEntry(
            name=name,
            category=category,
            source=source,
            description=description,
            params_schema=params_schema or {},
            returns_schema=returns_schema or {},
            tags=tuple(tags or ()),
            fn=fn,
        )
        return fn
    return _decorator


def lookup(name: str) -> DataPointEntry:
    """Return the entry for ``name``; raise KeyError if absent."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"data_point '{name}' not registered") from None


def list_all(
    category: Optional[str] = None,
    source: Optional[str] = None,
) -> List[DataPointEntry]:
    """Return registered entries, optionally filtered by category/source."""
    out = list(_REGISTRY.values())
    if category is not None:
        out = [e for e in out if e.category == category]
    if source is not None:
        out = [e for e in out if e.source == source]
    return sorted(out, key=lambda e: e.name)


def reset() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
