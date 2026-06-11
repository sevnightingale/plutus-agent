"""Order-flow data points: CVD (Cumulative Volume Delta) from HL candles.

These indicators estimate buying vs selling pressure from OHLCV data —
the closest thing to reading the tape without L3 order-book data.
"""

from __future__ import annotations

from typing import Any, Dict

from trading.perception.core.data_point_registry import register_data_point
from trading.integrations.hyperliquid.data_points import hl_candles as _hl_candles
from trading.integrations.ta._calc import candles_to_df
from ._calc import calc_cvd


def _get_df(symbol: str, interval: str, lookback_bars: int):
    """Fetch HL candles and return a DataFrame ready for flow calc."""
    result = _hl_candles(symbol=symbol, interval=interval, lookback_bars=lookback_bars)
    return candles_to_df(result)


@register_data_point(
    name="hl_cvd",
    category="market",
    source="hyperliquid",
    description=(
        "Cumulative Volume Delta — estimates buying vs selling pressure from "
        "candles using the close-vs-midpoint rule. Bullish CVD (rising) = "
        "accumulation, bears being absorbed. Bearish CVD = distribution. "
        "The divergence flag compares price direction vs net flow over the "
        "RECENT bars (last ~20% of the window); cvd_current and the "
        "percentile are relative to the lookback window, so keep params "
        "consistent when comparing reads. Includes CVD trend, buy/sell "
        "pressure %, and per-bar delta rates."
    ),
    params_schema={
        "symbol": {"type": "string", "required": True},
        "interval": {"type": "string", "default": "1h"},
        "lookback_bars": {"type": "integer", "default": 200},
    },
    tags=["order-flow", "volume", "divergence", "smart-money"],
    numeric_path="cvd_current",
)
def hl_cvd(
    symbol: str,
    interval: str = "1h",
    lookback_bars: int = 200,
) -> Dict[str, Any]:
    df = _get_df(symbol, interval, lookback_bars)
    result = calc_cvd(df)
    return result
