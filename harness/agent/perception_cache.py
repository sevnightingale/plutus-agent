"""Perception state cache — `~/.plutus-agent/perception_state.json`.

V2 Stratum 1.7 (per PLUTUS.md). The cache holds the most recent reading of
every data point Plutus has fetched, with a per-DP staleness budget. All
three tiers (plutus-main, plutus-ops, plutus-thesis) populate it
opportunistically based on what they fetch for their own purposes:

- plutus-ops refreshes its narrow set every 30 min (predictions due,
  open-position data points, equity)
- plutus-main reads the cache to know current state across wide perception;
  refetches stale entries
- plutus-thesis monitors refresh their declared `data_points_to_watch` at
  their cadence

The cache is a fail-safe optimization. Staleness budgets per DP type ensure
freshness where freshness matters (price=60s) and accept staleness where it
doesn't (macro=4h). V1's "no caching at fetch layer" doctrine turned out to
be wrong at 4 main beats/day — refetching every passive watchlist asset
every beat is wasteful.

Reads return None on cache miss or stale entry. Writes are atomic via
tempfile + os.replace. Last writer wins per key — acceptable because each
tier writes only the entries it fetched in this tick, and cross-tier writes
to the same key in the same tick are rare.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


PERCEPTION_CACHE_VERSION = 1


# Per-DP staleness budgets. Looked up by exact name first; if no exact match,
# falls back to prefix-based category match; if still no match, returns
# the DEFAULT_STALENESS_S sentinel (which callers can interpret as "if you
# care about staleness, pass max_age_s explicitly").
DEFAULT_STALENESS_S = 300.0

_PREFIX_STALENESS_BUDGETS = {
    # Fast-moving venue state — refetch eagerly.
    "hl_price": 60.0,
    "hl_orderbook": 60.0,
    "hl_total_equity": 60.0,
    "hl_drawdown_from_peak": 60.0,
    "hl_holdings": 60.0,
    # OHLC and TA — 5 min (assumes 1m-15m timeframes; agent can force_fresh
    # for higher-frequency needs).
    "hl_candles": 300.0,
    "ta_": 300.0,
    # Funding + OI — change slowly.
    "hl_funding_and_oi": 600.0,
    # On-chain — slow.
    "hl_cvd": 300.0,
    "eth_gas": 300.0,
    # Universe / metadata — very slow.
    "hl_universe": 3600.0,
    # External market context — 30 min.
    "coingecko_": 1800.0,
    "defillama_": 1800.0,
    "btc_dominance_velocity": 1800.0,
    # Macro — pre-cached separately by plutus-macro-cache cron at 4h cadence.
    "macro_": 14400.0,
    # Competition state — 1 hour.
    "dgclaw_": 3600.0,
    "acp_": 3600.0,
}


def _hermes_home() -> Path:
    """Resolve the active plutus-agent home directory."""
    # Lazy import to avoid circular dep when this module is imported during
    # tools/registry scan.
    from plutus_constants import get_hermes_home
    return Path(get_hermes_home())


def _cache_path() -> Path:
    """Absolute path to perception_state.json."""
    return _hermes_home() / "perception_state.json"


def get_staleness_budget(dp_name: str) -> float:
    """Return the staleness budget in seconds for a given data point.

    Lookup order: exact name match → longest matching prefix → DEFAULT_STALENESS_S.

    Args:
        dp_name: The cache key (e.g., "hl_price:BTC") or bare data point
            name (e.g., "hl_price"). The colon and anything after is stripped
            for matching.

    Returns:
        Staleness budget in seconds. Always positive.
    """
    bare = dp_name.split(":", 1)[0] if ":" in dp_name else dp_name
    # Exact match wins.
    if bare in _PREFIX_STALENESS_BUDGETS:
        return _PREFIX_STALENESS_BUDGETS[bare]
    # Longest matching prefix wins.
    best: Optional[tuple[int, float]] = None
    for prefix, budget in _PREFIX_STALENESS_BUDGETS.items():
        if bare.startswith(prefix):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), budget)
    return best[1] if best else DEFAULT_STALENESS_S


def _canonical_key(name: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Build a cache key from a data point name + optional params.

    Different params on the same DP cache independently — e.g., `hl_price:BTC`
    and `hl_price:ETH` are separate entries. Params are sorted by key and
    JSON-compacted so logically-equal param dicts produce identical keys.
    """
    if not params:
        return name
    # Compact + sorted for stable keys; default=str to handle non-JSON-native
    # values (rare but possible from typed params).
    canon = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return f"{name}:{canon}"


def read_perception_state() -> Dict[str, Any]:
    """Read and return the full perception state dict.

    Returns an empty (but well-formed) dict on missing file or parse error.
    Does NOT raise — perception cache is best-effort by design.
    """
    path = _cache_path()
    if not path.exists():
        return {"version": PERCEPTION_CACHE_VERSION, "updated_at": 0.0, "data_points": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict) or "data_points" not in state:
            return {"version": PERCEPTION_CACHE_VERSION, "updated_at": 0.0, "data_points": {}}
        # Backward-compat: if version is missing or different, return as-is —
        # readers handle individual entries' shapes themselves.
        state.setdefault("version", PERCEPTION_CACHE_VERSION)
        state.setdefault("updated_at", 0.0)
        return state
    except (OSError, json.JSONDecodeError):
        return {"version": PERCEPTION_CACHE_VERSION, "updated_at": 0.0, "data_points": {}}


def read_data_point(
    name: str,
    params: Optional[Dict[str, Any]] = None,
    max_age_s: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Read a single data point entry if cached and not stale.

    Args:
        name: Data point name (e.g., "hl_price").
        params: Optional params dict that identifies a specific reading
            (e.g., {"symbol": "BTC"}).
        max_age_s: Maximum acceptable staleness. If None, uses
            `get_staleness_budget(name)`.

    Returns:
        The cached entry dict (with keys: value, source, fetched_at,
        fetched_by_tier, ttl_s) if present and fresh enough. None on miss
        or stale.
    """
    key = _canonical_key(name, params)
    state = read_perception_state()
    entry = state.get("data_points", {}).get(key)
    if not entry or not isinstance(entry, dict):
        return None
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    budget = max_age_s if max_age_s is not None else get_staleness_budget(name)
    age = time.time() - float(fetched_at)
    if age > budget:
        return None
    return entry


def write_data_point(
    name: str,
    value: Any,
    *,
    source: str,
    params: Optional[Dict[str, Any]] = None,
    fetched_by_tier: Optional[str] = None,
    ttl_s: Optional[float] = None,
) -> None:
    """Upsert a data point reading into the cache (atomic write).

    Reads current state, mutates one key, writes the full state back via
    tempfile + os.replace (Linux-atomic on same filesystem). Last writer
    wins per key — acceptable because each tier writes only what it fetched.

    Args:
        name: Data point name.
        value: The reading (any JSON-serializable shape — usually a dict).
        source: Source identifier (registry source, e.g., "hyperliquid").
        params: Optional params dict that identifies the specific reading.
        fetched_by_tier: "main" | "ops" | "thesis_monitor" | other tier id.
            Recorded for sync-contract provenance debugging.
        ttl_s: Per-entry TTL hint. If omitted, defaults to the budget
            for this DP name.
    """
    key = _canonical_key(name, params)
    now = time.time()
    state = read_perception_state()
    data_points = state.setdefault("data_points", {})
    data_points[key] = {
        "value": value,
        "source": source,
        "fetched_at": now,
        "fetched_by_tier": fetched_by_tier or "unknown",
        "ttl_s": float(ttl_s) if ttl_s is not None else get_staleness_budget(name),
    }
    state["updated_at"] = now
    state["version"] = PERCEPTION_CACHE_VERSION
    _atomic_write_json(_cache_path(), state)


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON via tempfile + os.replace for atomicity on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile in the same dir so os.replace is atomic (same FS).
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_name, str(path))
    except Exception:
        # On any error, clean up the temp file rather than leaving litter.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def clear_cache() -> None:
    """Delete the cache file. Used in tests and operator-driven reset."""
    path = _cache_path()
    if path.exists():
        path.unlink()
