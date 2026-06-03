"""Account registry — catalog of value-holding entities.

An *account* is anywhere Plutus can hold (or has previously held) value:
the Hyperliquid trading account, the ACP main wallet on a given chain,
cold storage, staking positions, LP positions. Accounts are declarative —
holdings/balances are fetched as data points; capital movements are
recorded as events.

Each account has a ``purpose`` from a small taxonomy so Plutus can reason
about which account is for what (treasury vs trading capital vs spot
holding) without hard-coding integration-specific names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


VALID_PURPOSES = frozenset({
    "trading_capital",   # active risk capital (e.g., HL trading account)
    "treasury",          # operating reserve, off-venue
    "spot_holding",      # bought-and-held spot positions
    "staking",           # locked / earning staking rewards
    "lp",                # liquidity provision
    "cold_storage",      # offline / multi-sig long-term storage
})


class RegistryError(RuntimeError):
    """Raised on registry collisions or invalid registrations."""


@dataclass(frozen=True)
class AccountEntry:
    name: str
    purpose: str         # one of VALID_PURPOSES
    venue: Optional[str] = None     # tied to a venue (HL trading account) or None (ACP wallet)
    chain: Optional[str] = None     # ethereum, base, solana, ... (for on-chain wallets)
    address: Optional[str] = None   # public address if applicable
    description: str = ""


_REGISTRY: Dict[str, AccountEntry] = {}


def register_account(
    *,
    name: str,
    purpose: str,
    venue: Optional[str] = None,
    chain: Optional[str] = None,
    address: Optional[str] = None,
    description: str = "",
) -> AccountEntry:
    """Register an account with its purpose + optional venue/chain/address."""
    if purpose not in VALID_PURPOSES:
        raise RegistryError(
            f"Invalid purpose '{purpose}'; must be one of {sorted(VALID_PURPOSES)}"
        )
    if name in _REGISTRY:
        raise RegistryError(f"Account '{name}' already registered")
    entry = AccountEntry(
        name=name,
        purpose=purpose,
        venue=venue,
        chain=chain,
        address=address,
        description=description,
    )
    _REGISTRY[name] = entry
    return entry


def lookup(name: str) -> AccountEntry:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"account '{name}' not registered") from None


def list_all(
    purpose: Optional[str] = None,
    venue: Optional[str] = None,
    chain: Optional[str] = None,
) -> List[AccountEntry]:
    """Return registered accounts, optionally filtered."""
    out = list(_REGISTRY.values())
    if purpose is not None:
        out = [a for a in out if a.purpose == purpose]
    if venue is not None:
        out = [a for a in out if a.venue == venue]
    if chain is not None:
        out = [a for a in out if a.chain == chain]
    return sorted(out, key=lambda a: a.name)


def reset() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
