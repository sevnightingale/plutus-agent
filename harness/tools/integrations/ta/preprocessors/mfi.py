"""
MFI (Money Flow Index) Preprocessor.

Advanced MFI preprocessing with volume-weighted momentum analysis, 
overbought/oversold detection, and money flow pattern recognition.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class MFIPreprocessor(BasePreprocessor):
    """Advanced MFI preprocessor with professional-grade analysis."""
    
    def preprocess(self, mfi: pd.Series, prices: pd.Series = None, 
                  length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced MFI preprocessing with comprehensive volume-weighted analysis.
        
        MFI oscillates between 0-100, incorporating volume in its calculation.
        Values above 80 indicate overbought, below 20 indicate oversold.
        
        Args:
            mfi: MFI values
            prices: Price series for divergence analysis (optional)
            length: MFI calculation period
            
        Returns:
            Dictionary with comprehensive MFI analysis
        """
        # Capture original lengths
        orig_mfi_len = len(mfi)
        orig_prices_len = len(prices) if prices is not None else 0

        # Clean MFI once, keep full history for core analysis
        mfi_clean = pd.to_numeric(mfi, errors="coerce").dropna()
        if len(mfi_clean) < 5:
            return {"error": "Insufficient data for MFI analysis"}

        # Keep full MFI for core analysis
        mfi_core = mfi_clean
        current_mfi = float(mfi_core.iloc[-1])

        # Only align for divergence if prices provided
        prices_core = None
        mfi_div, prices_div = None, None
        if prices is not None:
            prices_clean = pd.to_numeric(prices, errors="coerce").dropna()
            mfi_div, prices_div = mfi_clean.align(prices_clean, join="inner")

        # Generate proper timestamp
        if np.issubdtype(mfi_core.index.dtype, np.datetime64):
            timestamp = mfi_core.index[-1].isoformat() if hasattr(mfi_core.index[-1], 'isoformat') else datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()
        
        # Use full MFI for core analysis
        zone_analysis = self._analyze_mfi_zones(mfi_core, length)
        momentum_analysis = self._analyze_mfi_momentum(mfi_core, length)
        money_flow_analysis = self._analyze_money_flow_characteristics(mfi_core, length)
        pattern_analysis = self._analyze_mfi_patterns(mfi_core, length)
        position_rank = self._calculate_position_rank(mfi_core, lookback=max(10, length))

        # Divergence analysis only uses aligned data
        divergence = self._detect_mfi_divergence(mfi_div, prices_div, length) if prices_div is not None else None
        
        return {
            "indicator": "MFI",
            "current": {
                "value": round(current_mfi, 2),
                "timestamp": timestamp
            },
            "context": {
                "length": length,
                "position_rank": {
                    "percentile": round(position_rank, 1),
                    "interpretation": self._interpret_position_rank(position_rank)
                }
            },
            "levels": {
                "zones": zone_analysis,
                "money_flow": money_flow_analysis
            },
            "patterns": {
                "momentum": momentum_analysis,
                "formations": pattern_analysis,
                "divergence": divergence
            },
            "evidence": {
                "data_quality": {
                    "original_periods": {"mfi": orig_mfi_len, "prices": orig_prices_len} if orig_prices_len > 0 else {"mfi": orig_mfi_len},
                    "core_analysis_periods": len(mfi_core),
                    "divergence_aligned_periods": len(mfi_div) if mfi_div is not None else None,
                    "had_prices": prices is not None
                },
                "calculation_notes": f"MFI analysis based on {len(mfi_core)} core periods, divergence on {len(mfi_div) if mfi_div is not None else 'N/A'} aligned periods"
            },
            "summary": self._generate_mfi_summary(current_mfi, zone_analysis, momentum_analysis, money_flow_analysis,
                                               divergence, pattern_analysis)
        }
    
    def _analyze_mfi_zones(self, mfi: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze MFI overbought/oversold zones."""
        current_mfi = mfi.iloc[-1]
        
        # MFI zones: 80 (overbought), 20 (oversold)
        if current_mfi >= 80:
            current_zone = "overbought"
        elif current_mfi <= 20:
            current_zone = "oversold"
        else:
            current_zone = "neutral"
        
        # Streak analysis
        ob_streak = self._calculate_zone_streak(mfi, 80, "above")
        os_streak = self._calculate_zone_streak(mfi, 20, "below")
        
        # Time percentage analysis on finite values
        finite = mfi.dropna()
        total_periods = len(finite)
        ob_pct = float((finite >= 80).mean() * 100)
        os_pct = float((finite <= 20).mean() * 100)
        
        # Exit analysis
        ob_exit = self._analyze_zone_exits(mfi, 80, "above")
        os_exit = self._analyze_zone_exits(mfi, 20, "below")
        
        # Extreme readings (beyond 90/10)
        extreme_high = current_mfi >= 90
        extreme_low = current_mfi <= 10
        
        return {
            "current_zone": current_zone,
            "overbought": {
                "level": 80,
                "status": "in_zone" if current_mfi >= 80 else "below",
                "streak_length": ob_streak,
                "time_percentage": round(ob_pct, 1),
                "exit_analysis": ob_exit,
                "extreme_reading": extreme_high
            },
            "oversold": {
                "level": 20,
                "status": "in_zone" if current_mfi <= 20 else "above",
                "streak_length": os_streak,
                "time_percentage": round(os_pct, 1),
                "exit_analysis": os_exit,
                "extreme_reading": extreme_low
            },
            "neutral_bias": "rising" if current_mfi > 51 else ("falling" if current_mfi < 49 else "neutral")
        }
    
    def _calculate_zone_streak(self, values: pd.Series, threshold: float, direction: str) -> int:
        """Calculate consecutive periods in a zone."""
        streak = 0
        for i in range(len(values) - 1, -1, -1):
            if direction == "above" and values.iloc[i] >= threshold:
                streak += 1
            elif direction == "below" and values.iloc[i] <= threshold:
                streak += 1
            else:
                break
        return streak
    
    def _analyze_zone_exits(self, values: pd.Series, threshold: float, direction: str) -> Dict[str, Any]:
        """Analyze recent exits from zones."""
        exits = []
        
        for i in range(1, min(10, len(values))):
            prev_val = values.iloc[-(i+1)]
            curr_val = values.iloc[-i]
            
            if direction == "above":
                if prev_val >= threshold and curr_val < threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": curr_val,
                        "strength": round((threshold - curr_val) / 20, 3)  # Normalize to 0-1 scale
                    })
            else:  # below
                if prev_val <= threshold and curr_val > threshold:
                    exits.append({
                        "periods_ago": i,
                        "exit_level": curr_val,
                        "strength": round((curr_val - threshold) / 20, 3)  # Normalize to 0-1 scale
                    })
        
        return {
            "recent_exits": exits[:3],
            "latest_exit": exits[0] if exits else None
        }
    
    def _analyze_mfi_momentum(self, mfi: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze MFI momentum characteristics."""
        if len(mfi) < 5:
            return {}
        
        velocity = self._calculate_velocity(mfi, max(2, length // 5))
        acceleration = self._calculate_acceleration(mfi, max(3, length // 3))
        
        # Smoothness analysis (MFI should be smoother than RSI due to volume weighting)
        volatility = mfi.std()
        recent_range = mfi.iloc[-10:].max() - mfi.iloc[-10:].min()
        
        # Momentum strength
        momentum_strength = min(1.0, abs(velocity) / 10)
        
        return {
            "velocity": round(velocity, 2),
            "acceleration": round(acceleration, 2),
            "volatility": round(volatility, 2),
            "recent_range": round(recent_range, 2),
            "momentum_strength": round(momentum_strength, 3),
            "momentum_interpretation": self._interpret_mfi_momentum(velocity, acceleration)
        }
    
    def _interpret_mfi_momentum(self, velocity: float, acceleration: float) -> str:
        """Interpret MFI momentum characteristics."""
        if velocity > 5 and acceleration > 0:
            return "strong_money_inflow_acceleration"
        elif velocity > 5:
            return "strong_money_inflow"
        elif velocity < -5 and acceleration < 0:
            return "strong_money_outflow_acceleration"
        elif velocity < -5:
            return "strong_money_outflow"
        elif abs(velocity) < 1:
            return "balanced_money_flow"
        else:
            return f"{'money_inflow' if velocity > 0 else 'money_outflow'}_momentum"
    
    def _analyze_money_flow_characteristics(self, mfi: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze specific money flow characteristics."""
        if len(mfi) < 10:
            return {}
        
        current_mfi = mfi.iloc[-1]
        
        # Money flow pressure analysis
        pressure = "buying" if current_mfi > 50 else "selling"
        pressure_strength = abs(current_mfi - 50) / 50  # 0-1 scale
        
        # Consistency analysis (how consistently money flows in one direction)
        recent_values = mfi.iloc[-5:]
        consistency = self._calculate_flow_consistency(recent_values)
        
        # Volume-price relationship strength
        # Higher MFI values suggest strong volume backing price moves
        volume_confirmation = "strong" if current_mfi > 60 or current_mfi < 40 else "weak"
        
        # Money flow cycles
        cycle_analysis = self._analyze_money_flow_cycles(mfi)
        
        return {
            "pressure": pressure,
            "pressure_strength": round(pressure_strength, 3),
            "consistency": round(consistency, 3),
            "volume_confirmation": volume_confirmation,
            "cycle_analysis": cycle_analysis,
            "flow_quality": self._assess_flow_quality(current_mfi, pressure_strength, consistency)
        }
    
    def _calculate_flow_consistency(self, values: pd.Series) -> float:
        """Calculate consistency of money flow direction."""
        if len(values) < 3:
            return 0.5
        
        changes = values.diff().dropna()
        if len(changes) == 0:
            return 0.5
        
        # Count directional consistency
        positive_changes = sum(1 for x in changes if x > 0)
        negative_changes = sum(1 for x in changes if x < 0)
        total_changes = len(changes)
        
        # Return consistency ratio (0.5 = neutral, 1.0 = all same direction)
        if total_changes == 0:
            return 0.5
        
        max_directional = max(positive_changes, negative_changes)
        return max_directional / total_changes
    
    def _analyze_money_flow_cycles(self, mfi: pd.Series) -> Dict[str, Any]:
        """Analyze money flow cycles and patterns."""
        if len(mfi) < 15:
            return {"cycle_detected": False}
        
        # Look for cyclical patterns in money flow
        recent_values = mfi.iloc[-15:]
        
        # Find peaks and troughs with relative prominence
        prom = max(1e-6, recent_values.std() * 0.5)
        peaks = self._find_peaks(recent_values, prominence=prom)
        troughs = self._find_troughs(recent_values, prominence=prom)
        
        cycle_detected = len(peaks) >= 2 or len(troughs) >= 2
        
        if cycle_detected:
            # Calculate average cycle length
            if len(peaks) >= 2:
                peak_distances = [peaks[i]["index"] - peaks[i-1]["index"] for i in range(1, len(peaks))]
                avg_cycle = np.mean(peak_distances) if peak_distances else None
            elif len(troughs) >= 2:
                trough_distances = [troughs[i]["index"] - troughs[i-1]["index"] for i in range(1, len(troughs))]
                avg_cycle = np.mean(trough_distances) if trough_distances else None
            else:
                avg_cycle = None
            
            return {
                "cycle_detected": True,
                "avg_cycle_length": round(avg_cycle, 1) if avg_cycle else None,
                "recent_peaks": len(peaks),
                "recent_troughs": len(troughs)
            }
        
        return {"cycle_detected": False}
    
    def _assess_flow_quality(self, current_mfi: float, pressure_strength: float, consistency: float) -> str:
        """Assess overall money flow quality."""
        if pressure_strength > 0.6 and consistency > 0.7:
            return "high_quality_flow"
        elif pressure_strength > 0.4 and consistency > 0.6:
            return "medium_quality_flow"
        else:
            return "low_quality_flow"
    
    def _analyze_mfi_patterns(self, mfi: pd.Series, length: int) -> Dict[str, Any]:
        """Analyze MFI patterns and formations."""
        patterns = {}
        
        min_patterns_length = max(12, length)
        if len(mfi) >= min_patterns_length:
            # Double top/bottom patterns
            double_pattern = self._detect_double_patterns(mfi)
            if double_pattern:
                patterns["double_pattern"] = double_pattern
            
            # Money flow exhaustion pattern
            exhaustion_pattern = self._detect_money_flow_exhaustion(mfi)
            if exhaustion_pattern:
                patterns["exhaustion"] = exhaustion_pattern
        
        return patterns
    
    def _detect_double_patterns(self, mfi: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect double top/bottom patterns in MFI."""
        if len(mfi) < 12:
            return None
        
        recent_values = mfi.iloc[-12:]
        prom = max(1e-6, recent_values.std() * 0.5)
        peaks = self._find_peaks(recent_values, prominence=prom)
        troughs = self._find_troughs(recent_values, prominence=prom)
        
        # Double top pattern
        if len(peaks) >= 2:
            last_peak = peaks[-1]
            prev_peak = peaks[-2]
            
            # Check if peaks are roughly equal and in overbought territory
            if (abs(last_peak["value"] - prev_peak["value"]) < 5 and 
                last_peak["value"] > 70 and prev_peak["value"] > 70):
                return {
                    "type": "double_top",
                    "description": f"Double top at MFI {last_peak['value']:.1f} level"
                }
        
        # Double bottom pattern
        if len(troughs) >= 2:
            last_trough = troughs[-1]
            prev_trough = troughs[-2]
            
            # Check if troughs are roughly equal and in oversold territory
            if (abs(last_trough["value"] - prev_trough["value"]) < 5 and 
                last_trough["value"] < 30 and prev_trough["value"] < 30):
                return {
                    "type": "double_bottom",
                    "description": f"Double bottom at MFI {last_trough['value']:.1f} level"
                }
        
        return None
    
    def _detect_money_flow_exhaustion(self, mfi: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect money flow exhaustion patterns."""
        if len(mfi) < 8:
            return None
        
        recent_values = mfi.iloc[-8:]
        current_mfi = mfi.iloc[-1]
        
        # Bullish exhaustion: prolonged high MFI starting to decline
        if current_mfi > 80:
            high_periods = sum(1 for v in recent_values if v > 80)
            if high_periods >= 4:
                # Check if showing signs of decline
                velocity = self._calculate_velocity(recent_values.iloc[-3:], 2)
                if velocity < -2:
                    return {
                        "type": "bullish_exhaustion",
                        "description": f"Money flow exhaustion after {high_periods} periods above 80"
                    }
        
        # Bearish exhaustion: prolonged low MFI starting to rise
        elif current_mfi < 20:
            low_periods = sum(1 for v in recent_values if v < 20)
            if low_periods >= 4:
                # Check if showing signs of recovery
                velocity = self._calculate_velocity(recent_values.iloc[-3:], 2)
                if velocity > 2:
                    return {
                        "type": "bearish_exhaustion",
                        "description": f"Money flow exhaustion after {low_periods} periods below 20"
                    }
        
        return None
    
    def _detect_mfi_divergence(self, mfi: pd.Series, prices: pd.Series, length: int) -> Optional[Dict[str, Any]]:
        """Detect MFI-price divergence patterns."""
        divergence_window = min(max(10, length), len(mfi), len(prices))
        if divergence_window < 10:
            return None

        # Use aligned data for the same window
        win = divergence_window
        m_recent, p_recent = mfi.tail(win), prices.tail(win)

        # Calculate relative prominence thresholds
        prom_m = max(1e-6, m_recent.std() * 0.6)
        prom_p = max(1e-6, p_recent.std() * 0.6)

        # Find peaks and troughs with scaled prominence
        mfi_peaks = self._find_peaks(m_recent, prominence=prom_m)
        mfi_troughs = self._find_troughs(m_recent, prominence=prom_m)
        price_peaks = self._find_peaks(p_recent, prominence=prom_p)
        price_troughs = self._find_troughs(p_recent, prominence=prom_p)
        
        # Bearish divergence: price higher highs, MFI lower highs
        if len(mfi_peaks) >= 2 and len(price_peaks) >= 2:
            latest_mfi_peak = mfi_peaks[-1]
            prev_mfi_peak = mfi_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]
            
            if (latest_price_peak["value"] > prev_price_peak["value"] and 
                latest_mfi_peak["value"] < prev_mfi_peak["value"]):
                return {
                    "type": "negative_divergence",
                    "description": "Price making higher highs while money flow weakening"
                }
        
        # Bullish divergence: price lower lows, MFI higher lows
        if len(mfi_troughs) >= 2 and len(price_troughs) >= 2:
            latest_mfi_trough = mfi_troughs[-1]
            prev_mfi_trough = mfi_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]
            
            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_mfi_trough["value"] > prev_mfi_trough["value"]):
                return {
                    "type": "positive_divergence",
                    "description": "Price making lower lows while money flow strengthening"
                }
        
        return None
    
    def _generate_mfi_summary(self, mfi_value: float, zone_analysis: Dict,
                             momentum_analysis: Dict, money_flow_analysis: Dict,
                             divergence: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable MFI summary with enriched signals."""
        summary = f"MFI={mfi_value:.1f}"

        # Add zone information
        zone = zone_analysis["current_zone"]
        if zone != "neutral":
            streak = zone_analysis[zone]["streak_length"]
            if streak > 0:
                summary += f" ({zone}, {streak}p)"
            else:
                summary += f" ({zone})"

        # Extreme readings
        if zone_analysis.get("overbought", {}).get("extreme_reading"):
            summary += ". ⚠️ Extreme overbought (>90)"
        elif zone_analysis.get("oversold", {}).get("extreme_reading"):
            summary += ". ⚠️ Extreme oversold (<10)"

        # Money flow pressure
        pressure = money_flow_analysis.get("pressure", "balanced")
        flow_quality = money_flow_analysis.get("flow_quality", "")
        if "high_quality" in flow_quality:
            if pressure == "buying":
                summary += ". ✓ Strong buying pressure"
            elif pressure == "selling":
                summary += ". ⚠️ Strong selling pressure"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "positive" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE (money flow strengthening)"
            elif "negative" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE (money flow weakening)"

        # Acceleration/deceleration
        if momentum_analysis:
            accel = momentum_analysis.get("acceleration", 0)
            if abs(accel) > 2:
                if accel > 0:
                    summary += ". Money flow accelerating"
                else:
                    summary += ". Money flow decelerating"

        # Pattern analysis (exhaustion, double patterns)
        if pattern_analysis:
            if "exhaustion" in pattern_analysis:
                exh = pattern_analysis["exhaustion"]
                if "bullish" in exh.get("type", ""):
                    summary += ". ⚠️ Buying exhaustion"
                elif "bearish" in exh.get("type", ""):
                    summary += ". ✓ Selling exhaustion"

            if "double_pattern" in pattern_analysis:
                dp = pattern_analysis["double_pattern"]
                if "top" in dp.get("type", ""):
                    summary += ". ⚠️ Double top pattern"
                elif "bottom" in dp.get("type", ""):
                    summary += ". ✓ Double bottom pattern"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full MFI output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "mfi", "timeframe": timeframe, "timestamp": None,
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
        trend_data = context.get("trend", {})
        levels = full_output.get("levels", {})
        patterns_data = full_output.get("patterns", {})

        mfi_val = to_native(current.get("value"))

        # Zone based on MFI level (80/20 standard)
        if mfi_val is not None:
            if mfi_val >= 80: zone = "overbought"
            elif mfi_val <= 20: zone = "oversold"
            else: zone = "neutral"
        else:
            zone = "unknown"

        zone_periods = 0
        if zone == "overbought":
            zone_periods = to_native(levels.get("overbought", {}).get("streak_length", 0)) or 0
        elif zone == "oversold":
            zone_periods = to_native(levels.get("oversold", {}).get("streak_length", 0)) or 0

        # Pattern extraction
        pattern_codes = []
        if "divergence" in patterns_data:
            div = patterns_data.get("divergence")
            if div and isinstance(div, dict):
                if "positive" in div.get("type", ""): pattern_codes.append("divergence_bullish")
                elif "negative" in div.get("type", ""): pattern_codes.append("divergence_bearish")
        if "double_pattern" in patterns_data:
            dp = patterns_data.get("double_pattern")
            if dp and isinstance(dp, dict):
                if "top" in dp.get("type", ""): pattern_codes.append("double_top")
                elif "bottom" in dp.get("type", ""): pattern_codes.append("double_bottom")

        return {
            "indicator": "mfi",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(float(mfi_val), 2) if mfi_val else None,
            "value_secondary": None,
            "value_tertiary": None,
            "velocity": round(to_native(trend_data.get("velocity", 0)) or 0, 4),
            "rank": round(float(mfi_val) / 100.0, 3) if mfi_val else 0.5,
            "zone": zone,
            "zone_periods": zone_periods,
            "trend": trend_data.get("direction", "unknown"),
            "crossover_type": None,
            "crossover_periods_ago": None,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }