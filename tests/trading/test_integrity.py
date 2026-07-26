"""Desk integrity — one test per invariant, each written against a failure
that actually happened on 2026-07-26 or before.

Every check must do BOTH jobs: fire on a violating desk and stay silent on a
healthy one. A health check that never fires is indistinguishable from health.
"""

import json
import sqlite3
import time

import pytest

from trading.lifecycle import integrity

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
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE action_runs (id INTEGER PRIMARY KEY, action_type TEXT,
                                  ts REAL, agent TEXT, session_name TEXT,
                                  ok INTEGER, notes_md TEXT);
        CREATE TABLE strategies (name TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE predictions (id INTEGER PRIMARY KEY, outcome TEXT);
        CREATE TABLE positions (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE capital_movements (id INTEGER PRIMARY KEY, ts REAL,
                                        tx_hash TEXT, amount_token REAL,
                                        amount_usd_at_time REAL,
                                        movement_type TEXT, token TEXT);
        """
    )
    now = time.time()
    for action in ("perception", "regime", "predict"):
        c.execute("INSERT INTO action_runs (action_type, ts, agent, ok) "
                  "VALUES (?,?,?,1)", (action, now - 600, "plutus-x"))
    c.execute("INSERT INTO strategies VALUES ('s1','test')")
    c.execute("INSERT INTO predictions (outcome) VALUES ('correct')")
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
        conn.execute("INSERT INTO positions (status) VALUES ('closed')")
        conn.commit()
        assert "capital_unrecorded" in _names(
            integrity.check_integrity(conn, home=home))

    def test_no_positions_yet_is_not_flagged(self, conn, home):
        conn.execute("DELETE FROM capital_movements")
        conn.commit()
        assert "capital_unrecorded" not in _names(
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
