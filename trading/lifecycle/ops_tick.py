"""The deterministic back office — the plutus-ops seat, as code.

The LLM ops seat ran twelve procedural steps every thirty minutes and was
82% of the desk's spawn volume, while its own recipe declared "ops never
interprets". Eleven of the twelve steps were already pure functions; the
escalation rules were prose mappings from structured verdicts to
``enqueue_wake`` keys. This module is those steps and mappings as code
(sustainable-desk rebuild, 2026-08-31). The wake keys are the exact ones
the retired recipe named, so the keyed backoff in ``harness.wake_queue``
carries over unchanged.

Two properties are deliberate:

* **A failing step never stops the tick.** Each step is isolated; its
  failure is recorded in the tick's notes and the remaining steps still
  run. A back office that dies on its first exception protects nothing.
* **The tick records itself.** The retired seat never wrote an
  ``action_runs`` row (its tools wrote their own), so there was no history
  of ops ticks at all. Every tick now lands one ``action_type="ops"`` row
  whose ``notes_md`` is parseable JSON per step.

Hosted by the watchers daemon via :func:`maybe_run_ops_tick` — the same
kick-a-thread-under-a-lock shape as ``harness.cli.staleness_ceiling``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from harness import wake_queue

logger = logging.getLogger(__name__)

OPS_TICK_INTERVAL_S = 30 * 60
PERCEPTION_SWEEP_INTERVAL_S = 4 * 3600  # the seat's old floor, now a gate
SOURCE = "ops-tick"


def _parse_tool_json(raw: str) -> Dict[str, Any]:
    """Dispatcher privates return ``tool_result``/``tool_error`` JSON strings."""
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data if isinstance(data, dict) else {"value": data}


def _wake(notes: Dict[str, Any], *, reason: str, key: str, detail: str) -> None:
    wake_queue.enqueue(reason, detail, source=SOURCE, key=key)
    notes.setdefault("wakes", []).append(key)


# ── The steps ──────────────────────────────────────────────────────────────
# Each takes (conn, notes) and may raise; the runner isolates failures.


def _step_resolve(conn, notes: Dict[str, Any]) -> None:
    """Safety-net deep resolution sweep (the watcher's fast path may miss a
    wick between ticks). Runs graduation as a side effect of any resolution."""
    from trading.dispatchers.resolution import _resolve_due

    res = _parse_tool_json(_resolve_due({}))
    notes["resolve"] = {
        "resolved": len(res.get("resolved") or []),
        "near": len(res.get("marked_near") or []),
        "open": res.get("open_count"),
    }
    unreadable = res.get("unresolvable_invalidations") or []
    if unreadable:
        # A thesis-break nothing can read looks exactly like a thesis that
        # is holding — loud by construction (fb73685 lesson).
        notes["resolve"]["unreadable"] = [u.get("prediction_id")
                                         for u in unreadable]
        _wake(notes, reason="escalation", key="resolution:unreadable",
              detail=(f"{len(unreadable)} prediction(s) carry an UNREADABLE "
                      f"invalidation: "
                      + ", ".join(str(u.get("prediction_id"))
                                  for u in unreadable)))


def _step_rescore(conn, notes: Dict[str, Any]) -> None:
    """Conviction trajectory on open predictions due for a re-score."""
    from trading.dispatchers.resolution import _rescore_open

    res = _parse_tool_json(_rescore_open({}))
    notes["rescore"] = {"rescored": len(res.get("rescored") or []),
                        "failures": len(res.get("failures") or [])}


def _step_position(conn, notes: Dict[str, Any]) -> None:
    """Evaluate the open position against its thesis. The retired seat's one
    judgment step — done by the deterministic ``rescore_position`` it never
    had in its toolset. A recommendation other than hold escalates to main;
    main still makes the exit call (the deliberately retained judgment)."""
    from trading.dispatchers.desk_execution import _rescore_position
    from trading.lifecycle import queries

    pos = queries.open_position(conn)
    if pos is None:
        notes["position"] = {"open": False}
        return
    res = _parse_tool_json(_rescore_position({"position_id": pos["id"]}))
    action = res.get("recommended_action")
    notes["position"] = {"open": True, "id": pos["id"],
                         "recommended_action": action,
                         "conviction": res.get("conviction")}
    if action and action != "hold":
        _wake(notes, reason="escalation", key="position:evaluation",
              detail=(f"position {pos['id']} {pos['symbol']} re-score "
                      f"recommends {action} "
                      f"(conviction {res.get('conviction')}); "
                      f"the exit decision is yours — rescore_position has "
                      f"recorded the evaluation"))


def _step_live_state(conn, notes: Dict[str, Any]) -> None:
    from trading.lifecycle.live_state import write_live_state

    res = write_live_state(conn)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "write_live_state failed")
    notes["live_state"] = {"ok": True}


def _step_capital(conn, notes: Dict[str, Any]) -> None:
    from trading.lifecycle.capital import reconcile_capital_movements

    res = reconcile_capital_movements(conn)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "capital reconcile failed")
    notes["capital"] = {"inserted": res.get("inserted"),
                        "net_deposits_usd": res.get("net_deposits_usd")}


def _step_perception(conn, notes: Dict[str, Any]) -> None:
    """The full board sweep + render, gated to the retired seat's 4h floor.
    Between sweeps, predict and the regime classifier refresh their own
    inputs at need (force_fresh / cache budgets) — the board is coverage,
    not the desk's only eyes. Records action_type="perception" so the floor
    stays satisfied by the same clock that used to satisfy it."""
    from trading.dispatchers.perception_sweep import _render, _sweep
    from trading.lifecycle import write

    last = conn.execute(
        "SELECT MAX(ts) FROM action_runs "
        "WHERE action_type = 'perception' AND ok = 1").fetchone()[0]
    if last is not None and time.time() - last < PERCEPTION_SWEEP_INTERVAL_S:
        notes["perception"] = {"swept": False,
                               "age_h": round((time.time() - last) / 3600, 1)}
        return
    swept = _parse_tool_json(_sweep({}))
    rendered = _parse_tool_json(_render({}))
    ok = not swept.get("failed_total")
    notes["perception"] = {"swept": True, "fetched": swept.get("fetched"),
                           "failed": swept.get("failed_total"),
                           "rows": rendered.get("rows")}
    write.record_action_run(
        conn, action_type="perception", agent=SOURCE, ok=True,
        session_name=SOURCE,
        notes_md=json.dumps({"fetched": swept.get("fetched"),
                             "failed": swept.get("failed_total")}))
    if not ok:
        # Failures are FAILED rows on the board (honest absence), and worth
        # a keyed note when they cluster.
        _wake(notes, reason="escalation", key="perception:sweep_failures",
              detail=f"{swept.get('failed_total')} of "
                     f"{swept.get('fetched')} sweep fetches failed — "
                     f"see the board's FAILED rows")


def _step_regime(conn, notes: Dict[str, Any]) -> None:
    """Deterministic regime classification from cached readings (the
    plutus-regime seat as arithmetic; hysteresis inside the classifier)."""
    from trading.regime.classifier import run

    res = run(conn)
    notes["regime"] = {"written": res["written"], "flips": res["flips"],
                       "skipped": len(res["skipped"])}


def _step_staleness(conn, notes: Dict[str, Any]) -> None:
    """The floor watchdog: overdue action → one keyed wake (backoff-managed)."""
    from trading.dispatchers.wake import STALENESS_FLOORS
    from trading.lifecycle import queries

    last = queries.last_action_runs(conn)
    now = time.time()
    overdue = []
    for action, floor in STALENESS_FLOORS.items():
        last_ts = last.get(action)
        if last_ts is None or (now - last_ts) > floor:
            overdue.append(action)
            age = "never" if last_ts is None else f"{(now - last_ts) / 3600:.1f}h"
            _wake(notes, reason="staleness", key=f"staleness:{action}",
                  detail=f"{action} is overdue (last: {age}, "
                         f"floor {floor / 3600:.0f}h)")
    notes["staleness"] = {"overdue": overdue}


def _fetch_dp(name: str) -> Dict[str, Any]:
    from trading.perception.fetch_core import fetch_and_snapshot

    res = fetch_and_snapshot(name, {}, session_id=SOURCE, tier="ops")
    if not res.get("ok"):
        raise RuntimeError(f"{name} fetch failed: {res.get('error')}")
    value = res.get("value")
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} returned no verdict dict")
    return value


def _step_trade_path(conn, notes: Dict[str, Any]) -> None:
    v = _fetch_dp("hl_trade_readiness")
    notes["trade_path"] = {"ready": v.get("ready"),
                           "days_remaining": v.get("days_remaining")}
    if not v.get("ready"):
        _wake(notes, reason="escalation", key="trade_path:readiness",
              detail=f"trade path NOT READY: {v.get('reason')}")
    elif v.get("warn_expiring_soon"):
        _wake(notes, reason="escalation", key="trade_path:readiness",
              detail=(f"API-wallet registration expires in "
                      f"{v.get('days_remaining')} day(s) — renew ahead of it"))


def _step_acp_auth(conn, notes: Dict[str, Any]) -> None:
    v = _fetch_dp("acp_auth_readiness")
    notes["acp"] = {"alive": v.get("alive")}
    if not v.get("alive"):
        _wake(notes, reason="escalation", key="acp:auth_dead",
              detail=f"ACP auth is dead: {v.get('reason')}")
    elif v.get("critical"):
        _wake(notes, reason="escalation", key="acp:auth_critical",
              detail=f"ACP auth critical: {v.get('reason')}")
    elif v.get("warn_reauth_soon"):
        _wake(notes, reason="escalation", key="acp:auth_warn",
              detail=f"ACP re-auth due soon: {v.get('reason')}")


def _step_provider_meter(conn, notes: Dict[str, Any]) -> None:
    """Both meters share one verdict contract, so the wake rules are shared."""
    from trading.integrations.deepseek.data_points import _configured_provider

    provider = _configured_provider()
    meter = {"deepseek": "deepseek_balance",
             "opencode-go": "opencode_go_usage"}.get(provider)
    if meter is None:
        notes["provider"] = {"provider": provider, "meter": None}
        return
    try:
        v = _fetch_dp(meter)
    except Exception as exc:
        _wake(notes, reason="escalation", key="provider:balance_unknown",
              detail=f"{meter} unreadable — the meter itself is dark: {exc}")
        raise
    notes["provider"] = {"provider": provider,
                         "balance_usd": v.get("balance_usd"),
                         "max_percent": v.get("max_percent")}
    if v.get("fetch_failed"):
        _wake(notes, reason="escalation", key="provider:balance_unknown",
              detail=f"{meter}: {v.get('reason')}")
    elif v.get("critical"):
        _wake(notes, reason="escalation", key="provider:balance_critical",
              detail=f"{meter}: {v.get('reason')}")
    elif v.get("low"):
        _wake(notes, reason="escalation", key="provider:balance_low",
              detail=f"{meter}: {v.get('reason')}")


def _step_hygiene(conn, notes: Dict[str, Any]) -> None:
    from trading.lifecycle.hygiene import sweep

    res = sweep(conn)
    notes["hygiene"] = {"ran": bool(res.get("ran", True))}


def _step_integrity(conn, notes: Dict[str, Any]) -> None:
    from trading.lifecycle.integrity import check_integrity

    res = check_integrity(conn)
    notes["integrity"] = {"ok": res["ok"],
                          "violations": len(res["violations"]),
                          "checks_failed": res["checks_failed"]}
    for v in res["violations"]:
        _wake(notes, reason="escalation", key=f"integrity:{v['check']}",
              detail=f"[{v.get('severity', 'warn')}] {v['check']}: "
                     f"{v['detail']}")
    if res["checks_failed"]:
        # The checker itself broke — which outranks anything it did report.
        _wake(notes, reason="escalation", key="integrity:checker_failed",
              detail=f"integrity checks raised: {res['checks_failed']}")


_STEPS: List[tuple] = [
    ("resolve", _step_resolve),
    ("rescore", _step_rescore),
    ("position", _step_position),
    ("live_state", _step_live_state),
    ("capital", _step_capital),
    ("perception", _step_perception),
    ("regime", _step_regime),
    ("staleness", _step_staleness),
    ("trade_path", _step_trade_path),
    ("acp", _step_acp_auth),
    ("provider", _step_provider_meter),
    ("hygiene", _step_hygiene),
    ("integrity", _step_integrity),
]


def ops_tick(conn=None) -> Dict[str, Any]:
    """Run the back office once. Isolated steps; one ``action_runs`` row."""
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db

    own_conn = conn is None
    if own_conn:
        conn = get_db()
    notes: Dict[str, Any] = {"wakes": []}
    failed: List[str] = []
    started = time.time()
    try:
        for name, fn in _STEPS:
            try:
                fn(conn, notes)
            except Exception as exc:
                failed.append(name)
                notes[name] = {"ok": False,
                               "error": f"{type(exc).__name__}: {exc}"}
                logger.warning("ops step %s failed: %s", name, exc)
        notes["failed_steps"] = failed
        notes["duration_s"] = round(time.time() - started, 1)
        ok = not failed
        try:
            write.record_action_run(
                conn, action_type="ops", agent=SOURCE, ok=ok,
                session_name=SOURCE, notes_md=json.dumps(notes))
        except Exception as exc:  # the record must never mask the tick result
            logger.error("ops tick could not record itself: %s", exc)
        return {"ok": ok, "failed_steps": failed, "notes": notes}
    finally:
        if own_conn:
            with contextlib.suppress(Exception):
                conn.close()


# ── Scheduling (hosted by the watchers daemon) ─────────────────────────────

_in_flight = threading.Lock()
_last_started: float = 0.0


def maybe_run_ops_tick(*, interval_s: float = OPS_TICK_INTERVAL_S,
                       background: bool = True) -> bool:
    """Kick a tick when one is due; never blocks the caller's loop.

    The first call after process start runs immediately (a restarted daemon
    should not wait half an hour to notice a naked position). Returns True
    when a tick was started.
    """
    global _last_started
    if time.time() - _last_started < interval_s:
        return False
    if not _in_flight.acquire(blocking=False):
        return False
    _last_started = time.time()

    def _run() -> None:
        try:
            ops_tick()
        except Exception:
            logger.exception("ops tick crashed")
        finally:
            _in_flight.release()

    if background:
        threading.Thread(target=_run, name="ops-tick", daemon=True).start()
    else:
        _run()
    return True
