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


def _strat_row(conn, name, status="active"):
    conn.execute(
        "INSERT INTO strategies (name,file_path,status,timescale,"
        "mechanism_family,created_at,updated_at) VALUES "
        "(?,?,?,'intraday','flow',0,0)", (name, f"{name}.md", status))
    conn.commit()


def _resolved(conn, strat, far, outcome, mae, reached_far, reached_near=False):
    """A resolved prediction with path stats; reached_far/near = it tagged that edge."""
    pid = _record(conn, strategy_name=strat, kind="strategy",
                  near_edge_pct=far / 2.0, far_edge_pct=far)
    write.resolve_prediction(conn, pid, outcome, resolved_by="r",
                             realized_value={"mae_pct": mae})
    cols = (["reached_far_at"] if reached_far else []) + \
           (["reached_near_at"] if reached_near else [])
    for col in cols:
        conn.execute(f"UPDATE predictions SET {col}=? WHERE id=?", (time.time(), pid))
    if cols:
        conn.commit()
    return pid


class TestStrategyExpectancy:
    """Expectancy gate (graduation + entry) — replaces survivorship-biased rr.
    The win signal is reached_far (tagged TP), NOT a floor/horizon 'correct'."""

    def test_tagged_far_winners_are_tradeable(self, conn):
        _strat_row(conn, "good")
        for _ in range(12):                 # win: tagged far, tiny adverse
            _resolved(conn, "good", far=3.0, outcome="correct", mae=-0.3, reached_far=True)
        for _ in range(4):                  # loser: big adverse, never tagged far
            _resolved(conn, "good", far=3.0, outcome="wrong", mae=-2.0, reached_far=False)
        exp = queries.strategy_expectancy(conn, "good")
        assert exp["n"] == 16 and exp["wins"] == 12
        assert exp["expectancy_pct"] > 0 and exp["tradeable"] is True

    def test_floor_correct_mirage_not_tradeable(self, conn):
        # 'correct' by floor/horizon but never tagged far, with big adverse moves:
        # rr (winners-only MFE/MAE) would bless it; expectancy correctly refuses.
        _strat_row(conn, "mirage")
        for _ in range(16):
            _resolved(conn, "mirage", far=1.0, outcome="correct", mae=-3.0, reached_far=False)
        exp = queries.strategy_expectancy(conn, "mirage")
        assert exp["wins"] == 0
        assert exp["expectancy_pct"] < 0 and exp["tradeable"] is False

    def test_near_exit_graduates_high_winrate(self, conn):
        # Reaches NEAR reliably (tagged near, tiny adverse), never far — profitable
        # only under the near-edge (alert-up) exit. Graduates on best_target=near.
        _strat_row(conn, "rev")
        for _ in range(12):
            _resolved(conn, "rev", far=2.0, outcome="correct", mae=-0.3,
                      reached_far=False, reached_near=True)
        for _ in range(4):                  # losers set the stop (~1.5%)
            _resolved(conn, "rev", far=2.0, outcome="wrong", mae=-1.5,
                      reached_far=False, reached_near=False)
        exp = queries.strategy_expectancy(conn, "rev")
        assert exp["best_target"] == "near"
        assert exp["expectancy_near"] > 0 and (exp["expectancy_far"] or 0) <= 0
        assert exp["tradeable"] is True

    def test_below_min_n_not_tradeable(self, conn):
        _strat_row(conn, "thin")            # +EV but only n=10 < 15
        for _ in range(7):                  # wins: adverse well inside the stop
            _resolved(conn, "thin", far=3.0, outcome="correct", mae=-0.3, reached_far=True)
        for _ in range(3):                  # losers set the p75 stop (~2%)
            _resolved(conn, "thin", far=3.0, outcome="wrong", mae=-2.0, reached_far=False)
        exp = queries.strategy_expectancy(conn, "thin")
        assert exp["expectancy_pct"] > 0 and exp["n"] == 10
        assert exp["tradeable"] is False


class TestBestActionable:
    def test_picks_open_pred_of_tradeable_active(self, conn):
        _strat_row(conn, "good")
        for _ in range(12):
            _resolved(conn, "good", far=3.0, outcome="correct", mae=-0.3, reached_far=True)
        for _ in range(4):
            _resolved(conn, "good", far=3.0, outcome="wrong", mae=-2.0, reached_far=False)
        open_pid = _record(conn, strategy_name="good", kind="strategy",
                           near_edge_pct=1.5, far_edge_pct=3.0)
        best = queries.best_actionable_prediction(conn)
        assert best is not None and best["id"] == open_pid and best["ev_pct"] > 0

    def test_none_when_strategy_not_tradeable(self, conn):
        _strat_row(conn, "mirage")
        for _ in range(16):
            _resolved(conn, "mirage", far=1.0, outcome="correct", mae=-3.0, reached_far=False)
        _record(conn, strategy_name="mirage", kind="strategy",
                near_edge_pct=0.5, far_edge_pct=1.0)
        assert queries.best_actionable_prediction(conn) is None

    def test_none_when_no_active_strategy(self, conn):
        _strat_row(conn, "t", status="test")
        _record(conn, strategy_name="t", kind="strategy")
        assert queries.best_actionable_prediction(conn) is None

    def test_stale_prediction_not_funded(self, conn):
        _strat_row(conn, "good")
        for _ in range(12):
            _resolved(conn, "good", far=3.0, outcome="correct", mae=-0.3, reached_far=True)
        for _ in range(4):
            _resolved(conn, "good", far=3.0, outcome="wrong", mae=-2.0, reached_far=False)
        # a prediction registered an hour ago is NOT funded (entry conditions aged)
        _record(conn, strategy_name="good", kind="strategy",
                near_edge_pct=1.5, far_edge_pct=3.0, ts=time.time() - 3600)
        assert queries.best_actionable_prediction(conn) is None
        # but a fresh one from the same strategy IS picked
        fresh = _record(conn, strategy_name="good", kind="strategy",
                        near_edge_pct=1.5, far_edge_pct=3.0)
        assert queries.best_actionable_prediction(conn)["id"] == fresh


class TestCostMargin:
    """The expectancy gate is net of estimated round-trip execution cost —
    a fee-thin paper edge must NOT be tradeable."""

    def test_fee_thin_edge_not_tradeable(self, conn):
        # Wins tag a tiny far edge (0.3%) against a 0.4% stop: paper
        # expectancy ~ +0.08%/trade — positive, but below the ~0.15%
        # round-trip cost. (11/16 wins keeps the winners' MAE below the
        # percentile stop, so the stop derives from the losers.)
        _strat_row(conn, "feethin")
        for _ in range(11):
            _resolved(conn, "feethin", far=0.3, outcome="correct", mae=-0.05,
                      reached_far=True)
        for _ in range(5):
            _resolved(conn, "feethin", far=0.3, outcome="wrong", mae=-0.4,
                      reached_far=False)
        exp = queries.strategy_expectancy(conn, "feethin")
        assert exp["expectancy_pct"] is not None
        assert 0 < exp["expectancy_pct"] <= queries.ESTIMATED_ROUND_TRIP_COST_PCT
        assert exp["cost_margin_pct"] == queries.ESTIMATED_ROUND_TRIP_COST_PCT
        assert exp["tradeable"] is False

    def test_solid_edge_clears_cost_margin(self, conn):
        _strat_row(conn, "solid")
        for _ in range(12):
            _resolved(conn, "solid", far=3.0, outcome="correct", mae=-0.3,
                      reached_far=True)
        for _ in range(4):
            _resolved(conn, "solid", far=3.0, outcome="wrong", mae=-2.0,
                      reached_far=False)
        exp = queries.strategy_expectancy(conn, "solid")
        assert exp["expectancy_pct"] > queries.ESTIMATED_ROUND_TRIP_COST_PCT
        assert exp["tradeable"] is True
