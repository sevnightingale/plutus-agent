"""
SMA (Simple Moving Average) Preprocessor.

Advanced SMA preprocessing with trend analysis, support/resistance detection,
and multi-timeframe moving average relationships.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class SMAPreprocessor(BasePreprocessor):
    """Advanced SMA preprocessor with professional-grade trend analysis."""
    
    def preprocess(self, sma: pd.Series, prices: pd.Series = None, 
                  length: int = 20, **kwargs) -> Dict[str, Any]:
        """
        Advanced SMA preprocessing with comprehensive trend analysis.
        
        SMA is a lagging indicator that smooths price action to identify trend direction.
        It acts as dynamic support/resistance and trend confirmation.
        
        Args:
            sma: SMA values
            prices: Price series for position analysis (optional)
            length: SMA calculation period
            
        Returns:
            Dictionary with comprehensive SMA analysis
        """
        # Clean and align data
        sma = pd.to_numeric(sma, errors="coerce").dropna()
        prices_clean = None if prices is None else pd.to_numeric(prices, errors="coerce").dropna()

        if len(sma) < 5:
            return {"error": "Insufficient data for SMA analysis"}

        # Align data if prices provided
        if prices_clean is not None:
            df = pd.concat({"sma": sma, "price": prices_clean}, axis=1, join="inner").dropna()
            sma_c = df["sma"]
            px_c = df["price"] if "price" in df and len(df["price"]) > 0 else None
        else:
            sma_c = sma
            px_c = None

        # Get timestamp from series index or use UTC
        ts = (sma_c.index[-1].isoformat()
              if is_datetime64_any_dtype(sma_c.index)
              else datetime.now(timezone.utc).isoformat())

        # Safe denominator function
        def _den(x: float) -> float:
            return max(1e-12, abs(float(x)))

        # Length-driven windows
        short_win = max(3, length // 6)
        medium_win = max(5, length // 3)
        long_win = max(10, length)
        slope_short = max(2, length // 6)
        slope_medium = max(3, length // 4)
        slope_long = max(5, length // 2)
        crossover_win = min(length, 20)
        support_win = min(length, len(px_c) if px_c is not None else len(sma_c))

        cur_sma = float(sma_c.iloc[-1])
        cur_px = float(px_c.iloc[-1]) if px_c is not None and len(px_c) > 0 else None
        
        # Trend analysis
        trend_analysis = self._analyze_sma_trend(sma_c, short_win, medium_win, long_win)

        # Price-SMA relationship
        price_relationship = {}
        if px_c is not None:
            price_relationship = self._analyze_price_sma_relationship(px_c, sma_c, _den)

        # Support/Resistance analysis
        support_resistance = self._analyze_support_resistance(sma_c, px_c, support_win, _den)

        # Slope analysis
        slope_analysis = self._analyze_sma_slope(sma_c, slope_short, slope_medium, slope_long)

        # Crossover analysis
        crossover_analysis = {}
        if px_c is not None:
            crossover_analysis = self._analyze_price_sma_crossovers(px_c, sma_c, crossover_win, _den)

        # Moving average quality
        quality_analysis = self._analyze_ma_quality(sma_c, _den)
        
        return {
            "indicator": "SMA",
            "current": {
                "sma_value": round(cur_sma, 4),
                "price": round(cur_px, 4) if cur_px is not None else None,
                "price_distance": round(cur_px - cur_sma, 4) if cur_px is not None else None,
                "price_distance_pct": round(((cur_px - cur_sma) / _den(cur_sma)) * 100, 3) if cur_px is not None else None,
                "timestamp": ts
            },
            "context": {
                "trend": trend_analysis,
                "slope": slope_analysis,
                "quality": quality_analysis,
                "length": length,
                "smoothing_factor": round(2.0 / (length + 1), 4)
            },
            "levels": {
                "price_relationship": price_relationship,
                "support_resistance": support_resistance,
                "current_level": round(cur_sma, 4),
                "trend_direction": trend_analysis.get("consensus", "mixed")
            },
            "patterns": {
                "crossovers": crossover_analysis,
                "trend_alignment": slope_analysis.get("alignment", "mixed"),
                "slope_direction": slope_analysis.get("direction", "flat")
            },
            "evidence": {
                "data_quality": {
                    "original_periods": len(sma),
                    "aligned_periods": len(sma_c),
                    "valid_data_percentage": round(len(sma_c) / len(sma) * 100, 1),
                    "had_prices": px_c is not None,
                    "calculation_periods": length
                },
                "calculation_notes": f"SMA analysis based on {len(sma_c)} periods with length={length}"
            },
            "summary": self._generate_sma_summary(cur_sma, cur_px, trend_analysis, price_relationship,
                                               crossover_analysis, support_resistance)
        }
    
    def _analyze_sma_trend(self, sma: pd.Series, short_win: int = 3, medium_win: int = 8, long_win: int = 15) -> Dict[str, Any]:
        """Analyze SMA trend characteristics."""
        current_sma = sma.iloc[-1]
        
        # Define safe denominator function for this method
        def _den(x: float) -> float:
            return max(1e-12, abs(float(x)))

        # Short, medium, long term trends
        short_trend = self._calculate_ma_trend_direction(sma, short_win, _den)
        medium_trend = self._calculate_ma_trend_direction(sma, medium_win, _den)
        long_trend = self._calculate_ma_trend_direction(sma, long_win, _den) if len(sma) >= long_win else "insufficient_data"
        
        # Trend consistency
        trend_consistency = self._calculate_ma_trend_consistency(sma)
        
        # Trend strength (based on slope steepness)
        slope = self._calculate_velocity(sma, 5)
        trend_strength = min(1.0, abs(slope) / (sma.std() * 0.1)) if sma.std() > 0 else 0
        
        # Overall trend consensus
        trends = [t for t in [short_trend, medium_trend, long_trend] if t not in ["insufficient_data", "sideways"]]
        if trends:
            bullish_count = sum(1 for t in trends if t == "bullish")
            bearish_count = sum(1 for t in trends if t == "bearish")
            
            if bullish_count > bearish_count:
                consensus = "bullish"
            elif bearish_count > bullish_count:
                consensus = "bearish"
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
            "slope": round(slope, 6)
        }
    
    def _calculate_ma_trend_direction(self, ma: pd.Series, periods: int, _den) -> str:
        """Calculate trend direction over specified periods."""
        if len(ma) < periods + 1:
            return "insufficient_data"
        
        start_value = ma.iloc[-(periods + 1)]
        end_value = ma.iloc[-1]
        
        change_pct = ((end_value - start_value) / _den(start_value)) * 100
        
        if change_pct > 0.2:
            return "bullish"
        elif change_pct < -0.2:
            return "bearish"
        else:
            return "sideways"
    
    def _calculate_ma_trend_consistency(self, ma: pd.Series) -> float:
        """Calculate consistency of moving average trend."""
        if len(ma) < 10:
            return 0.5
        
        # Look at direction changes over recent periods
        recent_ma = ma.iloc[-10:]
        changes = recent_ma.diff().dropna()
        
        if len(changes) == 0:
            return 0.5
        
        positive_changes = sum(1 for x in changes if x > 0)
        negative_changes = sum(1 for x in changes if x < 0)
        total_changes = len(changes)
        
        # Consistency is when most changes go in same direction
        max_directional = max(positive_changes, negative_changes)
        return max_directional / total_changes if total_changes > 0 else 0.5
    
    def _analyze_price_sma_relationship(self, prices: pd.Series, sma: pd.Series, _den) -> Dict[str, Any]:
        """Analyze price position relative to SMA."""
        current_price = prices.iloc[-1]
        current_sma = sma.iloc[-1]
        
        # Current position
        if current_price > current_sma:
            position = "above"
        elif current_price < current_sma:
            position = "below"
        else:
            position = "at_level"
        
        # Distance analysis
        distance = current_price - current_sma
        distance_pct = (distance / _den(current_sma)) * 100
        
        # Vectorized position analysis
        mask_above = (prices > sma)
        above_periods = mask_above.sum()
        below_periods = len(prices) - above_periods
        total_periods = len(prices)

        # Position changes using vectorized operations (exclude first NaN comparison)
        position_changes = mask_above.ne(mask_above.shift(1)).iloc[1:].sum()
        
        return {
            "position": position,
            "distance": round(distance, 4),
            "distance_pct": round(distance_pct, 3),
            "above_sma_pct": round((above_periods / total_periods) * 100, 1),
            "below_sma_pct": round((below_periods / total_periods) * 100, 1),
            "position_changes": position_changes,
            "position_stability": round(1 - position_changes / max(1, len(mask_above) - 1), 3)
        }
    
    def _analyze_support_resistance(self, sma: pd.Series, prices: pd.Series = None, support_win: int = 20, _den=None) -> Dict[str, Any]:
        """Analyze SMA as dynamic support/resistance."""
        if prices is None:
            return {"no_price_data": True}
        
        # Find touches and bounces off SMA
        touches = []
        bounces = []
        
        # Define touch as price within 0.5% of SMA
        touch_threshold = 0.005
        
        for i in range(1, len(prices)):
            price = prices.iloc[i]
            sma_val = sma.iloc[i]
            prev_price = prices.iloc[i-1]
            
            # Check if price touched SMA
            if abs(price - sma_val) / _den(sma_val) <= touch_threshold:
                touches.append({
                    "index": i,
                    "periods_ago": len(prices) - 1 - i,
                    "price": price,
                    "sma_value": sma_val
                })
                
                # Check for bounce (reversal after touch)
                if i < len(prices) - 2:
                    next_price = prices.iloc[i+1]
                    
                    # Support bounce (price was below, touched, then moved up)
                    if prev_price < sma_val and next_price > price:
                        bounces.append({
                            "type": "support_bounce",
                            "index": i,
                            "periods_ago": len(prices) - 1 - i,
                            "strength": abs(next_price - price) / _den(price)
                        })
                    
                    # Resistance bounce (price was above, touched, then moved down)
                    elif prev_price > sma_val and next_price < price:
                        bounces.append({
                            "type": "resistance_bounce",
                            "index": i,
                            "periods_ago": len(prices) - 1 - i,
                            "strength": abs(price - next_price) / _den(price)
                        })
        
        # Calculate success rate
        total_touches = len(touches)
        successful_bounces = len(bounces)
        success_rate = (successful_bounces / total_touches) if total_touches > 0 else 0
        
        return {
            "total_touches": total_touches,
            "successful_bounces": successful_bounces,
            "success_rate": round(success_rate, 3),
            "recent_touches": touches[-5:] if touches else [],
            "recent_bounces": bounces[-3:] if bounces else [],
            "effectiveness": "high" if success_rate > 0.6 else "medium" if success_rate > 0.3 else "low"
        }
    
    def _analyze_sma_slope(self, sma: pd.Series, slope_short: int = 3, slope_medium: int = 5, slope_long: int = 10) -> Dict[str, Any]:
        """Analyze SMA slope characteristics."""
        if len(sma) < 5:
            return {}
        
        # Calculate slope over different periods
        slope_3 = self._calculate_velocity(sma, slope_short)
        slope_5 = self._calculate_velocity(sma, slope_medium)
        slope_10 = self._calculate_velocity(sma, slope_long) if len(sma) >= slope_long else None
        
        # Acceleration
        acceleration = self._calculate_acceleration(sma, 5)
        
        # Slope classification
        if slope_5 > 0.001:
            slope_direction = "upward"
        elif slope_5 < -0.001:
            slope_direction = "downward"
        else:
            slope_direction = "flat"
        
        # Slope consistency (are short and long term slopes aligned?)
        slope_alignment = "aligned" if (slope_10 is not None) and (slope_5 * slope_10 > 0) else "mixed"
        
        return {
            "short_term_slope": round(slope_3, 6),
            "medium_term_slope": round(slope_5, 6),
            "long_term_slope": (None if slope_10 is None else round(slope_10, 6)),
            "acceleration": round(acceleration, 6),
            "direction": slope_direction,
            "alignment": slope_alignment
        }
    
    def _analyze_price_sma_crossovers(self, prices: pd.Series, sma: pd.Series, crossover_win: int = 20, _den=None) -> Dict[str, Any]:
        """Analyze price crossovers with SMA."""
        crossovers = []
        
        for i in range(1, min(crossover_win, len(prices))):
            prev_price = prices.iloc[-(i+1)]
            curr_price = prices.iloc[-i]
            prev_sma = sma.iloc[-(i+1)]
            curr_sma = sma.iloc[-i]
            
            # Bullish crossover (price crosses above SMA)
            if prev_price <= prev_sma and curr_price > curr_sma:
                crossovers.append({
                    "type": "bullish_crossover",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "sma_value": round(curr_sma, 4),
                    "strength": round(abs(curr_price - curr_sma) / _den(curr_sma), 3)
                })
            
            # Bearish crossover (price crosses below SMA)
            elif prev_price >= prev_sma and curr_price < curr_sma:
                crossovers.append({
                    "type": "bearish_crossover",
                    "periods_ago": i,
                    "price": round(curr_price, 4),
                    "sma_value": round(curr_sma, 4),
                    "strength": round(abs(curr_price - curr_sma) / _den(curr_sma), 3)
                })
        
        return {
            "recent_crossovers": crossovers[:5],
            "latest_crossover": crossovers[0] if crossovers else None,
            "crossover_frequency": len(crossovers) / max(1, min(crossover_win, len(prices)) - 1)
        }
    
    def _analyze_ma_quality(self, sma: pd.Series, _den) -> Dict[str, Any]:
        """Analyze quality characteristics of the moving average."""
        if len(sma) < 10:
            return {}
        
        # Smoothness (lower volatility = smoother)
        sma_mean = float(sma.mean())
        base = _den(sma_mean)
        smoothness = float(np.clip(1 - (sma.std() / base), 0, 1))
        
        # Responsiveness (how quickly it changes)
        changes = sma.diff().dropna()
        responsiveness = float(np.clip(changes.abs().mean() / (base * 0.01), 0, 1))
        
        # Trend clarity (consistent direction)
        direction_changes = sum(
            (np.sign(changes).shift(-1).fillna(0) != np.sign(changes)) & (np.sign(changes) != 0)
        )
        trend_clarity = float(np.clip(1 - direction_changes / max(1, len(changes)), 0, 1))
        
        return {
            "smoothness": round(smoothness, 3),
            "responsiveness": round(responsiveness, 3),
            "trend_clarity": round(trend_clarity, 3),
            "overall_quality": round((smoothness + trend_clarity) / 2, 3)
        }
    
    def _generate_sma_signals(self, sma_value: float, price: Optional[float], 
                             trend_analysis: Dict, price_relationship: Dict, 
                             crossover_analysis: Dict) -> List[Dict[str, Any]]:
        """Generate SMA trading signals."""
        signals = []
        
        # Trend-based signals
        consensus = trend_analysis.get("consensus", "mixed")
        trend_strength = trend_analysis.get("strength", 0)
        
        if consensus == "bullish" and trend_strength > 0.5:
            signals.append({
                "type": "trend_following_buy",
                "strength": "medium",
                "reason": f"Strong bullish SMA trend (strength: {trend_strength:.2f})",
                "confidence": 0.6 + (trend_strength * 0.2)
            })
        elif consensus == "bearish" and trend_strength > 0.5:
            signals.append({
                "type": "trend_following_sell",
                "strength": "medium",
                "reason": f"Strong bearish SMA trend (strength: {trend_strength:.2f})",
                "confidence": 0.6 + (trend_strength * 0.2)
            })
        
        # Price position signals
        if (price is not None) and price_relationship:
            position = price_relationship.get("position")
            distance_pct = abs(price_relationship.get("distance_pct", 0))
            
            if position == "above" and distance_pct > 3:
                signals.append({
                    "type": "extended_above_sma",
                    "strength": "low",
                    "reason": f"Price {distance_pct:.1f}% above SMA - potential pullback",
                    "confidence": 0.5
                })
            elif position == "below" and distance_pct > 3:
                signals.append({
                    "type": "extended_below_sma",
                    "strength": "low",
                    "reason": f"Price {distance_pct:.1f}% below SMA - potential bounce",
                    "confidence": 0.5
                })
        
        # Crossover signals
        if crossover_analysis:
            latest_crossover = crossover_analysis.get("latest_crossover")
            if latest_crossover and latest_crossover["periods_ago"] <= 3:
                crossover_type = latest_crossover["type"]
                signal_type = "buy_signal" if "bullish" in crossover_type else "sell_signal"
                
                signals.append({
                    "type": signal_type,
                    "strength": "medium",
                    "reason": f"Recent price {crossover_type.replace('_', ' ')} SMA",
                    "confidence": 0.7
                })
        
        return signals
    
    def _calculate_sma_confidence(self, sma: pd.Series, trend_analysis: Dict, quality_analysis: Dict) -> float:
        """Calculate SMA analysis confidence."""
        confidence_factors = []
        
        # Data quantity factor
        data_factor = min(1.0, len(sma) / 30)
        confidence_factors.append(data_factor)
        
        # Trend consistency factor
        trend_consistency = trend_analysis.get("consistency", 0.5)
        confidence_factors.append(trend_consistency)
        
        # Quality factor
        if quality_analysis:
            overall_quality = quality_analysis.get("overall_quality", 0.5)
            confidence_factors.append(overall_quality)
        else:
            confidence_factors.append(0.6)
        
        # Trend strength factor
        trend_strength = trend_analysis.get("strength", 0.5)
        confidence_factors.append(trend_strength)
        
        return round(np.mean(confidence_factors), 3)
    
    def _generate_sma_summary(self, sma_value: float, price: Optional[float],
                             trend_analysis: Dict, price_relationship: Dict,
                             crossover_analysis: Dict = None, support_resistance: Dict = None) -> str:
        """Generate human-readable SMA summary with enriched signals."""
        consensus = trend_analysis.get("consensus", "mixed")
        trend_strength = trend_analysis.get("strength", 0)

        summary = f"SMA={sma_value:.4f} ({consensus})"

        # Price position relative to SMA
        if (price is not None) and price_relationship:
            position = price_relationship.get("position", "unknown")
            distance_pct = price_relationship.get("distance_pct", 0)
            if position in ["above", "below"]:
                summary += f", price {position} by {abs(distance_pct):.1f}%"

        # Trend strength
        if trend_strength > 0.6:
            summary += ". Strong trend"

        # Recent crossover - important signal
        if crossover_analysis:
            latest_cross = crossover_analysis.get("latest_crossover")
            if latest_cross and latest_cross.get("periods_ago", 99) <= 5:
                cross_type = "bullish" if "bullish" in latest_cross.get("type", "") else "bearish"
                summary += f". ✓ {cross_type.capitalize()} crossover {latest_cross['periods_ago']}p ago"

        # Acceleration
        slope_analysis = trend_analysis.get("slope", 0)
        if abs(slope_analysis) > 0.001:
            if slope_analysis > 0:
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
        """Convert full SMA output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "sma", "timeframe": timeframe, "timestamp": None,
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

        sma_val = to_native(current.get("value"))
        price_distance = to_native(price_rel.get("distance_pct"))

        # Zone based on price position relative to SMA
        position = price_rel.get("position", "unknown")
        zone = "above" if position == "above" else "below" if position == "below" else "neutral"

        # Trend from analysis
        consensus = trend_data.get("consensus", "mixed")
        trend = "bullish" if consensus == "bullish" else "bearish" if consensus == "bearish" else "neutral"

        return {
            "indicator": "sma",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(sma_val, 4) if sma_val else None,
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