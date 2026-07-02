"""ACP data points — wallet balance, offerings discovery, chain list, auth.

Verified against `acp <subcommand> --help` (acp-cli v1.0.5):
- `acp wallet balance` REQUIRES `--chain-id` (no default)
- `acp browse` uses `--chain-ids` (plural; comma-separated)
- `acp chain list` takes no flags
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading.perception.core.data_point_registry import register_data_point

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


# ───────────────────────────────────────────────────────────────────────────
# ACP auth readiness — the identity system's hl_trade_readiness
# ───────────────────────────────────────────────────────────────────────────

AUTH_WARN_DAYS = 45
AUTH_CRITICAL_DAYS = 60
AUTH_STATE_FILENAME = "acp_auth_state.json"


def _acp_config_path() -> Path:
    return Path("~/.config/acp/config.json").expanduser()


def _auth_state_path() -> Path:
    from harness.constants import get_hermes_home
    return get_hermes_home() / AUTH_STATE_FILENAME


@register_data_point(
    name="acp_auth_readiness",
    category="account",
    source="acp",
    description=(
        "Liveness + age of the ACP CLI's OAuth session — the identity "
        "system's analogue of hl_trade_readiness. Runs `acp agent whoami` "
        "live and ages the last refresh (config.json mtime; self-healing "
        "state file catches out-of-band re-auths). alive=false or "
        "critical=true → the operator must run `acp configure` (never the "
        "desk). Computed per call, never persisted as a report."
    ),
    params_schema={},
    returns_schema={
        "alive": "bool — `acp agent whoami` succeeded",
        "days_since_refresh": "float|null — age of the OAuth session",
        "warn_reauth_soon": f"bool — age ≥ {AUTH_WARN_DAYS}d",
        "critical": f"bool — age ≥ {AUTH_CRITICAL_DAYS}d",
        "reason": "human-readable verdict",
    },
    tags=["account", "acp", "auth", "readiness", "watchdog"],
)
def acp_auth_readiness() -> Dict[str, Any]:
    """Return the auth verdict dict. Never raises; failures are encoded."""
    result: Dict[str, Any] = {
        "alive": False,
        "reason": "",
        "days_since_refresh": None,
        "refreshed_at_epoch": None,
        "refreshed_at_iso": None,
        "warn_reauth_soon": False,
        "critical": False,
    }

    cfg = _acp_config_path()
    if not cfg.exists():
        result["reason"] = (
            "~/.config/acp/config.json missing — the acp CLI has never been "
            "configured on this machine. Operator must run `acp configure`.")
        return result
    cfg_mtime = cfg.stat().st_mtime

    # Last refresh the desk knows about. Self-healing: a config.json newer
    # than the recorded epoch means the operator re-authed out of band —
    # record it silently instead of nagging about a refresh that happened.
    state_path = _auth_state_path()
    recorded: Optional[float] = None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        recorded = float(state.get("acp_auth_refreshed_at_epoch") or 0) or None
    except Exception:
        pass
    refreshed = max(recorded or 0.0, cfg_mtime)
    if recorded is None or cfg_mtime > recorded:
        try:
            state_path.write_text(json.dumps({
                "acp_auth_refreshed_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(refreshed)),
                "acp_auth_refreshed_at_epoch": refreshed,
                "notes": ("auto-updated by acp_auth_readiness — "
                          "config.json mtime advanced past the recorded epoch"),
            }, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            logger.warning("acp_auth_readiness: could not update %s: %s",
                           state_path, exc)

    result["refreshed_at_epoch"] = refreshed
    result["refreshed_at_iso"] = time.strftime(
        "%Y-%m-%d %H:%M UTC", time.gmtime(refreshed))
    days = (time.time() - refreshed) / 86400.0
    result["days_since_refresh"] = round(days, 1)
    result["warn_reauth_soon"] = days >= AUTH_WARN_DAYS
    result["critical"] = days >= AUTH_CRITICAL_DAYS

    # The live check — the only proof the session can still sign.
    try:
        whoami = _cli.acp("agent", "whoami")
    except Exception as exc:
        result["reason"] = (
            f"acp agent whoami FAILED — auth is dead; operator must run "
            f"`acp configure`. ({exc})")
        return result
    if isinstance(whoami, dict) and whoami.get("error"):
        result["reason"] = (
            f"acp agent whoami returned an error — auth is dead; operator "
            f"must run `acp configure`. ({whoami.get('error')})")
        return result

    result["alive"] = True
    if result["critical"]:
        result["reason"] = (
            f"ALIVE but auth is {result['days_since_refresh']}d old "
            f"(≥{AUTH_CRITICAL_DAYS}d) — may expire any moment; operator "
            "should run `acp configure` NOW.")
    elif result["warn_reauth_soon"]:
        result["reason"] = (
            f"ALIVE but auth is {result['days_since_refresh']}d old "
            f"(≥{AUTH_WARN_DAYS}d) — re-authenticate proactively with "
            "`acp configure`.")
    else:
        result["reason"] = (
            f"ALIVE — auth refreshed {result['days_since_refresh']}d ago.")
    return result
