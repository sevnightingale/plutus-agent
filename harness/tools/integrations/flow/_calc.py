"""Shared calculator for flow indicators.

Computes order-flow metrics from HL candle DataFrames without external APIs.
CVD (Cumulative Volume Delta) uses the close-vs-midpoint rule to estimate
buying vs selling volume per bar, then accumulates.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def _to_native(obj: Any) -> Any:
    """Recursively convert numpy types to native Python for JSON serialization."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_to_native(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(x) for x in obj]
    return obj


def calc_cvd(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute Cumulative Volume Delta from OHLCV candles.

    For each candle, estimates buy vs sell volume using the close-vs-midpoint
    rule: if close > midpoint((high+low)/2), the bar's volume counts as
    buying pressure; otherwise selling. CVD is the running sum.

    Returns CVD series, divergence detection (price up but CVD down = bearish
    divergence), trend classification, and buy/sell pressure summary.
    """
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    volume = df["volume"].values.astype(float)

    n = len(df)
    buy_vol = np.zeros(n)
    sell_vol = np.zeros(n)

    for i in range(n):
        midpoint = (high[i] + low[i]) / 2.0
        if close[i] > midpoint:
            buy_vol[i] = volume[i]
        elif close[i] < midpoint:
            sell_vol[i] = volume[i]
        else:
            # Neutral — split evenly
            buy_vol[i] = volume[i] / 2.0
            sell_vol[i] = volume[i] / 2.0

    delta = buy_vol - sell_vol
    cvd = np.cumsum(delta)

    # Net pressure over the window
    total_buy = float(np.sum(buy_vol))
    total_sell = float(np.sum(sell_vol))
    total_vol = total_buy + total_sell
    buy_pct = (total_buy / total_vol * 100) if total_vol > 0 else 50.0

    # Recent momentum (last 20% of bars vs first 80%)
    split = int(n * 0.8)
    recent_delta = float(np.sum(delta[split:])) if split < n else 0.0
    prior_delta = float(np.sum(delta[:split])) if split > 0 else 0.0

    # CVD trend classification
    cvd_current = float(cvd[-1])
    cvd_max = float(np.max(cvd))
    cvd_min = float(np.min(cvd))
    cvd_range = cvd_max - cvd_min

    if cvd_range > 0:
        cvd_pct = (cvd_current - cvd_min) / cvd_range * 100
    else:
        cvd_pct = 50.0

    if cvd_pct > 80:
        trend = "strong_buying"
    elif cvd_pct > 60:
        trend = "buying"
    elif cvd_pct > 40:
        trend = "neutral"
    elif cvd_pct > 20:
        trend = "selling"
    else:
        trend = "strong_selling"

    # Divergence detection: price up but CVD momentum down (or vice versa)
    price_change = float(close[-1] - close[0])
    cvd_change = cvd_current - cvd[0]
    divergence = None
    if price_change > 0 and recent_delta < prior_delta * 0.3:
        divergence = "bearish — price rising but buying pressure fading (distribution)"
    elif price_change < 0 and recent_delta > prior_delta * 1.5:
        divergence = "bullish — price falling but buying pressure accelerating (accumulation)"

    # Pressure ratio
    pressure_ratio = buy_pct / (100 - buy_pct) if buy_pct != 100 else 999.0

    return _to_native({
        "cvd_current": cvd_current,
        "cvd_max": cvd_max,
        "cvd_min": cvd_min,
        "cvd_range": cvd_range,
        "cvd_percentile": round(cvd_pct, 1),
        "buy_volume_total": total_buy,
        "sell_volume_total": total_sell,
        "total_volume": total_vol,
        "buy_pressure_pct": round(buy_pct, 1),
        "buy_sell_ratio": round(pressure_ratio, 2),
        "recent_delta": recent_delta,
        "prior_delta": prior_delta,
        "cvd_trend": trend,
        "divergence": divergence,
        "price_change_pct": round(float((close[-1] / close[0] - 1) * 100), 2),
        "cvd_change": cvd_change,
    })
