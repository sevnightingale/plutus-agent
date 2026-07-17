"""Session/liquidity context data point.

One registration: ``session_context`` — the UTC trading session, weekend
flag, and where this hour of day sits in the symbol's own 24-hour volume
profile (median hourly volume over the lookback). Gives session-thesis
strategies (US-open squeezes, Asia-hours drift) real evidence instead of
the analyst inferring the session from a timestamp.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List

from trading.integrations.hyperliquid.data_points import hl_candles as _hl_candles
from trading.perception.core.data_point_registry import register_data_point

# Half-open UTC hour ranges. The EU/US overlap (13:00-16:00 UTC — US morning,
# European afternoon) is historically the most liquid crypto window.
_SESSIONS = [
    (0, 7, "asia"),
    (7, 13, "europe"),
    (13, 16, "eu_us_overlap"),
    (16, 21, "us"),
    (21, 24, "off_hours"),
]


def _session_label(utc_hour: int) -> str:
    for lo, hi, name in _SESSIONS:
        if lo <= utc_hour < hi:
            return name
    raise ValueError(f"hour {utc_hour} outside 0-23")


def _hourly_profile(candles: List[Dict[str, Any]]) -> Dict[int, float]:
    """Median volume per UTC hour-of-day from 1h candles ({t: open ms, v})."""
    buckets: Dict[int, List[float]] = defaultdict(list)
    for c in candles:
        hour = datetime.fromtimestamp(c["t"] / 1000.0, tz=timezone.utc).hour
        buckets[hour].append(float(c["v"]))
    if len(buckets) < 24:
        raise ValueError(
            f"only {len(buckets)} distinct UTC hours in candle history — "
            "need all 24 for a liquidity profile"
        )
    return {h: median(v) for h, v in buckets.items()}


def _liquidity_pctile(profile: Dict[int, float], utc_hour: int) -> float:
    """Percentile of this hour's median volume among the 24 hourly medians."""
    own = profile[utc_hour]
    return 100.0 * sum(1 for v in profile.values() if v <= own) / len(profile)


@register_data_point(
    name="session_context",
    category="derived",
    source="hyperliquid",
    description=(
        "Trading-session + liquidity context: the live UTC session "
        "(asia|europe|eu_us_overlap|us|off_hours), weekend flag, and this "
        "hour's typical liquidity as a percentile of the symbol's own "
        "24-hour median-volume profile (100 = the most liquid hour of the "
        "day, ~4 = the quietest). activity_ratio compares the last "
        "completed hour's actual volume to its hour-of-day median — >1 "
        "means unusually busy for this time of day. Session theses "
        "(US-open squeeze, Asia drift) should cite this, not a timestamp."
    ),
    params_schema={
        "symbol":        {"type": "string", "default": "BTC"},
        "lookback_days": {"type": "integer", "default": 30},
    },
    returns_schema={
        "session": "string", "utc_hour": "int", "is_weekend": "bool",
        "liquidity_pctile": "float 0-100", "activity_ratio": "float",
        "hour_median_volume": "float", "last_hour_volume": "float",
    },
    tags=["session", "liquidity", "time-of-day", "derived"],
    numeric_path="liquidity_pctile",
)
def session_context(symbol: str = "BTC", lookback_days: int = 30) -> Dict[str, Any]:
    now = datetime.fromtimestamp(time.time(), tz=timezone.utc)
    result = _hl_candles(symbol, "1h", lookback_bars=lookback_days * 24)
    candles = result["candles"]
    if len(candles) < 2:
        raise ValueError(f"only {len(candles)} candles for {symbol} — no profile")
    profile = _hourly_profile(candles)
    # The final bar is still forming — the last COMPLETED hour is the honest
    # activity read.
    last_full = candles[-2]
    last_hour = datetime.fromtimestamp(last_full["t"] / 1000.0, tz=timezone.utc).hour
    hour_median = profile[last_hour]
    if hour_median <= 0:
        raise ValueError(f"zero median volume for hour {last_hour} — profile unusable")
    return {
        "symbol": symbol,
        "session": _session_label(now.hour),
        "utc_hour": now.hour,
        "is_weekend": now.weekday() >= 5,
        "liquidity_pctile": _liquidity_pctile(profile, now.hour),
        "activity_ratio": float(last_full["v"]) / hour_median,
        "hour_median_volume": hour_median,
        "last_hour_volume": float(last_full["v"]),
        "lookback_days": lookback_days,
    }
