"""lifecycle.db v2 — schema, writers, criteria, queries."""

import json
import time

import pytest

from trading.lifecycle import criteria, queries, write
from trading.lifecycle.db import SCHEMA_VERSION, _has_table, derive_timescale, get_db


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
        claim_md="BTC reaches +5% within 24h",
        horizon_ts=time.time() + 12 * 3600,
        entry_ref_price=100_000.0,
        near_edge_pct=5.0,
        far_edge_pct=10.0,
        conviction=0.7,
        agent="plutus-predict",
        symbol="BTC",
        strategy_name="funding-flush-reversal",
        regime_tag="ranging/compressed",
    )
    base.update(over)
    return write.PredictionDraft(**base)


def _index_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone() is not None


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

    def test_reopening_restores_an_index_added_after_creation(self, tmp_path):
        """An at-version database must still receive newly declared indexes.

        Indexes were created only on fresh create, so any index added to
        INDEXES_SQL after a runtime's database already existed never arrived —
        the version matched, the open path returned early, nothing else ran the
        block. Silent, because the database keeps working without it. On
        2026-07-26 that cost the capital reconciler its idempotency: it leans
        entirely on ux_capital_movements_tx, the live database never got the
        index, and the same two deposits were re-inserted every ops tick.

        Dropping an index and reopening reproduces that exactly.
        """
        import sqlite3
        path = tmp_path / "lifecycle.db"
        get_db(path).close()

        c = sqlite3.connect(str(path))
        c.execute("DROP INDEX ux_capital_movements_tx")
        c.commit()
        assert not _index_exists(c, "ux_capital_movements_tx")
        c.close()

        conn = get_db(path)
        assert _index_exists(conn, "ux_capital_movements_tx"), (
            "reopening an existing database did not apply declared indexes")

    def test_reopen_survives_an_index_that_cannot_be_built(self, tmp_path, caplog):
        """Duplicates must not brick the open — loud, but not fatal.

        A database that already accumulated duplicates (as the live one had)
        cannot take the UNIQUE index. Refusing to open would strand the desk
        over a constraint; the open continues and says so.
        """
        import sqlite3
        path = tmp_path / "lifecycle.db"
        get_db(path).close()

        c = sqlite3.connect(str(path))
        c.execute("DROP INDEX ux_capital_movements_tx")
        for _ in range(2):
            c.execute(
                """INSERT INTO capital_movements (ts, token, amount_token,
                       movement_type, tx_hash) VALUES (?,?,?,?,?)""",
                (1.0, "USDC", 5.0, "send", "0xdupe"))
        c.commit()
        c.close()

        with caplog.at_level("WARNING"):
            conn = get_db(path)
        assert conn.execute("SELECT COUNT(*) FROM capital_movements").fetchone()[0] == 2
        assert not _index_exists(conn, "ux_capital_movements_tx")
        assert "ux_capital_movements_tx" in caplog.text, "the failure was not logged"


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

    def test_refuses_invalid_zone(self, conn):
        # |far| must exceed |near| — a target beyond the correctness floor.
        with pytest.raises(ValueError, match="refused"):
            write.record_prediction(conn, _draft(near_edge_pct=5.0, far_edge_pct=5.0))

    def test_refuses_mismatched_zone_direction(self, conn):
        with pytest.raises(ValueError, match="refused"):
            write.record_prediction(conn, _draft(near_edge_pct=5.0, far_edge_pct=-10.0))

    def test_refuses_unknown_invalidation_data_point(self, conn):
        with pytest.raises(ValueError, match="not registered"):
            write.record_prediction(
                conn,
                _draft(invalidation_criteria={"data_point": "hl_price",
                                              "op": "gte", "threshold": 1.0}),
                known_data_points={"something_else"},
            )

    def test_refuses_strategyless_strategy_kind(self, conn):
        with pytest.raises(ValueError, match="file-at-birth"):
            write.record_prediction(conn, _draft(strategy_name=None))

    def test_stress_kind_allows_no_strategy(self, conn):
        pid = write.record_prediction(
            conn, _draft(strategy_name=None, kind="stress"))
        assert queries.prediction(conn, pid)["kind"] == "stress"

    def test_open_predictions_per_strategy_capped(self, conn):
        for _ in range(write.MAX_OPEN_PER_STRATEGY):
            write.record_prediction(conn, _draft())
        with pytest.raises(ValueError, match="open predictions"):
            write.record_prediction(conn, _draft())
        # a different strategy is unaffected
        write.record_prediction(conn, _draft(strategy_name="other-strategy"))
        # strategyless kinds are outside the cap
        write.record_prediction(
            conn, _draft(kind="stress", strategy_name=None))

    def test_resolution_frees_strategy_capacity(self, conn):
        ids = [write.record_prediction(conn, _draft())
               for _ in range(write.MAX_OPEN_PER_STRATEGY)]
        with pytest.raises(ValueError, match="open predictions"):
            write.record_prediction(conn, _draft())
        write.resolve_prediction(conn, ids[0], "correct",
                                 resolved_by="plutus-ops")
        write.record_prediction(conn, _draft())  # capacity freed

    def test_win_locked_predictions_dont_count_toward_cap(self, conn):
        ids = [write.record_prediction(conn, _draft())
               for _ in range(write.MAX_OPEN_PER_STRATEGY)]
        with pytest.raises(ValueError, match="undecided open predictions"):
            write.record_prediction(conn, _draft())
        # Near edge reached: outcome decided (win locked) but the row stays
        # open awaiting far edge / horizon — it must not hold a cap slot.
        assert write.mark_reached_near(conn, ids[0], time.time())
        write.record_prediction(conn, _draft())


def _resolved_trade(conn, strat, win):
    """Register + immediately resolve one prediction (never holds a cap slot).
    Wins tag the far edge with tiny adverse; losses set the ~2% stop."""
    pid = write.record_prediction(conn, _draft(
        strategy_name=strat, near_edge_pct=1.5, far_edge_pct=3.0))
    write.resolve_prediction(
        conn, pid, "correct" if win else "wrong", resolved_by="t",
        realized_value={"mae_pct": -0.3 if win else -2.0},
        reached_far_at=time.time() if win else None)


class TestIncubationFastLane:
    """A book proving out — net-positive above the cost margin but not yet
    tradeable, and not decaying — earns INCUBATION_OPEN_CAP instead of the
    base cap: evidence velocity toward the multiplicity-deflated hurdle."""

    def test_incubating_book_gets_wider_cap(self, conn):
        for win in [True] * 7 + [False] * 3:      # +EV at n=10 < 15: promising
            _resolved_trade(conn, "inc", win)
        capacity = queries.strategy_prediction_capacity(conn, "inc")
        assert capacity == {
            "strategy_name": "inc", "evidence_lane": "incubation",
            "open_predictions": 0, "open_cap": write.INCUBATION_OPEN_CAP,
            "open_slots_remaining": write.INCUBATION_OPEN_CAP,
        }
        for _ in range(write.INCUBATION_OPEN_CAP):
            write.record_prediction(conn, _draft(strategy_name="inc"))
        capacity = queries.strategy_prediction_capacity(conn, "inc")
        assert capacity["open_slots_remaining"] == 0
        with pytest.raises(ValueError, match=f"cap {write.INCUBATION_OPEN_CAP}"):
            write.record_prediction(conn, _draft(strategy_name="inc"))

    def test_decaying_book_keeps_base_cap(self, conn):
        # Lifetime-positive but the trailing window is all losses — a decaying
        # book gets no fast lane (more correlated trials won't save it).
        for win in [True] * 12 + [False] * 10:
            _resolved_trade(conn, "dk", win)
        capacity = queries.strategy_prediction_capacity(conn, "dk")
        assert capacity["evidence_lane"] == "base"
        assert capacity["open_cap"] == write.MAX_OPEN_PER_STRATEGY
        for _ in range(write.MAX_OPEN_PER_STRATEGY):
            write.record_prediction(conn, _draft(strategy_name="dk"))
        with pytest.raises(ValueError, match=f"cap {write.MAX_OPEN_PER_STRATEGY}"):
            write.record_prediction(conn, _draft(strategy_name="dk"))

    def test_tradeable_book_keeps_base_cap(self, conn):
        # Already clears the hurdle — its predictions are for trading, not
        # for inflating n.
        for win in [True] * 12 + [False] * 4:
            _resolved_trade(conn, "tr", win)
        capacity = queries.strategy_prediction_capacity(conn, "tr")
        assert capacity["evidence_lane"] == "base"
        assert capacity["open_cap"] == write.MAX_OPEN_PER_STRATEGY
        for _ in range(write.MAX_OPEN_PER_STRATEGY):
            write.record_prediction(conn, _draft(strategy_name="tr"))
        with pytest.raises(ValueError, match=f"cap {write.MAX_OPEN_PER_STRATEGY}"):
            write.record_prediction(conn, _draft(strategy_name="tr"))

    def test_open_slot_counts_shape(self, conn):
        write.record_prediction(conn, _draft())
        write.record_prediction(conn, _draft(strategy_name="other-strategy"))
        locked = write.record_prediction(conn, _draft())
        write.mark_reached_near(conn, locked, time.time())
        counts = queries.open_slot_counts(conn)
        assert counts["open_total"] == 3
        assert sum(counts["by_timescale"].values()) == 3
        assert counts["by_strategy"] == {
            "funding-flush-reversal": 1, "other-strategy": 1}
        assert counts["win_locked_by_strategy"] == {
            "funding-flush-reversal": 1}

    def test_unscorable_book_fails_safe_to_base_capacity(self, conn):
        capacity = queries.strategy_prediction_capacity(
            conn, "funding-flush-reversal")
        assert capacity["evidence_lane"] == "base"
        assert capacity["open_cap"] == write.MAX_OPEN_PER_STRATEGY
        assert capacity["open_slots_remaining"] == write.MAX_OPEN_PER_STRATEGY

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

    def test_double_resolution_is_noop(self, conn):
        pid = write.record_prediction(conn, _draft())
        assert write.resolve_prediction(
            conn, pid, "wrong", resolved_by="plutus-ops") is True
        # A second resolver (watcher vs ops sweep) loses the race — no raise,
        # no double counter-bump; the first outcome stands.
        assert write.resolve_prediction(
            conn, pid, "correct", resolved_by="plutus-ops") is False
        assert queries.prediction(conn, pid)["outcome"] == "wrong"

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
            agent="plutus-main", sl_price=102600.0)
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
                                text_md="x", agent="plutus-main")


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

    def test_validate_refuses_perception_only_data_point(self):
        # hl_orderbook is registered but has no numeric_path — resolution
        # could never extract a number, so the leaf must be refused at
        # write time, not discovered as expired_unresolvable weeks later.
        bad = {"data_point": "hl_orderbook", "op": "gte", "threshold": 5.0}
        probs = criteria.validate(
            bad,
            known_data_points={"hl_orderbook", "hl_price"},
            resolvable_data_points={"hl_price"},
        )
        assert any("perception-only" in p for p in probs)

    def test_validate_accepts_resolvable_data_point(self):
        probs = criteria.validate(
            _criteria(),
            known_data_points={"hl_price"},
            resolvable_data_points={"hl_price"},
        )
        assert probs == []

    def test_record_prediction_enforces_resolvable_gate(self, conn):
        # invalidation criteria are still gated to resolvable data points.
        bad = {"data_point": "hl_orderbook", "op": "gte", "threshold": 5.0}
        with pytest.raises(ValueError, match="perception-only"):
            write.record_prediction(
                conn, _draft(invalidation_criteria=bad),
                known_data_points={"hl_orderbook", "hl_price"},
                resolvable_data_points={"hl_price"},
            )


def _make_v2_db(path, *, open_pred=True, backed_pred=False):
    """Hand-build a minimal v2 lifecycle.db (no price-zone columns, no
    prediction_evaluations) with the tables the migration touches."""
    import sqlite3
    c = sqlite3.connect(str(path))
    c.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (2);
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, horizon_ts REAL, timescale TEXT, symbol TEXT,
            claim_md TEXT, success_criteria_json TEXT, conviction REAL,
            strategy_name TEXT, kind TEXT,
            resolved_at REAL, outcome TEXT, resolved_by TEXT,
            resolution_notes_md TEXT
        );
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, file_path TEXT,
            status TEXT, timescale TEXT, mechanism_family TEXT,
            data_points_json TEXT,
            created_at REAL, updated_at REAL,
            n_resolved INTEGER DEFAULT 0, n_correct INTEGER DEFAULT 0,
            n_wrong INTEGER DEFAULT 0, n_ambiguous INTEGER DEFAULT 0,
            last_resolved_at REAL
        );
        CREATE TABLE support_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER NOT NULL,
            data_point TEXT NOT NULL,
            score REAL NOT NULL, kind TEXT NOT NULL,
            reading_json TEXT, weight REAL, normalizer TEXT,
            reasoning_md TEXT, ts REAL NOT NULL,
            UNIQUE (prediction_id, data_point)
        );
        CREATE TABLE theses (id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_id INTEGER);
        CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, thesis_id INTEGER);
        CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER);
        CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                opening_trade_id INTEGER, status TEXT);
        """
    )
    c.execute(
        "INSERT INTO strategies (name, file_path, status, timescale, "
        "mechanism_family, data_points_json, created_at, updated_at, "
        "n_resolved, n_correct, n_wrong) "
        "VALUES ('s','s.md','test','intraday','flow', "
        "'[{\"name\":\"ta_vortex\",\"params\":{\"interval\":\"1h\",\"symbol\":\"BTC\"},\"weight\":0.5},"
        "{\"name\":\"hl_cvd\",\"params\":{\"interval\":\"1h\",\"symbol\":\"BTC\"},\"weight\":0.5}]',"
        "0,0,9,6,3)")
    if open_pred:
        c.execute(
            "INSERT INTO predictions (ts, horizon_ts, timescale, symbol, claim_md, "
            "success_criteria_json, conviction, strategy_name, kind) "
            "VALUES (0, 9e9, 'intraday','BTC','old','{}',0.7,'s','strategy')")
        opid = c.execute("SELECT id FROM predictions WHERE claim_md='old'").fetchone()[0]
        # messy historical key forms the v5 migration must canonicalize
        c.execute("INSERT INTO support_scores (prediction_id, data_point, score, "
                  "kind, ts) VALUES (?, 'ta_vortex', 0.8, 'numerical', 0)", (opid,))
        c.execute("INSERT INTO support_scores (prediction_id, data_point, score, "
                  "kind, ts) VALUES (?, 'hl_cvd(1h)', 0.6, 'numerical', 0)", (opid,))
        c.execute("INSERT INTO support_scores (prediction_id, data_point, score, "
                  "kind, ts) VALUES (?, 'ta_rsi', 0.5, 'numerical', 0)", (opid,))
    if backed_pred:
        c.execute(
            "INSERT INTO predictions (ts, horizon_ts, timescale, symbol, claim_md, "
            "success_criteria_json, conviction, strategy_name, kind) "
            "VALUES (0, 9e9, 'intraday','BTC','backed','{}',0.8,'s','strategy')")
        bpid = c.execute("SELECT id FROM predictions WHERE claim_md='backed'").fetchone()[0]
        c.execute("INSERT INTO theses (prediction_id) VALUES (?)", (bpid,))
        thid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO decisions (thesis_id) VALUES (?)", (thid,))
        did = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO trades (decision_id) VALUES (?)", (did,))
        trid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO positions (opening_trade_id, status) VALUES (?, 'open')", (trid,))
    c.commit()
    c.close()


class TestMigration:
    def test_v2_migrates_forward(self, tmp_path):
        p = tmp_path / "lifecycle.db"
        _make_v2_db(p, open_pred=True, backed_pred=True)
        c = get_db(p)
        try:
            # migrations chain v2 → v3 → v4
            assert c.execute(
                "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
            cols = {r[1] for r in c.execute("PRAGMA table_info(predictions)")}
            assert {"entry_ref_price", "near_edge_pct", "far_edge_pct",
                    "reached_near_at", "reached_far_at"} <= cols
            assert _has_table(c, "prediction_evaluations")
            # open (unbacked) prediction was clean-slate expired
            row = c.execute(
                "SELECT resolved_at, outcome FROM predictions WHERE claim_md='old'").fetchone()
            assert row["resolved_at"] is not None
            assert row["outcome"] == "expired_unresolvable"
            # the prediction backing the open position survives (still open)
            backed = c.execute(
                "SELECT resolved_at FROM predictions WHERE claim_md='backed'").fetchone()
            assert backed["resolved_at"] is None
            # strategy mirror counters zeroed
            s = c.execute(
                "SELECT n_resolved, n_correct, n_wrong FROM strategies WHERE name='s'").fetchone()
            assert (s["n_resolved"], s["n_correct"], s["n_wrong"]) == (0, 0, 0)
            # v5: messy support-score keys canonicalized against the mirror's
            # declared data points; unresolvable keys left untouched
            keys = {r["data_point"] for r in c.execute(
                "SELECT data_point FROM support_scores")}
            assert "ta_vortex(interval=1h,symbol=BTC)" in keys   # bare name resolved
            assert "hl_cvd(interval=1h,symbol=BTC)" in keys      # "(1h)" shorthand resolved
            assert "ta_rsi" in keys                              # undeclared → left as-is
        finally:
            c.close()

    def test_migration_idempotent_under_concurrent_open(self, tmp_path):
        import threading
        p = tmp_path / "lifecycle.db"
        _make_v2_db(p)
        errors = []

        def open_it():
            try:
                cc = get_db(p)
                cc.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=open_it) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        c = get_db(p)
        try:
            assert c.execute(
                "SELECT version FROM schema_version").fetchone()["version"] == SCHEMA_VERSION
        finally:
            c.close()


class TestPopulationQueries:
    def test_strategies_by_timescale(self, conn):
        conn.execute(
            "INSERT INTO strategies (name, file_path, status, timescale, "
            "mechanism_family, regime_applicability_json, created_at, updated_at, "
            "n_correct, n_wrong) VALUES "
            "('a','a.md','test','intraday','flow','{\"trending-up\": true}',0,0,6,3)")
        conn.execute(
            "INSERT INTO strategies (name, file_path, status, timescale, "
            "mechanism_family, created_at, updated_at) VALUES "
            "('b','b.md','active','swing','momentum',0,0)")
        conn.commit()
        rows = queries.strategies_by_timescale(conn, "intraday")
        assert [r["name"] for r in rows] == ["a"]
        assert rows[0]["regime_applicability"] == {"trending-up": True}
        assert rows[0]["win_rate"] == round(6 / 9, 3)
        assert rows[0]["evidence_lane"] == "base"
        assert rows[0]["open_predictions"] == 0
        assert rows[0]["open_cap"] == write.MAX_OPEN_PER_STRATEGY
        assert rows[0]["open_slots_remaining"] == write.MAX_OPEN_PER_STRATEGY
        # swing strategy not returned at the intraday timescale
        assert queries.strategies_by_timescale(conn, "swing")[0]["name"] == "b"

    def test_open_predictions_by_cell(self, conn):
        write.record_prediction(conn, _draft(regime_tag="trending-up/normal"))
        write.record_prediction(conn, _draft(strategy_name="other",
                                             regime_tag="ranging/compressed"))
        by = {(c["timescale"], c["regime_tag"]): c["n"]
              for c in queries.open_predictions_by_cell(conn)}
        assert by[("intraday", "trending-up/normal")] == 1
        assert by[("intraday", "ranging/compressed")] == 1

    def test_dispatcher_exposes_cell_query(self):
        import json as _json
        from trading.dispatchers.lifecycle_query import _run_query
        from trading.lifecycle.db import get_db
        conn = get_db()  # the conftest-isolated default db the dispatcher also opens
        write.record_prediction(conn, _draft(regime_tag="trending-up/normal"))
        out = _json.loads(_run_query({"query": "open_predictions_by_cell"}))
        assert out["query"] == "open_predictions_by_cell"
        assert out["result"][0]["n"] == 1


class TestMaeEnvelope:
    def test_envelope_from_resolved_correct_predictions(self, conn):
        for mae in [-1.0, -2.0, -3.0, -4.0, -5.0]:
            pid = write.record_prediction(conn, _draft())  # resolved each loop → no cap
            write.resolve_prediction(
                conn, pid, "correct", resolved_by="resolver",
                realized_value={"mae_pct": mae, "resolution_mode": "touch"})
        env = queries.mae_envelope(conn, percentile=0.8)
        assert env["n"] == 5
        assert env["suggested_sl_pct"] == 5.0   # p80 of magnitudes [1,2,3,4,5]
        assert env["p50_mae_pct"] == 3.0
        assert env["max_mae_pct"] == 5.0

    def test_wrong_outcomes_excluded(self, conn):
        pid = write.record_prediction(conn, _draft())
        write.resolve_prediction(conn, pid, "wrong", resolved_by="resolver",
                                 realized_value={"mae_pct": -9.0})
        assert queries.mae_envelope(conn)["n"] == 0  # only CORRECT setups inform the stop

    def test_empty_envelope_is_none_not_zero(self, conn):
        env = queries.mae_envelope(conn)
        assert env["n"] == 0 and env["suggested_sl_pct"] is None


class TestRescoreTrajectory:
    def test_due_for_rescore_schedule(self, conn):
        pid = write.record_prediction(conn, _draft())  # intraday → 30m cadence
        assert [d["id"] for d in queries.predictions_due_for_rescore(conn)] == [pid]
        # a trajectory point now → not due within the cadence...
        write.record_prediction_evaluation(
            conn, prediction_id=pid, conviction=0.6, agent="plutus-ops")
        assert queries.predictions_due_for_rescore(conn) == []
        # ...due again once the intraday cadence (1800s) elapses
        later = time.time() + 1801
        assert [d["id"] for d in queries.predictions_due_for_rescore(conn, now=later)] == [pid]

    def test_evaluation_round_trip(self, conn):
        pid = write.record_prediction(conn, _draft())
        eid = write.record_prediction_evaluation(
            conn, prediction_id=pid, conviction=0.55, support_scores_json="[]",
            regime_tag="ranging/normal", agent="plutus-ops")
        row = conn.execute(
            "SELECT prediction_id, conviction, regime_tag FROM "
            "prediction_evaluations WHERE id=?", (eid,)).fetchone()
        assert row["prediction_id"] == pid
        assert row["conviction"] == 0.55
        assert row["regime_tag"] == "ranging/normal"

    def test_strategyless_excluded(self, conn):
        # stress/adhoc predictions with no strategy have no conviction model
        write.record_prediction(conn, _draft(strategy_name=None, kind="stress"))
        assert queries.predictions_due_for_rescore(conn) == []


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


@pytest.fixture(autouse=True)
def _data_points_registered():
    """The binder reads params off the live data-point registry, which is
    populated by importing the integrations — as it is in any real process."""
    import trading.integrations.hyperliquid.data_points  # noqa: F401


class TestInvalidationSymbolBinding:
    """A prediction knows what it is about; its criteria must too.

    ``validate`` checked that a leaf's data point was registered and
    numerically resolvable, but never that the data point's own REQUIRED
    params were present. ``{"data_point": "hl_price", "op": "lte",
    "threshold": 88.9}`` — no symbol — passed registration and then failed at
    every fetch forever. Measured 2026-08-25: 20 of 99 predictions carrying
    machine invalidation were unreadable, three of them describing theses that
    had already broken.
    """

    def test_missing_required_param_is_named(self):
        from trading.lifecycle import criteria
        assert criteria.missing_required_params("hl_price", None) == ["symbol"]
        assert criteria.missing_required_params(
            "hl_price", {"symbol": "BTC"}) == []

    def test_bind_fills_from_the_prediction_symbol(self):
        from trading.lifecycle import criteria
        leaf = {"data_point": "hl_price", "op": "lte", "threshold": 88.9}
        bound = criteria.bind_symbol(leaf, "xyz:BRENTOIL")
        assert bound["params"] == {"symbol": "xyz:BRENTOIL"}
        assert leaf.get("params") is None          # input untouched

    def test_bind_never_overrides_an_explicit_symbol(self):
        # A leaf may deliberately watch a DIFFERENT instrument than the one
        # being predicted — an equity thesis invalidated by a dollar move.
        from trading.lifecycle import criteria
        leaf = {"data_point": "hl_price", "op": "lte", "threshold": 100.0,
                "params": {"symbol": "BTC"}}
        assert criteria.bind_symbol(leaf, "xyz:GOLD")["params"]["symbol"] == "BTC"

    def test_bind_walks_combinators(self):
        from trading.lifecycle import criteria
        tree = {"any": [{"data_point": "hl_price", "op": "lte", "threshold": 1.0},
                        {"all": [{"data_point": "hl_price", "op": "gte",
                                  "threshold": 2.0}]}]}
        bound = criteria.bind_symbol(tree, "ETH")
        assert bound["any"][0]["params"]["symbol"] == "ETH"
        assert bound["any"][1]["all"][0]["params"]["symbol"] == "ETH"

    def test_validate_refuses_an_unbindable_leaf(self):
        from trading.lifecycle import criteria
        problems = criteria.validate(
            {"data_point": "hl_candles", "op": "lte", "threshold": 1.0},
            known_data_points={"hl_candles"}, resolvable_data_points={"hl_candles"})
        # hl_candles requires interval as well — no prediction field supplies
        # it, so the leaf is refused rather than accepted and left dead.
        assert any("interval" in p for p in problems), problems

    def test_registration_binds_the_symbol_into_the_stored_row(self, tmp_path):
        from trading.lifecycle import write
        from trading.lifecycle.db import get_db
        conn = get_db()
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="brent coil", horizon_ts=time.time() + 3600,
            entry_ref_price=92.0, near_edge_pct=0.9, far_edge_pct=2.1,
            conviction=0.7, agent="plutus-predict", symbol="xyz:BRENTOIL",
            strategy_name="brentS", kind="strategy",
            invalidation_criteria={"data_point": "hl_price", "op": "lte",
                                   "threshold": 88.9}))
        stored = json.loads(conn.execute(
            "SELECT invalidation_criteria_json FROM predictions WHERE id=?",
            (pid,)).fetchone()[0])
        assert stored["params"]["symbol"] == "xyz:BRENTOIL"


class TestUnreadableInvalidationIsLoud:
    """'Unresolvable' and 'not met' are different answers.

    The resolver tested only ``== "correct"``, under a bare
    ``except Exception: pass``. An invalidation nothing could read was
    therefore indistinguishable from a thesis that was holding, and three
    broken theses rode toward horizon unseen.
    """

    def _row(self, inv, symbol="xyz:BRENTOIL"):
        from trading.lifecycle import write
        from trading.lifecycle.db import get_db
        conn = get_db()
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="c", horizon_ts=time.time() + 3600, entry_ref_price=92.0,
            near_edge_pct=0.9, far_edge_pct=2.1, conviction=0.7,
            agent="plutus-predict", symbol=symbol, strategy_name="brentS",
            kind="strategy"))
        conn.execute("UPDATE predictions SET invalidation_criteria_json=? "
                     "WHERE id=?", (json.dumps(inv), pid))
        conn.commit()
        return conn, pid

    def test_legacy_symbolless_row_resolves_after_binding(self):
        """The live case: BRENT at 87.88 against an lte-88.9 thesis-break.

        The row was written before the check existed, so it carries no symbol.
        Binding at resolution revives it — no hand-edited row.
        """
        from trading.lifecycle import resolver
        conn, pid = self._row({"data_point": "hl_price", "op": "lte",
                               "threshold": 88.9})
        seen = {}

        def fetch(dp, params):
            seen["params"] = params
            return 87.88 if (params or {}).get("symbol") == "xyz:BRENTOIL" else None

        res = resolver.resolve_open_predictions(
            conn, mids={"xyz:BRENTOIL": 87.88}, path_stats_fn=lambda *a, **k: {},
            fetch_fn=fetch, fetch_extreme_fn=None)
        assert seen["params"] == {"symbol": "xyz:BRENTOIL"}
        assert [r["prediction_id"] for r in res["resolved"]] == [pid]
        assert res["resolved"][0]["mode"] == "invalidated"

    def test_unreadable_invalidation_is_reported_not_swallowed(self):
        from trading.lifecycle import resolver
        conn, pid = self._row({"data_point": "hl_price", "op": "lte",
                               "threshold": 88.9})
        res = resolver.resolve_open_predictions(
            conn, mids={"xyz:BRENTOIL": 92.0}, path_stats_fn=lambda *a, **k: {},
            fetch_fn=lambda dp, params: None,      # the venue will not answer
            fetch_extreme_fn=None)
        assert [u["prediction_id"] for u in res["unresolvable_invalidations"]] == [pid]
        assert res["resolved"] == []               # still open, but SEEN

    def test_readable_and_unmet_is_not_reported(self):
        from trading.lifecycle import resolver
        conn, pid = self._row({"data_point": "hl_price", "op": "lte",
                               "threshold": 88.9})
        res = resolver.resolve_open_predictions(
            conn, mids={"xyz:BRENTOIL": 92.0}, path_stats_fn=lambda *a, **k: {},
            fetch_fn=lambda dp, params: 92.0, fetch_extreme_fn=None)
        assert res["unresolvable_invalidations"] == []
        assert res["resolved"] == []
