"""
Parabolic SAR Preprocessor.

Advanced Parabolic SAR preprocessing with trend following analysis,
stop and reverse signals, and acceleration factor tracking.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class ParabolicSARPreprocessor(BasePreprocessor):
    """Advanced Parabolic SAR preprocessor with professional-grade trend following analysis."""
    
    def preprocess(self, psar: pd.Series, prices: pd.Series = None, high_prices: pd.Series = None,
                  low_prices: pd.Series = None, length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced Parabolic SAR preprocessing with comprehensive trend following analysis.

        PSAR provides dynamic stop-and-reverse levels that adapt to price momentum.
        When price is above PSAR, trend is bullish; when below, trend is bearish.

        Args:
            psar: Parabolic SAR values
            prices: Close price series for analysis
            high_prices: High price series (optional, for better analysis)
            low_prices: Low price series (optional, for better analysis)
            length: Lookback period for analysis (default: 14)

        Returns:
            Dictionary with comprehensive Parabolic SAR analysis
        """
        # Clean and align data
        psar = pd.to_numeric(psar, errors="coerce").dropna()
        if prices is None:
            return {"error": "Price data required for Parabolic SAR analysis"}
        prices = pd.to_numeric(prices, errors="coerce").dropna()

        # Inner join to ensure alignment
        df = pd.concat({"psar": psar, "price": prices}, axis=1, join="inner").dropna()
        if len(df) < 5:
            return {"error": "Insufficient data for Parabolic SAR analysis"}

        psar, prices = df["psar"], df["price"]

        # Get timestamp from series index or use UTC
        ts = (psar.index[-1].isoformat()
              if is_datetime64_any_dtype(psar.index)
              else datetime.now(timezone.utc).isoformat())

        # Safe denominator function
        def _den(x: float) -> float:
            return max(1e-12, abs(float(x)))

        current_psar = float(psar.iloc[-1])
        current_price = float(prices.iloc[-1])
        
        # Trend direction analysis
        trend_analysis = self._analyze_psar_trend(psar, prices)
        
        # Signal analysis (stops and reversals)
        signal_analysis = self._analyze_psar_signals(psar, prices, high_prices, low_prices)
        
        # Distance and momentum analysis
        distance_analysis = self._analyze_price_psar_distance(psar, prices)
        
        # Acceleration analysis
        acceleration_analysis = self._analyze_psar_acceleration(psar)
        
        # Pattern analysis
        pattern_analysis = self._analyze_psar_patterns(psar, prices)
        
        # Stop loss analysis
        stop_loss_analysis = self._analyze_stop_loss_levels(psar, prices)
        
        return {
            "indicator": "Parabolic_SAR",
            "current": {
                "psar_value": round(current_psar, 4),
                "price": round(current_price, 4),
                "distance": round(current_price - current_psar, 4),
                "distance_percentage": round(((current_price - current_psar) / _den(current_price)) * 100, 3),
                "timestamp": ts
            },
            "context": {
                "trend": trend_analysis,
                "distance": distance_analysis,
                "acceleration": acceleration_analysis,
                "length": length,
                "calculation_periods": len(psar)
            },
            "levels": {
                "current_stop_level": round(current_psar, 4),
                "stop_distance_pct": round(abs((current_price - current_psar) / _den(current_price)) * 100, 3),
                "trend_direction": trend_analysis.get("current_trend", "unknown")
            },
            "patterns": {
                **pattern_analysis,
                "signal_analysis": signal_analysis
            },
            "evidence": {
                "data_quality": {
                    "aligned_periods": len(psar),
                    "had_high_low_data": high_prices is not None and low_prices is not None,
                    "calculation_periods": length
                },
                "stop_loss": stop_loss_analysis,
                "calculation_notes": f"Parabolic SAR analysis based on {len(psar)} aligned periods"
            },
            "summary": self._generate_psar_summary(current_psar, current_price, trend_analysis, signal_analysis,
                                               distance_analysis, pattern_analysis)
        }
    
    def _analyze_psar_trend(self, psar: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Analyze Parabolic SAR trend characteristics."""
        current_psar = psar.iloc[-1]
        current_price = prices.iloc[-1]
        
        # Current trend direction
        current_trend = "bullish" if current_price > current_psar else "bearish"
        
        # Trend duration analysis
        trend_periods = self._calculate_trend_duration(psar, prices)
        
        # Trend strength based on price-PSAR distance
        price_distance = abs(current_price - current_psar)
        avg_distance = abs(prices - psar).mean()
        trend_strength = min(1.0, price_distance / max(1e-12, avg_distance))
        
        # Trend consistency
        trend_consistency = self._calculate_trend_consistency(psar, prices)
        
        return {
            "current_direction": current_trend,
            "trend_periods": trend_periods,
            "trend_strength": round(trend_strength, 3),
            "trend_consistency": round(trend_consistency, 3),
            "strength_interpretation": self._interpret_trend_strength(trend_strength)
        }
    
    def _calculate_trend_duration(self, psar: pd.Series, prices: pd.Series) -> int:
        """Calculate how many periods the current trend has lasted."""
        if len(psar) < 2:
            return 1
        
        current_trend = "bullish" if prices.iloc[-1] > psar.iloc[-1] else "bearish"
        duration = 1
        
        for i in range(2, len(psar) + 1):
            if i > len(psar):
                break
            
            price = prices.iloc[-i]
            psar_val = psar.iloc[-i]
            trend = "bullish" if price > psar_val else "bearish"
            
            if trend == current_trend:
                duration += 1
            else:
                break
        
        return duration
    
    def _calculate_trend_consistency(self, psar: pd.Series, prices: pd.Series) -> float:
        """Calculate consistency of trend signals."""
        if len(psar) < 10:
            return 0.5
        
        # Look at last 10 periods
        recent_psar = psar.iloc[-10:]
        recent_prices = prices.iloc[-10:]
        
        # Count consistent trend periods
        bullish_periods = sum(1 for i in range(len(recent_prices)) 
                             if recent_prices.iloc[i] > recent_psar.iloc[i])
        
        consistency = max(bullish_periods, 10 - bullish_periods) / 10
        return consistency
    
    def _interpret_trend_strength(self, strength: float) -> str:
        """Interpret trend strength value."""
        if strength >= 0.8:
            return "very_strong"
        elif strength >= 0.6:
            return "strong"
        elif strength >= 0.4:
            return "moderate"
        elif strength >= 0.2:
            return "weak"
        else:
            return "very_weak"
    
    def _analyze_psar_signals(self, psar: pd.Series, prices: pd.Series, 
                             high_prices: pd.Series = None, low_prices: pd.Series = None) -> Dict[str, Any]:
        """Analyze Parabolic SAR stop and reverse signals."""
        signals = []
        
        # Find recent SAR reversals
        for i in range(1, min(10, len(psar))):
            curr_price = prices.iloc[-i]
            curr_psar = psar.iloc[-i]
            prev_price = prices.iloc[-(i+1)]
            prev_psar = psar.iloc[-(i+1)]
            
            curr_trend = "bullish" if curr_price > curr_psar else "bearish"
            prev_trend = "bullish" if prev_price > prev_psar else "bearish"
            
            if curr_trend != prev_trend:
                # SAR reversal occurred - fix window slicing
                start = max(0, len(prices) - (i + 5))
                end = len(prices) - max(0, i - 5)
                if end - start >= 3:
                    win_p = prices.iloc[start:end]
                    win_s = psar.iloc[start:end]
                else:
                    win_p = prices.iloc[-10:]
                    win_s = psar.iloc[-10:]
                signal_strength = self._calculate_reversal_strength(win_p, win_s)
                
                signals.append({
                    "type": f"sar_reversal_{curr_trend}",
                    "periods_ago": i,
                    "price_at_reversal": round(curr_price, 4),
                    "psar_at_reversal": round(curr_psar, 4),
                    "strength": round(signal_strength, 3)
                })
        
        return {
            "recent_reversals": signals[:3],
            "latest_reversal": signals[0] if signals else None,
            "reversal_frequency": self._calculate_reversal_frequency(psar, prices)
        }
    
    def _calculate_reversal_strength(self, prices: pd.Series, psar: pd.Series) -> float:
        """Calculate strength of a SAR reversal signal."""
        if len(prices) < 3:
            return 0.5

        # Realistic, sign-safe scaling
        base = max(1e-12, prices.abs().mean() * 0.05)
        price_momentum = abs(prices.iloc[-1] - prices.iloc[0]) / max(1, len(prices))
        avg_distance = abs(prices - psar).mean()

        strength = (price_momentum + avg_distance) / base
        return float(min(1.0, max(0.0, strength)))
    
    def _calculate_reversal_frequency(self, psar: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Calculate frequency of SAR reversals."""
        if len(psar) < 20:
            return {"insufficient_data": True}
        
        reversals = 0
        prev_trend = "bullish" if prices.iloc[0] > psar.iloc[0] else "bearish"
        
        for i in range(1, len(psar)):
            curr_trend = "bullish" if prices.iloc[i] > psar.iloc[i] else "bearish"
            if curr_trend != prev_trend:
                reversals += 1
            prev_trend = curr_trend
        
        periods_per_reversal = len(psar) / max(1, reversals)
        
        return {
            "total_reversals": reversals,
            "periods_per_reversal": round(periods_per_reversal, 1),
            "reversal_rate": "high" if periods_per_reversal < 10 else "medium" if periods_per_reversal < 20 else "low"
        }
    
    def _analyze_price_psar_distance(self, psar: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Analyze distance between price and PSAR."""
        distance = prices - psar
        current_distance = distance.iloc[-1]
        
        # Distance statistics
        avg_distance = distance.mean()
        max_distance = distance.max()
        min_distance = distance.min()
        distance_volatility = distance.std()
        
        # Relative distance (as percentage of price)
        relative_distance = (distance / prices.abs().clip(lower=1e-12) * 100)
        current_relative = relative_distance.iloc[-1]
        
        return {
            "current_absolute": round(current_distance, 4),
            "current_relative_pct": round(current_relative, 3),
            "average_distance": round(avg_distance, 4),
            "max_distance": round(max_distance, 4),
            "min_distance": round(min_distance, 4),
            "distance_volatility": round(distance_volatility, 4),
            "distance_interpretation": self._interpret_distance(current_relative, relative_distance.std())
        }
    
    def _interpret_distance(self, current_relative: float, volatility: float) -> str:
        """Interpret price-PSAR distance."""
        abs_distance = abs(current_relative)

        if abs_distance > volatility * 2:
            return "very_wide_distance"
        elif abs_distance > volatility:
            return "wide_distance"
        elif abs_distance < volatility * 0.25:
            return "very_close_distance"
        elif abs_distance < volatility * 0.5:
            return "close_distance"
        else:
            return "normal_distance"
    
    def _analyze_psar_acceleration(self, psar: pd.Series) -> Dict[str, Any]:
        """Analyze PSAR acceleration characteristics."""
        if len(psar) < 5:
            return {}
        
        # PSAR velocity (how fast it's moving)
        psar_velocity = self._calculate_velocity(psar, 3)
        psar_acceleration = self._calculate_acceleration(psar, 5)
        
        # Rate of change in PSAR
        def _den(x: float) -> float:
            return max(1e-12, abs(float(x)))
        psar_roc = ((psar.iloc[-1] - psar.iloc[-5]) / _den(psar.iloc[-5]) * 100) if len(psar) >= 5 else 0.0
        
        return {
            "velocity": round(psar_velocity, 6),
            "acceleration": round(psar_acceleration, 6),
            "rate_of_change_5p": round(psar_roc, 3),
            "acceleration_interpretation": self._interpret_psar_acceleration(psar_velocity, psar_acceleration)
        }
    
    def _interpret_psar_acceleration(self, velocity: float, acceleration: float) -> str:
        """Interpret PSAR acceleration characteristics."""
        if abs(velocity) > 0.001:
            if velocity > 0 and acceleration > 0:
                return "accelerating_upward"
            elif velocity > 0 and acceleration <= 0:
                return "decelerating_upward"
            elif velocity < 0 and acceleration < 0:
                return "accelerating_downward"
            elif velocity < 0 and acceleration >= 0:
                return "decelerating_downward"
        
        return "stable"
    
    def _analyze_psar_patterns(self, psar: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Analyze PSAR patterns and formations."""
        patterns = {}
        
        if len(psar) >= 15:
            # PSAR clustering (periods of tight SAR levels)
            clustering = self._detect_psar_clustering(psar)
            if clustering:
                patterns["clustering"] = clustering
            
            # Extended trend patterns
            extended_trend = self._detect_extended_trends(psar, prices)
            if extended_trend:
                patterns["extended_trend"] = extended_trend
        
        return patterns
    
    def _detect_psar_clustering(self, psar: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect periods where PSAR values cluster together."""
        if len(psar) < 10:
            return None
        
        recent_psar = psar.iloc[-10:]
        psar_range = recent_psar.max() - recent_psar.min()
        psar_avg = recent_psar.mean()
        
        # Clustering if range is very small relative to average value
        clustering_threshold = max(1e-12, abs(psar_avg) * 0.02)  # 2% of average
        
        if psar_range < clustering_threshold:
            return {
                "type": "tight_clustering",
                "range": round(psar_range, 4),
                "periods": 10,
                "description": "PSAR values clustered tightly, potential breakout setup"
            }
        
        return None
    
    def _detect_extended_trends(self, psar: pd.Series, prices: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect extended trend periods without reversals."""
        trend_duration = self._calculate_trend_duration(psar, prices)
        
        if trend_duration >= 20:
            current_trend = "bullish" if prices.iloc[-1] > psar.iloc[-1] else "bearish"
            return {
                "type": "extended_trend",
                "direction": current_trend,
                "duration": trend_duration,
                "description": f"Extended {current_trend} trend for {trend_duration} periods"
            }
        
        return None
    
    def _analyze_stop_loss_levels(self, psar: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Analyze PSAR as dynamic stop loss levels."""
        current_psar = psar.iloc[-1]
        current_price = prices.iloc[-1]
        
        # Stop loss distance
        stop_distance = abs(current_price - current_psar)
        den = max(1e-12, abs(current_price))
        stop_distance_pct = (stop_distance / den) * 100
        
        # Historical stop performance
        stop_performance = self._calculate_stop_performance(psar, prices)
        
        return {
            "current_stop_level": round(current_psar, 4),
            "stop_distance": round(stop_distance, 4),
            "stop_distance_pct": round(stop_distance_pct, 3),
            "stop_type": "trailing_stop",
            "performance": stop_performance,
            "recommendation": self._get_stop_recommendation(stop_distance_pct)
        }
    
    def _calculate_stop_performance(self, psar: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Calculate historical performance of PSAR stops."""
        if len(psar) < 20:
            return {"insufficient_data": True}
        
        # Count how often PSAR acted as effective support/resistance
        support_resistance_hits = 0
        total_tests = 0
        
        for i in range(5, len(psar) - 5):
            psar_level = psar.iloc[i]
            
            # Check if price tested this level in surrounding periods
            nearby_prices = prices.iloc[i-5:i+5]
            
            # Count tests within 1% of PSAR level
            den = max(1e-12, abs(psar_level))
            tests = sum(1 for p in nearby_prices if abs(p - psar_level) / den < 0.01)

            if tests > 0:
                total_tests += 1
                # Check if level held (price bounced)
                if any(abs(p - psar_level) / den < 0.005 for p in nearby_prices):
                    support_resistance_hits += 1
        
        effectiveness = support_resistance_hits / max(1, total_tests) if total_tests > 0 else 0
        
        return {
            "effectiveness_rate": round(effectiveness, 3),
            "total_tests": total_tests,
            "successful_stops": support_resistance_hits
        }
    
    def _get_stop_recommendation(self, stop_distance_pct: float) -> str:
        """Get recommendation based on stop distance."""
        if stop_distance_pct < 0.5:
            return "very_tight_stop"
        elif stop_distance_pct < 1.0:
            return "tight_stop"
        elif stop_distance_pct < 2.0:
            return "reasonable_stop"
        elif stop_distance_pct < 3.0:
            return "wide_stop"
        else:
            return "very_wide_stop"
    
    def _generate_psar_signals(self, psar_value: float, price: float, 
                              trend_analysis: Dict, signal_analysis: Dict) -> List[Dict[str, Any]]:
        """Generate Parabolic SAR trading signals."""
        signals = []
        
        current_trend = trend_analysis["current_direction"]
        trend_strength = trend_analysis["trend_strength"]
        
        # Recent reversal signals
        latest_reversal = signal_analysis.get("latest_reversal")
        if latest_reversal and latest_reversal["periods_ago"] <= 2:
            reversal_type = latest_reversal["type"]
            signal_type = "buy_signal" if "bullish" in reversal_type else "sell_signal"
            
            signals.append({
                "type": signal_type,
                "strength": "strong" if latest_reversal["strength"] > 0.7 else "medium",
                "reason": f"Recent PSAR reversal to {current_trend} trend",
                "confidence": min(0.9, 0.5 + latest_reversal["strength"])
            })
        
        # Trend continuation signals
        if trend_analysis["trend_periods"] >= 5 and trend_strength > 0.6:
            signals.append({
                "type": f"trend_continuation_{current_trend}",
                "strength": "medium",
                "reason": f"Strong {current_trend} trend continuation (PSAR {trend_analysis['trend_periods']} periods)",
                "strength_score": round(0.6 + (trend_strength * 0.2), 2)
            })
        
        # Stop loss signals
        distance_pct = abs((price - psar_value) / max(1e-12, abs(price))) * 100
        if distance_pct < 0.5:
            signals.append({
                "type": "tight_stop_warning",
                "strength": "low",
                "reason": f"Price very close to PSAR stop level ({distance_pct:.2f}%)",
                "strength_score": 0.7
            })
        
        return signals
    
    def _calculate_psar_confidence(self, psar: pd.Series, prices: pd.Series, 
                                  trend_analysis: Dict, distance_analysis: Dict) -> float:
        """Calculate Parabolic SAR analysis confidence."""
        confidence_factors = []
        
        # Data quantity factor
        data_factor = min(1.0, len(psar) / 30)
        confidence_factors.append(data_factor)
        
        # Trend consistency factor
        trend_consistency = trend_analysis.get("trend_consistency", 0.5)
        confidence_factors.append(trend_consistency)
        
        # Trend strength factor
        trend_strength = trend_analysis.get("trend_strength", 0.5)
        confidence_factors.append(trend_strength)
        
        # Distance stability factor
        distance_interpretation = distance_analysis.get("distance_interpretation", "normal_distance")
        if distance_interpretation in ["very_wide_distance", "very_close_distance"]:
            distance_factor = 0.6  # Extreme distances reduce confidence
        else:
            distance_factor = 0.8
        confidence_factors.append(distance_factor)
        
        return round(np.mean(confidence_factors), 3)
    
    def _generate_psar_summary(self, psar_value: float, price: float,
                              trend_analysis: Dict, signal_analysis: Dict,
                              distance_analysis: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable Parabolic SAR summary with enriched signals."""
        current_trend = trend_analysis.get("current_direction", "unknown")
        trend_periods = trend_analysis.get("trend_periods", 0)
        distance_pct = abs((price - psar_value) / max(1e-12, abs(price))) * 100

        summary = f"PSAR={psar_value:.4f} ({current_trend}, {trend_periods}p)"

        # Recent reversal - critical trend change signal
        latest_reversal = signal_analysis.get("latest_reversal")
        if latest_reversal and latest_reversal.get("periods_ago", 99) <= 3:
            reversal_type = "bullish" if "bullish" in latest_reversal.get("type", "") else "bearish"
            strength = latest_reversal.get("strength", 0)
            if strength > 0.6:
                summary += f". ⚠️ Strong {reversal_type} reversal {latest_reversal['periods_ago']}p ago"
            else:
                summary += f". {reversal_type.capitalize()} reversal {latest_reversal['periods_ago']}p ago"

        # Distance warning (price approaching stop)
        if distance_pct < 0.5:
            summary += ". ⚠️ Price very close to stop level"
        elif distance_pct < 1.0:
            summary += ". Price near stop level"

        # Extended trend pattern
        if pattern_analysis and "extended_trend" in pattern_analysis:
            ext = pattern_analysis["extended_trend"]
            if ext.get("duration", 0) >= 20:
                summary += f". ✓ Extended {current_trend} trend ({ext['duration']}p)"

        # Clustering pattern (potential breakout)
        if pattern_analysis and "clustering" in pattern_analysis:
            summary += ". ⚠️ PSAR clustering - breakout setup"

        # Trend strength
        trend_strength = trend_analysis.get("trend_strength", 0)
        if trend_strength > 0.7:
            summary += ". Strong trend"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full PSAR output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "psar", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        trend_data = full_output.get("trend_analysis", {})
        signal = full_output.get("signal_analysis", {})
        psar_val = to_native(current.get("psar"))
        direction = trend_data.get("current_direction", "unknown")
        zone = "bullish" if direction == "uptrend" else "bearish" if direction == "downtrend" else "neutral"
        patterns = []
        reversal = signal.get("latest_reversal")
        if reversal and reversal.get("periods_ago", 99) <= 3:
            patterns.append("crossover_bullish" if "up" in reversal.get("type", "") else "crossover_bearish")
        return {"indicator": "psar", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(psar_val), 4) if psar_val else None,
                "value_secondary": None, "value_tertiary": None,
                "velocity": 0.0, "rank": 0.5, "zone": zone,
                "zone_periods": to_native(trend_data.get("trend_periods", 0)) or 0,
                "trend": "bullish" if direction == "uptrend" else "bearish" if direction == "downtrend" else "neutral",
                "crossover_type": patterns[0] if patterns else None, "crossover_periods_ago": to_native(reversal.get("periods_ago")) if reversal else None,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
