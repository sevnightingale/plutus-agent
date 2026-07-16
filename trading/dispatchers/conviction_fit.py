"""conviction_fit — reflect's calibration harness (toolset: strategy-write).

Phase 1, REPORT-ONLY: builds the leak-free feature frame from resolved
predictions, runs the purged walk-forward evaluation against the honest
baselines (base rate · stored conviction · isotonic-recalibrated stored
conviction), writes the versioned JSON artifact, and returns the full
report. Nothing in the live scoring path consumes the artifact yet — the
switch to calibrated conviction is a later, evidence-gated step.

All arithmetic is code; reflect invokes, reads, narrates. Registered under
strategy-write so reflect and main carry it and predict does not.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "conviction_fit",
    "description": (
        "Train and evaluate the conviction calibration model over the resolved "
        "prediction record (report-only — the live scoring path is untouched). "
        "Runs a purged walk-forward: chronological folds where training rows "
        "must have RESOLVED before the fold opens. Reports out-of-sample Brier/"
        "log-loss for the model vs three baselines (strategy base rate, stored "
        "conviction, isotonic-recalibrated stored conviction), bootstrap CIs on "
        "the differences, and calibration tables. Writes a versioned JSON "
        "artifact under ~/.plutus-agent/models/conviction/. Narrate the verdict "
        "in your report; do not hand-copy numbers into weight updates."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folds": {"type": "integer",
                      "description": "Walk-forward folds over the tail 60% of history (default 5)."},
        },
    },
}


def _conviction_fit(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db

    try:
        from trading.calibration import features, fit
    except ImportError as exc:
        return tool_error(
            f"calibration stack unavailable ({exc}) — install the ml extra: "
            "pip install 'plutus-agent[ml]'")

    conn = get_db()
    try:
        frame = features.build_frame(conn)
        if frame.empty:
            return tool_error("no resolved strategy predictions to fit on")
        report = fit.walk_forward_report(frame, folds=int(args.get("folds") or 5))
        if "error" in report:
            return tool_error(f"conviction_fit: {report['error']}")
        saved = fit.fit_and_save(frame, report)
    except RuntimeError as exc:  # loud sklearn-missing path
        return tool_error(str(exc))
    report["artifact"] = saved
    # dataset.features is bulky for a chat report; keep the count, drop the list
    report["dataset"].pop("features", None)
    write.record_action_run(
        conn, action_type="conviction_fit", agent="plutus-reflect",
        session_name=session_id_from_context(),
        notes_md=(f"n={report['dataset']['n']} "
                  f"lr_brier={report['oos']['model_lr']['brier']} "
                  f"iso_brier={report['oos']['baseline_conviction_isotonic']['brier']} "
                  f"artifact={saved['artifact_path']}"))
    return tool_result(report)


registry.register(
    name="conviction_fit",
    toolset="strategy-write",
    schema=SCHEMA,
    handler=lambda args, **kw: _conviction_fit(args),
    description="Purged walk-forward fit/eval of the conviction calibration model (report-only).",
    emoji="📐",
)
