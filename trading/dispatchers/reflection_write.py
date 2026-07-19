"""record_reflection() — plutus-reflect's own writer (toolset: reflection-write).

The backward brain writes its own rows. Reflect already mutates lifecycle.db
directly through ``strategy-write`` (weights, status, retirement reason), so
its analytical record belongs on the same footing rather than being narrated
second-hand by main.

Why not route this through ``record()``: that tool hardcodes
``agent="plutus-main"`` on every row it writes, which is why ``observations``
carries no usable provenance — every row claims main authored it, whoever
actually reasoned it. A per-strategy, per-error-class judgement re-narrated by
another agent also loses the detail that makes it worth storing. And
``record()`` is a single tool whose kinds include ``forum_post``, so granting
it here would hand reflect a public Arena surface it has no business holding.

The split that follows: reflect writes its own structured rows here, and keeps
returning ``reflect_report`` to main, which journals the narrative summary as
it always has. Reflect owns its analytical record; main owns the day-log.
Neither paraphrases the other — and ``record()`` deliberately gains no
``reflection`` kind, so this table has exactly one writer.

Agent attribution is fixed to ``plutus-reflect``, mirroring how ``record()``
fixes ``plutus-main``: the toolset is single-agent by design, so the row says
who wrote it without trusting a model-supplied field.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

# Mirrors the validated vocabulary in trading.lifecycle.write.record_reflection.
# Kept here for the schema so the model sees the options; write.py remains the
# enforcing edge (a bad value raises there and surfaces as a tool error).
VALID_ERROR_CLASSES = (
    "forecast", "execution", "sizing", "regime", "variance", "process_violation",
)

RECORD_REFLECTION_SCHEMA = {
    "name": "record_reflection",
    "description": (
        "Record one reflect-pass judgement to lifecycle.db — the desk's "
        "queryable memory of WHY, not just what. Write one row per distinct "
        "finding (per strategy, per error class), not one summary blob per "
        "pass: these rows are read back by later reflect passes and by the "
        "operator, and a blob cannot be filtered. error_class is the "
        "diagnostic taxonomy for a miss — forecast (the read was wrong) | "
        "execution (the fill/stop was wrong) | sizing (the bet was wrong) | "
        "regime (the setup did not apply) | variance (right call, wrong "
        "outcome) | process_violation (the desk broke its own rules). Omit "
        "error_class when the reflection is not diagnosing a failure. "
        "Attach strategy_name and the prediction/position ids the judgement "
        "rests on so it can be traced back to its evidence."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The judgement (markdown). State the evidence, not just the verdict.",
            },
            "reflection_kind": {
                "type": "string",
                "description": (
                    "Free-form tag for the sort of pass this is, e.g. "
                    "weights | graduation | sizing | lesson | postmortem | population."
                ),
            },
            "error_class": {
                "type": "string",
                "enum": list(VALID_ERROR_CLASSES),
                "description": "Only when diagnosing a failure; omit otherwise.",
            },
            "strategy_name": {"type": "string"},
            "prediction_ids": {"type": "array", "items": {"type": "integer"}},
            "position_ids": {"type": "array", "items": {"type": "integer"}},
            "thesis_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["text"],
    },
}


def _record_reflection(args: Dict[str, Any]) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return tool_error("record_reflection requires text")

    error_class = args.get("error_class") or None
    if error_class is not None and error_class not in VALID_ERROR_CLASSES:
        return tool_error(
            f"error_class must be one of {VALID_ERROR_CLASSES} (got {error_class!r})")

    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import write
    from trading.lifecycle.db import get_db

    try:
        reflection_id = write.record_reflection(
            get_db(),
            text_md=text,
            agent="plutus-reflect",
            reflection_kind=args.get("reflection_kind") or None,
            error_class=error_class,
            strategy_name=args.get("strategy_name") or None,
            position_ids=args.get("position_ids") or (),
            thesis_ids=args.get("thesis_ids") or (),
            prediction_ids=args.get("prediction_ids") or (),
            session_name=session_id_from_context(),
        )
    except Exception as exc:
        return tool_error(f"record_reflection failed: {type(exc).__name__}: {exc}")

    return tool_result({"ok": True, "reflection_id": reflection_id})


registry.register(
    name="record_reflection",
    toolset="reflection-write",
    schema=RECORD_REFLECTION_SCHEMA,
    handler=lambda args, **kw: _record_reflection(args),
    description="Write one reflect-pass judgement to lifecycle.db (reflect's own record).",
    emoji="🔍",
)
