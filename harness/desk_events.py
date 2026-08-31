"""The desk's event engine — cognition woken by evidence, not by clocks.

The sustainable-desk rebuild's scheduler for the three thinking seats
(predict / generate / reflect). The five event kinds in the scoping doc
reduce here to DB predicates, because the database already is the event
log: resolutions stamp ``predictions.resolved_at``, seat runs stamp
``action_runs``, closes stamp ``positions.closed_at``. No new channels.

Due-ness per seat:

* **predict** — a resolution has landed since the last successful predict
  run (evidence arrived: a slot freed, an outcome accrued), or the floor
  backstop expired (``PREDICT_FLOOR_S``). The floor exists because a pure
  event loop can starve from a cold start — nothing open, nothing
  resolves, nothing wakes — and its firing is logged as a signal that the
  event path went quiet.
* **generate** — a LIT regime cell has open capacity (the 2026-08-13
  under-capacity directive, previously prose in doctrine), rate-limited by
  ``GENERATE_COOLDOWN_S`` so authoring cannot absorb the sampling budget
  (the measured 2026-08-22 failure); or the 7-day routine floor.
* **reflect** — ``REFLECT_CLOSES_N`` positions closed since the last
  reflect run, or the 7-day floor.

Mechanics copy the proven staleness-ceiling shape (harness/cli/
staleness_ceiling.py): an attempt row is recorded BEFORE the spawn so a
crash cannot storm on restart; one spawn per pass, on its own daemon
thread, under a non-blocking lock. Spawn cooldowns are read from those
attempt rows (action_type='event_spawn', agent=<seat>), so they survive a
daemon restart.

Payload consumption — the piece the ceiling never had: each seat's return
contract gets a thin code handler; anything needing judgment becomes a
keyed wake to main. Hosted by the watchers daemon via :func:`tick`.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from harness import wake_queue

logger = logging.getLogger(__name__)

SOURCE = "desk-events"

PREDICT_FLOOR_S = 6 * 3600
PREDICT_COOLDOWN_S = 30 * 60
GENERATE_FLOOR_S = 7 * 24 * 3600
GENERATE_COOLDOWN_S = 6 * 3600
REFLECT_FLOOR_S = 7 * 24 * 3600
REFLECT_COOLDOWN_S = 24 * 3600
REFLECT_CLOSES_N = 3
PASS_INTERVAL_S = 60

_in_flight = threading.Lock()
_last_pass: float = 0.0


def _last_spawn_ts(conn, seat: str) -> Optional[float]:
    row = conn.execute(
        "SELECT MAX(ts) FROM action_runs "
        "WHERE action_type = 'event_spawn' AND agent = ?", (seat,)).fetchone()
    return row[0] if row and row[0] is not None else None


def _last_ok_run_ts(conn, action_type: str) -> Optional[float]:
    row = conn.execute(
        "SELECT MAX(ts) FROM action_runs WHERE action_type = ? AND ok = 1",
        (action_type,)).fetchone()
    return row[0] if row and row[0] is not None else None


# ── Due predicates ─────────────────────────────────────────────────────────
# Each returns (due, reason) — reason is human prose that goes into the
# spawned seat's task verbatim, so the seat knows why it is awake.


def predict_due(conn, now: Optional[float] = None) -> Tuple[bool, str]:
    now = now or time.time()
    last = _last_ok_run_ts(conn, "predict")
    if last is None:
        return True, "no successful predict run on record (cold start)"
    row = conn.execute(
        "SELECT MAX(resolved_at) FROM predictions").fetchone()
    newest_resolution = row[0] if row and row[0] is not None else None
    if newest_resolution is not None and newest_resolution > last:
        n = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE resolved_at > ?",
            (last,)).fetchone()[0]
        return True, (f"{n} prediction(s) resolved since the last predict "
                      f"run — evidence arrived, slots may be free")
    row = conn.execute(
        "SELECT MAX(ts) FROM regime_observations WHERE flipped = 1"
    ).fetchone()
    if row and row[0] is not None and row[0] > last:
        return True, ("a regime flip landed since the last predict run — "
                      "cells re-lit, eligibility changed")
    try:
        from trading.integrations.macro.calendar import printed_since
        ev = printed_since(last, now)
        if ev is not None:
            return True, (f"scheduled macro event printed since the last "
                          f"predict run: {ev['label']} "
                          f"({ev['ago_s'] / 60:.0f}min ago)")
    except Exception:
        logger.exception("macro calendar clause failed")
    if now - last > PREDICT_FLOOR_S:
        return True, (f"floor backstop: no predict run in "
                      f"{(now - last) / 3600:.1f}h (floor "
                      f"{PREDICT_FLOOR_S / 3600:.0f}h) and no resolution "
                      f"woke one — if this fires often, the event path is "
                      f"quiet for a reason worth reading")
    return False, ""


def generate_due(conn, now: Optional[float] = None) -> Tuple[bool, str]:
    from trading.lifecycle.queries import cell_capacity

    now = now or time.time()
    last = _last_ok_run_ts(conn, "generation")
    gaps = [r["cell"] for r in cell_capacity(conn)
            if r.get("lit") and r.get("slots_remaining", 0) > 0]
    if gaps:
        return True, ("lit cells under capacity: " + ", ".join(gaps[:8])
                      + (" …" if len(gaps) > 8 else ""))
    if last is None or now - last > GENERATE_FLOOR_S:
        return True, "routine generation floor (7d) — survey the evidence space"
    return False, ""


def reflect_due(conn, now: Optional[float] = None) -> Tuple[bool, str]:
    now = now or time.time()
    last = _last_ok_run_ts(conn, "reflect")
    closes = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE status = 'closed' "
        "AND closed_at > ?", (last or 0.0,)).fetchone()[0]
    if closes >= REFLECT_CLOSES_N:
        return True, f"{closes} unreflected closed positions"
    if last is None or now - last > REFLECT_FLOOR_S:
        return True, "weekly reflect floor"
    return False, ""


# ── Payload consumers ──────────────────────────────────────────────────────
# Seats self-record through their tools; the handlers only route what needs
# judgment to main as keyed wakes.


def _consume_predict(payload: Dict[str, Any]) -> None:
    read = payload.get("situational_read")
    if isinstance(read, str) and read.strip():
        # Narrative interpretation survives the perception seat: predict
        # writes a short situational read for the symbols it just worked,
        # stamped, into the board's Narrative zone. Prose for the operator
        # and the forum — never an input to any code path.
        try:
            import time as _time

            from harness.constants import get_hermes_home
            from trading.lifecycle.live_state import replace_zone

            stamp = _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime())
            replace_zone(get_hermes_home() / "PERCEPTION.md", "Narrative",
                         f"\n*{stamp} — plutus-predict*\n\n"
                         f"{read.strip()}\n")
        except Exception:
            logger.exception("could not write predict's situational read")
    findings = payload.get("escalation_findings") or []
    if findings:
        wake_queue.enqueue(
            "escalation",
            "predict reported blockers: " + "; ".join(
                str(f)[:200] for f in findings[:5]),
            source=SOURCE, key="predict:escalation")
    stale = payload.get("perception_stale") or []
    if stale:
        wake_queue.enqueue(
            "escalation",
            f"predict skipped {len(stale)} strategies on stale perception "
            f"despite self-refresh: {', '.join(str(s)[:60] for s in stale[:5])}",
            source=SOURCE, key="predict:perception_stale")


def _consume_generate(payload: Dict[str, Any]) -> None:
    missing = payload.get("missing_data_points_declared") or []
    if missing:
        wake_queue.enqueue(
            "escalation",
            f"generate declared missing data points (the self-extension "
            f"hook): {', '.join(str(m) for m in missing[:10])}",
            source=SOURCE, key="generate:missing_dps")


def _consume_reflect(payload: Dict[str, Any]) -> None:
    # Reflect records everything through its own tools; nothing to route.
    logger.info("reflect pass: %s status changes, %s weight updates",
                len(payload.get("status_changes") or []),
                len(payload.get("weight_updates") or []))


_SEATS: List[Dict[str, Any]] = [
    {"seat": "predict", "agent": "plutus-predict", "due": predict_due,
     "cooldown_s": PREDICT_COOLDOWN_S, "consume": _consume_predict,
     "task": ("Event-driven predict beat — {reason}. Cover every eligible, "
              "below-cap book; refresh a stale strategy's declared points "
              "yourself (force_fresh) before drafting, per your Procedure.")},
    {"seat": "generate", "agent": "plutus-generate", "due": generate_due,
     "cooldown_s": GENERATE_COOLDOWN_S, "consume": _consume_generate,
     "task": ("Generation pass — {reason}. Consult reflect's latest "
              "seed_report and the retired book before authoring; never "
              "author into a full or unlit cell.")},
    {"seat": "reflect", "agent": "plutus-reflect", "due": reflect_due,
     "cooldown_s": REFLECT_COOLDOWN_S, "consume": _consume_reflect,
     "task": "Reflect pass — {reason}. Run your full Procedure."},
]


def _spawn(rule: Dict[str, Any], reason: str) -> None:
    from harness.spawn import spawn_agent
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db

    # Attempt row BEFORE the spawn — a crash cannot storm on restart, and
    # the cooldown reads these rows so it survives a daemon restart too.
    with contextlib.closing(get_db()) as conn:
        write.record_action_run(
            conn, action_type="event_spawn", agent=rule["seat"],
            session_name=SOURCE, notes_md=reason[:400])

    task = rule["task"].format(reason=reason)
    try:
        result = spawn_agent(rule["agent"], task, session_name=SOURCE)
    except Exception as exc:
        logger.exception("event spawn of %s failed", rule["agent"])
        wake_queue.enqueue(
            "escalation",
            f"event engine could not spawn {rule['agent']}: "
            f"{type(exc).__name__}: {exc}",
            source=SOURCE, key=f"event_spawn:{rule['seat']}_failed")
        return
    if not result.get("ok"):
        wake_queue.enqueue(
            "escalation",
            f"{rule['agent']} run reported problems: "
            f"{'; '.join(str(p)[:150] for p in (result.get('problems') or [])[:4])}",
            source=SOURCE, key=f"event_spawn:{rule['seat']}_failed")
    payload = result.get("payload")
    if isinstance(payload, dict):
        try:
            rule["consume"](payload)
        except Exception:
            logger.exception("payload consumer for %s failed", rule["seat"])


def tick(*, background: bool = True) -> Optional[str]:
    """Evaluate the seats in order; spawn at most one per pass.

    Never blocks the caller: the spawn runs on its own daemon thread, and a
    pass while one is in flight is a no-op. Self-gates to one predicate
    pass per ``PASS_INTERVAL_S`` so the daemon's 5-second loop can call it
    freely. Returns the seat spawned (or None) so tests and the daemon log
    can see the decision.
    """
    global _last_pass
    if time.time() - _last_pass < PASS_INTERVAL_S:
        return None
    if not _in_flight.acquire(blocking=False):
        return None
    _last_pass = time.time()

    # The funding pass rides every engine pass, on its own thread (a fill
    # holds a venue round-trip and must not stall the caller's loop). Its
    # own lock keeps one in flight; every guard lives in the pass itself.
    def _fund() -> None:
        try:
            from trading.lifecycle.funding import fund_pass
            fund_pass()
        except Exception:
            logger.exception("funding pass crashed")

    if background:
        threading.Thread(target=_fund, name="funding-pass",
                         daemon=True).start()
    else:
        _fund()
    chosen: Optional[Dict[str, Any]] = None
    reason = ""
    try:
        from trading.lifecycle.db import get_db

        with contextlib.closing(get_db()) as conn:
            now = time.time()
            for rule in _SEATS:
                last_spawn = _last_spawn_ts(conn, rule["seat"])
                if last_spawn is not None and \
                        now - last_spawn < rule["cooldown_s"]:
                    continue
                due, reason = rule["due"](conn, now)
                if due:
                    chosen = rule
                    break
    except Exception:
        logger.exception("desk-events predicate pass failed")
        _in_flight.release()
        return None

    if chosen is None:
        _in_flight.release()
        return None

    logger.info("desk-events: %s due — %s", chosen["seat"], reason)

    def _run() -> None:
        try:
            _spawn(chosen, reason)
        finally:
            _in_flight.release()

    if background:
        threading.Thread(target=_run, name=f"desk-events-{chosen['seat']}",
                         daemon=True).start()
    else:
        _run()
    return chosen["seat"]
