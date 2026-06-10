"""Identity-system registry — minimal placeholder catalog.

An *identity system* is an external account/identity layer (ACP today, others
later) that Plutus interacts with. Identity-specific operations (whoami,
signer_add, agent_create) are too system-specific to dispatch through a
common tool; they live as direct tools in ``tools/integrations/<name>/identity.py``
and are toolset-gated. This registry just records that a system exists, so
``list_identity_systems()`` can enumerate them at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


class RegistryError(RuntimeError):
    """Raised on registry collisions."""


@dataclass(frozen=True)
class IdentitySystemEntry:
    name: str
    description: str


_REGISTRY: Dict[str, IdentitySystemEntry] = {}


def register_identity_system(*, name: str, description: str) -> IdentitySystemEntry:
    """Register an identity system by name + short description."""
    if name in _REGISTRY:
        raise RegistryError(f"Identity system '{name}' already registered")
    entry = IdentitySystemEntry(name=name, description=description)
    _REGISTRY[name] = entry
    return entry


def lookup(name: str) -> IdentitySystemEntry:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"identity system '{name}' not registered") from None


def list_all() -> List[IdentitySystemEntry]:
    return sorted(_REGISTRY.values(), key=lambda e: e.name)


def reset() -> None:
    """Test-only: clear the registry."""
    _REGISTRY.clear()
