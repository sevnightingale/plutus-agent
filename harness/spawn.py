"""The desk spawn mechanism — AGENT.md context recipes, deterministically assembled.

plutus-main (and cron, for ops) spawns ephemeral specialist agents. An agent
IS a context recipe + a tool reach: ``agents/<name>/AGENT.md`` declares the
model, the toolsets, and the ``reads:`` list that this module resolves into
the prompt BEFORE the model sees a token. No mid-reasoning skill loading,
ever — the structural fix for the V2 failure mode lives in this format.

No-nesting is enforced by omission: the spawn tool is simply absent from
every subagent's toolsets (there is no depth counter).

Every spawn writes a full transcript (every prompt, message, tool call +
result) to ``~/.plutus-agent/ledger/YYYY-MM-DD/<session>-<agent>.md`` —
the audit trail is free, not a discipline.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class AgentSpec:
    def __init__(self, name: str, meta: dict, body_md: str):
        self.name = name
        self.model: str = meta["model"]
        self.toolsets: List[str] = list(meta.get("toolsets") or [])
        self.reads: List[str] = list(meta.get("reads") or [])
        self.returns: Optional[str] = meta.get("returns") or None
        self.spawned_by: List[str] = list(meta.get("spawned_by") or [])
        self.body_md = body_md

        if "spawn" in self.toolsets and name != "plutus-main":
            raise ValueError(
                f"{name}: the spawn toolset is main-only — no-nesting is "
                "enforced by omission (rebuild-architecture.md §1)"
            )


def load_agent(name: str, agents_dir: Optional[Path] = None) -> AgentSpec:
    base = agents_dir if agents_dir is not None else AGENTS_DIR
    path = base / name / "AGENT.md"
    if not path.exists():
        roster = sorted(p.parent.name for p in base.glob("*/AGENT.md"))
        raise FileNotFoundError(f"no agent {name!r} — roster: {roster}")
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: no YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    if meta.get("name") != name:
        raise ValueError(f"{path}: frontmatter name {meta.get('name')!r} != dir {name!r}")
    spec = AgentSpec(name, meta, text[m.end():])

    # Operator override: config.yaml `desk_models: {<agent-name>: <model>}`
    # wins over the AGENT.md frontmatter model. The recipe stays the
    # default; the operator retunes models without editing recipes.
    try:
        from harness.cli.config import load_config
        override = (load_config().get("desk_models") or {}).get(name)
        if override:
            spec.model = str(override)
    except Exception:
        pass  # config unavailable (e.g. isolated tests) — recipe model stands
    return spec


# ───────────────────────────────────────────────────────────────────────────
# reads: resolver
# ───────────────────────────────────────────────────────────────────────────

def _read_zone(file_path: Path, zone: Optional[str]) -> str:
    if not file_path.exists():
        return f"({file_path.name} does not exist yet)"
    text = file_path.read_text(encoding="utf-8")
    if not zone:
        return text
    # A zone is a `## Heading` section; `#doctrine` → `## Doctrine` … next `## `.
    pattern = re.compile(
        rf"^##\s+{re.escape(zone.replace('-', ' '))}\s*$(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return f"({file_path.name} has no '## {zone}' zone)"
    return f"## {zone.title()}\n{m.group(1).strip()}"


def resolve_read(entry: str, home: Optional[Path] = None) -> str:
    """Resolve one ``reads:`` entry into a context block."""
    home = home if home is not None else get_hermes_home()

    if entry.startswith("PLUTUS.md"):
        zone = entry.partition("#")[2] or None
        return _read_zone(home / "PLUTUS.md", zone)
    if entry in ("PERCEPTION.md", "REGIME.md"):
        return _read_zone(home / entry, None)
    if entry == "ledger:today":
        day = time.strftime("%Y-%-m-%-d")
        return _read_zone(home / "ledger" / f"{day}.md", None)
    if entry.startswith("strategies:"):
        from trading.strategies.loader import load_strategies, strategy_context_block
        which = entry.partition(":")[2]
        if which == "live":
            return strategy_context_block()
        if which == "all":
            allof = load_strategies(("test", "active", "dormant"))
            return strategy_context_block() + "\n### Dormant\n" + "\n".join(
                f"- {s.name} ({s.timescale}/{s.mechanism_family}; regime "
                f"{json.dumps(s.regime_applicability, sort_keys=True)})"
                for s in allof if s.status == "dormant"
            )
        raise ValueError(f"unknown strategies read {entry!r}")
    if entry.startswith("lifecycle:"):
        from trading.lifecycle import queries
        from trading.lifecycle.db import get_db
        conn = get_db()
        which = entry.partition(":")[2]
        block = {
            "open-predictions": lambda: queries.open_predictions(conn),
            "due-predictions": lambda: queries.due_predictions(conn),
            "open-position": lambda: queries.open_position(conn),
            "recent-outcomes": lambda: queries.recent_outcomes(conn),
        }
        if which not in block:
            raise ValueError(f"unknown lifecycle read {entry!r}")
        return f"## lifecycle:{which}\n```json\n" + json.dumps(
            block[which](), indent=1, default=str
        ) + "\n```"
    raise ValueError(f"unknown reads entry {entry!r}")


def assemble_context(spec: AgentSpec, task_md: str) -> str:
    parts: List[str] = []
    for entry in spec.reads:
        try:
            parts.append(resolve_read(entry))
        except Exception as exc:
            # A failed read is stated, never silently dropped.
            parts.append(f"## {entry}\n(READ FAILED: {exc})")
    parts.append(spec.body_md.strip())
    parts.append(f"# Task\n{task_md.strip()}")
    return "\n\n---\n\n".join(parts)


# ───────────────────────────────────────────────────────────────────────────
# Return contracts — minimal required-keys validation per named contract
# ───────────────────────────────────────────────────────────────────────────

RETURN_CONTRACTS: Dict[str, List[str]] = {
    "perception_report": ["updated", "failed", "notable"],
    "regime_report": ["rows", "flips"],
    "prediction_batch": ["predictions", "actionable", "slots"],
    "trade_report": ["ok", "fill", "sl", "verify"],
    "ops_report": ["resolved", "wakes_enqueued"],
    "reflect_report": ["status_changes", "weight_updates", "sizing_review",
                       "seed_report"],
}


def validate_return(contract: str, payload: Any) -> List[str]:
    required = RETURN_CONTRACTS.get(contract)
    if required is None:
        return [f"unknown return contract {contract!r}"]
    if not isinstance(payload, dict):
        return [f"{contract}: expected a JSON object, got {type(payload).__name__}"]
    return [f"{contract}: missing key {k!r}" for k in required if k not in payload]


def parse_return(contract: Optional[str], final_text: str) -> Dict[str, Any]:
    """Parse + validate the agent's final message against its contract.

    Returns {"ok": bool, "payload": dict|None, "problems": [...], "raw": str}.
    """
    if contract is None:
        return {"ok": True, "payload": None, "problems": [], "raw": final_text}
    text = final_text.strip()
    # tolerate a fenced block around the JSON
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return {"ok": False, "payload": None,
                "problems": [f"final message is not JSON: {exc}"], "raw": final_text}
    problems = validate_return(contract, payload)
    return {"ok": not problems, "payload": payload, "problems": problems,
            "raw": final_text}


# ───────────────────────────────────────────────────────────────────────────
# Transcripts
# ───────────────────────────────────────────────────────────────────────────

def transcript_path(agent_name: str, session_name: str,
                    home: Optional[Path] = None) -> Path:
    home = home if home is not None else get_hermes_home()
    day = time.strftime("%Y-%-m-%-d")
    d = home / "ledger" / day
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    return d / f"{session_name}-{agent_name}-{stamp}.md"


def write_transcript(path: Path, spec: AgentSpec, prompt: str,
                     messages: List[dict], result: dict) -> None:
    lines = [
        f"# {spec.name} transcript",
        f"- model: {spec.model}",
        f"- toolsets: {', '.join(spec.toolsets)}",
        f"- written: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        "",
        "## Assembled prompt",
        "````",
        prompt,
        "````",
        "",
        "## Conversation",
    ]
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        lines.append(f"### {role}")
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                lines.append(f"- tool_call: {fn.get('name')}({fn.get('arguments')})")
        lines.append(str(content))
        lines.append("")
    lines += ["## Result", "```json",
              json.dumps({k: v for k, v in result.items() if k != "raw"},
                         indent=1, default=str), "```"]
    path.write_text("\n".join(lines), encoding="utf-8")


# ───────────────────────────────────────────────────────────────────────────
# The runner
# ───────────────────────────────────────────────────────────────────────────

def _load_config_yaml() -> dict:
    try:
        cfg_path = get_hermes_home() / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("spawn: failed to load config.yaml: %s", exc)
    return {}


def _resolve_provider(cfg: dict) -> dict:
    from harness.cli.runtime_provider import (
        format_runtime_provider_error,
        resolve_runtime_provider,
    )
    import os
    try:
        return resolve_runtime_provider(
            requested=os.getenv("HERMES_INFERENCE_PROVIDER"),
        )
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc


def spawn_agent(
    name: str,
    task_md: str,
    *,
    session_name: str,
    agents_dir: Optional[Path] = None,
    inactivity_timeout_s: float = 900.0,
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    """Spawn a desk agent synchronously and return its validated result.

    Returns {"ok", "payload", "problems", "raw", "transcript", "duration_s",
    "error"}. The caller (plutus-main) writes lifecycle events from the
    payload — the spawn mechanism itself never writes to lifecycle.db.
    """
    import concurrent.futures
    import contextvars

    spec = load_agent(name, agents_dir)
    prompt = assemble_context(spec, task_md)

    cfg = _load_config_yaml()
    runtime = _resolve_provider(cfg)
    from harness.constants import parse_reasoning_effort
    effort = str(cfg.get("agent", {}).get("reasoning_effort", "")).strip()
    if max_iterations is None:
        max_iterations = cfg.get("agent", {}).get("max_turns") or 90

    sub_session = f"{session_name}-{name}"

    from harness.run_agent import AIAgent
    agent = AIAgent(
        model=spec.model,
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        max_iterations=max_iterations,
        reasoning_config=parse_reasoning_effort(effort),
        fallback_model=cfg.get("fallback_providers") or cfg.get("fallback_model"),
        enabled_toolsets=spec.toolsets,
        # The desk's no-nesting + no-side-channel invariants: never grant
        # spawn/cron/messaging to a subagent regardless of its declaration.
        disabled_toolsets=["spawn", "cronjob", "messaging", "clarify"],
        quiet_mode=True,
        # Identity comes from PLUTUS.md#doctrine via reads:, not SOUL files.
        skip_context_files=True,
        skip_memory=True,
        platform="subagent",
        session_id=sub_session,
    )

    started = time.time()
    final_text = ""
    messages: List[dict] = []
    err: Optional[str] = None
    timed_out = False

    def _run():
        from harness.gateway.session_context import set_session_vars, clear_session_vars
        tokens = set_session_vars(
            platform="subagent", chat_id="",
            chat_name=f"desk:{name}", session_key=sub_session,
        )
        try:
            return agent.run_conversation(prompt)
        finally:
            clear_session_vars(tokens)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    ctx = contextvars.copy_context()
    future = pool.submit(ctx.run, _run)
    try:
        while True:
            done, _ = concurrent.futures.wait({future}, timeout=5.0)
            if done:
                result = future.result()
                if isinstance(result, dict):
                    final_text = result.get("final_response") or ""
                    messages = result.get("messages") or []
                break
            idle = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    idle = agent.get_activity_summary().get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if inactivity_timeout_s and idle >= inactivity_timeout_s:
                timed_out = True
                if hasattr(agent, "interrupt"):
                    try:
                        agent.interrupt("desk agent inactivity timeout")
                    except Exception:
                        pass
                break
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.exception("desk agent %s raised: %s", name, err)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    parsed = parse_return(spec.returns, final_text)
    if timed_out:
        parsed["ok"] = False
        parsed["problems"].append(f"inactivity timeout after {inactivity_timeout_s:.0f}s")
    if err:
        parsed["ok"] = False
        parsed["problems"].append(err)
    parsed["duration_s"] = round(time.time() - started, 1)
    parsed["error"] = err

    try:
        tpath = transcript_path(name, session_name)
        write_transcript(tpath, spec, prompt, messages, parsed)
        parsed["transcript"] = str(tpath)
    except Exception as exc:
        logger.error("transcript write failed for %s: %s", name, exc)
        parsed["transcript"] = None

    return parsed
