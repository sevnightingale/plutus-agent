"""
Bollinger Bands Preprocessor.

Advanced Bollinger Bands preprocessing with squeeze analysis, bandwidth tracking,
%B position analysis, and volatility breakout detection.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class BollingerBandsPreprocessor(BasePreprocessor):
    """Advanced Bollinger Bands preprocessor with professional-grade analysis."""
    
    def preprocess(self, upper_band: pd.Series, middle_band: pd.Series, lower_band: pd.Series,
                  prices: pd.Series, **kwargs) -> Dict[str, Any]:
        """
        Advanced Bollinger Bands preprocessing with comprehensive analysis.
        
        Args:
            upper_band: Upper Bollinger Band values
            middle_band: Middle Bollinger Band values (SMA)
            lower_band: Lower Bollinger Band values  
            prices: Price series for position analysis
            
        Returns:
            Dictionary with comprehensive Bollinger Bands analysis
        """
        # Align all series and handle NaN values
        df = pd.DataFrame({
            "price": prices, 
            "upper": upper_band, 
            "middle": middle_band, 
            "lower": lower_band
        }).dropna()
        
        if len(df) < 5:
            return {"error": "Insufficient data for Bollinger Bands analysis"}
        
        # Extract current values safely
        current_price = float(df["price"].iloc[-1])
        current_upper = float(df["upper"].iloc[-1])
        current_middle = float(df["middle"].iloc[-1])
        current_lower = float(df["lower"].iloc[-1])
        
        # Guard against zero-division
        width = max(current_upper - current_lower, 1e-12)
        denom_mid = current_middle if abs(current_middle) > 1e-12 else 1e-12
        
        # Position analysis (%B calculation) - pass clean data
        position_analysis = self._analyze_price_position(df)
        
        # Bandwidth analysis
        bandwidth_analysis = self._analyze_bandwidth(df)
        
        # Squeeze analysis - pass Series explicitly
        squeeze_analysis = self._analyze_squeeze_conditions(df["upper"], df["lower"], df["middle"])
        
        # Band touching analysis - pass Series explicitly
        band_touch_analysis = self._analyze_band_touches(df["price"], df["upper"], df["lower"])
        
        # Volatility analysis
        volatility_analysis = self._analyze_volatility_patterns(df)
        
        # Trend analysis - pass Series explicitly
        trend_analysis = self._analyze_trend_with_bands(df["price"], df["middle"])
        
        # Pattern analysis - pass Series explicitly
        pattern_analysis = self._analyze_bollinger_patterns(df["price"], df["upper"], df["middle"], df["lower"])
        
        return {
            "indicator": "Bollinger_Bands",
            "current": {
                "price": round(current_price, 4),
                "upper_band": round(current_upper, 4),
                "middle_band": round(current_middle, 4),
                "lower_band": round(current_lower, 4),
                "bandwidth": round((current_upper - current_lower) / denom_mid * 100, 2),
                "percent_b": round((current_price - current_lower) / width, 3),
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            "context": {
                "position": position_analysis["position"],
                "percent_b": position_analysis["percent_b"],
                "distance_from_middle": position_analysis["distance_from_middle"],
                "distance_from_middle_pct": position_analysis["distance_from_middle_pct"]
            },
            "levels": {
                "upper_band": round(current_upper, 4),
                "middle_band": round(current_middle, 4),
                "lower_band": round(current_lower, 4),
                "bandwidth_level": bandwidth_analysis.get("level", "unknown"),
                "bandwidth_percentile": bandwidth_analysis.get("percentile", 50)
            },
            "squeeze": {
                "is_squeeze": squeeze_analysis["is_squeeze"],
                "squeeze_periods": squeeze_analysis["squeeze_periods"],
                "squeeze_quality": squeeze_analysis["squeeze_quality"],
                "expansion_potential": squeeze_analysis["expansion_potential"]
            },
            "band_touches": {
                "recent_touches": band_touch_analysis["recent_touches"],
                "total_touches": band_touch_analysis["total_touches"],
                "upper_touches": band_touch_analysis["upper_touches"],
                "lower_touches": band_touch_analysis["lower_touches"],
                "touch_frequency": band_touch_analysis["touch_frequency"]
            },
            "volatility": volatility_analysis,
            "trend": trend_analysis,
            "patterns": pattern_analysis,
            "summary": self._generate_bollinger_summary(current_price, current_upper, current_middle,
                                                       current_lower, position_analysis, squeeze_analysis,
                                                       pattern_analysis, volatility_analysis)
        }
    
    def _analyze_price_position(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze price position relative to Bollinger Bands."""
        current_price = df["price"].iloc[-1]
        current_upper = df["upper"].iloc[-1]
        current_middle = df["middle"].iloc[-1]
        current_lower = df["lower"].iloc[-1]
        
        # %B calculation with zero-division guard
        width = max(current_upper - current_lower, 1e-12)
        percent_b = (current_price - current_lower) / width
        
        # Position classification
        if percent_b > 1.0:
            position = "above_upper"
        elif percent_b > 0.8:
            position = "near_upper"
        elif percent_b > 0.5:
            position = "upper_half"
        elif percent_b > 0.2:
            position = "lower_half"
        elif percent_b >= 0:
            position = "near_lower"
        else:
            position = "below_lower"
        
        # %B momentum
        if len(df) >= 5:
            prev_width = max(df["upper"].iloc[-5] - df["lower"].iloc[-5], 1e-12)
            prev_percent_b = (df["price"].iloc[-5] - df["lower"].iloc[-5]) / prev_width
            percent_b_change = percent_b - prev_percent_b
        else:
            percent_b_change = 0
        
        # Distance from middle with zero-division guard
        denom_mid = current_middle if abs(current_middle) > 1e-12 else 1e-12
        distance_from_middle = current_price - current_middle
        distance_pct = (distance_from_middle / denom_mid) * 100
        
        # Position history analysis
        position_history = self._analyze_position_history(df)
        
        return {
            "percent_b": round(percent_b, 3),
            "position": position,
            "percent_b_change_5p": round(percent_b_change, 3),
            "distance_from_middle": round(distance_from_middle, 4),
            "distance_from_middle_pct": round(distance_pct, 3),
            "position_history": position_history
        }
    
    def _analyze_position_history(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze historical price position within bands."""
        if len(df) < 20:
            return {"insufficient_data": True}
        
        # Calculate %B for all periods with zero-division guard
        width_series = np.maximum(df["upper"] - df["lower"], 1e-12)
        percent_b_series = (df["price"] - df["lower"]) / width_series
        
        # Time spent in different zones
        above_upper = sum(1 for b in percent_b_series if b > 1.0)
        below_lower = sum(1 for b in percent_b_series if b < 0.0)
        upper_half = sum(1 for b in percent_b_series if 0.5 <= b <= 1.0)
        lower_half = sum(1 for b in percent_b_series if 0.0 <= b < 0.5)
        
        total_periods = len(percent_b_series)
        
        return {
            "above_upper_pct": round((above_upper / total_periods) * 100, 1),
            "below_lower_pct": round((below_lower / total_periods) * 100, 1),
            "upper_half_pct": round((upper_half / total_periods) * 100, 1),
            "lower_half_pct": round((lower_half / total_periods) * 100, 1),
            "avg_percent_b": round(percent_b_series.mean(), 3),
            "percent_b_volatility": round(percent_b_series.std(), 3)
        }
    
    def _analyze_bandwidth(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze Bollinger Band bandwidth."""
        # Calculate bandwidth with zero-division guard - keep as Series
        denom = df["middle"].where(df["middle"].abs() >= 1e-12)
        bw = ((df["upper"] - df["lower"]) / denom * 100).dropna()
        
        if len(bw) == 0:
            return {"error": "No valid bandwidth data"}
        
        current_bandwidth = bw.iloc[-1]
        
        # Bandwidth statistics
        mean_bandwidth = bw.mean()
        std_bandwidth = bw.std()
        max_bandwidth = bw.max()
        min_bandwidth = bw.min()
        
        # Bandwidth percentile
        bandwidth_percentile = self._calculate_position_rank(bw, lookback=len(bw))
        
        # Bandwidth trend
        bandwidth_velocity = self._calculate_velocity(bw, 3)
        
        # Bandwidth classification
        if current_bandwidth > mean_bandwidth + std_bandwidth:
            bandwidth_level = "high"
        elif current_bandwidth > mean_bandwidth:
            bandwidth_level = "above_average"
        elif current_bandwidth < mean_bandwidth - std_bandwidth:
            bandwidth_level = "low"
        else:
            bandwidth_level = "below_average"
        
        return {
            "current": round(current_bandwidth, 2),
            "level": bandwidth_level,
            "percentile": round(bandwidth_percentile, 1),
            "velocity": round(bandwidth_velocity, 3),
            "statistics": {
                "mean": round(mean_bandwidth, 2),
                "std": round(std_bandwidth, 2),
                "max": round(max_bandwidth, 2),
                "min": round(min_bandwidth, 2)
            },
            "trend": "expanding" if bandwidth_velocity > 0.1 else "contracting" if bandwidth_velocity < -0.1 else "stable"
        }
    
    def _analyze_squeeze_conditions(self, upper_band: pd.Series, lower_band: pd.Series, middle_band: pd.Series) -> Dict[str, Any]:
        """Analyze Bollinger Band squeeze conditions."""
        # Guard against zero division and handle NaNs
        denom = middle_band.where(middle_band.abs() >= 1e-12)  # keep as Series
        bw = ((upper_band - lower_band) / denom * 100).dropna()
        
        if len(bw) == 0:
            return {
                "is_squeeze": False, 
                "squeeze_periods": 0, 
                "squeeze_threshold": 0.0,
                "expansion_potential": 0.0, 
                "recent_bandwidth_change_pct": 0.0,
                "squeeze_quality": "weak"
            }
        
        current_bandwidth = bw.iloc[-1]
        
        # Squeeze threshold (typically 20-period low bandwidth)
        if len(bw) >= 20:
            squeeze_threshold = bw.rolling(20).min().iloc[-1]
            is_squeeze = current_bandwidth <= squeeze_threshold * 1.05  # 5% tolerance
        else:
            # Fallback: use statistical method
            mean_bandwidth = bw.mean()
            std_bandwidth = bw.std()
            squeeze_threshold = mean_bandwidth - std_bandwidth
            is_squeeze = current_bandwidth <= squeeze_threshold
        
        # Squeeze duration
        squeeze_periods = 0
        if is_squeeze:
            for i in range(len(bw) - 1, -1, -1):
                if bw.iloc[i] <= squeeze_threshold * 1.05:
                    squeeze_periods += 1
                else:
                    break
        
        # Post-squeeze expansion potential
        expansion_potential = min(1.0, squeeze_periods / 10) if squeeze_periods else 0.0
        
        # Recent bandwidth change
        recent_change = ((bw.iloc[-1] / bw.iloc[-5]) - 1) * 100 if len(bw) >= 5 else 0.0
        
        return {
            "is_squeeze": is_squeeze,
            "squeeze_periods": squeeze_periods,
            "squeeze_threshold": round(squeeze_threshold, 2),
            "expansion_potential": round(expansion_potential, 3),
            "recent_bandwidth_change_pct": round(recent_change, 2),
            "squeeze_quality": self._assess_squeeze_quality(squeeze_periods, current_bandwidth, squeeze_threshold)
        }
    
    def _assess_squeeze_quality(self, periods: int, current_bw: float, threshold: float) -> str:
        """Assess quality of squeeze for breakout potential."""
        if periods >= 8 and current_bw < threshold * 0.9:
            return "excellent"
        elif periods >= 5 and current_bw < threshold:
            return "good"
        elif periods >= 3:
            return "moderate"
        else:
            return "weak"
    
    def _analyze_band_touches(self, prices: pd.Series, upper_band: pd.Series, lower_band: pd.Series) -> Dict[str, Any]:
        """Analyze price touches of upper and lower bands."""
        touches = []
        
        # Define touch as price within 1% of band
        touch_threshold = 0.01
        
        for i in range(len(prices)):
            price = prices.iloc[i]
            upper = upper_band.iloc[i]
            lower = lower_band.iloc[i]
            
            # Upper band touch - guard against zero division
            if abs(price - upper) / max(abs(upper), 1e-12) <= touch_threshold:
                touches.append({
                    "index": i,
                    "type": "upper",
                    "price": price,
                    "band_value": upper,
                    "periods_ago": len(prices) - 1 - i
                })
            
            # Lower band touch - guard against zero division
            elif abs(price - lower) / max(abs(lower), 1e-12) <= touch_threshold:
                touches.append({
                    "index": i,
                    "type": "lower",
                    "price": price,
                    "band_value": lower,
                    "periods_ago": len(prices) - 1 - i
                })
        
        # Recent touches (last 10 periods)
        recent_touches = [t for t in touches if t["periods_ago"] <= 10]
        
        # Touch frequency analysis
        upper_touches = [t for t in touches if t["type"] == "upper"]
        lower_touches = [t for t in touches if t["type"] == "lower"]
        
        return {
            "recent_touches": recent_touches[-5:],  # Last 5 touches
            "total_touches": len(touches),
            "upper_touches": len(upper_touches),
            "lower_touches": len(lower_touches),
            "latest_touch": touches[-1] if touches else None,
            "touch_frequency": len(touches) / len(prices) if len(prices) > 0 else 0
        }
    
    def _analyze_volatility_patterns(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze volatility patterns using band width."""
        # Calculate bandwidth with zero-division guard
        denom_series = np.where(np.abs(df["middle"]) < 1e-12, np.nan, df["middle"])
        bandwidth = (df["upper"] - df["lower"]) / denom_series * 100
        bandwidth = bandwidth.dropna()
        
        if len(bandwidth) < 10:
            return {}
        
        # Volatility cycles - fix double-scaled prominence bug
        # Base class already scales by std, so pass unitless factor
        peaks = self._find_peaks(bandwidth, prominence=0.5)
        troughs = self._find_troughs(bandwidth, prominence=0.5)
        
        # Current volatility state
        current_bw = bandwidth.iloc[-1]
        recent_peak = peaks[-1] if peaks else None
        recent_trough = troughs[-1] if troughs else None
        
        if recent_peak and recent_trough:
            if recent_peak["index"] > recent_trough["index"]:
                volatility_state = "post_expansion"
            else:
                volatility_state = "post_contraction"
        else:
            volatility_state = "unclear"
        
        # Volatility regime
        long_term_avg = bandwidth.mean()
        if current_bw > long_term_avg * 1.5:
            regime = "high_volatility"
        elif current_bw < long_term_avg * 0.5:
            regime = "low_volatility"
        else:
            regime = "normal_volatility"
        
        return {
            "current_state": volatility_state,
            "regime": regime,
            "peaks_count": len(peaks),
            "troughs_count": len(troughs),
            "regime_ratio": round(current_bw / long_term_avg, 2)
        }
    
    def _analyze_trend_with_bands(self, prices: pd.Series, middle_band: pd.Series) -> Dict[str, Any]:
        """Analyze trend using middle band (SMA) as reference."""
        current_price = prices.iloc[-1]
        current_middle = middle_band.iloc[-1]
        
        # Price vs middle band
        price_vs_middle = "above" if current_price > current_middle else "below"
        
        # Guard against middle=0 division
        denom = current_middle if abs(current_middle) > 1e-12 else 1e-12
        distance_pct = ((current_price - current_middle) / denom) * 100
        
        # Middle band slope (trend)
        if len(middle_band) >= 5:
            middle_slope = self._calculate_velocity(middle_band, 5)
            if middle_slope > 0:
                middle_trend = "rising"
            elif middle_slope < 0:
                middle_trend = "falling"
            else:
                middle_trend = "flat"
        else:
            middle_slope = 0
            middle_trend = "insufficient_data"
        
        # Trend strength
        trend_strength = min(1.0, abs(distance_pct) / 5)  # Normalize to 5% distance
        
        return {
            "price_vs_middle": price_vs_middle,
            "distance_pct": round(distance_pct, 2),
            "middle_trend": middle_trend,
            "middle_slope": round(middle_slope, 6),
            "trend_strength": round(trend_strength, 3)
        }
    
    def _analyze_bollinger_patterns(self, prices: pd.Series, upper_band: pd.Series, 
                                   middle_band: pd.Series, lower_band: pd.Series) -> Dict[str, Any]:
        """Analyze Bollinger Band patterns."""
        patterns = {}
        
        if len(prices) >= 10:
            # Walking the bands pattern
            walking_pattern = self._detect_walking_bands(prices, upper_band, lower_band)
            if walking_pattern:
                patterns["walking_bands"] = walking_pattern
            
            # Double Bollinger pattern
            double_pattern = self._detect_double_bollinger_touch(prices, upper_band, lower_band)
            if double_pattern:
                patterns["double_touch"] = double_pattern
        
        return patterns
    
    def _detect_walking_bands(self, prices: pd.Series, upper_band: pd.Series, lower_band: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect walking the bands pattern (sustained move along one band)."""
        if len(prices) < 8:
            return None
        
        recent_prices = prices.iloc[-8:]
        recent_upper = upper_band.iloc[-8:]
        recent_lower = lower_band.iloc[-8:]
        
        # Check for walking upper band
        upper_proximity = 0
        for i in range(len(recent_prices)):
            price = recent_prices.iloc[i]
            upper = recent_upper.iloc[i]
            if (price - upper) / upper > -0.05:  # Within 5% of upper band
                upper_proximity += 1
        
        # Check for walking lower band
        lower_proximity = 0
        for i in range(len(recent_prices)):
            price = recent_prices.iloc[i]
            lower = recent_lower.iloc[i]
            if (lower - price) / lower < 0.05:  # Within 5% of lower band
                lower_proximity += 1
        
        if upper_proximity >= 5:
            return {
                "type": "walking_upper_band",
                "periods": upper_proximity,
                "description": f"Price walking upper band for {upper_proximity} periods"
            }
        elif lower_proximity >= 5:
            return {
                "type": "walking_lower_band", 
                "periods": lower_proximity,
                "description": f"Price walking lower band for {lower_proximity} periods"
            }
        
        return None
    
    def _detect_double_bollinger_touch(self, prices: pd.Series, upper_band: pd.Series, lower_band: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect double touch of same band (reversal pattern)."""
        if len(prices) < 15:
            return None
        
        # Get recent band touches
        band_touches = self._analyze_band_touches(prices, upper_band, lower_band)
        recent_touches = band_touches["recent_touches"]
        
        if len(recent_touches) >= 2:
            # Check for double touch of same band
            last_two = recent_touches[-2:]
            if (last_two[0]["type"] == last_two[1]["type"] and 
                last_two[1]["periods_ago"] <= 3):
                
                return {
                    "type": f"double_{last_two[0]['type']}_touch",
                    "description": f"Double touch of {last_two[0]['type']} band",
                    "periods_between": abs(last_two[0]["periods_ago"] - last_two[1]["periods_ago"])
                }
        
        return None
    
    
    def _generate_bollinger_summary(self, price: float, upper: float, middle: float, lower: float,
                                   position_analysis: Dict, squeeze_analysis: Dict,
                                   pattern_analysis: Dict = None, volatility_analysis: Dict = None) -> str:
        """Generate human-readable Bollinger Bands summary with enriched signals."""
        percent_b = position_analysis["percent_b"]
        position = position_analysis["position"]

        # Guard against middle=0 division
        denom = middle if abs(middle) > 1e-12 else 1e-12
        bandwidth = (upper - lower) / denom * 100

        summary = f"BB %B={percent_b:.2f} ({position.replace('_', ' ')}), Upper={upper:.4f}, Mid={middle:.4f}, Lower={lower:.4f}"

        # Squeeze detection - critical volatility signal
        if squeeze_analysis.get("is_squeeze"):
            quality = squeeze_analysis.get("squeeze_quality", "")
            periods = squeeze_analysis.get("squeeze_periods", 0)
            if quality in ["excellent", "good"]:
                summary += f". ⚠️ SQUEEZE ({periods}p, {quality})"
            else:
                summary += f". Squeeze ({periods}p)"

        # Walking bands pattern - strong trend confirmation
        if pattern_analysis:
            if "walking_bands" in pattern_analysis:
                wb = pattern_analysis["walking_bands"]
                if "upper" in wb.get("type", ""):
                    summary += f". ✓ Walking upper band ({wb.get('periods', 0)}p)"
                elif "lower" in wb.get("type", ""):
                    summary += f". ⚠️ Walking lower band ({wb.get('periods', 0)}p)"
            if "double_touch" in pattern_analysis:
                dt = pattern_analysis["double_touch"]
                summary += f". {dt.get('type', '').replace('_', ' ').capitalize()}"

        # Volatility regime
        if volatility_analysis:
            regime = volatility_analysis.get("regime", "")
            if regime == "high_volatility":
                summary += ". Volatility expanding"
            elif regime == "low_volatility":
                summary += ". Volatility contracting"

        # Band break signals
        if percent_b > 1.0:
            summary += ". ⚠️ Price above upper band"
        elif percent_b < 0.0:
            summary += ". ⚠️ Price below lower band"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full Bollinger Bands output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "bbands", "timeframe": timeframe, "timestamp": None,
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
        squeeze = full_output.get("squeeze", {})
        trend_data = full_output.get("trend", {})
        patterns_data = full_output.get("patterns", {})
        volatility = full_output.get("volatility", {})

        percent_b = to_native(current.get("percent_b"))
        bandwidth = to_native(current.get("bandwidth"))

        # Zone based on %B position
        position = context.get("position", "unknown")
        zone_map = {
            "above_upper": "above_upper",
            "near_upper": "upper_half",
            "upper_half": "upper_half",
            "lower_half": "lower_half",
            "near_lower": "lower_half",
            "below_lower": "below_lower"
        }
        zone = zone_map.get(position, "neutral")

        # Trend from price vs middle
        trend = trend_data.get("price_vs_middle", "unknown")
        if trend == "above": trend = "bullish"
        elif trend == "below": trend = "bearish"

        # Pattern extraction
        pattern_codes = []
        if squeeze.get("is_squeeze"):
            quality = squeeze.get("squeeze_quality", "")
            if quality in ["excellent", "good"]:
                pattern_codes.append("squeeze_active")
        if volatility.get("regime") == "high_volatility":
            pattern_codes.append("volatility_expanding")
        elif volatility.get("regime") == "low_volatility":
            pattern_codes.append("volatility_contracting")
        if "walking_bands" in patterns_data:
            wb = patterns_data["walking_bands"]
            if "upper" in wb.get("type", ""):
                pattern_codes.append("momentum_strong_up")
            elif "lower" in wb.get("type", ""):
                pattern_codes.append("momentum_strong_down")

        return {
            "indicator": "bbands",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(percent_b, 3) if percent_b is not None else None,
            "value_secondary": round(bandwidth, 2) if bandwidth is not None else None,
            "value_tertiary": None,
            "velocity": round(to_native(context.get("percent_b_change_5p", 0)) or 0, 4),
            "rank": round(percent_b, 3) if percent_b is not None else 0.0,
            "zone": zone,
            "zone_periods": to_native(squeeze.get("squeeze_periods", 0)) or 0,
            "trend": trend,
            "crossover_type": None,
            "crossover_periods_ago": None,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }