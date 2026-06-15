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

import time
from typing import Any, Dict, List, Optional, Sequence

# The operator's authoring floor: predict may reason on data up to this old even
# for fast-cache data points (their tight cache budget governs cache refresh,
# not what is "fresh enough to forecast on").
MIN_PREDICT_FRESHNESS_S = 1800.0  # 30 minutes


def effective_max_age(name: str) -> float:
    """Max age (s) a data point may be and still be author-fresh for predict."""
    from trading.perception.cache import get_staleness_budget

    return max(get_staleness_budget(name), MIN_PREDICT_FRESHNESS_S)


def stale_data_points(
    data_points: Sequence[dict], *, now: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Return the declared data points that are missing or too stale to author on.

    ``data_points`` are strategy-frontmatter dicts (``{name, params?, weight?}``).
    Each returned item is ``{name, params, age_s|None, max_age_s, reason}`` with
    ``reason`` ∈ {'missing', 'stale'}. An empty list means everything is fresh
    enough to ground a prediction.
    """
    from trading.perception.cache import _canonical_key, read_perception_state

    now = now if now is not None else time.time()
    state = read_perception_state()
    cached = state.get("data_points", {}) if isinstance(state, dict) else {}

    stale: List[Dict[str, Any]] = []
    for dp in data_points or []:
        if not isinstance(dp, dict):
            continue
        name = dp.get("name")
        if not name:
            continue
        params = dp.get("params")
        key = _canonical_key(name, params)
        max_age = effective_max_age(name)
        entry = cached.get(key)
        fetched_at = entry.get("fetched_at") if isinstance(entry, dict) else None
        if not isinstance(fetched_at, (int, float)):
            stale.append({"name": name, "params": params, "age_s": None,
                          "max_age_s": round(max_age), "reason": "missing"})
            continue
        age = now - float(fetched_at)
        if age > max_age:
            stale.append({"name": name, "params": params, "age_s": round(age),
                          "max_age_s": round(max_age), "reason": "stale"})
    return stale
