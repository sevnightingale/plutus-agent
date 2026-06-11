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
    # Dotted path to THE numeric reading inside the return value (e.g.
    # "price", "current.value", "funding"). A data point with a numeric_path
    # is machine-resolvable: prediction criteria may reference it, and ops
    # resolution extracts the number deterministically. Without one, the
    # data point is perception-only — register_prediction refuses criteria
    # leaves on it AT WRITE TIME (never a silent expired_unresolvable later).
    numeric_path: Optional[str] = None


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
    numeric_path: Optional[str] = None,
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
            numeric_path=numeric_path,
        )
        return fn
    return _decorator


def extract_numeric(value: Any, numeric_path: Optional[str]) -> Optional[float]:
    """Extract THE numeric reading from a fetch result via its numeric_path.

    A bare int/float result is returned as-is (path not needed). Dict results
    are traversed along the dotted path. Returns None when the path is absent,
    the traversal fails, or the leaf isn't a number — callers treat None as
    'unresolvable', never as a value.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not numeric_path or not isinstance(value, dict):
        return None
    node: Any = value
    for part in numeric_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return float(node)


def resolvable_names() -> set:
    """Names of data points usable as prediction-criteria leaves."""
    return {e.name for e in _REGISTRY.values() if e.numeric_path}


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
