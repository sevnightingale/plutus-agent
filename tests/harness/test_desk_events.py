"""The event engine — due predicates, one-spawn-per-pass, cooldowns from
attempt rows, and payload routing. The DB is the event log: these tests
drive due-ness by writing the same rows the live desk writes."""

from __future__ import annotations

import time

import pytest

from harness import desk_events, wake_queue
from trading.lifecycle import write
from trading.lifecycle.db import get_db


@pytest.fixture(autouse=True)
def fresh_engine(monkeypatch):
    monkeypatch.setattr(desk_events, "_last_pass", 0.0)
    yield


def _seed_prediction(conn, *, resolved_at=None):
    conn.execute(
        """INSERT INTO predictions
             (ts, horizon_ts, timescale, symbol, claim_md,
              success_criteria_json, conviction, strategy_name, kind,
              resolved_at, outcome)
           VALUES (?, ?, 'swing', 'BTC', 'test claim', '{}', 0.6,
                   'book-a', 'strategy', ?, ?)""",
        (time.time() - 3600, time.time() + 3600, resolved_at,
         "correct" if resolved_at else None))
    conn.commit()


def _seed_close(conn, closed_at):
    # The predicate reads only status + closed_at; skip the four-table
    # thesis→decision→trade chain a real close writes.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """INSERT INTO positions
             (venue, symbol, side, size, opening_trade_id, status,
              opened_at, closed_at)
           VALUES ('hyperliquid', 'BTC', 'long', 0.01, 1, 'closed', ?, ?)""",
        (closed_at - 3600, closed_at))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


class TestPredictDue:
    def test_cold_start_is_due(self):
        due, reason = desk_events.predict_due(get_db())
        assert due and "cold start" in reason

    def test_quiet_and_fresh_is_not_due(self):
        conn = get_db()
        write.record_action_run(conn, action_type="predict", agent="t", ok=True)
        due, _ = desk_events.predict_due(conn)
        assert not due

    def test_a_resolution_wakes_it(self):
        conn = get_db()
        write.record_action_run(conn, action_type="predict", agent="t", ok=True)
        _seed_prediction(conn, resolved_at=time.time())
        due, reason = desk_events.predict_due(conn)
        assert due and "resolved since" in reason

    def test_floor_backstop_fires_and_says_so(self):
        conn = get_db()
        write.record_action_run(conn, action_type="predict", agent="t",
                                ok=True,
                                ts=time.time() - desk_events.PREDICT_FLOOR_S - 60)
        due, reason = desk_events.predict_due(conn)
        assert due and "floor backstop" in reason

    def test_failed_runs_do_not_satisfy_the_floor(self):
        conn = get_db()
        write.record_action_run(conn, action_type="predict", agent="t",
                                ok=False)
        due, reason = desk_events.predict_due(conn)
        assert due  # cold start still — ok=0 rows are history, not satisfaction


class TestGenerateReflectDue:
    def test_lit_undercapacity_cell_wakes_generate(self, monkeypatch):
        import trading.lifecycle.queries as queries
        monkeypatch.setattr(queries, "cell_capacity", lambda conn: [
            {"cell": "BTC/swing/ranging/normal", "lit": True,
             "slots_remaining": 3},
            {"cell": "ETH/swing/ranging/normal", "lit": False,
             "slots_remaining": 7}])
        conn = get_db()
        write.record_action_run(conn, action_type="generation", agent="t",
                                ok=True)
        due, reason = desk_events.generate_due(conn)
        assert due and "BTC/swing/ranging/normal" in reason
        assert "ETH" not in reason  # unlit cells never wake authoring

    def test_full_cells_fall_back_to_the_7d_floor(self, monkeypatch):
        import trading.lifecycle.queries as queries
        monkeypatch.setattr(queries, "cell_capacity", lambda conn: [
            {"cell": "BTC/swing/ranging/normal", "lit": True,
             "slots_remaining": 0}])
        conn = get_db()
        write.record_action_run(conn, action_type="generation", agent="t",
                                ok=True)
        assert desk_events.generate_due(conn)[0] is False
        write.record_action_run(
            conn, action_type="generation", agent="t", ok=True,
            ts=time.time() - desk_events.GENERATE_FLOOR_S - 60)
        # the newer row above still satisfies; wipe and re-seed old only
        conn.execute("DELETE FROM action_runs WHERE action_type='generation'")
        write.record_action_run(
            conn, action_type="generation", agent="t", ok=True,
            ts=time.time() - desk_events.GENERATE_FLOOR_S - 60)
        due, reason = desk_events.generate_due(conn)
        assert due and "floor" in reason

    def test_three_closes_wake_reflect(self):
        conn = get_db()
        write.record_action_run(conn, action_type="reflect", agent="t",
                                ok=True, ts=time.time() - 60)
        for i in range(desk_events.REFLECT_CLOSES_N):
            _seed_close(conn, time.time() - i)
        due, reason = desk_events.reflect_due(conn)
        assert due and "unreflected" in reason

    def test_two_closes_do_not(self):
        conn = get_db()
        write.record_action_run(conn, action_type="reflect", agent="t",
                                ok=True, ts=time.time() - 60)
        for i in range(desk_events.REFLECT_CLOSES_N - 1):
            _seed_close(conn, time.time() - i)
        assert desk_events.reflect_due(conn)[0] is False


class TestTick:
    def _capture_spawn(self, monkeypatch, payload=None):
        calls = []

        def fake_spawn(agent, task, *, session_name, **kw):
            calls.append({"agent": agent, "task": task})
            return {"ok": True, "payload": payload or {}, "problems": []}

        import harness.spawn
        monkeypatch.setattr(harness.spawn, "spawn_agent", fake_spawn)
        return calls

    def test_cold_start_spawns_predict_and_records_attempt(self, monkeypatch):
        calls = self._capture_spawn(monkeypatch)
        seat = desk_events.tick(background=False)
        assert seat == "predict"
        assert calls[0]["agent"] == "plutus-predict"
        assert "cold start" in calls[0]["task"]
        row = get_db().execute(
            "SELECT agent FROM action_runs WHERE action_type='event_spawn' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row["agent"] == "predict"

    def test_cooldown_from_attempt_rows_holds(self, monkeypatch):
        self._capture_spawn(monkeypatch)
        assert desk_events.tick(background=False) == "predict"
        monkeypatch.setattr(desk_events, "_last_pass", 0.0)
        # predict is due again (still cold-start shaped: the fake spawn
        # records no predict action_run) but the attempt row's cooldown holds;
        # the pass falls through to the next due seat instead.
        seat = desk_events.tick(background=False)
        assert seat != "predict"

    def test_escalation_findings_route_to_a_wake(self, monkeypatch):
        self._capture_spawn(monkeypatch, payload={
            "predictions": [], "actionable": None,
            "escalation_findings": ["sweep manifest refuses registrations"]})
        desk_events.tick(background=False)
        wakes = wake_queue.drain()
        assert any(w.get("key") == "predict:escalation" for w in wakes)

    def test_situational_read_lands_in_the_narrative_zone(self, monkeypatch):
        from harness.constants import get_hermes_home
        from harness.runtime_templates import PERCEPTION_MD_TEMPLATE

        home = get_hermes_home()
        home.mkdir(parents=True, exist_ok=True)
        board = home / "PERCEPTION.md"
        board.write_text(PERCEPTION_MD_TEMPLATE, encoding="utf-8")
        self._capture_spawn(monkeypatch, payload={
            "predictions": [], "actionable": None,
            "situational_read": "BTC grinding the range lows on thin flow."})
        desk_events.tick(background=False)
        text = board.read_text(encoding="utf-8")
        assert "grinding the range lows" in text
        assert "plutus-predict" in text

    def test_spawn_failure_is_a_keyed_wake(self, monkeypatch):
        def boom(agent, task, *, session_name, **kw):
            raise RuntimeError("provider dark")

        import harness.spawn
        monkeypatch.setattr(harness.spawn, "spawn_agent", boom)
        desk_events.tick(background=False)
        wakes = wake_queue.drain()
        assert any(w.get("key") == "event_spawn:predict_failed"
                   for w in wakes)

    def test_pass_interval_gates(self, monkeypatch):
        self._capture_spawn(monkeypatch)
        desk_events.tick(background=False)
        # _last_pass is now recent — the next call is a gated no-op.
        assert desk_events.tick(background=False) is None
