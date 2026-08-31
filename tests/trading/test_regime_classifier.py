"""The deterministic regime classifier — golden label cases, hysteresis
(a boundary case is not a flip), honest absence on stale readings, and the
board + action_runs side effects the retired seat used to provide."""

from __future__ import annotations

import time

import pytest

from trading.regime import classifier
from trading.lifecycle import queries
from trading.lifecycle.db import get_db

# Captured before any monkeypatching, for the plumbing test below.
_REAL_ENSURE_FRESH = classifier._ensure_fresh


class TestComputeLabels:
    def test_strong_trend_up(self):
        out = classifier.compute_labels(
            adx=32.0, adx_bias="bullish", ema_consensus="rising",
            atr_percentile=50.0)
        assert out["direction"] == "trending-up"
        assert out["volatility"] == "normal"
        assert out["conviction"] == 7.0

    def test_weak_conflicted_trend_is_ranging(self):
        out = classifier.compute_labels(
            adx=22.0, adx_bias="bullish", ema_consensus="falling",
            atr_percentile=50.0)
        assert out["direction"] == "ranging"

    def test_weak_agreeing_trend_holds(self):
        out = classifier.compute_labels(
            adx=22.0, adx_bias="bullish", ema_consensus="rising",
            atr_percentile=50.0)
        assert out["direction"] == "trending-up"

    def test_trendless_and_compressed(self):
        out = classifier.compute_labels(
            adx=12.0, adx_bias="bullish", ema_consensus="rising",
            atr_percentile=10.0)
        assert out["direction"] == "ranging"
        assert out["volatility"] == "compressed"

    def test_elevated_and_bearish(self):
        out = classifier.compute_labels(
            adx=40.0, adx_bias="bearish", ema_consensus="falling",
            atr_percentile=90.0)
        assert out["direction"] == "trending-down"
        assert out["volatility"] == "elevated"

    @pytest.mark.parametrize("vix,label", [
        (12.0, "risk-on"), (20.0, "neutral"), (30.0, "risk-off")])
    def test_macro_bands(self, vix, label):
        out = classifier.compute_labels(
            adx=30.0, adx_bias="bullish", ema_consensus="rising",
            atr_percentile=50.0, vix=vix, want_macro=True)
        assert out["macro"] == label

    def test_macro_absent_off_position_scale(self):
        out = classifier.compute_labels(
            adx=30.0, adx_bias="bullish", ema_consensus="rising",
            atr_percentile=50.0, vix=12.0, want_macro=False)
        assert out["macro"] is None

    def test_missing_required_reading_is_none(self):
        assert classifier.compute_labels(
            adx=None, adx_bias="bullish", ema_consensus="rising",
            atr_percentile=50.0) is None


def _seed_cache(direction_bias="bullish", consensus="rising",
                atr_pctl=50.0, intervals=("1h", "4h", "1d")):
    from trading.perception import cache

    for iv in intervals:
        cache.write_data_point(
            "ta_adx", {"current": {"adx": 32.0},
                       "context": {"directional_bias": direction_bias}},
            source="test",
            params={"symbol": "BTC", "interval": iv, "length": 14})
        cache.write_data_point(
            "ta_ema", {"context": {"trend": {"consensus": consensus}}},
            source="test",
            params={"symbol": "BTC", "interval": iv, "length": 20})
        cache.write_data_point(
            "ta_atr", {"volatility_analysis": {"percentile_rank": atr_pctl}},
            source="test",
            params={"symbol": "BTC", "interval": iv, "length": 14})
    cache.write_data_point("macro_vix", {"value": 18.0}, source="test")


def _seed_board():
    from harness.constants import get_hermes_home
    from harness.runtime_templates import REGIME_MD_TEMPLATE

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "REGIME.md").write_text(REGIME_MD_TEMPLATE, encoding="utf-8")


class TestRun:
    @pytest.fixture(autouse=True)
    def no_network_refresh(self, monkeypatch):
        """The classifier's self-refresh hits the live venue; in tests the
        seeded cache is the whole world. Unpatched, real BTC readings
        overwrite the fixtures whenever the network answers — the suite
        caught exactly that nondeterminism (2026-08-31)."""
        monkeypatch.setattr(classifier, "_ensure_fresh", lambda symbols: None)

    def test_ensure_fresh_asks_for_the_classifier_panel(self, monkeypatch):
        calls = []
        import trading.perception.fetch_core as fc
        monkeypatch.setattr(fc, "fetch_and_snapshot",
                            lambda name, params, **kw:
                            calls.append((name, params.get("symbol"),
                                          params.get("interval"))) or
                            {"ok": True, "value": {}})
        # The module-level capture bypasses this class's autouse no-op.
        _REAL_ENSURE_FRESH(["BTC"])
        assert ("ta_adx", "BTC", "1h") in calls
        assert ("ta_atr", "BTC", "1d") in calls
        assert ("macro_vix", None, None) in calls
        assert len(calls) == 10  # 3 DPs × 3 intervals + vix

    def test_first_assessment_writes_immediately(self):
        _seed_cache()
        _seed_board()
        conn = get_db()
        res = classifier.run(conn, symbols=["BTC"])
        assert res["written"] == 3 and res["flips"] == []
        assert res["board_ok"] is True
        regime = queries.current_regime(conn, symbol="BTC")
        assert regime["swing"]["direction"] == "trending-up"
        assert regime["position"]["macro"] == "neutral"  # vix 18
        row = conn.execute(
            "SELECT ok FROM action_runs WHERE action_type='regime' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None and row["ok"] == 1

    def test_hysteresis_holds_then_confirms(self):
        _seed_cache()
        _seed_board()
        conn = get_db()
        classifier.run(conn, symbols=["BTC"])

        # The tape turns: bearish now. First pass holds the standing label.
        _seed_cache(direction_bias="bearish", consensus="falling")
        res2 = classifier.run(conn, symbols=["BTC"])
        assert res2["flips"] == []
        assert queries.current_regime(conn, "BTC")["swing"]["direction"] \
            == "trending-up"

        # Second consecutive pass confirms the flip, flagged as such.
        res3 = classifier.run(conn, symbols=["BTC"])
        assert "BTC/swing" in res3["flips"]
        assert queries.current_regime(conn, "BTC")["swing"]["direction"] \
            == "trending-down"
        n_flips = conn.execute(
            "SELECT COUNT(*) FROM regime_observations WHERE flipped=1"
        ).fetchone()[0]
        assert n_flips == 3  # one confirmed flip per timescale

    def test_boundary_wobble_never_flips(self):
        _seed_cache()
        _seed_board()
        conn = get_db()
        classifier.run(conn, symbols=["BTC"])
        _seed_cache(direction_bias="bearish", consensus="falling")
        classifier.run(conn, symbols=["BTC"])  # pending
        _seed_cache(direction_bias="bullish", consensus="rising")
        res = classifier.run(conn, symbols=["BTC"])  # wobbled back
        assert res["flips"] == []
        assert queries.current_regime(conn, "BTC")["swing"]["direction"] \
            == "trending-up"

    def test_missing_interval_is_skipped_with_reason(self):
        _seed_cache(intervals=("1h", "1d"))
        _seed_board()
        conn = get_db()
        res = classifier.run(conn, symbols=["BTC"])
        assert res["written"] == 2
        assert "BTC/swing" in res["skipped"]
        assert "missing" in res["skipped"]["BTC/swing"]

    def test_stale_reading_is_skipped_not_used(self, monkeypatch):
        _seed_cache()
        _seed_board()
        conn = get_db()
        # Age every 1h entry past 2× the interval by lying about now.
        real_time = time.time
        monkeypatch.setattr(classifier.time, "time",
                            lambda: real_time() + 3 * 3600)
        res = classifier.run(conn, symbols=["BTC"])
        assert "BTC/intraday" in res["skipped"]
        assert "old" in res["skipped"]["BTC/intraday"]
