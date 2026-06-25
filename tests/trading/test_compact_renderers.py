"""Issue 4 — conviction read-path renderers and the usability contract.

These lock in that the signal-dense renderers stay bounded and preserve the
fields the byte clamp used to delete, and that a renderer-less oversized value
becomes a loud <TRUNCATED … NO RENDERER> sentinel scored 'missing' rather than
a silently byte-clamped neutral.
"""

import json
import math

import pandas as pd
import pytest

from trading.perception.core.compact_renderers import (
    render_orderbook, render_candles, render_cvd, render_ta,
)


def _size(obj) -> int:
    return len(json.dumps(obj, default=str))


class TestOrderbook:
    def test_both_sides_survive_with_imbalance(self):
        ob = {"symbol": "BTC", "ts_ms": 1,
              "bids": [{"px": 100 - i * 0.5, "sz": 3.0, "n": 2} for i in range(10)],
              "asks": [{"px": 101 + i * 0.5, "sz": 1.0, "n": 2} for i in range(10)]}
        r = render_orderbook(ob)
        assert r["bids_top3"] and r["asks_top3"]          # ask side no longer deleted
        assert r["imbalance"] == pytest.approx(0.5)        # bid-heavy → +0.5
        assert r["mid"] == pytest.approx(100.5)
        assert r["spread_bp"] is not None
        assert _size(r) < 600

    def test_empty_book_is_total(self):
        r = render_orderbook({"symbol": "BTC", "bids": [], "asks": []})
        assert r["imbalance"] is None and r["mid"] is None


class TestCandles:
    def test_features_over_full_window(self):
        candles = [{"t": i, "o": 100 + math.sin(i / 5), "h": 100.6 + math.sin(i / 5),
                    "l": 99.4 + math.sin(i / 5), "c": 100 + math.sin((i + 1) / 5),
                    "v": 1000 + i} for i in range(200)]
        r = render_candles({"symbol": "BTC", "interval": "1h", "count": 200, "candles": candles})
        assert r["count"] == 200
        assert len(r["last5_ohlcv"]) == 5
        for k in ("pct_change_5", "atr_proxy_pct", "close_vs_sma20_pct",
                  "up_bar_ratio", "volume_trend", "pos_in_range_pct"):
            assert r[k] is not None
        assert _size(r) < 1500                              # under the rendered cap

    def test_empty_candles_is_total(self):
        r = render_candles({"symbol": "BTC", "interval": "1h", "candles": []})
        assert r["count"] == 0


class TestCVD:
    def test_keeps_trend_and_divergence_tail(self):
        from trading.integrations.flow._calc import calc_cvd
        df = pd.DataFrame({"high": [100 + i * 0.1 for i in range(50)],
                           "low": [99 + i * 0.1 for i in range(50)],
                           "close": [99.8 + i * 0.1 for i in range(50)],
                           "volume": [1000 + i * 10 for i in range(50)]})
        full = calc_cvd(df)
        assert _size(full) > 400                            # was truncated before
        r = render_cvd(full)
        assert r["cvd_trend"] is not None                   # the deleted tail survives
        assert "divergence" in r
        assert _size(r) < 600


class TestTA:
    def test_real_rsi_output_renders_zone_and_value(self):
        from trading.integrations.ta import _calc
        df = pd.DataFrame({"open": [100 + math.sin(i / 4) for i in range(120)],
                           "high": [100.6 + math.sin(i / 4) for i in range(120)],
                           "low": [99.4 + math.sin(i / 4) for i in range(120)],
                           "close": [100 + math.sin((i + 1) / 4) for i in range(120)],
                           "volume": [1000 + i for i in range(120)]})
        full = _calc.calc_rsi(df, length=14)
        assert _size(full) > 400                            # full output was truncated
        r = render_ta(full)
        assert r["indicator"] == "rsi"
        assert r["value"] is not None
        assert r["zone"] in ("overbought", "oversold", "neutral")
        assert _size(r) < 1500

    def test_error_output_passthrough(self):
        r = render_ta({"error": "insufficient_data", "message": "need 5"})
        assert r["error"] == "insufficient_data"

    def test_non_dict_is_total(self):
        assert "reading" in render_ta(["unexpected"])


class TestFetchReadingUsability:
    def _register(self, name, fn, **kw):
        from trading.perception.core.data_point_registry import register_data_point, _REGISTRY
        _REGISTRY.pop(name, None)
        register_data_point(name=name, category="x", source="t", description="d",
                            params_schema={}, returns_schema={}, **kw)(fn)

    def test_small_value_kept_raw_usable(self):
        from trading.dispatchers.predict_tools import _fetch_reading
        self._register("t_small_u", lambda: {"a": 1})
        num, reading, miss = _fetch_reading({"name": "t_small_u"})
        assert miss is None and reading == '{"a": 1}'

    def test_big_renderless_value_truncated_missing(self):
        from trading.dispatchers.predict_tools import _fetch_reading
        self._register("t_big_u", lambda: {"s": list(range(5000))})
        num, reading, miss = _fetch_reading({"name": "t_big_u"})
        assert miss == "no-renderer-truncated"
        assert reading.startswith("<TRUNCATED dp=t_big_u")

    def test_renderer_bounds_and_marks_usable(self):
        from trading.dispatchers.predict_tools import _fetch_reading
        self._register("t_rend_u", lambda: {"n": 7, "junk": "x" * 9000},
                       compact_fn=lambda v: {"kept": v["n"]})
        num, reading, miss = _fetch_reading({"name": "t_rend_u"})
        assert miss is None and reading == '{"kept": 7}'

    def test_fetch_failure_missing(self):
        from trading.dispatchers.predict_tools import _fetch_reading
        def _boom():
            raise RuntimeError("boom")
        self._register("t_fail_u", _boom)
        num, reading, miss = _fetch_reading({"name": "t_fail_u"})
        assert miss == "fetch-failed" and num is None

    def test_renderer_failure_missing_but_keeps_numeric(self):
        from trading.dispatchers.predict_tools import _fetch_reading
        def _raise(v):
            raise ValueError("nope")
        self._register("t_rfail_u", lambda: {"v": 3}, numeric_path="v", compact_fn=_raise)
        num, reading, miss = _fetch_reading({"name": "t_rfail_u"})
        assert miss == "render-failed" and num == 3.0
