"""ACP setup tools — install + identity bootstrap.

Most setup commands (`acp configure`, `acp agent add-signer`) wait for
a browser-mediated OAuth approval. The CLI long-polls Virtuals' API
for the response — typically 30-60 seconds. **A subprocess spawned
inside the gateway dies when the gateway restarts**, which means the
operator's browser approval lands on a dead listener and tokens
never get persisted.

So those tools here return **instructions for the operator to run
themselves in a fresh terminal**, rather than spawning subprocesses.
The operator's shell holds the long-poll, surviving any gateway
restart. After the operator confirms completion, Plutus continues
with the next non-interactive step (`acp_agent_create`,
`acp_wallet_topup`, etc.).

`acp_install` and `acp_install_check` stay as direct subprocess
wrappers — they're short-lived and don't depend on long-poll state.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

from . import _cli

logger = logging.getLogger(__name__)


# ─── acp_install_check ────────────────────────────────────────────────────


_INSTALL_CHECK_SCHEMA = {
    "name": "acp_install_check",
    "description": "Check whether the ACP CLI (`acp`) is installed and on PATH.",
    "parameters": {"type": "object", "properties": {}},
}


def _acp_install_check(args: Dict[str, Any]) -> str:
    if not _cli.is_installed():
        return tool_result({
            "installed": False,
            "version": None,
            "install_command": "npm install -g @virtuals-protocol/acp-cli",
            "next_step": "Call acp_install() to install it now.",
        })
    try:
        out = _cli.acp("--version", json_flag=False, capture=False)
        version = (out or "").strip() or "unknown"
    except Exception:
        version = "unknown"
    return tool_result({"installed": True, "version": version})


registry.register(
    name="acp_install_check",
    toolset="identity",
    schema=_INSTALL_CHECK_SCHEMA,
    handler=lambda args, **kw: _acp_install_check(args),
    description="Check ACP CLI install status.",
    emoji="🔎",
)


# ─── acp_install ──────────────────────────────────────────────────────────


_INSTALL_SCHEMA = {
    "name": "acp_install",
    "description": (
        "Install the ACP CLI globally via npm "
        "(`npm install -g @virtuals-protocol/acp-cli`). Requires Node ≥ 18 "
        "and global npm install permissions."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _acp_install(args: Dict[str, Any]) -> str:
    try:
        proc = subprocess.run(
            ["npm", "install", "-g", "@virtuals-protocol/acp-cli"],
            capture_output=True, text=True, check=False, timeout=300,
        )
    except FileNotFoundError:
        return tool_error("npm not on PATH. Install Node ≥ 18 first.")
    except subprocess.TimeoutExpired:
        return tool_error("npm install timed out after 5 minutes.")

    if proc.returncode != 0:
        return tool_error(
            f"npm install failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    return tool_result({
        "status": "installed",
        "stdout_tail": (proc.stdout or "").splitlines()[-5:],
        "next_step": "Call acp_install_check to confirm version, then acp_configure for instructions on the OAuth step.",
    })


registry.register(
    name="acp_install",
    toolset="identity",
    schema=_INSTALL_SCHEMA,
    handler=lambda args, **kw: _acp_install(args),
    description="Install ACP CLI globally via npm.",
    emoji="📦",
)


# ─── acp_configure (operator-instruction returner) ────────────────────────


_CONFIGURE_SCHEMA = {
    "name": "acp_configure",
    "description": (
        "Returns instructions for the OPERATOR to run `acp configure` in a "
        "fresh terminal themselves — does NOT spawn a subprocess. The OAuth "
        "long-poll subprocess can't survive gateway restarts; the operator's "
        "shell holds the listener, so the browser approval reliably lands. "
        "Surface the returned `command` and `instructions` to the operator "
        "and wait for them to confirm 'configure done' before proceeding."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _acp_configure(args: Dict[str, Any]) -> str:
    return tool_result({
        "operator_action_required": True,
        "command": "acp configure",
        "where_to_run": "Fresh terminal on the operator's machine (NOT inside Plutus).",
        "instructions": (
            "Operator: open a new terminal, run `acp configure`. The CLI prints "
            "an OAuth URL to stdout — open that URL in a browser, approve. "
            "When you see 'Successfully authenticated to ACP CLI', return to "
            "Plutus and confirm 'configure done'. Tokens land in OS keychain "
            "and config in ~/.config/acp/config.json — Plutus will see them on "
            "the next acp_whoami call."
        ),
        "why_manual": (
            "OAuth long-poll subprocess dies if the gateway restarts. "
            "Operator's shell preserves the subprocess across restarts."
        ),
        "verification": "After operator confirms, call acp_whoami — should return ownerWallet identity.",
    })


registry.register(
    name="acp_configure",
    toolset="identity",
    schema=_CONFIGURE_SCHEMA,
    handler=lambda args, **kw: _acp_configure(args),
    description="Return operator instructions for `acp configure` (don't spawn — operator runs it themselves).",
    emoji="🔐",
)


# ─── acp_agent_create ─────────────────────────────────────────────────────


_AGENT_CREATE_SCHEMA = {
    "name": "acp_agent_create",
    "description": (
        "Create an on-chain agent identity via `acp agent create --name X`. "
        "Non-interactive (no browser approval) — uses the OAuth token from "
        "`acp configure`. The CLI may prompt for description and image URL "
        "interactively; this wrapper passes flag overrides if provided."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "Plutus"},
            "description": {"type": "string", "description": "Optional agent description."},
        },
    },
}


def _acp_agent_create(args: Dict[str, Any]) -> str:
    name = args.get("name") or "Plutus"
    cmd = ["agent", "create", "--name", name]
    desc = args.get("description")
    if desc:
        cmd.extend(["--description", desc])
    try:
        result = _cli.acp(*cmd)
    except Exception as exc:
        return tool_error(f"acp agent create failed: {exc}")
    return tool_result({"status": "created", "result": result})


registry.register(
    name="acp_agent_create",
    toolset="identity",
    schema=_AGENT_CREATE_SCHEMA,
    handler=lambda args, **kw: _acp_agent_create(args),
    description="Create on-chain ACP agent identity.",
    emoji="🪪",
)


# ─── acp_agent_add_signer (operator-instruction returner) ─────────────────


_SIGNER_ADD_SCHEMA = {
    "name": "acp_agent_add_signer",
    "description": (
        "Returns instructions for the OPERATOR to run `acp agent add-signer` "
        "themselves — does NOT spawn a subprocess. Same browser-approval / "
        "long-poll fragility as `acp configure`. Surface the returned "
        "`command` and `instructions`, then wait for operator confirmation."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _acp_agent_add_signer(args: Dict[str, Any]) -> str:
    return tool_result({
        "operator_action_required": True,
        "command": "acp agent add-signer",
        "where_to_run": "Fresh terminal on the operator's machine (NOT inside Plutus).",
        "instructions": (
            "Operator: in a new terminal, run `acp agent add-signer`. The CLI "
            "lists registered agents — pick the one Plutus runs as (e.g. "
            "Plutus). It generates a P256 keypair, prints a browser approval "
            "URL — open and approve. When you see 'Signer added', return to "
            "Plutus and confirm. Private key lands in OS keychain "
            "automatically — no manual key handling needed."
        ),
        "why_manual": (
            "Same OAuth long-poll fragility as acp_configure — subprocess "
            "dies on gateway restart, operator's shell survives."
        ),
        "verification": (
            "After operator confirms, run `acp agent whoami --json` via "
            "terminal — `signers` should include the new public key. Then "
            "persist HL_PUBLIC_ADDRESS to ~/.plutus-agent/.env via "
            "acp_persist_env tool (or echo manually)."
        ),
    })


registry.register(
    name="acp_agent_add_signer",
    toolset="identity",
    schema=_SIGNER_ADD_SCHEMA,
    handler=lambda args, **kw: _acp_agent_add_signer(args),
    description="Return operator instructions for `acp agent add-signer` (operator runs it themselves).",
    emoji="🔑",
)


# ─── acp_wallet_topup ─────────────────────────────────────────────────────


_TOPUP_SCHEMA = {
    "name": "acp_wallet_topup",
    "description": (
        "Add funds to the ACP wallet via Coinbase or card. Method is "
        "REQUIRED ('coinbase' or 'card'). Card payments require --email and "
        "--us flag for US residents. Defaults chain id to Base mainnet "
        "(8453). Most operators just want the deposit address — for that, "
        "call with method='coinbase' and read the returned URL/address."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method":   {"type": "string", "description": "coinbase | card"},
            "amount":   {"type": "number", "description": "Amount in USD."},
            "chain_id": {"type": "string"},
            "email":    {"type": "string", "description": "Receipt email (required for card)."},
            "us":       {"type": "boolean", "description": "Required for US residents paying by card."},
        },
        "required": ["method"],
    },
}


def _acp_wallet_topup(args: Dict[str, Any]) -> str:
    method = args.get("method")
    if not method:
        return tool_error("method required ('coinbase' or 'card')")
    cmd_args = [
        "wallet", "topup",
        "--method", str(method),
        "--chain-id", str(args.get("chain_id") or "8453"),
    ]
    if args.get("amount") is not None:
        cmd_args.extend(["--amount", str(args["amount"])])
    if args.get("email"):
        cmd_args.extend(["--email", str(args["email"])])
    if args.get("us"):
        cmd_args.append("--us")
    try:
        result = _cli.acp(*cmd_args)
    except Exception as exc:
        return tool_error(f"acp wallet topup failed: {exc}")
    return tool_result({
        "status": "topup_info",
        "details": result,
        "warning": (
            "Send USDC on the chain id returned in the details ONLY. "
            "Wrong-chain transfers are unrecoverable."
        ),
    })


registry.register(
    name="acp_wallet_topup",
    toolset="identity",
    schema=_TOPUP_SCHEMA,
    handler=lambda args, **kw: _acp_wallet_topup(args),
    description="Get ACP wallet deposit address / QR for funding.",
    emoji="💰",
)


# ─── acp_persist_env_after_setup ──────────────────────────────────────────


_PERSIST_ENV_SCHEMA = {
    "name": "acp_persist_env_after_setup",
    "description": (
        "After `acp configure` + `acp agent create` + `acp agent add-signer` "
        "are done by the operator, call this to persist HL_PUBLIC_ADDRESS "
        "into ~/.plutus-agent/.env from `acp agent whoami`. Idempotent."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _acp_persist_env_after_setup(args: Dict[str, Any]) -> str:
    try:
        whoami = _cli.acp("agent", "whoami")
    except Exception as exc:
        return tool_error(
            f"acp agent whoami failed — operator may not have completed "
            f"acp configure / acp agent add-signer yet: {exc}"
        )
    addr = whoami.get("address") or (whoami.get("agent") or {}).get("address") \
        or (whoami.get("activeWallet") if isinstance(whoami.get("activeWallet"), str) else None)
    if not addr:
        return tool_error(
            f"could not extract agent address from acp whoami output: {whoami}"
        )

    from . import _env
    _env.set_env("HL_PUBLIC_ADDRESS", addr)
    return tool_result({
        "status": "persisted",
        "HL_PUBLIC_ADDRESS": addr,
        "next_step": (
            "Operator: please run `pm2 restart plutus-gateway` and "
            "`/reset` your Telegram session so HL data points see the new env."
        ),
    })


registry.register(
    name="acp_persist_env_after_setup",
    toolset="identity",
    schema=_PERSIST_ENV_SCHEMA,
    handler=lambda args, **kw: _acp_persist_env_after_setup(args),
    description="After operator-run ACP setup, persist HL_PUBLIC_ADDRESS to .env.",
    emoji="💾",
)
