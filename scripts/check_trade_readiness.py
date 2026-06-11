#!/usr/bin/env python3
"""check_trade_readiness.py — Is Plutus actually able to trade RIGHT NOW?

THE canonical health check for the trade-execution path. See TRADING.md for the full model.

Trading on plutus-agent goes through the NATIVE path:
    place_order(venue="hyperliquid") -> trading/integrations/hyperliquid/_client.py -> HL SDK
signing with the API wallet (HL_API_WALLET_KEY) on behalf of the MASTER — the ACP agent
wallet (ACP_AGENT_WALLET).

For that signature to be accepted, the API wallet must be REGISTERED on Hyperliquid as an
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
    for k in ("ACP_AGENT_WALLET", "HL_API_WALLET_ADDRESS", "HL_API_WALLET_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def check(warn_days: int = WARN_DAYS_DEFAULT) -> dict:
    """Return a dict describing trade readiness. Never raises; encodes errors in the dict."""
    from trading.integrations.hyperliquid.readiness import check_registration
    env = _load_env()
    return check_registration(
        env.get("ACP_AGENT_WALLET") or "",
        env.get("HL_API_WALLET_ADDRESS") or "",
        env.get("HL_API_WALLET_KEY") or "",
        warn_days=warn_days,
    )


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
    print(f"  API wallet (.env): {r.get('api_wallet_address')}")
    if r.get("matched_agent"):
        print(f"  registered as:     {r['matched_agent']['name']} (matches .env)")
    if r.get("valid_until_iso"):
        print(f"  valid until:       {r['valid_until_iso']} ({r['days_remaining']} days)")
    if r.get("registered_agents"):
        print(f"  on-chain agents:   {[a['address'] for a in r['registered_agents']]}")
    else:
        print("  on-chain agents:   [] (none registered)")
    if not r["ready"]:
        print("\n  → See TRADING.md (recovery runbook). Almost always: re-register the API wallet.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
