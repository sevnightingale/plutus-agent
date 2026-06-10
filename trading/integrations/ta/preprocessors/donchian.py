"""
Donchian Channels Preprocessor.

Advanced Donchian Channels preprocessing with breakout analysis,
channel width assessment, and comprehensive market structure analysis.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class DonchianChannelsPreprocessor(BasePreprocessor):
    """Advanced Donchian Channels preprocessor with breakout and market structure analysis."""
    
    def preprocess(self, upper_channel: pd.Series, middle_channel: pd.Series,
                  lower_channel: pd.Series, prices: pd.Series, length: int = 20, **kwargs) -> Dict[str, Any]:
        """
        Advanced Donchian Channels preprocessing with comprehensive breakout analysis.

        Donchian Channels are formed by the highest high and lowest low over N periods,
        creating natural support/resistance levels and breakout signals.

        Args:
            upper_channel: Upper Donchian Channel (highest high over N periods)
            middle_channel: Middle Donchian Channel (average of upper and lower)
            lower_channel: Lower Donchian Channel (lowest low over N periods)
            prices: Price series for analysis (required)
            length: Donchian calculation period

        Returns:
            Dictionary with comprehensive Donchian Channels analysis
        """
        # Sanitize and align all input series
        upper_clean = pd.to_numeric(upper_channel, errors='coerce').dropna()
        middle_clean = pd.to_numeric(middle_channel, errors='coerce').dropna()
        lower_clean = pd.to_numeric(lower_channel, errors='coerce').dropna()
        prices_clean = pd.to_numeric(prices, errors='coerce').dropna()

        if len(upper_clean) < 5 or len(prices_clean) < 5:
            return {"error": "Insufficient data for Donchian Channels analysis"}

        # Align all series on common index
        aligned_data = pd.DataFrame({
            'upper': upper_clean,
            'middle': middle_clean,
            'lower': lower_clean,
            'prices': prices_clean
        }).dropna()

        if len(aligned_data) < 5:
            return {"error": "Insufficient aligned data for Donchian Channels analysis"}

        # Extract aligned series
        upper = aligned_data['upper']
        middle = aligned_data['middle']
        lower = aligned_data['lower']
        prices_aligned = aligned_data['prices']

        # Enforce width correctness (upper >= lower)
        width_check = upper >= lower
        if not width_check.all():
            # Fix inverted channels by swapping where needed
            mask = ~width_check
            upper_temp = upper.copy()
            upper.loc[mask] = lower.loc[mask]
            lower.loc[mask] = upper_temp.loc[mask]

        current_price = float(prices_aligned.iloc[-1])
        current_upper = float(upper.iloc[-1])
        current_middle = float(middle.iloc[-1])
        current_lower = float(lower.iloc[-1])

        # Generate proper timestamp
        if hasattr(prices_aligned.index, 'tz') or np.issubdtype(prices_aligned.index.dtype, np.datetime64):
            timestamp = prices_aligned.index[-1].isoformat() if hasattr(prices_aligned.index[-1], 'isoformat') else datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()
        
        # Position analysis
        position_analysis = self._analyze_price_position_donchian(prices_aligned, upper, middle, lower)

        # Breakout analysis
        breakout_analysis = self._analyze_donchian_breakouts(prices_aligned, upper, lower, length)

        # Channel width analysis
        width_analysis = self._analyze_donchian_width(upper, lower, middle, length)

        # Turtle trading analysis (remove signals, keep analysis)
        turtle_analysis = self._analyze_turtle_patterns(prices_aligned, upper, lower, length)

        # Support/resistance analysis
        support_resistance = self._analyze_donchian_support_resistance(prices_aligned, upper, middle, lower, length)

        # Consolidation analysis
        consolidation_analysis = self._analyze_donchian_consolidation(upper, lower, prices_aligned, length)

        # Trend strength analysis
        trend_analysis = self._analyze_donchian_trend_strength(prices_aligned, upper, lower, length)
        
        return {
            "indicator": "Donchian_Channels",
            "current": {
                "price": round(current_price, 4),
                "upper_channel": round(current_upper, 4),
                "middle_channel": round(current_middle, 4),
                "lower_channel": round(current_lower, 4),
                "channel_width": round(max(0, current_upper - current_lower), 4),
                "price_position_pct": round(((current_price - current_lower) / max(1e-12, current_upper - current_lower)) * 100, 1),
                "timestamp": timestamp
            },
            "context": {
                "length": length,
                "trend": trend_analysis,
                "consolidation": consolidation_analysis
            },
            "levels": {
                "position": position_analysis,
                "support_resistance": support_resistance
            },
            "patterns": {
                "breakouts": breakout_analysis,
                "turtle_patterns": turtle_analysis,
                "width_analysis": width_analysis
            },
            "evidence": {
                "data_quality": {
                    "total_periods": len(aligned_data),
                    "valid_data_percentage": round(len(aligned_data) / len(prices) * 100, 1),
                    "width_corrections": int((~width_check).sum()) if 'width_check' in locals() else 0
                },
                "calculation_notes": f"Donchian analysis based on {len(aligned_data)} aligned data points with period {length}"
            },
            "summary": self._generate_donchian_summary(current_price, current_upper, current_middle,
                                                     current_lower, breakout_analysis, consolidation_analysis,
                                                     trend_analysis, turtle_analysis)
        }
    
    def _analyze_price_position_donchian(self, prices: pd.Series, upper: pd.Series, 
                                       middle: pd.Series, lower: pd.Series) -> Dict[str, Any]:
        """Analyze price position within Donchian Channels."""
        current_price = prices.iloc[-1]
        current_upper = upper.iloc[-1]
        current_middle = middle.iloc[-1]
        current_lower = lower.iloc[-1]
        
        # Position calculation
        if current_upper != current_lower:
            position_pct = ((current_price - current_lower) / (current_upper - current_lower)) * 100
        else:
            position_pct = 50
        
        # Position classification
        if current_price >= current_upper:
            position = "at_upper_breakout"
        elif position_pct > 80:
            position = "near_upper"
        elif position_pct > 60:
            position = "upper_third"
        elif position_pct > 40:
            position = "middle_third"
        elif position_pct > 20:
            position = "lower_third"
        elif position_pct > 0:
            position = "near_lower"
        else:
            position = "at_lower_breakout"
        
        # Distance from edges
        distance_to_upper = current_upper - current_price
        distance_to_lower = current_price - current_lower
        
        return {
            "position": position,
            "position_pct": round(position_pct, 1),
            "distance_to_upper": round(distance_to_upper, 4),
            "distance_to_lower": round(distance_to_lower, 4),
            "distance_to_middle": round(abs(current_price - current_middle), 4)
        }
    
    def _analyze_donchian_breakouts(self, prices: pd.Series, upper: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze breakouts from Donchian Channels."""
        breakouts = []
        
        for i in range(1, min(length, len(prices))):
            curr_price = prices.iloc[-i]
            prev_price = prices.iloc[-(i+1)]
            curr_upper = upper.iloc[-i]
            curr_lower = lower.iloc[-i]
            
            # Upper breakout (price reaches new high)
            if curr_price >= curr_upper and prev_price < curr_upper:
                breakouts.append({
                    "type": "upper_breakout",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "channel_level": round(curr_upper, 4),
                    "strength": (curr_price - curr_upper) / curr_upper if curr_upper > 0 else 0
                })
            
            # Lower breakout (price reaches new low)  
            elif curr_price <= curr_lower and prev_price > curr_lower:
                breakouts.append({
                    "type": "lower_breakout",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "channel_level": round(curr_lower, 4),
                    "strength": (curr_lower - curr_price) / curr_lower if curr_lower > 0 else 0
                })
        
        # Breakout persistence (how long since last breakout)
        latest_upper = next((b for b in breakouts if b["type"] == "upper_breakout"), None)
        latest_lower = next((b for b in breakouts if b["type"] == "lower_breakout"), None)
        
        return {
            "recent_breakouts": breakouts[:5],
            "latest_breakout": breakouts[0] if breakouts else None,
            "latest_upper_breakout": latest_upper,
            "latest_lower_breakout": latest_lower,
            "breakout_frequency": len(breakouts) / min(length, len(prices)) if len(prices) > 0 else 0
        }
    
    def _analyze_donchian_width(self, upper: pd.Series, lower: pd.Series, middle: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze Donchian Channel width characteristics."""
        width = upper - lower
        current_width = width.iloc[-1]
        
        # Width statistics
        mean_width = width.mean()
        std_width = width.std()
        max_width = width.max()
        min_width = width.min()
        
        # Width percentile using length-based lookback
        width_percentile = self._calculate_position_rank(width, lookback=min(length * 2, len(width)))
        
        # Width classification
        if current_width > mean_width + std_width:
            width_level = "very_wide"
        elif current_width > mean_width:
            width_level = "wide"
        elif current_width < mean_width - std_width:
            width_level = "narrow"
        else:
            width_level = "normal"
        
        # Width trend using length-based window
        velocity_window = max(3, length // 6)
        width_velocity = self._calculate_velocity(width, velocity_window)
        
        return {
            "current_width": round(current_width, 4),
            "width_level": width_level,
            "percentile": round(width_percentile, 1),
            "width_velocity": round(width_velocity, 6),
            "trend": "expanding" if width_velocity > 0 else "contracting" if width_velocity < 0 else "stable",
            "statistics": {
                "mean": round(mean_width, 4),
                "max": round(max_width, 4),
                "min": round(min_width, 4)
            }
        }
    
    def _analyze_turtle_patterns(self, prices: pd.Series, upper: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze turtle trading signals based on Donchian breakouts."""
        current_price = prices.iloc[-1]
        current_upper = upper.iloc[-1]
        current_lower = lower.iloc[-1]
        
        # Turtle pattern analysis (no signals)
        at_upper_channel = current_price >= current_upper  # At N-period high
        at_lower_channel = current_price <= current_lower  # At N-period low
        
        # Extended period analysis
        if len(prices) >= length * 2:
            extended_lookback = length * 2
            extended_high = prices.iloc[-extended_lookback:].max()
            extended_low = prices.iloc[-extended_lookback:].min()

            at_extended_high = current_price >= extended_high
            at_extended_low = current_price <= extended_low
        else:
            extended_lookback = None
            at_extended_high = False
            at_extended_low = False
        
        # Short-term reversal analysis
        exit_lookback = max(5, length // 4)
        if len(prices) >= exit_lookback:
            recent_high = prices.iloc[-exit_lookback:].max()
            recent_low = prices.iloc[-exit_lookback:].min()

            at_recent_low = current_price <= recent_low
            at_recent_high = current_price >= recent_high
        else:
            recent_high = recent_low = None
            at_recent_low = at_recent_high = False
        
        return {
            "primary_period": {
                "at_upper_channel": at_upper_channel,
                "at_lower_channel": at_lower_channel,
                "lookback_periods": length
            },
            "extended_period": {
                "at_extended_high": at_extended_high,
                "at_extended_low": at_extended_low,
                "lookback_periods": extended_lookback
            },
            "recent_extremes": {
                "at_recent_low": at_recent_low,
                "at_recent_high": at_recent_high,
                "lookback_periods": exit_lookback
            }
        }
    
    def _analyze_donchian_support_resistance(self, prices: pd.Series, upper: pd.Series,
                                           middle: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze Donchian levels as support/resistance."""
        levels = {
            "upper": {"touches": 0, "bounces": 0, "breaks": 0},
            "middle": {"touches": 0, "bounces": 0, "breaks": 0},
            "lower": {"touches": 0, "bounces": 0, "breaks": 0}
        }
        
        touch_threshold = 0.002  # 0.2%
        
        # Use length-based lookback for S/R analysis
        analysis_window = min(length * 2, len(prices) - 1)
        for i in range(1, analysis_window):
            price = prices.iloc[i]
            prev_price = prices.iloc[i-1]
            next_price = prices.iloc[i+1]
            
            upper_val = upper.iloc[i]
            middle_val = middle.iloc[i]
            lower_val = lower.iloc[i]
            
            # Check touches for each level
            level_data = [
                ("upper", upper_val),
                ("middle", middle_val), 
                ("lower", lower_val)
            ]
            
            for level_name, level_val in level_data:
                if level_val > 0 and abs(price - level_val) / level_val <= touch_threshold:
                    levels[level_name]["touches"] += 1
                    
                    # Check for bounce
                    if level_name == "upper" and prev_price < level_val and next_price < price:
                        levels[level_name]["bounces"] += 1
                    elif level_name == "lower" and prev_price > level_val and next_price > price:
                        levels[level_name]["bounces"] += 1
                    elif level_name == "middle":
                        # Middle can act as either support or resistance
                        if (prev_price < level_val and next_price > price) or (prev_price > level_val and next_price < price):
                            levels[level_name]["bounces"] += 1
                
                # Check for breaks using cross events (not bar counts)
                prev_val = prices.iloc[i-1]
                if level_name == "upper" and prev_val <= level_val and price > level_val:
                    levels[level_name]["breaks"] += 1
                elif level_name == "lower" and prev_val >= level_val and price < level_val:
                    levels[level_name]["breaks"] += 1
        
        # Calculate effectiveness
        for level in levels:
            total_tests = levels[level]["touches"]
            if total_tests > 0:
                levels[level]["bounce_rate"] = round(levels[level]["bounces"] / total_tests, 3)
            else:
                levels[level]["bounce_rate"] = 0
        
        return levels
    
    def _analyze_donchian_consolidation(self, upper: pd.Series, lower: pd.Series, prices: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze consolidation patterns in Donchian Channels."""
        width = upper - lower
        current_width = width.iloc[-1]
        
        # Consolidation detection using rolling statistics
        lookback_window = min(length * 2, len(width))
        if lookback_window >= 5:
            recent_width = width.iloc[-lookback_window:]
            mean_width = recent_width.mean()
            std_width = recent_width.std()
        else:
            mean_width = width.mean()
            std_width = width.std()
        
        is_consolidation = current_width < mean_width - 0.5 * std_width
        
        # Consolidation duration
        consolidation_periods = 0
        if is_consolidation:
            threshold = mean_width - 0.5 * std_width
            for i in range(len(width) - 1, -1, -1):
                if width.iloc[i] < threshold:
                    consolidation_periods += 1
                else:
                    break
        
        # Price range within consolidation
        if consolidation_periods > 0:
            recent_prices = prices.iloc[-consolidation_periods:]
            price_range = recent_prices.max() - recent_prices.min()
            avg_price = recent_prices.mean()
            range_pct = (price_range / avg_price) * 100 if avg_price > 0 else 0
        else:
            price_range = 0
            range_pct = 0
        
        return {
            "is_consolidation": is_consolidation,
            "consolidation_periods": consolidation_periods,
            "width_threshold": round(mean_width - 0.5 * std_width, 4),
            "price_range": round(price_range, 4),
            "price_range_pct": round(range_pct, 2),
            "breakout_potential": "high" if consolidation_periods >= 10 else "medium" if consolidation_periods >= 5 else "low"
        }
    
    def _analyze_donchian_trend_strength(self, prices: pd.Series, upper: pd.Series, lower: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze trend strength using Donchian position."""
        current_price = prices.iloc[-1]
        current_upper = upper.iloc[-1]
        current_lower = lower.iloc[-1]
        
        # Position-based trend strength
        if current_upper != current_lower:
            position_pct = ((current_price - current_lower) / (current_upper - current_lower)) * 100
        else:
            position_pct = 50
        
        # Trend classification using neutral language
        if position_pct >= 80:
            trend_strength = "strong_upward"
        elif position_pct >= 60:
            trend_strength = "moderate_upward"
        elif position_pct <= 20:
            trend_strength = "strong_downward"
        elif position_pct <= 40:
            trend_strength = "moderate_downward"
        else:
            trend_strength = "neutral"
        
        # Channel utilization using length-based window
        util_window = max(5, length // 2)
        recent_prices = prices.iloc[-util_window:] if len(prices) >= util_window else prices
        recent_range = recent_prices.max() - recent_prices.min()
        channel_width = current_upper - current_lower
        
        utilization = (recent_range / channel_width) if channel_width > 0 else 0
        
        return {
            "strength": trend_strength,
            "position_pct": round(position_pct, 1),
            "channel_utilization": round(utilization, 3),
            "utilization_rating": "high" if utilization > 0.8 else "medium" if utilization > 0.5 else "low"
        }
    
    # Signal generation and confidence scoring methods removed to comply with analysis-only philosophy
    
    def _generate_donchian_summary(self, price: float, upper: float, middle: float, lower: float,
                                 breakout_analysis: Dict, consolidation_analysis: Dict,
                                 trend_analysis: Dict = None, turtle_analysis: Dict = None) -> str:
        """Generate human-readable Donchian Channels summary with enriched signals."""
        position_pct = ((price - lower) / (upper - lower)) * 100 if upper != lower else 50

        summary = f"Donchian %pos={position_pct:.0f}%, Upper={upper:.4f}, Mid={middle:.4f}, Lower={lower:.4f}"

        # Recent breakout - critical signal
        latest_breakout = breakout_analysis.get("latest_breakout")
        if latest_breakout and latest_breakout.get("periods_ago", 99) <= 5:
            breakout_type = "bullish" if "upper" in latest_breakout.get("type", "") else "bearish"
            strength = latest_breakout.get("strength", 0)
            if strength > 0.01:
                summary += f". ⚠️ Strong {breakout_type} breakout {latest_breakout['periods_ago']}p ago"
            else:
                summary += f". {breakout_type.capitalize()} breakout {latest_breakout['periods_ago']}p ago"

        # Consolidation - potential breakout setup
        if consolidation_analysis.get("is_consolidation", False):
            periods = consolidation_analysis.get("consolidation_periods", 0)
            potential = consolidation_analysis.get("breakout_potential", "")
            if potential == "high":
                summary += f". ⚠️ CONSOLIDATION ({periods}p) - high breakout potential"
            else:
                summary += f". Consolidation ({periods}p)"

        # Trend strength from position
        if trend_analysis:
            strength = trend_analysis.get("strength", "")
            if "strong_upward" in strength:
                summary += ". ✓ Strong uptrend (near highs)"
            elif "strong_downward" in strength:
                summary += ". ⚠️ Strong downtrend (near lows)"

        # Turtle trading signals
        if turtle_analysis:
            primary = turtle_analysis.get("primary_period", {})
            if primary.get("at_upper_channel"):
                summary += ". ✓ New N-period high"
            elif primary.get("at_lower_channel"):
                summary += ". ⚠️ New N-period low"

        # Extreme positions
        if position_pct >= 100:
            summary += ". ⚠️ At upper channel"
        elif position_pct <= 0:
            summary += ". ⚠️ At lower channel"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full Donchian output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "donchian", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        breakout = full_output.get("breakout_analysis", {})
        position = full_output.get("position_analysis", {})
        pos_pct = to_native(position.get("position_pct"))
        latest_breakout = breakout.get("latest_breakout")
        patterns = []
        cross_type = None
        cross_periods = None
        if latest_breakout and latest_breakout.get("periods_ago", 99) <= 5:
            btype = latest_breakout.get("type", "")
            cross_type = "crossover_bullish" if "upper" in btype else "crossover_bearish" if "lower" in btype else None
            cross_periods = to_native(latest_breakout.get("periods_ago"))
            patterns.append("breakout" if cross_type else "")
        patterns = [p for p in patterns if p]
        zone = "upper" if pos_pct and pos_pct > 80 else "lower" if pos_pct and pos_pct < 20 else "middle"
        return {"indicator": "donchian", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(pos_pct), 2) if pos_pct else None,
                "value_secondary": None, "value_tertiary": None,
                "velocity": 0.0, "rank": round(float(pos_pct) / 100, 3) if pos_pct else 0.5,
                "zone": zone, "zone_periods": 0,
                "trend": "bullish" if pos_pct and pos_pct > 50 else "bearish" if pos_pct and pos_pct < 50 else "neutral",
                "crossover_type": cross_type, "crossover_periods_ago": cross_periods,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
