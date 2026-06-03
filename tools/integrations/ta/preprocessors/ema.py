"""
EMA (Exponential Moving Average) Preprocessor.

Advanced EMA preprocessing with responsiveness analysis, trend detection,
and comparison with SMA for enhanced signal quality assessment.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class EMAPreprocessor(BasePreprocessor):
    """Advanced EMA preprocessor with professional-grade responsiveness analysis."""
    
    def preprocess(self, ema: pd.Series, prices: pd.Series = None, sma: pd.Series = None,
                  length: int = 20, **kwargs) -> Dict[str, Any]:
        """
        Advanced EMA preprocessing with comprehensive responsiveness analysis.

        EMA gives more weight to recent prices, making it more responsive than SMA.
        This responsiveness can provide earlier signals but also more false signals.

        Args:
            ema: EMA values
            prices: Price series for position analysis (optional)
            sma: SMA values for comparison (optional)
            length: EMA calculation period

        Returns:
            Dictionary with comprehensive EMA analysis
        """
        # Clean and align all input series
        ema_clean = pd.to_numeric(ema, errors='coerce').dropna()
        if len(ema_clean) < 5:
            return {"error": "Insufficient data for EMA analysis"}

        prices_clean = None if prices is None else pd.to_numeric(prices, errors='coerce').dropna()
        sma_clean = None if sma is None else pd.to_numeric(sma, errors='coerce').dropna()

        # Single inner-join alignment across all present series
        frames = {"ema": ema_clean}
        if prices_clean is not None:
            frames["prices"] = prices_clean
        if sma_clean is not None:
            frames["sma"] = sma_clean

        df_aligned = pd.concat(frames, axis=1, join='inner').dropna()
        ema_clean = df_aligned["ema"]
        prices_clean = df_aligned["prices"] if "prices" in df_aligned else None
        sma_clean = df_aligned["sma"] if "sma" in df_aligned else None

        if len(ema_clean) < 5:
            return {"error": "Insufficient aligned data for EMA analysis"}

        # Generate proper timestamp
        if hasattr(ema_clean.index, 'tz') or np.issubdtype(ema_clean.index.dtype, np.datetime64):
            timestamp = ema_clean.index[-1].isoformat() if hasattr(ema_clean.index[-1], 'isoformat') else datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        # Safe current values with div-by-zero guards
        current_ema = float(ema_clean.iloc[-1])
        current_price = float(prices_clean.iloc[-1]) if prices_clean is not None and len(prices_clean) > 0 else None
        ema_denom = max(1e-12, abs(current_ema))
        
        # Trend analysis with length-based windows
        trend_analysis = self._analyze_ema_trend(ema_clean, length)

        # Responsiveness analysis
        responsiveness_analysis = self._analyze_ema_responsiveness(ema_clean)

        # Price-EMA relationship
        price_relationship = {}
        if prices_clean is not None:
            price_relationship = self._analyze_price_ema_relationship(prices_clean, ema_clean)

        # EMA-SMA comparison
        ema_sma_comparison = {}
        if sma_clean is not None:
            ema_sma_comparison = self._analyze_ema_sma_comparison(ema_clean, sma_clean, prices_clean)

        # Crossover analysis
        crossover_analysis = {}
        if prices_clean is not None:
            crossover_analysis = self._analyze_price_ema_crossovers(prices_clean, ema_clean, length)

        # Signal quality assessment
        signal_quality = self._assess_ema_signal_quality(ema_clean, responsiveness_analysis)

        # Support/resistance analysis
        support_resistance = self._analyze_ema_support_resistance(ema_clean, prices_clean)
        
        return {
            "indicator": "EMA",
            "current": {
                "ema_value": round(current_ema, 4),
                "price": round(current_price, 4) if current_price is not None else None,
                "price_distance": round(current_price - current_ema, 4) if current_price is not None else None,
                "price_distance_pct": round((current_price - current_ema) / ema_denom * 100, 3) if current_price is not None else None,
                "timestamp": timestamp
            },
            "context": {
                "length": length,
                "responsiveness": responsiveness_analysis,
                "signal_quality": signal_quality
            },
            "levels": {
                "trend": trend_analysis,
                "price_relationship": price_relationship,
                "support_resistance": support_resistance
            },
            "patterns": {
                "crossovers": crossover_analysis,
                "ema_sma_comparison": ema_sma_comparison
            },
            "evidence": {
                "data_quality": {
                    "original_ema_periods": len(ema),
                    "aligned_periods": len(df_aligned),
                    "valid_data_percentage": round(len(df_aligned) / len(ema) * 100, 1),
                    "had_prices": "prices" in frames,
                    "had_sma": "sma" in frames,
                    "has_price_data": prices_clean is not None,
                    "has_sma_comparison": sma_clean is not None
                },
                "calculation_notes": f"EMA analysis based on {len(ema_clean)} aligned data points with period {length}"
            },
            "summary": self._generate_ema_summary(current_ema, current_price, trend_analysis, responsiveness_analysis,
                                               crossover_analysis, support_resistance)
        }
    
    def _analyze_ema_trend(self, ema: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze EMA trend characteristics."""
        # Length-based trend analysis windows
        short_periods = max(2, length // 10)
        medium_periods = max(3, length // 4)
        long_periods = max(5, length // 2)

        short_trend = self._calculate_ema_trend_direction(ema, short_periods)
        medium_trend = self._calculate_ema_trend_direction(ema, medium_periods)
        long_trend = self._calculate_ema_trend_direction(ema, long_periods) if len(ema) >= long_periods else "insufficient_data"
        
        # Trend consistency (EMAs should be more volatile)
        trend_consistency = self._calculate_ema_trend_consistency(ema)
        
        # Trend strength with length-based velocity window
        velocity_window = max(3, length // 6)
        slope = self._calculate_velocity(ema, velocity_window)
        ema_std = ema.std()
        trend_strength = min(1.0, abs(slope) / max(1e-12, ema_std * 0.15)) if ema_std > 1e-12 else 0

        # Acceleration using length-based window
        accel_window = max(3, length // 4)
        acceleration = self._calculate_acceleration(ema, accel_window)
        
        # Overall trend consensus
        trends = [t for t in [short_trend, medium_trend, long_trend] if t not in ["insufficient_data", "sideways"]]
        if trends:
            rising_count = sum(1 for t in trends if t == "rising")
            falling_count = sum(1 for t in trends if t == "falling")
            
            if rising_count > falling_count:
                consensus = "rising"
            elif falling_count > rising_count:
                consensus = "falling"
            else:
                consensus = "mixed"
        else:
            consensus = "sideways"
        
        return {
            "short_term": short_trend,
            "medium_term": medium_trend,
            "long_term": long_trend,
            "consensus": consensus,
            "strength": round(trend_strength, 3),
            "consistency": round(trend_consistency, 3),
            "slope": round(slope, 6),
            "acceleration": round(acceleration, 6)
        }
    
    def _calculate_ema_trend_direction(self, ema: pd.Series, periods: int) -> str:
        """Calculate EMA trend direction (more sensitive thresholds)."""
        if len(ema) < periods + 1:
            return "insufficient_data"
        
        start_value = ema.iloc[-(periods + 1)]
        end_value = ema.iloc[-1]
        
        start_denom = max(1e-12, abs(start_value))
        change_pct = (end_value - start_value) / start_denom * 100
        
        # Lower thresholds for EMA due to higher responsiveness
        if change_pct > 0.1:
            return "rising"
        elif change_pct < -0.1:
            return "falling"
        else:
            return "sideways"
    
    def _calculate_ema_trend_consistency(self, ema: pd.Series) -> float:
        """Calculate EMA trend consistency (expect lower consistency due to responsiveness)."""
        if len(ema) < 8:
            return 0.5
        
        # Look at direction changes over recent periods (shorter for EMA)
        recent_ema = ema.iloc[-8:]
        changes = recent_ema.diff().dropna()
        
        if len(changes) == 0:
            return 0.5
        
        positive_changes = sum(1 for x in changes if x > 0)
        negative_changes = sum(1 for x in changes if x < 0)
        total_changes = len(changes)
        
        max_directional = max(positive_changes, negative_changes)
        return max_directional / total_changes if total_changes > 0 else 0.5
    
    def _analyze_ema_responsiveness(self, ema: pd.Series) -> Dict[str, Any]:
        """Analyze EMA responsiveness characteristics."""
        if len(ema) < 5:
            return {}
        
        # Rate of change analysis
        ema_changes = ema.diff().dropna()
        avg_change = ema_changes.abs().mean()
        max_change = ema_changes.abs().max()
        
        # Volatility of the EMA itself with div-by-zero guard
        ema_volatility = ema.std()
        ema_mean = ema.mean()
        mean_denom = max(1e-12, abs(ema_mean))
        relative_volatility = ema_volatility / mean_denom
        
        # Direction change frequency
        direction_changes = 0
        prev_direction = None
        
        for change in ema_changes:
            current_direction = "up" if change > 0 else "down" if change < 0 else "flat"
            if prev_direction and current_direction != prev_direction and current_direction != "flat":
                direction_changes += 1
            prev_direction = current_direction
        
        change_frequency = direction_changes / len(ema_changes) if len(ema_changes) > 0 else 0
        
        # Responsiveness score (higher = more responsive)
        responsiveness_score = min(1.0, (relative_volatility * 10 + change_frequency) / 2)
        
        return {
            "avg_change": round(avg_change, 6),
            "max_change": round(max_change, 6),
            "relative_volatility": round(relative_volatility, 6),
            "direction_changes": direction_changes,
            "change_frequency": round(change_frequency, 3),
            "responsiveness_score": round(responsiveness_score, 3),
            "responsiveness_rating": self._rate_responsiveness(responsiveness_score)
        }
    
    def _rate_responsiveness(self, score: float) -> str:
        """Rate EMA responsiveness level."""
        if score > 0.7:
            return "very_high"
        elif score > 0.5:
            return "high"
        elif score > 0.3:
            return "moderate"
        elif score > 0.1:
            return "low"
        else:
            return "very_low"
    
    def _analyze_price_ema_relationship(self, prices: pd.Series, ema: pd.Series) -> Dict[str, Any]:
        """Analyze price position relative to EMA."""
        current_price = prices.iloc[-1]
        current_ema = ema.iloc[-1]
        
        # Current position
        if current_price > current_ema:
            position = "above"
        elif current_price < current_ema:
            position = "below" 
        else:
            position = "at_level"
        
        # Distance analysis with div-by-zero guards
        distance = current_price - current_ema
        ema_denom = max(1e-12, abs(current_ema))
        distance_pct = distance / ema_denom * 100

        # Historical position analysis using vectorized operations
        above_ema_mask = prices > ema
        above_ema_pct = above_ema_mask.mean() * 100
        below_ema_pct = 100 - above_ema_pct

        # Average distance from EMA
        distances = prices - ema
        avg_distance = distances.mean()
        ema_mean = ema.mean()
        ema_mean_denom = max(1e-12, abs(ema_mean))
        avg_distance_pct = avg_distance / ema_mean_denom * 100
        
        return {
            "position": position,
            "distance": round(distance, 4),
            "distance_pct": round(distance_pct, 3),
            "above_ema_pct": round(above_ema_pct, 1),
            "below_ema_pct": round(below_ema_pct, 1),
            "avg_distance": round(avg_distance, 4),
            "avg_distance_pct": round(avg_distance_pct, 3)
        }
    
    def _analyze_ema_sma_comparison(self, ema: pd.Series, sma: pd.Series, prices: pd.Series = None) -> Dict[str, Any]:
        """Compare EMA vs SMA characteristics."""
        # Series are already aligned, no length check needed
        
        current_ema = ema.iloc[-1]
        current_sma = sma.iloc[-1]
        
        # Current relationship
        if current_ema > current_sma:
            ema_sma_position = "ema_above_sma"
        elif current_ema < current_sma:
            ema_sma_position = "ema_below_sma"
        else:
            ema_sma_position = "ema_equals_sma"
        
        # Spread analysis
        spread = ema - sma
        current_spread = spread.iloc[-1]
        avg_spread = spread.mean()
        spread_volatility = spread.std()
        
        # Responsiveness comparison
        ema_changes = ema.diff().dropna().abs().mean()
        sma_changes = sma.diff().dropna().abs().mean()
        responsiveness_ratio = ema_changes / sma_changes if sma_changes > 0 else 1
        
        # Signal timing analysis
        signal_timing = {}
        if prices is not None:
            signal_timing = self._analyze_ema_sma_signal_timing(prices, ema, sma)
        
        return {
            "position": ema_sma_position,
            "current_spread": round(current_spread, 4),
            "avg_spread": round(avg_spread, 4),
            "spread_volatility": round(spread_volatility, 4),
            "responsiveness_ratio": round(responsiveness_ratio, 3),
            "signal_timing": signal_timing
        }
    
    def _analyze_ema_sma_signal_timing(self, prices: pd.Series, ema: pd.Series, sma: pd.Series) -> Dict[str, Any]:
        """Analyze timing differences between EMA and SMA signals."""
        # Ensure all series are aligned for this analysis
        min_len = min(len(prices), len(ema), len(sma))
        prices_sync = prices.iloc[:min_len]
        ema_sync = ema.iloc[:min_len]
        sma_sync = sma.iloc[:min_len]

        # Find crossovers for both
        ema_crossovers = []
        sma_crossovers = []

        for i in range(1, min_len):
            # EMA crossovers
            if ((prices_sync.iloc[i-1] <= ema_sync.iloc[i-1] and prices_sync.iloc[i] > ema_sync.iloc[i]) or
                (prices_sync.iloc[i-1] >= ema_sync.iloc[i-1] and prices_sync.iloc[i] < ema_sync.iloc[i])):
                cross_type = "rising" if prices_sync.iloc[i] > ema_sync.iloc[i] else "falling"
                ema_crossovers.append({"index": i, "type": cross_type})

            # SMA crossovers
            if ((prices_sync.iloc[i-1] <= sma_sync.iloc[i-1] and prices_sync.iloc[i] > sma_sync.iloc[i]) or
                (prices_sync.iloc[i-1] >= sma_sync.iloc[i-1] and prices_sync.iloc[i] < sma_sync.iloc[i])):
                cross_type = "rising" if prices_sync.iloc[i] > sma_sync.iloc[i] else "falling"
                sma_crossovers.append({"index": i, "type": cross_type})
        
        # Calculate average timing difference - filter to same type first
        timing_differences = []
        for ema_cross in ema_crossovers:
            # Find nearest SMA crossover of same type
            same_type_sma = [x for x in sma_crossovers if x["type"] == ema_cross["type"]]
            if same_type_sma:
                nearest_sma = min(same_type_sma, key=lambda x: abs(x["index"] - ema_cross["index"]))
                timing_diff = ema_cross["index"] - nearest_sma["index"]
                timing_differences.append(timing_diff)
        
        avg_timing_advantage = np.mean(timing_differences) if timing_differences else 0
        
        return {
            "ema_crossovers": len(ema_crossovers),
            "sma_crossovers": len(sma_crossovers),
            "avg_timing_advantage": round(avg_timing_advantage, 2),  # Negative = EMA earlier
            "timing_interpretation": "EMA leads" if avg_timing_advantage < -0.5 else "SMA leads" if avg_timing_advantage > 0.5 else "Similar timing"
        }
    
    def _analyze_price_ema_crossovers(self, prices: pd.Series, ema: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze price crossovers with EMA."""
        crossovers = []
        
        # Use length-based lookback, limited by available data
        lookback = min(max(5, length // 2), len(prices), len(ema))
        for i in range(1, lookback):
            prev_price = prices.iloc[-(i+1)]
            curr_price = prices.iloc[-i]
            prev_ema = ema.iloc[-(i+1)]
            curr_ema = ema.iloc[-i]
            
            # Bullish crossover
            if prev_price <= prev_ema and curr_price > curr_ema:
                crossovers.append({
                    "type": "rising_crossover",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "ema_value": round(curr_ema, 4),
                    "strength": abs(curr_price - curr_ema) / max(1e-12, abs(curr_ema))
                })
            
            # Bearish crossover
            elif prev_price >= prev_ema and curr_price < curr_ema:
                crossovers.append({
                    "type": "falling_crossover",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "ema_value": round(curr_ema, 4),
                    "strength": abs(curr_price - curr_ema) / max(1e-12, abs(curr_ema))
                })
        
        return {
            "recent_crossovers": crossovers[:5],
            "latest_crossover": crossovers[0] if crossovers else None,
            "crossover_frequency": len(crossovers) / max(1, lookback - 1)
        }
    
    def _assess_ema_signal_quality(self, ema: pd.Series, responsiveness_analysis: Dict) -> Dict[str, Any]:
        """Assess quality of EMA signals."""
        # Signal reliability based on responsiveness
        responsiveness_score = responsiveness_analysis.get("responsiveness_score", 0.5)
        
        # High responsiveness can mean more false signals
        if responsiveness_score > 0.7:
            signal_quality = "high_frequency_low_reliability"
        elif responsiveness_score > 0.4:
            signal_quality = "balanced"
        else:
            signal_quality = "low_frequency_high_reliability"
        
        # Noise level assessment
        change_frequency = responsiveness_analysis.get("change_frequency", 0)
        if change_frequency > 0.6:
            noise_level = "high"
        elif change_frequency > 0.3:
            noise_level = "moderate"
        else:
            noise_level = "low"
        
        return {
            "signal_quality": signal_quality,
            "noise_level": noise_level,
            "recommended_use": self._get_ema_usage_recommendation(signal_quality, noise_level)
        }
    
    def _get_ema_usage_recommendation(self, quality: str, noise: str) -> str:
        """Get recommendation for EMA usage based on signal quality."""
        if quality == "high_frequency_low_reliability":
            return "Use with confirmation indicators, good for scalping"
        elif quality == "balanced":
            return "Good for general trend following with moderate filters"
        else:
            return "Reliable for position trading, slower signals"
    
    def _analyze_ema_support_resistance(self, ema: pd.Series, prices: pd.Series = None) -> Dict[str, Any]:
        """Analyze EMA as dynamic support/resistance."""
        if prices is None:
            return {"no_price_data": True}
        
        # EMA tends to provide weaker S/R than SMA due to responsiveness
        touches = []
        bounces = []
        
        # Tighter touch threshold for EMA
        touch_threshold = 0.003  # 0.3%
        
        for i in range(1, len(prices)):
            price = prices.iloc[i]
            ema_val = ema.iloc[i]
            prev_price = prices.iloc[i-1]

            # Check for EMA touch with div-by-zero guard
            ema_denom = max(1e-12, abs(ema_val))
            if abs(price - ema_val) / ema_denom <= touch_threshold:
                touches.append({
                    "index": i,
                    "periods_ago": len(prices) - 1 - i,
                    "price": price,
                    "ema_value": ema_val
                })
                
                # Check for bounce
                if i < len(prices) - 2:
                    next_price = prices.iloc[i+1]
                    
                    # Support bounce
                    if prev_price < ema_val and next_price > price:
                        bounces.append({
                            "type": "support_bounce",
                            "index": i,
                            "periods_ago": len(prices) - 1 - i,
                            "strength": abs(next_price - price) / price
                        })
                    
                    # Resistance bounce
                    elif prev_price > ema_val and next_price < price:
                        bounces.append({
                            "type": "resistance_bounce",
                            "index": i,
                            "periods_ago": len(prices) - 1 - i,
                            "strength": abs(price - next_price) / price
                        })
        
        # Success rate
        success_rate = (len(bounces) / len(touches)) if len(touches) > 0 else 0
        
        return {
            "total_touches": len(touches),
            "successful_bounces": len(bounces),
            "success_rate": round(success_rate, 3),
            "recent_touches": touches[-5:] if touches else [],
            "recent_bounces": bounces[-3:] if bounces else [],
            "effectiveness": "high" if success_rate > 0.5 else "medium" if success_rate > 0.25 else "low"
        }
    
    # Signal generation and confidence scoring methods removed to comply with analysis-only philosophy
    
    def _generate_ema_summary(self, ema_value: float, price: Optional[float],
                             trend_analysis: Dict, responsiveness_analysis: Dict,
                             crossover_analysis: Dict = None, support_resistance: Dict = None) -> str:
        """Generate human-readable EMA summary with enriched signals."""
        consensus = trend_analysis.get("consensus", "mixed")
        trend_strength = trend_analysis.get("strength", 0)

        summary = f"EMA={ema_value:.4f} ({consensus})"

        # Price position relative to EMA
        if price is not None:
            ema_denom = max(1e-12, abs(ema_value))
            distance_pct = (price - ema_value) / ema_denom * 100
            position = "above" if distance_pct > 0 else "below"
            summary += f", price {position} by {abs(distance_pct):.1f}%"

        # Trend strength
        if trend_strength > 0.7:
            summary += ". Strong trend"

        # Recent crossover - important signal
        if crossover_analysis:
            latest_cross = crossover_analysis.get("latest_crossover")
            if latest_cross and latest_cross.get("periods_ago", 99) <= 5:
                cross_type = "bullish" if "rising" in latest_cross.get("type", "") else "bearish"
                summary += f". ✓ {cross_type.capitalize()} crossover {latest_cross['periods_ago']}p ago"

        # Acceleration
        acceleration = trend_analysis.get("acceleration", 0)
        if abs(acceleration) > 0.001:
            if acceleration > 0:
                summary += ". Trend accelerating"
            else:
                summary += ". Trend decelerating"

        # Support/resistance effectiveness
        if support_resistance and not support_resistance.get("no_price_data"):
            effectiveness = support_resistance.get("effectiveness", "")
            if effectiveness == "high":
                summary += ". ✓ Strong dynamic S/R"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full EMA output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "ema", "timeframe": timeframe, "timestamp": None,
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
        trend_data = full_output.get("trend_analysis", {})
        price_rel = full_output.get("price_relationship", {})

        ema_val = to_native(current.get("value"))
        price_distance = to_native(price_rel.get("distance_pct"))

        # Zone based on price position relative to EMA
        position = price_rel.get("position", "unknown")
        zone = "above" if position == "above" else "below" if position == "below" else "neutral"

        # Trend from analysis
        consensus = trend_data.get("consensus", "mixed")
        trend = "bullish" if consensus == "bullish" else "bearish" if consensus == "bearish" else "neutral"

        return {
            "indicator": "ema",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(ema_val, 4) if ema_val else None,
            "value_secondary": round(price_distance, 2) if price_distance else None,
            "value_tertiary": None,
            "velocity": round(to_native(trend_data.get("momentum", 0)) or 0, 4),
            "rank": 0.5,
            "zone": zone,
            "zone_periods": 0,
            "trend": trend,
            "crossover_type": None,
            "crossover_periods_ago": None,
            "patterns": [],
            "analysis": full_output.get("summary", "")
        }