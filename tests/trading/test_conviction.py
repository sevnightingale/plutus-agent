"""Conviction engine v2 — weighted support-score aggregation + normalizers."""

import pytest

from trading.conviction import engine, normalizers
from trading.conviction.engine import ScoredInput, compute_conviction, update_weights

W = {"hl_funding(symbol=BTC)": 0.4, "hl_cvd(symbol=BTC)": 0.3, "news_digest(topic=btc)": 0.3}


def _s(key, score, kind="numerical", reasoning=None):
    return ScoredInput(dp_key=key, score=score, kind=kind,
                       reasoning_md=reasoning,
                       normalizer="linear_band" if kind == "numerical" else None)


class TestComputeConviction:
    def test_weighted_average(self):
        r = compute_conviction(W, [
            _s("hl_funding(symbol=BTC)", 1.0),
            _s("hl_cvd(symbol=BTC)", 0.5),
            _s("news_digest(topic=btc)", 0.0, kind="narrative", reasoning="bearish tape"),
        ])
        # (0.4*1 + 0.3*0.5 + 0.3*0) / 1.0 = 0.55
        assert r.conviction == pytest.approx(0.55)
        assert r.missing == []
        assert r.actionable  # clears the 0.50 gate; conviction sizes, not vetoes

    def test_missing_dp_excluded_not_defaulted(self):
        r = compute_conviction(W, [_s("hl_funding(symbol=BTC)", 1.0)])
        # only the funding weight participates: 0.4/0.4 = 1.0
        assert r.conviction == pytest.approx(1.0)
        assert sorted(r.missing) == ["hl_cvd(symbol=BTC)", "news_digest(topic=btc)"]
        assert r.actionable

    def test_nothing_scored_is_none(self):
        r = compute_conviction(W, [])
        assert r.conviction is None
        assert not r.actionable

    def test_undeclared_dp_refused(self):
        with pytest.raises(ValueError, match="undeclared"):
            compute_conviction(W, [_s("hl_oi(symbol=BTC)", 0.9)])

    def test_unreasoned_narrative_refused(self):
        with pytest.raises(ValueError, match="reasoning"):
            compute_conviction(W, [
                _s("news_digest(topic=btc)", 0.8, kind="narrative")])

    def test_out_of_range_score_refused(self):
        with pytest.raises(ValueError, match="outside"):
            compute_conviction(W, [_s("hl_cvd(symbol=BTC)", 1.4)])


class TestUpdateWeights:
    def test_alpha_step_toward_predictive(self):
        out = update_weights(W, {"hl_funding(symbol=BTC)": 1.0,
                                 "hl_cvd(symbol=BTC)": -1.0})
        # funding (0.4) can't GROW past the cap but isn't confiscated;
        # cvd steps down by alpha: 0.3 - 0.05 = 0.25
        assert out["hl_funding(symbol=BTC)"] == pytest.approx(0.4)
        assert out["hl_cvd(symbol=BTC)"] == pytest.approx(0.25)

    def test_cap_stops_growth_not_existing_weight(self):
        out = update_weights({"a": 0.28, "b": 0.5}, {"a": 1.0, "b": 1.0})
        assert out["a"] == pytest.approx(0.30)   # grew to the cap
        assert out["b"] == pytest.approx(0.5)    # above cap already: held, not cut

    def test_cap_and_sum_held(self):
        w = {"a": 0.29, "b": 0.29, "c": 0.29}
        out = update_weights(w, {"a": 1.0, "b": 1.0, "c": 1.0})
        assert all(v <= engine.WEIGHT_CAP + 1e-9 for v in out.values())
        assert sum(out.values()) <= engine.WEIGHT_SUM_MAX + 1e-9

    def test_unknown_dp_ignored(self):
        out = update_weights(W, {"nope": 1.0})
        assert out == {k: round(v, 4) for k, v in W.items()}


class TestTargetRiskBudget:
    def test_bands(self):
        rb = engine.target_risk_budget
        assert rb(0.49) is None           # below the gate: no trade
        assert rb(None) is None
        assert rb(0.50) == 0.01
        assert rb(0.599) == 0.01
        assert rb(0.60) == 0.03
        assert rb(0.70) == 0.07
        assert rb(0.80) == 0.12
        assert rb(1.0) == 0.12            # top band is inclusive

    def test_bands_cover_threshold_to_one(self):
        """The band table starts at the gate and tiles [threshold, 1.0]."""
        assert engine.RISK_BUDGET_BANDS[0][0] == engine.GLOBAL_CONVICTION_THRESHOLD
        for (_, hi, _), (lo, _, _) in zip(engine.RISK_BUDGET_BANDS,
                                          engine.RISK_BUDGET_BANDS[1:]):
            assert hi == lo               # no gaps, no overlaps

    def test_superlinear(self):
        """Budgets grow superlinearly — calibrated conviction earns more."""
        budgets = [b for _, _, b in engine.RISK_BUDGET_BANDS]
        gaps = [b - a for a, b in zip(budgets, budgets[1:])]
        assert all(g2 > g1 for g1, g2 in zip(gaps, gaps[1:]))  # widening steps

    def test_max_leverage(self):
        assert engine.MAX_LEVERAGE == 10.0


class TestNormalizers:
    def test_linear_band_and_flip(self):
        assert normalizers.apply("linear_band", 50, lo=0, hi=100) == 0.5
        assert normalizers.apply("linear_band", 80, lo=70, hi=20) == pytest.approx(0.0, abs=0.01)
        assert normalizers.apply("linear_band", 20, lo=70, hi=20) == 1.0

    def test_distance_from(self):
        assert normalizers.apply("distance_from", 100, anchor=100, full_at=10) == 0.5
        assert normalizers.apply("distance_from", 110, anchor=100, full_at=10) == 1.0
        assert normalizers.apply("distance_from", 90, anchor=100, full_at=10,
                                 direction="below") == 1.0

    def test_zscore(self):
        assert normalizers.apply("zscore", 0) == 0.5
        assert normalizers.apply("zscore", 3, cap=3) == 1.0
        assert normalizers.apply("zscore", -3, cap=3, invert=True) == 1.0

    def test_inside_band(self):
        assert normalizers.apply("inside_band", 5, lo=0, hi=10) == 1.0
        assert normalizers.apply("inside_band", 15, lo=0, hi=10) == 0.5
        assert normalizers.apply("inside_band", 25, lo=0, hi=10) == 0.0

    def test_unknown_normalizer_lists_known(self):
        with pytest.raises(KeyError, match="linear_band"):
            normalizers.apply("vibes", 1.0)


class TestNormalizerSpecs:
    def test_spec_id_stable(self):
        from trading.conviction.normalizers import spec_id
        assert spec_id("linear_band", {"lo": 70, "hi": 20}) == "linear_band(hi=20,lo=70)"
        assert spec_id("zscore") == "zscore"

    def test_validate_spec(self):
        from trading.conviction.normalizers import validate_spec
        assert validate_spec("linear_band", {"lo": 70, "hi": 20}) == []
        assert validate_spec("nope", {}) != []            # unregistered
        assert validate_spec("linear_band", {"lo": 5, "hi": 5}) != []   # degenerate
        assert validate_spec("linear_band", {"bogus": 1}) != []         # bad params
        assert validate_spec("distance_from", {"anchor": 100}) != []    # missing param
