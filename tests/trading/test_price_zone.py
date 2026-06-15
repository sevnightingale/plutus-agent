"""Price-zone math, path_stats, and the shared resolver."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from trading.lifecycle import price_zone, queries, resolver, write
from trading.lifecycle.db import get_db
from trading.integrations.hyperliquid import _client, outcomes


# ── Pure math ────────────────────────────────────────────────────────────────

class TestPriceZoneMath:
    def test_direction(self):
        assert price_zone.direction_of(5.0, 10.0) == 1
        assert price_zone.direction_of(-5.0, -10.0) == -1

    def test_validate_zone_good(self):
        assert price_zone.validate_zone(5.0, 10.0) == []
        assert price_zone.validate_zone(-5.0, -10.0) == []

    def test_validate_zone_rejects(self):
        assert price_zone.validate_zone(0.0, 10.0)              # zero edge
        assert price_zone.validate_zone(5.0, -10.0)             # mismatched sign
        assert price_zone.validate_zone(5.0, 5.0)               # far not beyond near
        assert price_zone.validate_zone(8.0, 5.0)               # far inside near

    def test_resolve_zone(self):
        # touched the near edge → correct, regardless of horizon
        assert price_zone.resolve_zone(5.0, 10.0, 6.0, False) == "correct"
        assert price_zone.resolve_zone(5.0, 10.0, 5.0, False) == "correct"
        # below near, horizon not passed → still open
        assert price_zone.resolve_zone(5.0, 10.0, 4.9, False) is None
        # below near, horizon passed → wrong
        assert price_zone.resolve_zone(5.0, 10.0, 4.9, True) == "wrong"
        # bearish uses magnitudes the same way (mfe is a positive magnitude)
        assert price_zone.resolve_zone(-5.0, -10.0, 6.0, False) == "correct"

    def test_profit_score(self):
        assert price_zone.profit_score(5.0, 10.0, 5.0) == pytest.approx(0.0)
        assert price_zone.profit_score(5.0, 10.0, 10.0) == pytest.approx(1.0)
        assert price_zone.profit_score(5.0, 10.0, 7.5) == pytest.approx(0.5)
        assert price_zone.profit_score(5.0, 10.0, 13.0) == pytest.approx(1.6)  # blow-past
        assert price_zone.profit_score(5.0, 10.0, 2.0) == pytest.approx(0.0)   # clamp
        assert price_zone.profit_score(5.0, 10.0, None) is None


# ── path_stats (windowed candle stats) ───────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_singletons():
    _client.reset_singletons_for_tests()
    yield
    _client.reset_singletons_for_tests()


def _stub_candles(monkeypatch, candles):
    info_mock = MagicMock()
    info_mock.candles_snapshot.return_value = candles
    monkeypatch.setattr(outcomes, "get_info", lambda: info_mock)


class TestPathStats:
    _CANDLES = [
        {"t": 0, "o": "100000", "h": "108000", "l": "98000", "c": "104000", "v": "1"},
        {"t": 1, "o": "104000", "h": "106000", "l": "101000", "c": "105000", "v": "1"},
    ]

    def test_long(self, monkeypatch):
        _stub_candles(monkeypatch, self._CANDLES)
        s = outcomes.path_stats("BTC", time.time() - 3600, time.time(), 100_000.0, 1)
        assert s["mfe_pct"] == pytest.approx(8.0)
        assert s["mae_pct"] == pytest.approx(-2.0)
        assert s["range_pct"] == pytest.approx(10.0)

    def test_short(self, monkeypatch):
        _stub_candles(monkeypatch, self._CANDLES)
        s = outcomes.path_stats("BTC", time.time() - 3600, time.time(), 100_000.0, -1)
        # favorable for a short is downward: low 98k → +2% favorable
        assert s["mfe_pct"] == pytest.approx(2.0)
        # adverse for a short is upward: high 108k → -8%
        assert s["mae_pct"] == pytest.approx(-8.0)

    def test_no_candles(self, monkeypatch):
        _stub_candles(monkeypatch, [])
        assert outcomes.path_stats("BTC", 0, 1, 100_000.0, 1) == {}


# ── Resolver ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def conn(tmp_path):
    c = get_db(tmp_path / "lifecycle.db")
    yield c
    c.close()


def _draft(**over):
    base = dict(
        claim_md="zone", horizon_ts=time.time() + 3600,
        entry_ref_price=100_000.0, near_edge_pct=5.0, far_edge_pct=10.0,
        conviction=0.7, agent="plutus-predict", symbol="BTC",
        strategy_name=None, kind="adhoc",
    )
    base.update(over)
    return write.PredictionDraft(**base)


def _record(conn, **over):
    return write.record_prediction(conn, _draft(**over))


def _stats_fn(mfe):
    def fn(symbol, start_ts, end_ts, entry, direction):
        return {"mfe_pct": mfe, "mae_pct": -1.0, "range_pct": mfe + 1.0,
                "low_px": 99_000.0, "high_px": entry * (1 + mfe / 100.0), "n_bars": 5}
    return fn


class TestResolver:
    def test_touch_resolves_correct(self, conn):
        pid = _record(conn)  # near +5% (=105k), far +10%
        t = time.time()
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 106_000.0}, path_stats_fn=_stats_fn(6.0), now=t)
        assert [r["outcome"] for r in res["resolved"]] == ["correct"]
        assert res["resolved"][0]["mode"] == "touch"
        got = queries.prediction(conn, pid)
        assert got["outcome"] == "correct"
        rv = json.loads(got["realized_value_json"])
        assert rv["resolution_mode"] == "touch"
        assert rv["profit_score"] == pytest.approx((6 - 5) / (10 - 5))

    def test_bearish_touch(self, conn):
        _record(conn, near_edge_pct=-5.0, far_edge_pct=-10.0)
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 94_000.0}, path_stats_fn=_stats_fn(6.0), now=time.time())
        assert res["resolved"][0]["outcome"] == "correct"

    def test_horizon_expiry_resolves_wrong(self, conn):
        t0 = time.time()
        _record(conn, ts=t0, horizon_ts=t0 + 60)
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 101_000.0}, path_stats_fn=_stats_fn(1.0), now=t0 + 120)
        assert res["resolved"][0]["outcome"] == "wrong"
        assert res["resolved"][0]["mode"] == "expired"

    def test_still_open_skips_candle_pull(self, conn):
        _record(conn)  # near +5%, horizon 1h out
        called = []

        def stats_fn(*a, **k):
            called.append(1)
            return {"mfe_pct": 1.0}

        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 101_000.0}, path_stats_fn=stats_fn, now=time.time())
        assert res["resolved"] == []
        assert not called  # the cheap path never touched the network

    def test_invalidation_resolves_wrong(self, conn):
        _record(conn, invalidation_criteria={
            "data_point": "hl_price", "params": {"symbol": "BTC"},
            "op": "lte", "threshold": 90_000.0})
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 101_000.0}, path_stats_fn=_stats_fn(1.0),
            fetch_fn=lambda dp, p: 85_000.0, now=time.time())  # price ≤ 90k → invalidated
        assert res["resolved"][0]["outcome"] == "wrong"
        assert res["resolved"][0]["mode"] == "invalidated"

    def test_success_beats_invalidation(self, conn):
        _record(conn, invalidation_criteria={
            "data_point": "hl_price", "op": "lte", "threshold": 90_000.0})
        # price touched the near edge AND the invalidation would trip — success wins
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 106_000.0}, path_stats_fn=_stats_fn(6.0),
            fetch_fn=lambda dp, p: 85_000.0, now=time.time())
        assert res["resolved"][0]["outcome"] == "correct"

    def test_second_pass_is_empty(self, conn):
        _record(conn)
        mids = {"BTC": 106_000.0}
        r1 = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=_stats_fn(6.0), now=time.time())
        assert len(r1["resolved"]) == 1
        r2 = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=_stats_fn(6.0), now=time.time())
        assert r2["resolved"] == [] and r2["open_count"] == 0


class TestOpsSweepWiring:
    """The resolve_due_predictions dispatcher wires all_mids + path_stats into
    the shared resolver against the (conftest-isolated) lifecycle.db."""

    def test_sweep_resolves_a_touch(self, monkeypatch):
        from trading.dispatchers import resolution
        from trading.lifecycle.db import get_db
        from trading.integrations.hyperliquid import _client, outcomes

        conn = get_db()  # conftest points this at a per-test tempdir
        pid = write.record_prediction(conn, _draft())  # near +5% of 100k = 105k

        info = MagicMock()
        info.all_mids.return_value = {"BTC": "106000"}  # above the near edge
        monkeypatch.setattr(_client, "get_info", lambda: info)
        monkeypatch.setattr(
            outcomes, "path_stats",
            lambda *a, **k: {"mfe_pct": 6.0, "mae_pct": -1.0, "range_pct": 7.0})

        res = json.loads(resolution._resolve_due({}))
        assert [r["outcome"] for r in res["resolved"]] == ["correct"]
        assert queries.prediction(conn, pid)["outcome"] == "correct"
