"""ACP identity tools — whoami, agent list/use.

Auto-registers an `IdentitySystemEntry("acp")` on first successful
call to acp_whoami so list_identity_systems shows it as available.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from tools.core.identity_registry import register_identity_system, RegistryError as IdRegistryError
from tools.registry import registry, tool_error, tool_result

from . import _cli

logger = logging.getLogger(__name__)


_acp_identity_registered = False


def _ensure_identity_registered() -> None:
    global _acp_identity_registered
    if _acp_identity_registered:
        return
    try:
        register_identity_system(
            name="acp",
            description=(
                "Virtuals ACP — agent identity, on-chain wallet, signer "
                "(P256 keypair in OS keychain), event stream subscription."
            ),
        )
    except IdRegistryError:
        pass
    _acp_identity_registered = True


# ─── acp_whoami ───────────────────────────────────────────────────────────


_WHOAMI_SCHEMA = {
    "name": "acp_whoami",
    "description": "Return Plutus's ACP agent identity (id, name, address, chains).",
    "parameters": {"type": "object", "properties": {}},
}


def _acp_whoami(args: Dict[str, Any]) -> str:
    try:
        result = _cli.acp("agent", "whoami")
    except Exception as exc:
        return tool_error(f"acp agent whoami failed: {exc}")
    _ensure_identity_registered()
    return tool_result(result)


registry.register(
    name="acp_whoami",
    toolset="identity",
    schema=_WHOAMI_SCHEMA,
    handler=lambda args, **kw: _acp_whoami(args),
    description="Show ACP agent identity.",
    emoji="🪪",
)


# ─── acp_agent_list ───────────────────────────────────────────────────────


_AGENT_LIST_SCHEMA = {
    "name": "acp_agent_list",
    "description": "List all ACP agents this OAuth login can switch between. Paginated.",
    "parameters": {
        "type": "object",
        "properties": {
            "page":      {"type": "integer", "description": "Page number (default 1)."},
            "page_size": {"type": "integer", "description": "Number of agents per page."},
        },
    },
}


def _acp_agent_list(args: Dict[str, Any]) -> str:
    cmd_args = ["agent", "list"]
    if args.get("page") is not None:
        cmd_args.extend(["--page", str(args["page"])])
    if args.get("page_size") is not None:
        cmd_args.extend(["--page-size", str(args["page_size"])])
    try:
        return tool_result(_cli.acp(*cmd_args))
    except Exception as exc:
        return tool_error(f"acp agent list failed: {exc}")


registry.register(
    name="acp_agent_list",
    toolset="identity",
    schema=_AGENT_LIST_SCHEMA,
    handler=lambda args, **kw: _acp_agent_list(args),
    description="List ACP agents.",
    emoji="📋",
)


# ─── acp_agent_use ────────────────────────────────────────────────────────


_AGENT_USE_SCHEMA = {
    "name": "acp_agent_use",
    "description": "Switch the active ACP agent context to the given agent_id.",
    "parameters": {
        "type": "object",
        "properties": {"agent_id": {"type": "string"}},
        "required": ["agent_id"],
    },
}


def _acp_agent_use(args: Dict[str, Any]) -> str:
    agent_id = args.get("agent_id")
    if not agent_id:
        return tool_error("agent_id required")
    try:
        result = _cli.acp("agent", "use", "--agent-id", str(agent_id))
    except Exception as exc:
        return tool_error(f"acp agent use failed: {exc}")
    return tool_result(result)


registry.register(
    name="acp_agent_use",
    toolset="identity",
    schema=_AGENT_USE_SCHEMA,
    handler=lambda args, **kw: _acp_agent_use(args),
    description="Switch active ACP agent.",
    emoji="🔄",
)
