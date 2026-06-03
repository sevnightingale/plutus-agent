"""
Base preprocessor class with common utilities for all technical indicators.

This module provides shared functionality used across all indicator preprocessors,
including mathematical utilities, pattern detection, and signal generation helpers.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

import logging
logger = logging.getLogger(__name__)


class BasePreprocessor:
    """
    Base class providing common functionality for all indicator preprocessors.
    
    Contains shared mathematical utilities, pattern detection algorithms,
    and signal generation helpers used across different technical indicators.
    """
    
    def __init__(self):
        """Initialize base preprocessor with logging."""
        self._log = logger  # no .bind() — stdlib logging doesn't support it

    # ==================================================================================
    # TYPE CONVERSION UTILITIES
    # ==================================================================================

    def _to_python_type(self, value: Any) -> Any:
        """
        Convert numpy types to Python native types for JSON serialization.

        This prevents Pydantic serialization errors when returning analysis results.
        """
        if isinstance(value, (np.integer, np.int64, np.int32, np.int16, np.int8)):
            return int(value)
        elif isinstance(value, (np.floating, np.float64, np.float32, np.float16)):
            return float(value)
        elif isinstance(value, np.bool_):
            return bool(value)
        elif isinstance(value, np.ndarray):
            return value.tolist()
        elif pd.isna(value):
            return None
        elif isinstance(value, dict):
            return {k: self._to_python_type(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            return [self._to_python_type(item) for item in value]
        else:
            return value

    # ==================================================================================
    # MATHEMATICAL UTILITIES
    # ==================================================================================
    
    def _calculate_velocity(self, values: pd.Series, periods: int = 3) -> float:
        """Calculate velocity (rate of change) over specified periods."""
        if len(values) < periods + 1:
            return 0.0

        # Drop NaN values for calculation
        clean_values = values.dropna()
        if len(clean_values) < periods + 1:
            return 0.0

        current = clean_values.iloc[-1]
        previous = clean_values.iloc[-(periods + 1)]
        return float((current - previous) / periods)  # Ensure Python native float
    
    def _calculate_acceleration(self, values: pd.Series, periods: int = 6) -> float:
        """Calculate acceleration (change in velocity)."""
        # Drop NaN values first
        clean_values = values.dropna()
        if len(clean_values) < periods + 3:
            return 0.0

        recent_velocity = self._calculate_velocity(clean_values.iloc[-3:], 2)
        past_velocity = self._calculate_velocity(clean_values.iloc[-(periods+3):-(periods)], 2)

        return float(recent_velocity - past_velocity)  # Ensure Python native float
    
    def _analyze_trend(self, values: pd.Series, periods: List[int] = [5, 10, 20]) -> Dict[str, Any]:
        """Sophisticated trend analysis using multiple timeframes."""
        # Drop NaN values first
        clean_values = values.dropna()
        if len(clean_values) < max(periods):
            return {"direction": "unknown", "strength": 0, "reliability": 0}
        
        trends = {}
        data_std = clean_values.std()
        
        for period in periods:
            if len(clean_values) >= period:
                recent = clean_values.iloc[-period:].values
                if len(recent) > 1 and not np.any(np.isnan(recent)):
                    slope = np.polyfit(range(len(recent)), recent, 1)[0]
                    # Normalize slope by data standard deviation
                    normalized_slope = slope / (data_std + 1e-12)
                    trends[f"ma{period}"] = normalized_slope
        
        # Weighted trend calculation
        if trends:
            weighted_trend = sum(slope * (1/period) for period, slope in 
                               [(int(k[2:]), v) for k, v in trends.items()]) / sum(1/p for p in periods if len(clean_values) >= p)
            
            # Use normalized thresholds (0.1 standard deviations)
            direction = "rising" if weighted_trend > 0.1 else "falling" if weighted_trend < -0.1 else "sideways"
            strength = min(abs(weighted_trend), 1.0)
            
            # Reliability based on trend consistency
            trend_consistency = 1 - np.std(list(trends.values())) / (np.mean(np.abs(list(trends.values()))) + 0.001)
            reliability = max(0, min(1, trend_consistency))

            result = {
                "direction": direction,
                "strength": strength,
                "reliability": reliability,
                "trends_by_period": trends
            }
            return self._to_python_type(result)  # Ensure all values are Python native types

        return {"direction": "unknown", "strength": 0, "reliability": 0}
    
    # ==================================================================================
    # PATTERN DETECTION UTILITIES
    # ==================================================================================
    
    def _find_peaks(self, values: pd.Series, prominence: float = 2) -> List[Dict[str, Any]]:
        """Find peaks in a series."""
        # Drop NaN values and get clean array
        clean_values = values.dropna()
        if len(clean_values) < 3:
            return []
        
        values_array = clean_values.values
        peaks = []
        
        # Use volatility-scaled prominence
        data_std = np.std(values_array)
        scaled_prominence = prominence * data_std if data_std > 0 else prominence
        
        for i in range(1, len(values_array) - 1):
            if (not np.isnan(values_array[i]) and 
                not np.isnan(values_array[i-1]) and 
                not np.isnan(values_array[i+1]) and
                values_array[i] > values_array[i-1] and 
                values_array[i] > values_array[i+1] and
                values_array[i] - min(values_array[i-1], values_array[i+1]) >= scaled_prominence):
                peaks.append({
                    "index": i,
                    "value": values_array[i],
                    "periods_ago": len(clean_values) - 1 - i
                })
        
        return peaks
    
    def _find_troughs(self, values: pd.Series, prominence: float = 2) -> List[Dict[str, Any]]:
        """Find troughs in a series.""" 
        # Drop NaN values and get clean array
        clean_values = values.dropna()
        if len(clean_values) < 3:
            return []
        
        values_array = clean_values.values
        troughs = []
        
        # Use volatility-scaled prominence
        data_std = np.std(values_array)
        scaled_prominence = prominence * data_std if data_std > 0 else prominence
        
        for i in range(1, len(values_array) - 1):
            if (not np.isnan(values_array[i]) and 
                not np.isnan(values_array[i-1]) and 
                not np.isnan(values_array[i+1]) and
                values_array[i] < values_array[i-1] and
                values_array[i] < values_array[i+1] and  
                max(values_array[i-1], values_array[i+1]) - values_array[i] >= scaled_prominence):
                troughs.append({
                    "index": i,
                    "value": values_array[i],
                    "periods_ago": len(clean_values) - 1 - i
                })
        
        return troughs
    
    def _find_recent_extremes(self, values: pd.Series, lookback: int = 20) -> Dict[str, Any]:
        """Find recent extreme values with significance analysis."""
        # Drop NaN values first
        clean_values = values.dropna()
        if len(clean_values) == 0:
            return {"high_value": 0, "high_periods_ago": 0, "high_significance": 0,
                   "low_value": 0, "low_periods_ago": 0, "low_significance": 0}
        
        lookback = min(lookback, len(clean_values))
        recent_values = clean_values.iloc[-lookback:]
        
        # Use positional indices instead of label-based to avoid duplicate index issues
        recent_array = recent_values.values
        high_pos = np.argmax(recent_array)
        low_pos = np.argmin(recent_array)
        
        high_value = recent_array[high_pos]
        low_value = recent_array[low_pos]
        
        # Calculate periods ago using positional indices
        high_periods_ago = len(recent_values) - 1 - high_pos
        low_periods_ago = len(recent_values) - 1 - low_pos
        
        # Significance calculation
        current = clean_values.iloc[-1]
        recent_std = np.std(recent_array)
        high_significance = min(1.0, abs(high_value - current) / (recent_std + 0.001))
        low_significance = min(1.0, abs(low_value - current) / (recent_std + 0.001))
        
        return {
            "high_value": high_value,
            "high_periods_ago": high_periods_ago,
            "high_significance": high_significance,
            "low_value": low_value,
            "low_periods_ago": low_periods_ago,
            "low_significance": low_significance
        }
    
    # ==================================================================================
    # ZONE ANALYSIS UTILITIES
    # ==================================================================================
    
    def _analyze_zones(self, values: pd.Series, upper_threshold: float, lower_threshold: float) -> Dict[str, Any]:
        """Analyze time spent in different zones."""
        # Drop NaN values for analysis
        clean_values = values.dropna()
        if len(clean_values) == 0:
            return {"current_zone": "unknown", "overbought_status": "unknown", "oversold_status": "unknown", 
                   "periods_overbought": 0, "periods_oversold": 0, "overbought_percentage": 0, "oversold_percentage": 0}
        
        current = clean_values.iloc[-1]
        
        # Current zone
        if current >= upper_threshold:
            current_zone = "overbought"
        elif current <= lower_threshold:
            current_zone = "oversold"
        else:
            current_zone = "neutral"
        
        # Time analysis (clean_values already has NaNs removed)
        total_periods = len(clean_values)
        overbought_periods = sum(1 for v in clean_values if v >= upper_threshold)
        oversold_periods = sum(1 for v in clean_values if v <= lower_threshold)
        
        # Current streak analysis
        periods_overbought = 0
        periods_oversold = 0
        
        for i in range(len(clean_values) - 1, -1, -1):
            val = clean_values.iloc[i]
            if current_zone == "overbought" and val >= upper_threshold:
                periods_overbought += 1
            elif current_zone == "oversold" and val <= lower_threshold:
                periods_oversold += 1
            else:
                break
        
        # Calculate indicator-specific far threshold (percentage of range)
        range_size = upper_threshold - lower_threshold
        far_threshold = range_size * 0.15 if range_size > 0 else 15  # 15% of range or fallback to 15
        
        return {
            "current_zone": current_zone,
            "overbought_status": self._get_zone_status(current, upper_threshold, "above", far_threshold),
            "oversold_status": self._get_zone_status(current, lower_threshold, "below", far_threshold),
            "periods_overbought": periods_overbought,
            "periods_oversold": periods_oversold,
            "overbought_percentage": round((overbought_periods / total_periods) * 100, 1),
            "oversold_percentage": round((oversold_periods / total_periods) * 100, 1)
        }
    
    def _get_zone_status(self, value: float, threshold: float, direction: str, far_threshold: float = 15) -> str:
        """Get descriptive zone status with configurable thresholds."""
        diff = abs(value - threshold)
        
        if direction == "above":
            if value > threshold:
                return "far_above" if diff > far_threshold else "above"
            else:
                return "far_below" if diff > far_threshold else "below"
        else:  # below
            if value < threshold:
                return "far_below" if diff > far_threshold else "below"
            else:
                return "far_above" if diff > far_threshold else "above"
    
    # ==================================================================================
    # CROSSOVER AND LEVEL ANALYSIS
    # ==================================================================================
    
    def _analyze_key_levels(self, values: pd.Series, levels: List[float]) -> Dict[str, Any]:
        """Analyze interaction with key levels."""
        crossovers = []
        
        for level in levels:
            # Find recent crossovers
            for i in range(1, min(10, len(values))):
                prev_val = values.iloc[-(i+1)]
                curr_val = values.iloc[-i]
                
                if prev_val <= level < curr_val:
                    crossovers.append({
                        "level": level,
                        "direction": "up",
                        "periods_ago": i,
                        "strength": abs(curr_val - level)
                    })
                elif prev_val >= level > curr_val:
                    crossovers.append({
                        "level": level,
                        "direction": "down", 
                        "periods_ago": i,
                        "strength": abs(curr_val - level)
                    })
        
        # Sort by recency
        crossovers.sort(key=lambda x: x["periods_ago"])
        
        return {
            "recent_crossovers": crossovers[:5],
            "current_level_distances": {level: round(values.iloc[-1] - level, 2) for level in levels}
        }
    
    # ==================================================================================
    # CONFIDENCE AND QUALITY METRICS
    # ==================================================================================
    
    def _calculate_analysis_confidence(self, values: pd.Series, trend: Dict, patterns: Dict) -> float:
        """Calculate overall confidence in the analysis."""
        confidence_factors = []
        
        # Data quantity factor
        data_factor = min(1.0, len(values) / 50)  # Full confidence with 50+ periods
        confidence_factors.append(data_factor)
        
        # Trend confidence
        confidence_factors.append(trend.get("confidence", 0.5))
        
        # Pattern confidence
        if patterns:
            pattern_confidences = [p.get("confidence", 0.5) for p in patterns.values() if isinstance(p, dict)]
            if pattern_confidences:
                confidence_factors.append(np.mean(pattern_confidences))
        
        # Data volatility factor (lower volatility = higher confidence)
        volatility = np.std(values) / np.mean(values) if np.mean(values) != 0 else 1
        volatility_factor = max(0.3, min(1.0, 1 - volatility))
        confidence_factors.append(volatility_factor)
        
        return round(np.mean(confidence_factors), 3)
    
    # ==================================================================================
    # POSITION AND RANK ANALYSIS
    # ==================================================================================
    
    def _calculate_position_rank(self, values: pd.Series, lookback: int = 20) -> float:
        """Calculate position rank within last N bars (percentile)."""
        clean = values.dropna()
        if len(clean) == 0:
            return 0.0

        lookback = min(lookback, len(clean))
        recent_values = clean.iloc[-lookback:]
        current = recent_values.iloc[-1]

        rank = (recent_values < current).sum() / len(recent_values) * 100
        return float(rank)  # Ensure Python native float type for serialization
    
    def _interpret_position_rank(self, rank: float) -> str:
        """Interpret position rank percentile."""
        if rank >= 90:
            return "extremely_high"
        elif rank >= 75:
            return "high"
        elif rank >= 60:
            return "above_average"
        elif rank >= 40:
            return "average"
        elif rank >= 25:
            return "below_average"
        elif rank >= 10:
            return "low"
        else:
            return "extremely_low"

    # ==================================================================================
    # COMPACT FORMAT CONVERSION (for Rei and optimized consumers)
    # ==================================================================================

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """
        Convert full preprocessor output to universal compact format.

        This method should be overridden by each indicator preprocessor to provide
        indicator-specific compact conversion. The base implementation provides
        a generic fallback.

        The compact format is designed to be:
        - Small (~300-400 bytes per indicator-timeframe)
        - Numerically precise (for Rei's Float64 preservation)
        - Self-contained (no external context needed)
        - Universal schema (same fields, optional values)

        Args:
            full_output: The full preprocessor output dict
            timeframe: The timeframe string (e.g., "1h", "4h")

        Returns:
            Compact format dict with universal schema
        """
        # Generic fallback - subclasses should override with specific logic
        indicator_name = full_output.get("indicator", "unknown").lower()

        # Normalize indicator names to short form
        name_mapping = {
            "bollinger_bands": "bbands",
            "bollinger_width": "bbwidth",
            "bb_width": "bbwidth",
            "keltner_channels": "keltner",
            "donchian_channels": "donchian",
            "parabolic_sar": "psar",
            "relative_strength_index": "rsi",
            "stochastic_oscillator": "stochastic",
            "williamsr": "williams_r",  # Keep underscore for this one
            "williams%r": "williams_r",
        }
        # First try direct mapping, then remove underscores for others
        normalized = indicator_name.replace("_", "").replace("%", "")
        indicator_name = name_mapping.get(indicator_name, name_mapping.get(normalized, indicator_name))

        current = full_output.get("current", {})

        # Helper to convert numpy types to Python native
        def to_native(val):
            if val is None:
                return None
            if isinstance(val, (np.integer, np.int64, np.int32)):
                return int(val)
            if isinstance(val, (np.floating, np.float64, np.float32)):
                return float(val)
            return val

        # Try to extract primary value from common structures
        value = current.get("value")
        if value is None:
            # Try other common field names
            for key in ["macd", "adx", "k_percent", "price", "atr"]:
                if key in current:
                    value = current.get(key)
                    break

        return {
            "indicator": indicator_name,
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),

            "value": to_native(value),
            "value_secondary": None,
            "value_tertiary": None,

            "velocity": 0.0,
            "rank": 0.0,

            "zone": "unknown",
            "zone_periods": 0,
            "trend": "unknown",

            "crossover_type": None,
            "crossover_periods_ago": None,

            "patterns": [],

            "analysis": full_output.get("summary", "")
        }