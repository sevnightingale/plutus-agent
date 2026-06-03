"""find_similar_reflections — hybrid FTS5 + sqlite-vec retrieval over reflections.

Same shape as ``find_similar_theses`` but over the reflections store. Useful
for "what did I learn last time something like this happened" loops in the
``consolidate-learnings`` skill.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

import sqlite_vec

from agent.lifecycle_db import get_lifecycle_db
from tools.core.embedder import EmbedderError, get_embedder
from tools.registry import registry, tool_error, tool_result


logger = logging.getLogger(__name__)


SCHEMA = {
    "name": "find_similar_reflections",
    "description": (
        "Search past reflections for ones semantically similar to a query "
        "or by existing reflection_id. Hybrid FTS5 + voyage-finance-2 vector "
        "cosine via RRF, with optional LLM digest of the top hits. Filter "
        "by reflection_kind (post_trade | loss_postmortem | weekly_review | ad_hoc)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query":           {"type": "string"},
            "reflection_id":   {"type": "integer"},
            "k":               {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
            "reflection_kind": {"type": "string"},
            "digest":          {"type": "boolean", "default": True},
        },
    },
}


_RRF_K = 60


def _rrf_merge(*ranked_lists: List[int]) -> List[int]:
    scores: Dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(scores.keys(), key=lambda i: scores[i], reverse=True)


def _fts_query(query_text: str) -> str:
    """Tokenise free-form text into a safe FTS5 MATCH expression (OR-joined)."""
    tokens = re.findall(r"[A-Za-z0-9_]+", query_text)
    if not tokens:
        return ""
    return " OR ".join(f'"{t.replace(chr(34), "")}"' for t in tokens)


def _candidates_fts(conn, query_text: str, k: int,
                    reflection_kind: Optional[str]) -> List[int]:
    expr = _fts_query(query_text)
    if not expr:
        return []
    if reflection_kind:
        rows = conn.execute(
            "SELECT r.id FROM reflections r JOIN reflections_fts f "
            "ON r.id = f.rowid WHERE reflections_fts MATCH ? AND r.reflection_kind = ? "
            "ORDER BY bm25(reflections_fts) LIMIT ?",
            (expr, reflection_kind, k),
        ).fetchall()
        return [r["id"] for r in rows]
    rows = conn.execute(
        "SELECT rowid FROM reflections_fts WHERE reflections_fts MATCH ? "
        "ORDER BY bm25(reflections_fts) LIMIT ?",
        (expr, k),
    ).fetchall()
    return [r["rowid"] for r in rows]


def _candidates_vec(conn, query_vec: List[float], k: int,
                    reflection_kind: Optional[str]) -> List[int]:
    rows = conn.execute(
        "SELECT reflection_id FROM reflections_vec "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(query_vec), k),
    ).fetchall()
    ids = [r["reflection_id"] for r in rows]
    if reflection_kind and ids:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id FROM reflections WHERE id IN ({placeholders}) "
            f"AND reflection_kind = ?",
            ids + [reflection_kind],
        ).fetchall()
        kept = {r["id"] for r in rows}
        return [i for i in ids if i in kept]
    return ids


async def _summarize(query_text: str, hits: List[Dict[str, Any]]) -> Optional[str]:
    try:
        from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
    except Exception:
        return None
    system = (
        "You are helping a trading agent recall past reflections / lessons. "
        "Summarize the most relevant lessons from the reflections below in "
        "the context of the current query. Cite reflection ids. Highlight "
        "patterns worth carrying forward into the current decision."
    )
    formatted = "\n\n---\n\n".join(
        f"reflection #{h['reflection_id']} (kind={h.get('reflection_kind') or '?'}, "
        f"ts={h.get('ts')}): {h['text_md']}"
        for h in hits
    )
    user = f"Current query:\n{query_text}\n\nPrior reflections:\n{formatted}"
    try:
        resp = await async_call_llm(
            task="find_similar_reflections",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1, max_tokens=600,
        )
        return extract_content_or_reasoning(resp) or None
    except Exception as exc:
        logger.warning("find_similar_reflections digest failed: %s", exc)
        return None


def _find_similar_reflections(args: Dict[str, Any]) -> str:
    db = get_lifecycle_db()
    conn = db.conn()
    k = max(1, min(int(args.get("k") or 5), 25))
    digest_requested = args.get("digest", True)
    reflection_kind = args.get("reflection_kind")

    query_text = (args.get("query") or "").strip()
    rid = args.get("reflection_id")
    if rid is not None:
        row = conn.execute(
            "SELECT text_md FROM reflections WHERE id = ?", (int(rid),)
        ).fetchone()
        if row is None:
            return tool_error(f"reflection {rid} not found")
        query_text = row["text_md"]
    if not query_text:
        return tool_error("find_similar_reflections requires query or reflection_id")

    try:
        query_vec = get_embedder().embed_query(query_text)
    except EmbedderError as exc:
        return tool_error(f"embedder unavailable: {exc}")
    except Exception as exc:
        return tool_error(f"embedding query failed: {exc}")

    fts_ids = _candidates_fts(conn, query_text, k * 2, reflection_kind)
    vec_ids = _candidates_vec(conn, query_vec, k * 2, reflection_kind)
    merged = _rrf_merge(fts_ids, vec_ids)[:k]
    if rid is not None:
        merged = [i for i in merged if i != int(rid)][:k]

    if not merged:
        return tool_result({"count": 0, "hits": [], "digest": None})

    placeholders = ",".join("?" * len(merged))
    rows = conn.execute(
        f"SELECT id AS reflection_id, ts, text_md, reflection_kind "
        f"FROM reflections WHERE id IN ({placeholders})",
        merged,
    ).fetchall()
    by_id = {r["reflection_id"]: dict(r) for r in rows}
    hits = [by_id[i] for i in merged if i in by_id]

    digest_text = None
    if digest_requested and hits:
        try:
            digest_text = asyncio.run(_summarize(query_text, hits))
        except RuntimeError:
            digest_text = None

    return tool_result({
        "count": len(hits),
        "k": k,
        "hits": hits,
        "digest": digest_text,
    })


registry.register(
    name="find_similar_reflections",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _find_similar_reflections(args),
    description="Hybrid FTS5 + vec cosine search over reflections, with LLM digest.",
    emoji="🪞",
)
