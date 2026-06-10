"""Register ACP wallet accounts.

On import: if ACP is configured (the wallet binary is on PATH AND
``acp wallet address --json`` succeeds), discover the wallet address(es)
across chains and register one ``acp_wallet_<chain>`` account per chain
with ``purpose="treasury"``. If ACP isn't set up yet, no-op (registration
will succeed on the next reload after the operator runs setup).

This deliberately avoids loud-failing at import time: missing ACP setup
during early launch is normal; setup happens AFTER the daemon is running.
"""

from __future__ import annotations

import logging

from harness.tools.core.account_registry import register_account, RegistryError

from . import _cli

logger = logging.getLogger(__name__)


def _discover_and_register() -> None:
    if not _cli.is_installed():
        logger.debug("acp not installed; skipping ACP account registration")
        return
    try:
        result = _cli.acp("wallet", "address")
    except _cli.ACPCLIError as exc:
        logger.debug("acp wallet address failed (likely not configured): %s", exc)
        return

    addresses = result.get("addresses") or result.get("wallets") or []
    if isinstance(addresses, dict):
        # API may return {chain_id: addr, ...}
        addresses = [{"chain_id": k, "address": v} for k, v in addresses.items()]

    for entry in addresses:
        chain = str(entry.get("chain_id") or entry.get("chain") or "ethereum").strip()
        addr = entry.get("address")
        name = f"acp_wallet_{chain}"
        try:
            register_account(
                name=name,
                purpose="treasury",
                chain=chain,
                address=addr,
                description=f"Plutus's ACP wallet on chain {chain}",
            )
            logger.info("registered ACP account %s (addr=%s)", name, addr)
        except RegistryError:
            logger.debug("acp account %s already registered, skipping", name)


_discover_and_register()
