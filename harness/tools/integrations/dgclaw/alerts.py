"""dgclaw alerts — leaderboard rank change + perp_deposit completion."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from harness.tools.core.alert_registry import register_alert

from . import _cli

logger = logging.getLogger(__name__)


@register_alert(
    name="dgclaw_leaderboard_rank_change",
    source="dgclaw",
    throttle_seconds=3600,
    description="Fires when Plutus's dgclaw leaderboard rank changes.",
)
def poll_dgclaw_rank_change(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not _cli.is_installed():
        return [], state or {}
    try:
        board = _cli.dgclaw("leaderboard")
    except Exception as exc:
        logger.debug("dgclaw_leaderboard_rank_change poll failed: %s", exc)
        return [], state or {}

    addr = os.getenv("HL_PUBLIC_ADDRESS") or os.getenv("HL_API_WALLET_ADDRESS")
    if not addr:
        return [], state or {}

    standings = board.get("standings") or board.get("leaderboard") or []
    my_rank: Optional[int] = None
    for entry in standings:
        if entry.get("address", "").lower() == addr.lower():
            my_rank = entry.get("rank")
            break

    prev_rank = (state or {}).get("rank")
    fired: List[Dict[str, Any]] = []
    if my_rank is not None and prev_rank is not None and my_rank != prev_rank:
        fired.append({
            "alert": "dgclaw_leaderboard_rank_change",
            "previous_rank": prev_rank,
            "current_rank": my_rank,
        })

    return fired, {"rank": my_rank}


@register_alert(
    name="dgclaw_perp_deposit_completed",
    source="dgclaw",
    throttle_seconds=600,
    description=(
        "Fires when an in-progress ACP perp_deposit job (pending bridge to "
        "HL spot) completes. State holds the watched job_id."
    ),
)
def poll_perp_deposit_completed(
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    state = state or {}
    job_id = state.get("watched_job_id")
    if not job_id:
        # Nothing to watch — Plutus calls dgclaw_perp_deposit_via_acp to set this.
        return [], state

    try:
        from harness.tools.integrations.acp import _cli as acp_cli
        if not acp_cli.is_installed():
            return [], state
        # `acp job list` doesn't accept --chain-id (REST aggregator).
        # --all returns both v1 and v2 jobs so we don't miss legacy ones.
        jobs = acp_cli.acp("job", "list", "--all")
    except Exception as exc:
        logger.debug("perp_deposit_completed poll failed: %s", exc)
        return [], state

    completed = False
    for j in (jobs.get("jobs") if isinstance(jobs, dict) else jobs) or []:
        if str(j.get("id") or j.get("job_id")) == str(job_id):
            if j.get("status") in ("completed", "fulfilled", "delivered"):
                completed = True
            break

    if completed:
        return (
            [{
                "alert": "dgclaw_perp_deposit_completed",
                "job_id": job_id,
            }],
            {},  # clear watched_job_id once fired
        )
    return [], state
