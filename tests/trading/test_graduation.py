"""Deterministic status sync — the binary gate is code-owned (review item E).

tradeable test books promote to active; active books that stop clearing
demote to test; dormancy/retirement are never touched (judgment moves).
"""

import time

import pytest

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


@pytest.fixture
def conn():
    """The per-test runtime database (HERMES_HOME is isolated per test)."""
    return get_db()


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


class TestMultiplicityExcludesRetired:
    """Retired books stop counting toward M (2026-07-27).

    They counted until then — a trial cannot be un-tried, the purer statistic.
    The price was a bar that only ever rose: measured on the live desk, 81-94%
    of every hurdle was multiplicity premium rather than trading cost, and no
    strategy had ever graduated. A gate that rises forever eventually forbids
    everything.

    The exclusion makes retirement an edit to the desk's own bar, so these
    tests also pin the boundary that keeps it honest: DORMANT still counts.
    """

    def _siblings(self, conn, name):
        return queries.strategy_expectancy(conn, name)["siblings_tried"]

    def _setup(self, conn):
        _mk_strategy(conn, "subject")
        _tradeable_book(conn, "subject")
        _mk_strategy(conn, "sibling")
        _tradeable_book(conn, "sibling")

    def _set_status(self, conn, name, status):
        conn.execute("UPDATE strategies SET status=? WHERE name=?", (status, name))
        conn.commit()

    def test_retired_sibling_does_not_count(self, conn):
        self._setup(conn)
        assert self._siblings(conn, "subject") == 2
        self._set_status(conn, "sibling", "retired")
        assert self._siblings(conn, "subject") == 1

    def test_dormant_sibling_still_counts(self, conn):
        """A parked hypothesis is not a withdrawn one."""
        self._setup(conn)
        self._set_status(conn, "sibling", "dormant")
        assert self._siblings(conn, "subject") == 2

    def test_retiring_a_sibling_lowers_the_hurdle(self, conn):
        """The point of the change, asserted as a number."""
        self._setup(conn)
        before = queries.strategy_expectancy(conn, "subject")["hurdle_pct"]
        self._set_status(conn, "sibling", "retired")
        after = queries.strategy_expectancy(conn, "subject")["hurdle_pct"]
        assert after < before
        # M=1 pays no selection premium at all — the bar falls to pure cost.
        assert queries.strategy_expectancy(
            conn, "subject")["multiplicity_premium_pct"] == 0

    def test_a_thin_retired_book_was_never_counted_anyway(self, conn):
        """Below SERIOUS_TRIAL_MIN_N nothing changes — the filter is evidence."""
        _mk_strategy(conn, "subject")
        _tradeable_book(conn, "subject")
        _mk_strategy(conn, "noise")
        _thin_book(conn, "noise")
        assert self._siblings(conn, "subject") == 1
        self._set_status(conn, "noise", "retired")
        assert self._siblings(conn, "subject") == 1


class TestSamplingCounters:
    """Predict could not see that a book had fallen out of rotation.

    Selection was regime match, open slot and perception freshness only, so
    books went unsampled silently — two sat 17 and 23 days untouched while
    still `test`, neither proving nor disproving themselves. These counters
    are visibility; the tiebreak lives in the agent brief, not in code.
    """

    def _row(self, conn, name):
        rows = queries.strategies_by_timescale(conn, "intraday")
        return next(r for r in rows if r["name"] == name)

    def test_never_sampled_reads_none_not_zero(self, conn):
        """Honest absence — a book never tried is not a book tried today."""
        _mk_strategy(conn, "untouched")
        r = self._row(conn, "untouched")
        assert r["days_since_last_prediction"] is None
        assert r["last_prediction_ts"] is None
        assert r["resolutions"] == 0
        assert r["is_serious_trial"] is False

    def test_age_is_reported_in_days(self, conn):
        _mk_strategy(conn, "stale")
        _resolved(conn, "stale", "correct", -0.3, True)
        conn.execute(
            "UPDATE predictions SET ts = ? WHERE strategy_name = 'stale'",
            (time.time() - 17 * 86400,))
        conn.commit()
        assert self._row(conn, "stale")["days_since_last_prediction"] == 17.0

    def test_serious_trial_flag_marks_the_multiplicity_cost(self, conn):
        """Crossing the threshold permanently raises the bar for the timescale.

        Sampling an already-serious book is free; sampling a young one charges
        every sibling. Predict must be able to tell them apart.
        """
        _mk_strategy(conn, "young")
        _thin_book(conn, "young")            # 4 resolutions, under the bar
        _mk_strategy(conn, "seasoned")
        _tradeable_book(conn, "seasoned")    # 16 resolutions, already paying
        assert self._row(conn, "young")["is_serious_trial"] is False
        assert self._row(conn, "seasoned")["is_serious_trial"] is True

    def test_counters_ride_the_query_predict_already_calls(self, conn):
        """No new plumbing: the fields arrive already regime-filtered."""
        _mk_strategy(conn, "carried")
        for field in ("days_since_last_prediction", "resolutions",
                      "is_serious_trial", "regime_applicability",
                      "open_slots_remaining"):
            assert field in self._row(conn, "carried")
