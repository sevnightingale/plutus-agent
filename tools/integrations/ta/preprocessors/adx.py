"""
ADX (Average Directional Index) Preprocessor.

Advanced ADX preprocessing with trend strength analysis, directional movement tracking,
and trend quality assessment using ADX, +DI, and -DI components.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class ADXPreprocessor(BasePreprocessor):
    """Advanced ADX preprocessor with professional-grade trend analysis."""
    
    def preprocess(self, adx: pd.Series, plus_di: pd.Series = None, minus_di: pd.Series = None, 
                  prices: pd.Series = None, length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced ADX preprocessing with comprehensive trend strength analysis.
        
        ADX measures trend strength (0-100), where values above 25 indicate
        strong trending conditions. +DI and -DI show directional bias.
        
        Args:
            adx: ADX values (trend strength)
            plus_di: +DI values (positive directional indicator)
            minus_di: -DI values (negative directional indicator)
            prices: Price series for additional analysis (optional)
            length: ADX calculation period
            
        Returns:
            Dictionary with comprehensive ADX analysis
        """
        # Clean input data
        clean_adx = adx.dropna()
        if len(clean_adx) < 5:
            return {"error": "Insufficient data for ADX analysis"}
        
        # Clean optional DI series
        clean_plus_di = plus_di.dropna() if plus_di is not None else None
        clean_minus_di = minus_di.dropna() if minus_di is not None else None
        
        current_adx = float(clean_adx.iloc[-1])
        current_plus_di = float(clean_plus_di.iloc[-1]) if clean_plus_di is not None else None
        current_minus_di = float(clean_minus_di.iloc[-1]) if clean_minus_di is not None else None
        
        # Trend strength analysis - use clean data
        trend_strength_analysis = self._analyze_trend_strength(clean_adx)
        
        # Directional analysis (if DI values available)
        directional_analysis = {}
        if clean_plus_di is not None and clean_minus_di is not None:
            directional_analysis = self._analyze_directional_movement(clean_plus_di, clean_minus_di)
        
        # ADX momentum analysis
        momentum_analysis = self._analyze_adx_momentum(clean_adx)
        
        # Pattern analysis
        pattern_analysis = self._analyze_adx_patterns(clean_adx, clean_plus_di, clean_minus_di)
        
        # Position rank analysis
        position_rank = self._calculate_position_rank(clean_adx, lookback=20)
        
        return {
            "indicator": "ADX",
            "current": {
                "adx": round(current_adx, 2),
                "plus_di": round(current_plus_di, 2) if current_plus_di is not None else None,
                "minus_di": round(current_minus_di, 2) if current_minus_di is not None else None,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            "context": {
                "trend_strength": trend_strength_analysis["current_strength"],
                "strength_value": trend_strength_analysis["strength_value"],
                "description": trend_strength_analysis["description"],
                "trend_evolution": trend_strength_analysis["trend_evolution"],
                "directional_bias": directional_analysis.get("current_bias"),
                "directional_strength": directional_analysis.get("directional_strength")
            },
            "levels": {
                "current_strength": trend_strength_analysis["current_strength"],
                "weak_threshold": 20,
                "strong_threshold": 25,
                "very_strong_threshold": 40,
                "extreme_threshold": 60
            },
            "trend_strength": {
                "strength_percentage": round((current_adx / 100) * 100, 1),
                "strong_trend_percentage": trend_strength_analysis["strong_trend_percentage"],
                "weak_trend_percentage": trend_strength_analysis["weak_trend_percentage"],
                "consistency": trend_strength_analysis["strength_consistency"]
            },
            "directional": directional_analysis,
            "momentum": momentum_analysis,
            "patterns": pattern_analysis,
            "position_rank": {
                "percentile": round(position_rank, 1),
                "interpretation": self._interpret_position_rank(position_rank)
            },
            "summary": self._generate_adx_summary(current_adx, current_plus_di, current_minus_di,
                                                 trend_strength_analysis, directional_analysis,
                                                 momentum_analysis, pattern_analysis)
        }
    
    def _analyze_trend_strength(self, adx: pd.Series) -> Dict[str, Any]:
        """Analyze ADX trend strength characteristics."""
        current_adx = adx.iloc[-1]
        
        # ADX strength levels
        if current_adx < 20:
            strength_level = "weak"
            description = "Weak or no trend"
        elif current_adx < 25:
            strength_level = "developing"
            description = "Developing trend"
        elif current_adx < 40:
            strength_level = "strong"
            description = "Strong trending market"
        elif current_adx < 60:
            strength_level = "very_strong"
            description = "Very strong trend"
        else:
            strength_level = "extreme"
            description = "Extremely strong trend"
        
        # Trend duration analysis
        strong_trend_periods = sum(1 for v in adx if v >= 25)
        weak_trend_periods = sum(1 for v in adx if v < 20)
        total_periods = len(adx)
        
        # Recent trend strength evolution
        recent_adx = adx.iloc[-5:] if len(adx) >= 5 else adx
        trend_evolution = self._analyze_trend_evolution(recent_adx)
        
        return {
            "current_strength": strength_level,
            "strength_value": round(current_adx, 2),
            "description": description,
            "strong_trend_percentage": round((strong_trend_periods / total_periods) * 100, 1),
            "weak_trend_percentage": round((weak_trend_periods / total_periods) * 100, 1),
            "trend_evolution": trend_evolution,
            "strength_consistency": self._calculate_strength_consistency(adx)
        }
    
    def _analyze_trend_evolution(self, recent_adx: pd.Series) -> str:
        """Analyze how trend strength has evolved recently."""
        if len(recent_adx) < 3:
            return "insufficient_data"
        
        first_half = recent_adx.iloc[:len(recent_adx)//2].mean()
        second_half = recent_adx.iloc[len(recent_adx)//2:].mean()
        
        change = second_half - first_half
        
        if change > 3:
            return "strengthening"
        elif change < -3:
            return "weakening"
        else:
            return "stable"
    
    def _calculate_strength_consistency(self, adx: pd.Series) -> float:
        """Calculate consistency of trend strength."""
        if len(adx) < 5:
            return 0.5
        
        # Calculate coefficient of variation (lower = more consistent)
        mean_adx = adx.mean()
        std_adx = adx.std()
        
        if abs(mean_adx) < 1e-12:
            return 0.0
        
        cv = std_adx / mean_adx
        # Convert to consistency score (0-1, higher = more consistent)
        consistency = max(0.0, min(1.0, 1.0 - cv))
        
        return round(consistency, 3)
    
    def _analyze_directional_movement(self, plus_di: pd.Series, minus_di: pd.Series) -> Dict[str, Any]:
        """Analyze directional movement indicators."""
        current_plus_di = plus_di.iloc[-1]
        current_minus_di = minus_di.iloc[-1]
        
        # Current directional bias
        if current_plus_di > current_minus_di:
            bias = "bullish"
            strength = current_plus_di - current_minus_di
        else:
            bias = "bearish"
            strength = current_minus_di - current_plus_di
        
        # DI crossover analysis
        crossover_analysis = self._analyze_di_crossovers(plus_di, minus_di)
        
        # Directional momentum
        plus_di_momentum = self._calculate_velocity(plus_di, 3)
        minus_di_momentum = self._calculate_velocity(minus_di, 3)
        
        # Directional spread analysis
        spread_analysis = self._analyze_directional_spread(plus_di, minus_di)
        
        return {
            "current_bias": bias,
            "directional_strength": round(strength, 2),
            "plus_di_momentum": round(plus_di_momentum, 2),
            "minus_di_momentum": round(minus_di_momentum, 2),
            "crossovers": crossover_analysis,
            "spread_analysis": spread_analysis,
            "dominance": self._calculate_directional_dominance(plus_di, minus_di)
        }
    
    def _analyze_di_crossovers(self, plus_di: pd.Series, minus_di: pd.Series) -> Dict[str, Any]:
        """Analyze +DI/-DI crossovers."""
        crossovers = []
        
        for i in range(1, min(10, len(plus_di))):
            prev_plus = plus_di.iloc[-(i+1)]
            curr_plus = plus_di.iloc[-i]
            prev_minus = minus_di.iloc[-(i+1)]
            curr_minus = minus_di.iloc[-i]
            
            # Bullish crossover (+DI crosses above -DI)
            if prev_plus <= prev_minus and curr_plus > curr_minus:
                crossovers.append({
                    "type": "bullish_crossover",
                    "periods_ago": i,
                    "strength": abs(curr_plus - curr_minus)
                })
            # Bearish crossover (+DI crosses below -DI)
            elif prev_plus >= prev_minus and curr_plus < curr_minus:
                crossovers.append({
                    "type": "bearish_crossover",
                    "periods_ago": i,
                    "strength": abs(curr_plus - curr_minus)
                })
        
        return {
            "recent_crossovers": crossovers[:3],
            "latest_crossover": crossovers[0] if crossovers else None
        }
    
    def _analyze_directional_spread(self, plus_di: pd.Series, minus_di: pd.Series) -> Dict[str, Any]:
        """Analyze the spread between +DI and -DI."""
        spread = plus_di - minus_di
        current_spread = spread.iloc[-1]
        
        # Spread statistics
        avg_spread = spread.mean()
        spread_volatility = spread.std()
        
        # Spread extremes
        max_bullish_spread = spread.max()
        max_bearish_spread = spread.min()
        
        return {
            "current_spread": round(current_spread, 2),
            "average_spread": round(avg_spread, 2),
            "spread_volatility": round(spread_volatility, 2),
            "max_bullish_spread": round(max_bullish_spread, 2),
            "max_bearish_spread": round(max_bearish_spread, 2),
            "spread_interpretation": self._interpret_spread(current_spread, avg_spread)
        }
    
    def _interpret_spread(self, current_spread: float, avg_spread: float) -> str:
        """Interpret directional spread."""
        if abs(current_spread) > abs(avg_spread) * 1.5:
            return "extreme_directional_bias"
        elif abs(current_spread) > abs(avg_spread):
            return "strong_directional_bias"
        elif abs(current_spread) < abs(avg_spread) * 0.5:
            return "balanced_directional_forces"
        else:
            return "moderate_directional_bias"
    
    def _calculate_directional_dominance(self, plus_di: pd.Series, minus_di: pd.Series) -> Dict[str, Any]:
        """Calculate which direction has been dominant."""
        total_periods = len(plus_di)
        bullish_periods = sum(1 for i in range(len(plus_di)) if plus_di.iloc[i] > minus_di.iloc[i])
        bearish_periods = total_periods - bullish_periods
        
        return {
            "bullish_dominance_pct": round((bullish_periods / total_periods) * 100, 1),
            "bearish_dominance_pct": round((bearish_periods / total_periods) * 100, 1),
            "dominant_direction": "bullish" if bullish_periods > bearish_periods else "bearish"
        }
    
    def _analyze_adx_momentum(self, adx: pd.Series) -> Dict[str, Any]:
        """Analyze ADX momentum characteristics."""
        if len(adx) < 5:
            return {}
        
        velocity = self._calculate_velocity(adx, 3)
        acceleration = self._calculate_acceleration(adx, 6)
        
        # ADX slope analysis
        slope_interpretation = self._interpret_adx_slope(velocity, acceleration)
        
        return {
            "velocity": round(velocity, 2),
            "acceleration": round(acceleration, 2),
            "slope_interpretation": slope_interpretation,
            "momentum_quality": self._assess_adx_momentum_quality(velocity, acceleration)
        }
    
    def _interpret_adx_slope(self, velocity: float, acceleration: float) -> str:
        """Interpret ADX slope characteristics."""
        if velocity > 1 and acceleration > 0:
            return "trend_strengthening_accelerating"
        elif velocity > 1:
            return "trend_strengthening"
        elif velocity < -1 and acceleration < 0:
            return "trend_weakening_accelerating"
        elif velocity < -1:
            return "trend_weakening"
        else:
            return "trend_strength_stable"
    
    def _assess_adx_momentum_quality(self, velocity: float, acceleration: float) -> str:
        """Assess quality of ADX momentum."""
        if abs(velocity) > 2 and abs(acceleration) > 0.5:
            return "high_quality_momentum"
        elif abs(velocity) > 1:
            return "moderate_quality_momentum"
        else:
            return "low_quality_momentum"
    
    def _analyze_adx_patterns(self, adx: pd.Series, plus_di: pd.Series = None, minus_di: pd.Series = None) -> Dict[str, Any]:
        """Analyze ADX patterns and formations."""
        patterns = {}
        
        if len(adx) >= 10:
            # ADX turning points
            turning_points = self._detect_adx_turning_points(adx)
            if turning_points:
                patterns["turning_points"] = turning_points
            
            # Extreme ADX levels
            extreme_levels = self._detect_extreme_adx_levels(adx)
            if extreme_levels:
                patterns["extreme_levels"] = extreme_levels
        
        if plus_di is not None and minus_di is not None and len(plus_di) >= 10:
            # DI convergence/divergence
            di_patterns = self._analyze_di_convergence_divergence(plus_di, minus_di)
            if di_patterns:
                patterns["di_patterns"] = di_patterns
        
        return patterns
    
    def _detect_adx_turning_points(self, adx: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect significant ADX turning points."""
        if len(adx) < 8:
            return None
        
        recent_adx = adx.iloc[-8:]
        
        # Find local maxima and minima using base class methods
        peaks = self._find_peaks(recent_adx, prominence=1.0)  # Use normalized prominence
        troughs = self._find_troughs(recent_adx, prominence=1.0)
        
        if peaks:
            latest_peak = peaks[-1]
            if latest_peak["periods_ago"] <= 3:  # Recent peak
                return {
                    "type": "peak",
                    "value": round(latest_peak["value"], 2),
                    "periods_ago": latest_peak["periods_ago"],
                    "description": f"ADX peaked at {latest_peak['value']:.1f}, trend may be weakening"
                }
        
        if troughs:
            latest_trough = troughs[-1]
            if latest_trough["periods_ago"] <= 3:  # Recent trough
                return {
                    "type": "trough",
                    "value": round(latest_trough["value"], 2),
                    "periods_ago": latest_trough["periods_ago"],
                    "description": f"ADX bottomed at {latest_trough['value']:.1f}, trend may be strengthening"
                }
        
        return None
    
    def _detect_extreme_adx_levels(self, adx: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect extreme ADX levels."""
        current_adx = adx.iloc[-1]
        max_adx = adx.max()
        min_adx = adx.min()
        
        # Extreme high ADX
        if current_adx > 60 and current_adx >= max_adx * 0.9:
            return {
                "type": "extreme_high",
                "value": round(current_adx, 2),
                "description": "ADX at extremely high level, trend exhaustion possible"
            }
        
        # Extreme low ADX
        if current_adx < 15 and current_adx <= min_adx * 1.1:
            return {
                "type": "extreme_low",
                "value": round(current_adx, 2),
                "description": "ADX at extremely low level, consolidation/ranging market"
            }
        
        return None
    
    def _analyze_di_convergence_divergence(self, plus_di: pd.Series, minus_di: pd.Series) -> Optional[Dict[str, Any]]:
        """Analyze +DI/-DI convergence and divergence patterns."""
        spread = abs(plus_di - minus_di)
        current_spread = spread.iloc[-1]
        recent_spread = spread.iloc[-5:] if len(spread) >= 5 else spread
        
        # Convergence: DIs moving closer together
        if len(recent_spread) >= 3:
            spread_trend = self._calculate_velocity(recent_spread, 2)
            
            if spread_trend < -1:  # Converging
                return {
                    "type": "convergence",
                    "current_spread": round(current_spread, 2),
                    "description": "Directional indicators converging, trend weakening"
                }
            elif spread_trend > 1:  # Diverging
                return {
                    "type": "divergence", 
                    "current_spread": round(current_spread, 2),
                    "description": "Directional indicators diverging, trend strengthening"
                }
        
        return None
    
    def _assess_trend_quality(self, adx_value: float, directional_analysis: Dict, momentum_analysis: Dict) -> Dict[str, Any]:
        """Assess overall trend quality."""
        quality_score = 0.0
        quality_factors = []
        
        # ADX strength contribution
        if adx_value >= 40:
            adx_contribution = 1.0
        elif adx_value >= 25:
            adx_contribution = 0.7
        elif adx_value >= 20:
            adx_contribution = 0.5
        else:
            adx_contribution = 0.2
        
        quality_factors.append(adx_contribution)
        
        # Directional clarity contribution
        if directional_analysis:
            dir_strength = directional_analysis.get("directional_strength", 0)
            dir_contribution = min(1.0, dir_strength / 20)
            quality_factors.append(dir_contribution)
        
        # Momentum quality contribution
        if momentum_analysis:
            momentum_quality = momentum_analysis.get("momentum_quality", "low_quality_momentum")
            if momentum_quality == "high_quality_momentum":
                momentum_contribution = 0.9
            elif momentum_quality == "moderate_quality_momentum":
                momentum_contribution = 0.6
            else:
                momentum_contribution = 0.3
            quality_factors.append(momentum_contribution)
        
        quality_score = np.mean(quality_factors) if quality_factors else 0.0
        
        # Quality interpretation
        if quality_score >= 0.8:
            quality_rating = "excellent"
        elif quality_score >= 0.6:
            quality_rating = "good"
        elif quality_score >= 0.4:
            quality_rating = "fair"
        else:
            quality_rating = "poor"
        
        return {
            "quality_score": round(quality_score, 3),
            "quality_rating": quality_rating,
            "description": self._get_quality_description(quality_rating)
        }
    
    def _get_quality_description(self, rating: str) -> str:
        """Get description for trend quality rating."""
        descriptions = {
            "excellent": "Very strong trending conditions with clear direction",
            "good": "Good trending conditions suitable for trend following",
            "fair": "Moderate trending conditions, some caution advised",
            "poor": "Weak or unclear trending conditions, consider range-bound strategies"
        }
        return descriptions.get(rating, "Unknown quality rating")
    
    
    def _generate_adx_summary(self, adx_value: float, plus_di: float, minus_di: float,
                             trend_strength: Dict, directional_analysis: Dict,
                             momentum_analysis: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable ADX summary with enriched signals."""
        # Strength classification with ADX value
        summary = f"ADX={adx_value:.1f} ({trend_strength['current_strength']})"

        # Directional bias if available
        if plus_di is not None and minus_di is not None:
            bias = directional_analysis.get("current_bias", "neutral")
            strength = directional_analysis.get("directional_strength", 0)
            summary += f", {bias} +DI/-DI spread={strength:.1f}"

        # Trend evolution
        evolution = trend_strength.get("trend_evolution", "stable")
        if evolution == "strengthening":
            summary += ". Trend strengthening"
        elif evolution == "weakening":
            summary += ". Trend weakening"

        # DI Crossover - important signal
        if directional_analysis:
            crossovers = directional_analysis.get("crossovers", {})
            latest_cross = crossovers.get("latest_crossover")
            if latest_cross and latest_cross.get("periods_ago", 99) <= 5:
                cross_type = "bullish" if "bullish" in latest_cross.get("type", "") else "bearish"
                summary += f". ✓ {cross_type.capitalize()} DI crossover {latest_cross['periods_ago']}p ago"

        # ADX turning points
        if pattern_analysis:
            if "turning_points" in pattern_analysis:
                tp = pattern_analysis["turning_points"]
                if tp.get("type") == "peak" and tp.get("periods_ago", 99) <= 3:
                    summary += ". ⚠️ ADX peaked, trend may weaken"
                elif tp.get("type") == "trough" and tp.get("periods_ago", 99) <= 3:
                    summary += ". ✓ ADX bottomed, trend may strengthen"

            # Extreme levels
            if "extreme_levels" in pattern_analysis:
                el = pattern_analysis["extreme_levels"]
                if el.get("type") == "extreme_high":
                    summary += ". ⚠️ Extreme ADX - trend exhaustion possible"
                elif el.get("type") == "extreme_low":
                    summary += ". ⚠️ Very low ADX - ranging/consolidation"

            # DI convergence/divergence
            if "di_patterns" in pattern_analysis:
                di = pattern_analysis["di_patterns"]
                if di.get("type") == "convergence":
                    summary += ". DI lines converging"
                elif di.get("type") == "divergence":
                    summary += ". DI lines diverging"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full ADX output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "adx", "timeframe": timeframe, "timestamp": None,
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
        trend_strength = full_output.get("trend_strength", {})
        directional = full_output.get("directional_analysis", {})
        momentum = full_output.get("momentum_analysis", {})
        patterns_data = full_output.get("patterns", {})

        adx_val = to_native(current.get("adx"))
        plus_di = to_native(current.get("plus_di"))
        minus_di = to_native(current.get("minus_di"))

        # Zone based on ADX strength
        if adx_val is not None:
            if adx_val >= 40: zone = "very_strong_trend"
            elif adx_val >= 25: zone = "strong_trend"
            elif adx_val >= 20: zone = "weak_trend"
            else: zone = "no_trend"
        else:
            zone = "unknown"

        # Trend direction from directional bias
        bias = directional.get("current_bias", "neutral")
        trend = "bullish" if bias == "bullish" else "bearish" if bias == "bearish" else "neutral"

        # Crossover extraction
        crossovers = directional.get("crossovers", {})
        latest_cross = crossovers.get("latest_crossover")
        crossover_type = None
        crossover_periods_ago = None
        if latest_cross:
            cross_type = latest_cross.get("type", "")
            crossover_type = "crossover_bullish" if "bullish" in cross_type else "crossover_bearish"
            crossover_periods_ago = to_native(latest_cross.get("periods_ago"))

        # Pattern extraction
        pattern_codes = []
        if "turning_points" in patterns_data:
            tp = patterns_data["turning_points"]
            if tp.get("type") == "peak": pattern_codes.append("momentum_weakening")
            elif tp.get("type") == "trough": pattern_codes.append("momentum_accelerating")
        if "extreme_levels" in patterns_data:
            el = patterns_data["extreme_levels"]
            if el.get("type") == "extreme_high": pattern_codes.append("momentum_strong_up")
            elif el.get("type") == "extreme_low": pattern_codes.append("squeeze_active")

        return {
            "indicator": "adx",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(adx_val, 2) if adx_val else None,
            "value_secondary": round(plus_di, 2) if plus_di else None,
            "value_tertiary": round(minus_di, 2) if minus_di else None,
            "velocity": round(to_native(momentum.get("velocity", 0)) or 0, 4),
            "rank": round(adx_val / 100.0, 3) if adx_val else 0.0,
            "zone": zone,
            "zone_periods": 0,
            "trend": trend,
            "crossover_type": crossover_type,
            "crossover_periods_ago": crossover_periods_ago,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }