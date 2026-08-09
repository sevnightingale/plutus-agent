"""Regime as a structured record — writer, queries, board rendering.

Regime lived only in REGIME.md until 2026-07-27: no code could read it, so
predict matched strategies against the tape inside its own reasoning and every
cell-aware surface stopped at the prompt boundary. The database is truth now
and the board is a rendering, following live_state.py.
"""

import sqlite3
import time

import pytest

from trading.lifecycle import db, regime_board, write
from trading.lifecycle.db import get_db
from trading.lifecycle.queries import current_regime, regime_occupancy

# The live board, byte-for-byte, as four agents read it. Per-symbol
# sections since 2026-08-08 (the multi-asset turn); the table shape within
# a section is unchanged from the single-symbol era.
LIVE_BOARD = """# REGIME
updated_at: 2026-07-27 12:15 UTC    by: plutus-regime

### BTC

| timescale | direction | volatility | macro |
|---|---|---|---|
| intraday | ranging | normal | — |
| swing | ranging | compressed | — |
| position | ranging | compressed | neutral |
"""

NOTES = """
## Assessment notes

**The swing squeeze persists.** 4h ATR at the 1.6th percentile.
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db._create_fresh(c)
    return c


class TestRenderedFormat:
    """The table's shape is load-bearing: predict, generate, main and regime
    all read REGIME.md as prompt text, so a format change moves four agents'
    behaviour at once."""

    def test_reproduces_the_live_board_byte_for_byte(self):
        regime = {
            "intraday": {"direction": "ranging", "volatility": "normal",
                         "macro": None},
            "swing": {"direction": "ranging", "volatility": "compressed",
                      "macro": None},
            "position": {"direction": "ranging", "volatility": "compressed",
                         "macro": "neutral"},
        }
        assert regime_board.render_table(
            {"BTC": regime}, updated_at="2026-07-27 12:15") == LIVE_BOARD

    def test_an_unassessed_timescale_still_renders_a_row(self):
        out = regime_board.render_table({"BTC": {}},
                                        updated_at="2026-07-27 12:15")
        assert out.count("(unassessed)") == 6      # 3 rows x direction+vol
        assert "| position |" in out

    def test_multi_symbol_board_sections(self, conn):
        write.record_regime(conn, timescale="swing", direction="ranging",
                            volatility="normal")
        write.record_regime(conn, symbol="xyz:GOLD", timescale="swing",
                            direction="trending-up", volatility="compressed")
        assert regime_board.board_symbols(conn) == ["BTC", "xyz:GOLD"]
        out = regime_board.render_table(
            {s: current_regime(conn, symbol=s)
             for s in regime_board.board_symbols(conn)})
        assert out.index("### BTC") < out.index("### xyz:GOLD")
        # ### heads must not trip the notes split (^##\s).
        assert not regime_board._NOTES_RE.search(out)


class TestBoard:
    def _board(self, tmp_path, body=LIVE_BOARD + NOTES):
        p = tmp_path / "REGIME.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_render_preserves_the_agent_s_notes(self, conn, tmp_path):
        p = self._board(tmp_path)
        write.record_regime(conn, timescale="swing", direction="trending-up",
                            volatility="elevated")
        assert regime_board.write_board(conn, p)["ok"]
        text = p.read_text()
        assert "## Assessment notes" in text
        assert "4h ATR at the 1.6th percentile" in text
        assert "| swing | trending-up | elevated | — |" in text

    def test_missing_file_is_honest_failure_not_a_create(self, conn, tmp_path):
        out = regime_board.write_board(conn, tmp_path / "nope.md")
        assert out["ok"] is False and "does not exist" in out["error"]
        assert not (tmp_path / "nope.md").exists()

    def test_drift_is_detected(self, conn, tmp_path):
        """A row landing without the board following is the one failure mode
        of a rendered file — the Live State zone froze exactly this way."""
        p = self._board(tmp_path)
        write.record_regime(conn, timescale="swing", direction="trending-down",
                            volatility="elevated")
        assert regime_board.board_matches_db(conn, p) is False
        regime_board.write_board(conn, p)
        assert regime_board.board_matches_db(conn, p) is True

    def test_never_assessed_does_not_read_as_drift(self, conn, tmp_path):
        assert regime_board.board_matches_db(conn, self._board(tmp_path)) is True

    def test_notes_retention_keeps_newest_dated_entries(self, conn, tmp_path):
        """The zone grew to 215KB in nine days unbounded; each re-render now
        keeps the newest NOTES_KEEP dated entries. Undated sections (the
        header, the flip log) survive wherever they sit."""
        dated = "".join(
            f"## {h:02d}:00Z Day {h} — entry\n\nbody {h}\n\n"
            for h in range(9))                       # newest-first: 00..08
        notes = "## Assessment notes\n\n" + dated + "## Flip Log Update\n\nflips\n"
        p = self._board(tmp_path, body=LIVE_BOARD + "\n" + notes)
        write.record_regime(conn, timescale="swing", direction="ranging",
                            volatility="normal")
        assert regime_board.write_board(conn, p)["ok"]
        text = p.read_text()
        assert "## Assessment notes" in text
        assert "## Flip Log Update" in text
        kept = [h for h in range(9) if f"## {h:02d}:00Z" in text]
        assert kept == list(range(regime_board.NOTES_KEEP))


class TestWriterVocabulary:
    """Closed taxonomy, enforced in the writer. M is cell-scoped now, so a
    label outside the vocabulary silently changes whose bar a strategy is
    measured against."""

    def test_accepts_the_vocabulary(self, conn):
        assert write.record_regime(conn, timescale="position",
                                   direction="ranging", volatility="compressed",
                                   macro="risk-off")

    @pytest.mark.parametrize("bad", ["choppy", "trending", "", "RANGING"])
    def test_refuses_an_invented_direction(self, conn, bad):
        with pytest.raises(ValueError, match="direction"):
            write.record_regime(conn, timescale="swing", direction=bad,
                                volatility="normal")

    def test_refuses_an_invented_volatility(self, conn):
        with pytest.raises(ValueError, match="volatility"):
            write.record_regime(conn, timescale="swing", direction="ranging",
                                volatility="mild")

    def test_macro_outside_position_scale_is_refused_not_dropped(self, conn):
        """A caller that thinks intraday has a macro label has misunderstood
        the taxonomy and should hear so."""
        with pytest.raises(ValueError, match="position-scale"):
            write.record_regime(conn, timescale="intraday", direction="ranging",
                                volatility="normal", macro="risk-on")

    def test_refuses_an_invented_macro(self, conn):
        with pytest.raises(ValueError, match="macro"):
            write.record_regime(conn, timescale="position", direction="ranging",
                                volatility="normal", macro="risk-sideways")


class TestQueries:
    def test_current_regime_is_the_latest_per_timescale(self, conn):
        now = time.time()
        write.record_regime(conn, timescale="swing", direction="ranging",
                            volatility="normal", ts=now - 7200)
        write.record_regime(conn, timescale="swing", direction="trending-up",
                            volatility="elevated", ts=now - 60)
        cur = current_regime(conn)["swing"]
        assert cur["direction"] == "trending-up"
        assert cur["cell"] == "swing/trending-up/elevated"

    def test_an_unassessed_timescale_is_absent_not_guessed(self, conn):
        write.record_regime(conn, timescale="swing", direction="ranging",
                            volatility="normal")
        assert "position" not in current_regime(conn)

    def test_occupancy_counts_distinct_days(self, conn):
        now = time.time()
        for d in range(4):
            write.record_regime(conn, timescale="swing", direction="ranging",
                                volatility="compressed", ts=now - d * 86400)
        for d in (4, 5):
            write.record_regime(conn, timescale="swing", direction="trending-up",
                                volatility="normal", ts=now - d * 86400)
        occ = {x["cell"]: x for x in regime_occupancy(conn, now - 30 * 86400)}
        assert occ["swing/ranging/compressed"]["days_lit"] == 4
        assert occ["swing/trending-up/normal"]["days_lit"] == 2
        assert occ["swing/ranging/compressed"]["lit_fraction"] > \
            occ["swing/trending-up/normal"]["lit_fraction"]


class TestMigrationBackfill:
    def test_v6_seeds_history_from_prediction_tags(self, tmp_path):
        """Derived, and labelled so — the regime for cells the desk SAMPLED,
        not a reading of what the tape did. Starting empty would leave
        occupancy unmeasurable for a month."""
        path = tmp_path / "lifecycle.db"
        c = get_db(path)
        now = time.time()
        for i, tag in enumerate(("swing/ranging/compressed",
                                 "swing/trending-up/normal")):
            c.execute(
                """INSERT INTO predictions (claim_md, ts, horizon_ts, timescale,
                       success_criteria_json, conviction, regime_tag)
                   VALUES ('z',?,?,'swing','{}',0.7,?)""",
                (now - i * 86400, now + 3600, tag))
        c.execute("DELETE FROM regime_observations")
        c.execute("UPDATE schema_version SET version = 5")
        c.commit()
        c.close()

        c2 = get_db(path)                     # re-open runs v5 -> v6
        rows = c2.execute(
            "SELECT timescale, direction, volatility, source "
            "FROM regime_observations ORDER BY ts").fetchall()
        assert len(rows) == 2
        assert {r["source"] for r in rows} == {"derived"}
        assert {r["direction"] for r in rows} == {"ranging", "trending-up"}
