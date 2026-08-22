"""Perception-freshness gate for prediction authoring.

predict must not author a prediction on stale perception data: a zone or an
invalidation reasoned from old readings is a guess, not a grounded forecast
(this is the root cause of the early instant-wrong predictions — thresholds set
against unobserved values). Given a strategy's declared data points, this
reports which are too stale to author on; predict surfaces that to plutus-main,
which re-runs perception before predict retries, and register_prediction refuses
as a hard backstop.

Freshness criterion per data point: ``age <= max(cache_budget, 30 min)``.
- The 30-min FLOOR is the operator's authoring tolerance — a fast signal like
  ``hl_price`` (60s cache budget) is not held to its 60s budget here, because
  reasoning about a setup tolerates minutes-old price.
- Naturally-slow signals keep their own (longer) budget — ``macro_*`` at 4h is
  not falsely blocked at 30 min just because it updates on a 4h cadence.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

# The operator's authoring floor: predict may reason on data up to this old even
# for fast-cache data points (their tight cache budget governs cache refresh,
# not what is "fresh enough to forecast on").
MIN_PREDICT_FRESHNESS_S = 1800.0  # 30 minutes — the intraday floor

# Timescale-aware floors (operator-set 2026-08-22): freshness is an intraday
# concern. A swing thesis rides 4h structure and a position thesis daily
# structure, so demanding 30-minute-fresh readings for them throttled
# authoring (a 12.5% duty cycle against a ~4h sweep) without protecting
# anything those horizons care about. Anchored to each timescale's ATR
# interval (1h / 4h / 1d). An unknown or absent timescale takes the
# strictest floor — erring stale-averse, never stale-blind.
TIMESCALE_FLOORS_S = {
    "intraday": MIN_PREDICT_FRESHNESS_S,   # 30 min
    "swing": 4 * 3600.0,                   # 4 h
    "position": 12 * 3600.0,               # 12 h
}


def effective_max_age(name: str, timescale: Optional[str] = None) -> float:
    """Max age (s) a data point may be and still be author-fresh for predict
    at the given strategy timescale (strictest floor when unknown)."""
    from trading.perception.cache import get_staleness_budget

    floor = TIMESCALE_FLOORS_S.get(timescale, MIN_PREDICT_FRESHNESS_S)
    return max(get_staleness_budget(name), floor)


def _parse_cache_key(key: str) -> tuple:
    """Split a canonical cache key into ``(data_point_name, stored_params_dict)``.

    Keys are ``name`` (no params) or ``name:{compact-json}`` (see
    ``cache._canonical_key``). Data point names never contain ``:`` and the JSON
    payload always begins with ``{``, so a partition on the first ``:`` is
    unambiguous.
    """
    if ":" not in key:
        return key, {}
    name, _, rest = key.partition(":")
    try:
        params = json.loads(rest)
    except Exception:
        params = {}
    return name, (params if isinstance(params, dict) else {})


def _params_subset(declared: Dict[str, Any], stored: Dict[str, Any]) -> bool:
    """True if every declared key/value is present and equal in ``stored``.

    A strategy declares only the params that identify the reading it wants
    (e.g. ``{interval, symbol}``); ``fetch_data_point`` caches under the full
    fetch signature, adding ``length``, ``lookback_bars``, ``std``, … . The
    declared params are therefore a SUBSET of the stored params for the same
    logical reading — an exact cache-key string match misses every TA point.
    Values are compared with a string fallback so ``2`` and ``"2"`` match.
    """
    for k, v in (declared or {}).items():
        if k not in stored:
            return False
        if stored[k] != v and str(stored[k]) != str(v):
            return False
    return True


def stale_data_points(
    data_points: Sequence[dict], *, now: Optional[float] = None,
    timescale: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the declared data points that are missing or too stale to author on.

    ``data_points`` are strategy-frontmatter dicts (``{name, params?, weight?}``).
    Each returned item is ``{name, params, age_s|None, max_age_s, reason}`` with
    ``reason`` ∈ {'missing', 'stale'}. An empty list means everything is fresh
    enough to ground a prediction.

    Matching is by logical param SUBSET, not exact cache-key string equality: a
    strategy's declared params match any cache entry whose stored params are a
    superset, and when several variants of one reading exist the FRESHEST is
    used. This lets a strategy declaring ``{interval:4h, symbol:BTC}`` resolve
    against the entry written as ``{interval:4h, lookback_bars:200, symbol:BTC}``
    and ignore stale leftover variants of the same reading.
    """
    from trading.perception.cache import read_perception_state
    from trading.strategies.files import _normalize_params

    now = now if now is not None else time.time()
    state = read_perception_state()
    cached = state.get("data_points", {}) if isinstance(state, dict) else {}

    # Index every parseable, timestamped cache entry by its base DP name.
    index: Dict[str, List[tuple]] = {}
    for key, entry in cached.items():
        if not isinstance(entry, dict):
            continue
        fetched_at = entry.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            continue
        base, stored_params = _parse_cache_key(key)
        index.setdefault(base, []).append((stored_params, float(fetched_at)))

    stale: List[Dict[str, Any]] = []
    for dp in data_points or []:
        if not isinstance(dp, dict):
            continue
        name = dp.get("name")
        if not name:
            continue
        declared = dp.get("params")
        params = _normalize_params(declared)  # tolerate legacy string params
        max_age = effective_max_age(name, timescale)

        # Freshest cache entry whose stored params superset the declared params.
        best_fetched_at: Optional[float] = None
        for stored_params, fetched_at in index.get(name, []):
            if _params_subset(params, stored_params):
                if best_fetched_at is None or fetched_at > best_fetched_at:
                    best_fetched_at = fetched_at

        if best_fetched_at is None:
            stale.append({"name": name, "params": declared, "age_s": None,
                          "max_age_s": round(max_age), "reason": "missing"})
            continue
        age = now - best_fetched_at
        if age > max_age:
            stale.append({"name": name, "params": declared, "age_s": round(age),
                          "max_age_s": round(max_age), "reason": "stale"})
    return stale
