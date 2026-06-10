"""dgclaw data points — leaderboard + forum reads.

Verified against `bash dgclaw.sh --help` (dgclaw-skill v4.0):
- All subcommands take POSITIONAL args, NOT named flags.
- `dgclaw.sh leaderboard [limit] [offset]` (default top 20)
- `dgclaw.sh leaderboard-agent <name>` — search by NAME, not agent id
- `dgclaw.sh forums` — no args
- `dgclaw.sh forum <agentId>` — agent's forum
- `dgclaw.sh posts <agentId> <threadId>` — list posts in a thread
- `dgclaw.sh unreplied-posts <agentId>` — unreplied posts on agent's forum
- `dgclaw.sh token-info <tokenAddress>` — token metadata

Output is native JSON (the bash script wraps API calls); no --json flag.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from trading.perception.core.data_point_registry import register_data_point

from . import _cli

logger = logging.getLogger(__name__)


@register_data_point(
    name="dgclaw_leaderboard",
    category="market",
    source="dgclaw",
    description=(
        "Current dgclaw competition leaderboard standings. "
        "Default returns top 20; pass limit / offset to paginate."
    ),
    params_schema={
        "limit":  {"type": "integer", "default": 20},
        "offset": {"type": "integer", "default": 0},
    },
    returns_schema={"data": "list of agent rows with rank, name, pnl, etc."},
    tags=["leaderboard", "dgclaw"],
)
def dgclaw_leaderboard(limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    return _cli.dgclaw("leaderboard", str(int(limit)), str(int(offset)))


@register_data_point(
    name="dgclaw_leaderboard_agent",
    category="market",
    source="dgclaw",
    description="Search the leaderboard by AGENT NAME (substring match per upstream).",
    params_schema={
        "name": {"type": "string", "required": True, "description": "Agent name to search for."},
    },
    returns_schema={"data": "matching agent rows"},
    tags=["leaderboard", "dgclaw"],
)
def dgclaw_leaderboard_agent(name: str) -> Dict[str, Any]:
    return _cli.dgclaw("leaderboard-agent", str(name))


@register_data_point(
    name="dgclaw_forums",
    category="social",
    source="dgclaw",
    description="List all dgclaw forums (one per agent).",
    params_schema={},
    returns_schema={"data": "list of forum objects with agent + threads"},
    tags=["forum", "dgclaw"],
)
def dgclaw_forums() -> Dict[str, Any]:
    return _cli.dgclaw("forums")


@register_data_point(
    name="dgclaw_forum",
    category="social",
    source="dgclaw",
    description="Get a specific agent's forum (threads + metadata).",
    params_schema={
        "agent_id": {"type": "string", "required": True, "description": "Numeric agent id from the leaderboard."},
    },
    returns_schema={"data": "forum object"},
    tags=["forum", "dgclaw"],
)
def dgclaw_forum(agent_id: str) -> Dict[str, Any]:
    return _cli.dgclaw("forum", str(agent_id))


@register_data_point(
    name="dgclaw_forum_posts",
    category="social",
    source="dgclaw",
    description="List posts in a specific thread on an agent's forum.",
    params_schema={
        "agent_id":  {"type": "string", "required": True},
        "thread_id": {"type": "string", "required": True},
    },
    returns_schema={"data": "list of posts"},
    tags=["forum", "dgclaw"],
)
def dgclaw_forum_posts(agent_id: str, thread_id: str) -> Dict[str, Any]:
    return _cli.dgclaw("posts", str(agent_id), str(thread_id))


@register_data_point(
    name="dgclaw_forum_unreplied",
    category="social",
    source="dgclaw",
    description="List unreplied posts on a specific agent's forum (typically your own).",
    params_schema={
        "agent_id": {"type": "string", "required": True, "description": "Usually your own agent id."},
    },
    returns_schema={"data": "list of unreplied post objects"},
    tags=["forum", "dgclaw"],
)
def dgclaw_forum_unreplied(agent_id: str) -> Dict[str, Any]:
    return _cli.dgclaw("unreplied-posts", str(agent_id))


@register_data_point(
    name="dgclaw_token_info",
    category="market",
    source="dgclaw",
    description="Get token metadata for an agent's token (where applicable; tokenization is optional post May-2026).",
    params_schema={
        "token_address": {"type": "string", "required": True},
    },
    returns_schema={"data": "token metadata"},
    tags=["token", "dgclaw"],
)
def dgclaw_token_info(token_address: str) -> Dict[str, Any]:
    return _cli.dgclaw("token-info", str(token_address))
