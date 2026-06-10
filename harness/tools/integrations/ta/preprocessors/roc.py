"""
ROC (Rate of Change) Preprocessor.

Advanced ROC preprocessing with momentum analysis, overbought/oversold detection,
and velocity-based trend strength assessment.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class ROCPreprocessor(BasePreprocessor):
    """Advanced ROC preprocessor with professional-grade momentum analysis."""
    
    def preprocess(self, roc: pd.Series, prices: pd.Series = None,
                  length: int = 10, **kwargs) -> Dict[str, Any]:
        """
        Advanced ROC preprocessing with comprehensive momentum analysis.

        ROC measures the percentage change in price over a specified period.
        Positive values indicate upward momentum, negative values indicate downward momentum.

        Args:
            roc: ROC values (percentage change)
            prices: Price series for additional analysis (optional)
            length: ROC calculation period

        Returns:
            Dictionary with comprehensive ROC analysis
        """
        # Clean and align data
        roc = pd.to_numeric(roc, errors="coerce").dropna()
        prices = None if prices is None else pd.to_numeric(prices, errors="coerce").dropna()

        if len(roc) < 5:
            return {"error": "Insufficient data for ROC analysis"}

        # Get timestamp from series index or use UTC
        ts = (roc.index[-1].isoformat()
              if is_datetime64_any_dtype(roc.index)
              else datetime.now(timezone.utc).isoformat())

        current_roc = float(roc.iloc[-1])

        # Length-driven windows
        vel_win = max(3, length // 3)
        acc_win = max(5, length // 2)
        rank_win = max(10, length)
        
        # Momentum analysis
        momentum_analysis = self._analyze_roc_momentum(roc, length)

        # Overbought/oversold analysis
        overbought_oversold = self._analyze_roc_extremes(roc, length)

        # Trend analysis
        trend_analysis = self._analyze_roc_trend(roc, vel_win)

        # Velocity analysis (rate of change of ROC)
        velocity_analysis = self._analyze_roc_velocity(roc, vel_win, acc_win)

        # Zero line analysis
        zero_line_analysis = self._analyze_roc_zero_line(roc, rank_win)

        # Divergence analysis
        divergence = None
        if prices is not None:
            divergence = self._detect_roc_price_divergence(roc, prices, length)

        # Pattern analysis
        pattern_analysis = self._analyze_roc_patterns(roc, length)
        
        return {
            "indicator": "ROC",
            "current": {
                "value": round(current_roc, 3),
                "value_pct": f"{current_roc:+.2f}%",
                "timestamp": ts
            },
            "context": {
                "momentum": momentum_analysis,
                "trend": trend_analysis,
                "velocity": velocity_analysis,
                "length": length,
                "calculation_periods": len(roc)
            },
            "levels": {
                "zero_line": zero_line_analysis,
                "extremes": overbought_oversold
            },
            "patterns": {
                **pattern_analysis,
                "divergence": divergence
            },
            "evidence": {
                "data_quality": {
                    "original_periods": len(roc),
                    "clean_periods": len(roc),
                    "valid_data_percentage": 100.0,
                    "had_prices": prices is not None,
                    "calculation_periods": length
                },
                "calculation_notes": f"ROC analysis based on {len(roc)} periods with length={length}"
            },
            "summary": self._generate_roc_summary(current_roc, momentum_analysis, overbought_oversold,
                                               divergence, pattern_analysis)
        }
    
    def _analyze_roc_momentum(self, roc: pd.Series, length: int = 10) -> Dict[str, Any]:
        """Analyze ROC momentum characteristics."""
        current_roc = roc.iloc[-1]
        
        # Momentum classification
        if current_roc > 0:
            momentum_direction = "positive"
        elif current_roc < 0:
            momentum_direction = "negative"
        else:
            momentum_direction = "neutral"
        
        # Momentum strength (absolute value)
        momentum_strength = abs(current_roc)
        
        # Momentum strength classification
        if momentum_strength > 5:
            strength_level = "very_strong"
        elif momentum_strength > 2:
            strength_level = "strong"
        elif momentum_strength > 1:
            strength_level = "moderate"
        elif momentum_strength > 0.5:
            strength_level = "weak"
        else:
            strength_level = "very_weak"
        
        # Recent momentum evolution
        lookback = max(5, length // 2)
        if len(roc) >= lookback:
            recent_avg = roc.iloc[-lookback:].mean()
            prior_avg = roc.iloc[-2*lookback:-lookback].mean() if len(roc) >= 2*lookback else roc.iloc[:-lookback].mean()
            momentum_change = recent_avg - prior_avg
            
            if momentum_change > 0.5:
                momentum_evolution = "accelerating"
            elif momentum_change < -0.5:
                momentum_evolution = "decelerating"
            else:
                momentum_evolution = "stable"
        else:
            momentum_evolution = "insufficient_data"
        
        # Momentum persistence
        persistence = self._calculate_momentum_persistence(roc, lookback)
        
        return {
            "direction": momentum_direction,
            "strength": momentum_strength,
            "strength_level": strength_level,
            "evolution": momentum_evolution,
            "persistence": round(persistence, 3)
        }
    
    def _calculate_momentum_persistence(self, roc: pd.Series, lookback: int = 5) -> float:
        """Calculate how persistent the momentum direction is."""
        if len(roc) < lookback:
            return 0.5

        # Look at recent periods
        recent = roc.dropna().iloc[-lookback:]
        if not len(recent):
            return 0.5

        current_val = recent.iloc[-1]
        if current_val > 0:
            cur = "pos"
        elif current_val < 0:
            cur = "neg"
        else:
            return 0.5  # Treat zero as neutral

        # Count periods with same direction
        same = sum((v > 0 and cur == "pos") or (v < 0 and cur == "neg") for v in recent)
        return same / len(recent)
    
    def _analyze_roc_extremes(self, roc: pd.Series, length: int = 10) -> Dict[str, Any]:
        """Analyze ROC overbought/oversold conditions."""
        current_roc = roc.iloc[-1]
        
        # Statistical analysis
        mean_roc = roc.mean()
        std_roc = roc.std()

        # Guard against zero std (flat series)
        if std_roc == 0.0:
            return {
                "condition": "neutral",
                "overbought_threshold": 0.0,
                "oversold_threshold": 0.0,
                "extreme_overbought_threshold": 0.0,
                "extreme_oversold_threshold": 0.0,
                "overbought_time_pct": 0.0,
                "oversold_time_pct": 0.0,
                "current_streak": 0
            }

        # Dynamic thresholds based on historical data (scale with length)
        sigma_mult = 1.5 * (length / 10)  # Adjust sigma multiplier based on length
        overbought_threshold = mean_roc + sigma_mult * std_roc
        oversold_threshold = mean_roc - sigma_mult * std_roc

        # Extreme thresholds
        extreme_overbought = mean_roc + 2.5 * sigma_mult * std_roc
        extreme_oversold = mean_roc - 2.5 * sigma_mult * std_roc
        
        # Current condition
        if current_roc >= extreme_overbought:
            condition = "extreme_overbought"
        elif current_roc >= overbought_threshold:
            condition = "overbought"
        elif current_roc <= extreme_oversold:
            condition = "extreme_oversold"
        elif current_roc <= oversold_threshold:
            condition = "oversold"
        else:
            condition = "neutral"
        
        # Time in extreme conditions
        overbought_periods = sum(1 for val in roc if val >= overbought_threshold)
        oversold_periods = sum(1 for val in roc if val <= oversold_threshold)
        total_periods = len(roc)
        
        # Current streak
        current_streak = self._calculate_extreme_streak(
            roc, condition,
            overbought_threshold, oversold_threshold,
            extreme_overbought, extreme_oversold
        )
        
        return {
            "condition": condition,
            "overbought_threshold": round(overbought_threshold, 2),
            "oversold_threshold": round(oversold_threshold, 2),
            "extreme_overbought_threshold": round(extreme_overbought, 2),
            "extreme_oversold_threshold": round(extreme_oversold, 2),
            "overbought_time_pct": round((overbought_periods / total_periods) * 100, 1),
            "oversold_time_pct": round((oversold_periods / total_periods) * 100, 1),
            "current_streak": current_streak
        }
    
    def _calculate_extreme_streak(
        self, roc: pd.Series, condition: str,
        overbought_threshold: float, oversold_threshold: float,
        extreme_overbought_threshold: float, extreme_oversold_threshold: float
    ) -> int:
        """Calculate consecutive periods in current extreme condition."""
        if condition == "neutral":
            return 0

        streak = 0
        for i in range(len(roc) - 1, -1, -1):
            v = roc.iloc[i]
            if condition == "extreme_overbought" and v >= extreme_overbought_threshold:
                streak += 1
            elif condition == "overbought" and v >= overbought_threshold:
                streak += 1
            elif condition == "extreme_oversold" and v <= extreme_oversold_threshold:
                streak += 1
            elif condition == "oversold" and v <= oversold_threshold:
                streak += 1
            else:
                break
        return streak
    
    def _analyze_roc_trend(self, roc: pd.Series, vel_win: int = 3) -> Dict[str, Any]:
        """Analyze ROC trend characteristics."""
        # ROC trend (trend of the momentum)
        roc_slope = self._calculate_velocity(roc, vel_win)
        
        if roc_slope > 0.2:
            roc_trend = "rising"
        elif roc_slope < -0.2:
            roc_trend = "falling"
        else:
            roc_trend = "sideways"
        
        # Trend strength
        trend_strength = min(1.0, abs(roc_slope) / 2)
        
        # Trend consistency
        trend_consistency = self._calculate_roc_trend_consistency(roc)
        
        return {
            "direction": roc_trend,
            "slope": round(roc_slope, 3),
            "strength": round(trend_strength, 3),
            "consistency": round(trend_consistency, 3)
        }
    
    def _calculate_roc_trend_consistency(self, roc: pd.Series) -> float:
        """Calculate consistency of ROC trend direction."""
        if len(roc) < 5:
            return 0.5
        
        # Look at ROC changes
        roc_changes = roc.diff().dropna()
        
        if len(roc_changes) == 0:
            return 0.5
        
        positive_changes = sum(1 for x in roc_changes if x > 0)
        negative_changes = sum(1 for x in roc_changes if x < 0)
        total_changes = len(roc_changes)
        
        max_directional = max(positive_changes, negative_changes)
        return max_directional / total_changes if total_changes > 0 else 0.5
    
    def _analyze_roc_velocity(self, roc: pd.Series, vel_win: int = 3, acc_win: int = 5) -> Dict[str, Any]:
        """Analyze velocity of ROC (acceleration/deceleration)."""
        if len(roc) < 5:
            return {}
        
        # First derivative (velocity of momentum)
        velocity = self._calculate_velocity(roc, vel_win)

        # Second derivative (acceleration of momentum)
        acceleration = self._calculate_acceleration(roc, acc_win)
        
        # Velocity interpretation
        if velocity > 0.5:
            velocity_interpretation = "accelerating_momentum"
        elif velocity < -0.5:
            velocity_interpretation = "decelerating_momentum"
        else:
            velocity_interpretation = "stable_momentum"
        
        return {
            "velocity": round(velocity, 3),
            "acceleration": round(acceleration, 3),
            "interpretation": velocity_interpretation
        }
    
    def _analyze_roc_zero_line(self, roc: pd.Series, rank_win: int = 10) -> Dict[str, Any]:
        """Analyze ROC behavior around zero line."""
        current_roc = roc.iloc[-1]
        
        # Position relative to zero with tolerance
        eps = 1e-6
        if current_roc > eps:
            position = "above_zero"
        elif current_roc < -eps:
            position = "below_zero"
        else:
            position = "at_zero"

        # Time above/below zero using finite values
        finite = roc.dropna()
        above_zero = (finite > 0).sum()
        below_zero = (finite < 0).sum()
        total = len(finite)
        above_zero_pct = (above_zero / max(1, total)) * 100
        below_zero_pct = (below_zero / max(1, total)) * 100
        
        # Zero line crossings with epsilon tolerance
        crossings = 0
        for i in range(1, len(roc)):
            if (roc.iloc[i] > eps and roc.iloc[i-1] <= -eps) or (roc.iloc[i] < -eps and roc.iloc[i-1] >= eps):
                crossings += 1
        
        # Recent zero line crosses with epsilon tolerance
        recent_crosses = []
        for i in range(1, min(rank_win, len(roc))):
            if (roc.iloc[-i] > eps and roc.iloc[-(i+1)] <= -eps):
                recent_crosses.append({
                    "type": "bullish_zero_cross",
                    "periods_ago": i,
                    "value": round(roc.iloc[-i], 3)
                })
            elif (roc.iloc[-i] < -eps and roc.iloc[-(i+1)] >= eps):
                recent_crosses.append({
                    "type": "bearish_zero_cross",
                    "periods_ago": i,
                    "value": round(roc.iloc[-i], 3)
                })
        
        return {
            "position": position,
            "above_zero_pct": round(above_zero_pct, 1),
            "below_zero_pct": round(below_zero_pct, 1),
            "total_crossings": crossings,
            "recent_crosses": recent_crosses[:3],
            "crossing_frequency": round(crossings / max(1, len(finite)), 3)
        }
    
    def _detect_roc_price_divergence(self, roc: pd.Series, prices: pd.Series, length: int = 10) -> Optional[Dict[str, Any]]:
        """Detect ROC-price divergence patterns."""
        # Align data properly
        df = pd.concat({"roc": roc, "px": prices}, axis=1, join="inner").dropna()
        if len(df) < max(15, length):
            return None

        # Use length-driven window
        win = min(max(10, length), len(df))
        r, p = df["roc"].tail(win), df["px"].tail(win)

        # Scale prominence to recent std
        prom_r = max(1e-6, r.std() * 0.6)
        prom_p = max(1e-6, p.std() * 0.6)

        # Find peaks and troughs with scaled prominence
        roc_peaks = self._find_peaks(r, prominence=prom_r)
        roc_troughs = self._find_troughs(r, prominence=prom_r)
        price_peaks = self._find_peaks(p, prominence=prom_p)
        price_troughs = self._find_troughs(p, prominence=prom_p)
        
        # Bullish divergence: price lower lows, ROC higher lows
        if len(roc_troughs) >= 2 and len(price_troughs) >= 2:
            latest_roc_trough = roc_troughs[-1]
            prev_roc_trough = roc_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]
            
            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_roc_trough["value"] > prev_roc_trough["value"]):
                return {
                    "type": "bullish_divergence",
                    "description": "Price making lower lows while ROC making higher lows - momentum improving",
                    "strength": "medium"
                }
        
        # Bearish divergence: price higher highs, ROC lower highs
        if len(roc_peaks) >= 2 and len(price_peaks) >= 2:
            latest_roc_peak = roc_peaks[-1]
            prev_roc_peak = roc_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]
            
            if (latest_price_peak["value"] > prev_price_peak["value"] and 
                latest_roc_peak["value"] < prev_roc_peak["value"]):
                return {
                    "type": "bearish_divergence",
                    "description": "Price making higher highs while ROC making lower highs - momentum weakening",
                    "strength": "medium"
                }
        
        return None
    
    def _analyze_roc_patterns(self, roc: pd.Series, length: int = 10) -> Dict[str, Any]:
        """Analyze ROC patterns and formations."""
        patterns = {}
        
        if len(roc) >= 15:
            # Double peaks/troughs in ROC
            double_pattern = self._detect_roc_double_patterns(roc)
            if double_pattern:
                patterns["double_pattern"] = double_pattern
            
            # ROC momentum exhaustion
            exhaustion = self._detect_roc_exhaustion(roc)
            if exhaustion:
                patterns["exhaustion"] = exhaustion
        
        return patterns
    
    def _detect_roc_double_patterns(self, roc: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect double peaks or troughs in ROC."""
        if len(roc) < 12:
            return None
        
        recent_roc = roc.iloc[-12:]
        peaks = self._find_peaks(recent_roc, prominence=1)
        troughs = self._find_troughs(recent_roc, prominence=1)
        
        # Double peak
        if len(peaks) >= 2:
            last_peak = peaks[-1]
            prev_peak = peaks[-2]
            
            if abs(last_peak["value"] - prev_peak["value"]) < 1 and abs(last_peak["index"] - prev_peak["index"]) <= 8:
                return {
                    "type": "double_peak",
                    "description": f"Double peak in ROC around {last_peak['value']:.1f}%",
                    "implication": "Momentum may be weakening"
                }
        
        # Double trough
        if len(troughs) >= 2:
            last_trough = troughs[-1]
            prev_trough = troughs[-2]
            
            if abs(last_trough["value"] - prev_trough["value"]) < 1 and abs(last_trough["index"] - prev_trough["index"]) <= 8:
                return {
                    "type": "double_trough",
                    "description": f"Double trough in ROC around {last_trough['value']:.1f}%",
                    "implication": "Momentum may be strengthening"
                }
        
        return None
    
    def _detect_roc_exhaustion(self, roc: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect momentum exhaustion patterns."""
        if len(roc) < 8:
            return None
        
        current_roc = roc.iloc[-1]
        recent_values = roc.iloc[-5:]
        
        # Positive momentum exhaustion
        if current_roc > 3 and all(val > 2 for val in recent_values):
            velocity = self._calculate_velocity(recent_values, 2)
            if velocity < -0.5:  # Slowing down
                return {
                    "type": "positive_momentum_exhaustion",
                    "description": "High positive ROC starting to decelerate",
                    "implication": "Upward momentum may be exhausting"
                }
        
        # Negative momentum exhaustion
        elif current_roc < -3 and all(val < -2 for val in recent_values):
            velocity = self._calculate_velocity(recent_values, 2)
            if velocity > 0.5:  # Less negative = recovering
                return {
                    "type": "negative_momentum_exhaustion",
                    "description": "High negative ROC starting to recover",
                    "implication": "Downward momentum may be exhausting"
                }
        
        return None
    
    def _generate_roc_signals(self, current_roc: float, momentum_analysis: Dict,
                             overbought_oversold: Dict, trend_analysis: Dict) -> List[Dict[str, Any]]:
        """Generate ROC trading signals."""
        signals = []
        
        # Extreme condition signals
        condition = overbought_oversold.get("condition", "neutral")
        streak = overbought_oversold.get("current_streak", 0)
        
        if condition == "extreme_overbought" and streak >= 3:
            signals.append({
                "type": "momentum_exhaustion_sell",
                "strength": "strong",
                "reason": f"ROC extreme overbought for {streak} periods ({current_roc:+.1f}%)",
                "confidence": 0.8
            })
        elif condition == "extreme_oversold" and streak >= 3:
            signals.append({
                "type": "momentum_exhaustion_buy",
                "strength": "strong",
                "reason": f"ROC extreme oversold for {streak} periods ({current_roc:+.1f}%)",
                "confidence": 0.8
            })
        
        # Momentum direction signals
        momentum_direction = momentum_analysis.get("direction", "neutral")
        strength_level = momentum_analysis.get("strength_level", "weak")
        persistence = momentum_analysis.get("persistence", 0.5)
        
        if momentum_direction == "positive" and strength_level in ["strong", "very_strong"] and persistence > 0.6:
            signals.append({
                "type": "strong_momentum_buy",
                "strength": "medium",
                "reason": f"Strong positive momentum ({current_roc:+.1f}%) with high persistence",
                "confidence": 0.7
            })
        elif momentum_direction == "negative" and strength_level in ["strong", "very_strong"] and persistence > 0.6:
            signals.append({
                "type": "strong_momentum_sell",
                "strength": "medium",
                "reason": f"Strong negative momentum ({current_roc:+.1f}%) with high persistence",
                "confidence": 0.7
            })
        
        # Zero line signals
        if abs(current_roc) < 0.5:  # Near zero
            signals.append({
                "type": "momentum_neutral",
                "strength": "low",
                "reason": f"ROC near zero ({current_roc:+.1f}%) - momentum stalling",
                "confidence": 0.6
            })
        
        # Trend change signals
        trend_direction = trend_analysis.get("direction", "sideways")
        if trend_direction == "rising" and current_roc < 0:
            signals.append({
                "type": "momentum_recovery",
                "strength": "low",
                "reason": "ROC trend turning up from negative territory",
                "confidence": 0.5
            })
        elif trend_direction == "falling" and current_roc > 0:
            signals.append({
                "type": "momentum_deterioration",
                "strength": "low",
                "reason": "ROC trend turning down from positive territory",
                "confidence": 0.5
            })
        
        return signals
    
    def _calculate_roc_confidence(self, roc: pd.Series, momentum_analysis: Dict, trend_analysis: Dict) -> float:
        """Calculate ROC analysis confidence."""
        confidence_factors = []
        
        # Data quantity factor
        data_factor = min(1.0, len(roc) / 20)  # ROC needs less data
        confidence_factors.append(data_factor)
        
        # Momentum persistence factor
        persistence = momentum_analysis.get("persistence", 0.5)
        confidence_factors.append(persistence)
        
        # Trend consistency factor
        trend_consistency = trend_analysis.get("consistency", 0.5)
        confidence_factors.append(trend_consistency)
        
        # Signal clarity factor (extreme values give higher confidence)
        current_roc = abs(roc.iloc[-1])
        if current_roc > 5:
            clarity_factor = 0.9
        elif current_roc > 2:
            clarity_factor = 0.7
        elif current_roc > 1:
            clarity_factor = 0.6
        else:
            clarity_factor = 0.4
        confidence_factors.append(clarity_factor)
        
        return round(np.mean(confidence_factors), 3)
    
    def _generate_roc_summary(self, current_roc: float, momentum_analysis: Dict, overbought_oversold: Dict,
                             divergence: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable ROC summary with enriched signals."""
        direction = momentum_analysis.get("direction", "neutral")
        strength_level = momentum_analysis.get("strength_level", "weak")
        condition = overbought_oversold.get("condition", "neutral")

        summary = f"ROC={current_roc:+.2f}% ({strength_level} {direction})"

        # Zone status
        if condition != "neutral":
            streak = overbought_oversold.get("current_streak", 0)
            if "extreme" in condition:
                summary += f". ⚠️ {condition.replace('_', ' ').upper()} ({streak}p)"
            elif streak > 0:
                summary += f", {condition} ({streak}p)"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "bullish" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE (momentum recovering)"
            elif "bearish" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE (momentum weakening)"

        # Pattern analysis (exhaustion, double patterns)
        if pattern_analysis:
            if "exhaustion" in pattern_analysis:
                exh = pattern_analysis["exhaustion"]
                if "positive_momentum_exhaustion" in exh.get("type", ""):
                    summary += ". ⚠️ Upward momentum exhausting"
                elif "negative_momentum_exhaustion" in exh.get("type", ""):
                    summary += ". ✓ Downward momentum exhausting"

            if "double_pattern" in pattern_analysis:
                dp = pattern_analysis["double_pattern"]
                if "double_peak" in dp.get("type", ""):
                    summary += ". ⚠️ Double peak"
                elif "double_trough" in dp.get("type", ""):
                    summary += ". ✓ Double trough"

        # Momentum evolution
        evolution = momentum_analysis.get("evolution", "")
        if evolution == "accelerating":
            summary += ". Momentum accelerating"
        elif evolution == "decelerating":
            summary += ". Momentum decelerating"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full ROC output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "roc", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        momentum = full_output.get("momentum_analysis", {})
        obos = full_output.get("overbought_oversold", {})
        roc_val = to_native(current.get("value"))
        direction = momentum.get("direction", "neutral")
        condition = obos.get("condition", "neutral")
        zone = "overbought" if condition == "overbought" else "oversold" if condition == "oversold" else "neutral"
        patterns = []
        if momentum.get("strength_level") == "strong":
            patterns.append("momentum_strong_up" if direction == "bullish" else "momentum_strong_down" if direction == "bearish" else "")
        patterns = [p for p in patterns if p]
        return {"indicator": "roc", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(roc_val), 4) if roc_val else None,
                "value_secondary": None, "value_tertiary": None,
                "velocity": round(to_native(momentum.get("acceleration", 0)) or 0, 4),
                "rank": 0.5, "zone": zone, "zone_periods": to_native(obos.get("current_streak", 0)) or 0,
                "trend": "rising" if direction == "bullish" else "falling" if direction == "bearish" else "neutral",
                "crossover_type": None, "crossover_periods_ago": None,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
