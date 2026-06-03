"""Venue registry — catalog of exchanges/places where Plutus can execute.

A *venue* is anywhere orders can be placed and positions held: Hyperliquid
today; Bybit/dYdX/Binance later if added by community integrations. Each
venue contributes the small set of execution functions the dispatchers call.

Integrations register via ``@register_venue``. Dispatchers
(``place_order(venue, ...)``, ``close_position(venue, ...)``, etc.) look up
the venue and call the appropriate function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


class RegistryError(RuntimeError):
    """Raised on registry collisions or invalid registrations."""


@dataclass(frozen=True)
class VenueEntry:
    name: str
    description: str
    place_order_fn: Optional[Callable[..., Any]] = None
    close_position_fn: Optional[Callable[..., Any]] = None
    modify_order_fn: Optional[Callable[..., Any]] = None
    cancel_order_fn: Optional[Callable[..., Any]] = None
    account_state_fn: Optional[Callable[..., Any]] = None
    # Places a standalone reduce-only trigger (SL or TP) on an existing
    # position. Used as a fallback when the atomic-bracket path on the
    # entry order fails (e.g. validation rejects TP due to inflated
    # slippage estimate) or to add a trigger to a position that was
    # opened without sl/tp params.
    place_trigger_fn: Optional[Callable[..., Any]] = None
    # Enriches the outcome row with venue-specific math (PnL, MAE/MFE,
    # r_multiple, efficiencies, slippage). Called by the close_position
    # dispatcher AFTER the shell row is written, OUTSIDE the DB
    # transaction so network calls don't hold the SQLite lock. Returns
    # a dict of column→value to UPDATE on outcomes for that position_id;
    # missing columns are left as the shell defaults.
    outcome_compute_fn: Optional[Callable[..., Any]] = None


_REGISTRY: Dict[str, VenueEntry] = {}


def register_venue(
    *,
    name: str,
    description: str,
    place_order_fn: Optional[Callable[..., Any]] = None,
    close_position_fn: Optional[Callable[..., Any]] = None,
    modify_order_fn: Optional[Callable[..., Any]] = None,
    cancel_order_fn: Optional[Callable[..., Any]] = None,
    account_state_fn: Optional[Callable[..., Any]] = None,
    place_trigger_fn: Optional[Callable[..., Any]] = None,
    outcome_compute_fn: Optional[Callable[..., Any]] = None,
) -> VenueEntry:
    """Register a venue with its execution callables.

    Unlike data point / event registrations this is not used as a function
    decorator — venues bundle multiple functions, so the registration call
    sits next to them in the integration module.
    """
    if name in _REGISTRY:
        raise RegistryError(f"Venue '{name}' already registered")
    entry = VenueEntry(
        name=name,
        description=description,
        place_order_fn=place_order_fn,
        close_position_fn=close_position_fn,
        modify_order_fn=modify_order_fn,
        cancel_order_fn=cancel_order_fn,
        account_state_fn=account_state_fn,
        place_trigger_fn=place_trigger_fn,
        outcome_compute_fn=outcome_compute_fn,
    )
    _REGISTRY[name] = entry
    return entry


def lookup(name: str) -> VenueEntry:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"venue '{name}' not registered") from None


def list_all() -> List[VenueEntry]:
    return sorted(_REGISTRY.values(), key=lambda v: v.name)


def reset() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
