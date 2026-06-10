"""Escalation channel — `~/.plutus-agent/escalation.flag` + self-scheduled wake.

V2 escalation contract (per architecture-v2 §10 + operator's correction:
operator gates NOTHING). When plutus-ops detects an urgent state during
its 30-min tick (near-liquidation, equity drop >10%, total drift, etc.),
it does NOT notify the operator. Instead it:

1. Writes the escalation sentinel file with details.
2. Schedules a one-shot kimi-k2.6 cron firing in ~60s — this becomes
   plutus-main's wake-up call. The cron prompt reads the flag, takes
   urgent action, clears the flag, records a reflection.

The self-scheduled wake gives plutus-main a ~30 min head start over its
next regular beat (0/7/14/21 UTC), but doesn't bother the operator. If
plutus-main happens to be running already (e.g., the regular beat is in
progress), the flag will be picked up at Phase 0 of that beat anyway.

The sentinel file is JSON:

    {
      "set_at": <unix_ts>,
      "set_by_tier": "ops" | "thesis_monitor" | "watcher",
      "set_by_session_id": "<session id>",
      "reason": "near_liquidation" | "equity_drop_10pct" | ...,
      "details_md": "<markdown description for plutus-main to read>",
      "trigger_observation_id": <int | null>
    }
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


# Approved escalation reasons (hard list per architecture-v2 §10). plutus-ops
# is doctrine-bound to escalate ONLY for these — everything else waits for
# the next scheduled plutus-main beat.
APPROVED_REASONS = frozenset({
    "near_liquidation",         # liquidation price within 1.5× ATR of current
    "equity_drop_10pct",        # >10% loss in a single tick
    "sl_approaching_low_conv",  # SL within 2× ATR + conviction < 0.4
    "total_drift",              # lifecycle.db says open, venue says closed (or vice versa)
    "watcher_catastrophic",     # >20% balance change in 1h, or HL liquidation event
})


WAKE_PROMPT_TEMPLATE = """[ESCALATION — self-scheduled wake]

An escalation flag is set at ~/.plutus-agent/escalation.flag. Read the
file contents to see what triggered this wake-up.

Steps:
1. Read the flag JSON. Note `reason`, `details_md`, `set_by_tier`,
   `set_by_session_id`, and `trigger_observation_id` if present.
2. If `trigger_observation_id` is set, query that observation for the
   underlying context.
3. Take the urgent action the situation demands (close position, modify
   SL, reduce size, place hedging trade, etc.). Use full agency — this
   is exactly what plutus-main is for.
4. Atomic-delete the flag file via terminal (`rm ~/.plutus-agent/escalation.flag`).
5. Record a reflection with `reflection_kind="escalation_response"` covering
   the position(s) involved (`position_ids_json`), the action taken, and
   the `error_class` (regime / forecast / execution / variance / etc.).

Do NOT notify the operator unless the situation genuinely requires their
input (e.g., funding required to add margin). The escalation pattern is
specifically designed to be operator-silent — Plutus handles its own
emergencies.
"""


def _hermes_home() -> Path:
    from harness.constants import get_hermes_home
    return Path(get_hermes_home())


def _flag_path() -> Path:
    return _hermes_home() / "escalation.flag"


def read_escalation_flag() -> Optional[Dict[str, Any]]:
    """Return the flag contents, or None if no flag is set / parse fails."""
    path = _flag_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def write_escalation_flag(
    *,
    reason: str,
    details_md: str,
    set_by_tier: str,
    set_by_session_id: Optional[str] = None,
    trigger_observation_id: Optional[int] = None,
) -> None:
    """Write (or overwrite) the escalation sentinel atomically.

    Args:
        reason: One of APPROVED_REASONS — plutus-ops doctrine.
        details_md: Markdown description for plutus-main to read.
        set_by_tier: "ops" | "thesis_monitor" | "watcher".
        set_by_session_id: The cron tick's session id (provenance).
        trigger_observation_id: lifecycle observation that flagged this
            condition (so plutus-main can query the full data point context).

    Raises:
        ValueError if reason is not in APPROVED_REASONS.
    """
    if reason not in APPROVED_REASONS:
        raise ValueError(
            f"reason {reason!r} not in approved list. Approved: {sorted(APPROVED_REASONS)}"
        )
    data = {
        "set_at": time.time(),
        "set_by_tier": set_by_tier,
        "set_by_session_id": set_by_session_id or "",
        "reason": reason,
        "details_md": details_md,
        "trigger_observation_id": trigger_observation_id,
    }
    _atomic_write_json(_flag_path(), data)


def clear_escalation_flag() -> bool:
    """Delete the sentinel file. Returns True if a flag existed, False otherwise.

    Used by plutus-main after handling the escalation. No-op on missing file.
    """
    path = _flag_path()
    if path.exists():
        path.unlink()
        return True
    return False


def schedule_escalation_wake(
    *,
    delay: str = "1m",
    model: str = "kimi-k2.6",
    provider: str = "opencode-go",
) -> Optional[str]:
    """Schedule a one-shot cron that fires kimi-k2.6 in `delay` to read the flag.

    The cron lands in the LEGACY fresh-session path (because model override
    is set — see cron/scheduler.py Path 1 condition added in A.2). That gives
    plutus-main an isolated kimi-k2.6 session with no carry-over from the
    operator chat, which is appropriate for surgical emergency response.

    Args:
        delay: Schedule string (e.g., "1m", "30s", "2m"). Default 1m.
        model: Model to bind to the cron's fresh session. Default kimi-k2.6
            (the brain model — escalation handling needs full reasoning).
        provider: Provider name. Default opencode-go.

    Returns:
        The created job's id, or None on failure (logged via cron module).
    """
    # Lazy import — cron module pulls a lot.
    from harness.cron.jobs import create_job
    try:
        job = create_job(
            prompt=WAKE_PROMPT_TEMPLATE,
            schedule=delay,
            name="plutus-escalation-wake",
            repeat=1,
            deliver="local",
            origin=None,           # No origin → no synthetic injection; goes to legacy path.
            skills=None,
            model=model,
            provider=provider,
            base_url=None,
            script=None,
            enabled_toolsets=None,
        )
        return job["id"]
    except Exception:
        # Cron-scheduling failure must not crash the ops tick; the flag is
        # still set on disk and plutus-main's next regular beat will see it
        # at Phase 0 handshake.
        return None


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
