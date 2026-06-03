"""plutus-agent setup-status — at-a-glance verification dashboard.

Walks all the checks the bootstrap-setup skill exercises (ACP install,
ACP configured, signer present, dgclaw-skill installed, dgclaw joined,
HL API wallet set up, cron jobs seeded, holographic memory enabled)
and prints a one-screen report. For when the operator wants to see
status without engaging Plutus.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple


GREEN_OK = "\033[32m[x]\033[0m"
RED_X = "\033[31m[ ]\033[0m"
GRAY_DASH = "\033[90m[-]\033[0m"


def _check(label: str, ok: Optional[bool], detail: str = "") -> str:
    marker = GRAY_DASH if ok is None else (GREEN_OK if ok else RED_X)
    line = f"{marker} {label}"
    if detail:
        line += f"  \033[90m{detail}\033[0m"
    return line


def _check_acp_installed() -> Tuple[bool, str]:
    try:
        from tools.integrations.acp import _cli
        if not _cli.is_installed():
            return False, "run `npm install -g @virtuals-protocol/acp-cli`"
        try:
            v = _cli.acp("--version", json_flag=False, capture=False).strip()
        except Exception:
            v = "unknown version"
        return True, v
    except Exception as exc:
        return False, str(exc)


def _check_acp_configured() -> Tuple[Optional[bool], str]:
    try:
        from tools.integrations.acp import _cli
        if not _cli.is_installed():
            return None, "acp not installed"
        try:
            whoami = _cli.acp("agent", "whoami")
        except _cli.ACPCLIError:
            return False, "run `acp configure && acp agent create --name Plutus`"
        # acp agent whoami --json shape (verified v1.0.0): the active agent
        # object — {id, name, walletAddress, role, chains: [...], ...}.
        addr = whoami.get("walletAddress") or whoami.get("activeWallet")
        name = whoami.get("name") or "?"
        if addr:
            return True, f"{name} @ {addr}"
        return False, f"unexpected whoami shape (no walletAddress)"
    except Exception as exc:
        return None, str(exc)


# Legitimate USDC contract addresses by chain id — used to distinguish the
# real USDC from spam tokens that mimic the symbol with Unicode lookalikes.
_LEGIT_USDC = {
    "8453":   "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",   # Base mainnet
    "1":      "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",   # Ethereum mainnet
    "42161":  "0xaf88d065e77c8cc2239327c5edb3a432268e5831",   # Arbitrum One
    "10":     "0x0b2c639c533813f4aa9d7837caf62653d097ff85",   # Optimism
    "137":    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",   # Polygon
}


def _parse_acp_balance_for_usdc(balance: dict, chain_id: str) -> float:
    """Parse acp wallet balance --json output for the legitimate USDC amount.

    Real shape: ``{"chainId":..., "tokens":[{"tokenAddress","tokenBalance"
    (hex), "tokenMetadata":{"decimals","symbol",...}}, ...]}``.
    Filters spam tokens that use Unicode lookalikes for "USDC".
    """
    legit_addr = _LEGIT_USDC.get(str(chain_id), "").lower()
    total = 0.0
    for t in balance.get("tokens") or []:
        addr = (t.get("tokenAddress") or "").lower()
        meta = t.get("tokenMetadata") or {}
        sym = (meta.get("symbol") or "").strip()
        # Strict match: legit contract OR exactly ASCII "USDC"
        if (legit_addr and addr == legit_addr) or sym == "USDC":
            raw_hex = t.get("tokenBalance") or "0x0"
            try:
                raw = int(raw_hex, 16) if raw_hex.startswith("0x") else int(raw_hex)
                decimals = int(meta.get("decimals") or 6)
                total += raw / (10 ** decimals)
            except (TypeError, ValueError):
                continue
    return total


def _check_acp_wallet_balance() -> Tuple[Optional[bool], str]:
    try:
        from tools.integrations.acp import _cli
        from tools.integrations.acp.data_points import DEFAULT_CHAIN_ID
        if not _cli.is_installed():
            return None, "acp not installed"
        balance = _cli.acp("wallet", "balance", "--chain-id", DEFAULT_CHAIN_ID)
        usdc_total = _parse_acp_balance_for_usdc(balance, DEFAULT_CHAIN_ID)
        return (usdc_total > 0), f"${usdc_total:.2f} USDC on chain {DEFAULT_CHAIN_ID}"
    except Exception as exc:
        return None, str(exc)


def _check_dgclaw_installed() -> Tuple[bool, str]:
    from tools.integrations.dgclaw import _cli
    if _cli.is_installed():
        return True, str(_cli.get_root())
    return False, f"missing at {_cli.get_root()} — run `dgclaw_install`"


def _check_dgclaw_joined() -> Tuple[bool, str]:
    from tools.integrations.dgclaw import _env
    val = _env.read_dgclaw_env("DGCLAW_API_KEY")
    if val:
        return True, "DGCLAW_API_KEY set in dgclaw-skill .env"
    return False, "run `dgclaw_join`"


def _check_hl_api_wallet() -> Tuple[bool, str]:
    val = os.getenv("HL_API_WALLET_KEY")
    if val:
        addr = os.getenv("HL_API_WALLET_ADDRESS", "")
        return True, f"HL_API_WALLET_KEY set" + (f" (addr={addr[:10]}…)" if addr else "")
    return False, "run `dgclaw_add_api_wallet`, then `pm2 restart plutus-gateway`"


def _check_hl_public_address() -> Tuple[bool, str]:
    val = os.getenv("HL_PUBLIC_ADDRESS")
    if val:
        return True, val
    return False, "auto-set after `acp_agent_add_signer_status` succeeds"


def _check_voyage_key() -> Tuple[bool, str]:
    return (bool(os.getenv("VOYAGE_API_KEY")), "VOYAGE_API_KEY set" if os.getenv("VOYAGE_API_KEY") else "set in ~/.plutus-agent/.env")


def _check_cron_jobs() -> Tuple[Optional[bool], str]:
    try:
        from cron.jobs import list_jobs
        jobs = list_jobs()
        names = {j.get("name") for j in jobs}
        hb = "plutus-heartbeat" in names
        wr = "plutus-weekly-review" in names
        if hb and wr:
            return True, "plutus-heartbeat + plutus-weekly-review"
        if hb:
            return False, "weekly-review missing — run `plutus-agent cron seed-weekly-review`"
        if wr:
            return False, "heartbeat missing — run `plutus-agent cron seed-heartbeat`"
        return False, "neither seeded — run `plutus-agent cron seed-heartbeat` + `seed-weekly-review`"
    except Exception as exc:
        return None, str(exc)


def _check_holographic_memory() -> Tuple[bool, str]:
    try:
        from plutus_cli.config import load_config
        cfg = load_config()
        provider = (cfg.get("memory") or {}).get("provider")
        if provider == "holographic":
            return True, "memory.provider = holographic"
        return False, f"memory.provider = {provider!r} (set to 'holographic')"
    except Exception as exc:
        return False, str(exc)


def _check_pm2_processes() -> Tuple[Optional[bool], str]:
    try:
        import subprocess
        out = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if out.returncode != 0:
            return None, "pm2 not available"
        import json as _json
        procs = _json.loads(out.stdout)
        names = {p.get("name") for p in procs}
        gateway = "plutus-gateway" in names
        watchers = "plutus-watchers" in names
        if gateway and watchers:
            return True, "gateway + watchers online"
        return False, f"gateway={gateway}, watchers={watchers}"
    except Exception as exc:
        return None, str(exc)


def _check_lifecycle_db() -> Tuple[Optional[bool], str]:
    try:
        from plutus_constants import get_hermes_home
        from agent.lifecycle_db import get_lifecycle_db
        path = get_hermes_home() / "lifecycle.db"
        if not path.exists():
            return False, f"missing at {path} — restart gateway to auto-init"
        db = get_lifecycle_db()
        sv = db.conn().execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return True, f"schema v{sv['version']}"
    except Exception as exc:
        return None, str(exc)


def setup_status_command(args=None) -> int:
    """Print the one-screen status dashboard."""
    print()
    print("plutus-agent setup status")
    print("─" * 60)

    checks: List[str] = []

    # ACP
    ok, detail = _check_acp_installed()
    checks.append(_check("ACP CLI installed", ok, detail))

    ok, detail = _check_acp_configured()
    checks.append(_check("ACP agent configured", ok, detail))

    ok, detail = _check_acp_wallet_balance()
    checks.append(_check("ACP wallet funded", ok, detail))

    ok, detail = _check_hl_public_address()
    checks.append(_check("HL_PUBLIC_ADDRESS set", ok, detail))

    # dgclaw
    ok, detail = _check_dgclaw_installed()
    checks.append(_check("dgclaw-skill installed", ok, detail))

    ok, detail = _check_dgclaw_joined()
    checks.append(_check("dgclaw joined", ok, detail))

    ok, detail = _check_hl_api_wallet()
    checks.append(_check("HL API wallet present", ok, detail))

    # Plutus harness
    ok, detail = _check_voyage_key()
    checks.append(_check("VOYAGE_API_KEY", ok, detail))

    ok, detail = _check_holographic_memory()
    checks.append(_check("holographic memory enabled", ok, detail))

    ok, detail = _check_lifecycle_db()
    checks.append(_check("lifecycle.db initialised", ok, detail))

    ok, detail = _check_cron_jobs()
    checks.append(_check("Plutus cron jobs seeded", ok, detail))

    ok, detail = _check_pm2_processes()
    checks.append(_check("pm2 gateway + watchers online", ok, detail))

    for line in checks:
        print(line)

    # Live trading readiness summary
    ready = (
        os.getenv("HL_API_WALLET_KEY") and
        os.getenv("HL_PUBLIC_ADDRESS")
    )
    print("─" * 60)
    if ready:
        print(f"{GREEN_OK} Live trading READY — Plutus can place real orders.")
    else:
        print(f"{RED_X} Live trading NOT YET — finish setup via the bootstrap-setup skill (chat with Plutus and say 'set yourself up for trading').")
    print()
    return 0


def add_setup_status_subparser(subparsers) -> None:
    """Wire `plutus-agent setup-status` into the CLI."""
    p = subparsers.add_parser(
        "setup-status",
        help="Show plutus-agent setup status (ACP / dgclaw / HL / Plutus harness).",
    )
    p.set_defaults(func=lambda args: setup_status_command(args))


if __name__ == "__main__":
    sys.exit(setup_status_command(None))
