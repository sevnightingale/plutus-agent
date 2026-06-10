"""lifecycle.db v2 — schema, writers, criteria, queries."""

import time

import pytest

from trading.lifecycle import criteria, queries, write
from trading.lifecycle.db import SCHEMA_VERSION, derive_timescale, get_db


@pytest.fixture()
def conn(tmp_path):
    c = get_db(tmp_path / "lifecycle.db")
    yield c
    c.close()


def _criteria(threshold=110_000.0, op="gte"):
    return {"data_point": "hl_price", "params": {"symbol": "BTC"},
            "op": op, "threshold": threshold}


def _draft(**over):
    base = dict(
        claim_md="BTC reaches 110k within 24h",
        horizon_ts=time.time() + 12 * 3600,
        success_criteria=_criteria(),
        conviction=0.7,
        agent="plutus-predict",
        symbol="BTC",
        strategy_name="funding-flush-reversal",
        regime_tag="ranging/compressed",
    )
    base.update(over)
    return write.PredictionDraft(**base)


class TestSchema:
    def test_fresh_create_stamps_version(self, conn):
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION

    def test_refuses_pre_v2_file(self, tmp_path):
        import sqlite3
        old = tmp_path / "lifecycle.db"
        c = sqlite3.connect(str(old))
        c.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        c.execute("INSERT INTO schema_version VALUES (1)")
        c.commit()
        c.close()
        with pytest.raises(RuntimeError, match="fresh-create only"):
            get_db(old)


class TestTimescale:
    def test_buckets(self):
        now = time.time()
        assert derive_timescale(now, now + 3600) == "intraday"
        assert derive_timescale(now, now + 3 * 86400) == "swing"
        assert derive_timescale(now, now + 20 * 86400) == "position"

    def test_cap_rejected(self):
        now = time.time()
        with pytest.raises(ValueError, match="30d cap"):
            derive_timescale(now, now + 45 * 86400)

    def test_backwards_horizon_rejected(self):
        now = time.time()
        with pytest.raises(ValueError):
            derive_timescale(now, now - 1)


class TestRecordPrediction:
    def test_round_trip(self, conn):
        pid = write.record_prediction(conn, _draft(support_scores=[
            write.SupportScore("hl_funding", 0.8, "numerical",
                               normalizer="funding_zscore", weight=0.3),
            write.SupportScore("news_digest", 0.6, "narrative", weight=0.2,
                               reasoning_md="ETF inflow headline supports the squeeze read."),
        ]))
        got = queries.prediction(conn, pid)
        assert got["claim_md"].startswith("BTC reaches")
        assert got["timescale"] == "intraday"
        assert len(got["support_scores"]) == 2
        narrative = [s for s in got["support_scores"] if s["kind"] == "narrative"][0]
        assert "ETF inflow" in narrative["reasoning_md"]

    def test_refuses_unresolvable_criteria(self, conn):
        with pytest.raises(ValueError, match="refused"):
            write.record_prediction(conn, _draft(success_criteria={"op": "gte"}))

    def test_refuses_unknown_data_point_when_registry_given(self, conn):
        with pytest.raises(ValueError, match="not registered"):
            write.record_prediction(
                conn, _draft(), known_data_points={"something_else"}
            )

    def test_refuses_strategyless_strategy_kind(self, conn):
        with pytest.raises(ValueError, match="file-at-birth"):
            write.record_prediction(conn, _draft(strategy_name=None))

    def test_stress_kind_allows_no_strategy(self, conn):
        pid = write.record_prediction(
            conn, _draft(strategy_name=None, kind="stress"))
        assert queries.prediction(conn, pid)["kind"] == "stress"

    def test_refuses_unreasoned_narrative_score(self, conn):
        with pytest.raises(ValueError, match="reasoning"):
            write.record_prediction(conn, _draft(support_scores=[
                write.SupportScore("news_digest", 0.6, "narrative"),
            ]))

    def test_refuses_over_cap_horizon(self, conn):
        with pytest.raises(ValueError, match="30d cap"):
            write.record_prediction(
                conn, _draft(horizon_ts=time.time() + 60 * 86400))


class TestResolvePrediction:
    def test_resolution_updates_strategy_counters(self, conn):
        conn.execute(
            """INSERT INTO strategies (name, file_path, status, timescale,
               mechanism_family, created_at, updated_at)
               VALUES ('funding-flush-reversal', 'x.md', 'test', 'intraday',
                       'flow', 0, 0)""")
        conn.commit()
        pid = write.record_prediction(conn, _draft())
        write.resolve_prediction(conn, pid, "correct", resolved_by="plutus-ops")
        stats = queries.strategy_stats(conn, "funding-flush-reversal")
        assert stats["n_resolved"] == 1 and stats["n_correct"] == 1
        assert stats["win_rate"] == 1.0

    def test_double_resolution_refused(self, conn):
        pid = write.record_prediction(conn, _draft())
        write.resolve_prediction(conn, pid, "wrong", resolved_by="plutus-ops")
        with pytest.raises(ValueError, match="already resolved"):
            write.resolve_prediction(conn, pid, "correct", resolved_by="plutus-ops")

    def test_due_listing(self, conn):
        past = write.record_prediction(
            conn, _draft(ts=time.time() - 7200, horizon_ts=time.time() - 3600))
        write.record_prediction(conn, _draft())  # future horizon
        due = queries.due_predictions(conn)
        assert [d["id"] for d in due] == [past]


class TestFundedChain:
    def test_prediction_to_outcome_chain(self, conn):
        pid = write.record_prediction(conn, _draft())
        tid = write.record_thesis(
            conn, prediction_id=pid, symbol="BTC",
            text_md="Funding flush; entry 104100, stop 102600.",
            agent="plutus-trade", sl_price=102600.0)
        did = write.record_decision(
            conn, thesis_id=tid, action="open_long", agent="plutus-main",
            conviction=0.7)
        trid = write.record_trade(
            conn, decision_id=did, venue="hyperliquid", symbol="BTC",
            side="long", size=0.001, fill_price=104100.0)
        posid = write.open_position(
            conn, venue="hyperliquid", symbol="BTC", side="long", size=0.001,
            opening_trade_id=trid)

        pos = queries.open_position(conn)
        assert pos["id"] == posid
        assert pos["thesis"]["prediction_id"] == pid
        assert pos["prediction"]["conviction"] == 0.7

        close_trid = write.record_trade(
            conn, decision_id=did, venue="hyperliquid", symbol="BTC",
            side="close", size=0.001, fill_price=105000.0)
        write.close_position(conn, position_id=posid, closing_trade_id=close_trid)
        write.record_outcome(conn, position_id=posid, realized_pnl_usd=0.9,
                             r_multiple=0.6, exit_reason="tp")

        assert queries.open_position(conn) is None
        recent = queries.recent_outcomes(conn)
        assert recent[0]["position_id"] == posid
        assert recent[0]["prediction_id"] == pid

    def test_thesis_requires_real_prediction(self, conn):
        with pytest.raises(ValueError, match="does not exist"):
            write.record_thesis(conn, prediction_id=999, symbol="BTC",
                                text_md="x", agent="plutus-trade")


class TestCalibration:
    def test_curve_excludes_ambiguous(self, conn):
        for conviction, outcome in [
            (0.72, "correct"), (0.75, "wrong"), (0.78, "correct"),
            (0.45, "wrong"), (0.62, "ambiguous"),
        ]:
            pid = write.record_prediction(conn, _draft(conviction=conviction))
            write.resolve_prediction(conn, pid, outcome, resolved_by="plutus-ops")
        cal = queries.calibration(conn)
        assert cal["n_resolved"] == 5
        assert cal["n_excluded_ambiguous"] == 1
        assert cal["buckets"]["0.7-0.8"]["n"] == 3
        assert cal["buckets"]["0.7-0.8"]["hit_rate"] == pytest.approx(2 / 3, abs=0.001)

    def test_filters(self, conn):
        pid = write.record_prediction(conn, _draft())
        write.resolve_prediction(conn, pid, "correct", resolved_by="plutus-ops")
        assert queries.calibration(conn, strategy_name="nope")["n_resolved"] == 0
        assert queries.calibration(
            conn, strategy_name="funding-flush-reversal")["n_resolved"] == 1


class TestCriteria:
    def test_validate_combinators(self):
        good = {"all": [_criteria(), {"any": [_criteria(op="lte")]}]}
        assert criteria.validate(good) == []
        assert criteria.validate({"all": []})
        assert criteria.validate({"all": [_criteria()], "any": [_criteria()]})

    def test_validate_crosses_needs_baseline(self):
        bad = {"data_point": "hl_price", "op": "crosses_above", "threshold": 1.0}
        assert any("baseline" in p for p in criteria.validate(bad))

    def test_resolve_simple(self):
        fetch = lambda dp, params: 111_000.0
        assert criteria.resolve(_criteria(), fetch) == "correct"
        fetch_low = lambda dp, params: 100_000.0
        assert criteria.resolve(_criteria(), fetch_low) == "wrong"

    def test_resolve_unresolvable_on_failed_fetch(self):
        assert criteria.resolve(_criteria(), lambda dp, p: None) == "unresolvable"

    def test_resolve_crosses_uses_window(self):
        crit = {"data_point": "hl_price", "op": "crosses_above",
                "threshold": 110_000.0,
                "baseline": {"value": 104_000.0, "ts": time.time() - 3600}}
        # current reading back below, but the window high crossed
        fetch = lambda dp, p: 105_000.0
        fetch_extreme = lambda dp, p, since: (103_000.0, 111_000.0)
        assert criteria.resolve(crit, fetch, fetch_extreme) == "correct"
        fetch_extreme_no = lambda dp, p, since: (103_000.0, 108_000.0)
        assert criteria.resolve(crit, fetch, fetch_extreme_no) == "wrong"

    def test_resolve_any_combinator(self):
        crit = {"any": [_criteria(threshold=200_000.0), _criteria(op="lte", threshold=120_000.0)]}
        assert criteria.resolve(crit, lambda dp, p: 111_000.0) == "correct"


class TestWatchdogSource:
    def test_last_action_runs(self, conn):
        write.record_action_run(conn, action_type="perception",
                                agent="plutus-perception", ts=100.0)
        write.record_action_run(conn, action_type="perception",
                                agent="plutus-perception", ts=200.0)
        write.record_action_run(conn, action_type="regime",
                                agent="plutus-regime", ts=150.0)
        last = queries.last_action_runs(conn)
        assert last == {"perception": 200.0, "regime": 150.0}
