"""Perception state cache — `~/.plutus-agent/perception_state.json`.

The cache holds the most recent reading of every data point the desk has
fetched, with a per-DP staleness budget. Every agent that fetches populates
it opportunistically: plutus-ops refreshes its narrow set each 30-min tick
(predictions due, open-position data points, equity); plutus-perception
fills the wide panel on its runs; plutus-main's reads between spawns hit
the cache instead of refetching.

The cache is a fail-safe optimization. Staleness budgets per DP type ensure
freshness where freshness matters (price=60s) and accept staleness where it
doesn't (macro=4h). Candle-derived entries (ta_*, hl_candles, hl_cvd) scale
their budget with the bar interval — a 1d indicator doesn't go stale in the
5 minutes a 1m one does. `force_fresh: true` on fetch_data_point bypasses
the cache entirely when judgment needs the live number.

Reads return None on cache miss or stale entry. Writes are atomic via
tempfile + os.replace. Last writer wins per key — acceptable because each
agent writes only the entries it fetched in this tick, and cross-agent
writes to the same key in the same tick are rare.
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
    # OHLC and TA — 5 min base; interval-scaled upward for slower bars
    # (see get_staleness_budget). force_fresh bypasses entirely.
    "hl_candles": 300.0,
    "ta_": 300.0,
    # Funding + OI — change slowly.
    "hl_funding_and_oi": 600.0,
    "hl_funding_zscore": 600.0,
    # Book microstructure — ephemeral by nature.
    "hl_book_imbalance": 60.0,
    # Session/liquidity context — changes on the hour.
    "session_context": 900.0,
    # Polymarket prediction-market odds — 15 min.
    "poly_": 900.0,
    # On-chain — slow.
    "hl_cvd": 300.0,
    "eth_gas": 300.0,
    # Universe / metadata — very slow.
    "hl_universe": 3600.0,
    # External market context — 30 min.
    "coingecko_": 1800.0,
    "defillama_": 1800.0,
    "btc_dominance_velocity": 1800.0,
    # Macro — slow-moving context; 4h matches the perception staleness floor.
    "macro_": 14400.0,
    # BTC ETF net flow — T+1 daily data; 4h cache (doesn't match macro_ prefix).
    "btc_etf_netflow": 14400.0,
    # Competition state — 1 hour.
    "dgclaw_": 3600.0,
    "acp_": 3600.0,
    # Readiness watchdogs — liveness checks whose whole point is being
    # current; a cached verdict is the thing they exist to prevent. 60s
    # dedupes within a single agent turn, nothing more. (2026-07-02: a
    # poisoned alive=false acp_auth_readiness rode the 1h acp_ budget
    # through two extra ops ticks of false escalations.)
    "hl_trade_readiness": 60.0,
    "acp_auth_readiness": 60.0,
}


def _hermes_home() -> Path:
    """Resolve the active plutus-agent home directory."""
    # Lazy import to avoid circular dep when this module is imported during
    # tools/registry scan.
    from harness.constants import get_hermes_home
    return Path(get_hermes_home())


def _cache_path() -> Path:
    """Absolute path to perception_state.json."""
    return _hermes_home() / "perception_state.json"


# Candle-derived data points whose staleness should scale with bar interval.
_INTERVAL_SCALED_PREFIXES = ("ta_", "hl_candles", "hl_cvd")

# Cap for interval-scaled budgets: never let a slow bar excuse data older
# than the perception staleness floor (4h).
_INTERVAL_BUDGET_CAP_S = 14400.0

_INTERVAL_UNIT_S = {"m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}


def _interval_seconds(interval: str) -> Optional[float]:
    """'15m'/'4h'/'1d'/'1w' → seconds; None when unparseable."""
    if not isinstance(interval, str) or len(interval) < 2:
        return None
    unit = _INTERVAL_UNIT_S.get(interval[-1])
    if unit is None:
        return None
    try:
        return float(interval[:-1]) * unit
    except ValueError:
        return None


def get_staleness_budget(dp_name: str) -> float:
    """Return the staleness budget in seconds for a given data point.

    Lookup order: exact name match → longest matching prefix →
    DEFAULT_STALENESS_S. For candle-derived entries (ta_*, hl_candles,
    hl_cvd) whose cache key carries an interval param, the budget scales
    to half the bar interval (floored at the base budget, capped at 4h):
    a 1d RSI is not stale after 5 minutes, a 1m one is.

    Args:
        dp_name: The cache key (e.g., 'hl_price:{"symbol":"BTC"}') or bare
            data point name (e.g., "hl_price").

    Returns:
        Staleness budget in seconds. Always positive.
    """
    bare, _, params_part = dp_name.partition(":")
    budget: Optional[float] = None
    if bare in _PREFIX_STALENESS_BUDGETS:
        budget = _PREFIX_STALENESS_BUDGETS[bare]
    else:
        best: Optional[tuple[int, float]] = None
        for prefix, b in _PREFIX_STALENESS_BUDGETS.items():
            if bare.startswith(prefix):
                if best is None or len(prefix) > best[0]:
                    best = (len(prefix), b)
        budget = best[1] if best else DEFAULT_STALENESS_S

    if params_part and bare.startswith(_INTERVAL_SCALED_PREFIXES):
        try:
            interval = (json.loads(params_part) or {}).get("interval")
        except (TypeError, json.JSONDecodeError):
            interval = None
        interval_s = _interval_seconds(interval) if interval else None
        if interval_s:
            budget = min(max(budget, interval_s / 2.0), _INTERVAL_BUDGET_CAP_S)
    return budget


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
    budget = max_age_s if max_age_s is not None else get_staleness_budget(key)
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
        "ttl_s": float(ttl_s) if ttl_s is not None else get_staleness_budget(key),
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
