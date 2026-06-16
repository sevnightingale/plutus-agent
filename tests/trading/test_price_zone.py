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

    def test_classify(self):
        c = price_zone.classify
        # far reached → target (correct early); a full target beats invalidation
        assert c(5.0, 10.0, 10.0, False) == "target"
        assert c(5.0, 10.0, 11.0, False, invalidation_tripped=True) == "target"
        # near reached, far not, horizon open → mark_near (win locked, stays open)
        assert c(5.0, 10.0, 6.0, False) == "mark_near"
        # near already stamped, still below far → open (no re-stamp)
        assert c(5.0, 10.0, 6.0, False, near_already_reached=True) == "open"
        # horizon: near reached → horizon-correct; never reached → expired
        assert c(5.0, 10.0, 6.0, True) == "horizon"
        assert c(5.0, 10.0, 4.9, True) == "expired"
        assert c(5.0, 10.0, None, True, near_already_reached=True) == "horizon"
        # below near, horizon open, invalidation trips → invalidated
        assert c(5.0, 10.0, 4.9, False, invalidation_tripped=True) == "invalidated"
        # LOCK: once near is reached, invalidation can no longer fire
        assert c(5.0, 10.0, 4.9, False, invalidation_tripped=True,
                 near_already_reached=True) == "open"
        # below near, nothing tripped → open
        assert c(5.0, 10.0, 4.9, False) == "open"
        # bearish uses magnitudes (mfe is a positive magnitude)
        assert c(-5.0, -10.0, 10.0, False) == "target"

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
    def test_near_marks_open_then_far_resolves_target(self, conn):
        pid = _record(conn)  # near +5% (105k), far +10% (110k)
        t = time.time()
        # +6% reaches near, not far → marked near, STAYS OPEN (win locked)
        r1 = resolver.resolve_open_predictions(
            conn, mids={"BTC": 106_000.0}, path_stats_fn=_stats_fn(6.0), now=t)
        assert r1["resolved"] == []
        assert [m["prediction_id"] for m in r1["marked_near"]] == [pid]
        got = queries.prediction(conn, pid)
        assert got["outcome"] is None and got["reached_near_at"] is not None
        # +11% reaches far → correct early (target)
        r2 = resolver.resolve_open_predictions(
            conn, mids={"BTC": 111_000.0}, path_stats_fn=_stats_fn(11.0), now=t + 10)
        assert r2["resolved"][0]["outcome"] == "correct"
        assert r2["resolved"][0]["mode"] == "target"
        got = queries.prediction(conn, pid)
        rv = json.loads(got["realized_value_json"])
        assert rv["resolution_mode"] == "target" and got["reached_far_at"] is not None
        assert rv["profit_score"] == pytest.approx((11 - 5) / (10 - 5))

    def test_bearish_far_resolves_target(self, conn):
        _record(conn, near_edge_pct=-5.0, far_edge_pct=-10.0)
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 89_000.0}, path_stats_fn=_stats_fn(11.0), now=time.time())
        assert res["resolved"][0]["outcome"] == "correct"
        assert res["resolved"][0]["mode"] == "target"

    def test_horizon_correct_when_near_reached(self, conn):
        t0 = time.time()
        pid = _record(conn, ts=t0, horizon_ts=t0 + 60)
        resolver.resolve_open_predictions(  # near reached before horizon
            conn, mids={"BTC": 106_000.0}, path_stats_fn=_stats_fn(6.0), now=t0 + 10)
        res = resolver.resolve_open_predictions(  # horizon passes, far never hit
            conn, mids={"BTC": 107_000.0}, path_stats_fn=_stats_fn(6.0), now=t0 + 120)
        assert res["resolved"][0]["outcome"] == "correct"
        assert res["resolved"][0]["mode"] == "horizon"
        assert queries.prediction(conn, pid)["reached_far_at"] is None

    def test_horizon_expiry_resolves_wrong(self, conn):
        t0 = time.time()
        _record(conn, ts=t0, horizon_ts=t0 + 60)  # near never reached
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
        assert res["resolved"] == [] and res["marked_near"] == []
        assert not called  # the cheap path never touched the network

    def test_invalidation_resolves_wrong(self, conn):
        _record(conn, invalidation_criteria={
            "data_point": "hl_price", "params": {"symbol": "BTC"},
            "op": "lte", "threshold": 90_000.0})
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 101_000.0}, path_stats_fn=_stats_fn(1.0),
            fetch_fn=lambda name, p: 85_000.0, now=time.time())  # price ≤ 90k → invalidated
        assert res["resolved"][0]["outcome"] == "wrong"
        assert res["resolved"][0]["mode"] == "invalidated"

    def test_near_lock_blocks_invalidation(self, conn):
        pid = _record(conn, invalidation_criteria={
            "data_point": "hl_price", "op": "lte", "threshold": 90_000.0})
        # near reached (+6%) AND the invalidation would trip — the win is LOCKED,
        # invalidation can't fire; stays open (marked near), not wrong.
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 106_000.0}, path_stats_fn=_stats_fn(6.0),
            fetch_fn=lambda name, p: 85_000.0, now=time.time())
        assert res["resolved"] == []
        assert [m["prediction_id"] for m in res["marked_near"]] == [pid]

    def test_far_beats_invalidation(self, conn):
        _record(conn, invalidation_criteria={
            "data_point": "hl_price", "op": "lte", "threshold": 90_000.0})
        # far reached AND the invalidation would trip — a full target wins
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 111_000.0}, path_stats_fn=_stats_fn(11.0),
            fetch_fn=lambda name, p: 85_000.0, now=time.time())
        assert res["resolved"][0]["outcome"] == "correct"
        assert res["resolved"][0]["mode"] == "target"

    def test_second_pass_is_empty(self, conn):
        _record(conn)
        mids = {"BTC": 111_000.0}  # straight to far → resolves on pass 1
        r1 = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=_stats_fn(11.0), now=time.time())
        assert len(r1["resolved"]) == 1
        r2 = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=_stats_fn(11.0), now=time.time())
        assert r2["resolved"] == [] and r2["open_count"] == 0

    def test_deep_sweep_catches_missed_near_wick(self, conn):
        # The live mid is back AT entry (mid_mfe == 0 — the watcher sees nothing),
        # but the candle path wicked past the near edge and recovered. The cheap
        # watcher pass leaves it open and pulls no candles; the deep ops sweep
        # pulls candles, sees the +6% favorable wick, and LOCKS the floor.
        # (This is the #71/BOJ case: a near touch between 5s mid polls.)
        pid = _record(conn)  # near +5% (105k), far +10%
        t = time.time()
        mids = {"BTC": 100_000.0}  # exactly entry → mid sees nothing
        called = []

        def wick_stats(*a, **k):
            called.append(1)
            return {"mfe_pct": 6.0, "mae_pct": -1.0, "range_pct": 7.0}

        # Cheap watcher pass: no candle pull, prediction stays fully open.
        r_cheap = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=wick_stats, now=t)
        assert r_cheap["marked_near"] == [] and r_cheap["resolved"] == []
        assert not called  # the mid said nothing → no candle look-back
        assert queries.prediction(conn, pid)["reached_near_at"] is None

        # Deep ops sweep: candle look-back catches the wick → marks near.
        r_deep = resolver.resolve_open_predictions(
            conn, mids=mids, path_stats_fn=wick_stats, now=t + 5, deep=True)
        assert [m["prediction_id"] for m in r_deep["marked_near"]] == [pid]
        assert called  # the deep sweep DID pull candles
        assert queries.prediction(conn, pid)["reached_near_at"] is not None

    def test_deep_sweep_resolves_missed_far_wick(self, conn):
        # A wick that touched the FAR target and recovered (mid sees nothing)
        # resolves CORRECT (target) on the deep sweep — the optimistic target
        # was genuinely hit.
        pid = _record(conn)  # near +5%, far +10% (110k)
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 100_000.0}, path_stats_fn=_stats_fn(11.0),
            now=time.time(), deep=True)
        assert res["resolved"][0]["outcome"] == "correct"
        assert res["resolved"][0]["mode"] == "target"
        assert queries.prediction(conn, pid)["reached_far_at"] is not None

    def test_deep_sweep_leaves_open_when_path_flat(self, conn):
        # Deep mode pulls candles but the path never reached the near edge —
        # still open, nothing marked or resolved.
        pid = _record(conn)  # near +5%
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 100_000.0}, path_stats_fn=_stats_fn(2.0),
            now=time.time(), deep=True)
        assert res["resolved"] == [] and res["marked_near"] == []
        assert queries.prediction(conn, pid)["reached_near_at"] is None


class TestOpsSweepWiring:
    """The resolve_due_predictions dispatcher wires all_mids + path_stats into
    the shared resolver against the (conftest-isolated) lifecycle.db."""

    def test_sweep_resolves_a_target(self, monkeypatch):
        from trading.dispatchers import resolution
        from trading.lifecycle.db import get_db
        from trading.integrations.hyperliquid import _client, outcomes

        conn = get_db()  # conftest points this at a per-test tempdir
        pid = write.record_prediction(conn, _draft())  # near +5%, far +10% (110k)

        info = MagicMock()
        info.all_mids.return_value = {"BTC": "111000"}  # above the FAR edge
        monkeypatch.setattr(_client, "get_info", lambda: info)
        monkeypatch.setattr(
            outcomes, "path_stats",
            lambda *a, **k: {"mfe_pct": 11.0, "mae_pct": -1.0, "range_pct": 12.0})

        res = json.loads(resolution._resolve_due({}))
        assert [r["outcome"] for r in res["resolved"]] == ["correct"]
        assert queries.prediction(conn, pid)["outcome"] == "correct"


class TestStrategyRR:
    """RR = median favorable ÷ median adverse over a strategy's wins — the
    graduation trade-worthiness gate, surfaced in strategy_book."""

    def _strat_row(self, conn, name):
        conn.execute(
            "INSERT INTO strategies (name,file_path,status,timescale,"
            "mechanism_family,created_at,updated_at) VALUES "
            "(?,?, 'test','intraday','flow',0,0)", (name, f"{name}.md"))
        conn.commit()

    def _win(self, conn, strat, mfe, mae):
        pid = _record(conn, strategy_name=strat, kind="strategy")
        write.resolve_prediction(conn, pid, "correct", resolved_by="r",
                                 realized_value={"mfe_pct": mfe, "mae_pct": mae})

    def test_rr_is_ratio_of_medians(self, conn):
        self._strat_row(conn, "s")
        self._win(conn, "s", 3.0, -1.0)
        self._win(conn, "s", 2.0, -1.0)
        self._win(conn, "s", 4.0, -2.0)  # median mfe 3, median |mae| 1 → RR 3.0
        assert queries.strategy_rr(conn, "s") == 3.0
        assert queries.strategy_book(conn)[0]["rr"] == 3.0

    def test_rr_none_without_wins(self, conn):
        self._strat_row(conn, "s")
        pid = _record(conn, strategy_name="s", kind="strategy")
        write.resolve_prediction(conn, pid, "wrong", resolved_by="r",
                                 realized_value={"mae_pct": -1.0, "resolution_mode": "expired"})
        assert queries.strategy_rr(conn, "s") is None


class TestReachedNear:
    def test_mark_reached_near_idempotent(self, conn):
        pid = _record(conn)
        assert write.mark_reached_near(conn, pid, ts=123.0) is True
        assert write.mark_reached_near(conn, pid, ts=456.0) is False  # already stamped
        got = queries.prediction(conn, pid)
        assert got["reached_near_at"] == 123.0 and got["resolved_at"] is None
