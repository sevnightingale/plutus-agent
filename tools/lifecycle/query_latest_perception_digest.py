"""query_latest_perception_digest — Plutus-main Phase 3 read tool.

V2.1: plutus-main spawns plutus-perception at beat start, which writes a
``perception_digest`` observation (event_type='perception_digest'). This
dispatcher reads the most recent one so plutus-main can interpret without
re-doing the wide perception sweep itself.

Filters:
- ``for_main_beat_at_unix``: match digests written for a specific beat
- ``max_age_s``: enforce freshness (return null if no digest within window)
- ``scope``: filter by 'standard' or 'weekly'

If no matching digest exists, returns ``{found: false, ...}`` with a
hint so plutus-main can fall back (re-spawn perception, or proceed
without digest).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from agent.lifecycle_db import get_lifecycle_db
from tools.registry import registry, tool_result


SCHEMA = {
    "name": "query_latest_perception_digest",
    "description": (
        "Return the most recent perception_digest observation written by the "
        "plutus-perception sub-agent. Used by plutus-main Phase 3 to read the "
        "perception substrate without re-doing the fetch sweep. "
        "\n\n"
        "Filters: for_main_beat_at_unix (match a specific beat), max_age_s "
        "(only return if recent enough), scope ('standard'|'weekly'). "
        "\n\n"
        "Returns {found: bool, observation_id, ts, age_s, text_md, "
        "structured_tags: {scope, fresh_count, failed_dps, watchlist_covered, "
        "strategies_perceived, snapshot_ids_by_dp, broken_list_retest_results, "
        "duration_s, session_id_perception, for_main_beat_at_unix}}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "for_main_beat_at_unix": {
                "type": "number",
                "description": "Match digests tagged with this exact main beat ts.",
            },
            "max_age_s": {
                "type": "number",
                "description": "Return null if no digest younger than this. Default no constraint.",
            },
            "scope": {
                "type": "string",
                "enum": ["standard", "weekly"],
                "description": "Filter by scope.",
            },
        },
    },
}


def _query_latest_perception_digest(args: Dict[str, Any]) -> str:
    now = time.time()
    where = ["json_extract(structured_tags_json, '$.event_type') = 'perception_digest'"]
    params: list = []

    if args.get("for_main_beat_at_unix") is not None:
        where.append("json_extract(structured_tags_json, '$.for_main_beat_at_unix') = ?")
        params.append(float(args["for_main_beat_at_unix"]))

    if args.get("scope"):
        where.append("json_extract(structured_tags_json, '$.scope') = ?")
        params.append(args["scope"])

    max_age_s = args.get("max_age_s")
    if max_age_s is not None:
        where.append("ts >= ?")
        params.append(now - float(max_age_s))

    sql = (
        "SELECT id, ts, session_id, text_md, structured_tags_json "
        "FROM observations "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ts DESC LIMIT 1"
    )

    row = get_lifecycle_db().conn().execute(sql, params).fetchone()

    if row is None:
        return tool_result({
            "found": False,
            "reason": (
                "No perception_digest matching filters. "
                "Plutus-main should spawn plutus-perception now."
            ),
            "filters_applied": {
                k: args[k] for k in ("for_main_beat_at_unix", "max_age_s", "scope")
                if args.get(k) is not None
            },
        })

    try:
        tags = json.loads(row["structured_tags_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        tags = {}

    return tool_result({
        "found": True,
        "observation_id": row["id"],
        "ts": row["ts"],
        "age_s": now - row["ts"],
        "session_id": row["session_id"],
        "text_md": row["text_md"],
        "structured_tags": {
            "scope": tags.get("scope"),
            "for_main_beat_at_unix": tags.get("for_main_beat_at_unix"),
            "fresh_count": tags.get("fresh_count"),
            "failed_dps": tags.get("failed_dps"),
            "watchlist_covered": tags.get("watchlist_covered"),
            "strategies_perceived": tags.get("strategies_perceived"),
            "snapshot_ids_by_dp": tags.get("snapshot_ids_by_dp"),
            "broken_list_retest_results": tags.get("broken_list_retest_results"),
            "duration_s": tags.get("duration_s"),
            "session_id_perception": tags.get("session_id_perception"),
        },
    })


registry.register(
    name="query_latest_perception_digest",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _query_latest_perception_digest(args),
    description="V2.1: read the most recent perception_digest observation.",
    emoji="🔭",
)
