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

    def _arm_pilot(self):
        from tests.trading.conftest import arm_pilot
        arm_pilot()

    def test_pilot_lane_picks_highest_conviction_test_book(self, conn):
        self._arm_pilot()
        _strat_row(conn, "t1", status="test")
        _strat_row(conn, "t2", status="test")
        _record(conn, strategy_name="t1", kind="strategy", conviction=0.62)
        top = _record(conn, strategy_name="t2", kind="strategy", conviction=0.71)
        best = queries.best_actionable_prediction(conn)
        assert best is not None and best["id"] == top and best["lane"] == "pilot"

    def test_pilot_lane_respects_conviction_threshold_and_recency(self, conn):
        self._arm_pilot()
        _strat_row(conn, "t", status="test")
        _record(conn, strategy_name="t", kind="strategy", conviction=0.45)
        _record(conn, strategy_name="t", kind="strategy", conviction=0.9,
                ts=time.time() - 3600)     # fresh-enough gate applies to pilot too
        assert queries.best_actionable_prediction(conn) is None

    def test_graduated_lane_outranks_pilot(self, conn):
        self._arm_pilot()
        _strat_row(conn, "good")
        for _ in range(12):
            _resolved(conn, "good", far=3.0, outcome="correct", mae=-0.3, reached_far=True)
        for _ in range(4):
            _resolved(conn, "good", far=3.0, outcome="wrong", mae=-2.0, reached_far=False)
        grad = _record(conn, strategy_name="good", kind="strategy",
                       near_edge_pct=1.5, far_edge_pct=3.0)
        _strat_row(conn, "t", status="test")
        _record(conn, strategy_name="t", kind="strategy", conviction=0.99)
        best = queries.best_actionable_prediction(conn)
        assert best["id"] == grad and best["lane"] == "graduated"

    def test_pilot_lane_dead_when_not_armed(self, conn):
        _strat_row(conn, "t", status="test")
        _record(conn, strategy_name="t", kind="strategy", conviction=0.9)
        assert queries.best_actionable_prediction(conn) is None


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


class TestMultiplicity:
    """The hurdle is deflated by search breadth (trading-design import A):
    a book that clears the bar as a lone hypothesis must NOT clear it as the
    survivor of thirty sibling trials at the same timescale."""

    def _borderline_book(self, conn, name):
        # 10 wins (reward 2%) / 6 losses (stop 2%), losses interleaved so the
        # trailing hazard window stays positive: expectancy 0.5%/trade — above
        # cost (0.15%) but only ~0.25 stdevs of selection premium away from it.
        _strat_row(conn, name)
        seq = [True, False, True, False, True, False] + \
              [False, True, True, False, True, True, False, True, True, True]
        assert sum(seq) == 10 and len(seq) == 16
        assert sum(seq[-10:]) == 7        # trailing window stays positive
        for is_win in seq:
            if is_win:
                _resolved(conn, name, far=2.0, outcome="correct", mae=-0.3,
                          reached_far=True)
            else:
                _resolved(conn, name, far=2.0, outcome="wrong", mae=-2.0,
                          reached_far=False)

    def test_lone_strategy_pays_no_premium(self, conn):
        self._borderline_book(conn, "cand")
        exp = queries.strategy_expectancy(conn, "cand")
        assert exp["siblings_tried"] == 1
        assert exp["multiplicity_premium_pct"] == 0.0
        assert exp["hurdle_pct"] == exp["cost_margin_pct"]
        assert exp["decaying"] is False
        assert exp["tradeable"] is True

    def _serious_siblings(self, conn, n, status):
        for i in range(n):
            _strat_row(conn, f"sib{i}", status=status)
            for _ in range(queries.SERIOUS_TRIAL_MIN_N):
                _resolved(conn, f"sib{i}", far=3.0, outcome="wrong", mae=-2.0,
                          reached_far=False)

    def test_sibling_trials_raise_the_hurdle(self, conn):
        self._borderline_book(conn, "cand")
        # 30 SERIOUS sibling trials (books of ≥ SERIOUS_TRIAL_MIN_N
        # resolutions) live at the same timescale.
        self._serious_siblings(conn, 30, status="test")
        exp = queries.strategy_expectancy(conn, "cand")
        assert exp["siblings_tried"] == 31
        assert exp["multiplicity_premium_pct"] > 0
        assert exp["hurdle_pct"] > exp["cost_margin_pct"]
        # the book itself is unchanged — only the bar moved
        assert exp["expectancy_pct"] > queries.ESTIMATED_ROUND_TRIP_COST_PCT
        assert exp["tradeable"] is False

    def test_retired_siblings_do_not_count(self, conn):
        """Reversed on 2026-07-27; it asserted the opposite until then.

        Retired books counted on the reasoning that pruning must not launder
        multiplicity — the purer statistic, and the reason M could only ever
        grow. Measured on the live desk that made 81-94% of every hurdle
        premium rather than trading cost, with nothing ever graduating; a bar
        that rises forever eventually forbids everything.

        The laundering risk is real and is closed on the other side instead:
        retirement now requires lifetime expectancy <= 0 at n >= 20, every
        judgement-based pruning move goes to dormancy (which still counts,
        above), and desk_integrity_check reports any book retired while still
        profitable. Evidence can lower this bar; judgement cannot.
        """
        self._borderline_book(conn, "cand")
        self._serious_siblings(conn, 30, status="retired")
        exp = queries.strategy_expectancy(conn, "cand")
        assert exp["siblings_tried"] == 1
        assert exp["multiplicity_premium_pct"] == 0.0
        assert exp["hurdle_pct"] == exp["cost_margin_pct"]
        assert exp["tradeable"] is True

    def test_thin_siblings_do_not_raise_the_hurdle(self, conn):
        # Serious-trial M: a one-resolution noise book was never an
        # independent trial — 30 of them must not pad the bar for a leader.
        self._borderline_book(conn, "cand")
        for i in range(30):
            _strat_row(conn, f"noise{i}", status="test")
            _resolved(conn, f"noise{i}", far=3.0, outcome="wrong", mae=-2.0,
                      reached_far=False)
        exp = queries.strategy_expectancy(conn, "cand")
        assert exp["siblings_tried"] == 1     # only cand's own serious book
        assert exp["multiplicity_premium_pct"] == 0.0
        assert exp["tradeable"] is True


class TestNToClear:
    """n_to_clear — the path-to-graduation projection: the premium shrinks
    with the strategy's own √n, so a real edge above cost always converges;
    an edge at/below cost reports None (never — structural, not patience)."""

    def test_lone_strategy_clears_at_min_n(self, conn):
        _strat_row(conn, "solo")
        for _ in range(12):
            _resolved(conn, "solo", far=3.0, outcome="correct", mae=-0.3,
                      reached_far=True)
        for _ in range(4):
            _resolved(conn, "solo", far=3.0, outcome="wrong", mae=-2.0,
                      reached_far=False)
        exp = queries.strategy_expectancy(conn, "solo")
        assert exp["siblings_tried"] == 1
        assert exp["n_to_clear"] == queries.GRADUATION_MIN_N

    def test_siblings_push_n_to_clear_out_but_finite(self, conn):
        # Borderline edge (+0.5%/trade, σ=2) against 30 serious siblings:
        # blocked today, but the projection is FINITE — evidence converges.
        _strat_row(conn, "grind")
        for _ in range(10):
            _resolved(conn, "grind", far=2.0, outcome="correct", mae=-0.3,
                      reached_far=True)
        for _ in range(6):
            _resolved(conn, "grind", far=2.0, outcome="wrong", mae=-2.0,
                      reached_far=False)
        for i in range(30):
            _strat_row(conn, f"sib{i}", status="test")
            for _ in range(queries.SERIOUS_TRIAL_MIN_N):
                _resolved(conn, f"sib{i}", far=3.0, outcome="wrong", mae=-2.0,
                          reached_far=False)
        exp = queries.strategy_expectancy(conn, "grind")
        assert exp["tradeable"] is False
        assert exp["n_to_clear"] is not None
        assert exp["n_to_clear"] > exp["n"]
        # sanity: at that book size the hurdle actually sits below the edge
        import math
        proj = (queries.ESTIMATED_ROUND_TRIP_COST_PCT
                + math.sqrt(2 * math.log(exp["siblings_tried"]))
                * exp["pnl_stdev_pct"] / math.sqrt(exp["n_to_clear"]))
        assert exp["expectancy_pct"] > proj

    def test_edge_at_or_below_cost_never_clears(self, conn):
        _strat_row(conn, "mirage")
        for _ in range(16):
            _resolved(conn, "mirage", far=1.0, outcome="correct", mae=-3.0,
                      reached_far=False)
        assert queries.strategy_expectancy(conn, "mirage")["n_to_clear"] is None


class TestDeskGaps:
    """desk_gaps — the deterministic 'broken vs patient' read (item J)."""

    def _book(self, conn, name):
        for _ in range(12):
            _resolved(conn, name, far=3.0, outcome="correct", mae=-0.3,
                      reached_far=True)
        for _ in range(4):
            _resolved(conn, name, far=3.0, outcome="wrong", mae=-2.0,
                      reached_far=False)

    def test_gaps_counts_and_mismatches(self, conn):
        _strat_row(conn, "good")                    # active + tradeable
        self._book(conn, "good")
        _strat_row(conn, "shy", status="test")      # tradeable but still test
        self._book(conn, "shy")
        g = queries.desk_gaps(conn)
        assert g["strategy_counts"]["active"]["intraday"] == 1
        assert g["strategy_counts"]["test"]["intraday"] == 1
        names = [b["strategy"] for b in g["closest_to_tradeable"]]
        assert "good" in names and "shy" in names
        assert [b["strategy"] for b in g["status_mismatches"]] == ["shy"]
        assert g["actionable_window_s"] == queries.ACTIONABLE_MAX_AGE_S

    def test_fundable_now_counts_test_books_under_pilot(self, conn):
        from tests.trading.conftest import arm_pilot
        arm_pilot()
        _strat_row(conn, "t", status="test")
        _record(conn, strategy_name="t", kind="strategy", conviction=0.7)
        g = queries.desk_gaps(conn)
        # the observability surface must agree with the funding surface: with
        # the pilot armed, a fresh test-book prediction counts as fundable
        assert g["fundable_now"]["count"] == 1

    def test_strategy_fundable_predicate(self, conn):
        assert queries.strategy_fundable("active") is True
        assert queries.strategy_fundable("test", pilot=False) is False
        assert queries.strategy_fundable("test", pilot=True) is True
        assert queries.strategy_fundable("retired", pilot=True) is False
        assert queries.strategy_fundable(None, pilot=True) is False

    def test_fundable_now_counts_fresh_active_predictions(self, conn):
        _strat_row(conn, "good")
        self._book(conn, "good")
        _record(conn, strategy_name="good", kind="strategy",
                near_edge_pct=1.5, far_edge_pct=3.0)
        g = queries.desk_gaps(conn)
        assert g["fundable_now"]["count"] == 1
        assert g["fundable_now"]["youngest_age_s"] is not None


class TestHazard:
    """Recency check (trading-design import B): 'was this real?' and 'is it
    still?' are different questions — a dead edge must not coast on the
    strength of its historical wins."""

    def test_dead_edge_cannot_coast_on_history(self, conn):
        _strat_row(conn, "coast")
        for _ in range(12):                 # a genuinely great early book...
            _resolved(conn, "coast", far=3.0, outcome="correct", mae=-0.3,
                      reached_far=True)
        for _ in range(10):                 # ...then 10 straight recent losses
            _resolved(conn, "coast", far=3.0, outcome="wrong", mae=-2.0,
                      reached_far=False)
        exp = queries.strategy_expectancy(conn, "coast")
        # lifetime book still clears the hurdle — that is exactly the trap
        assert exp["expectancy_pct"] > exp["hurdle_pct"] and exp["n"] >= 15
        assert exp["recent"]["n"] == queries.HAZARD_WINDOW_N
        assert exp["recent"]["expectancy_pct"] < 0
        assert exp["decaying"] is True
        assert exp["tradeable"] is False

    def test_recovered_edge_is_not_decaying(self, conn):
        _strat_row(conn, "recov")           # same book, opposite order
        for _ in range(10):
            _resolved(conn, "recov", far=3.0, outcome="wrong", mae=-2.0,
                      reached_far=False)
        for _ in range(12):
            _resolved(conn, "recov", far=3.0, outcome="correct", mae=-0.3,
                      reached_far=True)
        exp = queries.strategy_expectancy(conn, "recov")
        assert exp["recent"]["expectancy_pct"] > 0
        assert exp["decaying"] is False
        assert exp["tradeable"] is True


class TestPilotCalibratedRanking:
    """The pilot lane ranks by CALIBRATED conviction (wired in 2026-08-24);
    raw stays the candidate floor; absence degrades to raw, on the record."""

    def _arm(self):
        from tests.trading.conftest import arm_pilot
        arm_pilot()

    def _stub(self, monkeypatch, mapping):
        import trading.calibration.live as live
        monkeypatch.setattr(
            live, "calibrated_conviction",
            lambda conn, pid: ({"p": mapping[pid], "version": "test-v",
                                "trained_at": 0.0}
                               if mapping.get(pid) is not None else None))

    def test_calibrated_ranking_beats_raw(self, conn, monkeypatch):
        self._arm()
        _strat_row(conn, "t1", status="test")
        _strat_row(conn, "t2", status="test")
        raw_hi = _record(conn, strategy_name="t1", kind="strategy", conviction=0.9)
        raw_lo = _record(conn, strategy_name="t2", kind="strategy", conviction=0.6)
        # The model inverts the raw order: the 0.6-raw prediction calibrates
        # higher — it must win, and the anti-calibrated 0.9 must lose.
        self._stub(monkeypatch, {raw_hi: 0.52, raw_lo: 0.61})
        best = queries.best_actionable_prediction(conn)
        assert best["id"] == raw_lo and best["lane"] == "pilot"
        assert best["conviction_calibrated"] == 0.61
        assert best["calibration_version"] == "test-v"

    def test_calibrated_below_threshold_is_filtered(self, conn, monkeypatch):
        self._arm()
        _strat_row(conn, "t", status="test")
        pid = _record(conn, strategy_name="t", kind="strategy", conviction=0.9)
        self._stub(monkeypatch, {pid: 0.42})   # model says worse than a coin
        assert queries.best_actionable_prediction(conn) is None

    def test_no_artifact_degrades_to_raw_argmax(self, conn, monkeypatch):
        self._arm()
        _strat_row(conn, "t", status="test")
        lo = _record(conn, strategy_name="t", kind="strategy", conviction=0.6)
        hi = _record(conn, strategy_name="t", kind="strategy", conviction=0.8)
        self._stub(monkeypatch, {lo: None, hi: None})
        best = queries.best_actionable_prediction(conn)
        assert best["id"] == hi and best["calibration"] == "unavailable"
