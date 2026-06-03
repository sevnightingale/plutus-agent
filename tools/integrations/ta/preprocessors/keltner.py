"""
Keltner Channels Preprocessor.

Advanced Keltner Channels preprocessing with volatility-based channel analysis,
price position assessment, and breakout detection.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class KeltnerChannelsPreprocessor(BasePreprocessor):
    """Advanced Keltner Channels preprocessor with professional-grade channel analysis."""
    
    def preprocess(self, upper_channel: pd.Series, middle_channel: pd.Series,
                  lower_channel: pd.Series, prices: pd.Series, length: int = 20, **kwargs) -> Dict[str, Any]:
        """
        Advanced Keltner Channels preprocessing with comprehensive channel analysis.

        Keltner Channels use ATR-based volatility bands around an EMA centerline,
        providing dynamic support/resistance levels and trend analysis.

        Args:
            upper_channel: Upper Keltner Channel values
            middle_channel: Middle Keltner Channel (EMA) values
            lower_channel: Lower Keltner Channel values
            prices: Price series for position analysis (required)
            length: Keltner calculation period for configurable windows

        Returns:
            Dictionary with comprehensive Keltner Channels analysis
        """
        # Capture original lengths before alignment
        orig_lengths = {
            "prices": len(prices),
            "upper": len(upper_channel),
            "middle": len(middle_channel),
            "lower": len(lower_channel)
        }

        # Clean and align all input series
        df_aligned = self._clean_and_align_series(prices, upper_channel, middle_channel, lower_channel)
        if len(df_aligned) < 5:
            return {"error": "Insufficient aligned data for Keltner Channels analysis"}

        prices = df_aligned["prices"]
        upper_channel = df_aligned["upper"]
        middle_channel = df_aligned["middle"]
        lower_channel = df_aligned["lower"]

        # Generate proper timestamp
        if hasattr(prices.index, 'tz') or np.issubdtype(prices.index.dtype, np.datetime64):
            timestamp = prices.index[-1].isoformat() if hasattr(prices.index[-1], 'isoformat') else datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        # Safe current values
        current_price = float(prices.iloc[-1])
        current_upper = float(upper_channel.iloc[-1])
        current_middle = float(middle_channel.iloc[-1])
        current_lower = float(lower_channel.iloc[-1])
        
        # Position analysis
        position_analysis = self._analyze_price_position(prices, upper_channel, middle_channel, lower_channel)

        # Channel width analysis
        width_analysis = self._analyze_channel_width(upper_channel, middle_channel, lower_channel, length)

        # Trend analysis
        trend_analysis = self._analyze_keltner_trend(middle_channel, prices, length)

        # Breakout analysis
        breakout_analysis = self._analyze_keltner_breakouts(prices, upper_channel, lower_channel, length)

        # Support/resistance analysis
        support_resistance = self._analyze_channel_support_resistance(prices, upper_channel, middle_channel, lower_channel, length)

        # Squeeze analysis
        squeeze_analysis = self._analyze_keltner_squeeze(upper_channel, lower_channel, middle_channel, length)
        
        return {
            "indicator": "Keltner_Channels",
            "current": {
                "price": round(current_price, 4),
                "upper_channel": round(current_upper, 4),
                "middle_channel": round(current_middle, 4),
                "lower_channel": round(current_lower, 4),
                "channel_width": round((current_upper - current_lower) / self._safe_denom(current_middle) * 100, 2),
                "price_position_pct": position_analysis["position_pct"],  # Reuse guarded value
                "timestamp": timestamp
            },
            "context": {
                "length": length,
                "trend": trend_analysis,
                "squeeze": squeeze_analysis
            },
            "levels": {
                "position": position_analysis,
                "support_resistance": support_resistance
            },
            "patterns": {
                "breakouts": breakout_analysis,
                "width_analysis": width_analysis
            },
            "evidence": {
                "data_quality": {
                    "original_periods": orig_lengths,
                    "aligned_periods": len(df_aligned),
                    "valid_data_percentage": round(len(df_aligned) / max(orig_lengths.values()) * 100, 1)
                },
                "calculation_notes": f"Keltner analysis based on {len(df_aligned)} aligned data points with period {length}"
            },
            "summary": self._generate_keltner_summary(current_price, current_upper, current_middle,
                                                    current_lower, position_analysis, squeeze_analysis,
                                                    breakout_analysis, trend_analysis)
        }
    
    def _clean_and_align_series(self, prices: pd.Series, upper: pd.Series, middle: pd.Series, lower: pd.Series) -> pd.DataFrame:
        """Clean and align all input series."""
        df = pd.concat({
            "prices": pd.to_numeric(prices, errors='coerce'),
            "upper": pd.to_numeric(upper, errors='coerce'),
            "middle": pd.to_numeric(middle, errors='coerce'),
            "lower": pd.to_numeric(lower, errors='coerce')
        }, axis=1, join='inner').dropna()

        # Ensure upper >= lower; if not, swap those rows
        swapped = df["upper"] < df["lower"]
        if swapped.any():
            u, l = df.loc[swapped, "upper"].copy(), df.loc[swapped, "lower"].copy()
            df.loc[swapped, "upper"], df.loc[swapped, "lower"] = l, u

        return df

    def _safe_denom(self, x: float) -> float:
        """Safe denominator to prevent division by zero."""
        return max(1e-12, abs(float(x)))

    def _analyze_price_position(self, prices: pd.Series, upper: pd.Series,
                               middle: pd.Series, lower: pd.Series) -> Dict[str, Any]:
        """Analyze price position within Keltner Channels."""
        current_price = prices.iloc[-1]
        current_upper = upper.iloc[-1]
        current_middle = middle.iloc[-1]
        current_lower = lower.iloc[-1]
        
        # Position calculation (0-100 scale) with div-by-zero guard
        width = current_upper - current_lower
        if abs(width) > 1e-12:
            position_pct = (current_price - current_lower) / width * 100
        else:
            position_pct = 50
        
        # Position classification
        if position_pct > 100:
            position = "above_upper"
        elif position_pct > 80:
            position = "near_upper"
        elif position_pct > 60:
            position = "upper_channel"
        elif position_pct > 40:
            position = "middle_channel"
        elif position_pct > 20:
            position = "lower_channel"
        elif position_pct >= 0:
            position = "near_lower"
        else:
            position = "below_lower"
        
        # Distance from middle with div-by-zero guard
        distance_from_middle = current_price - current_middle
        distance_pct = distance_from_middle / self._safe_denom(current_middle) * 100
        
        # Historical position analysis
        position_history = self._analyze_position_history(prices, upper, middle, lower)
        
        return {
            "position": position,
            "position_pct": round(position_pct, 1),
            "distance_from_middle": round(distance_from_middle, 4),
            "distance_from_middle_pct": round(distance_pct, 3),
            "history": position_history
        }
    
    def _analyze_position_history(self, prices: pd.Series, upper: pd.Series, 
                                 middle: pd.Series, lower: pd.Series) -> Dict[str, Any]:
        """Analyze historical price position within channels."""
        if len(prices) < 20:
            return {"insufficient_data": True}
        
        positions = []
        for i in range(len(prices)):
            price = prices.iloc[i]
            upper_val = upper.iloc[i]
            middle_val = middle.iloc[i]
            lower_val = lower.iloc[i]
            
            width = upper_val - lower_val
            if abs(width) > 1e-12:
                pos_pct = (price - lower_val) / width * 100
            else:
                pos_pct = 50
                
            positions.append(pos_pct)
        
        positions = pd.Series(positions)
        
        # Time in different zones
        above_upper = sum(1 for pos in positions if pos > 100)
        below_lower = sum(1 for pos in positions if pos < 0)
        upper_half = sum(1 for pos in positions if 50 <= pos <= 100)
        lower_half = sum(1 for pos in positions if 0 <= pos < 50)
        
        total_periods = len(positions)
        
        return {
            "above_upper_pct": round((above_upper / total_periods) * 100, 1),
            "below_lower_pct": round((below_lower / total_periods) * 100, 1),
            "upper_half_pct": round((upper_half / total_periods) * 100, 1),
            "lower_half_pct": round((lower_half / total_periods) * 100, 1),
            "avg_position": round(positions.mean(), 1),
            "position_volatility": round(positions.std(), 1)
        }
    
    def _analyze_channel_width(self, upper: pd.Series, middle: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze Keltner Channel width characteristics."""
        # Safe width calculation with guarded denominators
        width = (upper - lower).clip(lower=0) / middle.abs().clip(lower=1e-12) * 100
        current_width = width.iloc[-1]
        
        # Width statistics
        mean_width = width.mean()
        std_width = width.std()
        max_width = width.max()
        min_width = width.min()
        
        # Width percentile with length-based lookback
        lookback = min(length * 2, len(width))
        width_percentile = self._calculate_position_rank(width, lookback=lookback)
        
        # Width classification
        if current_width > mean_width + std_width:
            width_level = "wide"
        elif current_width > mean_width:
            width_level = "above_average"
        elif current_width < mean_width - std_width:
            width_level = "narrow"
        else:
            width_level = "below_average"
        
        # Width trend with configurable window
        velocity_window = max(3, length // 6)
        width_velocity = self._calculate_velocity(width, velocity_window)
        width_trend = "expanding" if width_velocity > 0.1 else "contracting" if width_velocity < -0.1 else "stable"
        
        return {
            "current_width": round(current_width, 2),
            "width_level": width_level,
            "percentile": round(width_percentile, 1),
            "trend": width_trend,
            "velocity": round(width_velocity, 3),
            "statistics": {
                "mean": round(mean_width, 2),
                "std": round(std_width, 2),
                "max": round(max_width, 2),
                "min": round(min_width, 2)
            }
        }
    
    def _analyze_keltner_trend(self, middle: pd.Series, prices: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze trend using Keltner middle line."""
        current_middle = middle.iloc[-1]
        current_price = prices.iloc[-1]
        
        # Middle line trend with configurable window
        slope_window = max(3, length // 4)
        middle_slope = self._calculate_velocity(middle, slope_window)
        
        if middle_slope > 0.001:
            middle_trend = "rising"
        elif middle_slope < -0.001:
            middle_trend = "falling"
        else:
            middle_trend = "flat"
        
        # Price vs middle
        price_vs_middle = "above" if current_price > current_middle else "below"
        
        # Trend strength with div-by-zero guard
        middle_std = middle.std()
        trend_strength = min(1.0, abs(middle_slope) / self._safe_denom(middle_std * 0.1))
        
        return {
            "middle_trend": middle_trend,
            "middle_slope": round(middle_slope, 6),
            "price_vs_middle": price_vs_middle,
            "trend_strength": round(trend_strength, 3)
        }
    
    def _analyze_keltner_breakouts(self, prices: pd.Series, upper: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze breakouts from Keltner Channels."""
        breakouts = []
        
        # Use length-based lookback
        lookback = min(max(5, length), len(prices))
        for i in range(1, lookback):
            prev_price = prices.iloc[-(i+1)]
            curr_price = prices.iloc[-i]
            prev_upper = upper.iloc[-(i+1)]
            curr_upper = upper.iloc[-i]
            prev_lower = lower.iloc[-(i+1)]
            curr_lower = lower.iloc[-i]
            
            # Upward breakout
            if prev_price <= prev_upper and curr_price > curr_upper:
                breakouts.append({
                    "type": "upward_breakout",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "channel_level": round(curr_upper, 4),
                    "strength": (curr_price - curr_upper) / self._safe_denom(curr_upper)
                })
            
            # Downward breakout
            elif prev_price >= prev_lower and curr_price < curr_lower:
                breakouts.append({
                    "type": "downward_breakout",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "channel_level": round(curr_lower, 4),
                    "strength": (curr_lower - curr_price) / self._safe_denom(curr_lower)
                })
        
        return {
            "recent_breakouts": breakouts[:5],
            "latest_breakout": breakouts[0] if breakouts else None,
            "breakout_frequency": len(breakouts) / max(1, lookback - 1)
        }
    
    def _analyze_channel_support_resistance(self, prices: pd.Series, upper: pd.Series,
                                          middle: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze channels as support/resistance."""
        touches = {"upper": [], "middle": [], "lower": []}
        bounces = {"upper": [], "middle": [], "lower": []}
        
        # Touch threshold
        touch_threshold = 0.005  # 0.5%
        
        # Use length-based analysis window for recent bars
        analysis_window = min(length * 2, len(prices))
        start = max(1, len(prices) - analysis_window + 1)
        for i in range(start, len(prices)):
            price = prices.iloc[i]
            prev_price = prices.iloc[i-1]
            
            upper_val = upper.iloc[i]
            middle_val = middle.iloc[i]
            lower_val = lower.iloc[i]
            
            # Check touches and bounces for each level
            levels = [
                ("upper", upper_val),
                ("middle", middle_val),
                ("lower", lower_val)
            ]
            
            for level_name, level_val in levels:
                denom = self._safe_denom(level_val)
                if abs(price - level_val) / denom <= touch_threshold:
                    touches[level_name].append({
                        "index": i,
                        "periods_ago": len(prices) - 1 - i,
                        "price": price
                    })
                    
                    # Check for bounce
                    if i < len(prices) - 2:
                        next_price = prices.iloc[i+1]
                        
                        # Support bounce
                        if prev_price > level_val and next_price > price:
                            bounces[level_name].append({
                                "type": "support_bounce",
                                "periods_ago": len(prices) - 1 - i,
                                "strength": abs(next_price - price) / self._safe_denom(price)
                            })
                        # Resistance bounce
                        elif prev_price < level_val and next_price < price:
                            bounces[level_name].append({
                                "type": "resistance_bounce",
                                "periods_ago": len(prices) - 1 - i,
                                "strength": abs(price - next_price) / self._safe_denom(price)
                            })
        
        # Calculate effectiveness for each level
        effectiveness = {}
        for level in ["upper", "middle", "lower"]:
            total_touches = len(touches[level])
            successful_bounces = len(bounces[level])
            success_rate = (successful_bounces / total_touches) if total_touches > 0 else 0
            
            effectiveness[level] = {
                "touches": total_touches,
                "bounces": successful_bounces,
                "success_rate": round(success_rate, 3),
                "recent_touches": touches[level][-3:],
                "recent_bounces": bounces[level][-2:]
            }
        
        return effectiveness
    
    def _analyze_keltner_squeeze(self, upper: pd.Series, lower: pd.Series, middle: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze Keltner Channel squeeze conditions."""
        # Safe width calculation with guarded denominator
        width = (upper - lower).clip(lower=0) / middle.abs().clip(lower=1e-12) * 100
        current_width = width.iloc[-1]

        # Squeeze threshold with configurable lookback
        lookback = min(max(20, length), len(width))
        if len(width) >= lookback:
            squeeze_threshold = width.rolling(lookback).min().iloc[-1]
        else:
            mean_width = width.mean()
            std_width = width.std()
            squeeze_threshold = mean_width - std_width

        # Squeeze detection
        is_squeeze = current_width <= squeeze_threshold * 1.05

        # Squeeze duration with guarded condition
        squeeze_periods = 0
        if is_squeeze:
            for i in range(len(width) - 1, -1, -1):
                if width.iloc[i] <= squeeze_threshold * 1.05:
                    squeeze_periods += 1
                else:
                    break

        # Safe intensity calculation
        squeeze_threshold_denom = max(1e-12, abs(squeeze_threshold))
        intensity = ((squeeze_threshold - current_width) / squeeze_threshold_denom * 100) if is_squeeze else 0.0

        return {
            "is_squeeze": is_squeeze,
            "squeeze_periods": squeeze_periods,
            "squeeze_threshold": round(squeeze_threshold, 2),
            "current_width": round(current_width, 2),
            "squeeze_intensity": round(intensity, 2)
        }
    
    # Signal generation and confidence scoring methods removed to comply with analysis-only philosophy
    
    def _generate_keltner_summary(self, price: float, upper: float, middle: float, lower: float,
                                position_analysis: Dict, squeeze_analysis: Dict,
                                breakout_analysis: Dict = None, trend_analysis: Dict = None) -> str:
        """Generate human-readable Keltner Channels summary with enriched signals."""
        position = position_analysis.get("position", "middle").replace("_", " ")
        position_pct = position_analysis.get("position_pct", 50)

        summary = f"Keltner %pos={position_pct:.0f}% ({position}), Upper={upper:.4f}, Mid={middle:.4f}, Lower={lower:.4f}"

        # Squeeze detection - critical volatility signal
        if squeeze_analysis.get("is_squeeze", False):
            squeeze_periods = squeeze_analysis.get("squeeze_periods", 0)
            intensity = squeeze_analysis.get("squeeze_intensity", 0)
            if intensity > 50:
                summary += f". ⚠️ TIGHT SQUEEZE ({squeeze_periods}p)"
            else:
                summary += f". Squeeze ({squeeze_periods}p)"

        # Recent breakout - important trend signal
        if breakout_analysis:
            latest = breakout_analysis.get("latest_breakout")
            if latest and latest.get("periods_ago", 99) <= 5:
                breakout_type = "bullish" if "upward" in latest.get("type", "") else "bearish"
                summary += f". ✓ {breakout_type.capitalize()} breakout {latest['periods_ago']}p ago"

        # Trend direction
        if trend_analysis:
            trend = trend_analysis.get("middle_trend", "")
            trend_strength = trend_analysis.get("trend_strength", 0)
            if trend == "rising" and trend_strength > 0.5:
                summary += ". ✓ Strong uptrend"
            elif trend == "falling" and trend_strength > 0.5:
                summary += ". ⚠️ Strong downtrend"

        # Extreme position warnings
        if position_pct > 100:
            summary += ". ⚠️ Above upper channel"
        elif position_pct < 0:
            summary += ". ⚠️ Below lower channel"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full Keltner output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "keltner", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        position = full_output.get("position_analysis", {})
        squeeze = full_output.get("squeeze_analysis", {})
        pos_pct = to_native(position.get("position_pct"))
        pos_name = position.get("position", "middle")
        zone = "above_upper" if "above" in pos_name else "below_lower" if "below" in pos_name else "neutral"
        patterns = []
        if squeeze.get("squeeze_detected"): patterns.append("squeeze_active")
        return {"indicator": "keltner", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(pos_pct), 2) if pos_pct else None,
                "value_secondary": None, "value_tertiary": None,
                "velocity": 0.0, "rank": round(float(pos_pct) / 100, 3) if pos_pct else 0.5,
                "zone": zone, "zone_periods": 0,
                "trend": "bullish" if pos_pct and pos_pct > 50 else "bearish" if pos_pct and pos_pct < 50 else "neutral",
                "crossover_type": None, "crossover_periods_ago": None,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
