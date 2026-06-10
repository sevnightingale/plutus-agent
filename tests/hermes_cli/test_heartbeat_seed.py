"""plutus-agent cron seed-heartbeat / seed-weekly-review — write to jobs.json."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def temp_hermes_home(tmp_path, monkeypatch):
    """Redirect HERMES_HOME so the cron job store lives under tmp_path."""
    home = tmp_path / "plutus-agent"
    home.mkdir()
    (home / "cron").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # cron.jobs imports HERMES_DIR at module load time — reload to pick up env
    import importlib
    import harness.cron.jobs; import harness.cron as cron
    importlib.reload(cron.jobs)
    return home


def test_seed_heartbeat_creates_job(temp_hermes_home):
    from harness.cli.heartbeat import seed_heartbeat

    job = seed_heartbeat()

    assert job["name"] == "plutus-heartbeat"
    assert job["schedule"]["expr"] == "0 * * * *"
    assert job["skill"] == "trading/heartbeat"
    assert job["enabled_toolsets"] == ["plutus-agent-cli"]
    assert "Heartbeat tick" in job["prompt"]

    # File-backed
    jobs_file = temp_hermes_home / "cron" / "jobs.json"
    assert jobs_file.exists()
    raw = json.loads(jobs_file.read_text())
    jobs_iter = (
        raw["jobs"].values() if isinstance(raw.get("jobs"), dict) else raw.get("jobs", [])
    )
    assert any(j["name"] == "plutus-heartbeat" for j in jobs_iter)


def test_seed_heartbeat_idempotent(temp_hermes_home):
    """Re-seeding replaces the prior job rather than duplicating."""
    from harness.cli.heartbeat import seed_heartbeat
    import harness.cron.jobs; import harness.cron as cron

    first = seed_heartbeat(schedule="*/30 * * * *")
    second = seed_heartbeat(schedule="0 */2 * * *")

    assert first["id"] != second["id"]
    jobs = [j for j in cron.jobs.list_jobs() if j["name"] == "plutus-heartbeat"]
    assert len(jobs) == 1
    assert jobs[0]["schedule"]["expr"] == "0 */2 * * *"


def test_seed_weekly_review_creates_job(temp_hermes_home):
    from harness.cli.heartbeat import seed_weekly_review

    job = seed_weekly_review()
    assert job["name"] == "plutus-weekly-review"
    assert job["schedule"]["expr"] == "0 18 * * 0"
    assert job["skill"] == "trading/weekly-review"


def test_seed_weekly_review_custom_schedule(temp_hermes_home):
    from harness.cli.heartbeat import seed_weekly_review

    job = seed_weekly_review(schedule="0 12 * * 1")  # Monday noon
    assert job["schedule"]["expr"] == "0 12 * * 1"


def test_both_helpers_coexist(temp_hermes_home):
    from harness.cli.heartbeat import seed_heartbeat, seed_weekly_review
    import harness.cron.jobs; import harness.cron as cron

    seed_heartbeat()
    seed_weekly_review()

    names = {j["name"] for j in cron.jobs.list_jobs()}
    assert "plutus-heartbeat" in names
    assert "plutus-weekly-review" in names
