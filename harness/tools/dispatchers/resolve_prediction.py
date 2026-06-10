"""resolve_prediction — close out a pre-registered prediction.

Plutus calls this once the success/failure criteria can be evaluated. The
outcome (correct | wrong | ambiguous | expired_unresolvable) is what the
calibration curve consumes — without resolution, predictions sit forever
and contribute nothing.

The prediction-tracker skill scans for predictions whose horizon_ts has
passed and resolves them in batch each cycle.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.registry import registry, tool_error, tool_result


VALID_OUTCOMES = ("correct", "wrong", "ambiguous", "expired_unresolvable")


SCHEMA = {
    "name": "resolve_prediction",
    "description": (
        "Resolve a previously-recorded prediction. Outcome must be one of: "
        "'correct' (success criteria met), 'wrong' (failure criteria met), "
        "'ambiguous' (criteria didn't conclusively resolve — try to avoid "
        "this; ambiguous predictions don't feed calibration), or "
        "'expired_unresolvable' (data sources failed or claim depended on "
        "something now unmeasurable). Always include resolution_notes_md "
        "explaining how you evaluated."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prediction_id":            {"type": "integer"},
            "outcome":                  {"type": "string", "enum": list(VALID_OUTCOMES)},
            "resolution_notes_md":      {"type": "string"},
            "resolution_snapshot_ids":  {"type": "array"},
            "realized_value":           {
                "type": "object",
                "description": (
                    "Optional. The actual observed value/state at resolution "
                    "(e.g., {final_price: 81523, max_price: 82145, met_threshold: false})."
                ),
            },
        },
        "required": ["prediction_id", "outcome"],
    },
}


def _resolve_prediction(args: Dict[str, Any]) -> str:
    pid = args.get("prediction_id")
    if not isinstance(pid, int):
        return tool_error("prediction_id (int) required")

    outcome = args.get("outcome")
    if outcome not in VALID_OUTCOMES:
        return tool_error(f"outcome must be one of {VALID_OUTCOMES}")

    db = get_lifecycle_db()

    # Refuse double-resolve
    row = db.conn().execute(
        "SELECT id, resolved_at FROM predictions WHERE id = ?", (pid,),
    ).fetchone()
    if row is None:
        return tool_error(f"no prediction with id={pid}")
    if row["resolved_at"] is not None:
        return tool_error(
            f"prediction {pid} already resolved at "
            f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(row['resolved_at']))}"
        )

    notes = (args.get("resolution_notes_md") or "").strip()
    snap_ids = args.get("resolution_snapshot_ids")
    realized = args.get("realized_value")

    def _w(conn):
        conn.execute(
            "UPDATE predictions SET resolved_at = ?, outcome = ?, "
            "resolution_notes_md = ?, resolution_snapshot_ids_json = ?, "
            "realized_value_json = ? WHERE id = ?",
            (
                time.time(),
                outcome,
                notes or None,
                json.dumps(snap_ids) if snap_ids else None,
                json.dumps(realized) if realized else None,
                pid,
            ),
        )
        return pid

    db._execute_write(_w)
    return tool_result({
        "prediction_id": pid,
        "outcome": outcome,
    })


registry.register(
    name="resolve_prediction",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _resolve_prediction(args),
    description="Resolve a pending prediction (correct/wrong/ambiguous).",
    emoji="✅",
)
