"""Tests for agent/lifecycle_db.py — LifecycleDB schema, FTS5, vec0, FKs."""

import sqlite3
import time

import pytest
import sqlite_vec

from agent.lifecycle_db import (
    SCHEMA_VERSION,
    LifecycleDB,
    get_lifecycle_db,
    reset_lifecycle_db_singleton,
)


@pytest.fixture()
def db(tmp_path):
    """Fresh LifecycleDB at a temp path."""
    db_path = tmp_path / "lifecycle.db"
    instance = LifecycleDB(db_path=db_path)
    yield instance
    instance.close()


def _zero_vec(dim: int = 1024, hot_index: int = 0) -> bytes:
    """Build a 1024-float32 vector with one hot index set, serialized for vec0."""
    v = [0.0] * dim
    v[hot_index] = 1.0
    return sqlite_vec.serialize_float32(v)


# =========================================================================
# Schema bootstrap
# =========================================================================

class TestSchemaBootstrap:
    def test_creates_db_file(self, db, tmp_path):
        assert (tmp_path / "lifecycle.db").exists()

    def test_schema_version_row_set(self, db):
        row = db.conn().execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["version"] == SCHEMA_VERSION

    def test_all_base_tables_present(self, db):
        names = {
            r["name"] for r in db.conn().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "schema_version",
            "data_point_snapshots",
            "strategies",
            "theses",
            "decisions",
            "trades",
            "positions",
            "position_evaluations",
            "outcomes",
            "reflections",
            "capital_movements",
        }
        assert expected.issubset(names), f"Missing tables: {expected - names}"

    def test_fts5_virtual_tables_present(self, db):
        names = {
            r["name"] for r in db.conn().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # FTS5 virtual tables show up as 'table' type in sqlite_master
        assert "theses_fts" in names
        assert "reflections_fts" in names

    def test_vec0_virtual_tables_present(self, db):
        names = {
            r["name"] for r in db.conn().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "theses_vec" in names
        assert "reflections_vec" in names

    def test_init_is_idempotent(self, tmp_path):
        path = tmp_path / "lifecycle.db"
        first = LifecycleDB(db_path=path)
        first.close()
        # Re-opening should not duplicate schema_version rows
        second = LifecycleDB(db_path=path)
        rows = second.conn().execute(
            "SELECT COUNT(*) AS c FROM schema_version"
        ).fetchone()
        assert rows["c"] == 1
        second.close()


# =========================================================================
# Insert + select round-trips for every table (sanity over the schema shape)
# =========================================================================

class TestTableWrites:
    def test_data_point_snapshots(self, db):
        def w(c):
            return c.execute(
                "INSERT INTO data_point_snapshots(session_id, ts, name, params_json, value_json, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("s1", time.time(), "hl_funding_rate", '{"symbol":"BTC"}', '{"rate":0.0001}', "hyperliquid"),
            ).lastrowid

        snap_id = db._execute_write(w)
        row = db.conn().execute(
            "SELECT name, value_json FROM data_point_snapshots WHERE id = ?",
            (snap_id,),
        ).fetchone()
        assert row["name"] == "hl_funding_rate"
        assert row["value_json"] == '{"rate":0.0001}'

    def test_strategies(self, db):
        def w(c):
            return c.execute(
                "INSERT INTO strategies(name, description_md, hypothesis_md, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("funding-mean-revert", "MR on extreme funding", "extreme funding mean-reverts in 8h", "active", time.time()),
            ).lastrowid

        sid = db._execute_write(w)
        row = db.conn().execute(
            "SELECT name, status FROM strategies WHERE id = ?", (sid,)
        ).fetchone()
        assert row["name"] == "funding-mean-revert"
        assert row["status"] == "active"

    def test_theses_with_strategy_fk(self, db):
        def write_strategy(c):
            return c.execute(
                "INSERT INTO strategies(name, status, created_at) VALUES (?, ?, ?)",
                ("s1", "active", time.time()),
            ).lastrowid

        strat_id = db._execute_write(write_strategy)

        def write_thesis(c):
            return c.execute(
                "INSERT INTO theses(session_id, ts, symbol, text_md, strategy_id, "
                "invalidation_criteria_json) VALUES (?, ?, ?, ?, ?, ?)",
                ("sess1", time.time(), "BTC", "BTC funding flipped negative", strat_id,
                 '["funding turns positive again", "BTC closes below 60k"]'),
            ).lastrowid

        tid = db._execute_write(write_thesis)
        row = db.conn().execute(
            "SELECT symbol, text_md, strategy_id FROM theses WHERE id = ?", (tid,)
        ).fetchone()
        assert row["symbol"] == "BTC"
        assert row["strategy_id"] == strat_id

    def test_full_chain_thesis_decision_trade_position_outcome(self, db):
        """End-to-end FK chain: thesis → decision → trade → position → outcome."""
        ts = time.time()

        def write_chain(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, invalidation_criteria_json) "
                "VALUES (?, ?, ?, ?)",
                (ts, "ETH", "ETH breakout above 3500", '["close back below 3300"]'),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, ts, "open_long", 0.7),
            ).lastrowid
            trade_id = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, ts, "hyperliquid", "ETH", "long", 0.1, 3510.0),
            ).lastrowid
            pos_id = c.execute(
                "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("hyperliquid", "ETH", "long", 0.1, trade_id, "open", ts),
            ).lastrowid
            return tid, did, trade_id, pos_id

        tid, did, trade_id, pos_id = db._execute_write(write_chain)

        # Close the position and write an outcome
        def close_chain(c):
            close_trade_id = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, ts + 3600, "hyperliquid", "ETH", "close", 0.1, 3600.0),
            ).lastrowid
            c.execute(
                "UPDATE positions SET closing_trade_id = ?, status = 'closed', closed_at = ?, perceived_at = ? "
                "WHERE id = ?",
                (close_trade_id, ts + 3600, ts + 3601, pos_id),
            )
            c.execute(
                "INSERT INTO outcomes(position_id, realized_pnl_usd, r_multiple, holding_minutes, "
                "conviction_at_entry, conviction_at_exit) VALUES (?, ?, ?, ?, ?, ?)",
                (pos_id, 9.0, 1.5, 60.0, 0.7, 0.65),
            )

        db._execute_write(close_chain)

        row = db.conn().execute(
            "SELECT o.realized_pnl_usd, o.conviction_at_entry, p.symbol, p.status "
            "FROM outcomes o JOIN positions p ON p.id = o.position_id "
            "WHERE o.position_id = ?",
            (pos_id,),
        ).fetchone()
        assert row["realized_pnl_usd"] == 9.0
        assert row["conviction_at_entry"] == 0.7
        assert row["symbol"] == "ETH"
        assert row["status"] == "closed"

    def test_position_evaluation(self, db):
        ts = time.time()

        def setup(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md) VALUES (?, ?, ?)",
                (ts, "BTC", "thesis"),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, ts, "open_long", 0.6),
            ).lastrowid
            trade = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, ts, "hyperliquid", "BTC", "long", 0.01, 70000.0),
            ).lastrowid
            pos = c.execute(
                "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("hyperliquid", "BTC", "long", 0.01, trade, "open", ts),
            ).lastrowid
            return tid, pos

        tid, pos_id = db._execute_write(setup)

        def add_eval(c):
            return c.execute(
                "INSERT INTO position_evaluations(ts, position_id, conviction, thesis_status, "
                "active_thesis_id, recommended_action) VALUES (?, ?, ?, ?, ?, ?)",
                (ts + 60, pos_id, 0.55, "weakening", tid, "hold"),
            ).lastrowid

        eid = db._execute_write(add_eval)
        row = db.conn().execute(
            "SELECT conviction, thesis_status, recommended_action "
            "FROM position_evaluations WHERE id = ?",
            (eid,),
        ).fetchone()
        assert row["conviction"] == 0.55
        assert row["thesis_status"] == "weakening"
        assert row["recommended_action"] == "hold"

    def test_capital_movement(self, db):
        def w(c):
            return c.execute(
                "INSERT INTO capital_movements(ts, from_account, to_account, token, "
                "amount_token, amount_usd_at_time, movement_type, tx_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), "acp_wallet_main", "hl_trading", "USDC", 25.0, 25.0,
                 "venue_transfer", "0xabc123"),
            ).lastrowid

        cmid = db._execute_write(w)
        row = db.conn().execute(
            "SELECT token, amount_token, movement_type FROM capital_movements WHERE id = ?",
            (cmid,),
        ).fetchone()
        assert row["token"] == "USDC"
        assert row["amount_token"] == 25.0
        assert row["movement_type"] == "venue_transfer"


# =========================================================================
# FTS5
# =========================================================================

class TestFTS5:
    def test_theses_fts_round_trip(self, db):
        ts = time.time()

        def w(c):
            return c.execute(
                "INSERT INTO theses(ts, symbol, text_md) VALUES (?, ?, ?)",
                (ts, "BTC", "Funding rate flipped negative; coiled below 70k resistance"),
            ).lastrowid

        tid = db._execute_write(w)
        rows = db.conn().execute(
            "SELECT t.id FROM theses t JOIN theses_fts f ON t.id = f.rowid "
            "WHERE theses_fts MATCH ?",
            ("funding negative",),
        ).fetchall()
        assert any(r["id"] == tid for r in rows)

    def test_reflections_fts_round_trip(self, db):
        ts = time.time()

        def w(c):
            return c.execute(
                "INSERT INTO reflections(ts, text_md, reflection_kind) VALUES (?, ?, ?)",
                (ts, "Lesson: avoid chasing breakouts after 2% moves", "loss_postmortem"),
            ).lastrowid

        rid = db._execute_write(w)
        rows = db.conn().execute(
            "SELECT r.id FROM reflections r JOIN reflections_fts f ON r.id = f.rowid "
            "WHERE reflections_fts MATCH ?",
            ("breakouts",),
        ).fetchall()
        assert any(r["id"] == rid for r in rows)

    def test_theses_fts_update_trigger(self, db):
        """FTS5 reflects UPDATE of text_md."""
        ts = time.time()

        def w(c):
            return c.execute(
                "INSERT INTO theses(ts, text_md) VALUES (?, ?)",
                (ts, "old text bullish"),
            ).lastrowid

        tid = db._execute_write(w)

        def upd(c):
            c.execute("UPDATE theses SET text_md = ? WHERE id = ?",
                      ("new text bearish", tid))

        db._execute_write(upd)
        rows = db.conn().execute(
            "SELECT rowid FROM theses_fts WHERE theses_fts MATCH ?",
            ("bearish",),
        ).fetchall()
        assert any(r["rowid"] == tid for r in rows)

        rows_old = db.conn().execute(
            "SELECT rowid FROM theses_fts WHERE theses_fts MATCH ?",
            ("bullish",),
        ).fetchall()
        assert not any(r["rowid"] == tid for r in rows_old)


# =========================================================================
# sqlite-vec virtual tables
# =========================================================================

class TestVec:
    def test_theses_vec_knn(self, db):
        ts = time.time()

        def w(c):
            for i in range(3):
                tid = c.execute(
                    "INSERT INTO theses(ts, text_md) VALUES (?, ?)",
                    (ts + i, f"thesis {i}"),
                ).lastrowid
                # vector hot at index i — distance ordering will be: query=0 → 0,1,2
                c.execute(
                    "INSERT INTO theses_vec(thesis_id, embedding) VALUES (?, ?)",
                    (tid, _zero_vec(hot_index=i)),
                )

        db._execute_write(w)

        rows = db.conn().execute(
            "SELECT thesis_id, distance FROM theses_vec "
            "WHERE embedding MATCH ? AND k = 2 ORDER BY distance",
            (_zero_vec(hot_index=0),),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["thesis_id"] == 1
        assert rows[0]["distance"] == pytest.approx(0.0, abs=1e-6)

    def test_reflections_vec_knn(self, db):
        ts = time.time()

        def w(c):
            rid = c.execute(
                "INSERT INTO reflections(ts, text_md, reflection_kind) VALUES (?, ?, ?)",
                (ts, "reflection text", "ad_hoc"),
            ).lastrowid
            c.execute(
                "INSERT INTO reflections_vec(reflection_id, embedding) VALUES (?, ?)",
                (rid, _zero_vec(hot_index=5)),
            )

        db._execute_write(w)
        rows = db.conn().execute(
            "SELECT reflection_id, distance FROM reflections_vec "
            "WHERE embedding MATCH ? AND k = 1 ORDER BY distance",
            (_zero_vec(hot_index=5),),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["distance"] == pytest.approx(0.0, abs=1e-6)


# =========================================================================
# Foreign-key enforcement
# =========================================================================

class TestForeignKeys:
    def test_decision_requires_existing_thesis(self, db):
        def bad(c):
            c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (99999, time.time(), "open_long", 0.5),
            )

        with pytest.raises(sqlite3.IntegrityError):
            db._execute_write(bad)

    def test_trade_requires_existing_decision(self, db):
        def bad(c):
            c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (88888, time.time(), "hyperliquid", "BTC", "long", 0.01, 70000.0),
            )

        with pytest.raises(sqlite3.IntegrityError):
            db._execute_write(bad)

    def test_outcome_requires_existing_position(self, db):
        def bad(c):
            c.execute(
                "INSERT INTO outcomes(position_id, realized_pnl_usd) VALUES (?, ?)",
                (77777, 5.0),
            )

        with pytest.raises(sqlite3.IntegrityError):
            db._execute_write(bad)


# =========================================================================
# Singleton helper
# =========================================================================

class TestSingleton:
    def test_singleton_returns_same_instance(self, tmp_path):
        reset_lifecycle_db_singleton()
        try:
            path = tmp_path / "lifecycle.db"
            a = get_lifecycle_db(db_path=path)
            b = get_lifecycle_db()
            assert a is b
        finally:
            reset_lifecycle_db_singleton()

    def test_reset_drops_singleton(self, tmp_path):
        reset_lifecycle_db_singleton()
        try:
            path = tmp_path / "lifecycle.db"
            a = get_lifecycle_db(db_path=path)
            reset_lifecycle_db_singleton()
            b = get_lifecycle_db(db_path=path)
            assert a is not b
        finally:
            reset_lifecycle_db_singleton()


# =========================================================================
# close() and reopen
# =========================================================================

class TestCloseReopen:
    def test_close_then_reopen_preserves_data(self, tmp_path):
        path = tmp_path / "lifecycle.db"
        first = LifecycleDB(db_path=path)

        def w(c):
            return c.execute(
                "INSERT INTO strategies(name, status, created_at) VALUES (?, ?, ?)",
                ("scalping-v1", "active", time.time()),
            ).lastrowid

        sid = first._execute_write(w)
        first.close()

        second = LifecycleDB(db_path=path)
        try:
            row = second.conn().execute(
                "SELECT name FROM strategies WHERE id = ?", (sid,)
            ).fetchone()
            assert row["name"] == "scalping-v1"
        finally:
            second.close()
