"""Macro data points — free deterministic sources + classification.

The DPs read one number from a free structured source (Yahoo chart API, FRED
CSV, BLS public API, ECB rates, Farside-via-Jina), classify it into a regime
bucket deterministically, and expose it at numeric_path 'value'. We mock the
network — these tests cover the classification, the return shape, the
source-fallback chain, fail-loud behaviour, and the deterministic parsers.
"""

import importlib

import pytest

from trading.integrations.macro import _sources as src
from trading.integrations.macro import data_points as dp


@pytest.fixture(autouse=True)
def _registry_registrations_live():
    """Guard a latent order-dependency, not a product defect.

    ``_REGISTRY`` is a module global populated by import-time decorators. A
    test elsewhere that forces a fresh import (conftest's
    ``_sys_modules_hygiene`` restores sys.modules afterwards) can leave this
    worker holding a NEWLY imported registry module whose ``_REGISTRY`` is
    empty, while this file's already-imported ``data_points`` registered into
    the previous dict — so the lookups below KeyError depending only on which
    worker xdist happened to schedule this file onto.

    Re-apply the decorators when that has happened. Conditional because
    ``register_data_point`` raises RegistryError on a duplicate.
    """
    from trading.perception.core import data_point_registry as reg
    if "macro_vix" not in reg._REGISTRY:
        importlib.reload(dp)
    yield


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
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": 16.18, "source": "yahoo:^VIX"})
        out = dp.macro_vix()
        assert out["value"] == 16.18
        assert out["risk_regime"] == "moderate"
        assert out["source"] == "yahoo:^VIX"

    def test_dxy(self, monkeypatch):
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": 99.65, "source": "yahoo:DX-Y.NYB"})
        out = dp.macro_dxy()
        assert out["value"] == 99.65 and out["strength"] == "neutral"

    def test_us10y(self, monkeypatch):
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": 4.32, "source": "fred:DGS10"})
        out = dp.macro_us10y()
        assert out["value"] == 4.32 and out["rate_regime"] == "restrictive"

    def test_us10y_real(self, monkeypatch):
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": 1.85, "source": "fred:DFII10"})
        out = dp.macro_us10y_real()
        assert out["value"] == 1.85 and out["real_rate_regime"] == "elevated"
        # Negative real yields are gold's regime, not an error.
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": -0.4, "source": "fred:DFII10"})
        assert dp.macro_us10y_real()["real_rate_regime"] == "negative"

    def test_cpi(self, monkeypatch):
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": 4.2, "period": "May 2026", "source": "bls:CUUR0000SA0"})
        out = dp.macro_cpi()
        assert out["value"] == 4.2 and out["regime"] == "elevated"
        assert out["period"] == "May 2026"

    def test_etf_netflow(self, monkeypatch):
        monkeypatch.setattr(dp, "first_of",
                            lambda *a, **k: {"value": 53.608, "date": "15 Jun 2026", "source": "farside:btc"})
        out = dp.btc_etf_netflow_daily()
        assert out["value"] == 53.608 and out["flow_regime"] == "flat"
        assert out["date"] == "15 Jun 2026"

    def test_numeric_path_resolves_value(self):
        from trading.perception.core.data_point_registry import _REGISTRY, extract_numeric
        for name in ("macro_vix", "macro_dxy", "macro_cpi", "btc_etf_netflow_daily"):
            e = _REGISTRY[name]
            assert e.numeric_path == "value"
            assert extract_numeric({"value": 42.0, "x": "y"}, e.numeric_path) == 42.0


class TestFirstOfFallback:
    def test_falls_through_to_next_source(self):
        calls = []

        def down():
            calls.append("u1")
            raise RuntimeError("first source down")

        def up():
            calls.append("u2")
            return {"value": 5.0}

        out = src.first_of([("u1", down), ("u2", up)])
        assert out == {"value": 5.0, "source": "u2"}
        assert calls == ["u1", "u2"]

    def test_raises_when_all_sources_fail(self):
        def down():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError, match="every macro source failed"):
            src.first_of([("u1", down), ("u2", down)])

    def test_non_numeric_value_is_treated_as_failure(self):
        # A source that "succeeds" without a numeric value must not win.
        with pytest.raises(RuntimeError):
            src.first_of([("u1", lambda: {"value": None}),
                          ("u2", lambda: {})])


class TestParsers:
    def test_fred_latest_skips_missing_observations(self, monkeypatch):
        csv = "DATE,DGS10\n2026-08-19,4.65\n2026-08-20,4.69\n2026-08-21,.\n"
        monkeypatch.setattr(src, "_http_get", lambda *a, **k: csv)
        out = src.fred_latest("DGS10")
        assert out == {"value": 4.69, "date": "2026-08-20"}

    def test_fred_latest_raises_on_empty_series(self, monkeypatch):
        monkeypatch.setattr(src, "_http_get", lambda *a, **k: "DATE,DGS10\n2026-08-20,.\n")
        with pytest.raises(RuntimeError, match="no observations"):
            src.fred_latest("DGS10")

    def test_flow_number_forms(self):
        assert src._flow_number("306.7") == 306.7
        assert src._flow_number("(27,528)") == -27528.0
        assert src._flow_number("1,066") == 1066.0
        assert src._flow_number("-") == 0.0
        assert src._flow_number("") == 0.0

    def test_farside_reads_last_dated_row_total_column(self, monkeypatch):
        md = "\n".join([
            "| Seed | IBIT | FBTC | Total |",
            "| --- | --- | --- | --- |",
            "| Total | 62,426 | 10,185 | 53,775 |",  # all-time row — must be ignored
            "| 20 Aug 2026 | 503.0 | 64.7 | 606.3 |",
            "| 21 Aug 2026 | 239.3 | 30.2 | (307.5) |",
        ])
        monkeypatch.setattr(src, "_http_get", lambda *a, **k: md)
        out = src.farside_btc_netflow()
        assert out == {"value": -307.5, "date": "21 Aug 2026"}

    def test_farside_raises_when_no_dated_rows(self, monkeypatch):
        monkeypatch.setattr(src, "_http_get", lambda *a, **k: "| Total | 1 | 2 |\n")
        with pytest.raises(RuntimeError, match="no dated rows"):
            src.farside_btc_netflow()

    def test_bls_cpi_yoy_computes_from_index(self, monkeypatch):
        resp = {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"data": [
                {"year": "2026", "period": "M07", "periodName": "July", "value": "333.918"},
                {"year": "2026", "period": "M06", "periodName": "June", "value": "333.952"},
                {"year": "2025", "period": "M07", "periodName": "July", "value": "323.048"},
            ]}]},
        }
        monkeypatch.setattr(src, "_http_post_json", lambda *a, **k: resp)
        out = src.bls_cpi_yoy()
        assert out["period"] == "July 2026"
        assert out["value"] == pytest.approx((333.918 / 323.048 - 1) * 100, abs=0.01)

    def test_bls_cpi_yoy_raises_without_year_ago_index(self, monkeypatch):
        resp = {
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"data": [
                {"year": "2026", "period": "M07", "periodName": "July", "value": "333.918"},
            ]}]},
        }
        monkeypatch.setattr(src, "_http_post_json", lambda *a, **k: resp)
        with pytest.raises(RuntimeError, match="year-ago"):
            src.bls_cpi_yoy()

    def test_synthetic_dxy_matches_known_rates(self, monkeypatch):
        # Rates observed 2026-08-21; ICE DXY printed ~98.3 that day.
        rates = {"rates": {"EUR": 0.85477, "JPY": 158.7, "GBP": 0.73228,
                           "CAD": 1.374, "SEK": 9.4559, "CHF": 0.79947}}
        import json
        monkeypatch.setattr(src, "_http_get", lambda *a, **k: json.dumps(rates))
        value = src.synthetic_dxy()["value"]
        assert 95.0 < value < 101.0
