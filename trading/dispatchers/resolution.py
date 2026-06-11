"""Ops resolution tools (toolset: resolution) — deterministic, cheap, loud.

resolve_due_predictions: machine-evaluates every due prediction's structured
criteria against fresh registry fetches. Ops never interprets — a criteria
set that won't evaluate becomes expired_unresolvable plus a wake for main
(that's a predict bug, not a judgment call).

record_evaluation: one position_evaluations row per ops look at the open
position. enqueue_wake lives in wake.py (same toolset).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from harness.tools.registry import registry, tool_error, tool_result


def _fetch(name: str, params: Optional[dict]) -> Optional[float]:
    """Numeric reading for a leaf criteria check. None on any failure.

    Extraction follows the data point's declared ``numeric_path`` — the same
    contract register_prediction enforces at write time, so a prediction that
    was accepted can always be resolved (barring a live fetch failure).
    """
    from trading.perception.core import data_point_registry
    try:
        entry = data_point_registry.lookup(name)
        value = entry.fn(**(params or {})) if entry.fn else None
    except Exception:
        return None
    return data_point_registry.extract_numeric(value, entry.numeric_path)


def _fetch_extreme(name: str, params: Optional[dict], since_ts: float):
    """(low, high) over the window since since_ts — for crosses_* ops.

    Uses the symbol's candle data point when available; None otherwise
    (which correctly yields 'unresolvable' rather than a guess).
    """
    from trading.perception.core import data_point_registry
    symbol = (params or {}).get("symbol")
    if not symbol:
        return None
    try:
        entry = data_point_registry.lookup("hl_candles")
        bars = max(1, int((time.time() - since_ts) / 3600) + 1)
        candles = entry.fn(symbol=symbol, interval="1h", lookback_bars=bars)
    except Exception:
        return None
    rows = candles.get("candles") if isinstance(candles, dict) else candles
    if not rows:
        return None
    try:
        lows = [float(c["l"] if isinstance(c, dict) else c[3]) for c in rows]
        highs = [float(c["h"] if isinstance(c, dict) else c[2]) for c in rows]
        return (min(lows), max(highs))
    except Exception:
        return None


def _resolve_due(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import criteria as criteria_mod
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    due = queries.due_predictions(conn)
    resolved = []
    unresolvable = []
    for p in due:
        crit = json.loads(p["success_criteria_json"])
        verdict = criteria_mod.resolve(crit, _fetch, _fetch_extreme)
        outcome = {"correct": "correct", "wrong": "wrong",
                   "unresolvable": "expired_unresolvable"}[verdict]
        write.resolve_prediction(
            conn, p["id"], outcome, resolved_by="plutus-ops",
            notes_md=None if verdict != "unresolvable" else
            "criteria did not evaluate (fetch failed or DP gone) — predict bug?",
        )
        (unresolvable if verdict == "unresolvable" else resolved).append(
            {"prediction_id": p["id"], "outcome": outcome,
             "strategy_name": p.get("strategy_name")})
    write.record_action_run(conn, action_type="resolution", agent="plutus-ops",
                            session_name=session_id_from_context(),
                            notes_md=f"{len(resolved)} resolved, {len(unresolvable)} unresolvable")
    return tool_result({"resolved": resolved, "unresolvable": unresolvable,
                        "due_count": len(due)})


def _record_evaluation(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db

    try:
        eval_id = write.record_evaluation(
            get_db(),
            position_id=int(args["position_id"]),
            conviction=float(args["conviction"]),
            agent=args.get("agent") or "plutus-ops",
            thesis_status=args.get("thesis_status"),
            active_thesis_id=args.get("thesis_id"),
            rationale_md=args.get("rationale"),
            recommended_action=args.get("recommended_action"),
            session_name=session_id_from_context(),
        )
    except (ValueError, KeyError) as exc:
        return tool_error(str(exc))
    return tool_result({"evaluation_id": eval_id, "ok": True})


registry.register(
    name="resolve_due_predictions",
    toolset="resolution",
    schema={
        "name": "resolve_due_predictions",
        "description": (
            "Machine-resolve every prediction past its horizon: each leaf of "
            "its structured criteria is fetched fresh and evaluated "
            "deterministically. Unresolvable criteria become "
            "expired_unresolvable (report them to main — that's a predict bug)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: _resolve_due(args),
    description="Deterministically resolve all due predictions.",
    emoji="⚖️",
)

registry.register(
    name="record_evaluation",
    toolset="resolution",
    schema={
        "name": "record_evaluation",
        "description": (
            "Record a position evaluation (the conviction trajectory). "
            "thesis_status: intact|strengthened|weakening|invalidated; "
            "recommended_action: hold|exit_now|tighten_sl."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "position_id": {"type": "integer"},
                "conviction": {"type": "number"},
                "thesis_status": {"type": "string"},
                "thesis_id": {"type": "integer"},
                "rationale": {"type": "string"},
                "recommended_action": {"type": "string"},
                "agent": {"type": "string"},
            },
            "required": ["position_id", "conviction"],
        },
    },
    handler=lambda args, **kw: _record_evaluation(args),
    description="Record a position evaluation row (conviction trajectory).",
    emoji="🩺",
)
