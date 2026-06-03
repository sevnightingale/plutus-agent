#!/usr/bin/env python3
"""check_trade_readiness.py — Is Plutus actually able to trade RIGHT NOW?

THE canonical health check for the trade-execution path. See TRADING.md for the full model.

Trading on plutus-agent goes through the NATIVE path:
    place_order(venue="hyperliquid") -> tools/integrations/hyperliquid/_client.py -> HL SDK
signing with the AGENT wallet (HL_API_WALLET_KEY) on behalf of the MASTER (HL_PUBLIC_ADDRESS).

For that signature to be accepted, the agent wallet must be REGISTERED on Hyperliquid as an
approved agent of the master (an on-chain `approveAgent`, carrying a ~180-day `validUntil`).
If it is not registered (or expired), EVERY trade fails silently with
"User or API Wallet does not exist". That is the #1 failure mode (it once silently broke
trading for two weeks).

This script checks the LIVE on-chain registration and prints READY / NOT READY.
Exit code 0 = ready, 1 = not ready, 2 = could not determine (config/network error).

Usage:
    cd ~/plutus-agent && .venv/bin/python scripts/check_trade_readiness.py
    .venv/bin/python scripts/check_trade_readiness.py --json    # machine-readable
    .venv/bin/python scripts/check_trade_readiness.py --warn-days 7   # warn if expiring soon
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

WARN_DAYS_DEFAULT = 7
ENV_PATH = Path.home() / ".plutus-agent" / ".env"


def _load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for ln in ENV_PATH.read_text().splitlines():
            s = ln.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    # process env overrides file (so a running gateway's live values win if invoked in-proc)
    for k in ("HL_PUBLIC_ADDRESS", "HL_MASTER_ADDRESS", "HL_API_WALLET_ADDRESS", "HL_API_WALLET_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def check(warn_days: int = WARN_DAYS_DEFAULT) -> dict:
    """Return a dict describing trade readiness. Never raises; encodes errors in the dict."""
    env = _load_env()
    master = (env.get("HL_PUBLIC_ADDRESS") or env.get("HL_MASTER_ADDRESS") or "").lower()
    agent_env = (env.get("HL_API_WALLET_ADDRESS") or "").lower()
    agent_key = env.get("HL_API_WALLET_KEY") or ""

    result = {
        "ready": False,
        "reason": "",
        "master": master or None,
        "agent_env_address": agent_env or None,
        "registered_agents": [],
        "matched_agent": None,
        "valid_until_unix": None,
        "valid_until_iso": None,
        "days_remaining": None,
        "warn_expiring_soon": False,
    }

    if not master:
        result["reason"] = "HL_PUBLIC_ADDRESS/HL_MASTER_ADDRESS missing from ~/.plutus-agent/.env"
        result["_exit"] = 2
        return result
    if not agent_env:
        result["reason"] = "HL_API_WALLET_ADDRESS missing from ~/.plutus-agent/.env"
        result["_exit"] = 2
        return result
    if not agent_key or len(agent_key) < 64:
        result["reason"] = "HL_API_WALLET_KEY missing or malformed in ~/.plutus-agent/.env"
        result["_exit"] = 2
        return result

    # Verify the key actually derives to the recorded agent address (catches key/address drift).
    try:
        from eth_account import Account
        derived = Account.from_key(agent_key).address.lower()
        result["key_derives_to"] = derived
        if derived != agent_env:
            result["reason"] = (
                f"HL_API_WALLET_KEY derives to {derived} but HL_API_WALLET_ADDRESS is "
                f"{agent_env} — key/address mismatch in .env"
            )
            result["_exit"] = 1
            return result
    except Exception as exc:  # eth_account always present as SDK dep; defensive
        result["reason"] = f"could not derive address from HL_API_WALLET_KEY: {exc}"
        result["_exit"] = 2
        return result

    # Query live on-chain registration.
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL, skip_ws=True)
        extra = info.post("/info", {"type": "extraAgents", "user": master}) or []
    except Exception as exc:
        result["reason"] = f"could not query Hyperliquid extraAgents: {exc}"
        result["_exit"] = 2
        return result

    result["registered_agents"] = [
        {"name": a.get("name"), "address": (a.get("address") or "").lower(),
         "valid_until": a.get("validUntil")}
        for a in extra
    ]

    match = next((a for a in extra if (a.get("address") or "").lower() == agent_env), None)
    if not match:
        if not extra:
            result["reason"] = (
                "NO agent wallets registered on Hyperliquid for the master (extraAgents=[]). "
                "The agent registration is dead — every trade will fail with "
                "'User or API Wallet does not exist'. Re-register via add-api-wallet.ts "
                "(see TRADING.md recovery runbook)."
            )
        else:
            result["reason"] = (
                f"agent {agent_env} is NOT among the registered agents "
                f"{[a['address'] for a in result['registered_agents']]}. "
                ".env key is out of sync with on-chain registration. See TRADING.md."
            )
        result["_exit"] = 1
        return result

    # Matched. Check expiry.
    result["matched_agent"] = {"name": match.get("name"), "address": agent_env}
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
                f"agent {agent_env} registration EXPIRED at {result['valid_until_iso']}. "
                "Re-register via add-api-wallet.ts (see TRADING.md)."
            )
            result["_exit"] = 1
            return result
        if days <= warn_days:
            result["warn_expiring_soon"] = True

    result["ready"] = True
    if result["warn_expiring_soon"]:
        result["reason"] = (
            f"READY but agent registration expires in {result['days_remaining']} days "
            f"({result['valid_until_iso']}) — re-register soon."
        )
    else:
        result["reason"] = "READY — agent wallet is registered and unexpired."
    result["_exit"] = 0
    return result


def main() -> int:
    as_json = "--json" in sys.argv
    warn_days = WARN_DAYS_DEFAULT
    if "--warn-days" in sys.argv:
        try:
            warn_days = int(sys.argv[sys.argv.index("--warn-days") + 1])
        except (ValueError, IndexError):
            pass

    r = check(warn_days=warn_days)
    exit_code = r.pop("_exit", 1)

    if as_json:
        print(json.dumps(r, indent=1))
        return exit_code

    status = "READY ✅" if r["ready"] else "NOT READY ❌"
    print(f"=== Plutus trade readiness: {status} ===")
    print(f"  {r['reason']}")
    print(f"  master:            {r.get('master')}")
    print(f"  agent (.env):      {r.get('agent_env_address')}")
    if r.get("matched_agent"):
        print(f"  registered as:     {r['matched_agent']['name']} (matches .env)")
    if r.get("valid_until_iso"):
        print(f"  valid until:       {r['valid_until_iso']} ({r['days_remaining']} days)")
    if r.get("registered_agents"):
        print(f"  on-chain agents:   {[a['address'] for a in r['registered_agents']]}")
    else:
        print("  on-chain agents:   [] (none registered)")
    if not r["ready"]:
        print("\n  → See TRADING.md (recovery runbook). Almost always: re-register the agent wallet.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
