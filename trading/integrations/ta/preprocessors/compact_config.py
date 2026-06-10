"""
Compact format configuration for Rei and optimized consumers.

This module defines which timeframes to use for each indicator when
creating compact output for Rei (limited to ~30KB payload).

Selection criteria:
- Timeframes chosen based on indicator type and signal value
- Momentum oscillators: need short-to-long coverage
- Trend indicators: longer TFs more meaningful
- Volatility indicators: medium TFs for actionable context
"""

from typing import Dict, List

# Indicator-to-timeframe mapping for Rei payloads
# Selected for maximum signal value while staying under payload limits
REI_INDICATOR_TIMEFRAMES: Dict[str, List[str]] = {
    # === Momentum Oscillators ===
    # Work across all TFs, need short-to-long coverage for confluence
    "rsi": ["15m", "1h", "4h", "1d"],        # Full spectrum - divergences matter on all
    "stochastic": ["15m", "1h", "4h"],       # Faster indicator, less useful on 1d
    "cci": ["1h", "4h", "1d"],               # More meaningful on medium+ TFs
    "mfi": ["1h", "4h"],                     # Volume-weighted, needs sufficient volume
    "williams_r": ["15m", "1h", "4h"],       # Similar to stochastic

    # === Trend Indicators ===
    # Need longer TFs to filter noise, confirm trend direction
    "macd": ["1h", "4h", "1d"],              # Skip 15m (too noisy), 1d for major trend
    "adx": ["4h", "1d"],                     # Trend STRENGTH, longer TFs more meaningful
    "aroon": ["4h", "1d"],                   # Trend emergence, needs history
    "ema": ["1h", "4h", "1d"],               # Trend following, multi-TF alignment
    "sma": ["4h", "1d"],                     # Slower, skip short TFs

    # === Volatility Indicators ===
    # Medium TFs for actionable volatility context
    "bbands": ["1h", "4h", "1d"],            # Squeezes can occur on any, %B useful everywhere
    "atr": ["1h", "4h"],                     # Stop placement, position sizing
    "bbwidth": ["4h", "1d"],                 # Squeeze detection on significant TFs
    "keltner": ["4h", "1d"],                 # Similar to BB, longer TF squeezes matter more
    "donchian": ["4h", "1d"],                # Breakout levels, significant on longer TFs

    # === Volume Indicators ===
    "obv": ["1h", "4h"],                     # Volume confirmation, noisy on very short TFs
    "vwap": ["15m", "1h"],                   # Intraday only by design

    # === Other ===
    "psar": ["1h", "4h"],                    # Trailing stops, entry timing
    "roc": ["1h", "4h"],                     # Momentum rate, medium TFs
    "vortex": ["4h", "1d"],                  # Trend direction changes
    "trix": ["4h", "1d"],                    # Triple smoothed, filters noise on long TFs
}


# Standardized pattern codes used across all indicators
PATTERN_CODES = {
    # Divergence
    "divergence_bullish",    # Price lower low, indicator higher low
    "divergence_bearish",    # Price higher high, indicator lower high

    # Momentum
    "momentum_strong_up",    # Strong upward momentum
    "momentum_strong_down",  # Strong downward momentum
    "momentum_rising",       # Moderate upward momentum
    "momentum_falling",      # Moderate downward momentum
    "momentum_weakening",    # Momentum fading
    "momentum_accelerating", # Momentum increasing

    # Crossovers
    "crossover_bullish",     # Bullish cross (K>D, MACD>Signal, etc.)
    "crossover_bearish",     # Bearish cross

    # Zone events
    "entering_overbought",   # Just entered OB zone
    "exiting_overbought",    # Just left OB zone
    "entering_oversold",     # Just entered OS zone
    "exiting_oversold",      # Just left OS zone

    # Volatility
    "squeeze_active",        # Low volatility compression
    "squeeze_firing",        # Breakout from squeeze
    "volatility_expanding",  # Bands widening
    "volatility_contracting",# Bands narrowing

    # Formations
    "double_top",            # Two peaks at similar level
    "double_bottom",         # Two troughs at similar level
    "failure_swing",         # Failed to make new high/low
}


def get_timeframes_for_indicator(indicator: str) -> List[str]:
    """
    Get the list of timeframes to include for an indicator in Rei payloads.

    Args:
        indicator: Indicator name (lowercase)

    Returns:
        List of timeframe strings (e.g., ["15m", "1h", "4h"])
    """
    indicator = indicator.lower()

    # Handle aliases
    aliases = {
        "bollinger_bands": "bbands",
        "bb": "bbands",
        "bollinger": "bbands",
        "bb_width": "bbwidth",
        "parabolic_sar": "psar",
        "sar": "psar",
        "stoch": "stochastic",
    }
    indicator = aliases.get(indicator, indicator)

    return REI_INDICATOR_TIMEFRAMES.get(indicator, ["1h", "4h"])


def get_all_configured_indicators() -> List[str]:
    """Get list of all indicators with timeframe configuration."""
    return list(REI_INDICATOR_TIMEFRAMES.keys())


def estimate_payload_size(indicators: List[str]) -> int:
    """
    Estimate total payload size for given indicators.

    Assumes ~400 bytes per indicator-timeframe combination.

    Args:
        indicators: List of indicator names

    Returns:
        Estimated payload size in bytes
    """
    total_combos = 0
    for indicator in indicators:
        tfs = get_timeframes_for_indicator(indicator)
        total_combos += len(tfs)

    # ~400 bytes per compact indicator output
    return total_combos * 400
