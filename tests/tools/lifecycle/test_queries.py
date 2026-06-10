"""Tests for the Phase 4a lifecycle query tools.

Each test seeds a temp lifecycle.db with a small fixture set and verifies the
query tool returns the expected shape / values. find_similar_theses and
find_similar_reflections additionally embed via the live Voyage API and assert
the top-1 hit; they skip without VOYAGE_API_KEY.
"""

import json
import time
from pathlib import Path

import pytest

from trading.lifecycle.db import LifecycleDB, get_lifecycle_db, reset_lifecycle_db_singleton
from harness.tools.registry import registry as tool_registry

# Importing the modules triggers their registry.register at top level.
import trading.lifecycle.queries.find_similar_reflections      # noqa: F401
import trading.lifecycle.queries.find_similar_theses           # noqa: F401
import trading.lifecycle.queries.inspect_position              # noqa: F401
import trading.lifecycle.queries.query_calibration             # noqa: F401
import trading.lifecycle.queries.query_capital_movements       # noqa: F401
import trading.lifecycle.queries.query_conviction_outcomes     # noqa: F401
import trading.lifecycle.queries.query_conviction_trajectory   # noqa: F401
import trading.lifecycle.queries.query_equity_curve            # noqa: F401
import trading.lifecycle.queries.query_performance             # noqa: F401
import trading.lifecycle.queries.query_performance_attribution # noqa: F401
import trading.lifecycle.queries.query_skip_outcomes           # noqa: F401
import trading.lifecycle.queries.query_strategy_book           # noqa: F401
import trading.lifecycle.queries.query_trades                  # noqa: F401
import trading.lifecycle.queries.query_unreflected_closes      # noqa: F401


@pytest.fixture()
def db(tmp_path):
    reset_lifecycle_db_singleton()
    instance = get_lifecycle_db(db_path=tmp_path / "lifecycle.db")
    yield instance
    reset_lifecycle_db_singleton()


def _call(tool_name: str, args: dict) -> dict:
    entry = tool_registry.get_entry(tool_name)
    assert entry is not None, f"tool '{tool_name}' not registered"
    return json.loads(entry.handler(args))


# =========================================================================
# Test fixtures: seed a small but realistic dataset
# =========================================================================

def _seed_two_strategies_with_trades(db):
    """Two strategies, four closed trades (2 winners + 2 losers per strategy bucket)."""
    base_ts = time.time() - 60 * 86400  # 60 days ago

    def w(c):
        s1 = c.execute(
            "INSERT INTO strategies(name, status, created_at, hypothesis_md) VALUES (?, ?, ?, ?)",
            ("funding-mean-revert", "active", base_ts, "extreme funding mean-reverts"),
        ).lastrowid
        s2 = c.execute(
            "INSERT INTO strategies(name, status, created_at, hypothesis_md) VALUES (?, ?, ?, ?)",
            ("breakout", "active", base_ts, "ride confirmed breakouts"),
        ).lastrowid

        positions = []
        for i, (strat_id, conv, pnl, r, days_ago) in enumerate([
            (s1, 0.7, 12.0, 1.5, 5),
            (s1, 0.6, -4.0, -0.5, 4),
            (s1, 0.5, 8.0, 1.0, 35),    # outside 30d window
            (s2, 0.8, 20.0, 2.0, 3),
            (s2, 0.55, -10.0, -1.0, 2),
            (s2, 0.6, 15.0, 1.5, 50),   # outside 30d window
        ]):
            opened = base_ts + (60 - days_ago) * 86400
            closed = opened + 3600
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, strategy_id, "
                "invalidation_criteria_json) VALUES (?, ?, ?, ?, ?)",
                (opened, "BTC" if i % 2 == 0 else "ETH",
                 f"thesis #{i}", strat_id, '["x"]'),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, opened, "open_long", conv),
            ).lastrowid
            otrade = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, opened, "hyperliquid", "BTC" if i % 2 == 0 else "ETH",
                 "long", 0.01, 70_000.0 + i),
            ).lastrowid
            ctrade = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, closed, "hyperliquid", "BTC" if i % 2 == 0 else "ETH",
                 "close", 0.01, 70_000.0 + i + (pnl * 100)),
            ).lastrowid
            pos_id = c.execute(
                "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, "
                "closing_trade_id, status, opened_at, closed_at, perceived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?)",
                ("hyperliquid", "BTC" if i % 2 == 0 else "ETH",
                 "long", 0.01, otrade, ctrade, opened, closed, closed),
            ).lastrowid
            c.execute(
                "INSERT INTO outcomes(position_id, realized_pnl_usd, r_multiple, "
                "holding_minutes, conviction_at_entry, conviction_at_exit) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pos_id, pnl, r, 60.0, conv, conv * 0.9),
            )
            positions.append(pos_id)
        return s1, s2, positions

    return db._execute_write(w)


# =========================================================================
# query_trades
# =========================================================================

class TestQueryTrades:
    def test_returns_trades_with_thesis_join(self, db):
        _seed_two_strategies_with_trades(db)
        result = _call("query_trades", {"limit": 100})
        assert result["count"] >= 6
        for trade in result["trades"]:
            assert "thesis_id" in trade
            assert "conviction" in trade
            assert "thesis_snippet" in trade

    def test_filter_by_symbol(self, db):
        _seed_two_strategies_with_trades(db)
        result = _call("query_trades", {"symbol": "BTC", "limit": 100})
        assert all(t["symbol"] == "BTC" for t in result["trades"])

    def test_filter_by_min_conviction(self, db):
        _seed_two_strategies_with_trades(db)
        result = _call("query_trades", {"min_conviction": 0.7, "limit": 100})
        assert all(t["conviction"] >= 0.7 for t in result["trades"])


# =========================================================================
# query_performance
# =========================================================================

class TestQueryPerformance:
    def test_aggregates_overall(self, db):
        _seed_two_strategies_with_trades(db)
        result = _call("query_performance", {})
        assert result["count"] == 1
        row = result["rows"][0]
        assert row["n_trades"] == 6
        assert row["total_pnl_usd"] == pytest.approx(12 - 4 + 8 + 20 - 10 + 15)

    def test_group_by_symbol(self, db):
        _seed_two_strategies_with_trades(db)
        result = _call("query_performance", {"group_by": "symbol"})
        groups = {r["group_key"]: r for r in result["rows"]}
        assert "BTC" in groups
        assert "ETH" in groups


# =========================================================================
# query_performance_attribution
# =========================================================================

class TestQueryPerformanceAttribution:
    def test_group_by_strategy(self, db):
        s1, s2, _ = _seed_two_strategies_with_trades(db)
        result = _call("query_performance_attribution", {"group_by": "strategy_id"})
        rows = {r["strategy_id"]: r for r in result["rows"]}
        assert s1 in rows and s2 in rows
        # s2 has the highest single-trade PnL (20), so should appear at top
        assert max(rows.values(), key=lambda r: r["total_pnl_usd"])["strategy_id"] == s2

    def test_invalid_group_by(self, db):
        result = _call("query_performance_attribution", {"group_by": "bogus"})
        assert "error" in result


# =========================================================================
# query_calibration
# =========================================================================

class TestQueryCalibration:
    def test_buckets_by_conviction(self, db):
        _seed_two_strategies_with_trades(db)
        result = _call("query_calibration", {"bucket_width": 0.1})
        assert result["count"] >= 4
        assert "pearson_r" in result
        assert isinstance(result["buckets"], list)
        assert all("mean_r" in b for b in result["buckets"])


# =========================================================================
# query_strategy_book
# =========================================================================

class TestQueryStrategyBook:
    def test_returns_each_strategy_with_perf(self, db):
        s1, s2, _ = _seed_two_strategies_with_trades(db)
        result = _call("query_strategy_book", {})
        assert result["count"] == 2
        names = {s["name"] for s in result["strategies"]}
        assert names == {"funding-mean-revert", "breakout"}
        for strat in result["strategies"]:
            assert "lifetime" in strat and "last_30d" in strat
            assert "edge_decay" in strat
            assert isinstance(strat["edge_decay"], bool)


# =========================================================================
# query_equity_curve
# =========================================================================

class TestQueryEquityCurve:
    def test_returns_total_equity_snapshots(self, db):
        ts = time.time()

        def w(c):
            for i in range(5):
                c.execute(
                    "INSERT INTO data_point_snapshots(ts, name, value_json, source) "
                    "VALUES (?, ?, ?, ?)",
                    (ts + i * 60, "total_equity",
                     json.dumps({"usd": 25.0 + i}), "hyperliquid"),
                )

        db._execute_write(w)
        result = _call("query_equity_curve", {})
        assert result["count"] == 5
        assert [p["equity_usd"] for p in result["points"]] == [25.0, 26.0, 27.0, 28.0, 29.0]


# =========================================================================
# query_capital_movements
# =========================================================================

class TestQueryCapitalMovements:
    def test_filters_by_movement_type(self, db):
        ts = time.time()

        def w(c):
            for i, mtype in enumerate(["deposit", "venue_transfer", "withdrawal"]):
                c.execute(
                    "INSERT INTO capital_movements(ts, token, amount_token, "
                    "amount_usd_at_time, movement_type) VALUES (?, ?, ?, ?, ?)",
                    (ts + i, "USDC", 25.0 + i, 25.0 + i, mtype),
                )

        db._execute_write(w)
        result = _call("query_capital_movements", {"movement_type": "deposit"})
        assert result["count"] == 1
        assert result["movements"][0]["movement_type"] == "deposit"


# =========================================================================
# query_conviction_trajectory + query_conviction_outcomes
# =========================================================================

class TestConvictionTools:
    def _seed_position_with_trajectory(self, db, convictions):
        ts = time.time() - 86400

        def w(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, invalidation_criteria_json) "
                "VALUES (?, ?, ?, ?)",
                (ts, "BTC", "test", '["x"]'),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, ts, "open_long", convictions[0]),
            ).lastrowid
            otrade = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, ts, "hyperliquid", "BTC", "long", 0.01, 70_000.0),
            ).lastrowid
            ctrade = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, ts + 7200, "hyperliquid", "BTC", "close", 0.01, 71_000.0),
            ).lastrowid
            pos_id = c.execute(
                "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, "
                "closing_trade_id, status, opened_at, closed_at, perceived_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?)",
                ("hyperliquid", "BTC", "long", 0.01, otrade, ctrade,
                 ts, ts + 7200, ts + 7201),
            ).lastrowid
            for i, conv in enumerate(convictions):
                c.execute(
                    "INSERT INTO position_evaluations(ts, position_id, conviction, "
                    "thesis_status, recommended_action) VALUES (?, ?, ?, ?, ?)",
                    (ts + i * 600, pos_id, conv, "intact", "hold"),
                )
            c.execute(
                "INSERT INTO outcomes(position_id, realized_pnl_usd, r_multiple, "
                "holding_minutes, conviction_at_entry, conviction_at_exit) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pos_id, 10.0, 1.0, 120.0, convictions[0], convictions[-1]),
            )
            return pos_id

        return db._execute_write(w)

    def test_query_conviction_trajectory(self, db):
        pos = self._seed_position_with_trajectory(db, [0.7, 0.65, 0.6, 0.55])
        result = _call("query_conviction_trajectory", {"position_id": pos})
        assert result["count"] == 4
        assert result["position"]["status"] == "closed"
        assert [t["conviction"] for t in result["trajectory"]] == [0.7, 0.65, 0.6, 0.55]

    def test_query_conviction_outcomes_buckets(self, db):
        # rising trajectory
        self._seed_position_with_trajectory(db, [0.4, 0.5, 0.6, 0.7])
        # declining trajectory
        self._seed_position_with_trajectory(db, [0.7, 0.6, 0.5, 0.4])
        # steady
        self._seed_position_with_trajectory(db, [0.5, 0.51, 0.5, 0.49])

        result = _call("query_conviction_outcomes", {})
        shapes = {b["shape"] for b in result["buckets"]}
        assert "rising" in shapes
        assert "declining" in shapes
        assert "steady" in shapes


# =========================================================================
# query_skip_outcomes
# =========================================================================

class TestQuerySkipOutcomes:
    def test_lists_skips(self, db):
        ts = time.time()

        def w(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md) VALUES (?, ?, ?)",
                (ts, "BTC", "watch but skip"),
            ).lastrowid
            c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, ts, "skip", 0.3),
            )

        db._execute_write(w)
        result = _call("query_skip_outcomes", {})
        assert result["count"] == 1
        assert result["skips"][0]["action"] == "skip"


# =========================================================================
# inspect_position
# =========================================================================

class TestInspectPosition:
    def test_returns_full_chain(self, db):
        s1, _, positions = _seed_two_strategies_with_trades(db)
        pos_id = positions[0]
        result = _call("inspect_position", {"position_id": pos_id})
        assert result["position"]["id"] == pos_id
        assert len(result["trades"]) >= 1
        assert len(result["decisions"]) >= 1
        assert len(result["theses"]) >= 1
        assert result["outcome"] is not None
        assert any(s["id"] == s1 for s in result["strategies"])


# =========================================================================
# find_similar_theses + find_similar_reflections (live Voyage)
# =========================================================================

@pytest.fixture()
def voyage_key_required(monkeypatch):
    """Restore the operator's real VOYAGE_API_KEY (autouse conftest scrubs it)."""
    from dotenv import dotenv_values
    real = Path.home() / ".plutus-agent" / ".env"
    if not real.exists():
        pytest.skip(f"{real} not present")
    values = dotenv_values(real)
    key = (values.get("VOYAGE_API_KEY") or "").strip()
    if not key:
        pytest.skip("VOYAGE_API_KEY not set")
    monkeypatch.setenv("VOYAGE_API_KEY", key)
    from trading.perception.core.embedder import reset_embedder_singleton
    reset_embedder_singleton()
    yield
    reset_embedder_singleton()


def _embed_and_insert_theses(db, items):
    """Embed each (text, symbol) and insert thesis row + thesis_vec row atomically."""
    from trading.perception.core.embedder import get_embedder
    import sqlite_vec

    embedder = get_embedder()
    vectors = embedder.embed_documents([t for t, _ in items])

    def w(c):
        ids = []
        for (text, symbol), vec in zip(items, vectors):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, embedding, embedding_model, "
                "invalidation_criteria_json) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), symbol, text,
                 sqlite_vec.serialize_float32(vec), embedder.model_name,
                 '["x"]'),
            ).lastrowid
            c.execute(
                "INSERT INTO theses_vec(thesis_id, embedding) VALUES (?, ?)",
                (tid, sqlite_vec.serialize_float32(vec)),
            )
            ids.append(tid)
        return ids

    return db._execute_write(w)


class TestFindSimilarTheses:
    def test_top1_is_most_relevant(self, db, voyage_key_required):
        ids = _embed_and_insert_theses(db, [
            ("BTC funding rate flipped negative; coiled below 70k.", "BTC"),
            ("ETH breakout above 3500 with strong delta imbalance.", "ETH"),
            ("Tomato seedlings prefer indirect light.", None),
        ])
        result = _call("find_similar_theses", {
            "query": "Negative funding on Bitcoin perpetuals; longs accumulating.",
            "k": 3, "digest": False,
        })
        assert result["count"] >= 1
        # The BTC funding thesis (ids[0]) should rank top.
        assert result["hits"][0]["thesis_id"] == ids[0]

    def test_by_existing_thesis_id(self, db, voyage_key_required):
        ids = _embed_and_insert_theses(db, [
            ("BTC funding flipped negative below 70k.", "BTC"),
            ("BTC perp funding turning positive again.", "BTC"),
            ("Random unrelated text about gardening.", None),
        ])
        result = _call("find_similar_theses", {
            "thesis_id": ids[0], "k": 2, "digest": False,
        })
        # Self should be excluded, BTC perp funding (ids[1]) should rank above gardening.
        hit_ids = [h["thesis_id"] for h in result["hits"]]
        assert ids[0] not in hit_ids
        assert ids[1] in hit_ids


class TestFindSimilarReflections:
    def test_top1_is_most_relevant(self, db, voyage_key_required):
        from trading.perception.core.embedder import get_embedder
        import sqlite_vec
        items = [
            ("Lesson: avoid chasing breakouts after 2% moves — slippage ate the edge.", "loss_postmortem"),
            ("Lesson: funding-mean-reversion needs 3+ standard-deviation triggers.", "weekly_review"),
            ("Personal note: backed up the photos directory yesterday.", "ad_hoc"),
        ]
        vecs = get_embedder().embed_documents([t for t, _ in items])

        def w(c):
            for (text, kind), vec in zip(items, vecs):
                rid = c.execute(
                    "INSERT INTO reflections(ts, text_md, reflection_kind, embedding, embedding_model) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time.time(), text, kind,
                     sqlite_vec.serialize_float32(vec), get_embedder().model_name),
                ).lastrowid
                c.execute(
                    "INSERT INTO reflections_vec(reflection_id, embedding) VALUES (?, ?)",
                    (rid, sqlite_vec.serialize_float32(vec)),
                )

        db._execute_write(w)

        result = _call("find_similar_reflections", {
            "query": "When does mean reversion on funding actually work?",
            "k": 2, "digest": False,
        })
        assert result["count"] >= 1
        # Top hit should be the funding reflection.
        assert "funding" in result["hits"][0]["text_md"].lower()


class TestQueryUnreflectedCloses:
    """plutus-main Phase 0 calls this to discover positions that closed between
    beats and still need interpretive postmortem. The JSON1 ``json_each`` query
    must exact-match position ids — substring matching via LIKE would conflate
    position 2 with positions 12, 20, 22, etc."""

    def _seed_closed_positions(self, db, position_ids_with_closed_ts):
        ts_open = time.time() - 10000

        def w(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, invalidation_criteria_json) "
                "VALUES (?, ?, ?, ?)",
                (ts_open, "BTC", "test thesis", '["x"]'),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) VALUES (?, ?, ?, ?)",
                (tid, ts_open, "open_long", 0.5),
            ).lastrowid
            for pid_hint, closed_ts in position_ids_with_closed_ts:
                trade_id = c.execute(
                    "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (did, ts_open, "hl", "BTC", "long", 0.01, 70_000.0),
                ).lastrowid
                c.execute(
                    "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, "
                    "status, opened_at, closed_at) "
                    "VALUES (?, ?, ?, ?, ?, 'closed', ?, ?)",
                    ("hl", "BTC", "long", 0.01, trade_id, ts_open, closed_ts),
                )

        db._execute_write(w)

    def _seed_reflection(self, db, kind, position_ids):
        def w(c):
            c.execute(
                "INSERT INTO reflections(ts, text_md, position_ids_json, reflection_kind) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), "reflection text", json.dumps(position_ids), kind),
            )
        db._execute_write(w)

    def test_returns_closed_positions_without_reflection(self, db):
        now = time.time()
        # Seed 3 closed positions all after since_ts.
        self._seed_closed_positions(db, [(1, now - 100), (2, now - 80), (3, now - 60)])
        # Reflection covering position #1 only.
        self._seed_reflection(db, "loss_postmortem", [1])

        result = _call("query_unreflected_closes", {"since_ts": now - 200})
        assert result["count"] == 2
        returned_ids = {p["id"] for p in result["positions"]}
        # #1 was covered; #2 and #3 remain unreflected.
        # (Position ids are AUTOINCREMENT — first inserted gets id 1, etc.)
        assert returned_ids == {2, 3}

    def test_substring_match_does_not_falsely_cover_position(self, db):
        """Regression: position #2 must NOT be considered "covered" by a
        reflection whose position_ids_json contains 12 (which would happen
        with a naive LIKE '%' || id || '%' query). JSON1 json_each gives
        exact integer matching."""
        now = time.time()
        # Seed positions with auto-ids 1..15.
        self._seed_closed_positions(db, [(i, now - 100) for i in range(1, 16)])
        # Reflection covers ONLY position #12. Naive LIKE on '%' || 2 || '%'
        # would match "12", incorrectly excluding position #2.
        self._seed_reflection(db, "post_trade", [12])

        result = _call("query_unreflected_closes", {"since_ts": now - 200})
        returned_ids = {p["id"] for p in result["positions"]}
        # Position #12 is covered, all others remain.
        assert 12 not in returned_ids
        assert 2 in returned_ids, "position #2 must not be falsely covered by reflection on #12"
        assert returned_ids == set(range(1, 16)) - {12}

    def test_only_counts_postmortem_kinds(self, db):
        """ad_hoc / strategy_review / calibration_review reflections do not
        count as covering an unreflected close — only loss_postmortem,
        post_trade, weekly_review do."""
        now = time.time()
        self._seed_closed_positions(db, [(1, now - 100), (2, now - 80)])
        # ad_hoc reflection mentioning both — should NOT cover them.
        self._seed_reflection(db, "ad_hoc", [1, 2])

        result = _call("query_unreflected_closes", {"since_ts": now - 200})
        returned_ids = {p["id"] for p in result["positions"]}
        assert returned_ids == {1, 2}, "ad_hoc reflections should not cover closed positions"

    def test_since_ts_filters_old_closes(self, db):
        now = time.time()
        # Position closed BEFORE since_ts.
        self._seed_closed_positions(db, [(1, now - 1000)])
        # Position closed AFTER since_ts.
        self._seed_closed_positions(db, [(2, now - 100)])

        result = _call("query_unreflected_closes", {"since_ts": now - 200})
        returned_ids = {p["id"] for p in result["positions"]}
        # Both positions are unreflected, but only the recent one passes the filter.
        assert 2 in returned_ids
        assert 1 not in returned_ids

    def test_excludes_open_positions(self, db):
        """Only status='closed' positions are candidates."""
        now = time.time()

        def w(c):
            tid = c.execute(
                "INSERT INTO theses(ts, symbol, text_md, invalidation_criteria_json) "
                "VALUES (?, ?, ?, ?)",
                (now - 1000, "BTC", "t", '["x"]'),
            ).lastrowid
            did = c.execute(
                "INSERT INTO decisions(thesis_id, ts, action, conviction) "
                "VALUES (?, ?, ?, ?)",
                (tid, now - 1000, "open_long", 0.5),
            ).lastrowid
            trade_id = c.execute(
                "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, now - 1000, "hl", "BTC", "long", 0.01, 70000.0),
            ).lastrowid
            # Open position — should NOT appear in results.
            c.execute(
                "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, "
                "status, opened_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                ("hl", "BTC", "long", 0.01, trade_id, now - 1000),
            )

        db._execute_write(w)
        result = _call("query_unreflected_closes", {"since_ts": now - 2000})
        assert result["count"] == 0
