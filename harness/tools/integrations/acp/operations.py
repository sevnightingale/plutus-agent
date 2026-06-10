"""ACP operations — signing, ACP client jobs, job inspection.

Verified against `acp <subcommand> --help` (acp-cli v1.0.5):
- `acp wallet sign-message` uses `--message` and `--chain-id` (default 8453).
- `acp wallet sign-typed-data` uses `--data` (JSON string) and `--chain-id`.
- `acp client create-job` uses `--provider --offering-name --requirements
  --chain-id --legacy --evaluator --package-id --hook` (chain-id default 8453).
- `acp client fund` uses `--job-id --chain-id --amount` (chain-id default 8453).
- `acp job list` only takes `--legacy` and `--all`. NO chain-id, NO status filter.
- `acp job history` REQUIRES `--job-id` and (no default) `--chain-id`.

`acp_wallet_send` was dropped — the real `acp wallet send-transaction`
takes raw EVM `--to / --data / --value`, not a USDC convenience. Plutus
sends USDC by either constructing ERC20 calldata via terminal directly,
or by hiring a transfer agent via ACP. Future polish: re-add as a USDC
convenience that builds the calldata + calls send-transaction with
hardcoded USDC contract addresses per chain.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from harness.tools.registry import registry, tool_error, tool_result

from . import _cli
from .data_points import DEFAULT_CHAIN_ID

logger = logging.getLogger(__name__)


# ─── acp_wallet_sign_message ──────────────────────────────────────────────


_SIGN_MSG_SCHEMA = {
    "name": "acp_wallet_sign_message",
    "description": "Sign a plain message with the ACP signer key. Chain id defaults to Base mainnet (8453).",
    "parameters": {
        "type": "object",
        "properties": {
            "message":  {"type": "string"},
            "chain_id": {"type": "string"},
        },
        "required": ["message"],
    },
}


def _acp_wallet_sign_message(args: Dict[str, Any]) -> str:
    msg = args.get("message")
    if not msg:
        return tool_error("message required")
    cid = str(args.get("chain_id") or DEFAULT_CHAIN_ID)
    try:
        result = _cli.acp("wallet", "sign-message", "--message", str(msg),
                          "--chain-id", cid)
    except Exception as exc:
        return tool_error(f"acp sign-message failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_wallet_sign_message",
    toolset="identity",
    schema=_SIGN_MSG_SCHEMA,
    handler=lambda args, **kw: _acp_wallet_sign_message(args),
    description="Sign a plain message with the ACP signer key.",
    emoji="✍️",
)


# ─── acp_wallet_sign_typed_data ───────────────────────────────────────────


_SIGN_TYPED_SCHEMA = {
    "name": "acp_wallet_sign_typed_data",
    "description": "Sign EIP-712 typed-data with the ACP signer key. Chain id defaults to Base mainnet (8453).",
    "parameters": {
        "type": "object",
        "properties": {
            "typed_data": {"type": "object"},
            "chain_id":   {"type": "string"},
        },
        "required": ["typed_data"],
    },
}


def _acp_wallet_sign_typed_data(args: Dict[str, Any]) -> str:
    td = args.get("typed_data")
    if not td:
        return tool_error("typed_data required")
    cid = str(args.get("chain_id") or DEFAULT_CHAIN_ID)
    try:
        result = _cli.acp(
            "wallet", "sign-typed-data",
            "--data", json.dumps(td),
            "--chain-id", cid,
        )
    except Exception as exc:
        return tool_error(f"acp sign-typed-data failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_wallet_sign_typed_data",
    toolset="identity",
    schema=_SIGN_TYPED_SCHEMA,
    handler=lambda args, **kw: _acp_wallet_sign_typed_data(args),
    description="Sign EIP-712 typed-data with the ACP signer key.",
    emoji="✍️",
)


# ─── acp_client_create_job ────────────────────────────────────────────────


_CREATE_JOB_SCHEMA = {
    "name": "acp_client_create_job",
    "description": (
        "Create an ACP client job against a provider's offering. "
        "Per the acp-cli SKILL.md, providers split into two schemas: v1 "
        "agents (--legacy, dgclaw provider 0xd478a8B... etc.) and v2 "
        "agents (no flag). Match the flag to the provider. Chain id "
        "defaults to Base mainnet (8453). For perp_deposit + join_leaderboard "
        "(both v1) you need --legacy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "provider":        {"type": "string", "description": "Provider wallet address."},
            "offering_name":   {"type": "string"},
            "requirements":    {"type": "object", "description": "Per-offering JSON shape (e.g. {\"amount\":\"25\"})."},
            "chain_id":        {"type": "string"},
            "evaluator":       {"type": "string"},
            "package_id":      {"type": "string"},
            "legacy":          {"type": "boolean", "default": False},
            "hook":            {"type": "string"},
        },
        "required": ["provider", "offering_name"],
    },
}


def _acp_client_create_job(args: Dict[str, Any]) -> str:
    provider = args.get("provider")
    offering = args.get("offering_name")
    if not provider or not offering:
        return tool_error("provider and offering_name required")

    cmd_args = [
        "client", "create-job",
        "--provider", str(provider),
        "--offering-name", str(offering),
        "--chain-id", str(args.get("chain_id") or DEFAULT_CHAIN_ID),
    ]
    if args.get("requirements") is not None:
        cmd_args.extend(["--requirements", json.dumps(args["requirements"])])
    if args.get("evaluator"):
        cmd_args.extend(["--evaluator", str(args["evaluator"])])
    if args.get("package_id"):
        cmd_args.extend(["--package-id", str(args["package_id"])])
    if args.get("hook"):
        cmd_args.extend(["--hook", str(args["hook"])])
    if args.get("legacy"):
        cmd_args.append("--legacy")

    try:
        result = _cli.acp(*cmd_args)
    except Exception as exc:
        return tool_error(f"acp client create-job failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_client_create_job",
    toolset="execution",
    schema=_CREATE_JOB_SCHEMA,
    handler=lambda args, **kw: _acp_client_create_job(args),
    description="Create an ACP client job (perp_deposit, join_leaderboard, etc.).",
    emoji="📋",
)


# ─── acp_client_fund ──────────────────────────────────────────────────────


_FUND_JOB_SCHEMA = {
    "name": "acp_client_fund",
    "description": "Fund an ACP client job by id with the agreed USDC amount. Chain id defaults to Base mainnet (8453).",
    "parameters": {
        "type": "object",
        "properties": {
            "job_id":   {"type": "string"},
            "amount":   {"type": "number"},
            "chain_id": {"type": "string"},
        },
        "required": ["job_id"],
    },
}


def _acp_client_fund(args: Dict[str, Any]) -> str:
    job_id = args.get("job_id")
    if job_id is None:
        return tool_error("job_id required")
    cmd_args = [
        "client", "fund",
        "--job-id", str(job_id),
        "--chain-id", str(args.get("chain_id") or DEFAULT_CHAIN_ID),
    ]
    amount = args.get("amount")
    if amount is not None:
        cmd_args.extend(["--amount", str(amount)])
    try:
        result = _cli.acp(*cmd_args)
    except Exception as exc:
        return tool_error(f"acp client fund failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_client_fund",
    toolset="execution",
    schema=_FUND_JOB_SCHEMA,
    handler=lambda args, **kw: _acp_client_fund(args),
    description="Fund an ACP client job.",
    emoji="💵",
)


# ─── acp_job_list ─────────────────────────────────────────────────────────


_JOB_LIST_SCHEMA = {
    "name": "acp_job_list",
    "description": (
        "List active ACP jobs. Per upstream CLI, only --legacy (v1 jobs only) "
        "and --all (v1 + v2) flags are supported. There is NO --status filter "
        "and NO --chain-id flag. Defaults to v2 jobs only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "legacy": {"type": "boolean", "description": "List v1 jobs only."},
            "all":    {"type": "boolean", "description": "List v1 + v2 jobs."},
        },
    },
}


def _acp_job_list(args: Dict[str, Any]) -> str:
    cmd_args = ["job", "list"]
    if args.get("legacy"):
        cmd_args.append("--legacy")
    if args.get("all"):
        cmd_args.append("--all")
    try:
        result = _cli.acp(*cmd_args)
    except Exception as exc:
        return tool_error(f"acp job list failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_job_list",
    toolset="reflection",
    schema=_JOB_LIST_SCHEMA,
    handler=lambda args, **kw: _acp_job_list(args),
    description="List ACP jobs (v2 default; --legacy for v1; --all for both).",
    emoji="📋",
)


# ─── acp_job_history ──────────────────────────────────────────────────────


_JOB_HISTORY_SCHEMA = {
    "name": "acp_job_history",
    "description": "Show full history (status + all messages) for a specific ACP job.",
    "parameters": {
        "type": "object",
        "properties": {
            "job_id":   {"type": "string"},
            "chain_id": {"type": "string", "description": "Required by upstream — defaults here to Base mainnet (8453)."},
        },
        "required": ["job_id"],
    },
}


def _acp_job_history(args: Dict[str, Any]) -> str:
    job_id = args.get("job_id")
    if job_id is None:
        return tool_error("job_id required")
    try:
        result = _cli.acp(
            "job", "history",
            "--job-id", str(job_id),
            "--chain-id", str(args.get("chain_id") or DEFAULT_CHAIN_ID),
        )
    except Exception as exc:
        return tool_error(f"acp job history failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_job_history",
    toolset="reflection",
    schema=_JOB_HISTORY_SCHEMA,
    handler=lambda args, **kw: _acp_job_history(args),
    description="Show full history (status + messages) for one ACP job.",
    emoji="📜",
)
