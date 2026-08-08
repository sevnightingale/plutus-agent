"""The symbol dimension — Phase 2 of the multi-asset round.

Strategies carry one symbol; cells and the occupancy cap scope by it; the
multiplicity M counts serious trials within CORRELATION BUCKETS so crypto
majors cannot masquerade as independent evidence; regime eligibility is
judged against each strategy's own symbol's regime.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from trading.lifecycle import db, write, queries


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db._create_fresh(c)
    return c


def _mk(conn, name, symbol="BTC", timescale="swing", status="test",
        direction="ranging", vol="compressed"):
    conn.execute(
        """INSERT INTO strategies
             (name, file_path, status, symbol, timescale, mechanism_family,
              regime_applicability_json, data_points_json,
              created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (name, f"/tmp/{name}.md", status, symbol, timescale, "momentum",
         json.dumps({timescale: {"direction": [direction],
                                 "volatility": [vol]}}),
         json.dumps([]), time.time(), time.time()))
    conn.commit()


class TestBuckets:
    def test_crypto_majors_share_a_bucket(self):
        assert "ETH" in queries.bucket_of("BTC")
        assert "BTC" in queries.bucket_of("SOL")

    def test_gold_is_not_crypto_s_sibling(self):
        assert "BTC" not in queries.bucket_of("xyz:GOLD")
        assert "xyz:SILVER" in queries.bucket_of("xyz:GOLD")

    def test_unknown_symbol_competes_only_with_itself(self):
        assert queries.bucket_of("xyz:OBSCURE") == {"xyz:OBSCURE"}


class TestSymbolScopedCells:
    def test_cell_key_carries_symbol(self, conn):
        _mk(conn, "a", symbol="BTC")
        _mk(conn, "g", symbol="xyz:GOLD")
        cells = {c["cell"] for c in queries.cell_capacity(conn)}
        assert "BTC/swing/ranging/compressed" in cells
        assert "xyz:GOLD/swing/ranging/compressed" in cells

    def test_same_cell_different_symbols_do_not_share_occupancy(self, conn):
        for i in range(queries.CELL_OCCUPANCY_CAP):
            _mk(conn, f"b{i}", symbol="BTC")
        _mk(conn, "gold-book", symbol="xyz:GOLD")
        caps = {c["cell"]: c for c in queries.cell_capacity(conn)}
        assert caps["BTC/swing/ranging/compressed"]["slots_remaining"] == 0
        assert caps["xyz:GOLD/swing/ranging/compressed"][
            "slots_remaining"] == queries.CELL_OCCUPANCY_CAP - 1

    def test_lit_is_judged_per_symbol(self, conn):
        write.record_regime(conn, symbol="xyz:GOLD", timescale="swing",
                            direction="ranging", volatility="compressed")
        _mk(conn, "b", symbol="BTC")
        _mk(conn, "g", symbol="xyz:GOLD")
        caps = {c["cell"]: c for c in queries.cell_capacity(conn)}
        assert caps["xyz:GOLD/swing/ranging/compressed"]["lit"] is True
        # BTC's regime was never assessed — its cell is not lit by gold's tape.
        assert caps["BTC/swing/ranging/compressed"]["lit"] is False

    def test_regime_eligibility_uses_own_symbol(self, conn):
        write.record_regime(conn, symbol="xyz:GOLD", timescale="swing",
                            direction="ranging", volatility="compressed")
        _mk(conn, "g", symbol="xyz:GOLD")
        _mk(conn, "b", symbol="BTC")
        rows = {r["name"]: r
                for r in queries.strategies_by_timescale(conn, "swing")}
        assert rows["g"]["regime_eligible"] is True
        assert rows["b"]["regime_eligible"] is None   # BTC never assessed


class TestBucketScopedM:
    def _serious(self, conn, strategy, n=6):
        for i in range(n):
            conn.execute(
                """INSERT INTO predictions
                     (ts, strategy_name, symbol, timescale, claim_md,
                      horizon_ts, near_edge_pct, far_edge_pct,
                      success_criteria_json, conviction,
                      resolved_at, realized_value_json, outcome)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (time.time(), strategy, "BTC", "swing", "c",
                 time.time() + 86400, 1.0, 2.0, json.dumps({}), 0.6,
                 time.time(), json.dumps({"v": 1}), "correct"))
        conn.commit()

    def test_gold_sibling_does_not_count_toward_crypto_m(self, conn):
        _mk(conn, "btc-a", symbol="BTC")
        _mk(conn, "eth-a", symbol="ETH")
        _mk(conn, "gold-a", symbol="xyz:GOLD")
        for s in ("btc-a", "eth-a", "gold-a"):
            self._serious(conn, s)
        e = queries.strategy_expectancy(conn, "btc-a")
        # ETH shares crypto's bucket; gold does not. M = btc-a + eth-a.
        assert e["siblings_tried"] == 2
        g = queries.strategy_expectancy(conn, "gold-a")
        # Gold competes only within its own bucket — one serious trial.
        assert g["siblings_tried"] == 1


class TestMigration:
    def test_v7_idempotent_and_backfills(self, tmp_path):
        import sqlite3 as s3
        p = tmp_path / "lifecycle.db"
        c = s3.connect(p)
        c.row_factory = s3.Row
        db._create_fresh(c)
        # Simulate a v6 db: drop the column via table rebuild is heavy;
        # instead run the migration on a db that already HAS the column —
        # idempotence is the claim that matters for the live upgrade.
        c.execute(
            """INSERT INTO strategies
                 (name, file_path, status, timescale, mechanism_family,
                  regime_applicability_json, data_points_json,
                  created_at, updated_at)
               VALUES ('sol-book','/tmp/x.md','test','swing','momentum',
                       '{}', ?, 1, 1)""",
            (json.dumps([{"name": "hl_price",
                          "params": {"symbol": "SOL"}}]),))
        c.commit()
        db._migrate_v6_to_v7(c)
        row = c.execute(
            "SELECT symbol FROM strategies WHERE name='sol-book'").fetchone()
        assert row["symbol"] == "SOL"
        db._migrate_v6_to_v7(c)   # second run: no error, no change
        assert c.execute(
            "SELECT symbol FROM strategies WHERE name='sol-book'"
        ).fetchone()["symbol"] == "SOL"
