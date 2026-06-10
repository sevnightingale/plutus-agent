"""
CCI (Commodity Channel Index) Preprocessor.

Advanced CCI preprocessing with overbought/oversold analysis, trend detection,
and momentum-based pattern recognition.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class CCIPreprocessor(BasePreprocessor):
    """Advanced CCI preprocessor with professional-grade analysis."""
    
    def preprocess(self, cci: pd.Series, prices: pd.Series = None,
                  length: int = 20, **kwargs) -> Dict[str, Any]:
        """
        Advanced CCI preprocessing with comprehensive analysis.

        CCI oscillates around zero, with values above +100 indicating overbought
        conditions and values below -100 indicating oversold conditions.

        Args:
            cci: CCI values
            prices: Price series for divergence analysis (optional)
            length: CCI calculation period

        Returns:
            Dictionary with comprehensive CCI analysis
        """
        # Clean and sanitize input data
        cci_clean = pd.to_numeric(cci, errors='coerce').dropna()
        if len(cci_clean) < 5:
            return {"error": "Insufficient data for CCI analysis"}

        # Align prices if provided
        if prices is not None:
            prices = pd.to_numeric(prices, errors='coerce')
            cci_clean, prices = cci_clean.align(prices, join='inner')
            prices = prices.dropna()
            if len(cci_clean) < 5 or len(prices) < 5:
                prices = None  # Disable divergence if insufficient aligned data

        current_cci = float(cci_clean.iloc[-1])

        # Generate proper timestamp
        if hasattr(cci_clean.index, 'tz') or np.issubdtype(cci_clean.index.dtype, np.datetime64):
            timestamp = cci_clean.index[-1].isoformat() if hasattr(cci_clean.index[-1], 'isoformat') else datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()
        
        # Zone analysis (+/-100 levels for CCI)
        zone_analysis = self._analyze_cci_zones(cci_clean, length)

        # Momentum analysis
        momentum_analysis = self._analyze_cci_momentum(cci_clean, length)

        # Zero line analysis
        zero_line_analysis = self._analyze_zero_line_behavior(cci_clean)

        # Pattern analysis
        pattern_analysis = self._analyze_cci_patterns(cci_clean, length)

        # Position rank analysis
        position_rank = self._calculate_position_rank(cci_clean, lookback=length)

        # Divergence analysis
        divergence = None
        if prices is not None:
            divergence = self._detect_cci_divergence(cci_clean, prices, length)
        
        return {
            "indicator": "CCI",
            "current": {
                "value": round(current_cci, 2),
                "timestamp": timestamp
            },
            "context": {
                "length": length,
                "momentum": momentum_analysis,
                "zero_line": zero_line_analysis
            },
            "levels": zone_analysis,
            "patterns": pattern_analysis,
            "position_rank": {
                "percentile": round(position_rank, 1),
                "interpretation": self._interpret_position_rank(position_rank)
            },
            "divergence": divergence,
            "evidence": {
                "data_quality": {
                    "total_periods": len(cci_clean),
                    "valid_data_percentage": round(len(cci_clean) / len(cci) * 100, 1),
                    "recent_volatility": round(cci_clean.iloc[-10:].std(), 3) if len(cci_clean) >= 10 else None
                },
                "calculation_notes": f"CCI analysis based on {len(cci_clean)} valid data points with period {length}"
            },
            "summary": self._generate_cci_summary(current_cci, zone_analysis, momentum_analysis,
                                               divergence, pattern_analysis)
        }
    
    def _analyze_cci_zones(self, cci: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze CCI overbought/oversold zones."""
        current_cci = cci.iloc[-1]
        
        # CCI zones: +100 (overbought), -100 (oversold)
        if current_cci >= 100:
            current_zone = "overbought"
        elif current_cci <= -100:
            current_zone = "oversold"
        else:
            current_zone = "neutral"
        
        # Streak analysis
        ob_streak = self._calculate_zone_streak(cci, 100, "above")
        os_streak = self._calculate_zone_streak(cci, -100, "below")
        
        # Time percentage analysis - using vectorized operations on clean data
        total_periods = len(cci)
        ob_periods = (cci >= 100).sum()
        os_periods = (cci <= -100).sum()
        
        # Exit analysis
        ob_exit = self._analyze_zone_exits(cci, 100, "above")
        os_exit = self._analyze_zone_exits(cci, -100, "below")
        
        # Extreme readings analysis (beyond +/-200)
        extreme_high = current_cci >= 200
        extreme_low = current_cci <= -200
        
        return {
            "current_zone": current_zone,
            "overbought": {
                "level": 100,
                "status": "in_zone" if current_cci >= 100 else "below",
                "streak_length": ob_streak,
                "time_percentage": round((ob_periods / total_periods) * 100, 1),
                "exit_analysis": ob_exit,
                "extreme_reading": extreme_high
            },
            "oversold": {
                "level": -100,
                "status": "in_zone" if current_cci <= -100 else "above",
                "streak_length": os_streak,
                "time_percentage": round((os_periods / total_periods) * 100, 1),
                "exit_analysis": os_exit,
                "extreme_reading": extreme_low
            },
            "neutral_bias": "bullish" if current_cci > 0 else ("neutral" if current_cci == 0 else "bearish")
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
    
    def _analyze_zone_exits(self, values: pd.Series, threshold: float, direction: str) -> Dict[str, Any]:
        """Analyze recent exits from zones."""
        exits = []
        
        for i in range(1, min(10, len(values))):
            prev_val = values.iloc[-(i+1)]
            curr_val = values.iloc[-i]
            
            if direction == "above":
                if prev_val >= threshold and curr_val < threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": curr_val,
                        "strength": abs(curr_val - threshold)
                    })
            else:  # below
                if prev_val <= threshold and curr_val > threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": curr_val,
                        "strength": abs(curr_val - threshold)
                    })
        
        return {
            "recent_exits": exits[:3],
            "latest_exit": exits[0] if exits else None
        }
    
    def _analyze_cci_momentum(self, cci: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze CCI momentum characteristics."""
        if len(cci) < 5:
            return {}
        
        # Use length-based windows
        velocity_window = max(3, length // 6)
        accel_window = max(6, length // 3)
        velocity = self._calculate_velocity(cci, velocity_window)
        acceleration = self._calculate_acceleration(cci, accel_window)
        
        # Volatility analysis
        volatility = cci.std()
        recent_range = cci.iloc[-10:].max() - cci.iloc[-10:].min()
        
        # Trend strength - remove trading bias language
        trend_direction = "rising" if velocity > 0 else "falling"
        trend_strength = min(1.0, abs(velocity) / 20)  # Normalize to 0-1
        
        return {
            "velocity": round(velocity, 2),
            "acceleration": round(acceleration, 2),
            "volatility": round(volatility, 2),
            "recent_range": round(recent_range, 2),
            "trend_direction": trend_direction,
            "trend_strength": round(trend_strength, 3),
            "momentum_interpretation": self._interpret_cci_momentum(velocity, acceleration)
        }
    
    def _interpret_cci_momentum(self, velocity: float, acceleration: float) -> str:
        """Interpret CCI momentum characteristics."""
        if velocity > 10 and acceleration > 0:
            return "strong_rising_acceleration"
        elif velocity > 10:
            return "strong_rising_momentum"
        elif velocity < -10 and acceleration < 0:
            return "strong_falling_acceleration"
        elif velocity < -10:
            return "strong_falling_momentum"
        elif abs(velocity) < 2:
            return "sideways_momentum"
        else:
            return f"{'rising' if velocity > 0 else 'falling'}_momentum"
    
    def _analyze_zero_line_behavior(self, cci: pd.Series) -> Dict[str, Any]:
        """Analyze CCI behavior around zero line."""
        current = cci.iloc[-1]
        
        # Time above/below zero
        above_zero = sum(1 for v in cci if v > 0)
        below_zero = sum(1 for v in cci if v < 0)
        total = len(cci)
        
        # Zero line crossings
        crossings = 0
        for i in range(1, len(cci)):
            if (cci.iloc[i] > 0 and cci.iloc[i-1] <= 0) or (cci.iloc[i] < 0 and cci.iloc[i-1] >= 0):
                crossings += 1
        
        return {
            "current_position": "above" if current > 0 else "below",
            "distance_from_zero": round(abs(current), 2),
            "time_above_zero_pct": round((above_zero / total) * 100, 1),
            "time_below_zero_pct": round((below_zero / total) * 100, 1),
            "zero_crossings": crossings
        }
    
    def _analyze_cci_patterns(self, cci: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze CCI patterns and formations."""
        patterns = {}
        
        if len(cci) >= 15:
            # Hook pattern (CCI hooks back from extreme levels)
            hook_pattern = self._detect_cci_hooks(cci, length)
            if hook_pattern:
                patterns["hook"] = hook_pattern

            # 100/-100 rejection pattern
            rejection_pattern = self._detect_level_rejection(cci, length)
            if rejection_pattern:
                patterns["level_rejection"] = rejection_pattern
        
        return patterns
    
    def _detect_cci_hooks(self, cci: pd.Series, length: int) -> Optional[Dict[str, Any]]:
        """Detect CCI hook patterns from extreme levels."""
        lookback = min(8, length // 2)
        if len(cci) < lookback:
            return None

        recent_values = cci.iloc[-lookback:]
        
        # Rising hook: CCI drops below -100, then hooks back up
        if any(v <= -100 for v in recent_values.iloc[:-2]):
            min_idx = np.argmin(recent_values.to_numpy())
            if min_idx < len(recent_values) - 2:  # Not the last value
                min_val = recent_values.iloc[min_idx]
                current_val = recent_values.iloc[-1]

                if min_val <= -100 and current_val > min_val + 20:  # Significant hook up
                    return {
                        "type": "rising_hook_pattern",
                        "hook_strength": round(current_val - min_val, 1),
                        "min_level": round(min_val, 1),
                        "current_level": round(current_val, 1),
                        "description": f"CCI hooked up from {min_val:.1f} oversold level"
                    }
        
        # Falling hook: CCI rises above +100, then hooks back down
        if any(v >= 100 for v in recent_values.iloc[:-2]):
            max_idx = np.argmax(recent_values.to_numpy())
            if max_idx < len(recent_values) - 2:  # Not the last value
                max_val = recent_values.iloc[max_idx]
                current_val = recent_values.iloc[-1]

                if max_val >= 100 and current_val < max_val - 20:  # Significant hook down
                    return {
                        "type": "falling_hook_pattern",
                        "hook_strength": round(max_val - current_val, 1),
                        "max_level": round(max_val, 1),
                        "current_level": round(current_val, 1),
                        "description": f"CCI hooked down from {max_val:.1f} overbought level"
                    }
        
        return None
    
    def _detect_level_rejection(self, cci: pd.Series, length: int) -> Optional[Dict[str, Any]]:
        """Detect rejection at +100/-100 levels."""
        if len(cci) < 5:
            return None
        
        recent_values = cci.iloc[-5:]
        
        # Check for rejection at +100 level
        if any(95 <= v <= 105 for v in recent_values) and recent_values.iloc[-1] < 90:
            return {
                "type": "overbought_level_rejection",
                "rejection_level": 100,
                "current_level": round(recent_values.iloc[-1], 1),
                "rejection_strength": round(100 - recent_values.iloc[-1], 1),
                "description": "CCI rejected at +100 overbought level"
            }

        # Check for rejection at -100 level
        if any(-105 <= v <= -95 for v in recent_values) and recent_values.iloc[-1] > -90:
            return {
                "type": "oversold_level_rejection",
                "rejection_level": -100,
                "current_level": round(recent_values.iloc[-1], 1),
                "rejection_strength": round(recent_values.iloc[-1] - (-100), 1),
                "description": "CCI rejected at -100 oversold level"
            }
        
        return None
    
    def _detect_cci_divergence(self, cci: pd.Series, prices: pd.Series, length: int) -> Optional[Dict[str, Any]]:
        """Detect CCI-price divergence patterns."""
        if len(cci) < 15 or len(prices) < 15:
            return None

        recent_periods = min(10, length // 2)
        cci_recent = cci.iloc[-recent_periods:]
        price_recent = prices.iloc[-recent_periods:]
        
        # Find peaks and troughs with length-based prominence
        prominence_factor = max(5, length // 4)
        cci_peaks = self._find_peaks(cci_recent, prominence=prominence_factor)
        cci_troughs = self._find_troughs(cci_recent, prominence=prominence_factor)
        price_peaks = self._find_peaks(price_recent, prominence=0.5)
        price_troughs = self._find_troughs(price_recent, prominence=0.5)
        
        # Negative divergence: price higher highs, CCI lower highs
        if len(cci_peaks) >= 2 and len(price_peaks) >= 2:
            latest_cci_peak = cci_peaks[-1]
            prev_cci_peak = cci_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]

            if (latest_price_peak["value"] > prev_price_peak["value"] and
                latest_cci_peak["value"] < prev_cci_peak["value"]):
                return {
                    "type": "negative_divergence",
                    "cci_trend": "lower_highs",
                    "price_trend": "higher_highs",
                    "strength": round(abs(latest_cci_peak["value"] - prev_cci_peak["value"]), 1),
                    "description": "Price making higher highs while CCI making lower highs"
                }

        # Positive divergence: price lower lows, CCI higher lows
        if len(cci_troughs) >= 2 and len(price_troughs) >= 2:
            latest_cci_trough = cci_troughs[-1]
            prev_cci_trough = cci_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]

            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_cci_trough["value"] > prev_cci_trough["value"]):
                return {
                    "type": "positive_divergence",
                    "cci_trend": "higher_lows",
                    "price_trend": "lower_lows",
                    "strength": round(abs(latest_cci_trough["value"] - prev_cci_trough["value"]), 1),
                    "description": "Price making lower lows while CCI making higher lows"
                }
        
        return None
    
    # Signal generation and confidence scoring methods removed to comply with analysis-only philosophy

    def _generate_cci_summary(self, cci_value: float, zone_analysis: Dict, momentum_analysis: Dict,
                             divergence: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable CCI summary with enriched signals."""
        summary = f"CCI={cci_value:.1f}"

        zone = zone_analysis["current_zone"]
        if zone != "neutral":
            streak = zone_analysis[zone]["streak_length"]
            if streak > 0:
                summary += f" ({zone}, {streak}p)"
            else:
                summary += f" ({zone})"

        # Extreme readings
        if zone_analysis["overbought"]["extreme_reading"]:
            summary += ". ⚠️ EXTREME HIGH (>200)"
        elif zone_analysis["oversold"]["extreme_reading"]:
            summary += ". ⚠️ EXTREME LOW (<-200)"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "positive" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE"
            elif "negative" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE"

        # Acceleration/deceleration
        if momentum_analysis:
            accel = momentum_analysis.get("acceleration", 0)
            if abs(accel) > 5:
                if accel > 0:
                    summary += ". Momentum accelerating"
                else:
                    summary += ". Momentum decelerating"

        # Pattern analysis (hooks, rejections)
        if pattern_analysis:
            if "hook" in pattern_analysis:
                hook = pattern_analysis["hook"]
                if "rising" in hook.get("type", ""):
                    summary += ". ✓ Rising hook from oversold"
                elif "falling" in hook.get("type", ""):
                    summary += ". ⚠️ Falling hook from overbought"

            if "level_rejection" in pattern_analysis:
                rejection = pattern_analysis["level_rejection"]
                if "overbought" in rejection.get("type", ""):
                    summary += ". ⚠️ Rejected at +100"
                elif "oversold" in rejection.get("type", ""):
                    summary += ". ✓ Rejected at -100"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full CCI output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "cci", "timeframe": timeframe, "timestamp": None,
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

        cci_val = to_native(current.get("value"))

        # Zone based on CCI level (100/-100 standard)
        if cci_val is not None:
            if cci_val >= 100: zone = "overbought"
            elif cci_val <= -100: zone = "oversold"
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
            if "bullish" in div.get("type", ""): pattern_codes.append("divergence_bullish")
            elif "bearish" in div.get("type", ""): pattern_codes.append("divergence_bearish")
        if "momentum" in patterns_data:
            mom = patterns_data["momentum"]
            if "rising" in mom.get("type", ""): pattern_codes.append("momentum_rising")
            elif "falling" in mom.get("type", ""): pattern_codes.append("momentum_falling")

        return {
            "indicator": "cci",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(float(cci_val), 2) if cci_val else None,
            "value_secondary": None,
            "value_tertiary": None,
            "velocity": round(to_native(trend_data.get("velocity", 0)) or 0, 4),
            "rank": round((float(cci_val) + 200) / 400, 3) if cci_val else 0.5,  # Normalize -200 to +200
            "zone": zone,
            "zone_periods": zone_periods,
            "trend": trend_data.get("direction", "unknown"),
            "crossover_type": None,
            "crossover_periods_ago": None,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }