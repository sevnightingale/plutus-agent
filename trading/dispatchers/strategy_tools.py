"""Strategy lifecycle tools (toolset: strategy-write).

The ONLY mutation path for strategy files + their DB mirror (single code
path through trading.strategies.loader — file is truth, mirror synced
atomically). predict uses strategy_upsert at generation (file at birth);
reflect uses set_status + update_weights at checkpoints.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

UPSERT_SCHEMA = {
    "name": "strategy_upsert",
    "description": (
        "Create or rewrite a strategy file (file-at-birth: every hypothesis "
        "gets one, status=test). Frontmatter fields + body sections "
        "(Hypothesis and Mechanism are REQUIRED — state who is on the other "
        "side). Variants set parent_strategy AND variant_tweak (the one "
        "stated change). data_points declare perception needs with weights "
        "(sum ≤ 1.0); unregistered names must be listed in "
        "missing_data_points (the self-extension hook)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "kebab-case slug"},
            "status": {"type": "string", "enum": ["test", "active", "dormant", "retired"]},
            "timescale": {"type": "string", "enum": ["intraday", "swing", "position"]},
            "mechanism_family": {"type": "string",
                                 "enum": ["momentum", "mean_reversion", "flow", "event", "narrative"]},
            "regime_applicability": {"type": "object"},
            "data_points": {"type": "array", "items": {"type": "object"}},
            "missing_data_points": {"type": "array", "items": {"type": "string"}},
            "parent_strategy": {"type": "string"},
            "variant_tweak": {"type": "string"},
            "body": {"type": "string",
                     "description": "Markdown body with # Hypothesis / # Mechanism / # Trigger / # Invalidation template sections."},
        },
        "required": ["name", "timescale", "mechanism_family", "data_points", "body"],
    },
}


def _strategy_upsert(args: Dict[str, Any]) -> str:
    from trading.lifecycle.db import get_db
    from trading.perception.core import data_point_registry
    from trading.strategies import loader
    from trading.strategies.files import Strategy, strategies_dir

    name = args["name"]
    # Normalize string-params (e.g. "symbol=BTC") → dict before writing.
    # The LLM sometimes emits them as strings; _dp_key / _fetch_reading
    # handle them defensively, but written files should use the canonical
    # dict format so they never trip future readers.
    from trading.strategies.files import _normalize_params
    dps = args.get("data_points") or []
    for dp in dps:
        if isinstance(dp, dict) and "params" in dp:
            dp["params"] = _normalize_params(dp.get("params"))
    s = Strategy(
        name=name,
        status=args.get("status", "test"),
        timescale=args["timescale"],
        mechanism_family=args["mechanism_family"],
        file_path=strategies_dir() / f"{name}.md",
        parent_strategy=args.get("parent_strategy"),
        variant_tweak=args.get("variant_tweak"),
        regime_applicability=args.get("regime_applicability") or {},
        data_points=dps,
        missing_data_points=args.get("missing_data_points") or [],
        created=time.strftime("%Y-%m-%d"),
        body_md="\n" + args["body"].strip() + "\n",
    )
    known = {e.name for e in data_point_registry.list_all()} or None
    try:
        loader.write_strategy(s, get_db(), known_data_points=known)
    except (ValueError, FileNotFoundError) as exc:
        return tool_error(str(exc))
    return tool_result({"ok": True, "name": name, "status": s.status,
                        "file": str(s.file_path),
                        "missing_data_points": s.missing_data_points})


def _strategy_set_status(args: Dict[str, Any]) -> str:
    from trading.lifecycle.db import get_db
    from trading.strategies import loader

    try:
        s = loader.set_status(
            args["name"], args["status"], get_db(),
            reason=args.get("reason"),
        )
    except (ValueError, FileNotFoundError, KeyError) as exc:
        return tool_error(str(exc))
    return tool_result({"ok": True, "name": s.name, "status": s.status,
                        "retirement_reason": s.retirement_reason})


def _strategy_update_weights(args: Dict[str, Any]) -> str:
    from trading.conviction.engine import update_weights
    from trading.lifecycle.db import get_db
    from trading.strategies import loader
    from trading.strategies.files import parse_strategy, strategies_dir

    name = args["name"]
    path = strategies_dir() / f"{name}.md"
    if not path.exists():
        return tool_error(f"no strategy file {path}")
    s = parse_strategy(path)
    new_weights = update_weights(s.weights, args.get("dp_performance") or {})
    # write the updated weights back into the declared data_points
    from trading.strategies.files import _dp_key
    for dp in s.data_points:
        key = _dp_key(dp)
        if key in new_weights:
            dp["weight"] = new_weights[key]
    try:
        loader.write_strategy(s, get_db())
    except ValueError as exc:
        return tool_error(str(exc))
    return tool_result({"ok": True, "name": name, "weights": new_weights})


registry.register(
    name="strategy_upsert",
    toolset="strategy-write",
    schema=UPSERT_SCHEMA,
    handler=lambda args, **kw: _strategy_upsert(args),
    description="Create/rewrite a strategy file (file-at-birth) + sync the mirror.",
    emoji="📜",
)

registry.register(
    name="strategy_set_status",
    toolset="strategy-write",
    schema={
        "name": "strategy_set_status",
        "description": (
            "Change a strategy's lifecycle stage (test/active/dormant/"
            "retired). Graduation to active is reflect's call under the "
            "statistical bars; retirement requires a reason."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "status": {"type": "string", "enum": ["test", "active", "dormant", "retired"]},
                "reason": {"type": "string"},
            },
            "required": ["name", "status"],
        },
    },
    handler=lambda args, **kw: _strategy_set_status(args),
    description="Change a strategy's frontmatter status + sync the mirror.",
    emoji="🎓",
)

registry.register(
    name="strategy_update_weights",
    toolset="strategy-write",
    schema={
        "name": "strategy_update_weights",
        "description": (
            "Apply one reflect-pass weight update from per-data-point "
            "predictiveness (dp_performance: {dp_key: signed edge in "
            "[-1,1]}, e.g. avg-score-on-correct − avg-score-on-wrong). "
            "Alpha 0.05; the 0.30 cap stops growth, never confiscates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dp_performance": {"type": "object"},
            },
            "required": ["name", "dp_performance"],
        },
    },
    handler=lambda args, **kw: _strategy_update_weights(args),
    description="Reflect's weight retune through the single write path.",
    emoji="⚖️",
)
