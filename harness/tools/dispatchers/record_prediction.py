"""record_prediction — pre-register a falsifiable claim, no capital at risk.

The single most powerful learning primitive Plutus has. A prediction is a
public commitment with a horizon: "I think X will happen by Y, and here's
the success criterion that will make it correct." Time passes. Plutus (or
the prediction-tracker skill) checks the criterion and resolves the
prediction as correct / wrong / ambiguous / expired_unresolvable.

The resolved set feeds the calibration curve — does Plutus actually know
what it claims to know? Predictions accumulate hundreds of times faster
than trades because there's no capital risk gating them, so they're the
fastest path to building a measurable epistemic track record.

DISTINCT FROM theses: theses drive trades (require invalidation criteria,
size, R/R). Predictions drive learning. A prediction can mature into a
thesis once the setup actually triggers — but the prediction stays as the
original epistemic record.

DISTINCT FROM observations: observations are passive notes ("I saw X
today"). Predictions are commitments ("I claim Y will happen").
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from agent.lifecycle_db import get_lifecycle_db
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "record_prediction",
    "description": (
        "Pre-register a falsifiable claim with a time horizon. NO trade involved — "
        "this is calibration scaffolding. State the claim, set success criteria "
        "as machine-checkable conditions, set the resolution horizon, and Plutus "
        "(or the prediction-tracker skill) checks it later. The whole point is "
        "to accumulate evidence about whether Plutus's directional intuitions "
        "actually hold. Use 5-20× more often than place_order — predictions are "
        "free; trades cost capital."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "claim_md": {
                "type": "string",
                "description": (
                    "Plain-English falsifiable claim. Must specify direction + "
                    "magnitude + time bound. GOOD: 'BTC will close above $82,000 "
                    "within 24 hours.' BAD: 'BTC looks bullish.' (not falsifiable)."
                ),
            },
            "horizon_hours": {
                "type": "number",
                "description": "Hours from now until the prediction must be resolved.",
            },
            "success_criteria": {
                "type": "object",
                "description": (
                    "Machine-checkable definition of 'correct.' E.g. "
                    "{type: 'price_above', symbol: 'BTC', threshold: 82000} or "
                    "{type: 'composite', all_of: [...]}. The prediction-tracker "
                    "skill will read this to evaluate. If you can't write a "
                    "checkable criterion, the prediction is too vague — refine it."
                ),
            },
            "failure_criteria": {
                "type": "object",
                "description": (
                    "Optional. If omitted, failure = 'success criteria not met by "
                    "horizon.' Provide explicitly when there are TWO sided thresholds "
                    "(e.g., 'BTC above 82K succeeds; BTC below 78K explicitly fails; "
                    "anywhere in between is ambiguous')."
                ),
            },
            "conviction": {
                "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
                "description": (
                    "Prior probability you assign to the claim being correct. "
                    "0.5 = coin-flip / no edge. 0.7 = high confidence. 0.95 = "
                    "near-certain. Be honest — the calibration curve will catch "
                    "systematic over- or under-confidence."
                ),
            },
            "symbol": {
                "type": "string",
                "description": "Optional symbol focus (BTC, ETH, ...).",
            },
            "strategy_name": {
                "type": "string",
                "description": (
                    "Optional. Name of the strategy file under "
                    "~/.plutus-agent/strategies/ that this prediction tests "
                    "(e.g., 'cvd-divergence-fade' from observation/). Lets you "
                    "compute per-strategy calibration."
                ),
            },
            "regime_tag": {
                "type": "string",
                "description": (
                    "Perceived regime when the prediction was made. "
                    "Used to slice calibration by regime."
                ),
            },
            "snapshot_ids": {
                "type": "array",
                "description": "Optional list of data_point_snapshot ids supporting the claim.",
            },
            "structured_tags": {
                "type": "object",
                "description": "Optional tags for slicing later (e.g., {kind: 'price_target', horizon: 'short'}).",
            },
        },
        "required": ["claim_md", "horizon_hours", "success_criteria"],
    },
}


def _record_prediction(args: Dict[str, Any]) -> str:
    claim = (args.get("claim_md") or "").strip()
    if not claim:
        return tool_error("record_prediction requires a non-empty claim_md")

    try:
        horizon_hours = float(args.get("horizon_hours") or 0)
    except (TypeError, ValueError):
        return tool_error("record_prediction requires numeric horizon_hours")
    if horizon_hours <= 0:
        return tool_error(
            "horizon_hours must be > 0. Open-ended predictions can't be "
            "evaluated and don't feed calibration. Pick a horizon — 4h for "
            "intraday, 24h for swing, 168h (7d) for positioning."
        )

    success_criteria = args.get("success_criteria")
    if not isinstance(success_criteria, dict) or not success_criteria:
        return tool_error(
            "record_prediction requires success_criteria as a non-empty object. "
            "If you can't define a checkable criterion, the claim is too vague."
        )

    conviction = float(args.get("conviction") if args.get("conviction") is not None else 0.5)
    if not (0.0 <= conviction <= 1.0):
        return tool_error("conviction must be in [0.0, 1.0]")

    now = time.time()
    horizon_ts = now + horizon_hours * 3600.0

    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO predictions(ts, horizon_ts, symbol, claim_md, "
            "success_criteria_json, failure_criteria_json, conviction, "
            "strategy_name, regime_tag, snapshot_ids_json, structured_tags_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                horizon_ts,
                args.get("symbol"),
                claim,
                json.dumps(success_criteria),
                json.dumps(args["failure_criteria"]) if args.get("failure_criteria") else None,
                conviction,
                args.get("strategy_name"),
                args.get("regime_tag"),
                json.dumps(args["snapshot_ids"]) if args.get("snapshot_ids") else None,
                json.dumps(args["structured_tags"]) if args.get("structured_tags") else None,
            ),
        ).lastrowid

    prediction_id = db._execute_write(_w)
    return tool_result({
        "prediction_id": prediction_id,
        "horizon_ts": horizon_ts,
        "horizon_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(horizon_ts)),
        "conviction": conviction,
    })


registry.register(
    name="record_prediction",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _record_prediction(args),
    description="Pre-register a falsifiable claim with horizon — calibration scaffolding.",
    emoji="🔮",
)
