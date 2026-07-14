"""Deterministic status sync — the binary gate is code-owned (review item E).

tradeable test books promote to active; active books that stop clearing
demote to test; dormancy/retirement are never touched (judgment moves).
"""

import time

from trading.lifecycle import queries, write
from trading.lifecycle.db import get_db
from trading.lifecycle.graduation import sync_strategy_statuses
from trading.strategies import loader
from trading.strategies.files import Strategy, parse_strategy, strategies_dir

BODY = """
# Hypothesis
After a deeply negative funding flush while price holds support, BTC mean-reverts
upward within 24h.

# Mechanism
Over-levered shorts pay to hold; forced covering supplies the bid. The other side
is late momentum shorts chasing the flush.
"""


def _mk_strategy(conn, name, status="test"):
    s = Strategy(
        name=name, status=status, timescale="intraday", mechanism_family="flow",
        file_path=strategies_dir() / f"{name}.md",
        data_points=[{"name": "hl_funding", "params": {"symbol": "BTC"},
                      "weight": 0.4}],
        created="2026-07-09", body_md=BODY)
    loader.write_strategy(s, conn)


def _resolved(conn, strat, outcome, mae, reached):
    pid = write.record_prediction(conn, write.PredictionDraft(
        claim_md="z", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
        near_edge_pct=1.5, far_edge_pct=3.0, conviction=0.7,
        agent="plutus-predict", symbol="BTC", strategy_name=strat,
        kind="strategy"))
    write.resolve_prediction(conn, pid, outcome, resolved_by="r",
                             realized_value={"mae_pct": mae})
    if reached:
        conn.execute("UPDATE predictions SET reached_far_at=? WHERE id=?",
                     (time.time(), pid))
    conn.commit()


def _tradeable_book(conn, name):
    for _ in range(12):
        _resolved(conn, name, "correct", -0.3, True)
    for _ in range(4):
        _resolved(conn, name, "wrong", -2.0, False)


def _thin_book(conn, name):
    for _ in range(4):
        _resolved(conn, name, "correct", -0.3, True)


class TestSync:
    def test_tradeable_test_strategy_promotes(self):
        conn = get_db()
        _mk_strategy(conn, "grad", status="test")
        _tradeable_book(conn, "grad")
        assert queries.strategy_expectancy(conn, "grad")["tradeable"] is True

        changes = sync_strategy_statuses(conn)
        assert changes == [c for c in changes if c["strategy"] == "grad"]
        assert changes[0]["from"] == "test" and changes[0]["to"] == "active"
        # file is truth — the frontmatter moved too, not just the mirror
        assert parse_strategy(strategies_dir() / "grad.md").status == "active"
        row = conn.execute("SELECT status FROM strategies WHERE name='grad'").fetchone()
        assert row["status"] == "active"

    def test_active_strategy_under_bar_demotes(self):
        conn = get_db()
        _mk_strategy(conn, "fading", status="active")
        _thin_book(conn, "fading")           # n=4 — nowhere near the bar
        changes = sync_strategy_statuses(conn)
        assert [(c["from"], c["to"]) for c in changes
                if c["strategy"] == "fading"] == [("active", "test")]
        assert parse_strategy(strategies_dir() / "fading.md").status == "test"

    def test_in_sync_population_is_untouched(self):
        conn = get_db()
        _mk_strategy(conn, "grad", status="test")
        _tradeable_book(conn, "grad")
        sync_strategy_statuses(conn)
        assert sync_strategy_statuses(conn) == []   # idempotent

    def test_dormant_and_retired_never_touched(self):
        # Dormancy is regime judgment, retirement is a research call — the
        # sync must not wake or bury anything.
        conn = get_db()
        _mk_strategy(conn, "sleeper", status="dormant")
        _tradeable_book(conn, "sleeper")
        assert sync_strategy_statuses(conn) == []
        assert parse_strategy(strategies_dir() / "sleeper.md").status == "dormant"

    def test_resolver_path_promotes_on_resolution(self):
        """Both watcher and ops share resolve_open_predictions — a resolve
        batch must promote a tradeable-but-test book without a separate sync.

        Resolve an expired miss with mae matching the existing loss population
        so the hard-stop envelope (and tradeable) does not collapse: a
        winner-MAE of 0.3 with p75 stop of 0.3 would path-dependently count
        every prior win as a stop-out and wrongly demote the book.
        """
        from trading.lifecycle import resolver

        conn = get_db()
        name = "via-resolve"
        _mk_strategy(conn, name, status="test")
        _tradeable_book(conn, name)  # tradeable, but status still test until sync
        assert queries.strategy_expectancy(conn, name)["tradeable"] is True
        assert parse_strategy(strategies_dir() / f"{name}.md").status == "test"

        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="live", horizon_ts=time.time() + 3600,
            entry_ref_price=100_000.0, near_edge_pct=1.5, far_edge_pct=3.0,
            conviction=0.7, agent="plutus-predict", symbol="BTC",
            strategy_name=name, kind="strategy"))
        # Force expiry for the sweep (horizon must be after ts at insert time).
        conn.execute("UPDATE predictions SET horizon_ts=? WHERE id=?",
                     (time.time() - 10, pid))
        conn.commit()
        res = resolver.resolve_open_predictions(
            conn, mids={"BTC": 100_000.0},  # no favorable move
            path_stats_fn=lambda *a, **k: {
                "mfe_pct": 0.1, "mae_pct": -2.0, "range_pct": 2.1},
        )
        assert any(r["prediction_id"] == pid and r["outcome"] == "wrong"
                   for r in res["resolved"])
        assert queries.strategy_expectancy(conn, name)["tradeable"] is True
        assert parse_strategy(strategies_dir() / f"{name}.md").status == "active"
