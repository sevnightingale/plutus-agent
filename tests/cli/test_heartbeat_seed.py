"""plutus-agent cron seed-desk — the desk's standing jobs (rebuild R4)."""

from __future__ import annotations

import pytest


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    """Redirect HERMES_HOME so the cron job store lives under tmp_path."""
    home = tmp_path / "plutus-agent"
    home.mkdir()
    (home / "cron").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # cron.jobs captures HERMES_DIR at module load time — reload to pick up
    # the env, then reload AGAIN after the env is restored. Without the
    # second reload the tmp path stays baked into the module (reload mutates
    # the shared module dict in place) and poisons every later cron-touching
    # test in this worker.
    import importlib
    import harness.cron.jobs; import harness.cron as cron
    importlib.reload(cron.jobs)
    yield home
    monkeypatch.undo()
    importlib.reload(cron.jobs)


def test_seed_desk_creates_both_jobs(temp_hermes_home):
    from harness.cli.heartbeat import seed_desk_crons

    jobs = seed_desk_crons()

    ops = jobs["ops"]
    assert ops["name"] == "plutus-ops-tick"
    assert ops["schedule"]["expr"] == "*/30 * * * *"
    assert ops["agent"] == "plutus-ops"

    eod = jobs["eod"]
    assert eod["name"] == "plutus-eod"
    assert eod["schedule"]["expr"] == "55 23 * * *"
    assert eod.get("agent") is None  # synthetic injection into main, not a spawn
    assert "record(kind=eod)" in eod["prompt"]


def test_seed_desk_idempotent(temp_hermes_home):
    from harness.cli.heartbeat import seed_desk_crons
    from harness.cron.jobs import list_jobs

    seed_desk_crons()
    seed_desk_crons()
    names = [j["name"] for j in list_jobs()]
    assert names.count("plutus-ops-tick") == 1
    assert names.count("plutus-eod") == 1


def test_desk_agent_job_routes_to_spawn(temp_hermes_home, monkeypatch):
    """run_job dispatches agent-jobs straight to harness.spawn.spawn_agent."""
    from harness.cli.heartbeat import seed_desk_crons
    from harness.cron import scheduler

    jobs = seed_desk_crons()
    calls = {}

    def fake_spawn(name, task, *, session_name, **kw):
        calls["name"] = name
        calls["task"] = task
        return {"ok": True, "payload": {"resolved": []}, "problems": [],
                "duration_s": 0.1, "transcript": "/tmp/t.md", "raw": "{}"}

    import harness.spawn
    monkeypatch.setattr(harness.spawn, "spawn_agent", fake_spawn)

    ok, doc, final, err = scheduler.run_job(jobs["ops"])
    assert ok
    assert calls["name"] == "plutus-ops"
    assert "ops tick" in calls["task"]
    assert final == scheduler.SILENT_MARKER
    assert err is None
