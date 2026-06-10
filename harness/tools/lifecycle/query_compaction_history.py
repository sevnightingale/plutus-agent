"""query_compaction_history — V2 visibility into context-window compactions.

Both compaction layers (gateway pre-compress at 85% and agent mid-conversation
compress at 50%) emit a `compaction` event via record_event when they fire.
The event is stored as an observation row with structured_tags marking it.
This dispatcher pulls them back out with optional filters.

Plutus reads compactions to:
- Know when its context was last reset (kimi 256K means compactions are rare
  but each one drops a lot of working memory)
- Retroactively explain "I thought I knew X" when X turns out to be on the
  pre-compaction side
- Tune compression aggressiveness if the rate is too high or too low
"""

from __future__ import annotations

import json
from typing import Any, Dict

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.lifecycle._helpers import rows_to_dicts
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_compaction_history",
    "description": (
        "Return compaction events (context-window compactions at the gateway "
        "or agent loop layers). Each event records pre/post token counts, "
        "compression ratio, session_id transitions, optional focus topic. "
        "Filter by session_id, layer, or time window."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since_ts": {"type": "number"},
            "until_ts": {"type": "number"},
            "session_id": {
                "type": "string",
                "description": "Match either session_id_before or session_id_after.",
            },
            "layer": {
                "type": "string",
                "enum": ["gateway_pre_compress", "agent_mid_conversation"],
            },
            "limit": {"type": "integer", "default": 20},
        },
    },
}


def _query_compaction_history(args: Dict[str, Any]) -> str:
    limit = max(1, min(int(args.get("limit") or 20), 200))
    where = ["json_extract(structured_tags_json, '$.event_type') = 'compaction'"]
    params: list = []

    if args.get("since_ts") is not None:
        where.append("ts >= ?")
        params.append(float(args["since_ts"]))
    if args.get("until_ts") is not None:
        where.append("ts <= ?")
        params.append(float(args["until_ts"]))
    if args.get("layer"):
        where.append("json_extract(structured_tags_json, '$.layer') = ?")
        params.append(args["layer"])
    if args.get("session_id"):
        # Match either before or after (compaction rotates the session_id, so
        # filtering by a single value should find compactions involving that
        # session on either side of the cutover).
        where.append(
            "(json_extract(structured_tags_json, '$.session_id_before') = ? "
            "OR json_extract(structured_tags_json, '$.session_id_after') = ?)"
        )
        params.extend([args["session_id"], args["session_id"]])

    sql = (
        "SELECT id, ts, session_id, text_md, structured_tags_json "
        "FROM observations "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ts DESC LIMIT ?"
    )
    params.append(limit)

    rows = get_lifecycle_db().conn().execute(sql, params).fetchall()
    out = []
    for row in rows:
        try:
            tags = json.loads(row["structured_tags_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            tags = {}
        out.append({
            "observation_id": row["id"],
            "ts": row["ts"],
            "session_id": row["session_id"],
            "text_md": row["text_md"],
            "layer": tags.get("layer"),
            "pre_token_count": tags.get("pre_token_count"),
            "post_token_count": tags.get("post_token_count"),
            "compression_ratio": tags.get("compression_ratio"),
            "pre_message_count": tags.get("pre_message_count"),
            "post_message_count": tags.get("post_message_count"),
            "session_id_before": tags.get("session_id_before"),
            "session_id_after": tags.get("session_id_after"),
            "model": tags.get("model"),
            "focus_topic": tags.get("focus_topic"),
        })
    return tool_result({"count": len(out), "compactions": out})


registry.register(
    name="query_compaction_history",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_compaction_history(args),
    description="List context-window compaction events with metadata.",
    emoji="🗜️",
)
