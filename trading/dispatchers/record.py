"""record() — plutus-main's fan-out recorder (one narrative, three audiences).

Main hands record() the narrative once plus a few fields; the tool fans out
to lifecycle.db, the daily ledger journal, and (for forum posts) the Degen
Arena forum — so main never holds schemas, thread ids, or formatting in
context. Promote to a scribe agent only if this proves insufficient
(rebuild-architecture.md §2 — topology unaffected either way).

Failure semantics: every fan-out target reports independently; partial
failure returns ok=false with per-target errors. Nothing fails silently.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.constants import get_hermes_home
from harness.tools.registry import registry, tool_error, tool_result

VALID_KINDS = ("decision", "observation", "journal", "forum_post", "eod")

RECORD_SCHEMA = {
    "name": "record",
    "description": (
        "Record a desk event once; the tool fans out to lifecycle.db, the "
        "daily ledger journal, and (kind=forum_post) the Arena forum. "
        "kinds: decision (requires thesis_id+action; conviction optional) | "
        "observation (requires text; kind_tag optional) | journal (requires "
        "text) | forum_post (requires title+text+agent_id+thread_id — posts "
        "AND journals the same narrative) | eod (requires text — the "
        "end-of-day journal close). Every kind appends to today's journal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(VALID_KINDS)},
            "text": {"type": "string", "description": "The narrative (markdown)."},
            "title": {"type": "string"},
            "thesis_id": {"type": "integer"},
            "action": {"type": "string"},
            "conviction": {"type": "number"},
            "params": {"type": "object"},
            "kind_tag": {
                "type": "string",
                "description": "observation sub-kind (noticed/watching/almost_traded/...)",
            },
            "symbol": {"type": "string"},
            "strategy_name": {"type": "string"},
            "prediction_ids": {"type": "array", "items": {"type": "integer"}},
            "agent_id": {"type": "string", "description": "forum_post: Arena agent id"},
            "thread_id": {"type": "string", "description": "forum_post: forum thread id"},
        },
        "required": ["kind", "text"],
    },
}


def _journal_path(home: Optional[Path] = None) -> Path:
    home = home if home is not None else get_hermes_home()
    day = time.strftime("%Y-%-m-%-d")
    d = home / "ledger"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{day}.md"


def _journal_append(heading: str, text: str) -> str:
    path = _journal_path()
    stamp = time.strftime("%H:%M")
    block = f"\n## {stamp} — {heading}\n\n{text.strip()}\n"
    if not path.exists():
        day = time.strftime("%Y-%-m-%-d")
        path.write_text(f"# Journal {day}\n" + block, encoding="utf-8")
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(block)
    return str(path)


def _session_name() -> Optional[str]:
    try:
        from trading.dispatchers._helpers import session_id_from_context
        return session_id_from_context()
    except Exception:
        return None


def _record(args: Dict[str, Any]) -> str:
    kind = args.get("kind", "")
    text = (args.get("text") or "").strip()
    if kind not in VALID_KINDS:
        return tool_error(f"kind must be one of {VALID_KINDS}")
    if not text:
        return tool_error("record requires text")

    results: Dict[str, Any] = {}
    errors: List[str] = []
    session = _session_name()

    from trading.lifecycle import write
    from trading.lifecycle.db import get_db
    conn = get_db()

    # 1. lifecycle.db
    try:
        if kind == "decision":
            thesis_id = args.get("thesis_id")
            action = args.get("action")
            if not thesis_id or not action:
                return tool_error("kind=decision requires thesis_id and action")
            results["decision_id"] = write.record_decision(
                conn, thesis_id=int(thesis_id), action=str(action),
                agent="plutus-main",
                conviction=float(args.get("conviction", 0.5)),
                params=args.get("params"),
            )
        elif kind in ("observation", "forum_post", "eod", "journal"):
            obs_kind = {
                "observation": args.get("kind_tag") or "noticed",
                "forum_post": "forum_post",
                "eod": "eod",
                "journal": "journal",
            }[kind]
            results["observation_id"] = write.record_observation(
                conn, text_md=text, agent="plutus-main", kind=obs_kind,
                symbol=args.get("symbol"),
                strategy_name=args.get("strategy_name"),
                prediction_ids=args.get("prediction_ids") or (),
                session_name=session,
            )
    except Exception as exc:
        errors.append(f"lifecycle: {type(exc).__name__}: {exc}")

    # 2. ledger journal (every kind lands in the day's narrative)
    try:
        heading = {
            "decision": f"decision: {args.get('action', '')}".strip(": "),
            "observation": f"observation ({args.get('kind_tag') or 'noticed'})",
            "journal": "journal",
            "forum_post": f"forum post: {args.get('title', '')}".strip(": "),
            "eod": "EOD",
        }[kind]
        results["journal"] = _journal_append(heading, text)
    except Exception as exc:
        errors.append(f"journal: {type(exc).__name__}: {exc}")

    # 3. Arena forum (forum_post only) — posting is doctrine, not optional;
    # a failed post is a loud error main must react to.
    if kind == "forum_post":
        agent_id = args.get("agent_id")
        thread_id = args.get("thread_id")
        title = args.get("title")
        if not all([agent_id, thread_id, title]):
            errors.append("forum: forum_post requires agent_id, thread_id, title")
        else:
            try:
                from trading.integrations.dgclaw.operations import (
                    _dgclaw_forum_create_post,
                )
                raw = _dgclaw_forum_create_post({
                    "agent_id": agent_id, "thread_id": thread_id,
                    "title": title, "content": text,
                })
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("error"):
                    errors.append(f"forum: {parsed['error']}")
                else:
                    results["forum"] = "posted"
            except Exception as exc:
                errors.append(f"forum: {type(exc).__name__}: {exc}")

    payload = {"ok": not errors, "kind": kind, **results}
    if errors:
        payload["errors"] = errors
    return tool_result(payload)


registry.register(
    name="record",
    toolset="record",
    schema=RECORD_SCHEMA,
    handler=lambda args, **kw: _record(args),
    description="Fan-out recorder: lifecycle.db + ledger journal + Arena forum.",
    emoji="🖋️",
)
