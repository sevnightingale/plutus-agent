"""Register Hyperliquid accounts.

Currently one: ``hl_trading`` — Plutus's primary trading account on
Hyperliquid (perps + spot, unified). Address pulled from
``ACP_AGENT_WALLET`` env var; may be empty at registration time and
lazy-resolved by ``_client.resolve_account_address`` on each call.
"""

from __future__ import annotations

import logging
import os

from trading.perception.core.account_registry import register_account, RegistryError

logger = logging.getLogger(__name__)


try:
    register_account(
        name="hl_trading",
        purpose="trading_capital",
        venue="hyperliquid",
        chain="hyperliquid",
        address=os.getenv("ACP_AGENT_WALLET") or None,
        description="Plutus's Hyperliquid trading account (perps + spot, unified).",
    )
except RegistryError:
    # Re-import in tests; ignore if already registered.
    logger.debug("hl_trading already registered, skipping")
