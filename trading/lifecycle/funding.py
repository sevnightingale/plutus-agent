"""The funding pass — mechanical funding as an actual mechanism.

Doctrine has said since 2026-06-25 that funding carries zero trading
discretion: selection is a query, the guards are code, sizing is a pure
function, execution is a tool. The one thing left inside main's LLM was
calling the tool — so the sustainable-desk rebuild moves the call here.
The pass runs on the event engine's cadence: cheap pre-checks, then
``best_actionable_prediction`` → ``desk_open_position`` with a templated
thesis built from the same structured facts the tool records anyway.

Main keeps the two things that are genuinely its: the public narrative
(a fill enqueues a wake carrying the facts, and main writes the Arena
forum post), and the close decision at the alert edges.

A candidate the tool refuses for candidate-specific reasons (the live-
price RR gate, a missing stop estimate) is remembered and not retried —
its refusal is recorded as a skip observation, exactly what main used to
write by hand. Transient refusals (HALT, not flat, trade path not ready)
are not remembered; conditions change.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Set

from harness import wake_queue

logger = logging.getLogger(__name__)

SOURCE = "funding-pass"

_TRANSIENT_MARKERS = ("HALT", "position is already open", "READY",
                      "trade path", "readiness")

_lock = threading.Lock()
_dead_candidates: Set[int] = set()


def _is_transient(refusal: str) -> bool:
    return any(m.lower() in refusal.lower() for m in _TRANSIENT_MARKERS)


def _thesis_md(cand: Dict[str, Any]) -> str:
    lane = cand.get("lane", "graduated")
    bits = [f"Auto-funded {lane}-lane entry on {cand.get('symbol', '?')} — "
            f"strategy {cand.get('strategy_name')}."]
    conv_cal = cand.get("conviction_calibrated")
    conv = cand.get("conviction")
    if conv_cal is not None:
        bits.append(f"Calibrated conviction {conv_cal:.2f} (raw {conv:.2f}).")
    elif conv is not None:
        bits.append(f"Conviction {conv:.2f} (raw; calibration unavailable).")
    if cand.get("ev_pct") is not None:
        bits.append(f"Setup EV {cand['ev_pct']:+.2f}% "
                    f"(p_win {cand.get('p_win')}, stop {cand.get('stop_pct')}%, "
                    f"reward {cand.get('reward_pct')}%, "
                    f"target {cand.get('target')}).")
    bits.append(f"Selection by best_actionable_prediction; every guard, the "
                f"stop derivation and the sizing band are the tool's own — "
                f"prediction #{cand.get('prediction_id')}.")
    return " ".join(bits)


def fund_pass(conn=None) -> Dict[str, Any]:
    """One funding evaluation. Cheap when there is nothing to do."""
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    if not _lock.acquire(blocking=False):
        return {"acted": False, "why": "pass already in flight"}
    own = conn is None
    try:
        if own:
            conn = get_db()
        if queries.halt_reason() is not None:
            return {"acted": False, "why": "HALT"}
        if queries.open_position(conn) is not None:
            return {"acted": False, "why": "position open"}
        cand = queries.best_actionable_prediction(conn)
        if cand is None:
            return {"acted": False, "why": "no fundable candidate"}
        pid = int(cand["prediction_id"])
        if pid in _dead_candidates:
            return {"acted": False, "why": f"candidate #{pid} already refused"}

        from trading.dispatchers.desk_execution import _desk_open

        res = json.loads(_desk_open({"prediction_id": pid,
                                     "thesis_md": _thesis_md(cand),
                                     "agent": SOURCE}))
        if res.get("ok"):
            facts = {"position_id": res.get("position_id"),
                     "symbol": cand.get("symbol"),
                     "strategy": cand.get("strategy_name"),
                     "lane": cand.get("lane"),
                     "prediction_id": pid,
                     "sizing": (res.get("decision") or {}).get("sizing")
                     or res.get("sizing")}
            wake_queue.enqueue(
                "escalation",
                "FILLED by the funding pass — write and post the Arena forum "
                "narrative for this entry (record kind=forum_post): "
                + json.dumps({k: v for k, v in facts.items()
                              if v is not None}),
                source=SOURCE)
            logger.info("funding pass FILLED prediction #%s (%s)", pid,
                        cand.get("strategy_name"))
            return {"acted": True, "filled": True, "position_id":
                    res.get("position_id"), "prediction_id": pid}

        refusal = str(res.get("refused") or res.get("error") or
                      res.get("aborted_reason") or "unknown refusal")
        transient = _is_transient(refusal)
        if not transient:
            _dead_candidates.add(pid)
            try:
                write.record_observation(
                    conn, agent=SOURCE, kind="skip",
                    prediction_ids=[pid],
                    text_md=(f"funding pass skipped prediction #{pid} "
                             f"({cand.get('strategy_name')}, "
                             f"{cand.get('lane')} lane): {refusal}"))
            except Exception:
                logger.exception("could not record the skip observation")
        logger.info("funding pass refused (#%s, transient=%s): %s",
                    pid, transient, refusal[:200])
        return {"acted": True, "filled": False, "prediction_id": pid,
                "refusal": refusal, "transient": transient}
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _lock.release()
