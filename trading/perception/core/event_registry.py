"""Event-type registry — catalog of recordable lifecycle events.

Events are append-only writes to ``lifecycle.db``: thesis, decision, reflection,
position_evaluation, position_close_perceived, capital_movement, strategy_open,
strategy_pause, strategy_retire, watchlist_update, learning, etc.

Integrations register via the ``@register_event`` decorator at module load.
The agent dispatches via ``record_event(type, **params)`` (see
``tools/dispatchers/record_event.py``) which calls the registered handler.
For event types whose handler writes to a vector-indexed table (theses,
reflections), the dispatcher embeds + writes the vec0 row in the same
transaction as the row itself — atomicity is the dispatcher's responsibility,
not the registry's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


class RegistryError(RuntimeError):
    """Raised on registry collisions or invalid registrations."""


@dataclass(frozen=True)
class EventEntry:
    name: str
    description: str
    fields_schema: Dict[str, Any]
    fn: Callable[..., Any]


_REGISTRY: Dict[str, EventEntry] = {}


def register_event(
    *,
    name: str,
    description: str,
    fields_schema: Optional[Dict[str, Any]] = None,
):
    """Decorator: register a function as an event-type handler.

    Usage:
        @register_event(
            name="thesis",
            description="A market thesis with linked snapshot ids.",
            fields_schema={
                "text": {"type": "string", "required": True},
                "symbol": {"type": "string", "required": False},
                "snapshot_ids": {"type": "array", "required": True, "default": []},
            },
        )
        def record_thesis(session_id, text, symbol=None, snapshot_ids=None, ...):
            ...
    """
    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise RegistryError(f"Event type '{name}' already registered")
        _REGISTRY[name] = EventEntry(
            name=name,
            description=description,
            fields_schema=fields_schema or {},
            fn=fn,
        )
        return fn
    return _decorator


def lookup(name: str) -> EventEntry:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"event type '{name}' not registered") from None


def list_all() -> List[EventEntry]:
    return sorted(_REGISTRY.values(), key=lambda e: e.name)


def reset() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
