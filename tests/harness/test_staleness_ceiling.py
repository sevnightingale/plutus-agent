"""The staleness ceiling — the refresh main cannot decline.

Regression for 2026-07-26: main believed it was Saturday, declined perception
thirteen consecutive times over eleven hours, and scheduled its next refresh
for a day that had already passed. Nothing could contradict it.
"""

import sqlite3
import time

import pytest

from harness.cli import staleness_ceiling as SC
from trading.dispatchers.wake import STALENESS_CEILINGS, STALENESS_FLOORS


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row        # as get_db() configures it
    c.execute("""CREATE TABLE action_runs (id INTEGER PRIMARY KEY,
                 action_type TEXT, ts REAL, agent TEXT, session_name TEXT,
                 ok INTEGER, notes_md TEXT)""")
    c.commit()
    return c


def _ran(conn, action, ago_s):
    conn.execute("INSERT INTO action_runs (action_type, ts, agent, ok) "
                 "VALUES (?,?,?,1)", (action, time.time() - ago_s, "x"))
    conn.commit()


class TestCeilingsThemselves:
    def test_every_ceiling_exceeds_its_floor(self):
        """A ceiling at or below the floor would delete the judgement the
        floor exists to permit."""
        for action, ceiling in STALENESS_CEILINGS.items():
            assert ceiling > STALENESS_FLOORS[action], action

    def test_refresh_order_is_the_dependency_order(self):
        assert SC.REFRESH_ORDER == ("perception", "regime", "predict")


class TestBreachDetection:
    def test_within_ceiling_is_not_breached(self, conn):
        _ran(conn, "perception", 5 * 3600)      # past 4h floor, under 8h ceiling
        assert SC.breached(conn) == []

    def test_past_ceiling_is_breached(self, conn):
        _ran(conn, "perception", 11.4 * 3600)   # the actual 2026-07-26 age
        out = SC.breached(conn)
        assert [b["action"] for b in out] == ["perception"]

    def test_never_run_is_a_cold_start(self, conn):
        assert SC.breached(conn) == []

    def test_breaches_come_back_in_dependency_order(self, conn):
        _ran(conn, "predict", 30 * 3600)
        _ran(conn, "perception", 30 * 3600)
        _ran(conn, "regime", 30 * 3600)
        assert [b["action"] for b in SC.breached(conn)] == [
            "perception", "regime", "predict"]

    def test_a_recent_attempt_suppresses_a_retry(self, conn):
        """A perception run takes minutes; the ticker fires every 60s. Without
        this the desk would spawn a stampede."""
        _ran(conn, "perception", 30 * 3600)
        conn.execute("INSERT INTO action_runs (action_type, ts, agent, ok, notes_md) "
                     "VALUES (?,?,?,1,?)",
                     (SC._ATTEMPT_ACTION, time.time() - 60, "staleness-ceiling",
                      "perception"))
        conn.commit()
        assert SC.breached(conn) == []

    def test_a_stale_attempt_does_not_suppress_forever(self, conn):
        _ran(conn, "perception", 30 * 3600)
        conn.execute("INSERT INTO action_runs (action_type, ts, agent, ok, notes_md) "
                     "VALUES (?,?,?,1,?)",
                     (SC._ATTEMPT_ACTION, time.time() - SC.ATTEMPT_INTERVAL_S - 60,
                      "staleness-ceiling", "perception"))
        conn.commit()
        assert [b["action"] for b in SC.breached(conn)] == ["perception"]


class TestEnforcement:
    def test_no_breach_does_nothing(self, conn, monkeypatch):
        spawned = []
        monkeypatch.setattr("harness.spawn.spawn_agent",
                            lambda *a, **k: spawned.append(a) or {"ok": True})
        _ran(conn, "perception", 60)
        out = SC.enforce_once(conn)
        assert out["acted"] is False and spawned == []

    def test_refreshes_the_most_upstream_breach_only(self, conn, monkeypatch):
        """One per pass. Refreshing predict against stale perception produces
        a run that silently does nothing — predict's freshness gate skips
        every strategy whose data points are stale."""
        spawned = []
        monkeypatch.setattr(
            "harness.spawn.spawn_agent",
            lambda agent, task, **k: spawned.append(agent) or {"ok": True})
        _ran(conn, "perception", 30 * 3600)
        _ran(conn, "predict", 30 * 3600)
        out = SC.enforce_once(conn)
        assert spawned == ["plutus-perception"]
        assert out["action"] == "perception" and out["deferred"] == ["predict"]

    def test_attempt_is_recorded_before_the_spawn(self, conn, monkeypatch):
        """A crash mid-refresh must not produce a spawn storm on restart."""
        seen = {}

        def _spawn(agent, task, **k):
            seen["attempts_at_spawn_time"] = conn.execute(
                "SELECT COUNT(*) FROM action_runs WHERE action_type=?",
                (SC._ATTEMPT_ACTION,)).fetchone()[0]
            return {"ok": True}

        monkeypatch.setattr("harness.spawn.spawn_agent", _spawn)
        _ran(conn, "perception", 30 * 3600)
        SC.enforce_once(conn)
        assert seen["attempts_at_spawn_time"] == 1

    def test_spawn_failure_is_reported_not_raised(self, conn, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("provider down")
        monkeypatch.setattr("harness.spawn.spawn_agent", _boom)
        _ran(conn, "perception", 30 * 3600)
        out = SC.enforce_once(conn)
        assert out["acted"] is True and out["ok"] is False
        assert "provider down" in out["error"]
