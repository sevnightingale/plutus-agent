"""register_prediction — plutus-predict's write surface (toolset: prediction-write).

Thin handler over trading.lifecycle.write.record_prediction: all refusal
logic (machine-resolvable criteria, 30d horizon cap, file-at-birth strategy
requirement, reasoned narrative scores) lives in the writer. The registered
data-point names are passed in so unknown DPs are refused at the gate.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

SCHEMA = {
    "name": "register_prediction",
    "description": (
        "Register a falsifiable prediction. success_criteria MUST be machine-"
        "resolvable: a leaf {data_point, params?, op: gte|lte|crosses_above|"
        "crosses_below|within_range|outside_range, threshold|range, baseline "
        "{value,ts} for crosses_*} or {all:[...]}/{any:[...]} combinators. "
        "Criteria leaves may only use data points flagged resolvable: true "
        "in list_data_points (those with a numeric value) — perception-only "
        "data points are refused here, at write time. "
        "horizon_hours ≤ 720 (30d hard cap). kind='strategy' (default) "
        "requires strategy_name (file-at-birth); 'stress'/'adhoc' don't. "
        "support_scores record the conviction inputs — narrative entries "
        "REQUIRE reasoning_md (the recorded reasoning IS the audit trail)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "claim": {"type": "string"},
            "horizon_hours": {"type": "number"},
            "success_criteria": {"type": "object"},
            "failure_criteria": {"type": "object"},
            "invalidation_criteria": {"type": "object"},
            "conviction": {"type": "number"},
            "risk_tolerance": {"type": "string", "enum": ["low", "med", "high"]},
            "symbol": {"type": "string"},
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
        "required": ["claim", "horizon_hours", "success_criteria", "conviction"],
    },
}


def _register_prediction(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db
    from trading.perception.core import data_point_registry

    try:
        horizon_hours = float(args["horizon_hours"])
        now = time.time()
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
            success_criteria=args["success_criteria"],
            failure_criteria=args.get("failure_criteria"),
            invalidation_criteria=args.get("invalidation_criteria"),
            conviction=float(args["conviction"]),
            risk_tolerance=args.get("risk_tolerance"),
            symbol=args.get("symbol"),
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
        prediction_id = write.record_prediction(
            get_db(), draft, known_data_points=known,
            resolvable_data_points=resolvable)
    except (ValueError, KeyError) as exc:
        return tool_error(str(exc))
    return tool_result({"prediction_id": prediction_id, "ok": True})


registry.register(
    name="register_prediction",
    toolset="prediction-write",
    schema=SCHEMA,
    handler=lambda args, **kw: _register_prediction(args),
    description="Register a machine-resolvable prediction (refuses invalid criteria).",
    emoji="🔮",
)
