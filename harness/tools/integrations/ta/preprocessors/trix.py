"""
TRIX Preprocessor.

Advanced TRIX preprocessing with triple exponential smoothing analysis,
momentum turning points detection, and comprehensive market state description.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class TRIXPreprocessor(BasePreprocessor):
    """Advanced TRIX preprocessor with professional-grade momentum analysis."""

    def preprocess(self, trix: pd.Series, trix_signal: pd.Series = None,
                  prices: pd.Series = None, length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced TRIX preprocessing with sophisticated momentum analysis.

        Provides rich market state description following analysis-only pattern.
        No signals or confidence - pure market context for Decision LLM.

        Args:
            trix: TRIX line values (percentage rate of change of triple EMA)
            trix_signal: TRIX signal line (optional, EMA of TRIX)
            prices: Price series for divergence analysis (optional)
            length: TRIX calculation period

        Returns:
            Dictionary with comprehensive TRIX analysis
        """
        # Clean and align data
        trix = pd.to_numeric(trix, errors="coerce").dropna()
        sig = None if trix_signal is None else pd.to_numeric(trix_signal, errors="coerce").dropna()
        px = None if prices is None else pd.to_numeric(prices, errors="coerce").dropna()

        # Align TRIX & signal for all TRIX-only analytics
        cols = {"trix": trix}
        if sig is not None:
            cols["signal"] = sig
        df = pd.concat(cols, axis=1, join="inner").dropna()

        if len(df) < 5:
            return {"error": "Insufficient data for TRIX analysis"}

        trix, sig = df["trix"], (df["signal"] if "signal" in df else None)

        # Get timestamp from series index
        ts = (trix.index[-1].isoformat()
              if is_datetime64_any_dtype(trix.index)
              else datetime.now(timezone.utc).isoformat())

        # Period-driven windows
        vel_win = max(2, length // 5)
        acc_win = max(3, length // 3)
        tp_win = max(10, length)
        div_win = max(10, length)

        current_trix = float(trix.iloc[-1])
        current_signal = float(sig.iloc[-1]) if sig is not None else None

        # Momentum analysis (period-driven)
        momentum_analysis = self._analyze_trix_momentum(trix, vel_win, acc_win)

        # Zero line analysis
        zero_line_analysis = self._analyze_trix_zero_line(trix)

        # Signal line analysis (if available)
        signal_line_analysis = {}
        if sig is not None:
            signal_line_analysis = self._analyze_trix_signal_crossovers(trix, sig)

        # Turning points analysis (period-driven)
        turning_points = self._analyze_trix_turning_points(trix, tp_win)

        # Trend analysis
        trend_analysis = self._analyze_trend(trix)

        # Divergence analysis (if prices available)
        divergence = None
        if px is not None:
            divergence = self._detect_trix_price_divergence(trix, px, div_win)

        # Pattern detection
        patterns = self._detect_trix_patterns(trix, sig, length)

        # Include divergence in patterns if detected
        if divergence is not None:
            patterns["divergence"] = divergence

        # Level analysis
        level_analysis = self._analyze_key_levels(trix, [0])

        # Recent extremes
        extremes = self._find_recent_extremes(trix, max(20, length))

        # Generate summary
        summary = self._generate_trix_summary(
            current_trix, current_signal, momentum_analysis, zero_line_analysis
        )

        return {
            "indicator": "TRIX",
            "length": length,
            "current": {
                "trix": round(current_trix, 6),
                "signal": round(current_signal, 6) if current_signal is not None else None,
                "histogram": round(current_trix - current_signal, 6) if current_signal is not None else None,
                "timestamp": ts
            },
            "context": {
                "trend": {
                    "direction": trend_analysis["direction"],
                    "strength": round(trend_analysis["strength"], 3),
                    "velocity": round(momentum_analysis["velocity"], 6),
                    "acceleration": round(momentum_analysis["acceleration"], 6)
                },
                "momentum": {
                    "direction": momentum_analysis["direction"],
                    "strength_level": momentum_analysis["strength_level"],
                    "persistence": round(momentum_analysis["persistence"], 3)
                },
                "volatility": round(trix.std(), 6)
            },
            "levels": {
                "zero_line": {
                    "position": zero_line_analysis["position"],
                    "above_zero_pct": zero_line_analysis["above_zero_pct"],
                    "below_zero_pct": zero_line_analysis["below_zero_pct"],
                    "recent_crossings": zero_line_analysis.get("recent_crossings", [])
                },
                "signal_line": signal_line_analysis,
                "key_levels": [0],
                "recent_crossovers": level_analysis.get("recent_crossovers", [])
            },
            "extremes": {
                "recent_high": {
                    "value": round(extremes["high_value"], 6),
                    "periods_ago": extremes["high_periods_ago"],
                    "significance": extremes["high_significance"]
                },
                "recent_low": {
                    "value": round(extremes["low_value"], 6),
                    "periods_ago": extremes["low_periods_ago"],
                    "significance": extremes["low_significance"]
                }
            },
            "patterns": patterns,
            "evidence": {
                "data_quality": {
                    "aligned_periods": len(trix),
                    "had_signal": sig is not None,
                    "had_prices": px is not None,
                    "windows_used": {
                        "velocity": vel_win,
                        "acceleration": acc_win,
                        "turning_points": tp_win,
                        "divergence": div_win
                    }
                },
                "calculation_notes": f"TRIX analysis based on {len(trix)} aligned periods with length={length}"
            },
            "summary": summary
        }

    def _analyze_trix_momentum(self, trix: pd.Series, vel_win: int = 3, acc_win: int = 5) -> Dict[str, Any]:
        """Analyze TRIX momentum characteristics."""
        current_trix = trix.iloc[-1]

        # Momentum direction
        if current_trix > 0:
            momentum_direction = "bullish"
        elif current_trix < 0:
            momentum_direction = "bearish"
        else:
            momentum_direction = "neutral"

        # Momentum strength (TRIX values are typically small due to triple smoothing)
        momentum_strength = abs(current_trix)

        # Momentum strength classification
        trix_std = trix.std()
        if momentum_strength > trix_std * 2:
            strength_level = "very_strong"
        elif momentum_strength > trix_std:
            strength_level = "strong"
        elif momentum_strength > trix_std * 0.5:
            strength_level = "moderate"
        else:
            strength_level = "weak"

        # Momentum acceleration (period-driven windows)
        trix_velocity = self._calculate_velocity(trix, vel_win)
        trix_acceleration = self._calculate_acceleration(trix, acc_win)

        # Momentum persistence
        persistence = self._calculate_trix_momentum_persistence(trix)

        return {
            "direction": momentum_direction,
            "strength": round(momentum_strength, 6),
            "strength_level": strength_level,
            "velocity": round(trix_velocity, 6),
            "acceleration": round(trix_acceleration, 6),
            "persistence": round(persistence, 3)
        }

    def _calculate_trix_momentum_persistence(self, trix: pd.Series) -> float:
        """Calculate persistence of TRIX momentum direction."""
        if len(trix) < 5:
            return 0.5

        recent_trix = trix.iloc[-5:]
        current_direction = "positive" if trix.iloc[-1] > 0 else "negative"

        same_direction = sum(1 for val in recent_trix if
                           (val > 0 and current_direction == "positive") or
                           (val < 0 and current_direction == "negative"))

        return same_direction / len(recent_trix)

    def _analyze_trix_zero_line(self, trix: pd.Series) -> Dict[str, Any]:
        """Analyze TRIX behavior around zero line."""
        # Position relative to zero with epsilon tolerance
        finite = trix.dropna()
        eps = max(1e-8, finite.std() * 0.02)
        current_trix = float(finite.iloc[-1])

        position = "above_zero" if current_trix > eps else ("below_zero" if current_trix < -eps else "at_zero")

        # Time above/below zero (vectorized)
        above_zero = (finite > eps).sum()
        below_zero = (finite < -eps).sum()
        total = len(finite)

        # Zero line crossings
        crossings = []
        for i in range(1, min(15, len(trix))):
            prev_trix = trix.iloc[-(i+1)]
            curr_trix = trix.iloc[-i]

            # Bullish zero crossing
            if prev_trix <= 0 and curr_trix > 0:
                crossings.append({
                    "type": "bullish_zero_cross",
                    "periods_ago": i,
                    "value": round(curr_trix, 6)
                })
            # Bearish zero crossing
            elif prev_trix >= 0 and curr_trix < 0:
                crossings.append({
                    "type": "bearish_zero_cross",
                    "periods_ago": i,
                    "value": round(curr_trix, 6)
                })

        return {
            "position": position,
            "above_zero_pct": round((above_zero / total) * 100, 1),
            "below_zero_pct": round((below_zero / total) * 100, 1),
            "recent_crossings": crossings[:5],
            "latest_crossing": crossings[0] if crossings else None
        }

    def _analyze_trix_signal_crossovers(self, trix: pd.Series, trix_signal: pd.Series) -> Dict[str, Any]:
        """Analyze TRIX signal line crossovers with safe indexing."""
        n = min(15, len(trix), len(trix_signal))
        crossovers = []

        # Calculate spread scaling for strength normalization
        spread = (trix - trix_signal).tail(max(20, n))
        scale = float(spread.std() or 1e-6)

        for i in range(1, n):
            prev_trix = trix.iloc[-(i+1)]
            curr_trix = trix.iloc[-i]
            prev_signal = trix_signal.iloc[-(i+1)]
            curr_signal = trix_signal.iloc[-i]

            # Bullish crossover (TRIX crosses above signal)
            if prev_trix <= prev_signal and curr_trix > curr_signal:
                crossovers.append({
                    "type": "bullish_crossover",
                    "periods_ago": i,
                    "trix_value": round(curr_trix, 6),
                    "signal_value": round(curr_signal, 6),
                    "strength": round(abs(curr_trix - curr_signal) / scale, 3)
                })
            # Bearish crossover (TRIX crosses below signal)
            elif prev_trix >= prev_signal and curr_trix < curr_signal:
                crossovers.append({
                    "type": "bearish_crossover",
                    "periods_ago": i,
                    "trix_value": round(curr_trix, 6),
                    "signal_value": round(curr_signal, 6),
                    "strength": round(abs(curr_trix - curr_signal) / scale, 3)
                })

        return {
            "recent_crossovers": crossovers[:5],
            "latest_crossover": crossovers[0] if crossovers else None,
            "crossover_frequency": len(crossovers) / max(1, n - 1)
        }

    def _analyze_trix_turning_points(self, trix: pd.Series, tp_win: int = 20) -> Dict[str, Any]:
        """Analyze TRIX turning points (peaks and troughs)."""
        if len(trix) < 10:
            return {}

        # Scale prominence on recent window, not whole series
        win = min(tp_win, len(trix))
        seg = trix.tail(win)
        prominence = max(seg.std() * 0.6, 1e-6)

        peaks = self._find_peaks(seg, prominence=prominence)
        troughs = self._find_troughs(seg, prominence=prominence)

        # Recent turning points
        recent_peaks = [p for p in peaks if p["periods_ago"] <= 10]
        recent_troughs = [t for t in troughs if t["periods_ago"] <= 10]

        # Latest turning point
        latest_peak = peaks[-1] if peaks else None
        latest_trough = troughs[-1] if troughs else None

        if latest_peak and latest_trough:
            if latest_peak["periods_ago"] < latest_trough["periods_ago"]:
                latest_turning_point = {
                    "type": "peak",
                    "value": round(latest_peak["value"], 6),
                    "periods_ago": latest_peak["periods_ago"]
                }
            else:
                latest_turning_point = {
                    "type": "trough",
                    "value": round(latest_trough["value"], 6),
                    "periods_ago": latest_trough["periods_ago"]
                }
        else:
            latest_turning_point = None

        return {
            "total_peaks": len(peaks),
            "total_troughs": len(troughs),
            "recent_peaks": recent_peaks,
            "recent_troughs": recent_troughs,
            "latest_turning_point": latest_turning_point
        }

    def _detect_trix_price_divergence(self, trix: pd.Series, prices: pd.Series, div_win: int = 15) -> Optional[Dict[str, Any]]:
        """Detect TRIX-price divergence patterns."""
        # Align TRIX and price data
        df = pd.concat({"trix": trix, "px": prices}, axis=1, join="inner").dropna()
        if len(df) < max(15, div_win):
            return None

        # Use recent window for analysis
        r = df.tail(min(div_win, len(df)))

        # Scaled prominence for both series
        prom_t = max(r["trix"].std() * 0.6, 1e-6)
        prom_p = max(r["px"].std() * 0.6, 1e-6)

        trix_peaks = self._find_peaks(r["trix"], prominence=prom_t)
        trix_troughs = self._find_troughs(r["trix"], prominence=prom_t)
        price_peaks = self._find_peaks(r["px"], prominence=prom_p)
        price_troughs = self._find_troughs(r["px"], prominence=prom_p)

        # Bullish divergence: price lower lows, TRIX higher lows
        if len(trix_troughs) >= 2 and len(price_troughs) >= 2:
            latest_trix_trough = trix_troughs[-1]
            prev_trix_trough = trix_troughs[-2]
            latest_price_trough = price_troughs[-1]
            prev_price_trough = price_troughs[-2]

            if (latest_price_trough["value"] < prev_price_trough["value"] and
                latest_trix_trough["value"] > prev_trix_trough["value"]):
                return {
                    "type": "bullish_divergence",
                    "description": "Price making lower lows while TRIX making higher lows",
                    "trough_comparison": {
                        "price_change": round(latest_price_trough["value"] - prev_price_trough["value"], 4),
                        "trix_change": round(latest_trix_trough["value"] - prev_trix_trough["value"], 6)
                    }
                }

        # Bearish divergence: price higher highs, TRIX lower highs
        if len(trix_peaks) >= 2 and len(price_peaks) >= 2:
            latest_trix_peak = trix_peaks[-1]
            prev_trix_peak = trix_peaks[-2]
            latest_price_peak = price_peaks[-1]
            prev_price_peak = price_peaks[-2]

            if (latest_price_peak["value"] > prev_price_peak["value"] and
                latest_trix_peak["value"] < prev_trix_peak["value"]):
                return {
                    "type": "bearish_divergence",
                    "description": "Price making higher highs while TRIX making lower highs",
                    "peak_comparison": {
                        "price_change": round(latest_price_peak["value"] - prev_price_peak["value"], 4),
                        "trix_change": round(latest_trix_peak["value"] - prev_trix_peak["value"], 6)
                    }
                }

        return None

    def _detect_trix_patterns(self, trix: pd.Series, sig: Optional[pd.Series], length: int = 14) -> Dict[str, Any]:
        """Detect TRIX patterns and formations."""
        patterns = {}

        # Zero-line momentum patterns
        if len(trix) >= 10:
            vel_win = max(2, length // 5)
            velocity = self._calculate_velocity(trix, vel_win)
            current = float(trix.iloc[-1])

            # Strong momentum around zero line
            if abs(velocity) > trix.std() * 0.5:
                patterns["momentum"] = {
                    "type": f"strong_{'rising' if velocity > 0 else 'falling'}_momentum",
                    "velocity": round(velocity, 6),
                    "near_zero": abs(current) < trix.std() * 0.1,
                    "description": f"Strong {'rising' if velocity > 0 else 'falling'} TRIX momentum"
                }

        # Signal line convergence/divergence patterns
        if sig is not None and len(trix) >= length:
            histogram = trix - sig
            hist_velocity = self._calculate_velocity(histogram, max(2, length // 5))

            if abs(hist_velocity) > histogram.std() * 0.3:
                patterns["histogram_momentum"] = {
                    "type": f"histogram_{'expanding' if hist_velocity > 0 else 'contracting'}",
                    "velocity": round(hist_velocity, 6),
                    "description": f"TRIX histogram {'expanding' if hist_velocity > 0 else 'contracting'}"
                }

        return patterns

    def _generate_trix_summary(self, current_trix: float, current_signal: Optional[float],
                              momentum_analysis: Dict, zero_line_analysis: Dict,
                              crossover_analysis: Dict = None, divergence: Dict = None,
                              pattern_analysis: Dict = None) -> str:
        """Generate human-readable TRIX summary with enriched signals."""
        momentum_direction = momentum_analysis.get("direction", "neutral")
        strength_level = momentum_analysis.get("strength_level", "weak")
        position = zero_line_analysis.get("position", "at_zero")

        summary = f"TRIX={current_trix:.6f} ({strength_level} {momentum_direction})"

        # Zero line position
        if position == "above_zero":
            summary += " bullish"
        elif position == "below_zero":
            summary += " bearish"

        # Signal line crossover
        if current_signal is not None and crossover_analysis:
            latest = crossover_analysis.get("latest_crossover")
            if latest and latest.get("periods_ago", 99) <= 5:
                cross_type = "bullish" if "bullish" in latest.get("type", "") else "bearish"
                summary += f". ✓ {cross_type.capitalize()} signal crossover {latest['periods_ago']}p ago"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "bullish" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE"
            elif "bearish" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE"

        # Histogram momentum
        if pattern_analysis and "histogram_momentum" in pattern_analysis:
            hm = pattern_analysis["histogram_momentum"]
            if "expanding" in hm.get("type", ""):
                summary += ". Momentum expanding"
            elif "contracting" in hm.get("type", ""):
                summary += ". Momentum contracting"

        # Zero line crossover
        if zero_line_analysis:
            recent_cross = zero_line_analysis.get("recent_crosses", [])
            if recent_cross and recent_cross[0].get("periods_ago", 99) <= 5:
                cross = recent_cross[0]
                if "bullish" in cross.get("type", ""):
                    summary += ". ✓ Bullish zero cross"
                else:
                    summary += ". ⚠️ Bearish zero cross"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full TRIX output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "trix", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        momentum = full_output.get("momentum_analysis", {})
        zero_line = full_output.get("zero_line_analysis", {})
        trix_val = to_native(current.get("trix"))
        signal_val = to_native(current.get("signal"))
        direction = momentum.get("direction", "neutral")
        position = zero_line.get("position", "at_zero")
        zone = "bullish" if position == "above_zero" else "bearish" if position == "below_zero" else "neutral"
        patterns = []
        if momentum.get("strength_level") == "strong":
            patterns.append("momentum_strong_up" if direction == "bullish" else "momentum_strong_down" if direction == "bearish" else "")
        patterns = [p for p in patterns if p]
        return {"indicator": "trix", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(trix_val), 6) if trix_val else None,
                "value_secondary": round(float(signal_val), 6) if signal_val else None, "value_tertiary": None,
                "velocity": round(to_native(momentum.get("acceleration", 0)) or 0, 6),
                "rank": 0.5, "zone": zone, "zone_periods": 0,
                "trend": "rising" if direction == "bullish" else "falling" if direction == "bearish" else "neutral",
                "crossover_type": None, "crossover_periods_ago": None,
                "patterns": patterns, "analysis": full_output.get("summary", "")}
