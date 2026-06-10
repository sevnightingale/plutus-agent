"""
ATR (Average True Range) Preprocessor.

Advanced ATR preprocessing with volatility analysis, trend strength assessment,
and stop-loss level recommendations based on market volatility.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class ATRPreprocessor(BasePreprocessor):
    """Advanced ATR preprocessor with professional-grade volatility analysis."""
    
    def preprocess(self, atr: pd.Series, prices: pd.Series = None, 
                  length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced ATR preprocessing with comprehensive volatility analysis.
        
        ATR measures market volatility by calculating the average of true ranges
        over a specified period. Higher values indicate higher volatility.
        
        Args:
            atr: ATR values
            prices: Price series for additional analysis (optional)
            length: ATR calculation period
            
        Returns:
            Dictionary with comprehensive ATR analysis
        """
        atr_clean = atr.dropna()
        if len(atr_clean) < 5:
            return {"error": "Insufficient data for ATR analysis"}
        current_atr = float(atr_clean.iloc[-1])

        # Align with price only if provided
        price_aligned = None
        atr_aligned = atr_clean
        if prices is not None:
            df = pd.DataFrame({"atr": atr, "price": prices}).dropna()
            if len(df) >= 5:
                atr_aligned = df["atr"]
                price_aligned = df["price"]

        volatility_analysis = self._analyze_volatility_levels(atr_aligned, price_aligned)
        atr_trend_analysis = self._analyze_atr_trend(atr_clean)
        cycle_analysis = self._analyze_volatility_cycles(atr_clean)
        relative_analysis = self._analyze_relative_volatility(atr_clean)
        stop_loss_analysis = self._analyze_stop_loss_levels(atr_aligned, price_aligned) if price_aligned is not None else {}
        breakout_analysis = self._analyze_breakout_potential(atr_clean)

        # analysis-only evidence (no trading confidence)
        std = atr_clean.std() + 1e-12
        evidence = {
            "clarity": round(min(1.0, abs(current_atr - atr_clean.mean()) / std), 3),
            "consistency": round(min(1.0, abs(self._calculate_velocity(atr_clean, 3)) / std), 3),
            "data_quality": round(min(1.0, len(atr_clean) / 200.0), 3),
        }

        return {
            "indicator": "ATR",
            "current": {
                "value": round(current_atr, 6),
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            "context": {
                "trend": atr_trend_analysis,
                "relative": relative_analysis,
                "breakout": breakout_analysis
            },
            "levels": {
                "volatility": volatility_analysis,
                "cycles": cycle_analysis,
                "stop_loss": stop_loss_analysis
            },
            "evidence": evidence,
            "summary": self._generate_atr_summary(current_atr, volatility_analysis, atr_trend_analysis, breakout_analysis)
        }
    
    def _analyze_volatility_levels(self, atr: pd.Series, prices: pd.Series = None) -> Dict[str, Any]:
        """Analyze current volatility levels."""
        clean = atr.dropna()
        current_atr = float(clean.iloc[-1])
        mean_atr = float(clean.mean())
        std_atr = float(clean.std())
        max_atr = float(clean.max())
        min_atr = float(clean.min())

        percentile_rank = self._calculate_position_rank(clean, lookback=len(clean))

        # levels
        if current_atr > mean_atr + 2*std_atr: level = "extremely_high"
        elif current_atr > mean_atr + std_atr: level = "high"
        elif current_atr > mean_atr:           level = "above_average"
        elif current_atr > mean_atr - std_atr: level = "below_average"
        else:                                   level = "low"

        rel_price = None
        if prices is not None:
            rel_df = pd.DataFrame({"atr": atr, "price": prices}).dropna()
            if len(rel_df):
                cp = float(rel_df["price"].iloc[-1])
                rel_price = (current_atr / (cp if abs(cp) > 1e-12 else 1e-12)) * 100

        denom = mean_atr if abs(mean_atr) > 1e-12 else 1e-12
        return {
            "current_level": level,
            "percentile_rank": round(percentile_rank, 1),
            "relative_to_mean": round((current_atr / denom - 1) * 100, 2),
            "relative_to_price_pct": round(rel_price, 3) if rel_price is not None else None,
            "statistical": {"mean": round(mean_atr, 6), "std": round(std_atr, 6),
                            "max": round(max_atr, 6), "min": round(min_atr, 6)},
        }
    
    def _analyze_atr_trend(self, atr: pd.Series) -> Dict[str, Any]:
        """Analyze ATR trend characteristics."""
        clean = atr.dropna()
        if len(clean) < 5: 
            return {}
        vel = self._calculate_velocity(clean, 3)
        acc = self._calculate_acceleration(clean, 6)
        direction = "rising" if vel > 0 else "falling" if vel < 0 else "stable"
        std = clean.std()
        strength = min(1.0, abs(vel) / (std + 1e-12))
        recent = clean.iloc[-5:] if len(clean) >= 5 else clean
        consistency = self._calculate_trend_consistency(recent)
        return {"direction": direction, "velocity": round(vel, 6), "acceleration": round(acc, 6),
                "strength": round(strength, 3), "consistency": round(consistency, 3),
                "interpretation": self._interpret_atr_trend(direction, strength, consistency)}
    
    def _calculate_trend_consistency(self, values: pd.Series) -> float:
        """Calculate consistency of ATR trend."""
        if len(values) < 3:
            return 0.5
        
        changes = values.diff().dropna()
        if len(changes) == 0:
            return 0.5
        
        positive_changes = sum(1 for x in changes if x > 0)
        negative_changes = sum(1 for x in changes if x < 0)
        total_changes = len(changes)
        
        # Return consistency ratio
        max_directional = max(positive_changes, negative_changes)
        return max_directional / total_changes if total_changes > 0 else 0.5
    
    def _interpret_atr_trend(self, direction: str, strength: float, consistency: float) -> str:
        """Interpret ATR trend characteristics."""
        if direction == "rising" and strength > 0.7 and consistency > 0.7:
            return "volatility_expanding_strongly"
        elif direction == "rising" and strength > 0.4:
            return "volatility_expanding"
        elif direction == "falling" and strength > 0.7 and consistency > 0.7:
            return "volatility_contracting_strongly"
        elif direction == "falling" and strength > 0.4:
            return "volatility_contracting"
        else:
            return "volatility_stable"
    
    def _analyze_volatility_cycles(self, atr: pd.Series) -> Dict[str, Any]:
        """Analyze volatility cycles and patterns."""
        clean = atr.dropna()
        if len(clean) < 20:
            return {"insufficient_data": True}
        
        # Find volatility peaks and troughs - base scales by std, pass unitless factor
        peaks = self._find_peaks(clean, prominence=0.5)
        troughs = self._find_troughs(clean, prominence=0.5)
        
        # Cycle analysis
        cycle_detected = len(peaks) >= 2 or len(troughs) >= 2
        
        analysis = {"cycle_detected": cycle_detected}
        
        if cycle_detected:
            # Calculate average cycle length
            if len(peaks) >= 2:
                peak_distances = [peaks[i]["index"] - peaks[i-1]["index"] for i in range(1, len(peaks))]
                avg_peak_cycle = np.mean(peak_distances) if peak_distances else None
                analysis["avg_expansion_cycle"] = round(avg_peak_cycle, 1) if avg_peak_cycle else None
            
            if len(troughs) >= 2:
                trough_distances = [troughs[i]["index"] - troughs[i-1]["index"] for i in range(1, len(troughs))]
                avg_trough_cycle = np.mean(trough_distances) if trough_distances else None
                analysis["avg_contraction_cycle"] = round(avg_trough_cycle, 1) if avg_trough_cycle else None
            
            analysis["recent_peaks"] = len(peaks)
            analysis["recent_troughs"] = len(troughs)
            
            # Current cycle position
            current_atr = atr.iloc[-1]
            recent_peak = peaks[-1] if peaks else None
            recent_trough = troughs[-1] if troughs else None
            
            if recent_peak and recent_trough:
                if recent_peak["index"] > recent_trough["index"]:
                    # Most recent extreme was a peak
                    analysis["cycle_position"] = "post_peak_contraction"
                else:
                    # Most recent extreme was a trough  
                    analysis["cycle_position"] = "post_trough_expansion"
            
        return analysis
    
    def _analyze_relative_volatility(self, atr: pd.Series) -> Dict[str, Any]:
        """Analyze ATR relative to its own history."""
        clean = atr.dropna()
        if len(clean) < 10:
            return {}
        current_atr = float(clean.iloc[-1])
        periods = [p for p in (5, 10, 20, 50) if p <= len(clean)]
        comps = {}
        for p in periods:
            mean_p = float(clean.iloc[-p:].mean())
            denom = mean_p if abs(mean_p) > 1e-12 else 1e-12
            comps[f"{p}p_avg"] = round((current_atr / denom - 1) * 100, 2)
        lt_mean = float(clean.iloc[-min(50, len(clean)):].mean())
        denom = lt_mean if abs(lt_mean) > 1e-12 else 1e-12
        regime_ratio = current_atr / denom
        
        if regime_ratio > 1.5:
            regime = "high_volatility"
        elif regime_ratio > 1.2:
            regime = "elevated_volatility"
        elif regime_ratio < 0.5:
            regime = "low_volatility"
        elif regime_ratio < 0.8:
            regime = "suppressed_volatility"
        else:
            regime = "normal_volatility"
        
        return {
            "comparisons": comps,
            "regime": regime,
            "regime_ratio": round(regime_ratio, 3)
        }
    
    def _analyze_stop_loss_levels(self, atr: pd.Series, prices: pd.Series) -> Dict[str, Any]:
        """Analyze ATR-based stop loss recommendations."""
        df = pd.DataFrame({"atr": atr, "price": prices}).dropna()
        if len(df) == 0: 
            return {}
        current_atr = float(df["atr"].iloc[-1])
        current_price = float(df["price"].iloc[-1])
        price_denom = max(abs(current_price), 1e-12)
        
        # Multiple ATR multipliers for different strategies
        multipliers = [1.0, 1.5, 2.0, 2.5, 3.0]
        stop_levels = {}
        
        for mult in multipliers:
            stop_distance = current_atr * mult
            long_stop = current_price - stop_distance
            short_stop = current_price + stop_distance
            stop_pct = (stop_distance / price_denom) * 100
            
            stop_levels[f"{mult}x_atr"] = {
                "long_stop": round(long_stop, 6),
                "short_stop": round(short_stop, 6),
                "distance": round(stop_distance, 6),
                "distance_pct": round(stop_pct, 3)
            }
        
        # Recommended multiplier based on volatility regime
        volatility_level = self._get_volatility_level(atr)
        recommended_mult = self._get_recommended_multiplier(volatility_level)
        
        return {
            "current_price": round(current_price, 6),
            "stop_levels": stop_levels,
            "recommended_multiplier": recommended_mult,
            "recommended_stop": stop_levels.get(f"{recommended_mult}x_atr", {})
        }
    
    def _get_volatility_level(self, atr: pd.Series) -> str:
        """Get current volatility level classification."""
        current_atr = atr.iloc[-1]
        mean_atr = atr.mean()
        std_atr = atr.std()
        
        if current_atr > mean_atr + std_atr:
            return "high"
        elif current_atr < mean_atr - std_atr:
            return "low"
        else:
            return "normal"
    
    def _get_recommended_multiplier(self, volatility_level: str) -> float:
        """Get recommended ATR multiplier based on volatility level."""
        recommendations = {
            "low": 1.5,      # Tighter stops in low volatility
            "normal": 2.0,   # Standard stops in normal volatility
            "high": 2.5      # Wider stops in high volatility
        }
        return recommendations.get(volatility_level, 2.0)
    
    def _analyze_breakout_potential(self, atr: pd.Series) -> Dict[str, Any]:
        """Analyze breakout potential based on ATR patterns."""
        clean = atr.dropna()
        if len(clean) < 10: 
            return {}
        current_atr = float(clean.iloc[-1])
        recent = clean.iloc[-5:]
        mean_atr = float(clean.mean())
        std_atr = float(clean.std())
        squeeze_thr = mean_atr - 0.5*std_atr
        squeeze = current_atr < squeeze_thr
        squeeze_periods = 0
        if squeeze:
            for i in range(len(clean)-1, -1, -1):
                if clean.iloc[i] < squeeze_thr: 
                    squeeze_periods += 1
                else: 
                    break
        expansion = min(1.0, squeeze_periods / 10) if squeeze_periods else 0.0
        recent_change = ((recent.iloc[-1] / (recent.iloc[0] if abs(recent.iloc[0]) > 1e-12 else 1e-12)) - 1) * 100 if len(recent) >= 2 else 0.0
        return {"squeeze_detected": squeeze, "squeeze_periods": squeeze_periods,
                "expansion_potential": round(expansion, 3),
                "recent_volatility_change_pct": round(recent_change, 2),
                "breakout_setup": squeeze and squeeze_periods >= 3}
    
    
    def _generate_atr_summary(self, current_atr: float, volatility_analysis: Dict, trend_analysis: Dict,
                             breakout_analysis: Dict = None) -> str:
        """Generate human-readable ATR summary with enriched signals."""
        volatility_level = volatility_analysis["current_level"]
        percentile = volatility_analysis["percentile_rank"]

        summary = f"ATR={current_atr:.6f} ({volatility_level.replace('_', ' ')}, {percentile:.0f}%ile)"

        # Volatility trend
        if trend_analysis:
            interpretation = trend_analysis.get("interpretation", "")
            if "expanding_strongly" in interpretation:
                summary += ". ⚠️ Volatility expanding strongly"
            elif "expanding" in interpretation:
                summary += ". Volatility expanding"
            elif "contracting_strongly" in interpretation:
                summary += ". Volatility contracting strongly"
            elif "contracting" in interpretation:
                summary += ". Volatility contracting"

        # Squeeze detection - important for breakout setups
        if breakout_analysis:
            if breakout_analysis.get("squeeze_detected"):
                periods = breakout_analysis.get("squeeze_periods", 0)
                potential = breakout_analysis.get("expansion_potential", 0)
                if breakout_analysis.get("breakout_setup"):
                    summary += f". ⚠️ SQUEEZE ({periods}p) - breakout setup"
                else:
                    summary += f". Squeeze ({periods}p)"

            # Recent volatility change
            recent_change = breakout_analysis.get("recent_volatility_change_pct", 0)
            if abs(recent_change) > 20:
                if recent_change > 0:
                    summary += f". Volatility spiked +{recent_change:.0f}%"
                else:
                    summary += f". Volatility dropped {recent_change:.0f}%"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full ATR output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "atr", "timeframe": timeframe, "timestamp": None,
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
        volatility = full_output.get("volatility_analysis", {})
        trend_data = full_output.get("trend_analysis", {})
        squeeze = full_output.get("squeeze_analysis", {})

        atr_val = to_native(current.get("atr"))
        atr_pct = to_native(current.get("atr_percent"))

        # Zone based on volatility level
        level = volatility.get("current_level", "normal")
        zone_map = {"very_high": "high_volatility", "high": "high_volatility",
                    "above_normal": "normal", "normal": "normal",
                    "below_normal": "low_volatility", "low": "low_volatility", "very_low": "low_volatility"}
        zone = zone_map.get(level, "normal")

        # Pattern extraction
        pattern_codes = []
        interpretation = trend_data.get("interpretation", "")
        if "expanding" in interpretation: pattern_codes.append("volatility_expanding")
        elif "contracting" in interpretation: pattern_codes.append("volatility_contracting")
        if squeeze.get("squeeze_detected"): pattern_codes.append("squeeze_active")
        if squeeze.get("breakout_setup"): pattern_codes.append("squeeze_firing")

        return {
            "indicator": "atr",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(atr_val, 6) if atr_val else None,
            "value_secondary": round(atr_pct, 4) if atr_pct else None,
            "value_tertiary": None,
            "velocity": round(to_native(trend_data.get("change_rate", 0)) or 0, 4),
            "rank": round(to_native(volatility.get("percentile_rank", 50)) / 100, 3),
            "zone": zone,
            "zone_periods": to_native(squeeze.get("squeeze_periods", 0)) or 0,
            "trend": "expanding" if "expanding" in interpretation else "contracting" if "contracting" in interpretation else "stable",
            "crossover_type": None,
            "crossover_periods_ago": None,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }