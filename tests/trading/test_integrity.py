"""Desk integrity — one test per invariant, each written against a failure
that actually happened on 2026-07-26 or before.

Every check must do BOTH jobs: fire on a violating desk and stay silent on a
healthy one. A health check that never fires is indistinguishable from health.
"""

import json
import sqlite3
import time

import pytest

from trading.lifecycle import db, integrity

_HEALTHY_PLUTUS = """# PLUTUS

## Doctrine

North star.

## Live State

<!-- TOOL-REWRITTEN ONLY. Do not edit by hand. -->
- equity_usd: $75.12
- snapshot_at: {stamp}
- regime: see REGIME.md
- open_position: none
- strategies: 0 active / 88 test / 9 dormant / 15 retired

## Lessons

- L1. one lesson
"""


@pytest.fixture()
def home(tmp_path):
    stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    (tmp_path / "PLUTUS.md").write_text(_HEALTHY_PLUTUS.format(stamp=stamp))
    (tmp_path / "REGIME.md").write_text("# REGIME\n\n| a | b |\n")
    (tmp_path / "PERCEPTION.md").write_text("# PERCEPTION\n\n| a | b |\n")
    return tmp_path


@pytest.fixture()
def conn():
    """A healthy desk on the REAL schema.

    This fixture hand-wrote five minimal tables until 2026-07-27. That is the
    same shape of mistake that let the capital reconciler duplicate its whole
    history while its idempotency test passed: a fixture that builds its own
    schema can only ever confirm the code agrees with the fixture, and it
    cannot express a check that needs a column the fixture never invented.
    Build through the real creation path and seed on top of it.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db._create_fresh(c)
    now = time.time()
    for action in ("perception", "regime", "predict"):
        c.execute("INSERT INTO action_runs (action_type, ts, agent, ok) "
                  "VALUES (?,?,?,1)", (action, now - 600, "plutus-x"))
    c.execute("""INSERT INTO strategies (name, status, timescale,
                     mechanism_family, regime_applicability_json,
                     data_points_json, file_path, created_at, updated_at)
                 VALUES ('s1','test','intraday','flow','{}','[]','/tmp/s1.md',?,?)""",
              (now, now))
    c.execute("""INSERT INTO predictions (claim_md, ts, horizon_ts, timescale,
                     success_criteria_json, conviction, outcome)
                 VALUES ('z',?,?,'intraday','{}',0.7,'correct')""",
              (now, now + 3600))
    c.execute("INSERT INTO capital_movements (ts, tx_hash, amount_token, "
              "amount_usd_at_time, movement_type, token) "
              "VALUES (1.0,'0xabc',23.99,23.99,'send','USDC')")
    c.commit()
    return c


class TestHealthyDesk:
    def test_silent_when_everything_holds(self, conn, home):
        out = integrity.check_integrity(conn, home=home)
        assert out["ok"], out["violations"]
        assert out["checks_failed"] == []
        assert out["checks_run"] == len(integrity.CHECKS)


class TestBlackboards:
    def test_bloat_detected(self, conn, home):
        """The live PLUTUS.md reached 138 blank lines inside `## Live State`."""
        p = home / "PLUTUS.md"
        p.write_text(p.read_text().replace(
            "## Live State\n", "## Live State\n" + "\n" * 60))
        names = _names(integrity.check_integrity(conn, home=home))
        assert "blackboard_bloat" in names

    def test_missing_blackboard_is_critical(self, conn, home):
        (home / "REGIME.md").unlink()
        v = _by_name(integrity.check_integrity(conn, home=home), "blackboard_missing")
        assert v["severity"] == "critical"

    def test_missing_zone_detected(self, conn, home):
        """replace_zone no-ops on a missing zone, so the file just stops
        updating and nothing says why."""
        p = home / "PLUTUS.md"
        p.write_text(p.read_text().replace("## Live State", "## Live Status"))
        assert "blackboard_zone_missing" in _names(
            integrity.check_integrity(conn, home=home))

    def test_stale_live_state_detected(self, conn, home):
        p = home / "PLUTUS.md"
        old = time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() - 30 * 3600))
        p.write_text(_HEALTHY_PLUTUS.format(stamp=old))
        assert "live_state_stale" in _names(
            integrity.check_integrity(conn, home=home))

    def test_lessons_over_cap_detected(self, conn, home):
        p = home / "PLUTUS.md"
        lessons = "\n".join(f"- L{i}. lesson" for i in range(20))
        p.write_text(p.read_text().replace("- L1. one lesson", lessons))
        assert "lessons_over_cap" in _names(
            integrity.check_integrity(conn, home=home))


class TestStalenessCeiling:
    def test_breach_detected(self, conn, home):
        """Perception sat 11.4h against a 4h floor while main declined it."""
        conn.execute("UPDATE action_runs SET ts = ? WHERE action_type='perception'",
                     (time.time() - 11.4 * 3600,))
        conn.commit()
        v = _by_name(integrity.check_integrity(conn, home=home),
                     "staleness_ceiling_breached")
        assert v["severity"] == "critical"
        assert "perception" in v["detail"]

    def test_never_run_is_a_cold_start_not_a_breach(self, conn, home):
        conn.execute("DELETE FROM action_runs WHERE action_type='perception'")
        conn.commit()
        assert "staleness_ceiling_breached" not in _names(
            integrity.check_integrity(conn, home=home))


class TestTablesAndCapital:
    def test_empty_table_on_running_desk_detected(self, conn, home):
        """This is the reflections defect: 12 passes, 0 rows, no writer."""
        conn.execute("DELETE FROM strategies")
        conn.commit()
        assert "table_empty_on_running_desk" in _names(
            integrity.check_integrity(conn, home=home))

    def test_cold_install_is_not_flagged(self, conn, home):
        conn.execute("DELETE FROM action_runs")
        conn.execute("DELETE FROM strategies")
        conn.commit()
        assert "table_empty_on_running_desk" not in _names(
            integrity.check_integrity(conn, home=home))

    def test_unrecorded_capital_detected(self, conn, home):
        conn.execute("DELETE FROM capital_movements")
        conn.execute(
            """INSERT INTO positions (venue, symbol, side, size,
                   opening_trade_id, opened_at, status)
               VALUES ('hyperliquid','BTC','long',0.01,1,?,'closed')""",
            (time.time(),))
        conn.commit()
        assert "capital_unrecorded" in _names(
            integrity.check_integrity(conn, home=home))

    def test_no_positions_yet_is_not_flagged(self, conn, home):
        conn.execute("DELETE FROM capital_movements")
        conn.commit()
        assert "capital_unrecorded" not in _names(
            integrity.check_integrity(conn, home=home))


class TestRetirementEvidence:
    """Retirement lowers the desk's own bar, so it must be evidence-only.

    Retired books stopped counting toward the multiplicity premium on
    2026-07-27, which is what makes the graduation hurdle reachable at all —
    and simultaneously makes retiring a book an edit to that hurdle for every
    sibling at its timescale. This check is the thing that stops reflect
    lowering the bar by judgement.
    """

    def _retired_book(self, conn, name, n_correct, n_wrong):
        """A retired strategy with a simulatable book.

        Win/loss MIX drives expectancy, not the raw counts — the simulator
        derives the stop from the whole book's MAE distribution, so an
        all-winners book reads NEGATIVE (a 0.3 stop that every trade hits).
        18/6 measures +1.75%; 4/20 is properly dead.
        """
        import time as _t
        from trading.lifecycle import write as _w
        conn.execute(
            """INSERT INTO strategies (name, status, timescale, mechanism_family,
                   regime_applicability_json, data_points_json, file_path,
                   created_at, updated_at)
               VALUES (?, 'retired', 'intraday', 'flow', '{}', '[]', ?, ?, ?)""",
            (name, f"/tmp/{name}.md", _t.time(), _t.time()))
        for outcome, mae, reached, k in (("correct", -0.3, True, n_correct),
                                         ("wrong", -2.0, False, n_wrong)):
            for _ in range(k):
                pid = _w.record_prediction(conn, _w.PredictionDraft(
                    claim_md="z", horizon_ts=_t.time() + 3600,
                    entry_ref_price=100_000.0, near_edge_pct=1.5,
                    far_edge_pct=3.0, conviction=0.7, agent="plutus-predict",
                    symbol="BTC", strategy_name=name, kind="strategy"))
                _w.resolve_prediction(conn, pid, outcome, resolved_by="r",
                                      realized_value={"mae_pct": mae})
                if reached:
                    conn.execute(
                        "UPDATE predictions SET reached_far_at=? WHERE id=?",
                        (_t.time(), pid))
                # Single-cell book. The check reads cells, not the lifetime
                # blend, so an untagged book is unjudgeable by design — see
                # TestRetirementIsCellAware for the multi-cell case.
                conn.execute(
                    "UPDATE predictions SET regime_tag='intraday/trending-up/normal'"
                    " WHERE id=?", (pid,))
        conn.commit()

    def test_profitable_retirement_is_flagged(self, conn, home):
        self._retired_book(conn, "cut-too-soon", 18, 6)
        assert "retired_while_profitable" in _names(
            integrity.check_integrity(conn, home=home))

    def test_genuinely_dead_retirement_is_silent(self, conn, home):
        self._retired_book(conn, "properly-dead", 4, 20)
        assert "retired_while_profitable" not in _names(
            integrity.check_integrity(conn, home=home))

    def test_book_too_small_to_judge_is_not_flagged(self, conn, home):
        """Below n=20 the evidence bar does not apply, either way."""
        self._retired_book(conn, "thin", 6, 2)
        assert "retired_while_profitable" not in _names(
            integrity.check_integrity(conn, home=home))


class TestWakeLoop:
    def test_loop_detected(self, conn, home):
        (home / "wake_suppression.json").write_text(json.dumps({
            "staleness:perception": {"last_fired_ts": time.time(),
                                     "consecutive": 13, "suppressed": 0}}))
        v = _by_name(integrity.check_integrity(conn, home=home), "wake_loop")
        assert "13 times" in v["detail"]

    def test_normal_repetition_is_not_a_loop(self, conn, home):
        (home / "wake_suppression.json").write_text(json.dumps({
            "staleness:perception": {"last_fired_ts": time.time(),
                                     "consecutive": 2, "suppressed": 1}}))
        assert "wake_loop" not in _names(integrity.check_integrity(conn, home=home))


class TestFailureHandling:
    def test_a_raising_check_becomes_a_violation(self, conn, home, monkeypatch):
        """A health check that fails open is worse than none — it reports
        health it has not verified."""
        def _boom(*a, **k):
            raise RuntimeError("kaboom")
        monkeypatch.setitem(integrity.CHECKS, "exploding", _boom)
        out = integrity.check_integrity(conn, home=home)
        assert out["ok"] is False
        assert "exploding" in out["checks_failed"]
        assert "kaboom" in _by_name(out, "integrity_check_failed")["detail"]

    def test_one_bad_check_does_not_stop_the_others(self, conn, home, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("kaboom")
        monkeypatch.setitem(integrity.CHECKS, "exploding", _boom)
        conn.execute("UPDATE action_runs SET ts = ? WHERE action_type='perception'",
                     (time.time() - 20 * 3600,))
        conn.commit()
        names = _names(integrity.check_integrity(conn, home=home))
        assert "staleness_ceiling_breached" in names and "integrity_check_failed" in names


class TestTool:
    def test_registered_under_resolution(self):
        from harness.tools import registry as reg
        import trading.dispatchers.integrity  # noqa: F401
        assert reg.registry.get_toolset_for_tool("desk_integrity_check") == "resolution"


def _names(result):
    return {v["check"] for v in result["violations"]}


def _by_name(result, name):
    matches = [v for v in result["violations"] if v["check"] == name]
    assert matches, f"expected a {name!r} violation, got {result['violations']}"
    return matches[0]


class TestRetirementIsCellAware:
    """The retirement bar reads cells, not the lifetime blend.

    The blended form of this check shipped hours earlier and would have
    reported nothing while reflect retired ema20-pivot-swing — a mechanism
    positive in four of five cells — thereby lowering the graduation hurdle
    for every sibling on a false premise.
    """

    def _retired_mixed(self, conn, name, good_n, bad_n):
        import time as _t
        from trading.lifecycle import write as _w
        conn.execute(
            """INSERT INTO strategies (name, status, timescale, mechanism_family,
                   regime_applicability_json, data_points_json, file_path,
                   created_at, updated_at)
               VALUES (?, 'retired', 'swing', 'flow', '{}', '[]', ?, ?, ?)""",
            (name, f"/tmp/{name}.md", _t.time(), _t.time()))
        for tag, outcome, mae, reached, k in (
                ("swing/ranging/normal", "correct", -0.3, True, good_n),
                ("swing/trending-up/compressed", "wrong", -2.0, False, bad_n)):
            for _ in range(k):
                pid = _w.record_prediction(conn, _w.PredictionDraft(
                    claim_md="z", horizon_ts=_t.time() + 3600,
                    entry_ref_price=100_000.0, near_edge_pct=1.5,
                    far_edge_pct=3.0, conviction=0.7, agent="plutus-predict",
                    symbol="BTC", strategy_name=name, kind="strategy"))
                _w.resolve_prediction(conn, pid, outcome, resolved_by="r",
                                      realized_value={"mae_pct": mae})
                if reached:
                    conn.execute(
                        "UPDATE predictions SET reached_far_at=? WHERE id=?",
                        (_t.time(), pid))
                conn.execute("UPDATE predictions SET regime_tag=? WHERE id=?",
                             (tag, pid))
        conn.commit()

    def test_a_living_cell_under_a_dead_blend_is_flagged(self, conn, home):
        """The ema20-pivot-swing case, as a test."""
        self._retired_mixed(conn, "ema20-shaped", good_n=14, bad_n=12)
        names = _names(integrity.check_integrity(conn, home=home))
        assert "retired_while_profitable" in names

    def test_dead_in_every_cell_stays_silent(self, conn, home):
        self._retired_mixed(conn, "truly-dead", good_n=0, bad_n=26)
        assert "retired_while_profitable" not in _names(
            integrity.check_integrity(conn, home=home))


class TestRegimeBoard:
    """The board is a rendering; drift between it and the database is the one
    failure mode that arrangement has, and it is how the Live State zone froze
    for a month with nothing to notice."""

    _BOARD = ("# REGIME\nupdated_at: 2026-07-27 12:15 UTC    by: plutus-regime\n\n"
              "| timescale | direction | volatility | macro |\n|---|---|---|---|\n"
              "| intraday | ranging | normal | — |\n"
              "| swing | ranging | compressed | — |\n"
              "| position | ranging | compressed | neutral |\n\n"
              "## Assessment notes\n\nprose\n")

    def test_silent_when_the_board_agrees(self, conn, home):
        from trading.lifecycle import write as w
        (home / "REGIME.md").write_text(self._BOARD, encoding="utf-8")
        w.record_regime(conn, timescale="swing", direction="ranging",
                        volatility="compressed")
        assert "regime_board_stale" not in _names(
            integrity.check_integrity(conn, home=home))

    def test_stale_board_detected(self, conn, home):
        from trading.lifecycle import write as w
        (home / "REGIME.md").write_text(self._BOARD, encoding="utf-8")
        w.record_regime(conn, timescale="swing", direction="trending-down",
                        volatility="elevated")
        assert "regime_board_stale" in _names(
            integrity.check_integrity(conn, home=home))

    def test_never_assessed_is_not_a_violation(self, conn, home):
        (home / "REGIME.md").write_text(self._BOARD, encoding="utf-8")
        assert "regime_board_stale" not in _names(
            integrity.check_integrity(conn, home=home))


class TestToolRegistry:
    """A dispatcher that fails to import takes its toolset with it, and the
    agent declaring that toolset spawns anyway — silently short the tool its
    procedure is built around. Nothing errors; the desk just stops recording
    something. `record_regime` shipped that way on 2026-07-27 and ran a night.
    """

    def test_import_failure_is_critical(self, conn, home):
        from unittest.mock import patch as _patch
        with _patch("harness.tools.registry.builtin_import_failures",
                    return_value=[("trading.dispatchers.regime_write",
                                   "ModuleNotFoundError: No module named 'harness.tools.result'")]):
            out = integrity.check_integrity(conn, home=home)
        hit = [v for v in out["violations"] if v["check"] == "tool_module_import_failed"]
        assert hit, _names(out)
        assert hit[0]["severity"] == "critical"
        assert "regime_write" in hit[0]["detail"]

    def test_agent_declaring_a_phantom_toolset_is_critical(self, conn, home):
        from unittest.mock import patch as _patch
        from harness.tools.registry import discover_builtin_tools
        discover_builtin_tools()
        with _patch("harness.tools.registry.registry.get_tool_names_for_toolset",
                    return_value=[]), \
             _patch("harness.tools.registry.registry.get_toolset_alias_target",
                    return_value=None):
            out = integrity.check_integrity(conn, home=home)
        hit = [v for v in out["violations"] if v["check"] == "agent_toolset_missing"]
        assert hit, _names(out)
        assert hit[0]["severity"] == "critical"

    def test_silent_on_the_real_tree(self, conn, home):
        from harness.tools.registry import discover_builtin_tools
        discover_builtin_tools()
        out = integrity.check_integrity(conn, home=home)
        assert "tool_module_import_failed" not in _names(out)
        assert "agent_toolset_missing" not in _names(out)
