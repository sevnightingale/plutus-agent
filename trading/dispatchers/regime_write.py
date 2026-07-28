"""record_regime() — plutus-regime's own writer (toolset: regime-write).

The regime gated every prediction the desk made and was kept as freeform
markdown that plutus-regime rewrote by hand with the ``file`` toolset. No code
could read it, so predict matched strategies against the tape inside its own
reasoning and every cell-aware surface built on 2026-07-27 stopped at the
prompt boundary. Third record this month held as text with no writer, after
reflections and capital_movements.

One call per timescale writes the row and re-renders REGIME.md's table from
the database. The agent keeps ``## Assessment notes`` — the reasoning behind a
flip is exactly what a renderer cannot reconstruct, and it is the most useful
thing on the board.

Validation lives here, as the desk's law requires: the label taxonomy is
closed, and an invented label now does more than blur a report. The
multiplicity premium is cell-scoped, so a label outside the vocabulary
silently changes whose bar a strategy is measured against.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

RECORD_REGIME_SCHEMA = {
    "name": "record_regime",
    "description": (
        "Record the regime at ONE timescale and re-render REGIME.md's table. "
        "Closed vocabulary: direction ∈ trending-up|trending-down|ranging, "
        "volatility ∈ compressed|normal|elevated, macro ∈ "
        "risk-on|neutral|risk-off and ONLY at position scale. Anything else "
        "is refused, not coerced. Call once per timescale you assessed; the "
        "latest row per timescale IS the live regime. Your assessment notes "
        "stay yours — write them into REGIME.md below the table."),
    "input_schema": {
        "type": "object",
        "properties": {
            "timescale": {"type": "string",
                          "enum": ["intraday", "swing", "position"]},
            "direction": {"type": "string",
                          "enum": ["trending-up", "trending-down", "ranging"]},
            "volatility": {"type": "string",
                           "enum": ["compressed", "normal", "elevated"]},
            "macro": {"type": "string",
                      "enum": ["risk-on", "neutral", "risk-off"],
                      "description": "position scale only"},
            "conviction": {"type": "number",
                           "description": "0-10, the number you already state"},
            "flipped": {"type": "boolean",
                        "description": "true when this label CHANGED this pass"},
            "notes_md": {"type": "string",
                         "description": "one line on why; the full narrative "
                                        "belongs in the board's notes section"},
        },
        "required": ["timescale", "direction", "volatility"],
    },
}


def _record_regime(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import regime_board, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    try:
        row_id = write.record_regime(
            conn,
            timescale=args["timescale"],
            direction=args["direction"],
            volatility=args["volatility"],
            macro=args.get("macro") or None,
            conviction=args.get("conviction"),
            flipped=bool(args.get("flipped")),
            notes_md=args.get("notes_md") or None,
            session_name=session_id_from_context(),
        )
    except ValueError as exc:            # closed-vocabulary refusal — say why
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error(f"record_regime failed: {type(exc).__name__}: {exc}")

    # Render immediately. A row that lands without the board following leaves
    # the desk reading a stale table while believing it fresh — precisely how
    # the Live State zone froze for a month — so the render failing is
    # reported, never swallowed.
    board = regime_board.write_board(conn)
    return tool_result({"ok": True, "regime_observation_id": row_id,
                        "board_rendered": board["ok"],
                        "board_error": board["error"]})


registry.register(
    name="record_regime",
    toolset="regime-write",
    schema=RECORD_REGIME_SCHEMA,
    handler=lambda args, **kw: _record_regime(args),
    description="Record the regime at one timescale; re-renders REGIME.md's table.",
    emoji="🧭",
)
