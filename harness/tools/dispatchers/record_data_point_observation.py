"""record_data_point_observation — close the agentic-blueprint write-back loop.

Some data points (macro_vix, macro_dxy, macro_cpi, ...) are *agentic
query blueprints*: fetch_data_point returns search instructions, the
agent does web_search + extract, and gets the actual value. The
auto-snapshot in fetch_data_point captures the BLUEPRINT, not the
observed value, so a freshness-aware reader would just see "I asked
for VIX" instead of "VIX was 17.35 at 03:00Z."

This tool writes the OBSERVED value into data_point_snapshots so that
find-latest-snapshot returns the actual reading. Plutus calls it after
executing an agentic blueprint.

Snapshot rows written here are tagged with source='agentic_observation'
so they're distinguishable from blueprint rows (source='web_search'
on the registration).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.core.data_point_registry import lookup as lookup_data_point
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "record_data_point_observation",
    "description": (
        "Write back the OBSERVED value for an agentic-blueprint data point "
        "(macro_vix, macro_dxy, macro_cpi, etc.). After fetch_data_point "
        "returns a {_type: 'agentic_query', ...} blueprint and you execute "
        "the web_search + extract, call this with the observed value. The "
        "snapshot becomes a real observation that find_latest_snapshot can "
        "return, instead of just the blueprint metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Name of the data point (e.g. 'macro_vix'). Must match a "
                    "registered data point — verified at write."
                ),
            },
            "value": {
                "type": "object",
                "description": (
                    "The observed value as structured JSON. Should match "
                    "the blueprint's output_schema. E.g. for macro_vix: "
                    "{value: 17.35, risk_regime: 'moderate', "
                    "source: 'https://...', observed_at_iso: '...'}."
                ),
            },
            "params": {
                "type": "object",
                "description": "Params used (typically empty for blueprint queries).",
            },
        },
        "required": ["name", "value"],
    },
}


def _record_data_point_observation(args: Dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return tool_error("name required")
    try:
        lookup_data_point(name)  # will raise KeyError if unknown
    except KeyError:
        return tool_error(f"unknown data point: {name}")

    value = args.get("value")
    if not isinstance(value, dict) or not value:
        return tool_error("value must be a non-empty object")

    params = args.get("params") or {}
    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO data_point_snapshots(ts, name, params_json, "
            "value_json, source) VALUES (?, ?, ?, ?, ?)",
            (
                time.time(),
                name,
                json.dumps(params) if params else None,
                json.dumps(value),
                "agentic_observation",
            ),
        ).lastrowid

    snapshot_id = db._execute_write(_w)

    # ALSO populate the perception cache so downstream readers (regime-detection,
    # next beat) get a cache HIT on fetch_data_point(name) within the DP's staleness
    # budget instead of re-running the (expensive) web_search. This is what makes the
    # macro pipeline self-contained inside perception — there is no separate
    # macro-cache cron anymore; perception resolves macro_vix/macro_dxy/etc. via
    # web_search, calls this tool, and the value is cached for everyone. (2026-06-01)
    try:
        from agent import perception_cache
        perception_cache.write_data_point(
            name,
            value,
            source="agentic_observation",
            params=params or None,
            fetched_by_tier="perception",
        )
    except Exception:
        # Cache is a fail-safe optimization; never let a cache write break the
        # snapshot write-back (the snapshot already succeeded above).
        pass

    return tool_result({
        "snapshot_id": snapshot_id,
        "name": name,
        "source": "agentic_observation",
        "cached": True,
    })


registry.register(
    name="record_data_point_observation",
    toolset="perception",
    schema=SCHEMA,
    handler=lambda args, **kw: _record_data_point_observation(args),
    description="Write the observed value for an agentic-blueprint data point.",
    emoji="🌡️",
)
