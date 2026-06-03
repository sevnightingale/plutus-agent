"""
Bollinger Band Width Preprocessor.

Advanced Bollinger Band Width preprocessing with volatility analysis,
squeeze detection, and expansion/contraction cycle tracking.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class BollingerWidthPreprocessor(BasePreprocessor):
    """Advanced Bollinger Band Width preprocessor with volatility cycle analysis."""
    
    def preprocess(self, bb_width: pd.Series, prices: pd.Series = None, 
                  **kwargs) -> Dict[str, Any]:
        """
        Advanced Bollinger Band Width preprocessing with volatility analysis.
        
        BB Width = (Upper Band - Lower Band) / Middle Band * 100
        It measures volatility and helps identify squeeze/expansion cycles.
        
        Args:
            bb_width: Bollinger Band Width values
            prices: Price series for additional analysis (optional)
            
        Returns:
            Dictionary with comprehensive BB Width analysis
        """
        clean = bb_width.dropna()
        if len(clean) < 5:
            return {"error": "Insufficient data for Bollinger Band Width analysis"}

        current_width = float(clean.iloc[-1])

        volatility_analysis = self._analyze_volatility_levels(clean)
        squeeze_analysis = self._analyze_squeeze_conditions(clean)
        expansion_analysis = self._analyze_expansion_cycles(clean)
        trend_analysis = self._analyze_bb_width_trend(clean)
        cycle_analysis = self._analyze_volatility_cycles(clean)
        breakout_analysis = self._analyze_breakout_potential(clean)

        # analysis-only evidence
        std = clean.std() + 1e-12
        evidence = {
            "clarity": round(min(1.0, abs(current_width - clean.mean()) / std), 3),
            "consistency": round(min(1.0, abs(self._calculate_velocity(clean, 3)) / std), 3),
            "data_quality": round(min(1.0, len(clean) / 200.0), 3),
        }

        return {
            "indicator": "Bollinger_Width",
            "current": {
                "width": round(current_width, 2),
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            "context": {
                "trend": trend_analysis,
                "expansion": expansion_analysis,
                "breakout": breakout_analysis
            },
            "levels": {
                "volatility": volatility_analysis,
                "squeeze": squeeze_analysis,
                "cycles": cycle_analysis
            },
            "evidence": evidence,
            "summary": self._generate_bb_width_summary(current_width, volatility_analysis, squeeze_analysis)
        }
    
    def _analyze_volatility_levels(self, bb_width: pd.Series) -> Dict[str, Any]:
        """Analyze current volatility level."""
        current_width = float(bb_width.iloc[-1])
        mean_width = float(bb_width.mean())
        std_width = float(bb_width.std())
        max_width = float(bb_width.max())
        min_width = float(bb_width.min())
        percentile = self._calculate_position_rank(bb_width, lookback=len(bb_width))
        
        if current_width > mean_width + 2*std_width: level = "extremely_high"
        elif current_width > mean_width + std_width: level = "high"
        elif current_width > mean_width:             level = "above_average"
        elif current_width > mean_width - std_width: level = "below_average"
        else:                                        level = "low"
        
        denom = mean_width if abs(mean_width) > 1e-12 else 1e-12
        return {
            "level": level,
            "percentile_rank": round(percentile, 1),
            "relative_to_mean": round((current_width / denom - 1) * 100, 2),
            "statistics": {"mean": round(mean_width, 2), "std": round(std_width, 2),
                           "max": round(max_width, 2), "min": round(min_width, 2)}
        }
    
    def _analyze_squeeze_conditions(self, bb_width: pd.Series) -> Dict[str, Any]:
        """Analyze Bollinger Band squeeze conditions."""
        current_width = float(bb_width.iloc[-1])
        
        if len(bb_width) >= 20:
            squeeze_threshold = float(bb_width.rolling(20).min().iloc[-1])
        else:
            mean_w = float(bb_width.mean())
            std_w = float(bb_width.std())
            squeeze_threshold = mean_w - std_w
        
        tol = 1.05
        is_squeeze = current_width <= squeeze_threshold * tol
        
        # duration
        squeeze_periods = 0
        if is_squeeze:
            for i in range(len(bb_width)-1, -1, -1):
                if bb_width.iloc[i] <= squeeze_threshold * tol: 
                    squeeze_periods += 1
                else: 
                    break
        
        total = len(bb_width)
        total_squeeze = int((bb_width <= squeeze_threshold * tol).sum())
        freq = total_squeeze / total if total else 0.0
        
        denom_thr = squeeze_threshold if abs(squeeze_threshold) > 1e-12 else 1e-12
        intensity = ((squeeze_threshold - current_width) / denom_thr * 100) if is_squeeze else 0.0
        
        return {
            "is_squeeze": is_squeeze,
            "squeeze_periods": squeeze_periods,
            "squeeze_threshold": round(squeeze_threshold, 2),
            "squeeze_intensity": round(float(intensity), 2),
            "squeeze_frequency": round(float(freq), 3),
            "squeeze_quality": self._assess_squeeze_quality(squeeze_periods, current_width, squeeze_threshold)
        }
    
    def _assess_squeeze_quality(self, periods: int, current_width: float, threshold: float) -> str:
        """Assess quality of squeeze for breakout potential."""
        if periods >= 10 and current_width < threshold * 0.8:
            return "excellent"
        elif periods >= 6 and current_width < threshold * 0.9:
            return "good"
        elif periods >= 3:
            return "moderate"
        else:
            return "weak"
    
    def _analyze_expansion_cycles(self, bb_width: pd.Series) -> Dict[str, Any]:
        """Analyze volatility expansion cycles."""
        if len(bb_width) < 10:
            return {}
        
        # Find expansion peaks (volatility highs) - base scales by std, pass unitless factor
        peaks = self._find_peaks(bb_width, prominence=0.5)
        
        # Find contraction troughs (volatility lows)  
        troughs = self._find_troughs(bb_width, prominence=0.5)
        
        current_width = float(bb_width.iloc[-1])
        recent_peak = peaks[-1] if peaks else None
        recent_trough = troughs[-1] if troughs else None
        
        if recent_peak and recent_trough:
            cycle_position = "post_expansion" if recent_peak["index"] > recent_trough["index"] else "post_contraction"
            cycle_stage = "contracting" if cycle_position == "post_expansion" else "expanding"
        else:
            cycle_position = cycle_stage = "unclear"
        
        if peaks:
            avg_expansion = float(np.mean([p["value"] for p in peaks]))
            max_expansion = float(np.max([p["value"] for p in peaks]))
        else:
            avg_expansion = max_expansion = current_width
        
        return {
            "cycle_position": cycle_position,
            "cycle_stage": cycle_stage,
            "expansion_peaks": len(peaks),
            "contraction_troughs": len(troughs),
            "avg_expansion_height": round(avg_expansion, 2),
            "max_expansion": round(max_expansion, 2),
            "recent_peak": recent_peak,
            "recent_trough": recent_trough
        }
    
    def _analyze_bb_width_trend(self, bb_width: pd.Series) -> Dict[str, Any]:
        """Analyze BB Width trend characteristics."""
        if len(bb_width) < 5: 
            return {}
        
        vel = self._calculate_velocity(bb_width, 3)
        acc = self._calculate_acceleration(bb_width, 5)
        std = bb_width.std() + 1e-12
        zvel = vel / std
        direction = "expanding" if zvel > 0.1 else "contracting" if zvel < -0.1 else "stable"
        strength = min(1.0, abs(zvel))
        
        return {"direction": direction, "velocity": round(vel, 3), "acceleration": round(acc, 3),
                "strength": round(strength, 3)}
    
    def _analyze_volatility_cycles(self, bb_width: pd.Series) -> Dict[str, Any]:
        """Analyze complete volatility cycles."""
        if len(bb_width) < 20: 
            return {"insufficient_data": True}
        
        peaks = self._find_peaks(bb_width, prominence=0.3)
        troughs = self._find_troughs(bb_width, prominence=0.3)
        
        if len(troughs) >= 2:
            trough_cycles = [troughs[i]["index"] - troughs[i-1]["index"] for i in range(1, len(troughs))]
            avg_cycle_length = float(np.mean(trough_cycles)) if trough_cycles else None
        else:
            avg_cycle_length = None
        
        total = len(bb_width)
        mean_w = float(bb_width.mean())
        expanding = int((bb_width > mean_w).sum())
        contracting = total - expanding
        
        return {
            "avg_cycle_length": round(avg_cycle_length, 1) if avg_cycle_length else None,
            "total_cycles": len(troughs)-1 if len(troughs) > 1 else 0,
            "expanding_time_pct": round(expanding / total * 100, 1),
            "contracting_time_pct": round(contracting / total * 100, 1)
        }
    
    def _analyze_breakout_potential(self, bb_width: pd.Series) -> Dict[str, Any]:
        """Analyze breakout potential based on width patterns."""
        if len(bb_width) < 3: 
            return {}
        
        current = float(bb_width.iloc[-1])
        mean_w = float(bb_width.mean())
        std_w = float(bb_width.std())
        
        if current < mean_w - std_w:      potential, score = "high", 0.8
        elif current < mean_w - 0.5*std_w: potential, score = "medium", 0.6
        elif current < mean_w:             potential, score = "moderate", 0.4
        else:                              potential, score = "low", 0.2
        
        recent = bb_width.iloc[-3:]
        recent_change = (recent.iloc[-1] - recent.iloc[0])
        change_direction = "expanding" if recent_change > 0 else "contracting" if recent_change < 0 else "stable"
        
        return {
            "potential": potential,
            "potential_score": score,
            "recent_change": round(float(recent_change), 3),
            "change_direction": change_direction,
            "setup_quality": self._assess_breakout_setup(current, mean_w, std_w)
        }
    
    def _assess_breakout_setup(self, current: float, mean: float, std: float) -> str:
        """Assess quality of breakout setup."""
        if current < mean - 1.5 * std:
            return "excellent_setup"
        elif current < mean - std:
            return "good_setup"
        elif current < mean - 0.5 * std:
            return "fair_setup"
        else:
            return "poor_setup"
    
    
    def _generate_bb_width_summary(self, current_width: float, volatility_analysis: Dict,
                                  squeeze_analysis: Dict, expansion_analysis: Dict = None) -> str:
        """Generate human-readable BB Width summary with enriched signals."""
        volatility_level = volatility_analysis.get("level", "average")
        percentile = volatility_analysis.get("percentile_rank", 50)

        summary = f"BBWidth={current_width:.2f}% ({volatility_level.replace('_', ' ')}, {percentile:.0f}%ile)"

        # Squeeze detection - critical volatility signal
        if squeeze_analysis.get("is_squeeze", False):
            squeeze_periods = squeeze_analysis.get("squeeze_periods", 0)
            squeeze_quality = squeeze_analysis.get("squeeze_quality", "weak")
            if squeeze_quality in ["excellent", "good"]:
                summary += f". ⚠️ {squeeze_quality.upper()} SQUEEZE ({squeeze_periods}p)"
            else:
                summary += f". Squeeze ({squeeze_periods}p)"

        # Expansion analysis
        if expansion_analysis:
            expansion_type = expansion_analysis.get("type", "")
            if "rapid" in expansion_type:
                summary += ". ⚠️ Rapid volatility expansion"

        # Width trend
        width_trend = volatility_analysis.get("trend", "")
        if width_trend == "contracting":
            summary += ". Volatility contracting"
        elif width_trend == "expanding":
            summary += ". Volatility expanding"

        # Extreme percentiles
        if percentile < 10:
            summary += ". ⚠️ Historically low volatility"
        elif percentile > 90:
            summary += ". ⚠️ Historically high volatility"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full BB Width output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "bbwidth", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        volatility = full_output.get("volatility_analysis", {})
        squeeze = full_output.get("squeeze_analysis", {})
        width_val = to_native(current.get("value"))
        level = volatility.get("level", "average")
        zone = "high_volatility" if level in ["very_high", "high"] else "low_volatility" if level in ["very_low", "low"] else "normal"
        patterns = []
        if squeeze.get("is_squeeze"): patterns.append("squeeze_active")
        return {"indicator": "bbwidth", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(width_val), 4) if width_val else None,
                "value_secondary": None, "value_tertiary": None,
                "velocity": 0.0, "rank": round(to_native(volatility.get("percentile_rank", 50)) / 100, 3),
                "zone": zone, "zone_periods": to_native(squeeze.get("squeeze_periods", 0)) or 0,
                "trend": "expanding" if level in ["high", "very_high"] else "contracting" if level in ["low", "very_low"] else "stable",
                "crossover_type": None, "crossover_periods_ago": None,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
