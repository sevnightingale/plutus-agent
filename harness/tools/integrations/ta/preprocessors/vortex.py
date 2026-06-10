"""
Vortex Preprocessor.

Advanced Vortex preprocessing with directional movement analysis,
VI+ and VI- crossover detection, and comprehensive market state description.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class VortexPreprocessor(BasePreprocessor):
    """Advanced Vortex preprocessor with professional-grade directional analysis."""

    def preprocess(self, vi_plus: pd.Series, vi_minus: pd.Series,
                  prices: pd.Series = None, length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced Vortex preprocessing with sophisticated directional analysis.

        Provides rich market state description following analysis-only pattern.
        No signals or confidence - pure market context for Decision LLM.

        Args:
            vi_plus: VI+ values (positive vortex movement)
            vi_minus: VI- values (negative vortex movement)
            prices: Price series for additional analysis (optional)
            length: Vortex calculation period

        Returns:
            Dictionary with comprehensive Vortex analysis
        """
        # Clean and align data
        vi_p = pd.to_numeric(vi_plus, errors="coerce").dropna()
        vi_m = pd.to_numeric(vi_minus, errors="coerce").dropna()
        df = pd.concat({"vi_p": vi_p, "vi_m": vi_m}, axis=1, join="inner").dropna()

        if len(df) < 5:
            return {"error": "Insufficient data for Vortex analysis"}

        vi_p, vi_m = df["vi_p"], df["vi_m"]

        # Get timestamp from series index
        ts = (vi_p.index[-1].isoformat()
              if is_datetime64_any_dtype(vi_p.index)
              else datetime.now(timezone.utc).isoformat())

        # Length-driven windows
        vel_win = max(2, length // 5)
        persistence_win = min(max(5, length // 3), len(vi_p))
        cross_scan = min(length, len(vi_p))
        div_win = max(10, length)

        current_vi_plus = float(vi_p.iloc[-1])
        current_vi_minus = float(vi_m.iloc[-1])

        # Crossover analysis
        crossover_analysis = self._analyze_vortex_crossovers(vi_p, vi_m, cross_scan)

        # Directional dominance analysis
        dominance_analysis = self._analyze_directional_dominance(vi_p, vi_m, persistence_win)

        # Spread analysis
        spread_analysis = self._analyze_vortex_spread(vi_p, vi_m, length)

        # Trend strength analysis
        trend_strength = self._analyze_vortex_trend_strength(vi_p, vi_m)

        # Momentum analysis
        momentum_analysis = self._analyze_vortex_momentum(vi_p, vi_m, vel_win)

        # One-line analysis (threshold levels)
        one_line_analysis = self._analyze_one_line_levels(vi_p, vi_m, cross_scan)

        # Trend analysis
        trend_analysis = self._analyze_trend(vi_p - vi_m)  # Use spread for trend

        # Divergence analysis (if prices available)
        divergence = None
        if prices is not None:
            divergence = self._detect_vortex_price_divergence(vi_p, vi_m, prices, div_win)

        # Pattern detection
        patterns = self._detect_vortex_patterns(vi_p, vi_m, length)

        # Include divergence in patterns if detected
        if divergence is not None:
            patterns["divergence"] = divergence

        # Level analysis
        level_analysis = self._analyze_key_levels(vi_p, [1.0])

        # Recent extremes for both VI+ and VI-
        extremes_plus = self._find_recent_extremes(vi_p, max(20, length))
        extremes_minus = self._find_recent_extremes(vi_m, max(20, length))

        # Generate summary
        summary = self._generate_vortex_summary(
            current_vi_plus, current_vi_minus, crossover_analysis, dominance_analysis
        )

        return {
            "indicator": "Vortex",
            "length": length,
            "current": {
                "vi_plus": round(current_vi_plus, 4),
                "vi_minus": round(current_vi_minus, 4),
                "spread": round(current_vi_plus - current_vi_minus, 4),
                "dominant": "VI+" if current_vi_plus > current_vi_minus else "VI-",
                "timestamp": ts
            },
            "context": {
                "trend": {
                    "direction": trend_analysis["direction"],
                    "strength": round(trend_analysis["strength"], 3),
                    "velocity": round(momentum_analysis["spread_velocity"], 6),
                    "acceleration": round(momentum_analysis.get("spread_acceleration", 0), 6)
                },
                "dominance": {
                    "current": dominance_analysis["current_dominant"],
                    "strength": round(dominance_analysis["dominance_strength"], 4),
                    "persistence": round(dominance_analysis["persistence"], 3)
                },
                "volatility": round((vi_p - vi_m).std(), 4)
            },
            "levels": {
                "one_line": {
                    "vi_plus_vs_one": one_line_analysis["vi_plus_vs_one"],
                    "vi_minus_vs_one": one_line_analysis["vi_minus_vs_one"],
                    "plus_above_one_pct": one_line_analysis["plus_above_one_pct"],
                    "minus_above_one_pct": one_line_analysis["minus_above_one_pct"],
                    "recent_crosses": {
                        "plus_crosses": one_line_analysis.get("plus_one_crosses", []),
                        "minus_crosses": one_line_analysis.get("minus_one_crosses", [])
                    }
                },
                "vi_crossovers": crossover_analysis.get("recent_crossovers", []),
                "key_levels": [1.0],
                "key_level_crosses": level_analysis.get("recent_crossovers", [])
            },
            "extremes": {
                "vi_plus_extremes": {
                    "recent_high": {
                        "value": round(extremes_plus["high_value"], 4),
                        "periods_ago": extremes_plus["high_periods_ago"],
                        "significance": extremes_plus["high_significance"]
                    },
                    "recent_low": {
                        "value": round(extremes_plus["low_value"], 4),
                        "periods_ago": extremes_plus["low_periods_ago"],
                        "significance": extremes_plus["low_significance"]
                    }
                },
                "vi_minus_extremes": {
                    "recent_high": {
                        "value": round(extremes_minus["high_value"], 4),
                        "periods_ago": extremes_minus["high_periods_ago"],
                        "significance": extremes_minus["high_significance"]
                    },
                    "recent_low": {
                        "value": round(extremes_minus["low_value"], 4),
                        "periods_ago": extremes_minus["low_periods_ago"],
                        "significance": extremes_minus["low_significance"]
                    }
                }
            },
            "patterns": patterns,
            "evidence": {
                "data_quality": {
                    "aligned_periods": len(vi_p),
                    "period_used": length,
                    "had_prices": prices is not None,
                    "windows_used": {
                        "velocity": vel_win,
                        "persistence": persistence_win,
                        "crossover_scan": cross_scan,
                        "divergence": div_win
                    }
                },
                "calculation_notes": f"Vortex analysis based on {len(vi_p)} aligned VI+/VI- periods"
            },
            "summary": summary
        }

    def _analyze_vortex_crossovers(self, vi_plus: pd.Series, vi_minus: pd.Series, cross_scan: int = 20) -> Dict[str, Any]:
        """Analyze VI+/VI- crossovers with safe indexing."""
        n = min(cross_scan, len(vi_plus), len(vi_minus))
        crossovers = []

        for i in range(1, n):
            prev_plus = vi_plus.iloc[-(i+1)]
            curr_plus = vi_plus.iloc[-i]
            prev_minus = vi_minus.iloc[-(i+1)]
            curr_minus = vi_minus.iloc[-i]

            # Bullish crossover (VI+ crosses above VI-)
            if prev_plus <= prev_minus and curr_plus > curr_minus:
                crossovers.append({
                    "type": "bullish_crossover",
                    "periods_ago": i,
                    "vi_plus": round(curr_plus, 4),
                    "vi_minus": round(curr_minus, 4),
                    "strength": round(curr_plus - curr_minus, 3),
                    "crossover_level": round((curr_plus + curr_minus) / 2, 4)
                })

            # Bearish crossover (VI+ crosses below VI-)
            elif prev_plus >= prev_minus and curr_plus < curr_minus:
                crossovers.append({
                    "type": "bearish_crossover",
                    "periods_ago": i,
                    "vi_plus": round(curr_plus, 4),
                    "vi_minus": round(curr_minus, 4),
                    "strength": round(curr_minus - curr_plus, 3),
                    "crossover_level": round((curr_plus + curr_minus) / 2, 4)
                })

        return {
            "recent_crossovers": crossovers[:5],
            "latest_crossover": crossovers[0] if crossovers else None,
            "crossover_frequency": len(crossovers) / max(1, n - 1)
        }

    def _analyze_directional_dominance(self, vi_plus: pd.Series, vi_minus: pd.Series, persistence_win: int = 5) -> Dict[str, Any]:
        """Analyze which direction is dominant."""
        current_plus = vi_plus.iloc[-1]
        current_minus = vi_minus.iloc[-1]

        # Current dominance
        if current_plus > current_minus:
            current_dominant = "VI_plus"
            dominance_strength = current_plus - current_minus
        else:
            current_dominant = "VI_minus"
            dominance_strength = current_minus - current_plus

        # Historical dominance (vectorized on cleaned data)
        plus_dominant = (vi_plus > vi_minus).sum()
        minus_dominant = (vi_plus <= vi_minus).sum()
        total_periods = len(vi_plus)

        # Dominance persistence (length-driven window)
        persistence = self._calculate_dominance_persistence(vi_plus, vi_minus, persistence_win)

        # Average dominance strength
        spreads = vi_plus - vi_minus
        avg_spread = spreads.mean()
        max_spread = spreads.max()
        min_spread = spreads.min()

        return {
            "current_dominant": current_dominant,
            "dominance_strength": round(dominance_strength, 4),
            "vi_plus_dominant_pct": round((plus_dominant / total_periods) * 100, 1),
            "vi_minus_dominant_pct": round((minus_dominant / total_periods) * 100, 1),
            "persistence": round(persistence, 3),
            "avg_spread": round(avg_spread, 4),
            "max_spread": round(max_spread, 4),
            "min_spread": round(min_spread, 4)
        }

    def _calculate_dominance_persistence(self, vi_plus: pd.Series, vi_minus: pd.Series, persistence_win: int = 5) -> float:
        """Calculate persistence of current dominance."""
        if len(vi_plus) < 2:
            return 0.5

        current_dominant = "plus" if vi_plus.iloc[-1] > vi_minus.iloc[-1] else "minus"
        recent_periods = min(persistence_win, len(vi_plus))

        consistent_periods = 0
        for i in range(recent_periods):
            idx = -(i + 1)
            period_dominant = "plus" if vi_plus.iloc[idx] > vi_minus.iloc[idx] else "minus"
            if period_dominant == current_dominant:
                consistent_periods += 1

        return consistent_periods / recent_periods

    def _analyze_vortex_spread(self, vi_plus: pd.Series, vi_minus: pd.Series, length: int = 14) -> Dict[str, Any]:
        """Analyze spread between VI+ and VI-."""
        spread = vi_plus - vi_minus
        current_spread = spread.iloc[-1]

        # Spread statistics with zero-division guards
        mean_spread = spread.mean()
        std_spread = float(spread.std())
        std_spread = max(1e-6, std_spread)  # Guard zero std

        # Spread classification with tolerance
        if abs(current_spread) > abs(mean_spread) + std_spread:
            spread_level = "extreme"
        elif abs(current_spread) > abs(mean_spread):
            spread_level = "elevated"
        else:
            spread_level = "normal"

        # Spread momentum (length-driven window)
        spread_velocity = self._calculate_velocity(spread, max(2, length // 5))

        return {
            "current_spread": round(current_spread, 4),
            "mean_spread": round(mean_spread, 4),
            "std_spread": round(std_spread, 4),
            "spread_level": spread_level,
            "spread_momentum": round(spread_velocity, 6),
            "momentum_direction": "expanding" if spread_velocity > 0 and current_spread > 0 else "contracting" if spread_velocity < 0 and current_spread > 0 else "reversing" if spread_velocity != 0 else "stable"
        }

    def _analyze_vortex_trend_strength(self, vi_plus: pd.Series, vi_minus: pd.Series) -> Dict[str, Any]:
        """Analyze trend strength using Vortex indicators."""
        current_plus = vi_plus.iloc[-1]
        current_minus = vi_minus.iloc[-1]

        # Safe denominator function
        def _den(x: float) -> float:
            return max(1e-12, abs(float(x)))

        # Trend direction with safe ratio calculation
        if current_plus > current_minus:
            trend_direction = "bullish"
            strength_ratio = current_plus / _den(current_minus)
        else:
            trend_direction = "bearish"
            strength_ratio = current_minus / _den(current_plus)

        # Strength classification
        if strength_ratio > 1.2:
            strength_level = "strong"
        elif strength_ratio > 1.1:
            strength_level = "moderate"
        elif strength_ratio > 1.05:
            strength_level = "weak"
        else:
            strength_level = "very_weak"

        # Both indicators above 1.0 (strong trending market)
        both_above_one = current_plus > 1.0 and current_minus > 1.0

        return {
            "direction": trend_direction,
            "strength_ratio": round(strength_ratio, 4),
            "strength_level": strength_level,
            "both_above_one": both_above_one,
            "market_condition": "strong_trending" if both_above_one else "weak_trending"
        }

    def _analyze_vortex_momentum(self, vi_plus: pd.Series, vi_minus: pd.Series, vel_win: int = 3) -> Dict[str, Any]:
        """Analyze momentum of Vortex indicators."""
        if len(vi_plus) < 5:
            return {}

        # Individual momentum (length-driven window)
        plus_momentum = self._calculate_velocity(vi_plus, vel_win)
        minus_momentum = self._calculate_velocity(vi_minus, vel_win)

        # Spread momentum
        spread = vi_plus - vi_minus
        spread_velocity = self._calculate_velocity(spread, vel_win)
        spread_acceleration = self._calculate_acceleration(spread, max(3, vel_win + 1))

        # Relative momentum
        if abs(plus_momentum) > abs(minus_momentum):
            dominant_momentum = "VI_plus"
        else:
            dominant_momentum = "VI_minus"

        # Momentum alignment
        if plus_momentum * minus_momentum > 0:
            momentum_alignment = "aligned"
        else:
            momentum_alignment = "divergent"

        return {
            "vi_plus_momentum": round(plus_momentum, 6),
            "vi_minus_momentum": round(minus_momentum, 6),
            "spread_velocity": round(spread_velocity, 6),
            "spread_acceleration": round(spread_acceleration, 6),
            "dominant_momentum": dominant_momentum,
            "momentum_alignment": momentum_alignment,
            "momentum_interpretation": self._interpret_momentum_alignment(momentum_alignment, plus_momentum, minus_momentum)
        }

    def _interpret_momentum_alignment(self, alignment: str, plus_mom: float, minus_mom: float) -> str:
        """Interpret momentum alignment patterns."""
        if alignment == "aligned":
            if plus_mom > 0 and minus_mom > 0:
                return "both_strengthening"
            elif plus_mom < 0 and minus_mom < 0:
                return "both_weakening"
            else:
                return "both_stable"
        else:
            if plus_mom > 0 and minus_mom < 0:
                return "vi_plus_strengthening_minus_weakening"
            elif plus_mom < 0 and minus_mom > 0:
                return "vi_minus_strengthening_plus_weakening"
            else:
                return "mixed_momentum"

    def _analyze_one_line_levels(self, vi_plus: pd.Series, vi_minus: pd.Series, cross_scan: int = 10) -> Dict[str, Any]:
        """Analyze behavior around 1.0 threshold levels."""
        current_plus = vi_plus.iloc[-1]
        current_minus = vi_minus.iloc[-1]

        # Current position relative to 1.0
        plus_vs_one = "above" if current_plus > 1.0 else ("below" if current_plus < 1.0 else "at")
        minus_vs_one = "above" if current_minus > 1.0 else ("below" if current_minus < 1.0 else "at")

        # Time above 1.0 (computed on cleaned/aligned series)
        plus_above_one = (vi_plus > 1.0).sum()
        minus_above_one = (vi_minus > 1.0).sum()
        total_periods = len(vi_plus)

        # Recent crossings of 1.0 level (length-driven scan)
        plus_one_crosses = self._find_level_crossings(vi_plus, 1.0, cross_scan)
        minus_one_crosses = self._find_level_crossings(vi_minus, 1.0, cross_scan)

        return {
            "vi_plus_vs_one": plus_vs_one,
            "vi_minus_vs_one": minus_vs_one,
            "plus_above_one_pct": round((plus_above_one / total_periods) * 100, 1),
            "minus_above_one_pct": round((minus_above_one / total_periods) * 100, 1),
            "plus_one_crosses": plus_one_crosses[-3:] if plus_one_crosses else [],
            "minus_one_crosses": minus_one_crosses[-3:] if minus_one_crosses else []
        }

    def _find_level_crossings(self, series: pd.Series, level: float, scan_periods: int = 10) -> List[Dict[str, Any]]:
        """Find crossings of a specific level."""
        crossings = []
        n = min(scan_periods, len(series))

        for i in range(1, n):
            prev_val = series.iloc[-(i+1)]
            curr_val = series.iloc[-i]

            # Upward crossing
            if prev_val <= level and curr_val > level:
                crossings.append({
                    "type": "upward_cross",
                    "periods_ago": i,
                    "value": round(curr_val, 4)
                })
            # Downward crossing
            elif prev_val >= level and curr_val < level:
                crossings.append({
                    "type": "downward_cross",
                    "periods_ago": i,
                    "value": round(curr_val, 4)
                })

        return crossings

    def _detect_vortex_price_divergence(self, vi_plus: pd.Series, vi_minus: pd.Series, prices: pd.Series, div_win: int = 15) -> Optional[Dict[str, Any]]:
        """Detect Vortex-price divergence patterns."""
        # Choose dominant VI for divergence analysis
        current_plus = vi_plus.iloc[-1]
        current_minus = vi_minus.iloc[-1]

        if current_plus > current_minus:
            vi_series = vi_plus
            series_used = "VI+"
        else:
            vi_series = vi_minus
            series_used = "VI-"

        # Align VI and price data
        dfx = pd.concat({"vi": vi_series, "px": pd.to_numeric(prices, errors="coerce")}, axis=1, join="inner").dropna()
        if len(dfx) < max(15, div_win):
            return None

        # Use recent window for analysis
        win = min(max(10, div_win), len(dfx))
        seg = dfx.tail(win)

        # Scaled prominence for both series
        prom_vi = max(1e-6, seg["vi"].std() * 0.6)
        prom_px = max(1e-6, seg["px"].std() * 0.6)

        vi_peaks = self._find_peaks(seg["vi"], prominence=prom_vi)
        vi_troughs = self._find_troughs(seg["vi"], prominence=prom_vi)
        px_peaks = self._find_peaks(seg["px"], prominence=prom_px)
        px_troughs = self._find_troughs(seg["px"], prominence=prom_px)

        # Bullish divergence: price lower lows, VI higher lows
        if len(vi_troughs) >= 2 and len(px_troughs) >= 2:
            latest_vi_trough = vi_troughs[-1]
            prev_vi_trough = vi_troughs[-2]
            latest_px_trough = px_troughs[-1]
            prev_px_trough = px_troughs[-2]

            if (latest_px_trough["value"] < prev_px_trough["value"] and
                latest_vi_trough["value"] > prev_vi_trough["value"]):
                return {
                    "type": "bullish_divergence",
                    "series_used": series_used,
                    "description": f"Price making lower lows while {series_used} making higher lows",
                    "trough_comparison": {
                        "price_change": round(latest_px_trough["value"] - prev_px_trough["value"], 4),
                        "vortex_change": round(latest_vi_trough["value"] - prev_vi_trough["value"], 4),
                        "price_trough_periods_ago": latest_px_trough["periods_ago"],
                        "vortex_trough_periods_ago": latest_vi_trough["periods_ago"]
                    }
                }

        # Bearish divergence: price higher highs, VI lower highs
        if len(vi_peaks) >= 2 and len(px_peaks) >= 2:
            latest_vi_peak = vi_peaks[-1]
            prev_vi_peak = vi_peaks[-2]
            latest_px_peak = px_peaks[-1]
            prev_px_peak = px_peaks[-2]

            if (latest_px_peak["value"] > prev_px_peak["value"] and
                latest_vi_peak["value"] < prev_vi_peak["value"]):
                return {
                    "type": "bearish_divergence",
                    "series_used": series_used,
                    "description": f"Price making higher highs while {series_used} making lower highs",
                    "peak_comparison": {
                        "price_change": round(latest_px_peak["value"] - prev_px_peak["value"], 4),
                        "vortex_change": round(latest_vi_peak["value"] - prev_vi_peak["value"], 4),
                        "price_peak_periods_ago": latest_px_peak["periods_ago"],
                        "vortex_peak_periods_ago": latest_vi_peak["periods_ago"]
                    }
                }

        return None

    def _detect_vortex_patterns(self, vi_plus: pd.Series, vi_minus: pd.Series, length: int = 14) -> Dict[str, Any]:
        """Detect Vortex patterns and formations."""
        patterns = {}

        if len(vi_plus) >= 15:
            # Compression/expansion patterns
            compression = self._detect_vortex_compression(vi_plus, vi_minus)
            if compression:
                patterns["compression"] = compression

            # Parallel movement
            parallel = self._detect_parallel_movement(vi_plus, vi_minus, length)
            if parallel:
                patterns["parallel_movement"] = parallel

        # Strong directional patterns
        if len(vi_plus) >= 10:
            vel_win = max(2, length // 5)
            spread = vi_plus - vi_minus
            spread_velocity = self._calculate_velocity(spread, vel_win)

            if abs(spread_velocity) > spread.std() * 0.5:
                patterns["directional_momentum"] = {
                    "type": f"strong_{'bullish' if spread_velocity > 0 else 'bearish'}_momentum",
                    "velocity": round(spread_velocity, 6),
                    "description": f"Strong {'bullish' if spread_velocity > 0 else 'bearish'} directional momentum"
                }

        return patterns

    def _detect_vortex_compression(self, vi_plus: pd.Series, vi_minus: pd.Series) -> Optional[Dict[str, Any]]:
        """Detect compression patterns (VI+ and VI- converging)."""
        spread = (vi_plus - vi_minus).abs()
        current_spread = spread.iloc[-1]
        avg_spread = spread.mean()

        if current_spread < avg_spread * 0.5:
            # Look at recent trend
            recent_spread = spread.iloc[-5:]
            if len(recent_spread) >= 2 and recent_spread.iloc[-1] < recent_spread.iloc[0]:
                return {
                    "type": "converging_compression",
                    "current_spread": round(current_spread, 4),
                    "avg_spread": round(avg_spread, 4),
                    "description": "VI+ and VI- converging - potential breakout setup"
                }

        return None

    def _detect_parallel_movement(self, vi_plus: pd.Series, vi_minus: pd.Series, length: int = 14) -> Optional[Dict[str, Any]]:
        """Detect parallel movement patterns."""
        window = max(8, length // 2)
        if len(vi_plus) < window:
            return None

        recent_plus = vi_plus.iloc[-window:]
        recent_minus = vi_minus.iloc[-window:]

        plus_changes = recent_plus.diff().dropna()
        minus_changes = recent_minus.diff().dropna()

        if len(plus_changes) >= 3 and len(minus_changes) >= 3:
            correlation = np.corrcoef(plus_changes, minus_changes)[0, 1]

            if not np.isnan(correlation) and abs(correlation) > 0.7:
                return {
                    "type": "parallel_movement",
                    "correlation": round(correlation, 3),
                    "direction": "same" if correlation > 0 else "opposite",
                    "description": f"VI+ and VI- moving in {'same' if correlation > 0 else 'opposite'} direction"
                }

        return None

    def _generate_vortex_summary(self, vi_plus: float, vi_minus: float,
                               crossover_analysis: Dict, dominance_analysis: Dict,
                               divergence: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable Vortex summary with enriched signals."""
        dominant = dominance_analysis.get("current_dominant", "").replace("_", " ")
        dominance_strength = dominance_analysis.get("dominance_strength", 0)

        summary = f"Vortex VI+={vi_plus:.3f}, VI-={vi_minus:.3f} ({dominant})"

        # Recent crossover - important trend change signal
        latest_crossover = crossover_analysis.get("latest_crossover")
        if latest_crossover and latest_crossover.get("periods_ago", 99) <= 5:
            crossover_type = "bullish" if "bullish" in latest_crossover.get("type", "") else "bearish"
            strength = latest_crossover.get("strength", 0)
            if strength > 0.5:
                summary += f". ✓ Strong {crossover_type} crossover {latest_crossover['periods_ago']}p ago"
            else:
                summary += f". {crossover_type.capitalize()} crossover {latest_crossover['periods_ago']}p ago"

        # ⚠️ DIVERGENCE - critical signal
        if divergence:
            div_type = divergence.get("type", "")
            if "bullish" in div_type:
                summary += ". ✓ BULLISH DIVERGENCE"
            elif "bearish" in div_type:
                summary += ". ⚠️ BEARISH DIVERGENCE"

        # Dominance strength
        if abs(dominance_strength) > 0.2:
            if dominance_strength > 0:
                summary += ". ✓ Strong bullish trend"
            else:
                summary += ". ⚠️ Strong bearish trend"

        # Pattern analysis
        if pattern_analysis and "parallel_movement" in pattern_analysis:
            pm = pattern_analysis["parallel_movement"]
            if pm.get("direction") == "opposite":
                summary += ". VI lines diverging"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full Vortex output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "vortex", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        dominance = full_output.get("dominance_analysis", {})
        crossover = full_output.get("crossover_analysis", {})
        vi_plus = to_native(current.get("vi_plus"))
        vi_minus = to_native(current.get("vi_minus"))
        dominant = dominance.get("current_dominant", "neutral")
        zone = "bullish" if "bullish" in dominant else "bearish" if "bearish" in dominant else "neutral"
        latest_cross = crossover.get("latest_crossover")
        cross_type = None
        cross_periods = None
        if latest_cross:
            cross_type = "crossover_bullish" if "bullish" in latest_cross.get("type", "") else "crossover_bearish"
            cross_periods = to_native(latest_cross.get("periods_ago"))
        return {"indicator": "vortex", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(vi_plus), 4) if vi_plus else None,
                "value_secondary": round(float(vi_minus), 4) if vi_minus else None, "value_tertiary": None,
                "velocity": 0.0, "rank": 0.5, "zone": zone, "zone_periods": 0,
                "trend": "bullish" if "bullish" in dominant else "bearish" if "bearish" in dominant else "neutral",
                "crossover_type": cross_type, "crossover_periods_ago": cross_periods,
                "patterns": [], "analysis": full_output.get("summary", "")}
