"""find_similar_theses — hybrid FTS5 + sqlite-vec retrieval with optional LLM digest.

Combines BM25 candidates from the FTS5 index over thesis text with cosine
candidates from the sqlite-vec virtual table, merged via Reciprocal Rank
Fusion (RRF). Optionally summarizes the top hits via the Hermes auxiliary
client — mirroring the ``session_search`` digest pattern so Plutus receives
actionable insight rather than raw rows.

Runs against the operator's real lifecycle.db. The digest layer fails
gracefully (returns raw rows) when the auxiliary LLM is unavailable so this
tool stays usable in CLI / test contexts without a live model.
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
    "name": "find_similar_theses",
    "description": (
        "Search past theses for ones semantically similar to a query (free-text "
        "or by existing thesis_id). Hybrid retrieval: FTS5 BM25 + voyage-finance-2 "
        "vector cosine, merged via Reciprocal Rank Fusion. By default summarizes "
        "the top hits via a fast model — set digest=false for raw rows. "
        "Use this for 'have I thought about this before' / 'what happened last "
        "time the market looked like this' loops."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query":     {"type": "string", "description": "Natural-language query."},
            "thesis_id": {"type": "integer", "description": "Reuse an existing thesis's text."},
            "k":         {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
            "digest":    {"type": "boolean", "default": True},
        },
    },
}


_RRF_K = 60  # Reciprocal Rank Fusion smoothing constant; standard value.


def _rrf_merge(*ranked_lists: List[int]) -> List[int]:
    """Merge multiple ranked id lists via Reciprocal Rank Fusion."""
    scores: Dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return sorted(scores.keys(), key=lambda i: scores[i], reverse=True)


def _fts_query(query_text: str) -> str:
    """Build an FTS5 MATCH expression from free-form text.

    Tokenises on word characters (FTS5's MATCH grammar treats most punctuation
    as syntax). Each token is double-quoted so unusual characters can't sneak
    in as operators, and tokens are OR-joined to keep recall high — the RRF
    merge with the vec layer handles precision.
    """
    tokens = re.findall(r"[A-Za-z0-9_]+", query_text)
    if not tokens:
        return ""
    return " OR ".join(f'"{t.replace(chr(34), "")}"' for t in tokens)


def _candidates_fts(conn, query_text: str, k: int) -> List[int]:
    expr = _fts_query(query_text)
    if not expr:
        return []
    rows = conn.execute(
        "SELECT rowid FROM theses_fts WHERE theses_fts MATCH ? "
        "ORDER BY bm25(theses_fts) LIMIT ?",
        (expr, k),
    ).fetchall()
    return [r["rowid"] for r in rows]


def _candidates_vec(conn, query_vec: List[float], k: int) -> List[int]:
    rows = conn.execute(
        "SELECT thesis_id FROM theses_vec "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(query_vec), k),
    ).fetchall()
    return [r["thesis_id"] for r in rows]


async def _summarize(query_text: str, hits: List[Dict[str, Any]]) -> Optional[str]:
    """Hand the top hits to the auxiliary LLM and return an actionable digest."""
    try:
        from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
    except Exception as exc:
        logger.debug("auxiliary_client unavailable for find_similar_theses: %s", exc)
        return None

    system = (
        "You are helping a trading agent recall past theses. Summarize how "
        "the prior theses below relate to the current query. Group by theme "
        "where useful, call out outcomes (if mentioned), and highlight any "
        "patterns worth carrying forward. Be concrete; cite thesis ids."
    )
    formatted = "\n\n---\n\n".join(
        f"thesis #{h['thesis_id']} (symbol={h.get('symbol') or '?'}, "
        f"ts={h.get('ts')}): {h['text_md']}"
        for h in hits
    )
    user = f"Current query:\n{query_text}\n\nPrior theses:\n{formatted}"
    try:
        resp = await async_call_llm(
            task="find_similar_theses",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        return extract_content_or_reasoning(resp) or None
    except Exception as exc:
        logger.warning("find_similar_theses digest failed: %s", exc)
        return None


def _find_similar_theses(args: Dict[str, Any]) -> str:
    db = get_lifecycle_db()
    conn = db.conn()
    k = max(1, min(int(args.get("k") or 5), 25))
    digest_requested = args.get("digest", True)

    query_text = (args.get("query") or "").strip()
    thesis_id = args.get("thesis_id")
    if thesis_id is not None:
        row = conn.execute(
            "SELECT text_md FROM theses WHERE id = ?", (int(thesis_id),)
        ).fetchone()
        if row is None:
            return tool_error(f"thesis {thesis_id} not found")
        query_text = row["text_md"]
    if not query_text:
        return tool_error("find_similar_theses requires query or thesis_id")

    try:
        query_vec = get_embedder().embed_query(query_text)
    except EmbedderError as exc:
        return tool_error(f"embedder unavailable: {exc}")
    except Exception as exc:
        return tool_error(f"embedding query failed: {exc}")

    fts_ids = _candidates_fts(conn, query_text, k * 2)
    vec_ids = _candidates_vec(conn, query_vec, k * 2)
    merged = _rrf_merge(fts_ids, vec_ids)[:k]
    if thesis_id is not None:
        merged = [i for i in merged if i != int(thesis_id)][:k]

    if not merged:
        return tool_result({"count": 0, "hits": [], "digest": None})

    placeholders = ",".join("?" * len(merged))
    rows = conn.execute(
        f"SELECT id AS thesis_id, ts, symbol, text_md, strategy_id "
        f"FROM theses WHERE id IN ({placeholders})",
        merged,
    ).fetchall()
    by_id = {r["thesis_id"]: dict(r) for r in rows}
    hits = [by_id[i] for i in merged if i in by_id]

    digest_text = None
    if digest_requested and hits:
        try:
            digest_text = asyncio.run(_summarize(query_text, hits))
        except RuntimeError:
            # Already inside a running loop (rare for tool calls). Skip digest.
            digest_text = None

    return tool_result({
        "count": len(hits),
        "k": k,
        "hits": hits,
        "digest": digest_text,
    })


registry.register(
    name="find_similar_theses",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _find_similar_theses(args),
    description="Hybrid FTS5 + vec cosine search over theses, with LLM digest.",
    emoji="🔎",
)
