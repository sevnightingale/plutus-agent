"""spawn_subagent — orchestration dispatcher (V2.1).

Plutus-main uses this to spawn focused sub-agents (plutus-perception in
v1, deep-research in v2) and block on their completion. The sub-agent
runs in its own AIAgent + session + (optional) model override, has a
restricted toolset, and is expected to write exactly one observation
whose ``structured_tags.event_type`` matches the caller's
``expected_event_type``. The dispatcher returns the observation id.

This is a blocking call. The sub-agent typically takes 1-5 minutes for
plutus-perception (95 tool calls + LLM inference). The orchestrator
should kick this off at the start of its beat so other phases can
overlap if needed — but the current implementation does NOT support
async overlap; it blocks the caller. Async overlap can come later if
we identify it as the bottleneck.

Sub-agent toolset defaults are pre-set for known skills to enforce the
"narrow tool surface" invariant; pass ``enabled_toolsets`` to override
when spawning a non-default skill.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agent.subagent_spawn import spawn_subagent_blocking
from tools.registry import registry, tool_error, tool_result


# Pre-configured toolset bundles per known sub-agent. Keep narrow.
_DEFAULT_TOOLSETS_PER_SKILL: Dict[str, Dict[str, Any]] = {
    "plutus-perception": {
        # perception: fetch_data_point, list_data_points, account_state
        # reflection: record_event, query_observations + lifecycle queries (so the
        #             agent can record the digest event and query for the retest)
        # skills:     skill_view (to load its own SKILL.md), skills_list
        # search:     web_search (to resolve macro agentic_blueprint DPs)
        # NOTE: deepseek-v4-flash (not kimi) for cost — perception is the heaviest
        # call-volume sub-agent (4×/day × ~50 fetches). flash's request budget is
        # ~158k/mo vs kimi's ~5.75k, which both saves the kimi quota AND removes the
        # budget-wall flakiness perception hit on kimi. Macro web_search extraction
        # is simple numeric parsing that flash handles fine. (Cost directive 2026-06-01.)
        "enabled_toolsets": ["perception", "reflection", "skills", "search"],
        "model_default": "deepseek-v4-flash",
    },
    "deep-research": {
        # Future user — same shape, possibly wider toolset
        "enabled_toolsets": ["perception", "reflection", "skills", "web"],
        "model_default": "kimi-k2.6",
    },
}


SCHEMA = {
    "name": "spawn_subagent",
    "description": (
        "V2.1 orchestration: spawn a focused sub-agent (own AIAgent, own session, "
        "own model override) to execute a specific skill, then return the id of "
        "the result observation the sub-agent wrote. BLOCKS until the sub-agent "
        "completes (typically 1-5 min for plutus-perception). "
        "\n\n"
        "Sub-agent has a RESTRICTED toolset — trading, messaging, and cron tools "
        "are forbidden. For known skills (plutus-perception, deep-research), the "
        "toolset and model default are pre-configured; for ad-hoc spawns, the "
        "caller must pass `enabled_toolsets` explicitly. "
        "\n\n"
        "Use this from plutus-main Phase 0 to spawn plutus-perception. The "
        "perception sub-agent writes a `perception_digest` observation; "
        "plutus-main reads it in Phase 3 via query_latest_perception_digest "
        "instead of doing wide perception itself. "
        "\n\n"
        "Returns {ok: bool, observation_id: int|null, session_id: str, "
        "duration_s: float, final_response: str, error: str|null, timed_out: bool}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Skill name to run (e.g., 'plutus-perception').",
            },
            "expected_event_type": {
                "type": "string",
                "description": (
                    "structured_tags.event_type the sub-agent's result observation "
                    "MUST have. Polled after run completes. E.g., 'perception_digest'."
                ),
            },
            "scope": {
                "type": "string",
                "description": "Optional scope param passed to the sub-agent (e.g., 'standard'|'weekly' for plutus-perception).",
            },
            "extra_context_md": {
                "type": "string",
                "description": "Optional additional context from the orchestrator. Passed verbatim into the kick-off prompt.",
            },
            "for_main_beat_at_unix": {
                "type": "number",
                "description": "Unix ts of the orchestrator's beat. Passed to the sub-agent so it can tag the result observation with this beat's identifier.",
            },
            "model": {
                "type": "string",
                "description": "Override sub-agent model. Defaults to known skill's preferred model (kimi-k2.6 for plutus-perception). Required for unknown skills.",
            },
            "provider": {
                "type": "string",
                "description": "Override provider (e.g., 'opencode-go'). Defaults to runtime resolution.",
            },
            "enabled_toolsets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Override sub-agent's enabled toolsets. Required for unknown skills; defaults configured for plutus-perception/deep-research.",
            },
            "inactivity_timeout_s": {
                "type": "number",
                "description": "Kill sub-agent if no activity for this many seconds. Default 600 (10 min).",
                "default": 600,
            },
        },
        "required": ["skill", "expected_event_type"],
    },
}


def _spawn_subagent(args: Dict[str, Any]) -> str:
    skill = (args.get("skill") or "").strip()
    expected_event_type = (args.get("expected_event_type") or "").strip()
    if not skill:
        return tool_error("spawn_subagent: 'skill' is required")
    if not expected_event_type:
        return tool_error("spawn_subagent: 'expected_event_type' is required")

    defaults = _DEFAULT_TOOLSETS_PER_SKILL.get(skill, {})
    enabled_toolsets: Optional[List[str]] = args.get("enabled_toolsets") or defaults.get("enabled_toolsets")
    if not enabled_toolsets:
        return tool_error(
            f"spawn_subagent: skill {skill!r} has no default toolset configuration; "
            f"pass enabled_toolsets explicitly."
        )

    model = args.get("model") or defaults.get("model_default")

    try:
        result = spawn_subagent_blocking(
            skill_name=skill,
            expected_event_type=expected_event_type,
            model=model,
            provider=args.get("provider"),
            base_url=args.get("base_url"),
            enabled_toolsets=enabled_toolsets,
            scope=args.get("scope"),
            extra_context_md=args.get("extra_context_md"),
            for_main_beat_at_unix=args.get("for_main_beat_at_unix"),
            inactivity_timeout_s=float(args.get("inactivity_timeout_s", 600.0)),
        )
    except Exception as exc:
        return tool_error(f"spawn_subagent: {type(exc).__name__}: {exc}")

    return tool_result(result)


registry.register(
    name="spawn_subagent",
    toolset="identity",  # orchestration belongs with identity (multi-tier coordination)
    schema=SCHEMA,
    handler=lambda args, **kw: _spawn_subagent(args),
    description="V2.1: spawn a focused sub-agent to run a skill, block on completion, return the result observation id.",
    emoji="🪞",
)
