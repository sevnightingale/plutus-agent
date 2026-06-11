"""On-chain trade-readiness verdict — TRADING.md fact #3 as code.

The API wallet must be approveAgent-registered for the master with an
unexpired ``validUntil``; nothing else (equity included) proves trading
works. One implementation, two consumers:

- ``scripts/check_trade_readiness.py`` — the operator CLI (loads the env
  file itself, prints READY / NOT READY).
- the ``hl_trade_readiness`` data point — the desk's view; plutus-ops
  fetches it every watchdog tick and escalates on a dead/expiring path.
"""

from __future__ import annotations

import time
from typing import Any, Dict

WARN_DAYS_DEFAULT = 7


def check_registration(master: str, api_address: str, api_key: str,
                       warn_days: int = WARN_DAYS_DEFAULT) -> Dict[str, Any]:
    """Return the readiness verdict dict. Never raises; errors are encoded.

    ``_exit`` carries the CLI exit code: 0 ready, 1 not ready,
    2 could-not-determine (config/network error).
    """
    master = (master or "").lower()
    api_address = (api_address or "").lower()
    api_key = api_key or ""

    result: Dict[str, Any] = {
        "ready": False,
        "reason": "",
        "master": master or None,
        "api_wallet_address": api_address or None,
        "registered_agents": [],
        "matched_agent": None,
        "valid_until_unix": None,
        "valid_until_iso": None,
        "days_remaining": None,
        "warn_expiring_soon": False,
    }

    if not master:
        result["reason"] = "ACP_AGENT_WALLET missing from ~/.plutus-agent/.env"
        result["_exit"] = 2
        return result
    if not api_address:
        result["reason"] = "HL_API_WALLET_ADDRESS missing from ~/.plutus-agent/.env"
        result["_exit"] = 2
        return result
    if not api_key or len(api_key) < 64:
        result["reason"] = "HL_API_WALLET_KEY missing or malformed in ~/.plutus-agent/.env"
        result["_exit"] = 2
        return result

    # Verify the key actually derives to the recorded API-wallet address
    # (catches key/address drift).
    try:
        from eth_account import Account
        derived = Account.from_key(api_key).address.lower()
        result["key_derives_to"] = derived
        if derived != api_address:
            result["reason"] = (
                f"HL_API_WALLET_KEY derives to {derived} but HL_API_WALLET_ADDRESS is "
                f"{api_address} — key/address mismatch in .env"
            )
            result["_exit"] = 1
            return result
    except Exception as exc:  # eth_account always present as SDK dep; defensive
        result["reason"] = f"could not derive address from HL_API_WALLET_KEY: {exc}"
        result["_exit"] = 2
        return result

    # Query live on-chain registration.
    try:
        from ._client import get_info
        extra = get_info().post("/info", {"type": "extraAgents", "user": master}) or []
    except Exception as exc:
        result["reason"] = f"could not query Hyperliquid extraAgents: {exc}"
        result["_exit"] = 2
        return result

    result["registered_agents"] = [
        {"name": a.get("name"), "address": (a.get("address") or "").lower(),
         "valid_until": a.get("validUntil")}
        for a in extra
    ]

    match = next((a for a in extra if (a.get("address") or "").lower() == api_address), None)
    if not match:
        if not extra:
            result["reason"] = (
                "NO API wallets registered on Hyperliquid for the master (extraAgents=[]). "
                "The registration is dead — every trade will fail with "
                "'User or API Wallet does not exist'. Re-register via add-api-wallet.ts "
                "(see TRADING.md recovery runbook)."
            )
        else:
            result["reason"] = (
                f"API wallet {api_address} is NOT among the registered agents "
                f"{[a['address'] for a in result['registered_agents']]}. "
                ".env key is out of sync with on-chain registration. See TRADING.md."
            )
        result["_exit"] = 1
        return result

    # Matched. Check expiry.
    result["matched_agent"] = {"name": match.get("name"), "address": api_address}
    vu = match.get("validUntil")
    now_ms = time.time() * 1000
    if vu is not None:
        result["valid_until_unix"] = vu / 1000.0
        result["valid_until_iso"] = time.strftime(
            "%Y-%m-%d %H:%M UTC", time.gmtime(vu / 1000.0)
        )
        days = (vu - now_ms) / 1000.0 / 86400.0
        result["days_remaining"] = round(days, 1)
        if vu <= now_ms:
            result["reason"] = (
                f"API wallet {api_address} registration EXPIRED at "
                f"{result['valid_until_iso']}. Re-register via add-api-wallet.ts "
                "(see TRADING.md)."
            )
            result["_exit"] = 1
            return result
        if days <= warn_days:
            result["warn_expiring_soon"] = True

    result["ready"] = True
    if result["warn_expiring_soon"]:
        result["reason"] = (
            f"READY but the API-wallet registration expires in "
            f"{result['days_remaining']} days ({result['valid_until_iso']}) — "
            "re-register soon."
        )
    else:
        result["reason"] = "READY — API wallet is registered and unexpired."
    result["_exit"] = 0
    return result
