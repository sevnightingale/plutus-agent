"""The deterministic back office — verdict→wake mappings, step isolation,
and the tick's own record. Each mapping is driven to fire (and not fire) so
the code carries what used to be recipe prose."""

from __future__ import annotations

import json

import pytest

from harness import wake_queue
from trading.lifecycle import ops_tick as mod
from trading.lifecycle.db import get_db


def _tool_json(d):
    return json.dumps(d)


@pytest.fixture
def quiet_steps(monkeypatch):
    """Neutral fixtures for every heavy step; tests override what they probe."""
    import trading.dispatchers.perception_sweep as sweep
    import trading.dispatchers.resolution as resolution
    import trading.lifecycle.capital as capital
    import trading.lifecycle.hygiene as hygiene
    import trading.lifecycle.integrity as integrity
    import trading.lifecycle.live_state as live_state
    import trading.regime.classifier as regime_classifier
    import trading.integrations.deepseek.data_points as ds

    monkeypatch.setattr(sweep, "_sweep", lambda a: _tool_json(
        {"fetched": 10, "failed_total": 0, "symbols": {}}))
    monkeypatch.setattr(sweep, "_render", lambda a: _tool_json(
        {"rows": 10, "replaced": True}))
    monkeypatch.setattr(regime_classifier, "run", lambda conn: (
        {"written": 3, "flips": [], "skipped": {}, "board_ok": True}))
    monkeypatch.setattr(resolution, "_resolve_due", lambda a: _tool_json(
        {"resolved": [], "marked_near": [], "unresolvable_invalidations": [],
         "open_count": 0}))
    monkeypatch.setattr(resolution, "_rescore_open", lambda a: _tool_json(
        {"rescored": [], "failures": []}))
    monkeypatch.setattr(live_state, "write_live_state",
                        lambda conn, path=None: {"ok": True})
    monkeypatch.setattr(capital, "reconcile_capital_movements",
                        lambda conn: {"ok": True, "inserted": 0,
                                      "net_deposits_usd": 0.0})
    monkeypatch.setattr(hygiene, "sweep",
                        lambda conn, **kw: {"ran": False})
    monkeypatch.setattr(integrity, "check_integrity",
                        lambda conn, home=None: {"ok": True, "violations": [],
                                                 "checks_run": 19,
                                                 "checks_failed": []})
    monkeypatch.setattr(ds, "_configured_provider", lambda: "deepseek")

    verdicts = {
        "hl_trade_readiness": {"ready": True, "warn_expiring_soon": False,
                               "days_remaining": 90, "reason": "ok"},
        "acp_auth_readiness": {"alive": True, "critical": False,
                               "warn_reauth_soon": False, "reason": "ok"},
        "deepseek_balance": {"fetch_failed": False, "low": False,
                             "critical": False, "balance_usd": 20.0,
                             "reason": "ok"},
    }
    monkeypatch.setattr(mod, "_fetch_dp", lambda name: dict(verdicts[name]))
    return verdicts


def _wake_keys():
    return [w.get("key") for w in wake_queue.drain()]


def test_clean_tick_records_itself(quiet_steps):
    conn = get_db()
    res = mod.ops_tick(conn)
    assert res["ok"] is True
    assert res["failed_steps"] == []
    row = conn.execute(
        "SELECT ok, notes_md FROM action_runs WHERE action_type='ops' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and row["ok"] == 1
    notes = json.loads(row["notes_md"])  # always parseable JSON
    assert notes["position"] == {"open": False}
    # Fresh db: the seat floors are overdue — the watchdog says so. (The
    # perception floor is NOT here: the tick's own sweep step just
    # satisfied it, which is the designed behavior.)
    assert "staleness:reflect" in notes["wakes"]
    assert "staleness:perception" not in notes["wakes"]


def test_verdicts_map_to_the_recipe_wake_keys(quiet_steps):
    quiet_steps["hl_trade_readiness"] = {
        "ready": False, "warn_expiring_soon": False, "days_remaining": None,
        "reason": "registration EXPIRED"}
    quiet_steps["deepseek_balance"] = {
        "fetch_failed": False, "low": True, "critical": True,
        "balance_usd": 1.2, "reason": "CRITICAL"}
    conn = get_db()
    mod.ops_tick(conn)
    keys = _wake_keys()
    assert "trade_path:readiness" in keys
    assert "provider:balance_critical" in keys
    assert "provider:balance_low" not in keys  # critical outranks low


def test_expiry_warning_wakes_without_blocking(quiet_steps):
    quiet_steps["hl_trade_readiness"]["warn_expiring_soon"] = True
    quiet_steps["hl_trade_readiness"]["days_remaining"] = 5
    conn = get_db()
    res = mod.ops_tick(conn)
    assert res["ok"] is True
    assert "trade_path:readiness" in _wake_keys()


def test_integrity_violations_fan_out(quiet_steps, monkeypatch):
    import trading.lifecycle.integrity as integrity
    monkeypatch.setattr(
        integrity, "check_integrity",
        lambda conn, home=None: {
            "ok": False,
            "violations": [{"check": "position_stop_missing",
                            "severity": "critical", "detail": "naked"}],
            "checks_run": 19, "checks_failed": ["watcher_fds"]})
    conn = get_db()
    mod.ops_tick(conn)
    keys = _wake_keys()
    assert "integrity:position_stop_missing" in keys
    assert "integrity:checker_failed" in keys


def test_a_failing_step_never_stops_the_tick(quiet_steps, monkeypatch):
    import trading.dispatchers.resolution as resolution

    def boom(a):
        raise RuntimeError("venue down")

    monkeypatch.setattr(resolution, "_resolve_due", boom)
    conn = get_db()
    res = mod.ops_tick(conn)
    assert res["ok"] is False
    assert res["failed_steps"] == ["resolve"]
    # Later steps still ran and the tick still recorded itself, ok=0.
    assert res["notes"]["integrity"]["ok"] is True
    row = conn.execute(
        "SELECT ok, notes_md FROM action_runs WHERE action_type='ops' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row["ok"] == 0
    assert json.loads(row["notes_md"])["resolve"]["ok"] is False


def test_unreadable_invalidations_are_loud(quiet_steps, monkeypatch):
    import trading.dispatchers.resolution as resolution
    monkeypatch.setattr(resolution, "_resolve_due", lambda a: _tool_json(
        {"resolved": [], "marked_near": [],
         "unresolvable_invalidations": [{"prediction_id": 1147}],
         "open_count": 3}))
    conn = get_db()
    mod.ops_tick(conn)
    assert "resolution:unreadable" in _wake_keys()


def test_maybe_run_gates_on_interval(quiet_steps, monkeypatch):
    monkeypatch.setattr(mod, "_last_started", 0.0)
    ran = mod.maybe_run_ops_tick(background=False)
    assert ran is True
    # Immediately after, the interval gate holds.
    assert mod.maybe_run_ops_tick(background=False) is False
