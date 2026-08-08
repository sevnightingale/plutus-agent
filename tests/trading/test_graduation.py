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


class TestCellExpectancy:
    """A blended book averages trades that share no stop, target or horizon.

    ema20-pivot-swing measured -0.004 lifetime and therefore met the
    retirement bar, while four of its five regime cells were positive and one
    (-0.429) sank the average. Retiring on the blend would have buried a
    working mechanism AND lowered the graduation hurdle for every sibling at
    its timescale on a false premise.
    """

    def _mixed_book(self, conn, name, good_tag, bad_tag):
        _mk_strategy(conn, name)
        for _ in range(12):
            _resolved(conn, name, "correct", -0.3, True)
        conn.execute("UPDATE predictions SET regime_tag=? WHERE strategy_name=?",
                     (good_tag, name))
        for _ in range(12):
            _resolved(conn, name, "wrong", -2.0, False)
        conn.execute(
            "UPDATE predictions SET regime_tag=? WHERE strategy_name=? "
            "AND regime_tag IS NULL", (bad_tag, name))
        conn.commit()

    def test_one_bad_cell_does_not_make_a_strategy_dead(self, conn):
        self._mixed_book(conn, "mixed", "swing/ranging/normal",
                         "swing/trending-up/compressed")
        r = queries.strategy_cell_expectancy(conn, "mixed")
        assert r["dead"] is False
        assert r["best_cell"]["regime_tag"] == "swing/ranging/normal"
        assert r["best_cell"]["expectancy_pct"] > 0
        # and the blend hides it — the whole point
        assert r["blended_expectancy_pct"] < r["best_cell"]["expectancy_pct"]

    def test_dead_only_when_no_cell_clears(self, conn):
        _mk_strategy(conn, "allbad")
        for _ in range(16):
            _resolved(conn, "allbad", "wrong", -2.0, False)
        conn.execute("UPDATE predictions SET regime_tag='swing/ranging/normal' "
                     "WHERE strategy_name='allbad'")
        conn.commit()
        assert queries.strategy_cell_expectancy(conn, "allbad")["dead"] is True

    def test_thin_cells_are_reported_but_never_judged(self, conn):
        """Below CELL_MIN_N a cell is noise; it must not decide anything."""
        _mk_strategy(conn, "thin")
        for _ in range(3):
            _resolved(conn, "thin", "correct", -0.3, True)
        conn.execute("UPDATE predictions SET regime_tag='swing/ranging/normal' "
                     "WHERE strategy_name='thin'")
        conn.commit()
        r = queries.strategy_cell_expectancy(conn, "thin")
        assert r["cells"] and r["cells"][0]["judged"] is False
        assert r["cells_judged"] == 0
        # nothing judgeable is NOT the same as alive, and must not read as dead
        assert r["dead"] is None

    def test_regime_tag_filter_narrows_the_book(self, conn):
        self._mixed_book(conn, "filt", "swing/ranging/normal",
                         "swing/trending-up/compressed")
        whole = queries.strategy_expectancy(conn, "filt")["n"]
        part = queries.strategy_expectancy(
            conn, "filt", regime_tag="swing/ranging/normal")["n"]
        assert part < whole and part == 12


class TestCellScopedMultiplicity:
    """M counts siblings in the strategy's own CELL, not its whole timescale.

    The premium prices a best-of-M selection, and the selection that actually
    happens is among books declaring the cell the tape is in — a strategy in
    another cell cannot be chosen instead. Cell scope was rejected on
    2026-07-07 because set-valued declarations would have let a strategy narrow
    its way to a lower bar; the writer's single-cell refusal and the cell cap
    removed that, and the cap now bounds M by construction.
    """

    def _mk_cell(self, conn, name, direction, volatility, status="test"):
        from trading.strategies.files import Strategy, strategies_dir
        loader.write_strategy(Strategy(
            name=name, status=status, timescale="intraday",
            mechanism_family="flow", file_path=strategies_dir() / f"{name}.md",
            regime_applicability={"intraday": {"direction": [direction],
                                               "volatility": [volatility]}},
            data_points=[{"name": "hl_funding", "params": {"symbol": "BTC"},
                          "weight": 0.4}],
            created="2026-07-27", body_md=BODY), conn)

    def test_a_sibling_in_another_cell_does_not_count(self, conn):
        self._mk_cell(conn, "subject", "ranging", "normal")
        _tradeable_book(conn, "subject")
        self._mk_cell(conn, "elsewhere", "trending-up", "elevated")
        _tradeable_book(conn, "elsewhere")
        assert queries.strategy_expectancy(conn, "subject")["siblings_tried"] == 1

    def test_a_sibling_in_the_same_cell_counts(self, conn):
        self._mk_cell(conn, "subject", "ranging", "normal")
        _tradeable_book(conn, "subject")
        self._mk_cell(conn, "rival", "ranging", "normal")
        _tradeable_book(conn, "rival")
        assert queries.strategy_expectancy(conn, "subject")["siblings_tried"] == 2

    def test_crowding_a_cell_raises_that_cell_s_bar_only(self, conn):
        self._mk_cell(conn, "subject", "ranging", "normal")
        _tradeable_book(conn, "subject")
        lone = queries.strategy_expectancy(conn, "subject")["hurdle_pct"]
        for i in range(4):
            self._mk_cell(conn, f"rival{i}", "ranging", "normal")
            _tradeable_book(conn, f"rival{i}")
        assert queries.strategy_expectancy(conn, "subject")["hurdle_pct"] > lone

    def test_legacy_multi_cell_declaration_counts_in_every_cell(self, conn):
        """It genuinely competes in all of them."""
        from trading.strategies.files import Strategy, strategies_dir
        self._mk_cell(conn, "subject", "ranging", "normal")
        _tradeable_book(conn, "subject")
        loader.write_strategy(Strategy(
            name="legacy-wide", status="test", timescale="intraday",
            mechanism_family="flow",
            file_path=strategies_dir() / "legacy-wide.md",
            regime_applicability={"intraday": {
                "direction": ["ranging", "trending-up"],
                "volatility": ["normal", "elevated"]}},
            data_points=[{"name": "hl_funding", "params": {"symbol": "BTC"},
                          "weight": 0.4}],
            created="2026-07-01", body_md=BODY), conn)
        _tradeable_book(conn, "legacy-wide")
        assert queries.strategy_expectancy(conn, "subject")["siblings_tried"] == 2

    def test_retired_still_excluded_within_the_cell(self, conn):
        self._mk_cell(conn, "subject", "ranging", "normal")
        _tradeable_book(conn, "subject")
        self._mk_cell(conn, "gone", "ranging", "normal")
        _tradeable_book(conn, "gone")
        conn.execute("UPDATE strategies SET status='retired' WHERE name='gone'")
        conn.commit()
        assert queries.strategy_expectancy(conn, "subject")["siblings_tried"] == 1


class TestCellCapacity:
    def _mk(self, conn, name, direction, status="test"):
        from trading.strategies.files import Strategy, strategies_dir
        loader.write_strategy(Strategy(
            name=name, status=status, timescale="intraday",
            mechanism_family="flow", file_path=strategies_dir() / f"{name}.md",
            regime_applicability={"intraday": {"direction": [direction],
                                               "volatility": ["normal"]}},
            data_points=[{"name": "hl_funding", "params": {"symbol": "BTC"},
                          "weight": 0.4}],
            created="2026-07-27", body_md=BODY), conn)

    def test_occupancy_counts_test_and_active_only(self, conn):
        self._mk(conn, "a", "ranging")
        self._mk(conn, "b", "ranging", status="active")
        self._mk(conn, "c", "ranging", status="dormant")
        self._mk(conn, "d", "ranging", status="retired")
        row = next(r for r in queries.cell_capacity(conn)
                   if r["cell"] == "BTC/intraday/ranging/normal")
        assert row["occupants"] == 2          # dormant frees the slot
        assert row["slots_remaining"] == queries.CELL_OCCUPANCY_CAP - 2

    def test_over_cap_is_reported(self, conn):
        for i in range(queries.CELL_OCCUPANCY_CAP + 2):
            self._mk(conn, f"s{i}", "ranging")
        row = next(r for r in queries.cell_capacity(conn)
                   if r["cell"] == "BTC/intraday/ranging/normal")
        assert row["over_by"] == 2 and row["slots_remaining"] == 0


class TestRegimeEligibility:
    """Selection is code's answer now, not the agent's.

    Predict used to match a declared cell against REGIME.md in its own
    reasoning, so the rotation counters arrived unfiltered: a book silent 23
    days because its cell was dark reads as a scheduling gap when it is simply
    correctly idle. On the live desk this cut swing candidates from 65 to 9.
    """

    def _mk(self, conn, name, direction, volatility):
        from trading.strategies.files import Strategy, strategies_dir
        loader.write_strategy(Strategy(
            name=name, status="test", timescale="swing",
            mechanism_family="flow", file_path=strategies_dir() / f"{name}.md",
            regime_applicability={"swing": {"direction": [direction],
                                            "volatility": [volatility]}},
            data_points=[{"name": "hl_funding", "params": {"symbol": "BTC"},
                          "weight": 0.4}],
            created="2026-07-27", body_md=BODY), conn)

    def _row(self, conn, name):
        return next(r for r in queries.strategies_by_timescale(conn, "swing")
                    if r["name"] == name)

    def test_matching_the_live_cell_is_eligible(self, conn):
        from trading.lifecycle import write as w
        w.record_regime(conn, timescale="swing", direction="ranging",
                        volatility="compressed")
        self._mk(conn, "in-cell", "ranging", "compressed")
        assert self._row(conn, "in-cell")["regime_eligible"] is True

    def test_a_dark_cell_is_not_eligible(self, conn):
        from trading.lifecycle import write as w
        w.record_regime(conn, timescale="swing", direction="ranging",
                        volatility="compressed")
        self._mk(conn, "elsewhere", "trending-up", "elevated")
        assert self._row(conn, "elsewhere")["regime_eligible"] is False

    def test_unknown_regime_is_None_not_False(self, conn):
        """A desk that has never assessed must not read as 'nothing eligible'."""
        self._mk(conn, "orphan", "ranging", "compressed")
        assert self._row(conn, "orphan")["regime_eligible"] is None

    def test_eligibility_follows_a_flip(self, conn):
        from trading.lifecycle import write as w
        w.record_regime(conn, timescale="swing", direction="ranging",
                        volatility="compressed")
        self._mk(conn, "mover", "trending-up", "normal")
        assert self._row(conn, "mover")["regime_eligible"] is False
        w.record_regime(conn, timescale="swing", direction="trending-up",
                        volatility="normal", flipped=True)
        assert self._row(conn, "mover")["regime_eligible"] is True

    def test_cell_capacity_marks_what_is_lit(self, conn):
        from trading.lifecycle import write as w
        w.record_regime(conn, timescale="swing", direction="ranging",
                        volatility="compressed")
        self._mk(conn, "a", "ranging", "compressed")
        self._mk(conn, "b", "trending-up", "normal")
        caps = {x["cell"]: x for x in queries.cell_capacity(conn)}
        assert caps["BTC/swing/ranging/compressed"]["lit"] is True
        assert caps["BTC/swing/trending-up/normal"]["lit"] is False
