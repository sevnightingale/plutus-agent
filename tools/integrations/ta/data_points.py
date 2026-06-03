"""TA data points — 21 technical indicators powered by ggbot preprocessors.

Each data point internally fetches hl_candles, builds a DataFrame, runs
pandas-ta, and passes the result through the corresponding ggbot
preprocessor.  Output is rich structured JSON with divergence detection,
pattern recognition, zone analysis, and human-readable summaries.

All indicators share the same call signature:
    fetch_data_point("ta_<name>", symbol="SOL", interval="1h", ...)
"""

from __future__ import annotations

import numpy as np
from typing import Any, Dict

from tools.core.data_point_registry import register_data_point

from . import _calc


# ── helpers ────────────────────────────────────────────────────────────────


def _to_native(obj: Any) -> Any:
    """Recursively convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_to_native(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(x) for x in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _ta_fetch(
    symbol: str,
    interval: str,
    lookback_bars: int,
    calc_fn,
    **calc_kwargs: Any,
) -> Dict[str, Any]:
    """Fetch candles, build DataFrame, run calculator, return preprocessor output."""
    result = _calc._hl_candles(symbol=symbol, interval=interval,
                               lookback_bars=lookback_bars)
    df = _calc.candles_to_df(result)
    if len(df) < 5:
        return {"error": "insufficient_data",
                "message": f"Need at least 5 candles, got {len(df)}"}
    try:
        output = calc_fn(df, **calc_kwargs)
    except Exception as e:
        return {"error": "preprocessor_failed",
                "indicator": calc_fn.__name__,
                "message": f"Preprocessor raised {type(e).__name__}: {e}",
                "working": False}
    return _to_native(output)


# ── momentum (6) ───────────────────────────────────────────────────────────


@register_data_point(
    name="ta_rsi",
    category="ta",
    source="hyperliquid",
    description=(
        "RSI with divergence detection, zone analysis (>70 overbought, <30 "
        "oversold), trend direction/strength/velocity/acceleration, pattern "
        "recognition (double top/bottom, momentum), level crossovers, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True, "example": "SOL"},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14, "description": "RSI period"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["momentum", "oscillator", "overbought-oversold", "divergence"],
)
def ta_rsi(symbol: str, interval: str = "1h", length: int = 14,
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_rsi, length=length)


@register_data_point(
    name="ta_stochastic",
    category="ta",
    source="hyperliquid",
    description=(
        "Stochastic Oscillator (%K/%D) with zone analysis (>80 overbought, "
        "<20 oversold), crossover detection, trend/momentum, divergence "
        "detection, and summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "k":             {"type": "integer", "default": 14, "description": "%K period"},
        "d":             {"type": "integer", "default": 3, "description": "%D smoothing"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["momentum", "oscillator", "overbought-oversold"],
)
def ta_stochastic(symbol: str, interval: str = "1h", k: int = 14,
                  d: int = 3, lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_stochastic, k=k, d=d)


@register_data_point(
    name="ta_williams_r",
    category="ta",
    source="hyperliquid",
    description=(
        "Williams %R — momentum oscillator (-100 to 0). >-20 overbought, "
        "<-80 oversold. Zone analysis, trend, momentum state, and summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["momentum", "oscillator", "overbought-oversold"],
)
def ta_williams_r(symbol: str, interval: str = "1h", length: int = 14,
                  lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_williams_r, length=length)


@register_data_point(
    name="ta_cci",
    category="ta",
    source="hyperliquid",
    description=(
        "Commodity Channel Index — measures price deviation from average. "
        ">100 overbought, <-100 oversold. Zone analysis, trend, momentum, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["momentum", "oscillator"],
)
def ta_cci(symbol: str, interval: str = "1h", length: int = 20,
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_cci, length=length)


@register_data_point(
    name="ta_mfi",
    category="ta",
    source="hyperliquid",
    description=(
        "Money Flow Index — volume-weighted RSI (0-100). >80 overbought, "
        "<20 oversold. Divergence detection, trend, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["momentum", "volume", "overbought-oversold"],
)
def ta_mfi(symbol: str, interval: str = "1h", length: int = 14,
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_mfi, length=length)


@register_data_point(
    name="ta_roc",
    category="ta",
    source="hyperliquid",
    description=(
        "Rate of Change — pure momentum (% change over N periods). "
        "Overbought/oversold zones, trend, momentum, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 10},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["momentum"],
)
def ta_roc(symbol: str, interval: str = "1h", length: int = 10,
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_roc, length=length)


# ── trend (5) ──────────────────────────────────────────────────────────────


@register_data_point(
    name="ta_macd",
    category="ta",
    source="hyperliquid",
    description=(
        "MACD (12/26/9) with line/signal/histogram, crossover detection, "
        "divergence analysis, momentum, trend classification, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "fast":          {"type": "integer", "default": 12},
        "slow":          {"type": "integer", "default": 26},
        "signal":        {"type": "integer", "default": 9, "description": "Signal smoothing"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["trend", "momentum", "divergence"],
)
def ta_macd(symbol: str, interval: str = "1h", fast: int = 12,
            slow: int = 26, signal: int = 9,
            lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_macd, fast=fast, slow=slow, signal=signal)


@register_data_point(
    name="ta_adx",
    category="ta",
    source="hyperliquid",
    description=(
        "Average Directional Index — trend strength (0-100). <20 weak/absent "
        "trend, >25 developing, >40 strong. Direction, strength, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["trend", "strength"],
)
def ta_adx(symbol: str, interval: str = "1h", length: int = 14,
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_adx, length=length)


@register_data_point(
    name="ta_aroon",
    category="ta",
    source="hyperliquid",
    description=(
        "Aroon indicator — identifies trend presence and strength. Aroon Up "
        "vs Aroon Down. >70 strong trend, <30 weakening, crossover signals."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["trend"],
)
def ta_aroon(symbol: str, interval: str = "1h", length: int = 14,
             lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_aroon, length=length)


@register_data_point(
    name="ta_trix",
    category="ta",
    source="hyperliquid",
    description=(
        "TRIX — triple-smoothed rate of change. Filters out short-term noise. "
        "Zero-line crossovers, divergence, trend, momentum, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["trend", "momentum"],
)
def ta_trix(symbol: str, interval: str = "1h", length: int = 14,
            lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_trix, length=length)


@register_data_point(
    name="ta_vortex",
    category="ta",
    source="hyperliquid",
    description=(
        "Vortex Indicator — directional movement with +VI and -VI lines. "
        "Crossover signals, trend strength, momentum, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["trend", "directional"],
)
def ta_vortex(symbol: str, interval: str = "1h", length: int = 14,
              lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_vortex, length=length)


# ── moving averages (3) ────────────────────────────────────────────────────


@register_data_point(
    name="ta_sma",
    category="ta",
    source="hyperliquid",
    description=(
        "Simple Moving Average with multi-period analysis (20/50/200). "
        "Price vs SMA positioning, trend, slope, crossover detection, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "lookback_bars": {"type": "integer", "default": 500},
    },
    tags=["moving-average", "trend"],
)
def ta_sma(symbol: str, interval: str = "1h", length: int = 20,
           lookback_bars: int = 500) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_sma, length=length)


@register_data_point(
    name="ta_ema",
    category="ta",
    source="hyperliquid",
    description=(
        "Exponential Moving Average with multi-period analysis (20/50/200). "
        "Price vs EMA positioning, trend, slope, crossover detection, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "lookback_bars": {"type": "integer", "default": 500},
    },
    tags=["moving-average", "trend"],
)
def ta_ema(symbol: str, interval: str = "1h", length: int = 20,
           lookback_bars: int = 500) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_ema, length=length)


@register_data_point(
    name="ta_vwap",
    category="ta",
    source="hyperliquid",
    description=(
        "Volume-Weighted Average Price — institutional benchmark. "
        "Price vs VWAP positioning, deviation analysis, trend, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["moving-average", "volume"],
)
def ta_vwap(symbol: str, interval: str = "1h",
            lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars, _calc.calc_vwap)


# ── volatility (5) ─────────────────────────────────────────────────────────


@register_data_point(
    name="ta_bbands",
    category="ta",
    source="hyperliquid",
    description=(
        "Bollinger Bands (20/2) — volatility envelope around SMA. Upper/lower "
        "bands, %b, bandwidth, squeeze detection, price position, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "std":           {"type": "number", "default": 2.0, "description": "Standard deviations"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["volatility", "envelope"],
)
def ta_bbands(symbol: str, interval: str = "1h", length: int = 20,
              std: float = 2.0, lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_bbands, length=length, std=std)


@register_data_point(
    name="ta_bbwidth",
    category="ta",
    source="hyperliquid",
    description=(
        "Bollinger Band Width — volatility regime indicator. Narrow bands "
        "precede expansion (squeeze). Zone analysis (low/normal/high vol)."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "std":           {"type": "number", "default": 2.0},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["volatility", "squeeze"],
)
def ta_bbwidth(symbol: str, interval: str = "1h", length: int = 20,
               std: float = 2.0, lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_bbwidth, length=length, std=std)


@register_data_point(
    name="ta_keltner",
    category="ta",
    source="hyperliquid",
    description=(
        "Keltner Channels — ATR-based volatility envelope. Upper/middle/lower "
        "bands, price position, squeeze vs Bollinger, trend bias, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "multiplier":    {"type": "number", "default": 2.0},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["volatility", "envelope"],
)
def ta_keltner(symbol: str, interval: str = "1h", length: int = 20,
               multiplier: float = 2.0,
               lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_keltner, length=length, multiplier=multiplier)


@register_data_point(
    name="ta_donchian",
    category="ta",
    source="hyperliquid",
    description=(
        "Donchian Channels — N-period high/low breakout envelope. "
        "Upper/middle/lower, price position, breakout detection, trend, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 20},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["volatility", "breakout"],
)
def ta_donchian(symbol: str, interval: str = "1h", length: int = 20,
                lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_donchian, length=length)


@register_data_point(
    name="ta_atr",
    category="ta",
    source="hyperliquid",
    description=(
        "Average True Range — volatility normalizer (not directional). "
        "Current ATR, percentile rank, trend, volatility regime, summary. "
        "Essential for position sizing (SL distance in ATR multiples)."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "length":        {"type": "integer", "default": 14},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["volatility", "risk-management"],
)
def ta_atr(symbol: str, interval: str = "1h", length: int = 14,
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_atr, length=length)


# ── volume / other (2) ─────────────────────────────────────────────────────


@register_data_point(
    name="ta_obv",
    category="ta",
    source="hyperliquid",
    description=(
        "On-Balance Volume — cumulative volume flow indicator. "
        "Divergence detection, trend, momentum, volume confirmation, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["volume", "divergence"],
)
def ta_obv(symbol: str, interval: str = "1h",
           lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars, _calc.calc_obv)


@register_data_point(
    name="ta_psar",
    category="ta",
    source="hyperliquid",
    description=(
        "Parabolic SAR — stop-and-reverse indicator. Trailing dots above "
        "(downtrend) or below (uptrend) price. Current SAR level, reversal "
        "signals, acceleration factor, trend direction, summary."
    ),
    params_schema={
        "symbol":        {"type": "string", "required": True},
        "interval":      {"type": "string", "default": "1h"},
        "af_start":      {"type": "number", "default": 0.02},
        "af_increment":  {"type": "number", "default": 0.02},
        "af_max":        {"type": "number", "default": 0.2},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["trend", "stop-and-reverse"],
)
def ta_psar(symbol: str, interval: str = "1h", af_start: float = 0.02,
            af_increment: float = 0.02, af_max: float = 0.2,
            lookback_bars: int = 200) -> Dict[str, Any]:
    return _ta_fetch(symbol, interval, lookback_bars,
                     _calc.calc_psar, af_start=af_start,
                     af_increment=af_increment, af_max=af_max)
