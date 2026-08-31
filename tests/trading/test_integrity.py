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

    def test_oversized_board_detected(self, conn, home):
        """PERCEPTION.md hit 133k and perception could not read its own file."""
        (home / "PERCEPTION.md").write_text(
            "# PERCEPTION\n\n" + ("row\n" * 20_000), encoding="utf-8")
        assert "blackboard_oversized" in _names(
            integrity.check_integrity(conn, home=home))

    def test_file_db_status_drift_detected(self, conn, home):
        """The 2026-07-27 cell-split wrote dormant to the db and left the
        file on test — predict loads from files, so the parent stayed live."""
        from trading.strategies.files import render_strategy, Strategy
        d = home / "strategies"
        d.mkdir()
        s = Strategy(name="split-parent", status="test", timescale="swing",
                     mechanism_family="flow", file_path=d / "split-parent.md",
                     body_md="\n# Hypothesis\nh\n\n# Mechanism\nm\n")
        (d / "split-parent.md").write_text(render_strategy(s), encoding="utf-8")
        conn.execute(
            """INSERT INTO strategies (name, status, timescale, mechanism_family,
                   regime_applicability_json, data_points_json, file_path,
                   created_at, updated_at)
               VALUES ('split-parent','dormant','swing','flow','{}','[]',?,?,?)""",
            (str(d / "split-parent.md"), time.time(), time.time()))
        conn.commit()
        assert "file_db_status" in _names(
            integrity.check_integrity(conn, home=home))

    def test_unassessed_worked_symbol_detected(self, conn, home):
        """Regime running on BTC while GOLD books sit unmatched."""
        from trading.lifecycle import write as w
        w.record_regime(conn, symbol="BTC", timescale="swing",
                        direction="ranging", volatility="normal")
        conn.execute(
            """INSERT INTO strategies (name, status, timescale, mechanism_family,
                   symbol, regime_applicability_json, data_points_json,
                   file_path, created_at, updated_at)
               VALUES ('gold-seed','test','swing','flow','xyz:GOLD','{}','[]',
                       '/tmp/g.md',?,?)""",
            (time.time(), time.time()))
        conn.commit()
        assert "regime_symbol_unassessed" in _names(
            integrity.check_integrity(conn, home=home))

    def test_cold_desk_is_not_unassessed(self, conn, home):
        """No regime observations at all is a cold start, not a skip."""
        assert "regime_symbol_unassessed" not in _names(
            integrity.check_integrity(conn, home=home))


class TestStalenessCeiling:
    def test_breach_detected(self, conn, home):
        """Predict sat 18h against its 16h ceiling — the one seat the ceiling
        still backstops since the sustainable-desk rebuild (perception and
        regime are code and left the ceilings table)."""
        conn.execute("UPDATE action_runs SET ts = ? WHERE action_type='predict'",
                     (time.time() - 18 * 3600,))
        conn.commit()
        v = _by_name(integrity.check_integrity(conn, home=home),
                     "staleness_ceiling_breached")
        assert v["severity"] == "critical"
        assert "predict" in v["detail"]

    def test_never_run_is_a_cold_start_not_a_breach(self, conn, home):
        conn.execute("DELETE FROM action_runs WHERE action_type='predict'")
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


class TestNoDormant:
    """Dormant is abolished. A leftover row is a writer that did not hear."""

    def test_dormant_row_is_flagged(self, conn, home):
        conn.execute(
            """INSERT INTO strategies (name, status, timescale, mechanism_family,
                   regime_applicability_json, data_points_json, file_path,
                   created_at, updated_at)
               VALUES ('ghost','dormant','swing','flow','{}','[]','/tmp/g.md',?,?)""",
            (time.time(), time.time()))
        conn.commit()
        assert "dormant_status" in _names(
            integrity.check_integrity(conn, home=home))

    def test_retired_is_silent(self, conn, home):
        conn.execute(
            """UPDATE strategies SET status='retired' WHERE name='s1'""")
        conn.commit()
        assert "dormant_status" not in _names(
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

    def test_stopped_loop_is_cleared_not_declined(self, conn, home):
        """The counter resets only on the NEXT fire, so after the condition
        clears the old count lingers in the state file. A key silent past
        WAKE_LOOP_STALE_S has stopped looping — no violation (board #480)."""
        (home / "wake_suppression.json").write_text(json.dumps({
            "staleness:perception": {
                "last_fired_ts": time.time() - integrity.WAKE_LOOP_STALE_S - 60,
                "consecutive": 9, "suppressed": 0}}))
        assert "wake_loop" not in _names(integrity.check_integrity(conn, home=home))


class TestAppendOnly:
    """Twice rows vanished from an append-only table via unrecorded
    hand-repairs (board #481). Counts checkpoint to append_only_counts.json;
    a decrease is the violation."""

    def _obs(self, conn, n):
        for i in range(n):
            conn.execute(
                "INSERT INTO observations (session_name, agent, ts, text_md)"
                " VALUES ('s', 'a', ?, 'x')", (time.time() + i,))
        conn.commit()

    def test_first_run_checkpoints_silently(self, conn, home):
        self._obs(conn, 3)
        assert "append_only_shrunk" not in _names(
            integrity.check_integrity(conn, home=home))
        state = json.loads((home / "append_only_counts.json").read_text())
        assert state["counts"]["observations"] == 3

    def test_growth_is_silent(self, conn, home):
        self._obs(conn, 2)
        integrity.check_integrity(conn, home=home)
        self._obs(conn, 2)
        assert "append_only_shrunk" not in _names(
            integrity.check_integrity(conn, home=home))

    def test_shrinkage_fires_and_records_the_event(self, conn, home):
        self._obs(conn, 5)
        integrity.check_integrity(conn, home=home)
        conn.execute("DELETE FROM observations WHERE id > 2")
        conn.commit()
        v = _by_name(integrity.check_integrity(conn, home=home),
                     "append_only_shrunk")
        assert "observations shrank 5 → 2" in v["detail"]
        state = json.loads((home / "append_only_counts.json").read_text())
        ev = state["events"][-1]
        assert (ev["table"], ev["from"], ev["to"]) == ("observations", 5, 2)
        # The checkpoint moved with the shrink — it fires once, not forever.
        assert "append_only_shrunk" not in _names(
            integrity.check_integrity(conn, home=home))


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
        conn.execute("UPDATE action_runs SET ts = ? WHERE action_type='predict'",
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


class TestWatcherFds:
    """The 17th invariant — descriptor pressure in the watcher daemon.

    The full account of what it catches and why lives in
    ``integrity._check_watcher_fds``, where the invariant is enforced.
    """

    def test_skipped_for_a_home_that_is_not_the_live_runtime(self, conn, home):
        """Scoping guard: it inspects a live host process, so a temp home
        must not drag the machine's state into a unit test's verdict."""
        assert integrity._check_watcher_fds(conn, home) == []


    def test_pm2_deployment_is_found_when_systemd_is_not(self, monkeypatch):
        """The OSS tree runs the daemon under pm2, the manor under systemd.

        Hardcoding either makes the invariant dead on every install using the
        other — a check that cannot go red, reading as coverage.
        """
        import json as _json
        import subprocess as _sp

        def _fake(args, **kw):
            if args and args[0] == "systemctl":
                return type("R", (), {"stdout": "", "returncode": 1})()
            if args and args[0] == "pm2":
                return type("R", (), {
                    "stdout": _json.dumps(
                        [{"name": "plutus-watchers", "pid": 4242}]),
                    "returncode": 0})()
            raise AssertionError(f"unexpected command {args}")

        monkeypatch.setattr(_sp, "run", _fake)
        assert integrity._watcher_pid() == 4242

    def test_pid_is_none_when_no_process_manager_knows_the_daemon(
        self, monkeypatch
    ):
        import subprocess as _sp

        monkeypatch.setattr(
            _sp, "run",
            lambda *a, **k: type("R", (), {"stdout": "", "returncode": 1})())
        assert integrity._watcher_pid() is None

    def test_reports_unknown_rather_than_clear_when_it_cannot_look(
        self, conn, home, monkeypatch
    ):
        """A check that cannot resolve its target must not say all-clear."""
        import subprocess as _sp

        from harness import constants

        monkeypatch.setattr(constants, "get_hermes_home", lambda: home)
        monkeypatch.setattr(
            _sp, "run", lambda *a, **k: type("R", (), {"stdout": "4242"})()
        )

        class _FakeProc:
            """Stands in for Path('/proc'); /proc/<pid>/fd refuses to be read."""

            def exists(self):
                return True

            def __truediv__(self, other):
                return self

            def iterdir(self):
                raise OSError("permission denied")

            def __str__(self):
                return "/proc/4242/fd"

        monkeypatch.setattr(
            integrity, "Path", lambda *a, **k: _FakeProc()
        )

        out = integrity._check_watcher_fds(conn, home)

        assert out, "an unreadable fd table must produce a violation, not silence"
        assert "UNKNOWN" in out[0]["detail"]


class TestAgentEscalations:
    """The reader for specialist escalation reports (board #657) — fires on
    consecutive reported blockage, silent on health, blind to legacy rows."""

    def _run(self, conn, notes_list, ok=1):
        now = time.time()
        for i, notes in enumerate(notes_list):
            conn.execute(
                "INSERT INTO action_runs (action_type, ts, agent, ok, notes_md)"
                " VALUES ('predict', ?, 'plutus-predict', ?, ?)",
                (now - 60 * (len(notes_list) - i), ok, notes))
        conn.commit()

    def _fire(self, conn, home):
        return [v for v in integrity._check_agent_escalations(conn, home)
                if v["check"] == "agent_escalations"]

    def test_two_consecutive_findings_fire(self, conn, home):
        self._run(conn, [
            json.dumps({"escalation_findings": ["SWEEP-MANIFEST GAP: ..."],
                        "summary": "{}"}),
            json.dumps({"escalation_findings": ["still blocked"],
                        "summary": "{}"})])
        v = self._fire(conn, home)
        assert len(v) == 1 and "still blocked" in v[0]["detail"]

    def test_single_finding_is_silent(self, conn, home):
        self._run(conn, [
            json.dumps({"escalation_findings": [], "summary": "{}"}),
            json.dumps({"escalation_findings": ["one-off"], "summary": "{}"})])
        assert self._fire(conn, home) == []

    def test_legacy_truncated_rows_are_skipped(self, conn, home):
        # pre-fix rows are unparseable JSON prefixes — never counted, so two
        # of them plus one clean report stays silent rather than crashing
        self._run(conn, [
            '{"predictions": [{"id": 1, "claim',
            '{"predictions": [{"id": 2',
            json.dumps({"escalation_findings": [], "summary": "{}"})])
        assert self._fire(conn, home) == []

    def test_perception_refresh_failures_fire_independently(self, conn, home):
        self._run(conn, [
            json.dumps({"perception_stale": [{"strategy": "s", "stale": []}],
                        "summary": "{}"}),
            json.dumps({"perception_stale": [{"strategy": "s", "stale": []}],
                        "summary": "{}"})])
        v = self._fire(conn, home)
        assert len(v) == 1 and "refresh failures" in v[0]["detail"]


class TestReportNotes:
    """The writer half of board #657: notes_md must stay parseable with the
    escalation fields whole, however large the rest of the report."""

    def test_findings_survive_a_large_report(self):
        from harness.spawn import _report_notes
        payload = {"predictions": [{"claim": "x" * 200}] * 20,
                   "escalation_findings": ["THE BLOCKER: " + "y" * 500]}
        doc = json.loads(_report_notes(payload))
        assert doc["escalation_findings"][0].startswith("THE BLOCKER")
        assert len(doc["summary"]) <= 400   # the rest stays bounded

    def test_items_and_lists_are_bounded(self):
        from harness.spawn import _report_notes
        payload = {"escalation_findings": ["z" * 5000] * 50}
        doc = json.loads(_report_notes(payload))
        assert len(doc["escalation_findings"]) == 20
        assert all(len(i) <= 1000 for i in doc["escalation_findings"])

    def test_dict_items_serialize(self):
        from harness.spawn import _report_notes
        payload = {"perception_stale": [{"strategy": "s", "stale": [1, 2]}],
                   "actionable": None}
        doc = json.loads(_report_notes(payload))
        assert "strategy" in doc["perception_stale"][0]
