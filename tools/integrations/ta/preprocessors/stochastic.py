"""
Stochastic Oscillator Preprocessor.

Advanced Stochastic preprocessing with %K/%D analysis, crossover detection,
overbought/oversold zone tracking, and divergence pattern recognition.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class StochasticPreprocessor(BasePreprocessor):
    """Advanced Stochastic preprocessor with professional-grade analysis."""
    
    def preprocess(self, k_percent: pd.Series, d_percent: pd.Series,
                  prices: pd.Series = None, period: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced Stochastic Oscillator preprocessing with sophisticated analysis.

        Provides rich market state description following analysis-only pattern.
        No signals or confidence - pure market context for Decision LLM.

        Args:
            k_percent: %K values (fast stochastic)
            d_percent: %D values (slow stochastic, signal line)
            prices: Price series for divergence analysis (optional)
            period: Stochastic period for window calculations

        Returns:
            Dictionary with comprehensive Stochastic analysis
        """
        # Clean and align data
        k = pd.to_numeric(k_percent, errors="coerce").dropna()
        d = pd.to_numeric(d_percent, errors="coerce").dropna()
        kd = pd.concat({"k": k, "d": d}, axis=1, join="inner").dropna()

        if len(kd) < 5:
            return {"error": "Insufficient data for Stochastic analysis"}

        k, d = kd["k"], kd["d"]

        # Price alignment for divergence (optional)
        px = None if prices is None else pd.to_numeric(prices, errors="coerce").dropna()
        kdpx = None if px is None else pd.concat({"k": k, "px": px}, axis=1, join="inner").dropna()

        # Get timestamp from series index
        ts = (k.index[-1].isoformat()
              if is_datetime64_any_dtype(k.index)
              else datetime.now(timezone.utc).isoformat())

        # Clamp values to [0,100] range
        current_k = np.clip(float(k.iloc[-1]), 0, 100)
        current_d = np.clip(float(d.iloc[-1]), 0, 100)
        spread = current_k - current_d
        
        # Cross analysis
        cross_analysis = self._analyze_stoch_crossovers(k, d, period)

        # Zone analysis (80/20 levels for Stochastic)
        zone_analysis = self._analyze_stoch_zones(k, d, period)

        # Position rank analysis
        position_rank = self._calculate_position_rank(k, lookback=max(10, period))

        # Momentum analysis
        momentum_analysis = self._analyze_stoch_momentum(k, d, period)

        # Divergence analysis (if prices available)
        divergence = None
        if kdpx is not None:
            divergence = self._detect_stoch_divergence(kdpx["k"], kdpx["px"], period)

        # Level analysis
        level_analysis = self._analyze_key_levels(k, [20, 50, 80])

        # Recent extremes
        extremes = self._find_recent_extremes(k, max(20, period))

        # Pattern detection
        patterns = self._detect_stoch_patterns(k, d, kdpx, period)

        # Include divergence in patterns if detected
        if divergence is not None:
            patterns["divergence"] = divergence

        # Generate summary
        summary = self._generate_stoch_summary(
            current_k, current_d, cross_analysis, zone_analysis, position_rank,
            patterns, momentum_analysis
        )
        
        return {
            "indicator": "Stochastic",
            "period": period,
            "current": {
                "k_percent": round(current_k, 2),
                "d_percent": round(current_d, 2),
                "spread": round(spread, 2),
                "timestamp": ts
            },
            "context": {
                "trend": {
                    "k_direction": "rising" if momentum_analysis.get("k_velocity", 0) > 0 else ("falling" if momentum_analysis.get("k_velocity", 0) < 0 else "sideways"),
                    "momentum": momentum_analysis.get("momentum_interpretation", "neutral"),
                    "velocity": round(momentum_analysis.get("k_velocity", 0), 3),
                    "acceleration": round(momentum_analysis.get("k_acceleration", 0), 3)
                },
                "spread_momentum": round(momentum_analysis.get("spread_momentum", 0), 3),
                "volatility": round(k.std(), 3)
            },
            "levels": {
                "overbought": zone_analysis["overbought"],
                "oversold": zone_analysis["oversold"],
                "neutral": {
                    "level": 50,
                    "bias": zone_analysis["neutral_bias"],
                    "distance_from_50": round(current_k - 50, 2)
                },
                "key_levels": [20, 50, 80],
                "recent_crossovers": cross_analysis.get("recent_crossovers", [])
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
            "patterns": patterns,
            "evidence": {
                "data_quality": {
                    "aligned_periods": len(k),
                    "period_used": period,
                    "had_prices": kdpx is not None,
                    "valid_data_percentage": round(len(k) / len(k_percent) * 100, 1) if len(k_percent) > 0 else 0
                },
                "calculation_notes": f"Stochastic analysis based on {len(k)} aligned K/D periods"
            },
            "summary": summary
        }
    
    def _analyze_stoch_crossovers(self, k_percent: pd.Series, d_percent: pd.Series, period: int = 14) -> Dict[str, Any]:
        """Analyze Stochastic crossovers (%K crossing %D)."""
        crossovers = []
        
        for i in range(1, min(max(10, period), len(k_percent), len(d_percent))):
            prev_k = k_percent.iloc[-(i+1)]
            curr_k = k_percent.iloc[-i]
            prev_d = d_percent.iloc[-(i+1)]
            curr_d = d_percent.iloc[-i]
            
            # Bullish crossover (%K crosses above %D)
            if prev_k <= prev_d and curr_k > curr_d:
                crossovers.append({
                    "type": "bullish_crossover",
                    "periods_ago": i,
                    "strength": round(abs(curr_k - curr_d), 2),
                    "location": self._get_stoch_zone(curr_k)
                })
            # Bearish crossover (%K crosses below %D)
            elif prev_k >= prev_d and curr_k < curr_d:
                crossovers.append({
                    "type": "bearish_crossover",
                    "periods_ago": i,
                    "strength": round(abs(curr_k - curr_d), 2),
                    "location": self._get_stoch_zone(curr_k)
                })
        
        return {
            "recent_crossovers": crossovers[:5],
            "latest_crossover": crossovers[0] if crossovers else None,
            "bars_since_cross": crossovers[0]["periods_ago"] if crossovers else None
        }
    
    def _analyze_stoch_zones(self, k_percent: pd.Series, d_percent: pd.Series, period: int = 14) -> Dict[str, Any]:
        """Analyze Stochastic overbought/oversold zones (80/20 levels)."""
        current_k = k_percent.iloc[-1]
        
        # Current zone determination
        if current_k >= 80:
            current_zone = "overbought"
        elif current_k <= 20:
            current_zone = "oversold"
        else:
            current_zone = "neutral"
        
        # Streak analysis - consecutive periods in zone
        ob_streak = self._calculate_zone_streak(k_percent, 80, "above")
        os_streak = self._calculate_zone_streak(k_percent, 20, "below")
        
        # Time percentage analysis (vectorized)
        total_periods = len(k_percent)
        ob_periods = (k_percent >= 80).sum()
        os_periods = (k_percent <= 20).sum()
        
        # Exit analysis
        ob_exit = self._analyze_zone_exits(k_percent, 80, "above")
        os_exit = self._analyze_zone_exits(k_percent, 20, "below")
        
        return {
            "current_zone": current_zone,
            "overbought": {
                "level": 80,
                "status": "in_zone" if current_k >= 80 else "below",
                "streak_length": ob_streak,
                "time_percentage": round((ob_periods / total_periods) * 100, 1),
                "exit_analysis": ob_exit
            },
            "oversold": {
                "level": 20,
                "status": "in_zone" if current_k <= 20 else "above", 
                "streak_length": os_streak,
                "time_percentage": round((os_periods / total_periods) * 100, 1),
                "exit_analysis": os_exit
            },
            "neutral_bias": "bullish" if current_k > 50 else ("bearish" if current_k < 50 else "neutral")
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
        """Analyze recent exits from overbought/oversold zones."""
        exits = []
        
        for i in range(1, min(10, len(values))):
            prev_val = values.iloc[-(i+1)]
            curr_val = values.iloc[-i]
            
            if direction == "above":
                if prev_val >= threshold and curr_val < threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": curr_val,
                        "strength": min(1.0, (threshold - curr_val) / 20)
                    })
            else:  # below
                if prev_val <= threshold and curr_val > threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": curr_val,
                        "strength": min(1.0, (curr_val - threshold) / 20)
                    })
        
        return {
            "recent_exits": exits[:3],
            "latest_exit": exits[0] if exits else None
        }
    
    def _analyze_stoch_momentum(self, k_percent: pd.Series, d_percent: pd.Series, period: int = 14) -> Dict[str, Any]:
        """Analyze Stochastic momentum characteristics."""
        if len(k_percent) < 5:
            return {}
        
        # %K velocity and acceleration (period-driven windows)
        vel_win = max(2, period // 5)
        acc_win = max(3, period // 3)
        k_velocity = self._calculate_velocity(k_percent, vel_win)
        k_acceleration = self._calculate_acceleration(k_percent, acc_win)
        
        # %D smoothing effect
        spread_current = k_percent.iloc[-1] - d_percent.iloc[-1]
        spread_previous = k_percent.iloc[-2] - d_percent.iloc[-2] if len(k_percent) > 1 else 0
        spread_momentum = spread_current - spread_previous
        
        return {
            "k_velocity": round(k_velocity, 2),
            "k_acceleration": round(k_acceleration, 2),
            "spread_momentum": round(spread_momentum, 2),
            "momentum_interpretation": self._interpret_stoch_momentum(k_velocity, k_acceleration)
        }
    
    def _interpret_stoch_momentum(self, velocity: float, acceleration: float) -> str:
        """Interpret Stochastic momentum characteristics."""
        if velocity > 5 and acceleration > 0:
            return "strong_bullish_acceleration"
        elif velocity > 5:
            return "strong_bullish_momentum"
        elif velocity < -5 and acceleration < 0:
            return "strong_bearish_acceleration"
        elif velocity < -5:
            return "strong_bearish_momentum"
        elif abs(velocity) < 1:
            return "sideways_momentum"
        else:
            return f"{'bullish' if velocity > 0 else 'bearish'}_momentum"
    
    def _detect_stoch_divergence(self, k_percent: pd.Series, prices: pd.Series, period: int = 14) -> Optional[Dict[str, Any]]:
        """Detect Stochastic-price divergence patterns."""
        if len(k_percent) < 15 or len(prices) < 15:
            return None
        
        # Period-driven analysis window
        win = min(max(10, period), len(k_percent), len(prices))
        k_recent = k_percent.tail(win)
        price_recent = prices.tail(win)

        # Scaled prominence for peak/trough detection
        prom_k = max(1e-6, k_recent.std() * 0.6)
        prom_p = max(1e-6, price_recent.std() * 0.6)

        # Find recent peaks and troughs
        k_peaks = self._find_peaks(k_recent, prominence=prom_k)
        k_troughs = self._find_troughs(k_recent, prominence=prom_k)
        price_peaks = self._find_peaks(price_recent, prominence=prom_p)
        price_troughs = self._find_troughs(price_recent, prominence=prom_p)
        
        # Check for divergence patterns
        if len(k_peaks) >= 2 and len(price_peaks) >= 2:
            # Bearish divergence: price making higher highs, Stochastic making lower highs
            latest_k_peak = k_peaks[-1]
            prev_k_peak = k_peaks[-2]
            latest_price_peak = price_peaks[-1] 
            prev_price_peak = price_peaks[-2]
            
            if (latest_price_peak["value"] > prev_price_peak["value"] and 
                latest_k_peak["value"] < prev_k_peak["value"]):
                return {
                    "type": "bearish_divergence",
                    "description": "Price making higher highs while Stochastic making lower highs",
                    "peak_comparison": {
                        "price_change": round(latest_price_peak["value"] - prev_price_peak["value"], 4),
                        "stoch_change": round(latest_k_peak["value"] - prev_k_peak["value"], 2)
                    }
                }
        
        if len(k_troughs) >= 2 and len(price_troughs) >= 2:
            # Bullish divergence: price making lower lows, Stochastic making higher lows
            latest_k_trough = k_troughs[-1]
            prev_k_trough = k_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]
            
            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_k_trough["value"] > prev_k_trough["value"]):
                return {
                    "type": "bullish_divergence",
                    "description": "Price making lower lows while Stochastic making higher lows",
                    "trough_comparison": {
                        "price_change": round(latest_price_trough["value"] - prev_price_trough["value"], 4),
                        "stoch_change": round(latest_k_trough["value"] - prev_k_trough["value"], 2)
                    }
                }
        
        return None
    
    def _get_stoch_zone(self, value: float) -> str:
        """Get Stochastic zone description."""
        if value >= 80:
            return "overbought"
        elif value <= 20:
            return "oversold"
        else:
            return "neutral"
    
    def _detect_stoch_patterns(self, k: pd.Series, d: pd.Series,
                              kdpx: Optional[pd.DataFrame], period: int = 14) -> Dict[str, Any]:
        """Detect Stochastic patterns and formations."""
        patterns = {}

        # Crossover momentum patterns (period-driven velocity window)
        if len(k) >= 10:
            vel_win = max(2, period // 5)
            velocity = self._calculate_velocity(k, vel_win)
            if abs(velocity) > 2.0:
                patterns["momentum"] = {
                    "type": f"strong_{'rising' if velocity > 0 else 'falling'}_momentum",
                    "velocity": round(velocity, 3),
                    "window": vel_win,
                    "description": f"Strong {'rising' if velocity > 0 else 'falling'} momentum in %K"
                }

        # Squeeze patterns (low volatility)
        if len(k) >= period:
            k_volatility = k.tail(period).std()
            overall_volatility = k.std()
            if k_volatility < overall_volatility * 0.5:
                patterns["squeeze"] = {
                    "type": "low_volatility_squeeze",
                    "current_volatility": round(k_volatility, 3),
                    "baseline_volatility": round(overall_volatility, 3),
                    "description": "Stochastic showing reduced volatility - potential breakout setup"
                }

        return patterns
    
    def _generate_stoch_summary(self, k_value: float, d_value: float, cross_analysis: Dict,
                               zone_analysis: Dict, position_rank: float,
                               patterns: Dict = None, momentum_analysis: Dict = None) -> str:
        """Generate human-readable Stochastic summary with enriched signals."""
        summary = f"Stochastic %K={k_value:.1f}, %D={d_value:.1f}"

        # Add zone information
        zone = zone_analysis["current_zone"]
        if zone != "neutral":
            streak = zone_analysis[zone]["streak_length"]
            if streak > 0:
                summary += f" ({zone}, {streak}p)"
            else:
                summary += f" ({zone})"

        # Recent crossover
        latest_cross = cross_analysis.get("latest_crossover")
        if latest_cross and latest_cross["periods_ago"] <= 5:
            cross_type = "bullish" if "bullish" in latest_cross.get("type", "") else "bearish"
            location = latest_cross.get("location", "neutral")
            if location in ["overbought", "oversold"]:
                summary += f". {cross_type.capitalize()} crossover in {location} {latest_cross['periods_ago']}p ago"
            else:
                summary += f". {cross_type.capitalize()} crossover {latest_cross['periods_ago']}p ago"

        # ⚠️ DIVERGENCE - critical signal
        if patterns and "divergence" in patterns:
            div = patterns["divergence"]
            if "bullish" in div.get("type", ""):
                summary += ". ✓ BULLISH DIVERGENCE"
            elif "bearish" in div.get("type", ""):
                summary += ". ⚠️ BEARISH DIVERGENCE"

        # Acceleration/deceleration
        if momentum_analysis:
            accel = momentum_analysis.get("k_acceleration", 0)
            if abs(accel) > 2:
                if accel > 0:
                    summary += ". Momentum accelerating"
                else:
                    summary += ". Momentum decelerating"

        # Squeeze pattern (low volatility)
        if patterns and "squeeze" in patterns:
            summary += ". ⚠️ Volatility squeeze"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full Stochastic output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "stochastic", "timeframe": timeframe, "timestamp": None,
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

        k_val = to_native(current.get("k_percent"))
        d_val = to_native(current.get("d_percent"))

        # Zone determination
        if k_val is not None:
            if k_val >= 80: zone = "overbought"
            elif k_val <= 20: zone = "oversold"
            else: zone = "neutral"
        else:
            zone = "unknown"

        # Zone periods
        zone_periods = 0
        if zone == "overbought":
            zone_periods = to_native(levels.get("overbought", {}).get("streak_length", 0)) or 0
        elif zone == "oversold":
            zone_periods = to_native(levels.get("oversold", {}).get("streak_length", 0)) or 0

        # Crossover extraction
        recent_crossovers = levels.get("recent_crossovers", [])
        crossover_type = None
        crossover_periods_ago = None
        if recent_crossovers:
            latest = recent_crossovers[0]
            cross_type = latest.get("type", "")
            crossover_type = "crossover_bullish" if "bullish" in cross_type else "crossover_bearish"
            crossover_periods_ago = to_native(latest.get("periods_ago"))

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
            "indicator": "stochastic",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(float(k_val), 2) if k_val else None,
            "value_secondary": round(float(d_val), 2) if d_val else None,
            "value_tertiary": None,
            "velocity": round(to_native(trend_data.get("velocity", 0)) or 0, 4),
            "rank": round(float(k_val) / 100.0, 3) if k_val else 0.0,
            "zone": zone,
            "zone_periods": zone_periods,
            "trend": trend_data.get("k_direction", "unknown"),
            "crossover_type": crossover_type,
            "crossover_periods_ago": crossover_periods_ago,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }

    def _calculate_stoch_confidence(self, k: pd.Series, d: pd.Series,
                                   cross_analysis: Dict, zone_analysis: Dict, period: int = 14) -> float:
        """Calculate Stochastic analysis confidence with spread volatility scaling."""
        confidence_factors = []

        # Data quantity factor
        data_factor = min(1.0, len(k) / max(20, period))
        confidence_factors.append(data_factor)

        # Signal clarity factor (scaled to spread volatility)
        latest_cross = cross_analysis.get("latest_crossover")
        if latest_cross:
            spread_std = float((k - d).tail(max(20, period)).std() or 1e-6)
            cross_strength = min(1.0, abs(latest_cross["strength"]) / (2 * spread_std))
            confidence_factors.append(cross_strength)

        # Zone consistency factor
        current_k = k.iloc[-1]
        if current_k >= 80 or current_k <= 20:
            confidence_factors.append(0.8)  # High confidence in extreme zones
        else:
            confidence_factors.append(0.6)  # Medium confidence in neutral zone

        return round(np.mean(confidence_factors), 3)