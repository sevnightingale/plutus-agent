"""Staleness ceilings — the refresh that is not main's to decline.

The floors are judgement and should stay that way. A $74 range over seven
hours genuinely does not need six perception runs, a fixed floor is wrong in
both directions (too slack on FOMC day, too tight on a dead weekend), and
main is the only agent holding the context to tell those apart.

But a floor that can be declined indefinitely is not a floor. On 2026-07-26
main believed it was Saturday, declined perception thirteen consecutive times
over eleven hours, and scheduled its next refresh for a day that had already
passed. Nothing in the system could contradict it, because staleness
enforcement was a prose instruction to a model that had made up its mind.

So: between floor and ceiling, main may defer with a reason. Past the ceiling
this module refreshes and does not ask. It is deterministic — no model, no
judgement, one SQL query on a 60-second tick and almost always a no-op.

Ordering is real. perception feeds regime feeds predict, and predict's
freshness gate skips any strategy whose declared data points are stale, so
refreshing predict against stale perception would produce a run that silently
does nothing. Breached actions are therefore refreshed in dependency order,
one per pass — the next tick picks up the next one, by which time the
upstream refresh has landed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# perception feeds regime feeds predict. Refresh in this order, never in
# parallel and never downstream-first.
REFRESH_ORDER = ("perception", "regime", "predict")

# An attempt is recorded BEFORE the spawn, so a crash mid-refresh cannot
# produce a spawn storm on restart. Comfortably longer than a perception run.
ATTEMPT_INTERVAL_S = 30 * 60

_ATTEMPT_ACTION = "ceiling_spawn"

_in_flight = threading.Lock()


def _last_attempts(conn) -> Dict[str, float]:
    rows = conn.execute(
        "SELECT notes_md, MAX(ts) FROM action_runs "
        "WHERE action_type = ? GROUP BY notes_md", (_ATTEMPT_ACTION,)
    ).fetchall()
    return {r[0]: r[1] for r in rows if r[0]}


def breached(conn, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Which actions are past their ceiling and not recently attempted."""
    from trading.dispatchers.wake import STALENESS_CEILINGS
    from trading.lifecycle import queries

    now = now if now is not None else time.time()
    last = queries.last_action_runs(conn)
    attempts = _last_attempts(conn)

    out = []
    for action in REFRESH_ORDER:
        ceiling = STALENESS_CEILINGS.get(action)
        if ceiling is None:
            continue
        ts = last.get(action)
        if ts is None:
            # Never run at all is a cold start, not a breach — the desk's
            # normal boot path brings these up and a forced spawn into an
            # unconfigured runtime helps nobody.
            continue
        age = now - ts
        if age <= ceiling:
            continue
        since_attempt = now - attempts.get(action, 0.0)
        if since_attempt < ATTEMPT_INTERVAL_S:
            continue          # one already in flight or just tried
        out.append({"action": action, "age_s": age, "ceiling_s": ceiling})
    return out


def enforce_once(conn=None) -> Dict[str, Any]:
    """Refresh the most upstream breached action. Returns what it did."""
    from harness.spawn import spawn_agent
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db

    conn = conn if conn is not None else get_db()
    todo = breached(conn)
    if not todo:
        return {"acted": False, "breached": []}

    first = todo[0]
    action = first["action"]
    agent = f"plutus-{action}"

    # Record the attempt BEFORE spawning: if the process dies mid-refresh the
    # restart sees a recent attempt and waits rather than storming.
    write.record_action_run(
        conn, action_type=_ATTEMPT_ACTION, agent="staleness-ceiling",
        ok=True, notes_md=action)

    logger.warning(
        "staleness ceiling breached: %s is %.1fh old (ceiling %.0fh) — "
        "refreshing deterministically",
        action, first["age_s"] / 3600, first["ceiling_s"] / 3600)

    task = (
        f"CEILING REFRESH. {action} last ran {first['age_s'] / 3600:.1f}h ago, "
        f"past its {first['ceiling_s'] / 3600:.0f}h ceiling. This refresh was "
        f"not requested by plutus-main and is not optional — run your normal "
        f"procedure in full and return your contract."
    )
    try:
        result = spawn_agent(agent, task, session_name="staleness-ceiling")
    except Exception as exc:
        logger.exception("ceiling refresh of %s failed", agent)
        return {"acted": True, "action": action, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}

    return {"acted": True, "action": action, "ok": bool(result.get("ok")),
            "problems": result.get("problems") or [],
            "deferred": [t["action"] for t in todo[1:]]}


def tick(background: bool = True) -> None:
    """Called from the gateway's cron ticker. Cheap and usually a no-op.

    The refresh itself runs on its own thread — a perception run takes
    minutes and the ticker also drains the wake queue, which must not stall
    behind it. The lock keeps exactly one refresh in flight; the recorded
    attempt keeps it that way across restarts.
    """
    def _run() -> None:
        if not _in_flight.acquire(blocking=False):
            return
        try:
            from trading.lifecycle.db import get_db
            enforce_once(get_db())
        except Exception:
            logger.exception("staleness ceiling tick failed")
        finally:
            _in_flight.release()

    if not background:
        _run()
        return
    threading.Thread(target=_run, name="staleness-ceiling", daemon=True).start()
