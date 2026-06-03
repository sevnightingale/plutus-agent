"""
Williams %R Preprocessor.

Advanced Williams %R preprocessing with zone analysis, momentum tracking,
and comprehensive market state description for overbought/oversold conditions.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class WilliamsRPreprocessor(BasePreprocessor):
    """Advanced Williams %R preprocessor with professional-grade analysis."""

    def preprocess(self, williams_r: pd.Series, prices: pd.Series = None,
                  length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced Williams %R preprocessing with sophisticated momentum analysis.

        Provides rich market state description following analysis-only pattern.
        No signals or confidence - pure market context for Decision LLM.

        Args:
            williams_r: Williams %R values (oscillates between 0 and -100)
            prices: Price series for divergence analysis (optional)
            length: Williams %R calculation period

        Returns:
            Dictionary with comprehensive Williams %R analysis
        """
        # Clean core Williams %R data
        wr_clean = pd.to_numeric(williams_r, errors="coerce").dropna()
        if len(wr_clean) < 5:
            return {"error": "Insufficient data for Williams %R analysis"}

        # Get timestamp from series index
        ts = (wr_clean.index[-1].isoformat()
              if is_datetime64_any_dtype(wr_clean.index)
              else datetime.now(timezone.utc).isoformat())

        # Keep full WR for core analysis
        wr_core = wr_clean

        # Only align for divergence analysis
        prices_div = None
        wr_div = None
        if prices is not None:
            px_clean = pd.to_numeric(prices, errors="coerce").dropna()
            wr_div, prices_div = wr_clean.align(px_clean, join="inner")

        # Length-driven windows
        vel_win = max(2, length // 5)
        acc_win = max(3, length // 3)
        rank_win = max(10, length)
        exit_scan = min(10, len(wr_core))
        div_win = max(10, length)

        # Clamp to expected range [-100, 0]
        current_wr = np.clip(float(wr_core.iloc[-1]), -100, 0)

        # Zone analysis
        zone_analysis = self._analyze_wr_zones(wr_core, exit_scan)

        # Momentum analysis (period-driven)
        momentum_analysis = self._analyze_wr_momentum(wr_core, vel_win, acc_win)

        # Pattern analysis
        pattern_analysis = self._analyze_wr_patterns(wr_core, length)

        # Position rank analysis (period-driven)
        position_rank = self._calculate_position_rank(wr_core, lookback=rank_win)

        # Trend analysis
        trend_analysis = self._analyze_trend(wr_core)

        # Divergence analysis (aligned data if available)
        divergence = None
        if prices_div is not None:
            divergence = self._detect_wr_divergence(wr_div, prices_div, div_win)

        # Include divergence in patterns if detected
        if divergence is not None:
            pattern_analysis["divergence"] = divergence

        # Level analysis
        level_analysis = self._analyze_key_levels(wr_core, [-20, -50, -80])

        # Recent extremes
        extremes = self._find_recent_extremes(wr_core, max(20, length))

        # Generate summary
        summary = self._generate_wr_summary(current_wr, zone_analysis, momentum_analysis, pattern_analysis)

        return {
            "indicator": "Williams_R",
            "length": length,
            "current": {
                "value": round(current_wr, 2),
                "timestamp": ts
            },
            "context": {
                "trend": {
                    "direction": trend_analysis["direction"],
                    "strength": round(trend_analysis["strength"], 3),
                    "velocity": round(momentum_analysis["velocity"], 3),
                    "acceleration": round(momentum_analysis["acceleration"], 3)
                },
                "momentum": {
                    "interpretation": momentum_analysis["momentum_interpretation"],
                    "recent_range": round(momentum_analysis["recent_range"], 2),
                    "volatility": round(momentum_analysis["volatility"], 2)
                },
                "volatility": round(wr_core.std(), 3)
            },
            "levels": {
                "overbought": zone_analysis["overbought"],
                "oversold": zone_analysis["oversold"],
                "neutral": {
                    "level": -50,
                    "bias": zone_analysis["neutral_bias"],
                    "distance_from_50": round(current_wr - (-50), 2)
                },
                "key_levels": [-20, -50, -80],
                "recent_crossovers": level_analysis.get("recent_crossovers", [])
            },
            "extremes": {
                "recent_high": {
                    "value": round(extremes["high_value"], 2),
                    "periods_ago": extremes["high_periods_ago"],
                    "significance": extremes["high_significance"]
                },
                "recent_low": {
                    "value": round(extremes["low_value"], 2),
                    "periods_ago": extremes["low_periods_ago"],
                    "significance": extremes["low_significance"]
                }
            },
            "patterns": pattern_analysis,
            "evidence": {
                "data_quality": {
                    "aligned_periods": len(wr_core),
                    "had_prices": prices is not None,
                    "period_used": length,
                    "windows_used": {
                        "velocity": vel_win,
                        "acceleration": acc_win,
                        "position_rank": rank_win,
                        "divergence": div_win
                    }
                },
                "calculation_notes": f"Williams %R analysis based on {len(wr_core)} periods with length={length}"
            },
            "summary": summary
        }

    def _analyze_wr_zones(self, williams_r: pd.Series, exit_scan: int = 10) -> Dict[str, Any]:
        """Analyze Williams %R overbought/oversold zones."""
        # Compute on finite values only
        finite = williams_r.dropna()
        current_wr = finite.iloc[-1]

        # Williams %R zones: -20 (overbought), -80 (oversold)
        if current_wr >= -20:
            current_zone = "overbought"
        elif current_wr <= -80:
            current_zone = "oversold"
        else:
            current_zone = "neutral"

        # Streak analysis
        ob_streak = self._calculate_zone_streak(finite, -20, "above")
        os_streak = self._calculate_zone_streak(finite, -80, "below")

        # Time percentage analysis (vectorized on finite values)
        total = len(finite) or 1
        ob_pct = float((finite >= -20).mean() * 100)
        os_pct = float((finite <= -80).mean() * 100)

        # Neutral bias (tri-state at -50)
        neutral_bias = ("bullish" if finite.iloc[-1] > -50
                       else "bearish" if finite.iloc[-1] < -50
                       else "neutral")

        # Exit analysis (with exit_scan limit)
        ob_exit = self._analyze_zone_exits(finite, -20, "above", exit_scan)
        os_exit = self._analyze_zone_exits(finite, -80, "below", exit_scan)

        return {
            "current_zone": current_zone,
            "overbought": {
                "level": -20,
                "status": "in_zone" if current_wr >= -20 else "below",
                "streak_length": ob_streak,
                "time_percentage": round(ob_pct, 1),
                "exit_analysis": ob_exit
            },
            "oversold": {
                "level": -80,
                "status": "in_zone" if current_wr <= -80 else "above",
                "streak_length": os_streak,
                "time_percentage": round(os_pct, 1),
                "exit_analysis": os_exit
            },
            "neutral_bias": neutral_bias
        }

    def _calculate_zone_streak(self, values: pd.Series, threshold: float, direction: str) -> int:
        """Calculate consecutive periods in a zone."""
        streak = 0
        for i in range(len(values) - 1, -1, -1):
            if direction == "above" and values.iloc[i] >= threshold:
                streak += 1
            elif direction == "below" and values.iloc[i] <= threshold:
                streak += 1
            else:
                break
        return streak

    def _analyze_zone_exits(self, values: pd.Series, threshold: float, direction: str, exit_scan: int = 10) -> Dict[str, Any]:
        """Analyze recent exits from zones."""
        exits = []

        for i in range(1, min(exit_scan, len(values))):
            prev_val = values.iloc[-(i+1)]
            curr_val = values.iloc[-i]

            if direction == "above":
                if prev_val >= threshold and curr_val < threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": round(curr_val, 2),
                        "strength": min(1.0, abs(curr_val - threshold) / 20.0)
                    })
            else:  # below
                if prev_val <= threshold and curr_val > threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": round(curr_val, 2),
                        "strength": min(1.0, abs(curr_val - threshold) / 20.0)
                    })

        return {
            "recent_exits": exits[:3],
            "latest_exit": exits[0] if exits else None
        }

    def _analyze_wr_momentum(self, williams_r: pd.Series, vel_win: int = 3, acc_win: int = 6) -> Dict[str, Any]:
        """Analyze Williams %R momentum characteristics."""
        if len(williams_r) < 5:
            return {}

        # Period-driven velocity and acceleration
        velocity = self._calculate_velocity(williams_r, vel_win)
        acceleration = self._calculate_acceleration(williams_r, acc_win)

        # Mean reversion potential
        recent_range = williams_r.iloc[-10:].max() - williams_r.iloc[-10:].min()
        volatility = williams_r.std()

        return {
            "velocity": round(velocity, 3),
            "acceleration": round(acceleration, 3),
            "recent_range": round(recent_range, 2),
            "volatility": round(volatility, 2),
            "momentum_interpretation": self._interpret_wr_momentum(velocity, acceleration)
        }

    def _interpret_wr_momentum(self, velocity: float, acceleration: float) -> str:
        """Interpret Williams %R momentum characteristics."""
        if velocity > 5 and acceleration > 0:
            return "strong_upward_acceleration"
        elif velocity > 5:
            return "strong_upward_momentum"
        elif velocity < -5 and acceleration < 0:
            return "strong_downward_acceleration"
        elif velocity < -5:
            return "strong_downward_momentum"
        elif abs(velocity) < 1:
            return "sideways_momentum"
        else:
            return f"{'upward' if velocity > 0 else 'downward'}_momentum"

    def _analyze_wr_patterns(self, williams_r: pd.Series, length: int = 14) -> Dict[str, Any]:
        """Analyze Williams %R patterns and formations."""
        patterns = {}

        if len(williams_r) >= 10:
            # Failure swing pattern (similar to RSI)
            failure_swing = self._detect_failure_swing(williams_r.tail(max(10, length)))
            if failure_swing:
                patterns["failure_swing"] = failure_swing

        # Strong momentum patterns
        if len(williams_r) >= length:
            vel_win = max(2, length // 5)
            velocity = self._calculate_velocity(williams_r, vel_win)

            if abs(velocity) > williams_r.std() * 0.5:
                patterns["momentum"] = {
                    "type": f"strong_{'rising' if velocity > 0 else 'falling'}_momentum",
                    "velocity": round(velocity, 3),
                    "description": f"Strong {'rising' if velocity > 0 else 'falling'} momentum in Williams %R"
                }

        return patterns

    def _detect_failure_swing(self, values: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect Williams %R failure swing patterns."""
        if len(values) < 8:
            return None

        # Look for bullish failure swing (double bottom above -80)
        recent_lows = []
        for i in range(1, len(values) - 1):
            if values.iloc[i] < values.iloc[i-1] and values.iloc[i] < values.iloc[i+1]:
                recent_lows.append({"index": i, "value": values.iloc[i]})

        if len(recent_lows) >= 2:
            last_low = recent_lows[-1]
            prev_low = recent_lows[-2]

            # Bullish failure swing: second low higher than first, both above -80
            if (last_low["value"] > prev_low["value"] and
                last_low["value"] > -80 and prev_low["value"] > -80):
                return {
                    "type": "bullish_failure_swing",
                    "description": "Double bottom above oversold level",
                    "low_comparison": {
                        "previous_low": round(prev_low["value"], 2),
                        "latest_low": round(last_low["value"], 2),
                        "improvement": round(last_low["value"] - prev_low["value"], 2)
                    }
                }

        return None

    def _detect_wr_divergence(self, williams_r: pd.Series, prices: pd.Series, div_win: int = 15) -> Optional[Dict[str, Any]]:
        """Detect Williams %R-price divergence patterns."""
        # Align Williams %R and price data
        df = pd.concat({"wr": williams_r, "px": prices}, axis=1, join="inner").dropna()
        if len(df) < max(15, div_win):
            return None

        # Use recent window for analysis
        seg = df.tail(min(div_win, len(df)))

        # Scaled prominence for both series
        prom_wr = max(1e-6, seg["wr"].std() * 0.6)
        prom_px = max(1e-6, seg["px"].std() * 0.6)

        wr_peaks = self._find_peaks(seg["wr"], prominence=prom_wr)
        wr_troughs = self._find_troughs(seg["wr"], prominence=prom_wr)
        price_peaks = self._find_peaks(seg["px"], prominence=prom_px)
        price_troughs = self._find_troughs(seg["px"], prominence=prom_px)

        # Bearish divergence: price higher highs, Williams %R lower highs
        if len(wr_peaks) >= 2 and len(price_peaks) >= 2:
            latest_wr_peak = wr_peaks[-1]
            prev_wr_peak = wr_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]

            if (latest_price_peak["value"] > prev_price_peak["value"] and
                latest_wr_peak["value"] < prev_wr_peak["value"]):
                return {
                    "type": "bearish_divergence",
                    "description": "Price making higher highs while Williams %R making lower highs",
                    "peak_comparison": {
                        "price_change": round(latest_price_peak["value"] - prev_price_peak["value"], 4),
                        "wr_change": round(latest_wr_peak["value"] - prev_wr_peak["value"], 2),
                        "price_peak_periods_ago": latest_price_peak["periods_ago"],
                        "wr_peak_periods_ago": latest_wr_peak["periods_ago"]
                    }
                }

        # Bullish divergence: price lower lows, Williams %R higher lows
        if len(wr_troughs) >= 2 and len(price_troughs) >= 2:
            latest_wr_trough = wr_troughs[-1]
            prev_wr_trough = wr_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]

            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_wr_trough["value"] > prev_wr_trough["value"]):
                return {
                    "type": "bullish_divergence",
                    "description": "Price making lower lows while Williams %R making higher lows",
                    "trough_comparison": {
                        "price_change": round(latest_price_trough["value"] - prev_price_trough["value"], 4),
                        "wr_change": round(latest_wr_trough["value"] - prev_wr_trough["value"], 2),
                        "price_trough_periods_ago": latest_price_trough["periods_ago"],
                        "wr_trough_periods_ago": latest_wr_trough["periods_ago"]
                    }
                }

        return None

    def _generate_wr_summary(self, wr_value: float, zone_analysis: Dict, momentum_analysis: Dict,
                            pattern_analysis: Dict = None) -> str:
        """Generate human-readable Williams %R summary with enriched signals."""
        summary = f"Williams %R={wr_value:.1f}"

        zone = zone_analysis["current_zone"]
        if zone != "neutral":
            streak = zone_analysis[zone]["streak_length"]
            if streak > 0:
                summary += f" ({zone}, {streak}p)"
            else:
                summary += f" ({zone})"

        # ⚠️ DIVERGENCE - critical signal
        if pattern_analysis and "divergence" in pattern_analysis:
            div = pattern_analysis["divergence"]
            if "bullish" in div.get("type", ""):
                summary += ". ✓ BULLISH DIVERGENCE"
            elif "bearish" in div.get("type", ""):
                summary += ". ⚠️ BEARISH DIVERGENCE"

        # Acceleration/deceleration
        if momentum_analysis:
            accel = momentum_analysis.get("acceleration", 0)
            if abs(accel) > 2:
                if accel > 0:
                    summary += ". Momentum accelerating"
                else:
                    summary += ". Momentum decelerating"

        # Failure swing pattern
        if pattern_analysis and "failure_swing" in pattern_analysis:
            fs = pattern_analysis["failure_swing"]
            if "bullish" in fs.get("type", ""):
                summary += ". ✓ Bullish failure swing"
            elif "bearish" in fs.get("type", ""):
                summary += ". ⚠️ Bearish failure swing"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full Williams %R output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "williams_r", "timeframe": timeframe, "timestamp": None,
                "value": None, "value_secondary": None, "value_tertiary": None,
                "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                "patterns": [], "analysis": full_output.get("error", "")
            }

        def to_native(val):
            if val is None: return None
            if isinstance(val, (np.integer,)): return int(val)
            if isinstance(val, (np.floating,)): return float(val)
            return val

        current = full_output.get("current", {})
        context = full_output.get("context", {})
        trend_data = context.get("trend", {})
        levels = full_output.get("levels", {})
        patterns_data = full_output.get("patterns", {})

        wr_val = to_native(current.get("value"))

        # Zone based on Williams %R level (-20/-80 standard, inverted)
        if wr_val is not None:
            if wr_val >= -20: zone = "overbought"
            elif wr_val <= -80: zone = "oversold"
            else: zone = "neutral"
        else:
            zone = "unknown"

        zone_periods = 0
        if zone == "overbought":
            zone_periods = to_native(levels.get("overbought", {}).get("streak_length", 0)) or 0
        elif zone == "oversold":
            zone_periods = to_native(levels.get("oversold", {}).get("streak_length", 0)) or 0

        # Pattern extraction
        pattern_codes = []
        if "divergence" in patterns_data:
            div = patterns_data["divergence"]
            if "positive" in div.get("type", ""): pattern_codes.append("divergence_bullish")
            elif "negative" in div.get("type", ""): pattern_codes.append("divergence_bearish")
        if "failure_swing" in patterns_data:
            fs = patterns_data["failure_swing"]
            if "bullish" in fs.get("type", ""): pattern_codes.append("failure_swing")
            elif "bearish" in fs.get("type", ""): pattern_codes.append("failure_swing")

        return {
            "indicator": "williams_r",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(float(wr_val), 2) if wr_val else None,
            "value_secondary": None,
            "value_tertiary": None,
            "velocity": round(to_native(trend_data.get("velocity", 0)) or 0, 4),
            "rank": round((float(wr_val) + 100) / 100, 3) if wr_val else 0.5,  # -100 to 0 → 0 to 1
            "zone": zone,
            "zone_periods": zone_periods,
            "trend": trend_data.get("direction", "unknown"),
            "crossover_type": None,
            "crossover_periods_ago": None,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }