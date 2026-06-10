#!/usr/bin/env python3
"""
Toolsets Module

This module provides a flexible system for defining and managing tool aliases/toolsets.
Toolsets allow you to group tools together for specific scenarios and can be composed
from individual tools or other toolsets.

Features:
- Define custom toolsets with specific tools
- Compose toolsets from other toolsets
- Built-in common toolsets for typical use cases
- Easy extension for new toolsets
- Support for dynamic toolset resolution

Usage:
    from harness.toolsets import get_toolset, resolve_toolset, get_all_toolsets
    
    # Get tools for a specific toolset
    tools = get_toolset("research")
    
    # Resolve a toolset to get all tool names (including from composed toolsets)
    all_tools = resolve_toolset("full_stack")
"""

from typing import List, Dict, Any, Set, Optional


# Shared tool list for CLI and all messaging platform toolsets.
# Edit this once to update all platforms simultaneously.
_HERMES_CORE_TOOLS = [
    # Web
    "web_search", "web_extract",
    # Terminal + process management
    "terminal", "process",
    # File manipulation
    "read_file", "write_file", "patch", "search_files",
    # Vision + image generation
    "vision_analyze", "image_generate",
    # Skills
    "skills_list", "skill_view", "skill_manage",
    # Browser automation
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back",
    "browser_press", "browser_get_images",
    "browser_vision", "browser_console", "browser_cdp",
    # Text-to-speech
    "text_to_speech",
    # Planning & memory
    "todo", "memory",
    # Session history search
    "session_search",
    # Clarifying questions
    "clarify",
    # Code execution + delegation
    "execute_code", "delegate_task",
    # Cronjob management
    "cronjob",
    # Cross-platform messaging (gated on gateway running via check_fn)
    "send_message",
    # Home Assistant smart home control (gated on HASS_TOKEN via check_fn)
    "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",
]


# plutus-agent: trader-focused core. Pruned from _HERMES_CORE_TOOLS.
# Drops personal-AI-flavored tools (vision, image gen, TTS, delegate,
# Home Assistant) that aren't relevant to autonomous trading. Browser
# stays — primary-source research is core. Cronjob stays
# — self-scheduling is core. send_message stays — proactive messaging
# (trade notifications, alerts, asking the operator) is core for an
# autonomous trader.
_TRADER_CORE_TOOLS = [
    # Web research
    "web_search", "web_extract",
    # Terminal + process
    "terminal", "process",
    # File operations (self-modification, skill authoring)
    "read_file", "write_file", "patch", "search_files",
    # Skills system
    "skills_list", "skill_view", "skill_manage",
    # Browser automation (primary-source research)
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back",
    "browser_press", "browser_get_images",
    "browser_vision", "browser_console", "browser_cdp",
    # Planning & memory
    "todo", "memory",
    # Session log search
    "session_search",
    # Clarifying questions
    "clarify",
    # Code execution (sandboxed analysis, indicator math)
    "execute_code",
    # Self-scheduling
    "cronjob",
    # Cross-platform messaging — agent can proactively notify operator
    # of trades, alerts, threshold events. Gated on gateway running.
    "send_message",
]


# Phase 4 placeholder toolset lists. Populated as each toolset's tools
# get authored. Empty for now — opt-in toolsets show up in `tools list`
# as toolsets but resolve to no tools until Phase 4.

# Phase 4a: native Hyperliquid (info + trade + indicators + observations)
_HYPERLIQUID_TOOLS: List[str] = []

# Phase 4: Virtuals ACP (subprocess-wraps acp-cli — wallet, events, browse, identity)
# All ACP tools are listed here so the `acp` toolset can be enabled or
# disabled wholesale. Individual tools are registered via tools/integrations/acp/
# at import time (the @register_data_point / registry.register decorators).
#
# acp_configure and acp_agent_add_signer are *operator-instruction returners*
# (return the command + URL for the operator to run in their own terminal),
# not subprocess spawners — see tools/integrations/acp/setup.py for why.
# The previous `*_status` polling tools have been removed.
_ACP_TOOLS: List[str] = [
    # setup
    "acp_install_check", "acp_install",
    "acp_configure",
    "acp_agent_create",
    "acp_agent_add_signer",
    "acp_wallet_topup",
    "acp_persist_env_after_setup",
    # data points (also reachable via fetch_data_point)
    "acp_wallet_balance", "acp_browse_offerings", "acp_chain_list",
    # operations
    "acp_wallet_sign_message", "acp_wallet_sign_typed_data",
    "acp_client_create_job", "acp_client_fund",
    "acp_job_list", "acp_job_history",
    # identity
    "acp_whoami", "acp_agent_list", "acp_agent_use",
]

# Phase 4: Virtuals dgclaw — steady-state ops only.
# First-time setup is documented in skills/dgclaw/SKILL.md (vendored from
# upstream); Plutus loads that skill and follows it directly. The brittle
# `dgclaw_install` / `dgclaw_join` / `dgclaw_perp_deposit_via_acp` / etc.
# wrappers were dropped in the Phase 4 polish pass.
_DGCLAW_TOOLS: List[str] = [
    # data points (also reachable via fetch_data_point)
    "dgclaw_leaderboard", "dgclaw_leaderboard_agent",
    "dgclaw_forum_posts", "dgclaw_forum_unreplied",
    # operations
    "dgclaw_forum_reply", "dgclaw_forum_create_post",
    "dgclaw_trade_open", "dgclaw_trade_close",
    "dgclaw_trade_positions", "dgclaw_trade_balance",
]


# PLUTUS architecture (Phase 4a): function-shaped toolsets — perception,
# execution, reflection, identity. Sources/venues are *integrations* under
# tools/integrations/<name>/ that contribute entries via decorators; the
# tool surface here stays at ~15 always-on dispatchers.
#
# These are defined so `tools list` surfaces them and so they can be wired
# into plutus-agent-cli once Phase 4b validates the integration. Phase 4a
# does NOT yet add them to plutus-agent-cli's includes — the agent still
# runs with the existing trader_core toolset until 4b.

_PERCEPTION_TOOLS = [
    "fetch_data_point",
    "list_data_points",
    "account_state",
    # Phase 5: agentic-blueprint write-back
    "record_data_point_observation",
    # Phase 4: dgclaw direct read tools
    "dgclaw_trade_balance",
    "dgclaw_trade_positions",
]

_EXECUTION_TOOLS = [
    "place_order",
    "close_position",
    "modify_order",
    "cancel_order",
    "list_venues",
    # Phase 4: ACP-job ops (acp_wallet_send dropped — real CLI takes raw EVM
    # to/data/value, not a USDC convenience; Plutus uses terminal directly
    # for raw sends, or hires another agent via ACP)
    "acp_client_create_job",
    "acp_client_fund",
    # Phase 4: dgclaw forum + trade-routing ops
    "dgclaw_forum_create_post",
    "dgclaw_trade_open",
    "dgclaw_trade_close",
]

_REFLECTION_TOOLS = [
    # event recording (registry-dispatched)
    "record_event",
    "list_event_types",
    # Phase 5: predictions + observations + per-strategy stats
    "record_prediction",
    "resolve_prediction",
    "record_observation",
    "query_predictions",
    "query_observations",
    "query_strategy_stats",
    # direct lifecycle queries
    "query_trades",
    "query_performance",
    "query_performance_attribution",
    "query_equity_curve",
    "query_capital_movements",
    "query_calibration",
    "query_skip_outcomes",
    "query_conviction_trajectory",
    "query_conviction_outcomes",
    "query_strategy_book",      # legacy — strategies-table backed; kept for backwards compat
    "query_unreflected_closes", # plutus-main Phase 0 handshake — pending postmortems
    "query_compaction_history", # V2 compaction visibility
    "query_latest_perception_digest", # V2.1 — plutus-main Phase 3 read of perception sub-agent output
    "inspect_position",
    "find_similar_theses",
    "find_similar_reflections",
    # Phase 4: ACP job inspection
    "acp_job_list",
    "acp_job_history",
]

_IDENTITY_TOOLS = [
    "list_accounts",
    "list_identity_systems",
    # V2.1 multi-tier orchestration — plutus-main spawns plutus-perception
    # (and future deep-research) via this.
    "spawn_subagent",
    # Phase 4: ACP identity / setup-helper tools.
    # acp_configure + acp_agent_add_signer are *instruction returners* —
    # they tell the operator what to run in their own terminal, since
    # the OAuth long-poll subprocess can't survive gateway restarts.
    "acp_install_check", "acp_install",
    "acp_configure",
    "acp_agent_create",
    "acp_agent_add_signer",
    "acp_wallet_topup",
    "acp_persist_env_after_setup",
    "acp_wallet_sign_message", "acp_wallet_sign_typed_data",
    "acp_whoami", "acp_agent_list", "acp_agent_use",
    # No dgclaw setup tools — dgclaw setup lives in skills/dgclaw/SKILL.md
    # (vendored from upstream); Plutus loads that skill and follows it.
]


# Core toolset definitions
# These can include individual tools or reference other toolsets
TOOLSETS = {
    # Basic toolsets - individual tool categories
    "web": {
        "description": "Web research and content extraction tools",
        "tools": ["web_search", "web_extract"],
        "includes": []  # No other toolsets included
    },
    
    "search": {
        "description": "Web search only (no content extraction/scraping)",
        "tools": ["web_search"],
        "includes": []
    },
    
    "vision": {
        "description": "Image analysis and vision tools",
        "tools": ["vision_analyze"],
        "includes": []
    },
    
    "image_gen": {
        "description": "Creative generation tools (images)",
        "tools": ["image_generate"],
        "includes": []
    },
    
    "terminal": {
        "description": "Terminal/command execution and process management tools",
        "tools": ["terminal", "process"],
        "includes": []
    },
    
    
    "skills": {
        "description": "Access, create, edit, and manage skill documents with specialized instructions and knowledge",
        "tools": ["skills_list", "skill_view", "skill_manage"],
        "includes": []
    },
    
    "browser": {
        "description": "Browser automation for web interaction (navigate, click, type, scroll, iframes, hold-click) with web search for finding URLs",
        "tools": [
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console", "browser_cdp", "web_search"
        ],
        "includes": []
    },
    
    "cronjob": {
        "description": "Cronjob management tool - create, list, update, pause, resume, remove, and trigger scheduled tasks",
        "tools": ["cronjob"],
        "includes": []
    },
    
    "messaging": {
        "description": "Cross-platform messaging: send messages to Telegram, Discord, Slack, SMS, etc.",
        "tools": ["send_message"],
        "includes": []
    },
    
    
    "file": {
        "description": "File manipulation tools: read, write, patch (with fuzzy matching), and search (content + files)",
        "tools": ["read_file", "write_file", "patch", "search_files"],
        "includes": []
    },
    
    "tts": {
        "description": "Text-to-speech: convert text to audio with Edge TTS (free), ElevenLabs, OpenAI, or xAI",
        "tools": ["text_to_speech"],
        "includes": []
    },
    
    "todo": {
        "description": "Task planning and tracking for multi-step work",
        "tools": ["todo"],
        "includes": []
    },
    
    "memory": {
        "description": "Persistent memory across sessions (personal notes + user profile)",
        "tools": ["memory"],
        "includes": []
    },
    
    "session_search": {
        "description": "Search and recall past conversations with summarization",
        "tools": ["session_search"],
        "includes": []
    },
    
    "clarify": {
        "description": "Ask the user clarifying questions (multiple-choice or open-ended)",
        "tools": ["clarify"],
        "includes": []
    },
    
    "code_execution": {
        "description": "Run Python scripts that call tools programmatically (reduces LLM round trips)",
        "tools": ["execute_code"],
        "includes": []
    },
    
    "delegation": {
        "description": "Spawn subagents with isolated context for complex subtasks",
        "tools": ["delegate_task"],
        "includes": []
    },

    # "honcho" toolset removed — Honcho is now a memory provider plugin.
    # Tools are injected via MemoryManager, not the toolset system.



    # Scenario-specific toolsets
    
    "debugging": {
        "description": "Debugging and troubleshooting toolkit",
        "tools": ["terminal", "process"],
        "includes": ["web", "file"]  # For searching error messages and solutions, and file operations
    },
    
    "safe": {
        "description": "Safe toolkit without terminal access",
        "tools": [],
        "includes": ["web", "vision", "image_gen"]
    },
    
    # ==========================================================================
    # Full Hermes toolsets (CLI + messaging platforms)
    #
    # All platforms share the same core tools (including send_message,
    # which is gated on gateway running via its check_fn).
    # ==========================================================================

    "hermes-api-server": {
        "description": "OpenAI-compatible API server — full agent tools accessible via HTTP (no interactive UI tools like clarify or send_message)",
        "tools": [
            # Web
            "web_search", "web_extract",
            # Terminal + process management
            "terminal", "process",
            # File manipulation
            "read_file", "write_file", "patch", "search_files",
            # Vision + image generation
            "vision_analyze", "image_generate",
            # Skills
            "skills_list", "skill_view", "skill_manage",
            # Browser automation
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console", "browser_cdp",
            # Planning & memory
            "todo", "memory",
            # Session history search
            "session_search",
            # Code execution + delegation
            "execute_code", "delegate_task",
            # Cronjob management
            "cronjob",
            # Home Assistant smart home control (gated on HASS_TOKEN via check_fn)
            "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",

        ],
        "includes": []
    },
    
    "hermes-cli": {
        "description": "Full interactive CLI toolset - all default tools plus cronjob management. (Upstream Hermes default; kept for users who want vanilla personal-AI behavior.)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    # ─── plutus-agent toolsets ────────────────────────────────────────────
    # Lean trading-focused bundles. trader_core is the pruned upstream core;
    # hyperliquid/acp/dgclaw are domain-specific toolsets (acp/dgclaw opt-in).
    # plutus-agent-cli is the new default referenced by DEFAULT_CONFIG.

    "trader_core": {
        "description": "Core plutus-agent toolset — research, file ops, browser, planning, memory, scheduling. No exchange-specific tools.",
        "tools": _TRADER_CORE_TOOLS,
        "includes": []
    },

    "hyperliquid": {
        "description": "Hyperliquid native trading (Phase 4a — empty until tools are authored). Read market state, execute trades, set stops, manage positions.",
        "tools": _HYPERLIQUID_TOOLS,
        "includes": []
    },

    "acp": {
        "description": "Virtuals ACP integration (Phase 4b — empty until tools are authored). On-chain wallet, agent identity, event streaming, agent discovery. Subprocess-wraps acp-cli.",
        "tools": _ACP_TOOLS,
        "includes": []
    },

    "dgclaw": {
        "description": "Virtuals dgclaw competition (Phase 4c — empty until tools are authored). Join leaderboard, route trades, post to degen.virtuals.io forum. Depends on acp toolset.",
        "tools": _DGCLAW_TOOLS,
        "includes": ["acp"]
    },

    "plutus-agent-cli": {
        "description": "Default plutus-agent interactive CLI toolset — trader_core plus hyperliquid (native trading) plus the four PLUTUS function toolsets (perception/execution/reflection/identity). ACP and dgclaw toolsets are opt-in via config.",
        "tools": [],
        "includes": ["trader_core", "hyperliquid", "perception", "execution", "reflection", "identity"]
    },

    # ─── PLUTUS function-shaped toolsets (Phase 4a) ───────────────────────
    # Defined here, included by plutus-agent-cli above. Sources/venues are
    # *integrations* under tools/integrations/<source>/ that contribute
    # entries to these function toolsets via decorators.

    "perception": {
        "description": "Plutus perception — fetch_data_point (registry-dispatched, auto-snapshots), list_data_points, account_state.",
        "tools": _PERCEPTION_TOOLS,
        "includes": [],
    },

    "execution": {
        "description": "Plutus execution — place_order/close_position/modify_order/cancel_order (venue-dispatched), list_venues. Ungated.",
        "tools": _EXECUTION_TOOLS,
        "includes": [],
    },

    "reflection": {
        "description": "Plutus reflection — record_event (registry-dispatched: thesis/decision/reflection/conviction/...), list_event_types. Lifecycle queries join in Phase 4a-c6.",
        "tools": _REFLECTION_TOOLS,
        "includes": [],
    },

    "identity": {
        "description": "Plutus identity — list_accounts, list_identity_systems. Per-integration admin tools (acp_*, etc.) load when their integration is enabled.",
        "tools": _IDENTITY_TOOLS,
        "includes": [],
    },
    
    "hermes-telegram": {
        "description": "Telegram bot toolset - full access for personal use (terminal has safety checks)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },
    
    "hermes-discord": {
        "description": "Discord bot toolset - full access (terminal has safety checks via dangerous command approval)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },
    
    "hermes-whatsapp": {
        "description": "WhatsApp bot toolset - similar to Telegram (personal messaging, more trusted)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },
    
    "hermes-slack": {
        "description": "Slack bot toolset - full access for workspace use (terminal has safety checks)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },
    
    "hermes-signal": {
        "description": "Signal bot toolset - encrypted messaging platform (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-bluebubbles": {
        "description": "BlueBubbles iMessage bot toolset - Apple iMessage via local BlueBubbles server",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-homeassistant": {
        "description": "Home Assistant bot toolset - smart home event monitoring and control",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-email": {
        "description": "Email bot toolset - interact with Hermes via email (IMAP/SMTP)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-mattermost": {
        "description": "Mattermost bot toolset - self-hosted team messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-matrix": {
        "description": "Matrix bot toolset - decentralized encrypted messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-dingtalk": {
        "description": "DingTalk bot toolset - enterprise messaging platform (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-feishu": {
        "description": "Feishu/Lark bot toolset - enterprise messaging via Feishu/Lark (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-weixin": {
        "description": "Weixin bot toolset - personal WeChat messaging via iLink (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-qqbot": {
        "description": "QQBot toolset - QQ messaging via Official Bot API v2 (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-wecom": {
        "description": "WeCom bot toolset - enterprise WeChat messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-wecom-callback": {
        "description": "WeCom callback toolset - enterprise self-built app messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-sms": {
        "description": "SMS bot toolset - interact with Hermes via SMS (Twilio)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-webhook": {
        "description": "Webhook toolset - receive and process external webhook events",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-gateway": {
        "description": "Gateway toolset - union of all messaging platform tools",
        "tools": [],
        "includes": ["hermes-telegram", "hermes-discord", "hermes-whatsapp", "hermes-slack", "hermes-signal", "hermes-bluebubbles", "hermes-homeassistant", "hermes-email", "hermes-sms", "hermes-mattermost", "hermes-matrix", "hermes-dingtalk", "hermes-feishu", "hermes-wecom", "hermes-wecom-callback", "hermes-weixin", "hermes-qqbot", "hermes-webhook"]
    }
}



def get_toolset(name: str) -> Optional[Dict[str, Any]]:
    """
    Get a toolset definition by name.
    
    Args:
        name (str): Name of the toolset
        
    Returns:
        Dict: Toolset definition with description, tools, and includes
        None: If toolset not found
    """
    toolset = TOOLSETS.get(name)
    if toolset:
        return toolset

    try:
        from harness.tools.registry import registry
    except Exception:
        return None

    registry_toolset = name
    description = f"Plugin toolset: {name}"
    alias_target = registry.get_toolset_alias_target(name)

    if name not in _get_plugin_toolset_names():
        registry_toolset = alias_target
        if not registry_toolset:
            return None
        description = f"MCP server '{name}' tools"
    else:
        reverse_aliases = {
            canonical: alias
            for alias, canonical in _get_registry_toolset_aliases().items()
            if alias not in TOOLSETS
        }
        alias = reverse_aliases.get(name)
        if alias:
            description = f"MCP server '{alias}' tools"

    return {
        "description": description,
        "tools": registry.get_tool_names_for_toolset(registry_toolset),
        "includes": [],
    }


def resolve_toolset(name: str, visited: Set[str] = None) -> List[str]:
    """
    Recursively resolve a toolset to get all tool names.
    
    This function handles toolset composition by recursively resolving
    included toolsets and combining all tools.
    
    Args:
        name (str): Name of the toolset to resolve
        visited (Set[str]): Set of already visited toolsets (for cycle detection)
        
    Returns:
        List[str]: List of all tool names in the toolset
    """
    if visited is None:
        visited = set()
    
    # Special aliases that represent all tools across every toolset
    # This ensures future toolsets are automatically included without changes.
    if name in {"all", "*"}:
        all_tools: Set[str] = set()
        for toolset_name in get_toolset_names():
            # Use a fresh visited set per branch to avoid cross-branch contamination
            resolved = resolve_toolset(toolset_name, visited.copy())
            all_tools.update(resolved)
        return sorted(all_tools)

    # Check for cycles / already-resolved (diamond deps).
    # Silently return [] — either this is a diamond (not a bug, tools already
    # collected via another path) or a genuine cycle (safe to skip).
    if name in visited:
        return []

    visited.add(name)

    # Get toolset definition
    toolset = get_toolset(name)
    if not toolset:
        return []

    # Collect direct tools
    tools = set(toolset.get("tools", []))

    # Recursively resolve included toolsets, sharing the visited set across
    # sibling includes so diamond dependencies are only resolved once and
    # cycle warnings don't fire multiple times for the same cycle.
    for included_name in toolset.get("includes", []):
        included_tools = resolve_toolset(included_name, visited)
        tools.update(included_tools)
    
    return sorted(tools)


def resolve_multiple_toolsets(toolset_names: List[str]) -> List[str]:
    """
    Resolve multiple toolsets and combine their tools.
    
    Args:
        toolset_names (List[str]): List of toolset names to resolve
        
    Returns:
        List[str]: Combined list of all tool names (deduplicated)
    """
    all_tools = set()
    
    for name in toolset_names:
        tools = resolve_toolset(name)
        all_tools.update(tools)
    
    return sorted(all_tools)


def _get_plugin_toolset_names() -> Set[str]:
    """Return toolset names registered by plugins (from the tool registry).

    These are toolsets that exist in the registry but not in the static
    ``TOOLSETS`` dict — i.e. they were added by plugins at load time.
    """
    try:
        from harness.tools.registry import registry
        return {
            toolset_name
            for toolset_name in registry.get_registered_toolset_names()
            if toolset_name not in TOOLSETS
        }
    except Exception:
        return set()


def _get_registry_toolset_aliases() -> Dict[str, str]:
    """Return explicit toolset aliases registered in the live registry."""
    try:
        from harness.tools.registry import registry
        return registry.get_registered_toolset_aliases()
    except Exception:
        return {}


def get_all_toolsets() -> Dict[str, Dict[str, Any]]:
    """
    Get all available toolsets with their definitions.

    Includes both statically-defined toolsets and plugin-registered ones.
    
    Returns:
        Dict: All toolset definitions
    """
    result = dict(TOOLSETS)
    aliases = _get_registry_toolset_aliases()
    for ts_name in _get_plugin_toolset_names():
        display_name = ts_name
        for alias, canonical in aliases.items():
            if canonical == ts_name and alias not in TOOLSETS:
                display_name = alias
                break
        if display_name in result:
            continue
        toolset = get_toolset(display_name)
        if toolset:
            result[display_name] = toolset
    return result


def get_toolset_names() -> List[str]:
    """
    Get names of all available toolsets (excluding aliases).

    Includes plugin-registered toolset names.
    
    Returns:
        List[str]: List of toolset names
    """
    names = set(TOOLSETS.keys())
    aliases = _get_registry_toolset_aliases()
    for ts_name in _get_plugin_toolset_names():
        for alias, canonical in aliases.items():
            if canonical == ts_name and alias not in TOOLSETS:
                names.add(alias)
                break
        else:
            names.add(ts_name)
    return sorted(names)




def validate_toolset(name: str) -> bool:
    """
    Check if a toolset name is valid.
    
    Args:
        name (str): Toolset name to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    # Accept special alias names for convenience
    if name in {"all", "*"}:
        return True
    if name in TOOLSETS:
        return True
    if name in _get_plugin_toolset_names():
        return True
    return name in _get_registry_toolset_aliases()


def create_custom_toolset(
    name: str,
    description: str,
    tools: List[str] = None,
    includes: List[str] = None
) -> None:
    """
    Create a custom toolset at runtime.
    
    Args:
        name (str): Name for the new toolset
        description (str): Description of the toolset
        tools (List[str]): Direct tools to include
        includes (List[str]): Other toolsets to include
    """
    TOOLSETS[name] = {
        "description": description,
        "tools": tools or [],
        "includes": includes or []
    }




def get_toolset_info(name: str) -> Dict[str, Any]:
    """
    Get detailed information about a toolset including resolved tools.
    
    Args:
        name (str): Toolset name
        
    Returns:
        Dict: Detailed toolset information
    """
    toolset = get_toolset(name)
    if not toolset:
        return None
    
    resolved_tools = resolve_toolset(name)
    
    return {
        "name": name,
        "description": toolset["description"],
        "direct_tools": toolset["tools"],
        "includes": toolset["includes"],
        "resolved_tools": resolved_tools,
        "tool_count": len(resolved_tools),
        "is_composite": bool(toolset["includes"])
    }




if __name__ == "__main__":
    print("Toolsets System Demo")
    print("=" * 60)
    
    print("\nAvailable Toolsets:")
    print("-" * 40)
    for name, toolset in get_all_toolsets().items():
        info = get_toolset_info(name)
        composite = "[composite]" if info["is_composite"] else "[leaf]"
        print(f"  {composite} {name:20} - {toolset['description']}")
        print(f"     Tools: {len(info['resolved_tools'])} total")
    
    print("\nToolset Resolution Examples:")
    print("-" * 40)
    for name in ["web", "terminal", "safe", "debugging"]:
        tools = resolve_toolset(name)
        print(f"\n  {name}:")
        print(f"    Resolved to {len(tools)} tools: {', '.join(sorted(tools))}")
    
    print("\nMultiple Toolset Resolution:")
    print("-" * 40)
    combined = resolve_multiple_toolsets(["web", "vision", "terminal"])
    print("  Combining ['web', 'vision', 'terminal']:")
    print(f"    Result: {', '.join(sorted(combined))}")
    
    print("\nCustom Toolset Creation:")
    print("-" * 40)
    create_custom_toolset(
        name="my_custom",
        description="My custom toolset for specific tasks",
        tools=["web_search"],
        includes=["terminal", "vision"]
    )
    custom_info = get_toolset_info("my_custom")
    print("  Created 'my_custom' toolset:")
    print(f"    Description: {custom_info['description']}")
    print(f"    Resolved tools: {', '.join(custom_info['resolved_tools'])}")
