"""
VWAP (Volume Weighted Average Price) Preprocessor.

Advanced VWAP preprocessing with volume profile analysis, fair value assessment,
and comprehensive market state description.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pandas.api.types import is_datetime64_any_dtype

from .base import BasePreprocessor


class VWAPPreprocessor(BasePreprocessor):
    """Advanced VWAP preprocessor with professional-grade volume analysis."""

    def preprocess(self, vwap: pd.Series, prices: pd.Series = None, volumes: pd.Series = None,
                  length: int = 14, **kwargs) -> Dict[str, Any]:
        """
        Advanced VWAP preprocessing with sophisticated volume-weighted analysis.

        Provides rich market state description following analysis-only pattern.
        No signals or confidence - pure market context for Decision LLM.

        Args:
            vwap: VWAP values
            prices: Price series for position analysis (required)
            volumes: Volume series for volume profile analysis (optional)
            length: Period for window calculations

        Returns:
            Dictionary with comprehensive VWAP analysis
        """
        # Helper functions
        def _num(s): return pd.to_numeric(s, errors="coerce").dropna()
        def _den(x: float) -> float: return max(1e-12, abs(float(x)))

        # Clean and align core data (VWAP + prices required)
        vwap = _num(vwap)
        if prices is None:
            return {"error": "Price data required for VWAP analysis"}
        prices = _num(prices)

        # Robust alignment - handle different indices
        if len(vwap) == 0:
            return {"error": "No valid VWAP data after cleaning"}
        if len(prices) == 0:
            return {"error": "No valid price data after cleaning"}

        # Align by position if indices don't match, or by index if they do
        try:
            df = pd.concat({"vwap": vwap, "price": prices}, axis=1, join="inner").dropna()
        except Exception:
            # Fallback: align by position (take shorter length)
            min_len = min(len(vwap), len(prices))
            if min_len < 5:
                return {"error": "Insufficient aligned data for VWAP analysis"}

            # Create new series with matching indices
            vwap = pd.Series(vwap.iloc[-min_len:].values, index=range(min_len))
            prices = pd.Series(prices.iloc[-min_len:].values, index=range(min_len))
            df = pd.DataFrame({"vwap": vwap, "price": prices})

        if len(df) < 5:
            return {"error": "Insufficient data for VWAP analysis"}

        vwap, prices = df["vwap"], df["price"]

        # Clean volumes separately (optional)
        volumes = None if volumes is None else _num(volumes)

        # Get timestamp from series index
        ts = (vwap.index[-1].isoformat()
              if is_datetime64_any_dtype(vwap.index)
              else datetime.now(timezone.utc).isoformat())

        cur_vwap, cur_price = float(vwap.iloc[-1]), float(prices.iloc[-1])

        # Length-driven windows
        vel_win = max(2, length // 5)
        acc_win = max(3, length // 3)
        reversion_win = max(10, length)

        # Price-VWAP relationship
        price_relationship = self._analyze_price_vwap_relationship(prices, vwap)

        # Fair value analysis
        fair_value_analysis = self._analyze_fair_value_assessment(prices, vwap)

        # VWAP trend analysis
        trend_analysis = self._analyze_vwap_trend(vwap, vel_win)

        # Volume profile analysis (if volumes available)
        volume_profile = {}
        if volumes is not None:
            volume_profile = self._analyze_volume_profile(prices, vwap, volumes)

        # Support/resistance analysis
        support_resistance = self._analyze_vwap_support_resistance(prices, vwap)

        # Deviation analysis
        deviation_analysis = self._analyze_vwap_deviations(prices, vwap)

        # Anchored VWAP behavior
        anchored_analysis = self._analyze_anchored_vwap_behavior(vwap, vel_win)

        # Overall trend analysis
        overall_trend = self._analyze_trend(vwap)

        # Pattern detection
        patterns = self._detect_vwap_patterns(prices, vwap, volumes, length)

        # Level analysis
        level_analysis = self._analyze_key_levels(prices - vwap, [0])

        # Recent extremes
        extremes = self._find_recent_extremes(vwap, max(20, length))

        # Generate summary
        summary = self._generate_vwap_summary(
            cur_vwap, cur_price, price_relationship, fair_value_analysis
        )

        return {
            "indicator": "VWAP",
            "length": length,
            "current": {
                "vwap_value": round(cur_vwap, 4),
                "price": round(cur_price, 4),
                "price_distance": round(cur_price - cur_vwap, 4),
                "price_distance_pct": round(((cur_price - cur_vwap) / _den(cur_vwap)) * 100, 3),
                "timestamp": ts
            },
            "context": {
                "trend": {
                    "direction": overall_trend["direction"],
                    "strength": round(overall_trend["strength"], 3),
                    "velocity": round(trend_analysis["slope"], 6),
                    "smoothness": round(trend_analysis["smoothness"], 3)
                },
                "fair_value": {
                    "assessment": fair_value_analysis["current_assessment"],
                    "distance_pct": round(fair_value_analysis["distance_from_fair_value_pct"], 3),
                    "reversion_tendency": fair_value_analysis["mean_reversion"]["reversion_strength"]
                },
                "anchored": anchored_analysis,
                "volume_profile": volume_profile,
                "volatility": round((prices - vwap).std(), 4)
            },
            "levels": {
                "price_position": {
                    "current": price_relationship["position"],
                    "bias": price_relationship["bias"],
                    "above_vwap_pct": price_relationship["above_vwap_pct"],
                    "below_vwap_pct": price_relationship["below_vwap_pct"],
                    "position_changes": price_relationship["position_changes"]
                },
                "deviation_bands": {
                    "current_position": deviation_analysis["std_position"],
                    "std_devs_from_vwap": deviation_analysis["current_std_devs"],
                    "upper_1std": deviation_analysis["upper_1std"],
                    "lower_1std": deviation_analysis["lower_1std"],
                    "upper_2std": deviation_analysis["upper_2std"],
                    "lower_2std": deviation_analysis["lower_2std"]
                },
                "key_levels": [cur_vwap],
                "recent_crossovers": level_analysis.get("recent_crossovers", [])
            },
            "extremes": {
                "recent_high": {
                    "value": round(extremes["high_value"], 4),
                    "periods_ago": extremes["high_periods_ago"],
                    "significance": extremes["high_significance"]
                },
                "recent_low": {
                    "value": round(extremes["low_value"], 4),
                    "periods_ago": extremes["low_periods_ago"],
                    "significance": extremes["low_significance"]
                }
            },
            "patterns": patterns,
            "evidence": {
                "data_quality": {
                    "aligned_periods": len(vwap),
                    "had_volumes": volumes is not None,
                    "volume_profile_available": len(volume_profile) > 0,
                    "support_resistance_touches": support_resistance.get("total_touches", 0)
                },
                "calculation_notes": f"VWAP analysis based on {len(vwap)} aligned price/VWAP periods"
            },
            "summary": summary
        }

    def _analyze_price_vwap_relationship(self, prices: pd.Series, vwap: pd.Series) -> Dict[str, Any]:
        """Analyze price position relative to VWAP."""
        def _den(x: float) -> float: return max(1e-12, abs(float(x)))

        cur_price = prices.iloc[-1]
        cur_vwap = vwap.iloc[-1]

        # Current position (vectorized)
        distance = cur_price - cur_vwap
        distance_pct = (distance / _den(cur_vwap)) * 100
        position = "above" if distance > 0 else ("below" if distance < 0 else "at_level")

        # Historical position analysis (vectorized)
        mask_above = prices > vwap
        above_vwap_pct = mask_above.mean() * 100
        below_vwap_pct = (~mask_above).mean() * 100

        # Position changes (crossovers)
        position_changes = int(mask_above.ne(mask_above.shift()).iloc[1:].sum())

        # Distance statistics
        distances = prices - vwap
        avg_distance = distances.mean()
        max_distance = distances.max()
        min_distance = distances.min()

        return {
            "position": position,
            "bias": "bullish" if position == "above" else ("bearish" if position == "below" else "neutral"),
            "distance": round(distance, 4),
            "distance_pct": round(distance_pct, 3),
            "above_vwap_pct": round(above_vwap_pct, 1),
            "below_vwap_pct": round(below_vwap_pct, 1),
            "position_changes": position_changes,
            "avg_distance": round(avg_distance, 4),
            "max_distance": round(max_distance, 4),
            "min_distance": round(min_distance, 4)
        }

    def _analyze_fair_value_assessment(self, prices: pd.Series, vwap: pd.Series) -> Dict[str, Any]:
        """Analyze price relative to VWAP fair value."""
        def _den(x: float) -> float: return max(1e-12, abs(float(x)))

        cur_price = prices.iloc[-1]
        cur_vwap = vwap.iloc[-1]

        # Fair value assessment (safe percentage)
        dist_pct = ((cur_price - cur_vwap) / _den(cur_vwap)) * 100

        if dist_pct > 2:
            fair_value_assessment = "overvalued"
        elif dist_pct > 0.5:
            fair_value_assessment = "slightly_overvalued"
        elif dist_pct < -2:
            fair_value_assessment = "undervalued"
        elif dist_pct < -0.5:
            fair_value_assessment = "slightly_undervalued"
        else:
            fair_value_assessment = "fairly_valued"

        # Historical fair value analysis (safe percentages)
        hist = ((prices - vwap) / vwap.abs().clip(lower=1e-12)) * 100

        overvalued_periods = (hist > 1).sum()
        undervalued_periods = (hist < -1).sum()
        fair_valued_periods = len(hist) - overvalued_periods - undervalued_periods
        total_periods = len(hist)

        # Reversion tendency
        reversion_analysis = self._analyze_mean_reversion_tendency(prices, vwap)

        return {
            "current_assessment": fair_value_assessment,
            "distance_from_fair_value_pct": round(dist_pct, 3),
            "overvalued_time_pct": round((overvalued_periods / total_periods) * 100, 1),
            "undervalued_time_pct": round((undervalued_periods / total_periods) * 100, 1),
            "fairly_valued_time_pct": round((fair_valued_periods / total_periods) * 100, 1),
            "mean_reversion": reversion_analysis
        }

    def _analyze_mean_reversion_tendency(self, prices: pd.Series, vwap: pd.Series) -> Dict[str, Any]:
        """Analyze tendency for prices to revert to VWAP."""
        def _den(x: float) -> float: return max(1e-12, abs(float(x)))

        # Look for instances where price deviated significantly and then reverted
        reversions = []
        distance_threshold = 0.02  # 2% threshold

        distances = (prices - vwap) / vwap.abs().clip(lower=1e-12)

        for i in range(2, len(distances)):
            prev_distance = distances.iloc[i-1]
            curr_distance = distances.iloc[i]

            # Check for reversion from extreme levels
            if abs(prev_distance) > distance_threshold and abs(curr_distance) < abs(prev_distance) * 0.7:
                reversions.append({
                    "index": i,
                    "periods_ago": len(distances) - 1 - i,
                    "from_distance": round(prev_distance, 4),
                    "to_distance": round(curr_distance, 4),
                    "reversion_strength": round(abs(prev_distance - curr_distance), 4),
                    "timestamp": distances.index[i].isoformat() if is_datetime64_any_dtype(distances.index) else None
                })

        # Calculate reversion statistics
        total_extreme_cases = (distances.abs() > distance_threshold).sum()
        reversion_cases = len(reversions)
        reversion_rate = (reversion_cases / total_extreme_cases) if total_extreme_cases > 0 else 0

        return {
            "reversion_rate": round(reversion_rate, 3),
            "total_reversions": reversion_cases,
            "recent_reversions": reversions[-3:] if reversions else [],
            "reversion_strength": "high" if reversion_rate > 0.6 else "medium" if reversion_rate > 0.3 else "low"
        }

    def _analyze_vwap_trend(self, vwap: pd.Series, vel_win: int = 5) -> Dict[str, Any]:
        """Analyze VWAP trend characteristics."""
        # VWAP slope (length-driven window)
        slope = self._calculate_velocity(vwap, vel_win)

        if slope > 0.001:
            trend_direction = "rising"
        elif slope < -0.001:
            trend_direction = "falling"
        else:
            trend_direction = "flat"

        # Trend strength
        vwap_std = vwap.std()
        trend_strength = min(1.0, abs(slope) / (vwap_std * 0.1)) if vwap_std > 0 else 0

        # VWAP smoothness (should be smoother than regular prices) - no negative values
        vwap_volatility = vwap.std()
        vwap_mean = float(vwap.mean())
        smoothness = float(np.clip(1 - (vwap_volatility / max(1e-12, abs(vwap_mean))), 0, 1))

        return {
            "direction": trend_direction,
            "slope": round(slope, 6),
            "strength": round(trend_strength, 3),
            "smoothness": round(smoothness, 3)
        }

    def _analyze_volume_profile(self, prices: pd.Series, vwap: pd.Series, volumes: pd.Series) -> Dict[str, Any]:
        """Analyze volume profile around VWAP."""
        def _den(x: float) -> float: return max(1e-12, abs(float(x)))

        # Align all three series for volume analysis
        dv = pd.concat({"price": prices, "vwap": vwap, "vol": volumes}, axis=1, join="inner").dropna()
        if len(dv) == 0:
            return {}

        total = float(dv["vol"].sum()) or 1.0

        # Volume above vs below VWAP (vectorized)
        above_mask = dv["price"] > dv["vwap"]
        above = float(dv.loc[above_mask, "vol"].sum())
        below = total - above

        # Volume near VWAP (institutional activity)
        near_mask = (dv["price"] - dv["vwap"]).abs() / dv["vwap"].abs().clip(lower=1e-12) <= 0.005
        near = float(dv.loc[near_mask, "vol"].sum())

        # Average volumes
        above_count = above_mask.sum()
        below_count = (~above_mask).sum()

        return {
            "above_vwap_volume_pct": round(above / total * 100, 1),
            "below_vwap_volume_pct": round(below / total * 100, 1),
            "near_vwap_volume_pct": round(near / total * 100, 1),
            "avg_volume_above": round(above / max(1, above_count), 2),
            "avg_volume_below": round(below / max(1, below_count), 2),
            "volume_bias": "above_vwap" if above > below else "below_vwap",
            "institutional_activity": "high" if (near / total * 100) > 20 else ("medium" if (near / total * 100) > 10 else "low")
        }

    def _analyze_vwap_support_resistance(self, prices: pd.Series, vwap: pd.Series) -> Dict[str, Any]:
        """Analyze VWAP as dynamic support/resistance."""
        def _den(x: float) -> float: return max(1e-12, abs(float(x)))

        touches = []
        bounces = []

        # VWAP touch threshold
        touch_threshold = 0.003  # 0.3%

        for i in range(1, len(prices)):
            price = prices.iloc[i]
            vwap_val = vwap.iloc[i]
            prev_price = prices.iloc[i-1]

            # Check for VWAP touch (safe percentage)
            if abs(price - vwap_val) / _den(vwap_val) <= touch_threshold:
                touch_data = {
                    "index": i,
                    "periods_ago": len(prices) - 1 - i,
                    "price": round(price, 4),
                    "vwap_value": round(vwap_val, 4),
                    "timestamp": prices.index[i].isoformat() if is_datetime64_any_dtype(prices.index) else None
                }
                touches.append(touch_data)

                # Check for bounce
                if i < len(prices) - 2:
                    next_price = prices.iloc[i+1]

                    # Support bounce
                    if prev_price < vwap_val and next_price > price:
                        bounces.append({
                            "type": "support_bounce",
                            "index": i,
                            "periods_ago": len(prices) - 1 - i,
                            "strength": round(abs(next_price - price) / _den(price), 4),
                            "timestamp": prices.index[i].isoformat() if is_datetime64_any_dtype(prices.index) else None
                        })

                    # Resistance bounce
                    elif prev_price > vwap_val and next_price < price:
                        bounces.append({
                            "type": "resistance_bounce",
                            "index": i,
                            "periods_ago": len(prices) - 1 - i,
                            "strength": round(abs(price - next_price) / _den(price), 4),
                            "timestamp": prices.index[i].isoformat() if is_datetime64_any_dtype(prices.index) else None
                        })

        # Calculate effectiveness
        success_rate = (len(bounces) / len(touches)) if len(touches) > 0 else 0

        return {
            "total_touches": len(touches),
            "successful_bounces": len(bounces),
            "success_rate": round(success_rate, 3),
            "recent_touches": touches[-5:] if touches else [],
            "recent_bounces": bounces[-3:] if bounces else [],
            "effectiveness": "high" if success_rate > 0.5 else "medium" if success_rate > 0.25 else "low"
        }

    def _analyze_vwap_deviations(self, prices: pd.Series, vwap: pd.Series) -> Dict[str, Any]:
        """Analyze standard deviations from VWAP."""
        # Calculate deviations
        deviations = prices - vwap
        std_dev = deviations.std()

        current_deviation = deviations.iloc[-1]
        current_std_devs = current_deviation / max(1e-12, std_dev)

        # Standard deviation bands
        vwap_current = vwap.iloc[-1]
        upper_1std = vwap_current + std_dev
        lower_1std = vwap_current - std_dev
        upper_2std = vwap_current + 2 * std_dev
        lower_2std = vwap_current - 2 * std_dev

        # Current position in std dev terms
        current_price = prices.iloc[-1]
        if current_price > upper_2std:
            std_position = "above_2std"
        elif current_price > upper_1std:
            std_position = "above_1std"
        elif current_price < lower_2std:
            std_position = "below_2std"
        elif current_price < lower_1std:
            std_position = "below_1std"
        else:
            std_position = "within_1std"

        return {
            "current_std_devs": round(current_std_devs, 2),
            "std_position": std_position,
            "standard_deviation": round(std_dev, 4),
            "upper_1std": round(upper_1std, 4),
            "lower_1std": round(lower_1std, 4),
            "upper_2std": round(upper_2std, 4),
            "lower_2std": round(lower_2std, 4)
        }

    def _analyze_anchored_vwap_behavior(self, vwap: pd.Series, vel_win: int = 3) -> Dict[str, Any]:
        """Analyze anchored VWAP behavior patterns."""
        # VWAP should typically trend with the overall price movement
        vwap_changes = vwap.diff().dropna()

        # Direction consistency (vectorized)
        positive_changes = (vwap_changes > 0).sum()
        negative_changes = (vwap_changes < 0).sum()
        total_changes = len(vwap_changes)

        if total_changes > 0:
            direction_consistency = max(positive_changes, negative_changes) / total_changes
        else:
            direction_consistency = 0.5

        # VWAP momentum (length-driven window)
        vwap_momentum = self._calculate_velocity(vwap, vel_win)

        # Reset behavior (if VWAP appears to have reset/anchored)
        reset_detected = self._detect_vwap_reset(vwap)

        return {
            "direction_consistency": round(direction_consistency, 3),
            "momentum": round(vwap_momentum, 6),
            "reset_detected": reset_detected,
            "behavior_quality": "stable" if direction_consistency > 0.7 else "choppy"
        }

    def _detect_vwap_reset(self, vwap: pd.Series) -> bool:
        """Detect if VWAP appears to have been reset (new session)."""
        if len(vwap) < 10:
            return False

        # Look for significant jumps that might indicate reset
        vwap_changes = vwap.diff().dropna()
        change_threshold = vwap.std() * 2  # 2 standard deviations

        significant_jumps = (vwap_changes.abs() > change_threshold).sum()

        # If we see significant jumps, might indicate resets
        return significant_jumps > 0

    def _detect_vwap_patterns(self, prices: pd.Series, vwap: pd.Series, volumes: Optional[pd.Series], length: int = 14) -> Dict[str, Any]:
        """Detect VWAP patterns and formations."""
        patterns = {}

        # Price-VWAP convergence/divergence patterns
        if len(prices) >= 10:
            vel_win = max(2, length // 5)
            spread = prices - vwap
            spread_velocity = self._calculate_velocity(spread, vel_win)

            if abs(spread_velocity) > spread.std() * 0.5:
                patterns["convergence_divergence"] = {
                    "type": f"{'diverging' if spread_velocity > 0 else 'converging'}_from_vwap",
                    "velocity": round(spread_velocity, 6),
                    "description": f"Price {'diverging from' if spread_velocity > 0 else 'converging to'} VWAP"
                }

        # Volume clustering patterns (if volume available)
        if volumes is not None and len(prices) >= length:
            # Align for volume analysis
            dv = pd.concat({"price": prices, "vwap": vwap, "vol": volumes}, axis=1, join="inner").dropna()
            if len(dv) >= length:
                vol_profile = self._analyze_volume_profile(dv["price"], dv["vwap"], dv["vol"])
                if vol_profile.get("institutional_activity") == "high":
                    patterns["volume_clustering"] = {
                        "type": "high_institutional_activity",
                        "near_vwap_pct": vol_profile["near_vwap_volume_pct"],
                        "description": "High volume clustering near VWAP - institutional activity"
                    }

        # Mean reversion patterns
        if len(prices) >= 15:
            current_distance_pct = abs(((prices.iloc[-1] - vwap.iloc[-1]) / max(1e-12, abs(vwap.iloc[-1]))) * 100)
            if current_distance_pct > 2.0:
                patterns["extreme_deviation"] = {
                    "type": "extreme_deviation_from_vwap",
                    "distance_pct": round(current_distance_pct, 3),
                    "direction": "above" if prices.iloc[-1] > vwap.iloc[-1] else "below",
                    "description": f"Price showing extreme deviation ({current_distance_pct:.1f}%) from VWAP"
                }

        return patterns

    def _generate_vwap_summary(self, vwap_value: float, price: float,
                              price_relationship: Dict, fair_value_analysis: Dict,
                              crossover_analysis: Dict = None, pattern_analysis: Dict = None) -> str:
        """Generate human-readable VWAP summary with enriched signals."""
        position = price_relationship.get("position", "at_level")
        distance_pct = price_relationship.get("distance_pct", 0)
        fair_value = fair_value_analysis.get("current_assessment", "fairly_valued")

        summary = f"VWAP={vwap_value:.4f}, price {position}"

        if abs(distance_pct) > 0.5:
            summary += f" by {abs(distance_pct):.1f}%"

        # Fair value assessment
        if "overvalued" in fair_value:
            summary += ". ⚠️ Price overvalued"
        elif "undervalued" in fair_value:
            summary += ". ✓ Price undervalued"

        # Recent crossover
        if crossover_analysis:
            latest = crossover_analysis.get("latest_crossover")
            if latest and latest.get("periods_ago", 99) <= 5:
                cross_type = "bullish" if "bullish" in latest.get("type", "") else "bearish"
                summary += f". {cross_type.capitalize()} VWAP cross {latest['periods_ago']}p ago"

        # Extreme deviation pattern
        if pattern_analysis and "extreme_deviation" in pattern_analysis:
            ed = pattern_analysis["extreme_deviation"]
            direction = ed.get("direction", "")
            if direction == "above":
                summary += ". ⚠️ Extreme above VWAP - mean reversion likely"
            elif direction == "below":
                summary += ". ⚠️ Extreme below VWAP - bounce likely"

        # Institutional support/resistance
        if abs(distance_pct) < 0.3:
            summary += ". Price at VWAP (key institutional level)"

        return summary

    def to_compact(self, full_output: dict, timeframe: str) -> dict:
        """Convert full VWAP output to universal compact format."""
        if "error" in full_output:
            return {"indicator": "vwap", "timeframe": timeframe, "timestamp": None,
                    "value": None, "value_secondary": None, "value_tertiary": None,
                    "velocity": 0.0, "rank": 0.0, "zone": "error", "zone_periods": 0,
                    "trend": "unknown", "crossover_type": None, "crossover_periods_ago": None,
                    "patterns": [], "analysis": full_output.get("error", "")}
        def to_native(val):
            if val is None: return None
            if hasattr(val, 'item'): return val.item()
            return val
        current = full_output.get("current", {})
        price_rel = full_output.get("price_relationship", {})
        vwap_val = to_native(current.get("vwap"))
        distance = to_native(price_rel.get("distance_pct"))
        position = price_rel.get("position", "at_level")
        zone = "above" if position == "above" else "below" if position == "below" else "neutral"
        return {"indicator": "vwap", "timeframe": timeframe, "timestamp": current.get("timestamp"),
                "value": round(float(vwap_val), 4) if vwap_val else None,
                "value_secondary": round(float(distance), 2) if distance else None, "value_tertiary": None,
                "velocity": 0.0, "rank": 0.5, "zone": zone, "zone_periods": 0, "trend": zone,
                "crossover_type": None, "crossover_periods_ago": None,
                "patterns": [], "analysis": full_output.get("summary", "")}
