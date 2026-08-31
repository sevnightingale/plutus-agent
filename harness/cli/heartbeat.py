"""Cron seed helpers for the desk.

One idempotent entry point the wizard (and operator) runs on a fresh install:

    plutus-agent cron seed-desk

Installs:
- ``plutus-eod`` — 23:55 daily, a synthetic message into plutus-main's
  session asking for the journal close (record(kind=eod)). The one standing
  beat that is genuinely LLM work, and the cheapest: one turn a day in a
  session that already exists.

``plutus-ops-tick`` is gone (sustainable-desk rebuild, 2026-08-31): the ops
seat is now code — ``trading.lifecycle.ops_tick`` — hosted by the watchers
daemon on its own 30-minute gate. Seeding removes a lingering ops job from
older installs, so running seed-desk brings the runtime to the current
shape.

plutus-main needs no seed: it IS the operator's persistent session, woken by
the wake queue, the operator, and its own self-scheduled crons.
"""

from __future__ import annotations

from typing import Any, Dict

EOD_PROMPT = (
    "[EOD] End of day. Close the journal via record(kind=eod): how the day "
    "went, what changed in the book, what you're watching tomorrow. Keep it "
    "honest and short. The session rolls after this turn."
)


def seed_desk_crons() -> Dict[str, Any]:
    """Install (or replace) the desk's standing cron jobs."""
    from harness.cron.jobs import create_job, list_jobs, remove_job

    for job in list_jobs():
        if job.get("name") in ("plutus-ops-tick", "plutus-eod"):
            remove_job(job["id"])

    eod = create_job(
        prompt=EOD_PROMPT,
        schedule="55 23 * * *",
        name="plutus-eod",
    )
    return {"eod": eod}


def cmd_seed_desk(args) -> int:
    """CLI handler — `plutus-agent cron seed-desk`."""
    jobs = seed_desk_crons()
    for label, job in jobs.items():
        print(f"  ✓ {job['name']}: {job['schedule']['expr']}"
              + (f" (agent: {job['agent']})" if job.get("agent") else ""))
    return 0
