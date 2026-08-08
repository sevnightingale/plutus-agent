"""fetch_core — the fetch-and-snapshot path, shared by dispatchers.

Extracted from ``trading/dispatchers/fetch_data_point.py`` (2026-08-08,
Phase 1 of the multi-asset plan) so the batch sweep can fetch dozens of
points without routing each result through an LLM tool call. Behaviour is
identical to the dispatcher's original inline path: per-DP staleness-budget
cache reads, auto-snapshot on every read (cache hits included, tagged
``perception_cache:<source>``), best-effort cache writes, and param
filtering against the fetcher's signature (extras reported, never silently
dropped).

Errors are encoded — ``{"ok": False, "error": ...}`` — never raised; the
callers decide how to surface them.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from trading.perception import cache as perception_cache
from trading.lifecycle.db import get_db
from trading.perception.core import data_point_registry
from trading.dispatchers._helpers import json_dumps_compact


def fetch_and_snapshot(
    name: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    force_fresh: bool = False,
    session_id: Optional[str] = None,
    tier: str = "unknown",
) -> Dict[str, Any]:
    """Fetch one data point through the cache, snapshotting the read.

    Returns ``{"ok": True, "snapshot_id", "name", "source", "ts", "value",
    "cache", ...}`` or ``{"ok": False, "name", "error"}``.
    """
    params = dict(params or {})
    if not name:
        return {"ok": False, "name": name, "error": "fetch requires 'name'"}

    try:
        entry = data_point_registry.lookup(name)
    except KeyError as exc:
        return {"ok": False, "name": name, "error": str(exc)}

    # Filter params to the fetcher's signature — callers routinely pass
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
            (session_id, fetched_at, name, json_dumps_compact(params),
             json_dumps_compact(value), cache_source),
        ).lastrowid
        conn.commit()
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "name": name,
            "source": cache_source,
            "ts": fetched_at,
            "value": value,
            "cache": "hit",
            "age_s": time.time() - fetched_at,
            **({"ignored_params": ignored_params} if ignored_params else {}),
        }

    # Cache miss (or force_fresh) → fetch from source.
    try:
        value = entry.fn(**params) if entry.fn else None
    except Exception as exc:
        return {"ok": False, "name": name,
                "error": f"data point '{name}' fetcher raised: {exc}"}

    ts = time.time()

    snapshot_id = conn.execute(
        "INSERT INTO data_point_snapshots(session_name, ts, name, params_json, value_json, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, ts, name, json_dumps_compact(params),
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

    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "name": name,
        "source": entry.source,
        "ts": ts,
        "value": value,
        "cache": "miss" if not force_fresh else "bypass",
        **({"ignored_params": ignored_params} if ignored_params else {}),
    }
