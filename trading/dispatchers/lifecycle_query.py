"""lifecycle_query — the desk's named read surface (toolset: lifecycle-read).

One tool, named queries — the same trading.lifecycle.queries functions the
spawn mechanism injects via ``reads:``, exposed for ad-hoc reads by main,
predict, ops, and reflect. Adding a query = one entry in _QUERIES.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result


def _run_query(args: Dict[str, Any]) -> str:
    from trading.lifecycle import queries
    from trading.lifecycle.db import get_db

    conn = get_db()
    name = args.get("query", "")
    params = args.get("params") or {}

    _QUERIES = {
        "open_predictions": lambda: queries.open_predictions(conn, **params),
        "due_predictions": lambda: queries.due_predictions(conn),
        "prediction": lambda: queries.prediction(conn, int(params["prediction_id"])),
        "open_position": lambda: queries.open_position(conn),
        "recent_outcomes": lambda: queries.recent_outcomes(conn, **params),
        "calibration": lambda: queries.calibration(conn, **params),
        "strategy_stats": lambda: queries.strategy_stats(conn, params["name"]),
        "strategy_book": lambda: queries.strategy_book(conn),
        "support_score_performance": lambda: queries.support_score_performance(
            conn, params.get("strategy_name")),
        "last_action_runs": lambda: queries.last_action_runs(conn),
        "timescale_mix": lambda: queries.timescale_mix(conn, float(params["since_ts"])),
        "sizing_performance": lambda: queries.sizing_performance(conn),
    }
    if name not in _QUERIES:
        return tool_error(f"unknown query {name!r} — available: {sorted(_QUERIES)}")
    try:
        return tool_result({"query": name, "result": _QUERIES[name]()})
    except (KeyError, TypeError, ValueError) as exc:
        return tool_error(f"{name}: {type(exc).__name__}: {exc}")


registry.register(
    name="lifecycle_query",
    toolset="lifecycle-read",
    schema={
        "name": "lifecycle_query",
        "description": (
            "Read the prediction lifecycle. query: open_predictions | "
            "due_predictions | prediction {prediction_id} | open_position | "
            "recent_outcomes {limit} | calibration {strategy_name?, "
            "regime_tag?, timescale?} | strategy_stats {name} | "
            "strategy_book | support_score_performance {strategy_name?} | "
            "last_action_runs | timescale_mix {since_ts} | sizing_performance."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: _run_query(args),
    description="Named reads over lifecycle.db v2.",
    emoji="🔎",
)
