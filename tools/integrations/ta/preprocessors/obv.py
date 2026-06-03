"""
OBV (On-Balance Volume) Preprocessor.

Advanced OBV preprocessing with volume flow analysis, divergence detection,
and accumulation/distribution pattern recognition.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class OBVPreprocessor(BasePreprocessor):
    """Advanced OBV preprocessor with professional-grade volume analysis."""
    
    def preprocess(self, obv: pd.Series, prices: pd.Series = None, volumes: pd.Series = None,
                  length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced OBV preprocessing with comprehensive volume flow analysis.
        
        OBV tracks volume flow by adding volume on up days and subtracting
        volume on down days. It helps identify accumulation/distribution patterns.
        
        Args:
            obv: OBV values  
            prices: Price series for divergence analysis (optional)
            volumes: Volume series for additional analysis (optional)
            
        Returns:
            Dictionary with comprehensive OBV analysis
        """
        # Capture original lengths
        orig_obv_len = len(obv)
        orig_prices_len = len(prices) if prices is not None else 0
        orig_volumes_len = len(volumes) if volumes is not None else 0

        # Clean data
        obv = pd.to_numeric(obv, errors="coerce").dropna()
        prices = None if prices is None else pd.to_numeric(prices, errors="coerce").dropna()
        volumes = None if volumes is None else pd.to_numeric(volumes, errors="coerce").dropna()

        if len(obv) < 5:
            return {"error": "Insufficient data for OBV analysis"}

        current_obv = float(obv.iloc[-1])

        # Generate proper timestamp
        def _ts_from(s: pd.Series):
            idx = s.index
            return (idx[-1].isoformat()
                    if is_datetime64_any_dtype(idx)
                    else datetime.now(timezone.utc).isoformat())

        timestamp = _ts_from(obv)
        
        # Analysis using length parameter
        trend_analysis = self._analyze_obv_trend(obv, length)
        flow_analysis = self._analyze_volume_flow(obv, volumes) if volumes is not None else {}
        accumulation_analysis = self._analyze_accumulation_distribution(obv, length)
        momentum_analysis = self._analyze_obv_momentum(obv, length)
        divergence = self._detect_obv_price_divergence(obv, prices, length) if prices is not None else None
        pattern_analysis = self._analyze_obv_patterns(obv, length)
        relative_analysis = self._analyze_relative_obv(obv)
        
        return {
            "indicator": "OBV",
            "current": {
                "value": round(current_obv, 2),
                "timestamp": timestamp
            },
            "context": {
                "length": length,
                "relative": relative_analysis
            },
            "levels": {
                "trend": trend_analysis,
                "accumulation": accumulation_analysis
            },
            "patterns": {
                "momentum": momentum_analysis,
                "formations": pattern_analysis,
                "divergence": divergence,
                "flow": flow_analysis
            },
            "evidence": {
                "data_quality": {
                    "original_periods": {
                        "obv": orig_obv_len,
                        "prices": orig_prices_len,
                        "volumes": orig_volumes_len
                    },
                    "cleaned_periods": len(obv),
                    "had_prices": prices is not None,
                    "had_volumes": volumes is not None
                },
                "calculation_notes": f"OBV analysis based on {len(obv)} periods with length {length}"
            },
            "summary": self._generate_obv_summary(current_obv, trend_analysis, accumulation_analysis,
                                                divergence, pattern_analysis)
        }
    
    def _analyze_obv_trend(self, obv: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze OBV trend characteristics."""
        current_obv = obv.iloc[-1]
        
        # Length-driven trend windows
        short = max(3, length // 4)
        medium = max(5, length // 2)
        long = max(10, length)

        short_trend = self._calculate_trend_direction(obv, short)
        medium_trend = self._calculate_trend_direction(obv, medium)
        long_trend = self._calculate_trend_direction(obv, long) if len(obv) >= long else "insufficient_data"
        
        # Overall trend consensus
        trends = [t for t in [short_trend, medium_trend, long_trend] if t != "insufficient_data"]
        if trends:
            # Count bullish vs bearish
            bullish_count = sum(1 for t in trends if t == "bullish")
            bearish_count = sum(1 for t in trends if t == "bearish")
            
            if bullish_count > bearish_count:
                consensus = "bullish"
            elif bearish_count > bullish_count:
                consensus = "bearish"
            else:
                consensus = "mixed"
        else:
            consensus = "insufficient_data"
        
        # Trend strength with safe denominator
        velocity = self._calculate_velocity(obv, 5)
        obv_std = float(obv.std())
        base = max(1e-12, obv_std * 0.1)
        trend_strength = min(1.0, abs(velocity) / base)
        
        # Trend consistency
        consistency = self._calculate_obv_trend_consistency(obv)
        
        return {
            "short_term": short_trend,
            "medium_term": medium_trend,
            "long_term": long_trend,
            "consensus": consensus,
            "velocity": round(velocity, 2),
            "strength": round(trend_strength, 3),
            "consistency": round(consistency, 3)
        }
    
    def _calculate_trend_direction(self, obv: pd.Series, periods: int) -> str:
        """Calculate trend direction over specified periods."""
        if len(obv) < periods:
            return "insufficient_data"
        
        start_value = obv.iloc[-periods]
        end_value = obv.iloc[-1]
        
        change_pct = ((end_value - start_value) / abs(start_value)) * 100 if start_value != 0 else 0
        
        if change_pct > 1:
            return "bullish"
        elif change_pct < -1:
            return "bearish"
        else:
            return "sideways"
    
    def _calculate_obv_trend_consistency(self, obv: pd.Series) -> float:
        """Calculate consistency of OBV trend."""
        if len(obv) < 10:
            return 0.5
        
        # Look at direction changes
        recent_obv = obv.iloc[-10:]
        changes = recent_obv.diff().dropna()
        
        if len(changes) == 0:
            return 0.5
        
        positive_changes = sum(1 for x in changes if x > 0)
        negative_changes = sum(1 for x in changes if x < 0)
        total_changes = len(changes)
        
        # Consistency is when most changes go in same direction
        max_directional = max(positive_changes, negative_changes)
        return max_directional / total_changes if total_changes > 0 else 0.5
    
    def _analyze_volume_flow(self, obv: pd.Series, volumes: pd.Series) -> Dict[str, Any]:
        """Analyze volume flow characteristics."""
        if len(obv) < 5 or volumes is None or len(volumes) < 5:
            return {}

        # Align OBV and volumes with inner join
        obv_v = pd.concat({"obv": obv, "vol": volumes}, axis=1, join="inner").dropna()
        if len(obv_v) < 5:
            return {}

        # Recent volume flow using aligned data
        recent_obv_change = float(obv_v["obv"].iloc[-1] - obv_v["obv"].iloc[-5])
        recent_volume_sum = float(obv_v["vol"].iloc[-5:].sum())
        flow_efficiency = abs(recent_obv_change) / recent_volume_sum if recent_volume_sum > 0 else 0.0

        # Volume-weighted trend
        if recent_obv_change > 0:
            volume_trend = "accumulation"
        elif recent_obv_change < 0:
            volume_trend = "distribution"
        else:
            volume_trend = "neutral"

        # Average volume during up/down moves using aligned data
        chg = obv_v["obv"].diff()
        avg_up_volume = float(obv_v["vol"][chg > 0].mean()) if (chg > 0).any() else 0.0
        avg_down_volume = float(obv_v["vol"][chg < 0].mean()) if (chg < 0).any() else 0.0
        
        return {
            "recent_flow": volume_trend,
            "flow_efficiency": round(flow_efficiency, 6),
            "avg_up_volume": round(avg_up_volume, 2),
            "avg_down_volume": round(avg_down_volume, 2),
            "volume_bias": "up_days" if avg_up_volume > avg_down_volume else ("down_days" if avg_down_volume > avg_up_volume else "balanced"),
            "volume_ratio": round(avg_up_volume / avg_down_volume, 2) if avg_down_volume > 0 else None
        }
    
    def _analyze_accumulation_distribution(self, obv: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze accumulation/distribution patterns."""
        if len(obv) < 10:
            return {}
        
        # Different timeframe analysis
        periods = [5, 10, 20] if len(obv) >= 20 else [p for p in [5, 10] if p <= len(obv)]
        
        accumulation_scores = {}
        for period in periods:
            start_obv = obv.iloc[-period]
            end_obv = obv.iloc[-1]
            
            if start_obv != 0:
                change_pct = ((end_obv - start_obv) / abs(start_obv)) * 100
            else:
                change_pct = 0
            
            if change_pct > 2:
                score = "strong_accumulation"
            elif change_pct > 0.5:
                score = "accumulation"
            elif change_pct < -2:
                score = "strong_distribution"
            elif change_pct < -0.5:
                score = "distribution"
            else:
                score = "neutral"
            
            accumulation_scores[f"{period}p"] = {
                "score": score,
                "change_pct": round(change_pct, 2)
            }
        
        # Overall accumulation assessment
        all_scores = [data["score"] for data in accumulation_scores.values()]
        accumulation_count = sum(1 for s in all_scores if "accumulation" in s)
        distribution_count = sum(1 for s in all_scores if "distribution" in s)
        
        if accumulation_count > distribution_count:
            overall = "accumulation_phase"
        elif distribution_count > accumulation_count:
            overall = "distribution_phase"
        else:
            overall = "neutral_phase"
        
        return {
            "timeframe_analysis": accumulation_scores,
            "overall_phase": overall,
            "phase_strength": self._calculate_phase_strength(accumulation_scores)
        }
    
    def _calculate_phase_strength(self, scores: Dict) -> str:
        """Calculate strength of accumulation/distribution phase."""
        strong_signals = sum(1 for data in scores.values() if "strong" in data["score"])
        total_signals = len(scores)
        
        if strong_signals >= total_signals * 0.7:
            return "strong"
        elif strong_signals >= total_signals * 0.4:
            return "moderate"
        else:
            return "weak"
    
    def _analyze_obv_momentum(self, obv: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze OBV momentum characteristics."""
        if len(obv) < 5:
            return {}
        
        # Length-driven velocity and acceleration windows
        velocity = self._calculate_velocity(obv, max(2, length // 5))
        acceleration = self._calculate_acceleration(obv, max(3, length // 3))
        
        # Momentum classification
        if velocity > 0 and acceleration > 0:
            momentum_type = "accelerating_bullish"
        elif velocity > 0:
            momentum_type = "decelerating_bullish"
        elif velocity < 0 and acceleration < 0:
            momentum_type = "accelerating_bearish"
        elif velocity < 0:
            momentum_type = "decelerating_bearish"
        else:
            momentum_type = "neutral"
        
        # Rate of change
        if len(obv) >= 10:
            roc_5 = ((obv.iloc[-1] / obv.iloc[-6]) - 1) * 100 if obv.iloc[-6] != 0 else 0
            roc_10 = ((obv.iloc[-1] / obv.iloc[-11]) - 1) * 100 if len(obv) >= 11 and obv.iloc[-11] != 0 else 0
        else:
            roc_5 = roc_10 = 0
        
        return {
            "velocity": round(velocity, 2),
            "acceleration": round(acceleration, 2),
            "momentum_type": momentum_type,
            "roc_5p": round(roc_5, 2),
            "roc_10p": round(roc_10, 2)
        }
    
    def _detect_obv_price_divergence(self, obv: pd.Series, prices: pd.Series, length: int) -> Optional[Dict[str, Any]]:
        """Detect OBV-price divergence patterns."""
        if obv is None or prices is None or len(obv) < 15 or len(prices) < 15:
            return None

        # Align OBV and prices
        df = pd.concat({"obv": obv, "px": prices}, axis=1, join="inner").dropna()
        if len(df) < 15:
            return None

        win = min(max(10, length), len(df))
        m_recent = df["obv"].tail(win)
        p_recent = df["px"].tail(win)

        # Calculate relative prominence thresholds
        prom_m = max(1e-6, m_recent.std() * 0.6)
        prom_p = max(1e-6, p_recent.std() * 0.6)

        # Find peaks and troughs with scaled prominence
        obv_peaks = self._find_peaks(m_recent, prominence=prom_m)
        obv_troughs = self._find_troughs(m_recent, prominence=prom_m)
        price_peaks = self._find_peaks(p_recent, prominence=prom_p)
        price_troughs = self._find_troughs(p_recent, prominence=prom_p)
        
        # Bullish divergence: price lower lows, OBV higher lows
        if len(obv_troughs) >= 2 and len(price_troughs) >= 2:
            latest_obv_trough = obv_troughs[-1]
            prev_obv_trough = obv_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]
            
            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_obv_trough["value"] > prev_obv_trough["value"]):
                return {
                    "type": "positive_divergence",
                    "description": "Price making lower lows while OBV making higher lows - accumulation"
                }
        
        # Bearish divergence: price higher highs, OBV lower highs  
        if len(obv_peaks) >= 2 and len(price_peaks) >= 2:
            latest_obv_peak = obv_peaks[-1]
            prev_obv_peak = obv_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]
            
            if (latest_price_peak["value"] > prev_price_peak["value"] and 
                latest_obv_peak["value"] < prev_obv_peak["value"]):
                return {
                    "type": "negative_divergence",
                    "description": "Price making higher highs while OBV making lower highs - distribution"
                }
        
        return None
    
    def _analyze_obv_patterns(self, obv: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze OBV patterns and formations."""
        patterns = {}
        
        if len(obv) >= 15:
            # Breakout patterns
            breakout = self._detect_obv_breakout(obv)
            if breakout:
                patterns["breakout"] = breakout
            
            # Confirmation patterns
            confirmation = self._detect_obv_confirmation(obv)
            if confirmation:
                patterns["confirmation"] = confirmation
        
        return patterns
    
    def _detect_obv_breakout(self, obv: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect OBV breakout patterns."""
        if len(obv) < 20:
            return None
        
        # Look for breakout from recent range with safe math
        recent = obv.iloc[-10:]
        prior = obv.iloc[-20:-10]
        prior_high, prior_low = float(prior.max()), float(prior.min())
        curr = float(obv.iloc[-1])

        # Safe denominators to prevent div-by-zero and sign issues
        den_hi = max(1e-12, abs(prior_high))
        den_lo = max(1e-12, abs(prior_low))

        # Upward breakout
        if curr > max(prior_high, recent.max()):
            return {
                "type": "upward_breakout",
                "strength": round((curr - prior_high) / den_hi * 100, 2),
                "description": "OBV breaking above recent resistance"
            }

        # Downward breakout
        elif curr < min(prior_low, recent.min()):
            return {
                "type": "downward_breakout",
                "strength": round((prior_low - curr) / den_lo * 100, 2),
                "description": "OBV breaking below recent support"
            }
        
        return None
    
    def _detect_obv_confirmation(self, obv: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect OBV trend confirmation patterns."""
        if len(obv) < 10:
            return None
        
        # Check if OBV is making new highs/lows with tolerance
        current_obv = obv.iloc[-1]
        tol = max(1e-12, obv.std() * 0.05)

        # New high confirmation
        if abs(current_obv - obv.max()) <= tol:
            return {
                "type": "new_high_confirmation",
                "description": "OBV making new highs, strong accumulation"
            }

        # New low confirmation
        elif abs(current_obv - obv.min()) <= tol:
            return {
                "type": "new_low_confirmation",
                "description": "OBV making new lows, strong distribution"
            }
        
        return None
    
    def _analyze_relative_obv(self, obv: pd.Series) -> Dict[str, Any]:
        """Analyze OBV relative to its own history."""
        if len(obv) < 20:
            return {}
        
        current_obv = obv.iloc[-1]
        
        # Position within historical range
        max_obv = obv.max()
        min_obv = obv.min()
        
        if max_obv != min_obv:
            position_pct = ((current_obv - min_obv) / (max_obv - min_obv)) * 100
        else:
            position_pct = 50
        
        # Position classification
        if position_pct > 80:
            position = "near_high"
        elif position_pct > 60:
            position = "upper_range"
        elif position_pct > 40:
            position = "middle_range"
        elif position_pct > 20:
            position = "lower_range"
        else:
            position = "near_low"
        
        return {
            "position_percentile": round(position_pct, 1),
            "position": position,
            "max_obv": round(max_obv, 2),
            "min_obv": round(min_obv, 2)
        }
    
    # Signal generation and confidence methods removed to comply with analysis-only philosophy

    def _calculate_obv_confidence(self, obv: pd.Series, trend_analysis: Dict, flow_analysis: Dict) -> float:
        """Calculate OBV analysis confidence."""
        confidence_factors = []
        
        # Data quantity factor
        data_factor = min(1.0, len(obv) / 30)
        confidence_factors.append(data_factor)
        
        # Trend consistency factor
        trend_consistency = trend_analysis.get("consistency", 0.5)
        confidence_factors.append(trend_consistency)
        
        # Trend strength factor
        trend_strength = trend_analysis.get("strength", 0.5)
        confidence_factors.append(trend_strength)
        
        # Volume data availability factor
        if flow_analysis:
            confidence_factors.append(0.8)  # Higher confidence with volume data
        else:
            confidence_factors.append(0.6)  # Lower without volume data
        
        return round(np.mean(confidence_factors), 3)
    
    def _generate_obv_summary(self, current_obv: float, trend_analysis: Dict, accumulation_analysis: Dict,
                             divergence: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable OBV summary with enriched signals."""
        consensus = trend_analysis.get("consensus", "mixed")
        trend_strength = trend_analysis.get("strength", 0)

        summary = f"OBV={current_obv:.0f} ({consensus})"

        if trend_strength > 0.6:
            summary += f" strong trend"

        # Accumulation/distribution phase
        if accumulation_analysis:
            overall_phase = accumulation_analysis.get("overall_phase", "neutral_phase")
            phase_strength = accumulation_analysis.get("phase_strength", "weak")
            if overall_phase == "accumulation_phase":
                if phase_strength == "strong":
                    summary += ". ✓ Strong accumulation"
                else:
                    summary += ". Accumulation"
            elif overall_phase == "distribution_phase":
                if phase_strength == "strong":
                    summary += ". ⚠️ Strong distribution"
                else:
                    summary += ". Distribution"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "positive" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE (accumulation)"
            elif "negative" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE (distribution)"

        # Breakout patterns
        if pattern_analysis:
            if "breakout" in pattern_analysis:
                breakout = pattern_analysis["breakout"]
                if breakout.get("type") == "upward_breakout":
                    summary += ". ✓ OBV breakout above resistance"
                elif breakout.get("type") == "downward_breakout":
                    summary += ". ⚠️ OBV breakdown below support"

            if "confirmation" in pattern_analysis:
                conf = pattern_analysis["confirmation"]
                if conf.get("type") == "new_high_confirmation":
                    summary += ". OBV at new highs"
                elif conf.get("type") == "new_low_confirmation":
                    summary += ". OBV at new lows"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full OBV output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "obv", "timeframe": timeframe, "timestamp": None,
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
        accum = full_output.get("accumulation_analysis", {})
        obv_val = to_native(current.get("value"))
        phase = accum.get("overall_phase", "neutral")
        zone = "accumulation" if "accumulation" in phase else "distribution" if "distribution" in phase else "neutral"
        consensus = trend_data.get("consensus", "mixed")
        trend = "bullish" if consensus == "bullish" else "bearish" if consensus == "bearish" else "neutral"
        patterns = []
        if full_output.get("patterns", {}).get("divergence"):
            div = full_output["patterns"]["divergence"]
            if "positive" in div.get("type", ""): patterns.append("divergence_bullish")
            elif "negative" in div.get("type", ""): patterns.append("divergence_bearish")
        return {"indicator": "obv", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(obv_val), 2) if obv_val else None,
                "value_secondary": None, "value_tertiary": None,
                "velocity": round(to_native(trend_data.get("momentum", 0)) or 0, 4),
                "rank": 0.5, "zone": zone, "zone_periods": 0, "trend": trend,
                "crossover_type": None, "crossover_periods_ago": None,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
