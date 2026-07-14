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


def _epoch(v: Any) -> float:
    """Accept epoch seconds or an ISO-8601 string (the model sends both)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def _desk_status(conn) -> Dict[str, Any]:
    """One deterministic answer to 'is the desk broken or patient?' —
    tradeable gaps + fundable window (desk_gaps), HALT, the open position,
    and live trade-path readiness (honest error when unverifiable)."""
    from harness.constants import get_hermes_home
    from trading.lifecycle import queries

    out = queries.desk_gaps(conn)
    out["halt"] = (get_hermes_home() / "HALT").exists()
    out["open_position"] = queries.open_position(conn)
    try:
        from trading.integrations.hyperliquid.data_points import hl_trade_readiness
        r = hl_trade_readiness()
        out["readiness"] = {"ready": r.get("ready"), "reason": r.get("reason"),
                            "days_remaining": r.get("days_remaining")}
    except Exception as exc:
        out["readiness"] = {"ready": None,
                            "error": f"{type(exc).__name__}: {exc}"}
    return out


def _run_query(args: Dict[str, Any]) -> str:
    import re

    from trading.lifecycle import queries
    from trading.lifecycle.db import get_db

    conn = get_db()
    name = str(args.get("query", "")).strip()
    params = dict(args.get("params") or {})

    # Normalize the model's predictable variations instead of crashing on
    # them (each of these was a recurring daily failure in the logs):
    # "prediction 464" shorthand → prediction {prediction_id: 464}
    m = re.match(r"^prediction\s+#?(\d+)$", name)
    if m:
        name = "prediction"
        params.setdefault("prediction_id", int(m.group(1)))
    # name ↔ strategy_name (the underlying query functions are inconsistent)
    strategy = params.get("strategy_name", params.get("name"))

    _QUERIES = {
        "open_predictions": lambda: queries.open_predictions(
            conn, **{k: v for k, v in params.items() if k == "limit"}),
        "due_predictions": lambda: queries.due_predictions(conn),
        "prediction": lambda: queries.prediction(conn, int(params["prediction_id"])),
        "open_position": lambda: queries.open_position(conn),
        "recent_outcomes": lambda: queries.recent_outcomes(conn, **params),
        "calibration": lambda: queries.calibration(conn, **params),
        "strategy_stats": lambda: queries.strategy_stats(conn, strategy),
        "strategy_book": lambda: queries.strategy_book(conn),
        "strategy_expectancy": lambda: queries.strategy_expectancy(
            conn, **{("strategy_name" if k == "name" else k): v
                     for k, v in params.items()}),
        "best_actionable_prediction": lambda: queries.best_actionable_prediction(conn),
        "desk_status": lambda: _desk_status(conn),
        "strategies_by_timescale": lambda: queries.strategies_by_timescale(
            conn, params["timescale"], **{k: tuple(v) for k, v in params.items()
                                          if k == "statuses"}),
        "open_predictions_by_cell": lambda: queries.open_predictions_by_cell(conn),
        "mae_envelope": lambda: queries.mae_envelope(conn, **params),
        "support_score_performance": lambda: queries.support_score_performance(
            conn, strategy),
        "last_action_runs": lambda: queries.last_action_runs(conn),
        "timescale_mix": lambda: queries.timescale_mix(conn, _epoch(params["since_ts"])),
        "sizing_performance": lambda: queries.sizing_performance(
            conn, _conviction_render_fix_ts()),
    }
    if name not in _QUERIES:
        return tool_error(f"unknown query {name!r} — available: {sorted(_QUERIES)}")
    try:
        return tool_result({"query": name, "result": _QUERIES[name]()})
    except (KeyError, TypeError, ValueError) as exc:
        return tool_error(
            f"{name}: {type(exc).__name__}: {exc} — check this query's "
            f"params in the lifecycle_query tool description")


registry.register(
    name="lifecycle_query",
    toolset="lifecycle-read",
    schema={
        "name": "lifecycle_query",
        "description": (
            "Read the prediction lifecycle. query: open_predictions | "
            "due_predictions | "
            "prediction {prediction_id} | open_position | "
            "recent_outcomes {limit} | calibration {strategy_name?, "
            "regime_tag?, timescale?} | strategy_stats {name} | "
            "strategy_book | strategy_expectancy {strategy_name} (the "
            "profitability gate) | best_actionable_prediction (the fundable "
            "pick) | desk_status (broken vs patient: gaps to tradeable, "
            "HALT, readiness, fundable window) | "
            "strategies_by_timescale {timescale, statuses?} | "
            "open_predictions_by_cell | mae_envelope {strategy_name?, "
            "timescale?, regime_tag?, percentile?, population?, statistic?} | "
            "support_score_performance {strategy_name?} | last_action_runs | "
            "timescale_mix {since_ts} | sizing_performance."
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
