"""Data-point layer — numeric_path contract, TA calc fixes, CVD, cache budgets.

Everything here is hermetic: TA calcs run on synthetic OHLCV frames, the
dominance-velocity history test uses the per-test lifecycle.db, and the
registry tests use a scratch registration. No network.
"""

import json
import time

import numpy as np
import pandas as pd
import pytest

from trading.perception.core import data_point_registry as dpr
from trading.perception.cache import get_staleness_budget, _canonical_key

# Top-level imports on purpose: importing integration modules inside test
# functions makes conftest's sys.modules hygiene pop them after the test,
# and the NEXT test's re-import re-runs the @register_data_point decorators
# into the still-populated registry → RegistryError. Collection-time imports
# are in every test's snapshot and never popped.
from trading.integrations.ta import _calc as ta_calc
from trading.integrations.ta import data_points as ta_dp
from trading.integrations.flow._calc import calc_cvd
from trading.integrations.coingecko.data_points import _historical_dominance
from trading.integrations.hyperliquid.data_points import (
    _book_imbalance_calc,
    _funding_stats,
)
from trading.integrations.sessions.data_points import (
    _hourly_profile,
    _liquidity_pctile,
    _session_label,
)


# ── synthetic OHLCV helpers ────────────────────────────────────────────────


def _ohlcv(close, freq="h", vol_bias=None):
    """DataFrame from a close path. vol_bias: +1 close-near-high, -1 near-low."""
    n = len(close)
    idx = pd.date_range("2026-01-01", periods=n, freq=freq)
    high = np.asarray(close, dtype=float) + 1.0
    low = np.asarray(close, dtype=float) - 1.0
    if vol_bias is not None:
        close = [l + (h - l) * (0.9 if b > 0 else 0.1)
                 for h, l, b in zip(high, low, vol_bias)]
    return pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": np.full(n, 100.0),
    }, index=idx)


def _trend_df(n=120, start=100.0, end=80.0, seed=3):
    rs = np.random.RandomState(seed)
    path = np.linspace(start, end, n) + rs.normal(0, 0.3, n)
    return _ohlcv(path)


# ── numeric_path / extract_numeric ─────────────────────────────────────────


class TestExtractNumeric:
    def test_dotted_path(self):
        assert dpr.extract_numeric({"current": {"value": 56.6}}, "current.value") == 56.6

    def test_top_level_path(self):
        assert dpr.extract_numeric({"funding": 1.25e-05}, "funding") == 1.25e-05

    def test_bare_numeric_needs_no_path(self):
        assert dpr.extract_numeric(42, None) == 42.0

    def test_missing_path_returns_none(self):
        assert dpr.extract_numeric({"current": {}}, "current.value") is None
        assert dpr.extract_numeric({"a": 1}, None) is None

    def test_non_numeric_leaf_returns_none(self):
        assert dpr.extract_numeric({"v": "high"}, "v") is None
        assert dpr.extract_numeric({"v": True}, "v") is None
        assert dpr.extract_numeric(True, None) is None

    def test_resolvable_names_tracks_numeric_path(self):
        # Surgical scratch registrations — dpr.reset() would wipe the real
        # decorator registrations for every later test on this worker.
        try:
            @dpr.register_data_point(
                name="t_scalar", category="market", source="test",
                description="x", numeric_path="value")
            def t_scalar():
                return {"value": 1.0}

            @dpr.register_data_point(
                name="t_blob", category="market", source="test",
                description="x")
            def t_blob():
                return {"stuff": []}

            names = dpr.resolvable_names()
            assert "t_scalar" in names
            assert "t_blob" not in names
        finally:
            dpr._REGISTRY.pop("t_scalar", None)
            dpr._REGISTRY.pop("t_blob", None)


# ── TA calc fixes ──────────────────────────────────────────────────────────


class TestTrixCalc:
    def test_trix_works_with_default_params(self):
        out = ta_calc.calc_trix(_trend_df(end=120.0))
        assert "error" not in out
        assert isinstance(out["current"]["trix"], float)

    def test_trix_works_with_custom_signal(self):
        out = ta_calc.calc_trix(_trend_df(end=120.0), length=10, signal=5)
        assert "error" not in out


class TestPsarCalc:
    def test_downtrend_reads_bearish_not_fabricated_bullish(self):
        # Pre-fix, only the long-side column was read; in a downtrend its
        # NaNs were dropped and the analysis fabricated an uptrend.
        out = ta_calc.calc_psar(_trend_df(start=100.0, end=60.0))
        cur = out["current"]
        assert cur["psar_value"] > cur["price"]  # SAR above price = downtrend
        assert out["context"]["trend"]["current_direction"] == "bearish"

    def test_uptrend_reads_bullish(self):
        out = ta_calc.calc_psar(_trend_df(start=60.0, end=100.0))
        cur = out["current"]
        assert cur["psar_value"] < cur["price"]
        assert out["context"]["trend"]["current_direction"] == "bullish"

    def test_non_default_af_params_dont_keyerror(self):
        out = ta_calc.calc_psar(_trend_df(), af_start=0.03, af_max=0.3)
        assert "error" not in out and "current" in out


class TestPsarParamAlias:
    """ta_psar accepts ``af_step`` as an alias for ``af_increment`` — agents
    author strategies with either name (TradingView 'increment', pandas_ta
    'af', many refs 'af_step'); an unknown kwarg used to crash the fetch."""

    def _capture_fetch(self, monkeypatch):
        captured = {}

        def fake(symbol, interval, lookback_bars, calc_fn, **kw):
            captured.update(kw)
            return {"current": {"psar_value": 1.0}}

        monkeypatch.setattr(ta_dp, "_ta_fetch", fake)
        return captured

    def test_af_step_routes_to_af_increment(self, monkeypatch):
        captured = self._capture_fetch(monkeypatch)
        ta_dp.ta_psar("BTC", af_step=0.05)
        assert captured["af_increment"] == 0.05

    def test_af_increment_still_honored(self, monkeypatch):
        captured = self._capture_fetch(monkeypatch)
        ta_dp.ta_psar("BTC", af_increment=0.07)
        assert captured["af_increment"] == 0.07


class TestCvdDivergence:
    def test_bearish_divergence_price_up_flow_selling(self):
        close = list(np.linspace(100, 95, 80)) + list(np.linspace(95, 99, 20))
        bias = [1] * 80 + [-1] * 20
        out = calc_cvd(_ohlcv(close, vol_bias=bias))
        assert out["divergence"] and out["divergence"].startswith("bearish")

    def test_bullish_divergence_price_down_flow_buying(self):
        close = list(np.linspace(100, 105, 80)) + list(np.linspace(105, 101, 20))
        bias = [-1] * 80 + [1] * 20
        out = calc_cvd(_ohlcv(close, vol_bias=bias))
        assert out["divergence"] and out["divergence"].startswith("bullish")

    def test_no_false_bullish_when_selling_merely_abates(self):
        # The old multiplicative rule (recent > prior * 1.5) inverted on
        # negative deltas and tagged decelerating sell-offs "bullish".
        close = list(np.linspace(100, 90, 80)) + list(np.linspace(90, 88, 20))
        out = calc_cvd(_ohlcv(close, vol_bias=[-1] * 100))
        assert out["divergence"] is None
        assert out["cvd_trend"] in ("selling", "strong_selling")

    def test_aligned_rally_is_not_divergence(self):
        out = calc_cvd(_ohlcv(list(np.linspace(100, 110, 100)), vol_bias=[1] * 100))
        assert out["divergence"] is None


# ── interval-scaled cache budgets ──────────────────────────────────────────


class TestCacheBudgets:
    @pytest.mark.parametrize("interval,expected", [
        ("1m", 300.0),      # base floor
        ("1h", 1800.0),     # half-bar
        ("4h", 7200.0),
        ("1d", 14400.0),    # capped at the 4h perception floor
        ("1w", 14400.0),
    ])
    def test_ta_budget_scales_with_interval(self, interval, expected):
        key = _canonical_key("ta_rsi", {"symbol": "BTC", "interval": interval})
        assert get_staleness_budget(key) == expected

    def test_non_interval_points_unaffected(self):
        key = _canonical_key("hl_price", {"symbol": "BTC"})
        assert get_staleness_budget(key) == 60.0

    def test_bare_name_uses_base_budget(self):
        assert get_staleness_budget("ta_rsi") == 300.0


# ── dominance velocity from own snapshot history ───────────────────────────


class TestDominanceVelocityHistory:
    def test_historical_dominance_reads_own_snapshots(self, tmp_path, monkeypatch):
        from trading.lifecycle.db import get_db

        conn = get_db(tmp_path / "lifecycle.db")
        # Pin the module-level singleton get_db() resolves to this conn.
        import trading.lifecycle.db as dbmod
        monkeypatch.setattr(dbmod, "get_db", lambda *a, **k: conn)

        now = time.time()
        conn.execute(
            "INSERT INTO data_point_snapshots(session_name, ts, name, params_json, value_json, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t", now - 7 * 86400, "coingecko_global", "{}",
             json.dumps({"btc_dominance_pct": 58.4}), "coingecko"),
        )
        conn.commit()

        past = _historical_dominance(7)
        assert past is not None and past["btc_dominance_pct"] == 58.4
        assert _historical_dominance(30) is None  # no history that far back


# ── book imbalance (pure calc) ─────────────────────────────────────────────


class TestBookImbalanceCalc:
    def _book(self, bid_szs, ask_szs, mid=100.0, step=0.01):
        bids = [{"px": mid - step * (i + 1), "sz": s} for i, s in enumerate(bid_szs)]
        asks = [{"px": mid + step * (i + 1), "sz": s} for i, s in enumerate(ask_szs)]
        return bids, asks

    def test_balanced_book_is_zero(self):
        bids, asks = self._book([5.0, 5.0], [5.0, 5.0])
        assert _book_imbalance_calc(bids, asks, 50)["imbalance"] == 0.0

    def test_bid_heavy_is_positive(self):
        bids, asks = self._book([9.0], [1.0])
        out = _book_imbalance_calc(bids, asks, 50)
        assert out["imbalance"] == pytest.approx(0.8)  # (9-1)/(9+1)
        assert out["bid_size_in_band"] == 9.0

    def test_band_excludes_far_levels(self):
        # mid=100, 50 bps band = ±0.5: the size parked at ±2.0 is invisible.
        bids = [{"px": 99.9, "sz": 3.0}, {"px": 98.0, "sz": 100.0}]
        asks = [{"px": 100.1, "sz": 1.0}, {"px": 102.0, "sz": 100.0}]
        out = _book_imbalance_calc(bids, asks, 50)
        assert out["imbalance"] == pytest.approx(0.5)  # (3-1)/(3+1)

    def test_empty_side_raises(self):
        bids, _ = self._book([1.0], [1.0])
        with pytest.raises(ValueError, match="side empty"):
            _book_imbalance_calc(bids, [], 50)

    def test_no_size_in_band_raises(self):
        bids = [{"px": 90.0, "sz": 5.0}]
        asks = [{"px": 110.0, "sz": 5.0}]
        with pytest.raises(ValueError, match="no resting size"):
            _book_imbalance_calc(bids, asks, 10)


# ── funding z-score (pure calc) ────────────────────────────────────────────


class TestFundingStats:
    def test_known_distribution(self):
        rates = [0.0] * 50 + [1.0] * 50  # mean 0.5, population std 0.5
        out = _funding_stats(rates, 1.0)
        assert out["zscore"] == pytest.approx(1.0)
        assert out["percentile"] == pytest.approx(100.0)
        assert out["current_annualized_pct"] == pytest.approx(1.0 * 24 * 365 * 100)
        assert out["n_samples"] == 100

    def test_below_mean_is_negative(self):
        rates = [0.0] * 50 + [1.0] * 50
        assert _funding_stats(rates, 0.0)["zscore"] == pytest.approx(-1.0)

    def test_too_few_samples_raises(self):
        with pytest.raises(ValueError, match="need ≥ 24"):
            _funding_stats([0.1] * 23, 0.1)

    def test_zero_variance_raises(self):
        with pytest.raises(ValueError, match="zero variance"):
            _funding_stats([0.1] * 100, 0.1)


# ── session context (pure calcs) ───────────────────────────────────────────


class TestSessionCalcs:
    def test_session_boundaries(self):
        assert _session_label(0) == "asia"
        assert _session_label(6) == "asia"
        assert _session_label(7) == "europe"
        assert _session_label(13) == "eu_us_overlap"
        assert _session_label(15) == "eu_us_overlap"
        assert _session_label(16) == "us"
        assert _session_label(21) == "off_hours"
        assert _session_label(23) == "off_hours"

    def _day_of_candles(self, day_offset=0):
        # 24 hourly bars starting at UTC midnight; volume = hour index + 1.
        base_ms = (1704067200 + day_offset * 86400) * 1000  # 2024-01-01+n 00:00Z
        return [{"t": base_ms + h * 3600_000, "v": float(h + 1)} for h in range(24)]

    def test_profile_medians_by_hour(self):
        candles = self._day_of_candles(0) + self._day_of_candles(1)
        profile = _hourly_profile(candles)
        assert profile[0] == 1.0 and profile[23] == 24.0

    def test_liquidity_pctile_ranks(self):
        profile = _hourly_profile(self._day_of_candles())
        assert _liquidity_pctile(profile, 23) == pytest.approx(100.0)
        assert _liquidity_pctile(profile, 0) == pytest.approx(100.0 / 24)

    def test_partial_profile_raises(self):
        half_day = self._day_of_candles()[:12]
        with pytest.raises(ValueError, match="distinct UTC hours"):
            _hourly_profile(half_day)


class TestNewDpCacheBudgets:
    def test_budgets(self):
        assert get_staleness_budget("hl_book_imbalance") == 60.0
        assert get_staleness_budget("hl_funding_zscore") == 600.0
        assert get_staleness_budget("session_context") == 900.0
        assert get_staleness_budget("poly_price_ladder") == 900.0
        assert get_staleness_budget("poly_event_odds") == 900.0
