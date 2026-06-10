"""In-process synchronous spawn of an isolated sub-agent.

V2.1 architecture: plutus-main acts as orchestrator. When it needs focused
work done in an isolated context (perception sweep, deep research), it
*spawns* a sub-agent — a separate AIAgent with its own session, its own
model override, and a restricted toolset — runs it to completion in a
worker thread, then reads the structured result observation the sub-agent
wrote.

This module extracts the in-process AIAgent construction pattern from
``cron/scheduler.py:_legacy_run_job`` and exposes it as a synchronous
helper. The cron module remains the user for scheduled-run-this-later
work; this module is the user for inline "spawn-and-block-now" work.

The sync contract: the spawned sub-agent is expected to write exactly
one observation whose ``structured_tags_json.event_type`` matches the
caller's ``expected_event_type``. After the sub-agent's
``run_conversation`` returns, this module queries lifecycle.db for that
observation and returns its id. If none was written, the call returns
``ok=False`` with a diagnostic — the caller (plutus-main) is responsible
for falling back gracefully.

Restricted toolset: callers MUST pass ``enabled_toolsets`` explicitly.
Sub-agents do NOT inherit the caller's tool surface — that would defeat
the focused-context point. For plutus-perception the allowed toolsets
are ``perception`` (fetch_data_point, account_state), ``search``
(web_search for macro blueprints), and a *narrow* slice of ``reflection``
(record_event for the digest only; record_observation for the broken-list
retest). Trade tools, cron tools, messaging — all disabled.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Where Plutus stores his SQLite + config.
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".plutus-agent")))


def _hermes_now_str() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def _load_config_yaml() -> Dict[str, Any]:
    """Read ~/.plutus-agent/config.yaml. Empty dict on any failure."""
    try:
        import yaml
        cfg_path = _HERMES_HOME / "config.yaml"
        if cfg_path.exists():
            with cfg_path.open() as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("subagent_spawn: failed to load config.yaml: %s", exc)
    return {}


def _resolve_provider(
    cfg: Dict[str, Any],
    requested_provider: Optional[str],
    requested_base_url: Optional[str],
) -> Dict[str, Any]:
    """Resolve runtime provider via plutus_cli.runtime_provider, raising on failure.

    Mirrors the legacy-cron resolution shape so spawned sub-agents pick up
    the same OpenCode/credential setup as the cron-fired jobs.
    """
    from plutus_cli.runtime_provider import (
        resolve_runtime_provider,
        format_runtime_provider_error,
    )
    runtime_kwargs: Dict[str, Any] = {
        "requested": requested_provider or os.getenv("HERMES_INFERENCE_PROVIDER"),
    }
    if requested_base_url:
        runtime_kwargs["explicit_base_url"] = requested_base_url
    try:
        return resolve_runtime_provider(**runtime_kwargs)
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc


def _load_external_context() -> Optional[str]:
    """For ``plutus-perception`` spawns, auto-load an optional external-context
    JSON file from disk and embed it in the kick-off prompt so perception sees
    it alongside SOUL.md + WORLDVIEW.md.

    This is the mechanism by which an operator-side process (a daily market
    research brief, an external automation, a separate intelligence agent, etc)
    can feed ancillary context to perception without plutus-main having to
    touch the file. The harness owns the file-path coupling; main reads only
    the digest, which now carries an "External context" section composed by
    perception from this block.

    Path resolution order:
      1. ``$PLUTUS_EXTERNAL_CONTEXT_PATH`` if set
      2. ``$HERMES_HOME/external-context.json``
      3. ``$HERMES_HOME/sebastian-context.json`` (legacy compatibility)

    The JSON is expected to carry a ``generated_at`` ISO8601 timestamp so a
    freshness label can be computed (``STALE`` if > 30h old). Schema is
    otherwise open — perception receives the raw JSON and surfaces whatever
    fields it recognizes (macro_context, narrative_context, polymarket_shifts,
    x_panel_callouts are conventional names but not enforced).

    Returns a markdown block ready to embed in the kick-off prompt, or
    ``None`` if no file is found or it is unparseable. Treats absent files as
    a no-op — the brief is ancillary, not required.
    """
    override = os.environ.get("PLUTUS_EXTERNAL_CONTEXT_PATH")
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(_HERMES_HOME / "external-context.json")
    candidates.append(_HERMES_HOME / "sebastian-context.json")  # legacy

    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        logger.info("subagent_spawn: external-context file present but unparseable: %s", exc)
        return None
    generated_at = data.get("generated_at")
    age_label = "unknown age"
    fresh_label = "fresh"
    if generated_at:
        try:
            import datetime
            gen_dt = datetime.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            age_h = (now_dt - gen_dt).total_seconds() / 3600
            age_label = f"age {age_h:.1f}h"
            fresh_label = "STALE" if age_h > 30 else "fresh"
        except Exception:
            pass
    return (
        f"## External context ({age_label}, {fresh_label})\n\n"
        "```json\n"
        f"{raw.strip()}\n"
        "```"
    )


def _build_subagent_prompt(skill_name: str, scope: Optional[str],
                           extra_context_md: Optional[str],
                           for_main_beat_at_unix: Optional[float]) -> str:
    """Construct the kick-off prompt the sub-agent receives.

    The agent's normal skill-loading machinery resolves ``skill_name`` to
    the ``SKILL.md`` body and inlines it. We just hand the agent a clear
    "run this skill now" instruction plus any context the orchestrator
    wants to pass through.

    For ``plutus-perception`` specifically, this helper auto-loads an
    optional external-context JSON from disk (see ``_load_external_context``)
    and embeds it in the prompt — perception sees it like an inherited
    context file, not as something main has to pass through. Keeps plutus-main
    free of any external-context handling.
    """
    parts = [
        f"[SUB-AGENT INVOCATION — skill={skill_name}]",
        "",
        f"You are a focused sub-agent spawned by plutus-main. Your sole task is to "
        f"execute the **{skill_name}** skill end-to-end and write exactly one "
        f"result observation. You have a restricted toolset — trading, messaging, "
        f"and cron tools are not available to you.",
        "",
        f"Use the skill body for the procedure. Do NOT chat back — write the "
        f"result observation and finish.",
    ]
    if scope:
        parts += ["", f"Scope parameter: **{scope}**"]
    if for_main_beat_at_unix is not None:
        parts += ["", f"This run serves the main beat scheduled at unix={for_main_beat_at_unix:.0f}."]
    if skill_name == "plutus-perception":
        external_block = _load_external_context()
        if external_block:
            parts += ["", external_block]
    if extra_context_md:
        parts += ["", "Additional context from orchestrator:", extra_context_md]
    parts += ["", f"skill_view: \"trading/{skill_name}\""]
    return "\n".join(parts)


def _query_result_observation(
    db, session_id: str, expected_event_type: str, spawn_ts: float,
) -> Optional[Dict[str, Any]]:
    """Find the result observation for this sub-agent run.

    Matches:
      - structured_tags.event_type == expected_event_type
      - ts >= spawn_ts (avoid catching stale observations from a prior collision)
      - EITHER session_id column == sub-agent's session  (cleanest path: event
        handler defaulted from session_id_from_context() which the spawn helper
        now sets via set_session_vars before running the agent)
      - OR structured_tags.session_id_perception == sub-agent's session
        (robustness: catches sub-agents that explicitly pass session_id_perception
        OR legacy event handlers that don't auto-populate)
      - OR structured_tags.tier_session_id == sub-agent's session
        (forward-compat for future event types using the V2 sync-contract tag)

    Returns most recent match.
    """
    cur = db.conn().execute(
        """
        SELECT id, ts, kind, session_id, text_md, structured_tags_json
        FROM observations
        WHERE ts >= ?
          AND json_extract(structured_tags_json, '$.event_type') = ?
          AND (
            session_id = ?
            OR json_extract(structured_tags_json, '$.session_id_perception') = ?
            OR json_extract(structured_tags_json, '$.tier_session_id') = ?
          )
        ORDER BY ts DESC
        LIMIT 1
        """,
        (spawn_ts, expected_event_type, session_id, session_id, session_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


def spawn_subagent_blocking(
    *,
    skill_name: str,
    expected_event_type: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    scope: Optional[str] = None,
    extra_context_md: Optional[str] = None,
    for_main_beat_at_unix: Optional[float] = None,
    inactivity_timeout_s: Optional[float] = 600.0,
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    """Spawn a sub-agent synchronously and return its result observation.

    Returns dict with keys:
      - ``ok`` (bool): True iff the run completed AND a result observation was written
      - ``observation_id`` (int | None): id of the result observation
      - ``session_id`` (str): the sub-agent's session id
      - ``duration_s`` (float): wall-clock from spawn to return
      - ``final_response`` (str): the sub-agent's last assistant message (often empty)
      - ``error`` (str | None): diagnostic on failure
      - ``timed_out`` (bool): True iff inactivity timeout killed the run
    """
    if not skill_name or not isinstance(skill_name, str):
        raise ValueError("skill_name is required")
    if not expected_event_type or not isinstance(expected_event_type, str):
        raise ValueError("expected_event_type is required")
    if enabled_toolsets is None:
        raise ValueError(
            "enabled_toolsets must be provided explicitly — sub-agents must not "
            "inherit the caller's full tool surface."
        )

    # Re-read .env so provider/key changes take effect.
    try:
        from dotenv import load_dotenv
        env_path = _HERMES_HOME / ".env"
        if env_path.exists():
            try:
                load_dotenv(str(env_path), override=True, encoding="utf-8")
            except UnicodeDecodeError:
                load_dotenv(str(env_path), override=True, encoding="latin-1")
    except Exception:
        pass

    cfg = _load_config_yaml()
    model_cfg = cfg.get("model", {})
    if not model:
        if isinstance(model_cfg, str):
            model = model_cfg
        elif isinstance(model_cfg, dict):
            model = model_cfg.get("default", "")
    model = model or os.getenv("HERMES_MODEL", "") or ""

    runtime = _resolve_provider(cfg, provider, base_url)

    # Reasoning config — pick up from config.yaml like cron does.
    from plutus_constants import parse_reasoning_effort
    effort = str(cfg.get("agent", {}).get("reasoning_effort", "")).strip()
    reasoning_config = parse_reasoning_effort(effort)
    if max_iterations is None:
        max_iterations = (
            cfg.get("agent", {}).get("max_turns")
            or cfg.get("max_turns")
            or 90
        )

    pr = cfg.get("provider_routing", {})

    # Credential pool if configured for this provider (allows quota rotation).
    credential_pool = None
    runtime_provider = str(runtime.get("provider") or "").strip().lower()
    if runtime_provider:
        try:
            from agent.credential_pool import load_pool
            pool = load_pool(runtime_provider)
            if pool.has_credentials():
                credential_pool = pool
        except Exception:
            pass

    # SQLite session store so the sub-agent's run is searchable like any other.
    session_db = None
    try:
        from plutus_state import SessionDB
        session_db = SessionDB()
    except Exception:
        pass

    # Sub-agent's own session id (isolated from caller's session).
    sub_session_id = f"subagent_{skill_name.replace('/', '_')}_{_hermes_now_str()}"

    # Build the prompt that triggers skill_view in the sub-agent.
    prompt = _build_subagent_prompt(
        skill_name=skill_name,
        scope=scope,
        extra_context_md=extra_context_md,
        for_main_beat_at_unix=for_main_beat_at_unix,
    )

    # Disabled toolsets default — always block trade/cron/messaging.
    base_disabled = ["cronjob", "messaging", "clarify"]
    if disabled_toolsets:
        for t in disabled_toolsets:
            if t not in base_disabled:
                base_disabled.append(t)

    # Construct AIAgent. Mirrors _legacy_run_job kwargs except:
    #   - platform="subagent" (vs "cron")
    #   - skip_context_files + skip_memory same (we DON'T want SOUL/AGENTS files for sub-agent)
    #     since sub-agent inherits its identity from the skill, not from operator memory.
    from run_agent import AIAgent
    agent = AIAgent(
        model=model,
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        acp_command=runtime.get("command"),
        acp_args=runtime.get("args"),
        max_iterations=max_iterations,
        reasoning_config=reasoning_config,
        prefill_messages=None,
        fallback_model=cfg.get("fallback_providers") or cfg.get("fallback_model"),
        credential_pool=credential_pool,
        providers_allowed=pr.get("only"),
        providers_ignored=pr.get("ignore"),
        providers_order=pr.get("order"),
        provider_sort=pr.get("sort"),
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=base_disabled,
        quiet_mode=True,
        skip_context_files=False,  # sub-agent DOES want SOUL/WORLDVIEW for context
        skip_memory=True,
        platform="subagent",
        session_id=sub_session_id,
        session_db=session_db,
    )

    spawn_ts = time.time()
    timed_out = False
    final_response = ""
    err: Optional[str] = None

    # Run in a worker thread with inactivity-poll timeout, mirroring the cron pattern.
    # Critical: rewrite the session ContextVars BEFORE the agent runs so
    # session_id_from_context() returns the SUB-agent's session id (not the
    # caller's). Without this, dispatcher tools (record_event, fetch_data_point)
    # see the caller's session_id and write their rows under the wrong session
    # — observation #278 bug (2026-05-21).
    def _run_with_sub_session():
        from gateway.session_context import set_session_vars, clear_session_vars
        tokens = set_session_vars(
            platform="subagent",
            chat_id="",
            chat_name=f"sub:{skill_name}",
            session_key=sub_session_id,
        )
        try:
            return agent.run_conversation(prompt)
        finally:
            clear_session_vars(tokens)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    ctx = contextvars.copy_context()
    future = pool.submit(ctx.run, _run_with_sub_session)
    POLL = 5.0
    try:
        if not inactivity_timeout_s or inactivity_timeout_s <= 0:
            result = future.result()
        else:
            result = None
            while True:
                done, _ = concurrent.futures.wait({future}, timeout=POLL)
                if done:
                    result = future.result()
                    break
                idle = 0.0
                if hasattr(agent, "get_activity_summary"):
                    try:
                        idle = agent.get_activity_summary().get("seconds_since_activity", 0.0)
                    except Exception:
                        pass
                if idle >= inactivity_timeout_s:
                    timed_out = True
                    if hasattr(agent, "interrupt"):
                        try:
                            agent.interrupt("Sub-agent inactivity timeout")
                        except Exception:
                            pass
                    break
        if isinstance(result, dict):
            final_response = result.get("final_response", "") or ""
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.exception("Sub-agent '%s' raised: %s", skill_name, err)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    duration_s = time.time() - spawn_ts

    if timed_out:
        return {
            "ok": False,
            "observation_id": None,
            "session_id": sub_session_id,
            "duration_s": duration_s,
            "final_response": final_response,
            "error": f"inactivity timeout after {inactivity_timeout_s:.0f}s",
            "timed_out": True,
        }

    if err:
        return {
            "ok": False,
            "observation_id": None,
            "session_id": sub_session_id,
            "duration_s": duration_s,
            "final_response": final_response,
            "error": err,
            "timed_out": False,
        }

    # Look for the result observation.
    from agent.lifecycle_db import get_lifecycle_db
    db = get_lifecycle_db()
    obs = _query_result_observation(
        db, sub_session_id, expected_event_type, spawn_ts,
    )

    if obs is None:
        return {
            "ok": False,
            "observation_id": None,
            "session_id": sub_session_id,
            "duration_s": duration_s,
            "final_response": final_response,
            "error": (
                f"sub-agent finished but no observation with event_type="
                f"{expected_event_type!r} was written to session {sub_session_id!r}"
            ),
            "timed_out": False,
        }

    return {
        "ok": True,
        "observation_id": obs["id"],
        "session_id": sub_session_id,
        "duration_s": duration_s,
        "final_response": final_response,
        "error": None,
        "timed_out": False,
    }
