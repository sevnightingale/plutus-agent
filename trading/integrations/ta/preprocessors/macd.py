"""
MACD (Moving Average Convergence Divergence) Preprocessor.

Advanced MACD preprocessing with sophisticated analysis including crossover detection,
histogram analysis, zero line behavior, and divergence pattern recognition.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .base import BasePreprocessor


class MACDPreprocessor(BasePreprocessor):
    """Advanced MACD preprocessor with professional-grade analysis."""
    
    def preprocess(self, macd_line: pd.Series, signal_line: pd.Series, 
                  histogram: pd.Series, prices: pd.Series = None, **kwargs) -> Dict[str, Any]:
        """
        Advanced MACD preprocessing with sophisticated analysis.
        
        Args:
            macd_line: MACD line values
            signal_line: Signal line values
            histogram: MACD histogram values
            prices: Price series for divergence analysis (optional)
            
        Returns:
            Dictionary with comprehensive MACD analysis
        """
        # Capture original lengths before alignment
        orig_lengths = {
            "macd": len(macd_line),
            "signal": len(signal_line),
            "histogram": len(histogram)
        }
        if prices is not None:
            orig_lengths["prices"] = len(prices)

        # Clean and align all input series
        macd = pd.to_numeric(macd_line, errors="coerce")
        sig = pd.to_numeric(signal_line, errors="coerce")
        hist = pd.to_numeric(histogram, errors="coerce")
        frames = {"macd": macd, "signal": sig, "hist": hist}
        if prices is not None:
            frames["price"] = pd.to_numeric(prices, errors="coerce")

        df = pd.concat(frames, axis=1, join="inner").dropna()
        if len(df) < 5:
            return {"error": "Insufficient aligned data for MACD analysis"}

        macd, sig, hist = df["macd"], df["signal"], df["hist"]
        px = df["price"] if "price" in df else None

        current_macd = float(macd.iloc[-1])
        current_signal = float(sig.iloc[-1])
        current_histogram = float(hist.iloc[-1])
        
        # Generate proper timestamp
        if np.issubdtype(macd.index.dtype, np.datetime64):
            timestamp = macd.index[-1].isoformat() if hasattr(macd.index[-1], 'isoformat') else datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        # Crossover analysis
        crossover_analysis = self._analyze_macd_crossovers(macd, sig)

        # Histogram analysis
        histogram_analysis = self._analyze_histogram(hist)

        # Divergence analysis
        divergence = None
        if px is not None:
            divergence = self._detect_macd_price_divergence(macd, px)

        # Trend strength
        trend_strength = self._analyze_macd_trend_strength(macd, sig, hist)

        # Zero line analysis
        zero_line_analysis = self._analyze_zero_line_behavior(macd)
        
        return {
            "indicator": "MACD",
            "current": {
                "macd": round(current_macd, 4),
                "signal": round(current_signal, 4),
                "histogram": round(current_histogram, 4),
                "timestamp": timestamp
            },
            "trend": {
                "direction": "bullish" if current_macd > current_signal else "bearish",
                "strength": trend_strength["strength"],
                "momentum": histogram_analysis["momentum_direction"],
                "acceleration": histogram_analysis["acceleration"]
            },
            "patterns": {
                "crossovers": crossover_analysis,
                "divergence": divergence
            },
            "levels": {
                "zero_line": zero_line_analysis,
                "histogram": histogram_analysis
            },
            "evidence": {
                "data_quality": {
                    "original_periods": orig_lengths,
                    "aligned_periods": len(df),
                    "valid_data_percentage": round(len(df) / max(orig_lengths.values()) * 100, 1)
                },
                "calculation_notes": f"MACD analysis based on {len(df)} aligned data points"
            },
            "summary": self._generate_macd_summary(
                current_macd, current_signal, current_histogram,
                crossover_analysis, trend_strength,
                divergence, zero_line_analysis, histogram_analysis
            )
        }
    
    def _analyze_macd_crossovers(self, macd_line: pd.Series, signal_line: pd.Series) -> Dict[str, Any]:
        """Analyze MACD crossovers."""
        crossovers = []
        
        # Calculate volatility for realistic strength scaling
        lookback = min(50, len(macd_line))
        vol = float(macd_line.tail(lookback).std() + signal_line.tail(lookback).std()) or 1e-6

        for i in range(1, min(20, len(macd_line), len(signal_line))):
            prev_macd = macd_line.iloc[-(i+1)]
            curr_macd = macd_line.iloc[-i]
            prev_signal = signal_line.iloc[-(i+1)]
            curr_signal = signal_line.iloc[-i]

            # Calculate volatility-scaled strength
            strength = abs(curr_macd - curr_signal) / vol

            # Bullish crossover
            if prev_macd <= prev_signal and curr_macd > curr_signal:
                crossovers.append({
                    "type": "bullish_crossover",
                    "periods_ago": i,
                    "strength": round(strength, 3),
                    "strength_level": "high" if strength > 1.5 else ("medium" if strength > 0.7 else "low")
                })
            # Bearish crossover
            elif prev_macd >= prev_signal and curr_macd < curr_signal:
                crossovers.append({
                    "type": "bearish_crossover",
                    "periods_ago": i,
                    "strength": round(strength, 3),
                    "strength_level": "high" if strength > 1.5 else ("medium" if strength > 0.7 else "low")
                })
        
        return {
            "recent_crossovers": crossovers[:3],
            "latest_crossover": crossovers[0] if crossovers else None
        }
    
    def _analyze_histogram(self, histogram: pd.Series) -> Dict[str, Any]:
        """Analyze MACD histogram for momentum insights."""
        if len(histogram) < 3:
            return {
                "momentum_direction": "flat",
                "acceleration": 0.0,
                "zero_crossings_recent": 0,
                "histogram_strength": float(histogram.iloc[-1]) if len(histogram) else 0.0
            }
        
        current = histogram.iloc[-1]
        previous = histogram.iloc[-2]
        
        momentum_direction = "increasing" if current > previous else "decreasing"
        acceleration = current - previous
        
        # Histogram zero crossings
        zero_crossings = 0
        for i in range(1, min(10, len(histogram))):
            if (histogram.iloc[-i] > 0 and histogram.iloc[-(i+1)] <= 0) or \
               (histogram.iloc[-i] < 0 and histogram.iloc[-(i+1)] >= 0):
                zero_crossings += 1
        
        return {
            "momentum_direction": momentum_direction,
            "acceleration": round(acceleration, 4),
            "zero_crossings_recent": zero_crossings,
            "histogram_strength": abs(current)
        }
    
    def _analyze_zero_line_behavior(self, macd_line: pd.Series) -> Dict[str, Any]:
        """Analyze MACD behavior around zero line."""
        current = macd_line.iloc[-1]
        
        # Use finite values and handle zero case
        finite = macd_line.dropna()
        above_pct = (finite > 0).mean() * 100
        below_pct = (finite < 0).mean() * 100

        # Position classification
        position = "above" if current > 0 else ("below" if current < 0 else "at_zero")

        return {
            "current_position": position,
            "distance_from_zero": round(abs(current), 4),
            "time_above_zero_pct": round(above_pct, 1),
            "time_below_zero_pct": round(below_pct, 1)
        }
    
    def _analyze_macd_trend_strength(self, macd: pd.Series, signal: pd.Series, histogram: pd.Series) -> Dict[str, Any]:
        """Analyze MACD trend strength."""
        current_macd = macd.iloc[-1]
        current_signal = signal.iloc[-1]
        current_histogram = histogram.iloc[-1]
        
        # Relative histogram strength calculation
        lookback = min(50, len(histogram))
        hvol = max(1e-6, histogram.tail(lookback).std())
        strength = min(1.0, abs(current_histogram) / (2 * hvol))

        return {
            "strength": round(strength, 3)
        }
    
    def _detect_macd_price_divergence(self, macd: pd.Series, prices: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect MACD-price divergence."""
        if len(macd) < 15:
            return None

        # Look for divergence patterns using aligned data
        win = 10
        m_recent = macd.tail(win)
        p_recent = prices.tail(win)

        # Calculate relative prominence thresholds
        prom_m = max(1e-6, m_recent.std() * 0.8)
        prom_p = max(1e-6, p_recent.std() * 0.8)

        # Find peaks and troughs with scaled prominence
        macd_peaks = self._find_peaks(m_recent, prominence=prom_m)
        macd_troughs = self._find_troughs(m_recent, prominence=prom_m)
        price_peaks = self._find_peaks(p_recent, prominence=prom_p)
        price_troughs = self._find_troughs(p_recent, prominence=prom_p)
        
        # Bearish divergence: price higher highs, MACD lower highs
        if len(macd_peaks) >= 2 and len(price_peaks) >= 2:
            latest_macd_peak = macd_peaks[-1]
            prev_macd_peak = macd_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]
            
            if (latest_price_peak["value"] > prev_price_peak["value"] and
                latest_macd_peak["value"] < prev_macd_peak["value"]):
                return {
                    "type": "negative_divergence",
                    "description": "Price making higher highs while MACD making lower highs"
                }
        
        # Bullish divergence: price lower lows, MACD higher lows
        if len(macd_troughs) >= 2 and len(price_troughs) >= 2:
            latest_macd_trough = macd_troughs[-1]
            prev_macd_trough = macd_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]
            
            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_macd_trough["value"] > prev_macd_trough["value"]):
                return {
                    "type": "positive_divergence",
                    "description": "Price making lower lows while MACD making higher lows"
                }
        
        return None
    
    def _generate_macd_summary(self, macd: float, signal: float, histogram: float,
                              crossovers: Dict, trend_strength: Dict,
                              divergence: Dict = None, zero_line: Dict = None,
                              histogram_analysis: Dict = None) -> str:
        """Generate MACD summary with enriched signals."""
        trend = "bullish" if macd > signal else "bearish"
        momentum = "strengthening" if histogram > 0 else "weakening"

        summary = f"MACD {trend}, momentum {momentum}"

        # Zero line position (important context)
        if zero_line:
            position = zero_line.get("current_position", "")
            if position == "above":
                summary += " (above zero)"
            elif position == "below":
                summary += " (below zero)"

        # Recent crossovers
        if crossovers.get("latest_crossover"):
            cross = crossovers["latest_crossover"]
            if cross.get("periods_ago", 99) <= 5:
                cross_type = "bullish" if "bullish" in cross.get("type", "") else "bearish"
                strength = cross.get("strength_level", "")
                summary += f". {strength.capitalize() + ' ' if strength else ''}{cross_type} crossover {cross['periods_ago']}p ago"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "positive" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE"
            elif "negative" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE"

        # Histogram acceleration
        if histogram_analysis:
            accel = histogram_analysis.get("acceleration", 0)
            if abs(accel) > 0.001:  # Significant acceleration
                if accel > 0:
                    summary += ". Histogram accelerating"
                else:
                    summary += ". Histogram decelerating"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full MACD output to universal compact format."""
        if "error" in full_output:
            return {
                "indicator": "macd", "timeframe": timeframe, "timestamp": None,
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
        trend_data = full_output.get("trend", {})
        patterns_data = full_output.get("patterns", {})
        levels = full_output.get("levels", {})

        macd_val = to_native(current.get("macd"))
        signal_val = to_native(current.get("signal"))
        histogram_val = to_native(current.get("histogram"))

        # Zone based on MACD vs signal
        if macd_val is not None and signal_val is not None:
            zone = "bullish" if macd_val > signal_val else "bearish"
        else:
            zone = "unknown"

        # Extract crossover
        crossovers = patterns_data.get("crossovers", {})
        latest_cross = crossovers.get("latest_crossover")
        crossover_type = None
        crossover_periods_ago = None
        if latest_cross:
            cross_type = latest_cross.get("type", "")
            crossover_type = "crossover_bullish" if "bullish" in cross_type else "crossover_bearish"
            crossover_periods_ago = to_native(latest_cross.get("periods_ago"))

        # Extract patterns
        pattern_codes = []
        if patterns_data.get("divergence"):
            div_type = patterns_data["divergence"].get("type", "")
            if "positive" in div_type: pattern_codes.append("divergence_bullish")
            elif "negative" in div_type: pattern_codes.append("divergence_bearish")

        hist_analysis = levels.get("histogram", {})
        if hist_analysis.get("momentum_direction") == "increasing":
            pattern_codes.append("momentum_rising")
        elif hist_analysis.get("momentum_direction") == "decreasing":
            pattern_codes.append("momentum_falling")

        return {
            "indicator": "macd",
            "timeframe": timeframe,
            "timestamp": current.get("timestamp"),
            "value": round(macd_val, 4) if macd_val else None,
            "value_secondary": round(signal_val, 4) if signal_val else None,
            "value_tertiary": round(histogram_val, 4) if histogram_val else None,
            "velocity": round(to_native(hist_analysis.get("acceleration", 0)) or 0, 4),
            "rank": 0.0,
            "zone": zone,
            "zone_periods": 0,
            "trend": trend_data.get("direction", "unknown"),
            "crossover_type": crossover_type,
            "crossover_periods_ago": crossover_periods_ago,
            "patterns": pattern_codes,
            "analysis": full_output.get("summary", "")
        }