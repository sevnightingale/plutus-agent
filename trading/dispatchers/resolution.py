"""Ops resolution tools (toolset: resolution) — deterministic, cheap, loud.

resolve_due_predictions: the SAFETY-NET sweep. The live watcher daemon resolves
price-zone predictions event-driven (a touch fires within seconds); this sweep
runs on the ops cadence and catches anything the watcher missed (daemon down,
horizon expiry between ticks). Both call the SAME shared resolver
(``trading.lifecycle.resolver``), which is race-safe, so a prediction the
watcher already resolved is a no-op here.

record_evaluation: one position_evaluations row per ops look at the open
position. enqueue_wake lives in wake.py (same toolset).
"""

from __future__ import annotations

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
    from trading.lifecycle import resolver, write
    from trading.lifecycle.db import get_db
    from trading.integrations.hyperliquid._client import get_info
    from trading.integrations.hyperliquid.outcomes import path_stats

    conn = get_db()
    try:
        raw = get_info().all_mids()
        mids = {k: float(v) for k, v in raw.items()}
    except Exception as exc:
        return tool_error(f"could not fetch prices (all_mids): {exc}")

    # deep=True: the safety-net sweep pulls candles for each still-open
    # prediction and re-checks the edges against the path MFE, catching any
    # favorable wick the watcher's live-mid poll missed between ticks.
    # Shared resolver also runs graduation.sync_strategy_statuses when anything
    # resolves — so ops-only resolutions (watcher down) still flip test↔active.
    res = resolver.resolve_open_predictions(
        conn, mids=mids, path_stats_fn=path_stats,
        fetch_fn=_fetch, fetch_extreme_fn=_fetch_extreme, deep=True)
    marked = res.get("marked_near", [])
    write.record_action_run(
        conn, action_type="resolution", agent="plutus-ops",
        session_name=session_id_from_context(),
        notes_md=f"{len(res['resolved'])} resolved, {len(marked)} near-locked "
                 f"of {res['open_count']} open")
    return tool_result({"resolved": res["resolved"], "marked_near": marked,
                        "open_count": res["open_count"]})


def _rescore_open(args: Dict[str, Any]) -> str:
    import json
    from trading.dispatchers._helpers import session_id_from_context
    from trading.dispatchers.predict_tools import score_strategy
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    due = queries.predictions_due_for_rescore(conn)
    by_strat: Dict[str, list] = {}
    for p in due:
        by_strat.setdefault(p["strategy_name"], []).append(p)

    rescored, failures = [], []
    for strat, preds in by_strat.items():
        try:
            result = score_strategy(strat)
        except Exception as exc:  # noqa: BLE001 — skip this strategy, keep going
            failures.append({"strategy_name": strat, "error": str(exc)})
            continue
        conv = result.get("conviction")
        if conv is None:
            failures.append({"strategy_name": strat, "error": "no conviction (all DPs missing)"})
            continue
        ss_json = json.dumps(result.get("support_scores"))
        for p in preds:
            write.record_prediction_evaluation(
                conn, prediction_id=p["id"], conviction=conv,
                support_scores_json=ss_json, regime_tag=p.get("regime_tag"),
                agent="plutus-ops", session_name=session_id_from_context())
        rescored.append({"strategy_name": strat, "conviction": conv,
                         "n_predictions": len(preds)})

    write.record_action_run(
        conn, action_type="rescore", agent="plutus-ops",
        session_name=session_id_from_context(),
        notes_md=f"{len(rescored)} strategies rescored, {len(failures)} failed")
    return tool_result({"rescored": rescored, "failures": failures,
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
            "Safety-net sweep: deterministically resolve every open price-zone "
            "prediction whose terms are met — favorable move touched the near "
            "edge (correct), invalidation criteria tripped (wrong), or the "
            "horizon expired (wrong). The live watcher resolves these "
            "event-driven; this catches anything it missed. Race-safe (no "
            "double-count). Returns the predictions resolved this pass."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: _resolve_due(args),
    description="Deterministically resolve all due predictions.",
    emoji="⚖️",
)

registry.register(
    name="rescore_open_predictions",
    toolset="resolution",
    schema={
        "name": "rescore_open_predictions",
        "description": (
            "Re-score conviction for every OPEN prediction due per its timescale "
            "cadence (intraday 30m, swing 4h, position 1d), appending a "
            "conviction-trajectory point. Groups by strategy — one cheap scoring "
            "pass per strategy — and writes one trajectory row per open "
            "prediction. The curve feeds reflect's calibration and "
            "invalidation-by-decay. Run it once per ops tick."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: _rescore_open(args),
    description="Re-score conviction for due open predictions (trajectory).",
    emoji="📉",
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
