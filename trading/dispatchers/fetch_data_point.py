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

from typing import Any, Dict

from harness.gateway.session_context import get_synthetic_kind
from trading.dispatchers._helpers import session_id_from_context
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

    # The fetch path itself (cache, snapshot, param filtering) lives in
    # trading.perception.fetch_core, shared with the batch sweep dispatcher.
    from trading.perception.fetch_core import fetch_and_snapshot

    result = fetch_and_snapshot(
        name, params,
        force_fresh=force_fresh,
        session_id=session_id_from_context(),
        tier=_tier_from_synthetic_kind(),
    )
    if not result.pop("ok", False):
        return tool_error(result.get("error", f"fetch of '{name}' failed"))
    return tool_result(result)


registry.register(
    name="fetch_data_point",
    toolset="perception",
    schema=SCHEMA,
    handler=lambda args, **kw: _fetch_data_point(args),
    description="Fetch a registered data point and auto-snapshot it.",
    emoji="🛰️",
)
