"""Cron seed helpers for the desk (rebuild R4).

One idempotent entry point the wizard (and operator) runs on a fresh install:

    plutus-agent cron seed-desk

Installs:
- ``plutus-ops-tick`` — every 30 minutes, a desk-agent job that spawns
  agents/plutus-ops/AGENT.md directly (resolution + watchdog; silent).
- ``plutus-eod`` — 23:55 daily, a synthetic message into plutus-main's
  session asking for the journal close (record(kind=eod)).

plutus-main needs no seed: it IS the operator's persistent session, woken by
the wake queue, the operator, and its own self-scheduled crons.
"""

from __future__ import annotations

from typing import Any, Dict

OPS_TICK_PROMPT = (
    "30-minute ops tick. Run your Procedure: resolve due predictions, "
    "evaluate the open position (if any), check staleness floors, enqueue "
    "wakes where needed, return your ops_report."
)

EOD_PROMPT = (
    "[EOD] End of day. Close the journal via record(kind=eod): how the day "
    "went, what changed in the book, what you're watching tomorrow. Keep it "
    "honest and short. The session rolls after this turn."
)


def seed_desk_crons() -> Dict[str, Any]:
    """Install (or replace) the desk's two standing cron jobs."""
    from harness.cron.jobs import create_job, list_jobs, remove_job

    for job in list_jobs():
        if job.get("name") in ("plutus-ops-tick", "plutus-eod"):
            remove_job(job["id"])

    ops = create_job(
        prompt=OPS_TICK_PROMPT,
        schedule="*/30 * * * *",
        name="plutus-ops-tick",
        agent="plutus-ops",
    )
    eod = create_job(
        prompt=EOD_PROMPT,
        schedule="55 23 * * *",
        name="plutus-eod",
    )
    return {"ops": ops, "eod": eod}


def cmd_seed_desk(args) -> int:
    """CLI handler — `plutus-agent cron seed-desk`."""
    jobs = seed_desk_crons()
    for label, job in jobs.items():
        print(f"  ✓ {job['name']}: {job['schedule']['expr']}"
              + (f" (agent: {job['agent']})" if job.get("agent") else ""))
    return 0
