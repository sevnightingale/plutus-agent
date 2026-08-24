"""Singletons + helpers for the Hyperliquid SDK.

We hold a single ``Info`` and a single ``Exchange`` per process. ``Info``
is constructed eagerly (no credentials required); ``Exchange`` is
constructed lazily on first call so a missing ``HL_API_WALLET_KEY``
doesn't crash the import — execution tools loud-fail at call time
instead, with an actionable message.

Address resolution: every account-state data point takes an
``account_name`` argument. We look it up in the account registry; if
the registry entry's address is empty (e.g. registration ran before
``ACP_AGENT_WALLET`` was set in ``.env``) we fall back to the env var
on each call, so a single ``pm2 restart`` after the wallet is generated
is enough to bring everything online.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from trading.perception.core.account_registry import lookup as lookup_account

logger = logging.getLogger(__name__)


_INFO_LOCK = threading.Lock()
_EXCHANGE_LOCK = threading.Lock()
_info: Optional[Info] = None
_exchange: Optional[Exchange] = None
_exchange_addr: Optional[str] = None


_INTERVAL_MS = {
    "1m":   60_000,
    "3m":   3 * 60_000,
    "5m":   5 * 60_000,
    "15m":  15 * 60_000,
    "30m":  30 * 60_000,
    "1h":   60 * 60_000,
    "2h":   2 * 60 * 60_000,
    "4h":   4 * 60 * 60_000,
    "8h":   8 * 60 * 60_000,
    "12h":  12 * 60 * 60_000,
    "1d":   24 * 60 * 60_000,
    "3d":   3 * 24 * 60 * 60_000,
    "1w":   7 * 24 * 60 * 60_000,
}


class HLConfigError(RuntimeError):
    """Raised when required credentials/configuration are missing."""


def _configured_perp_dexs() -> list:
    """Builder dexes to map at client construction (config ``trading.perp_dexs``).

    Empty on a fresh install — the client then behaves exactly as before
    (main dex only, no discovery round-trip).
    """
    try:
        from harness.cli.config import load_config
        dexs = ((load_config().get("trading") or {}).get("perp_dexs")) or []
        return [str(d).strip() for d in dexs if str(d).strip()]
    except Exception:
        return []


def dex_of(symbol: str) -> str:
    """The builder dex a symbol belongs to: 'xyz:GOLD' → 'xyz'; else ''."""
    return symbol.split(":", 1)[0] if ":" in symbol else ""


def get_info() -> Info:
    """Return the singleton ``Info`` client.

    ``skip_ws=True`` is mandatory in daemon contexts — the constructor
    otherwise spawns a WebSocket thread per process which keeps the
    interpreter from exiting on shutdown.

    Builder dexes named in config ``trading.perp_dexs`` are passed at
    construction so the SDK's name/asset maps cover dex-qualified symbols
    ("xyz:GOLD") — without this, ``name_to_coin`` KeyErrors on every
    builder-dex data call. Non-empty config costs two extra HTTP calls at
    construction (dex discovery + per-dex meta), once per process.
    """
    global _info
    if _info is None:
        with _INFO_LOCK:
            if _info is None:
                dexs = _configured_perp_dexs()
                _info = Info(
                    constants.MAINNET_API_URL, skip_ws=True,
                    perp_dexs=([""] + dexs) if dexs else None,
                )
                logger.debug(
                    "Hyperliquid Info client initialised (mainnet, dexs=%s)",
                    dexs or "main-only")
    return _info


def meta_and_ctxs(dex: str = ""):
    """``metaAndAssetCtxs`` for any dex.

    The SDK's ``meta_and_asset_ctxs()`` covers only the main dex; builder
    dexes need the raw request. Both shapes return ``[meta, ctxs]``.
    """
    info = get_info()
    if not dex:
        return info.meta_and_asset_ctxs()
    return info.post("/info", {"type": "metaAndAssetCtxs", "dex": dex})


def get_exchange() -> Exchange:
    """Return the singleton ``Exchange`` client; loud-fail without ``HL_API_WALLET_KEY``."""
    global _exchange, _exchange_addr
    key = os.getenv("HL_API_WALLET_KEY")
    if not key:
        raise HLConfigError(
            "HL_API_WALLET_KEY is not set in ~/.plutus-agent/.env. "
            "Generate + register an API wallet via the dgclaw skill's "
            "add-api-wallet.ts (see SETUP.md / TRADING.md), then "
            "`pm2 restart plutus-gateway`."
        )
    if _exchange is not None and _exchange_addr == key:
        return _exchange

    with _EXCHANGE_LOCK:
        if _exchange is not None and _exchange_addr == key:
            return _exchange
        try:
            from eth_account import Account
        except ImportError as exc:  # pragma: no cover — eth_account is a SDK transitive dep
            raise HLConfigError(f"eth_account not available: {exc}") from exc

        wallet = Account.from_key(key)
        master_address = os.getenv("ACP_AGENT_WALLET") or None
        # Same perp_dexs mirroring as get_info(): without it the Exchange's
        # coin map covers only the main dex and every dex-qualified order
        # ("xyz:GOLD") KeyErrors at signing — perceivable but untradeable
        # (cost two pilot entries, 2026-08-23/24).
        dexs = _configured_perp_dexs()
        _exchange = Exchange(
            wallet,
            constants.MAINNET_API_URL,
            account_address=master_address,
            perp_dexs=([""] + dexs) if dexs else None,
        )
        _exchange_addr = key
        logger.info(
            "Hyperliquid Exchange client initialised (signer=%s, account=%s)",
            wallet.address,
            master_address or wallet.address,
        )
    return _exchange


def resolve_account_address(account_name: str) -> str:
    """Return the on-chain address for ``account_name``.

    Reads from the account registry; falls back to ``ACP_AGENT_WALLET``
    env var when the registry entry has no address (the registration
    runs eagerly at import time, possibly before ``.env`` is populated).
    """
    try:
        entry = lookup_account(account_name)
    except KeyError:
        entry = None
    if entry and entry.address:
        return entry.address
    addr = os.getenv("ACP_AGENT_WALLET")
    if not addr:
        raise HLConfigError(
            f"No address known for account '{account_name}'. "
            f"Set ACP_AGENT_WALLET in ~/.plutus-agent/.env or "
            f"register a Hyperliquid account with a non-empty address."
        )
    return addr


def interval_to_ms(interval: str) -> int:
    """Translate a Hyperliquid candle interval string to milliseconds."""
    if interval not in _INTERVAL_MS:
        raise ValueError(
            f"Unknown candle interval '{interval}'. Valid: "
            f"{sorted(_INTERVAL_MS.keys())}"
        )
    return _INTERVAL_MS[interval]


def reset_singletons_for_tests() -> None:
    """Wipe singletons. Tests use this between fixtures."""
    global _info, _exchange, _exchange_addr
    _info = None
    _exchange = None
    _exchange_addr = None
