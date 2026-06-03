"""Cron seed helpers for the Plutus heartbeat + weekly review.

Two-line entry points the operator runs once per fresh install:
    plutus-agent cron seed-heartbeat
    plutus-agent cron seed-weekly-review

Both are idempotent — re-running with the same name simply replaces
the existing job with the new schedule (current behavior of the cron
job store: name collisions overwrite).
"""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional


HEARTBEAT_PROMPT = (
    "Heartbeat tick. Load the trading/heartbeat skill and route to the "
    "appropriate phase skill (watchlist-scan / deep-research / "
    "position-monitor / reconcile-and-reflect). Examine WORLDVIEW.md and "
    "lifecycle.db before deciding."
)

WEEKLY_REVIEW_PROMPT = (
    "Weekly review. Load the trading/weekly-review skill, run the lifecycle "
    "queries, write a structured weekly_review reflection, surface the "
    "summary to the operator, and pass control to strategy-curator if "
    "any strategies need pause/retire actions."
)


def seed_heartbeat(
    schedule: str = "0 * * * *",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Install (or replace) the hourly Plutus heartbeat cron job."""
    from cron.jobs import create_job, list_jobs, remove_job

    # Idempotent replace — remove any existing job with the same name first.
    for job in list_jobs():
        if job.get("name") == "plutus-heartbeat":
            remove_job(job["id"])

    return create_job(
        prompt=HEARTBEAT_PROMPT,
        schedule=schedule,
        name="plutus-heartbeat",
        skill="trading/heartbeat",
        enabled_toolsets=["plutus-agent-cli"],
        repeat=None,
        model=model,
    )


def seed_weekly_review(
    schedule: str = "0 18 * * 0",   # Sunday 18:00 UTC
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Install (or replace) the Sunday 18:00 UTC weekly review cron job."""
    from cron.jobs import create_job, list_jobs, remove_job

    for job in list_jobs():
        if job.get("name") == "plutus-weekly-review":
            remove_job(job["id"])

    return create_job(
        prompt=WEEKLY_REVIEW_PROMPT,
        schedule=schedule,
        name="plutus-weekly-review",
        skill="trading/weekly-review",
        enabled_toolsets=["plutus-agent-cli"],
        repeat=None,
        model=model,
    )


def cmd_seed_heartbeat(args) -> int:
    """CLI handler — `plutus-agent cron seed-heartbeat [--schedule X]`."""
    schedule = getattr(args, "schedule", None) or "0 * * * *"
    model = getattr(args, "model", None) or None
    job = seed_heartbeat(schedule=schedule, model=model)
    _print_seeded(job)
    return 0


def cmd_seed_weekly_review(args) -> int:
    """CLI handler — `plutus-agent cron seed-weekly-review [--schedule X]`."""
    schedule = getattr(args, "schedule", None) or "0 18 * * 0"
    model = getattr(args, "model", None) or None
    job = seed_weekly_review(schedule=schedule, model=model)
    _print_seeded(job)
    return 0


def _print_seeded(job: Dict[str, Any]) -> None:
    sys.stdout.write(
        f"Installed cron job '{job.get('name')}' (id={job.get('id')}).\n"
        f"  schedule:        {job.get('schedule')}\n"
        f"  skill:           {job.get('skill')}\n"
        f"  enabled_toolsets: {job.get('enabled_toolsets')}\n"
        f"  next run (utc):  {job.get('next_run')}\n"
    )
