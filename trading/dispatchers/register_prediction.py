"""register_prediction — plutus-predict's write surface (toolset: prediction-write).

A prediction is a PRICE ZONE: a near edge (correctness floor) and a far edge
(target), both signed % moves from the spot price captured HERE at registration
(``entry_ref_price`` — never supplied by the LLM, whose price view is stale).
Direction is implied by the sign. Resolution is early + continuous: correct the
moment price touches the near edge, wrong at horizon otherwise. Optional
machine-resolvable invalidation criteria can resolve it wrong early.

All refusal logic (zone validity, 30d horizon cap, file-at-birth strategy
requirement, per-strategy open cap, reasoned narrative scores) lives in
trading.lifecycle.write.record_prediction.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "register_prediction",
    "description": (
        "Register a falsifiable PRICE-ZONE prediction. A prediction is a target "
        "zone expressed as a signed % move from the current price: near_edge_pct "
        "is the correctness floor (the smallest move that still counts as right) "
        "and far_edge_pct is the optimistic target — both same sign (bullish +, "
        "bearish -), with |far| > |near|. Direction is implied by the sign; you "
        "do NOT set a stop (that is the trade agent's job once a strategy is "
        "graduated). The entry reference price is captured server-side at "
        "registration. Resolution is early and continuous: CORRECT the moment "
        "price touches near_edge_pct before the horizon, WRONG at the horizon "
        "otherwise. invalidation_criteria (optional) is a machine-resolvable "
        "thesis-break over resolvable data points (leaf {data_point, params?, "
        "op, threshold|range} or all/any) that resolves the prediction WRONG "
        "early. horizon_hours ≤ 720 (30d hard cap). kind='strategy' (default) "
        "requires strategy_name (file-at-birth). The default capacity is 3 "
        "OPEN predictions per strategy; a positive-expectancy, non-tradeable, "
        "non-decaying incubation book may use 5. Concurrent predictions from "
        "one strategy are correlated trials, not independent evidence. "
        "support_scores record the conviction "
        "inputs — narrative entries REQUIRE reasoning_md (the audit trail)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "symbol": {"type": "string"},
            "horizon_hours": {"type": "number"},
            "near_edge_pct": {
                "type": "number",
                "description": "Correctness floor: signed % move (bullish +, bearish -).",
            },
            "far_edge_pct": {
                "type": "number",
                "description": "Target: signed % move, same sign as near, |far| > |near|.",
            },
            "conviction": {"type": "number"},
            "invalidation_criteria": {"type": "object"},
            "risk_tolerance": {"type": "string", "enum": ["low", "med", "high"]},
            "strategy_name": {"type": "string"},
            "kind": {"type": "string", "enum": ["strategy", "stress", "adhoc"]},
            "regime_tag": {"type": "string"},
            "snapshot_ids": {"type": "array", "items": {"type": "integer"}},
            "support_scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "data_point": {"type": "string"},
                        "score": {"type": "number"},
                        "kind": {"type": "string", "enum": ["numerical", "narrative"]},
                        "weight": {"type": "number"},
                        "normalizer": {"type": "string"},
                        "reasoning_md": {"type": "string"},
                        "reading_json": {"type": "string"},
                    },
                    "required": ["data_point", "score", "kind"],
                },
            },
        },
        "required": ["claim", "symbol", "horizon_hours", "near_edge_pct",
                     "far_edge_pct", "conviction"],
    },
}


def _capture_entry_ref(symbol: str):
    """Spot at registration, fetched fresh server-side (not from the LLM)."""
    from trading.perception.core import data_point_registry
    entry = data_point_registry.lookup("hl_price")
    value = entry.fn(symbol=symbol) if entry.fn else None
    return data_point_registry.extract_numeric(value, entry.numeric_path)


def _register_prediction(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db
    from trading.perception.core import data_point_registry

    symbol = args.get("symbol")
    if not symbol:
        return tool_error("symbol is required — a price zone needs a symbol to price")
    try:
        entry_ref_price = _capture_entry_ref(symbol)
    except Exception as exc:
        return tool_error(f"could not capture entry reference price for {symbol}: {exc}")
    if not entry_ref_price or entry_ref_price <= 0:
        return tool_error(
            f"entry reference price for {symbol} is unavailable — refusing to "
            "register a price zone with no anchor")

    # Grounding backstop: refuse to author on STALE strategy data. predict is
    # meant to catch this in ORIENT (perception_freshness) and have main refresh
    # perception first; this is the hard guard. Only stale (present-but-old)
    # blocks — a never-fetched DP shouldn't loop us forever, and conviction
    # self-fetches live regardless.
    kind = args.get("kind", "strategy")
    strat_name = args.get("strategy_name")
    if kind == "strategy" and strat_name:
        from trading.perception import freshness as fresh_mod
        from trading.strategies import files as strat_files

        spath = strat_files.strategies_dir() / f"{strat_name}.md"
        if spath.exists():
            strat = strat_files.parse_strategy(spath)
            missing_declared = set(strat.missing_data_points or [])
            declared = [dp for dp in (strat.data_points or [])
                        if isinstance(dp, dict) and dp.get("name") not in missing_declared]
            stale = [e for e in fresh_mod.stale_data_points(declared)
                     if e["reason"] == "stale"]
            if stale:
                pts = ", ".join(f"{e['name']} ({e['age_s']}s > {e['max_age_s']}s)"
                                for e in stale)
                return tool_error(
                    f"stale perception data for strategy {strat_name!r} — "
                    f"prediction refused: {pts}. Author on fresh readings: "
                    f"return perception_stale to main so it re-runs perception, "
                    f"then retry.")

    try:
        horizon_hours = float(args["horizon_hours"])
        now = time.time()
        # One score per data point — a duplicate used to surface as a raw
        # UNIQUE-constraint traceback aborting the whole registration.
        dp_seen: set = set()
        for s in (args.get("support_scores") or []):
            if s["data_point"] in dp_seen:
                return tool_error(
                    f"duplicate support score for data_point "
                    f"{s['data_point']!r} — provide ONE score per data point")
            dp_seen.add(s["data_point"])
        scores = [
            write.SupportScore(
                data_point=s["data_point"], score=float(s["score"]),
                kind=s["kind"], weight=s.get("weight"),
                normalizer=s.get("normalizer"),
                reasoning_md=s.get("reasoning_md"),
                reading_json=s.get("reading_json"),
            )
            for s in (args.get("support_scores") or [])
        ]
        draft = write.PredictionDraft(
            claim_md=args["claim"],
            horizon_ts=now + horizon_hours * 3600.0,
            entry_ref_price=float(entry_ref_price),
            near_edge_pct=float(args["near_edge_pct"]),
            far_edge_pct=float(args["far_edge_pct"]),
            invalidation_criteria=args.get("invalidation_criteria"),
            conviction=float(args["conviction"]),
            risk_tolerance=args.get("risk_tolerance"),
            symbol=symbol,
            strategy_name=args.get("strategy_name"),
            kind=args.get("kind", "strategy"),
            regime_tag=args.get("regime_tag"),
            snapshot_ids=args.get("snapshot_ids") or (),
            support_scores=scores,
            agent="plutus-predict",
            session_name=session_id_from_context(),
            ts=now,
        )
        known = {e.name for e in data_point_registry.list_all()} or None
        resolvable = data_point_registry.resolvable_names() if known else None
        conn = get_db()
        prediction_id = write.record_prediction(
            conn, draft, known_data_points=known,
            resolvable_data_points=resolvable)
    except (ValueError, KeyError) as exc:
        return tool_error(str(exc))
    # Fundable window: an ACTIVE strategy's prediction is only fundable for
    # ACTIONABLE_MAX_AGE_S (20 min). predict runs synchronously under main, so
    # main is awake right now — the wake is the backstop against main deferring
    # past the window within its own turn: the queue re-nudges it next drain.
    fundable_wake = False
    if strat_name:
        srow = conn.execute("SELECT status FROM strategies WHERE name=?",
                            (strat_name,)).fetchone()
        if srow and srow["status"] == "active":
            try:
                from harness.wake_queue import enqueue
                enqueue(reason="schedule",
                        detail=(f"fundable prediction #{prediction_id} registered "
                                f"(strategy {strat_name}, ACTIVE) — actionable "
                                f"window {int(queries.ACTIONABLE_MAX_AGE_S // 60)} "
                                f"min from registration"),
                        source="plutus-predict")
                fundable_wake = True
            except Exception as exc:
                logger.warning("fundable-window wake enqueue failed: %s", exc)
    # Intrinsic reward:risk from the zone geometry — exists BEFORE any wins
    # (queries.strategy_rr needs realized wins). |far| > |near| is enforced at
    # write, so rr > 1; the v2 conditional-entry gate reads this value.
    near = float(args["near_edge_pct"])
    far = float(args["far_edge_pct"])
    intrinsic_rr = round(abs(far) / abs(near), 3) if near else None
    strategy_capacity = (queries.strategy_prediction_capacity(conn, strat_name)
                         if strat_name else None)
    return tool_result({"prediction_id": prediction_id, "ok": True,
                        "entry_ref_price": float(entry_ref_price),
                        "intrinsic_rr": intrinsic_rr,
                        "fundable_wake": fundable_wake,
                        "strategy_capacity": strategy_capacity,
                        "slots": queries.open_slot_counts(conn)})


registry.register(
    name="register_prediction",
    toolset="prediction-write",
    schema=SCHEMA,
    handler=lambda args, **kw: _register_prediction(args),
    description="Register a machine-resolvable price-zone prediction.",
    emoji="🔮",
)
