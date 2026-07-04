"""fetch_data_point — registry-dispatched perception tool.

Dispatches to the appropriate fetcher in ``tools.core.data_point_registry``,
auto-snapshots the result to ``data_point_snapshots`` in lifecycle.db, and
returns the value plus the snapshot id.

V2: integrates with `agent.perception_cache` (Stratum 1.7). Reads consult
the cache first, returning the cached entry when fresh enough per per-DP
staleness budget. On miss or stale, fetches fresh and writes the cache.
Set `force_fresh: true` to bypass the cache (used by regime-detection and
similar where staleness is unacceptable).

Auto-snapshot is the architectural feature here (PLUTUS Principle: every
perception is captured for free). The agent never calls a separate
``record_observation`` — it just fetches, and the trace appears. Cache
hits ALSO get a snapshot (with `source="perception_cache:<source>"`) so
the lifecycle record always shows what the agent read, regardless of
fetch path.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from trading.perception import cache as perception_cache
from trading.lifecycle.db import get_db
from harness.gateway.session_context import get_synthetic_kind
from trading.perception.core import data_point_registry
from trading.dispatchers._helpers import json_dumps_compact, session_id_from_context
from harness.tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "fetch_data_point",
    "description": (
        "Fetch a registered data point (price, funding, OI, indicator, holdings, ...). "
        "Use list_data_points first to discover what's available. "
        "Every fetch is auto-snapshotted to the lifecycle store, and the snapshot id "
        "is returned so you can link it to a thesis later via "
        "record_event('thesis', snapshot_ids=[...]). "
        "By default, reads the perception_state cache when the cached value is "
        "fresh per the data-point's staleness budget (price=60s, ta=300s, macro=4h, etc.); "
        "set `force_fresh: true` to bypass the cache."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Data point name as registered (e.g., 'hl_funding_rate').",
            },
            "params": {
                "type": "object",
                "description": "Keyword arguments passed to the fetcher (e.g., {symbol: 'BTC'}).",
                "additionalProperties": True,
            },
            "force_fresh": {
                "type": "boolean",
                "description": "Bypass the perception cache and fetch from source. Default false.",
                "default": False,
            },
        },
        "required": ["name"],
    },
}


def _tier_from_synthetic_kind() -> str:
    """Derive a coarse tier label from the synthetic_kind marker.

    Used to tag perception_cache entries with `fetched_by_tier` for sync-
    contract provenance debugging. Falls back to 'unknown' for direct
    operator turns or non-cron contexts.
    """
    kind = get_synthetic_kind()
    if not kind:
        return "operator"
    if kind.startswith("cron:plutus-ops"):
        return "ops"
    return kind


def _fetch_data_point(args: Dict[str, Any]) -> str:
    name = args.get("name", "").strip()
    params = args.get("params") or {}
    force_fresh = bool(args.get("force_fresh", False))
    if not name:
        return tool_error("fetch_data_point requires 'name'")

    try:
        entry = data_point_registry.lookup(name)
    except KeyError as exc:
        return tool_error(str(exc))

    # Filter params to the fetcher's signature — the model routinely passes
    # contextual extras (symbol/venue on global DPs) and a raw **params call
    # crashed the fetch. Ignored keys are reported back, never dropped
    # silently. Filtering BEFORE the cache read also unifies cache keys.
    ignored_params: list = []
    if entry.fn is not None and params:
        import inspect
        sig = inspect.signature(entry.fn)
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD
                   for p in sig.parameters.values()):
            kept = {k: v for k, v in params.items() if k in sig.parameters}
            ignored_params = sorted(set(params) - set(kept))
            params = kept

    conn = get_db()
    sid = session_id_from_context()
    tier = _tier_from_synthetic_kind()

    # Cache lookup (unless force_fresh). Uses the per-DP staleness budget.
    cached_entry = None
    if not force_fresh:
        try:
            cached_entry = perception_cache.read_data_point(name, params=params)
        except Exception:
            # Cache problems are never fatal — fall through to fresh fetch.
            cached_entry = None

    if cached_entry is not None:
        value = cached_entry["value"]
        fetched_at = float(cached_entry.get("fetched_at", time.time()))
        cache_source = f"perception_cache:{entry.source}"

        snapshot_id = conn.execute(
            "INSERT INTO data_point_snapshots(session_name, ts, name, params_json, value_json, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, fetched_at, name, json_dumps_compact(params),
             json_dumps_compact(value), cache_source),
        ).lastrowid
        conn.commit()
        return tool_result({
            "snapshot_id": snapshot_id,
            "name": name,
            "source": cache_source,
            "ts": fetched_at,
            "value": value,
            "cache": "hit",
            "age_s": time.time() - fetched_at,
            **({"ignored_params": ignored_params} if ignored_params else {}),
        })

    # Cache miss (or force_fresh) → fetch from source.
    try:
        value = entry.fn(**params) if entry.fn else None
    except Exception as exc:
        return tool_error(f"data point '{name}' fetcher raised: {exc}")

    ts = time.time()

    snapshot_id = conn.execute(
        "INSERT INTO data_point_snapshots(session_name, ts, name, params_json, value_json, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, ts, name, json_dumps_compact(params),
         json_dumps_compact(value), entry.source),
    ).lastrowid
    conn.commit()

    # Populate the cache for downstream tiers.
    try:
        perception_cache.write_data_point(
            name, value,
            source=entry.source,
            params=params,
            fetched_by_tier=tier,
        )
    except Exception:
        # Best-effort cache write; never fail the fetch on cache write error.
        pass

    return tool_result({
        "snapshot_id": snapshot_id,
        "name": name,
        "source": entry.source,
        "ts": ts,
        "value": value,
        "cache": "miss" if not force_fresh else "bypass",
        **({"ignored_params": ignored_params} if ignored_params else {}),
    })


registry.register(
    name="fetch_data_point",
    toolset="perception",
    schema=SCHEMA,
    handler=lambda args, **kw: _fetch_data_point(args),
    description="Fetch a registered data point and auto-snapshot it.",
    emoji="🛰️",
)
