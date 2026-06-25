"""lifecycle_query — the desk's named read surface (toolset: lifecycle-read).

One tool, named queries — the same trading.lifecycle.queries functions the
spawn mechanism injects via ``reads:``, exposed for ad-hoc reads by main,
predict, ops, and reflect. Adding a query = one entry in _QUERIES.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from harness.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def _conviction_render_fix_ts() -> Optional[float]:
    """Operator-set Issue-4 cutover marker (epoch seconds) from config.yaml.

    ``conviction_render_fix_ts`` may be a number (epoch seconds) or an ISO-8601
    string; reflect's ``sizing_performance`` excludes pre-fix blinded
    convictions from the band review. Absent/unparseable → None (no filtering;
    honest absence — the whole history is shown rather than silently cut)."""
    try:
        import yaml
        from harness.constants import get_config_path
        path = get_config_path()
        if not path.exists():
            return None
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        raw = cfg.get("conviction_render_fix_ts")
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str) and raw.strip():
            from datetime import datetime
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).timestamp()
    except Exception as exc:  # misconfigured marker must be loud, not silently filtering
        logger.warning("conviction_render_fix_ts unreadable (%s) — sizing review unfiltered", exc)
    return None


def _run_query(args: Dict[str, Any]) -> str:
    from trading.conviction.engine import GLOBAL_CONVICTION_THRESHOLD
    from trading.lifecycle import queries
    from trading.lifecycle.db import get_db

    conn = get_db()
    name = args.get("query", "")
    params = args.get("params") or {}

    _QUERIES = {
        "open_predictions": lambda: queries.open_predictions(conn, **params),
        "due_predictions": lambda: queries.due_predictions(conn),
        "unhandled_actionable": lambda: queries.unhandled_actionable(
            conn, float(params.get("min_age_s", 0)), GLOBAL_CONVICTION_THRESHOLD),
        "prediction": lambda: queries.prediction(conn, int(params["prediction_id"])),
        "open_position": lambda: queries.open_position(conn),
        "recent_outcomes": lambda: queries.recent_outcomes(conn, **params),
        "calibration": lambda: queries.calibration(conn, **params),
        "strategy_stats": lambda: queries.strategy_stats(conn, params["name"]),
        "strategy_book": lambda: queries.strategy_book(conn),
        "strategies_by_timescale": lambda: queries.strategies_by_timescale(
            conn, params["timescale"], **{k: tuple(v) for k, v in params.items()
                                          if k == "statuses"}),
        "open_predictions_by_cell": lambda: queries.open_predictions_by_cell(conn),
        "mae_envelope": lambda: queries.mae_envelope(conn, **params),
        "support_score_performance": lambda: queries.support_score_performance(
            conn, params.get("strategy_name")),
        "last_action_runs": lambda: queries.last_action_runs(conn),
        "timescale_mix": lambda: queries.timescale_mix(conn, float(params["since_ts"])),
        "sizing_performance": lambda: queries.sizing_performance(
            conn, _conviction_render_fix_ts()),
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
            "due_predictions | unhandled_actionable {min_age_s?} | "
            "prediction {prediction_id} | open_position | "
            "recent_outcomes {limit} | calibration {strategy_name?, "
            "regime_tag?, timescale?} | strategy_stats {name} | "
            "strategy_book | strategies_by_timescale {timescale, statuses?} | "
            "open_predictions_by_cell | mae_envelope {strategy_name?, "
            "timescale?, regime_tag?, percentile?} | support_score_performance "
            "{strategy_name?} | last_action_runs | timescale_mix {since_ts} | "
            "sizing_performance."
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
