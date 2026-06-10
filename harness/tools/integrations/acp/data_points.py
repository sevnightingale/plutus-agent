"""ACP data points — wallet balance, offerings discovery, chain list.

Verified against `acp <subcommand> --help` (acp-cli v1.0.5):
- `acp wallet balance` REQUIRES `--chain-id` (no default)
- `acp browse` uses `--chain-ids` (plural; comma-separated)
- `acp chain list` takes no flags
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from tools.core.data_point_registry import register_data_point

from . import _cli

logger = logging.getLogger(__name__)


# Base mainnet — the default chain for ACP agents and the network where
# the dgclaw + Plutus wallet live. Override per call when querying other
# chains.
DEFAULT_CHAIN_ID = "8453"


@register_data_point(
    name="acp_wallet_balance",
    category="account",
    source="acp",
    description=(
        "Token balances on Plutus's ACP wallet on a specific chain. "
        "Defaults to Base mainnet (8453) where the dgclaw + Plutus agent "
        "wallet live; pass chain_id to query other chains."
    ),
    params_schema={
        "chain_id": {"type": "string", "description": "Chain id; default 8453 (Base)."},
    },
    returns_schema={"balances": "list of {symbol, amount, decimals, ...}"},
    tags=["account", "balance", "acp", "wallet"],
)
def acp_wallet_balance(chain_id: Optional[str] = None) -> Dict[str, Any]:
    cid = str(chain_id or DEFAULT_CHAIN_ID)
    return _cli.acp("wallet", "balance", "--chain-id", cid)


@register_data_point(
    name="acp_browse_offerings",
    category="market",
    source="acp",
    description=(
        "Discover other ACP agents and their offerings (services Plutus "
        "could request via the ACP commerce surface). Useful for sourcing "
        "data, tools, or services that don't have a native integration."
    ),
    params_schema={
        "query":     {"type": "string", "required": True},
        "top_k":     {"type": "integer", "default": 20},
        "sort_by":   {"type": "string",
                      "description": "Comma-separated: successfulJobCount, successRate, uniqueBuyerCount, minsFromLastOnlineTime."},
        "chain_ids": {"type": "string",
                      "description": "Comma-separated chain ids to filter by; default 8453 (Base)."},
        "online":    {"type": "string", "description": "all | online | offline"},
        "legacy":    {"type": "boolean", "description": "Search legacy (openclaw-cli) agents instead of v2."},
    },
    returns_schema={"agents": "list of agent metadata"},
    tags=["market", "discovery", "acp"],
)
def acp_browse_offerings(query: str,
                         top_k: int = 20,
                         sort_by: Optional[str] = None,
                         chain_ids: Optional[str] = None,
                         online: Optional[str] = None,
                         legacy: bool = False) -> Dict[str, Any]:
    args = ["browse", str(query), "--top-k", str(top_k)]
    args.extend(["--chain-ids", chain_ids or DEFAULT_CHAIN_ID])
    if sort_by:
        args.extend(["--sort-by", sort_by])
    if online:
        args.extend(["--online", online])
    if legacy:
        args.append("--legacy")
    return _cli.acp(*args)


@register_data_point(
    name="acp_chain_list",
    category="account",
    source="acp",
    description="List the chains supported by ACP.",
    params_schema={},
    returns_schema={"chains": "list of chain metadata"},
    tags=["account", "chains", "acp"],
)
def acp_chain_list() -> Dict[str, Any]:
    return _cli.acp("chain", "list")
