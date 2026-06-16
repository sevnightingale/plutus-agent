"""Macro data points — direct context.dev web.extract DPs + classification.

The DPs read one number from a canonical source via context.dev, classify it
into a regime bucket deterministically, and expose it at numeric_path 'value'.
We mock the context.dev call — these tests cover the classification, the
return shape, the source-fallback loop, and fail-loud behaviour.
"""

import pytest

from trading.integrations.macro import _context_client as cc
from trading.integrations.macro import data_points as dp


class TestClassify:
    def test_vix_bucket_boundaries(self):
        # [0,15) low · [15,20) moderate · [20,30) elevated · [30,inf) extreme
        assert dp.classify(10, dp._VIX_BUCKETS)["label"] == "low_volatility"
        assert dp.classify(15, dp._VIX_BUCKETS)["label"] == "moderate"  # boundary → upper
        assert dp.classify(19.99, dp._VIX_BUCKETS)["label"] == "moderate"
        assert dp.classify(25, dp._VIX_BUCKETS)["label"] == "elevated"
        assert dp.classify(45, dp._VIX_BUCKETS)["label"] == "extreme"

    def test_etf_flow_signed_buckets(self):
        assert dp.classify(-800, dp._ETF_FLOW_BUCKETS)["label"] == "heavy_outflows"
        assert dp.classify(-200, dp._ETF_FLOW_BUCKETS)["label"] == "moderate_outflows"
        assert dp.classify(-50, dp._ETF_FLOW_BUCKETS)["label"] == "flat"
        assert dp.classify(300, dp._ETF_FLOW_BUCKETS)["label"] == "moderate_inflows"
        assert dp.classify(700, dp._ETF_FLOW_BUCKETS)["label"] == "heavy_inflows"


class TestMacroDPs:
    def test_vix(self, monkeypatch):
        monkeypatch.setattr(dp, "extract_value",
                            lambda *a, **k: {"value": 16.18, "source": "marketwatch"})
        out = dp.macro_vix()
        assert out["value"] == 16.18
        assert out["risk_regime"] == "moderate"
        assert out["source"] == "marketwatch"

    def test_dxy(self, monkeypatch):
        monkeypatch.setattr(dp, "extract_value",
                            lambda *a, **k: {"value": 99.65, "source": "mw"})
        out = dp.macro_dxy()
        assert out["value"] == 99.65 and out["strength"] == "neutral"

    def test_cpi(self, monkeypatch):
        monkeypatch.setattr(dp, "extract_value",
                            lambda *a, **k: {"value": 4.2, "period": "May 2026", "source": "bls"})
        out = dp.macro_cpi()
        assert out["value"] == 4.2 and out["regime"] == "elevated"
        assert out["period"] == "May 2026"

    def test_etf_netflow(self, monkeypatch):
        monkeypatch.setattr(dp, "extract_value",
                            lambda *a, **k: {"value": 53.608, "date": "15 Jun 2026", "source": "farside"})
        out = dp.btc_etf_netflow_daily()
        assert out["value"] == 53.608 and out["flow_regime"] == "flat"
        assert out["date"] == "15 Jun 2026"

    def test_numeric_path_resolves_value(self):
        from trading.perception.core.data_point_registry import _REGISTRY, extract_numeric
        for name in ("macro_vix", "macro_dxy", "macro_cpi", "btc_etf_netflow_daily"):
            e = _REGISTRY[name]
            assert e.numeric_path == "value"
            assert extract_numeric({"value": 42.0, "x": "y"}, e.numeric_path) == 42.0


class TestExtractValueFallback:
    def test_falls_through_to_next_source(self, monkeypatch):
        calls = []

        class FakeResp:
            def __init__(self, data):
                self.data = data

        class FakeWeb:
            def extract(self, *, url, schema, instructions, timeout_ms):
                calls.append(url)
                if url == "u1":
                    raise RuntimeError("first source down")
                return FakeResp({"value": 5.0})

        class FakeClient:
            web = FakeWeb()

        monkeypatch.setattr(cc, "get_context_client", lambda: FakeClient())
        out = cc.extract_value("u1", {"type": "object"}, "instr", fallback_urls=["u2"])
        assert out == {"value": 5.0, "source": "u2"}
        assert calls == ["u1", "u2"]

    def test_raises_when_all_sources_fail(self, monkeypatch):
        class FakeWeb:
            def extract(self, **k):
                raise RuntimeError("down")

        class FakeClient:
            web = FakeWeb()

        monkeypatch.setattr(cc, "get_context_client", lambda: FakeClient())
        with pytest.raises(RuntimeError):
            cc.extract_value("u1", {}, "instr")

    def test_empty_data_is_treated_as_failure(self, monkeypatch):
        class FakeResp:
            data = {}  # context.dev returned nothing useful

        class FakeWeb:
            def extract(self, **k):
                return FakeResp()

        class FakeClient:
            web = FakeWeb()

        monkeypatch.setattr(cc, "get_context_client", lambda: FakeClient())
        with pytest.raises(RuntimeError):
            cc.extract_value("u1", {}, "instr")
