"""Strategy lifecycle tools (toolset: strategy-write).

The ONLY mutation path for strategy files + their DB mirror (single code
path through trading.strategies.loader — file is truth, mirror synced
atomically). plutus-generate uses strategy_upsert at authoring (file at
birth); reflect uses set_status + update_weights at checkpoints.
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
        "missing_data_points (the self-extension hook). A NUMERICAL data "
        "point should also declare a normalizer — {name, params} from the "
        "deterministic library (linear_band {lo,hi,invert?} · distance_from "
        "{anchor,full_at,direction?} · zscore {cap?,invert?} · inside_band "
        "{lo,hi}) — encoding how ITS reading supports THIS thesis (e.g. "
        "mean-reversion RSI: linear_band lo=70 hi=20 reads oversold as "
        "support). Normalized DPs score deterministically every beat (no "
        "LLM, reproducible); leave narrative/contextual DPs (orderbook "
        "shape, candles structure, macro narrative) without a normalizer so "
        "the analyst scores them in context. Bad specs refuse at write time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "kebab-case slug"},
            "status": {"type": "string", "enum": ["test", "active", "dormant", "retired"]},
            "timescale": {"type": "string", "enum": ["intraday", "swing", "position"]},
            "mechanism_family": {"type": "string",
                                 "enum": ["momentum", "mean_reversion", "flow", "event", "narrative"]},
            "regime_applicability": {
                "type": "object",
                "description": (
                    "EXACTLY ONE cell: {<timescale>: {direction: [one], "
                    "volatility: [one]}} (macro instead of volatility at "
                    "position scale). Single-element lists only — a set is "
                    "refused. Several conditions means several hypotheses; "
                    "author one strategy per cell."),
            },
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
    # ONE CELL PER STRATEGY (2026-07-27). Refused in the writer, not merely
    # asked for in the brief: the (timescale x regime) cap has existed as
    # prose since the rebuild and was ignored for as long, so a rule that
    # matters lives where it can say no. A set-valued declaration produces one
    # book averaging several different trades, and the average describes none
    # of them — ema20-pivot-swing blended to -0.004 and met the retirement bar
    # while four of its five cells were positive. Several conditions means
    # several hypotheses: author them separately.
    wide = {ax: vals for ax, vals in (s.regime_applicability.get(s.timescale)
                                      or {}).items() if len(vals or []) > 1}
    if wide:
        return tool_error(
            f"regime_applicability declares a SET on {sorted(wide)} "
            f"({', '.join(f'{a}={v}' for a, v in wide.items())}) — a strategy "
            f"declares exactly ONE cell: one direction, one volatility (one "
            f"macro at position scale). A book spanning cells averages trades "
            f"that share no stop, target or horizon, and the average is "
            f"evidence about nothing. If the mechanism holds in several "
            f"conditions those are several hypotheses — author one strategy "
            f"per cell.")

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


def _strategy_status_sync(args: Dict[str, Any]) -> str:
    from trading.lifecycle.db import get_db
    from trading.lifecycle.graduation import sync_strategy_statuses

    changes = sync_strategy_statuses(get_db())
    return tool_result({"ok": True, "changes": changes,
                        "in_sync": not changes})


def _strategy_update_weights(args: Dict[str, Any]) -> str:
    from trading.conviction.engine import update_weights
    from trading.lifecycle.db import get_db
    from trading.strategies import loader
    from trading.strategies.files import parse_strategy, resolve_dp_key, strategies_dir

    name = args["name"]
    path = strategies_dir() / f"{name}.md"
    if not path.exists():
        return tool_error(f"no strategy file {path}")
    s = parse_strategy(path)
    # Resolve every dp_performance key against the DECLARED data points and
    # refuse anything unresolvable. update_weights ignores unknown keys by
    # design, so a bare-name key against a parameterized declaration used to
    # be a SILENT NO-OP reported as ok:true — 24 of the first 37 reflect
    # weight updates changed nothing (2026-07-16 audit).
    resolved: Dict[str, float] = {}
    problems = []
    for key, edge in (args.get("dp_performance") or {}).items():
        canonical = resolve_dp_key(s.data_points, key)
        if canonical is None:
            problems.append(f"{key!r} does not resolve to a declared data point")
        elif canonical in resolved:
            problems.append(f"{key!r} duplicates {canonical!r}")
        else:
            resolved[canonical] = edge
    if problems:
        return tool_error(
            f"weight update for {name!r} refused — no weights changed:\n  "
            + "\n  ".join(problems)
            + f"\nDeclared keys: {sorted(s.weights)}")
    new_weights = update_weights(s.weights, resolved)
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
    name="strategy_status_sync",
    toolset="strategy-write",
    schema={
        "name": "strategy_status_sync",
        "description": (
            "Deterministic test↔active sync from the graduation bar: "
            "tradeable test strategies promote to active, active strategies "
            "no longer clearing the bar demote to test. Runs automatically "
            "after every resolution batch; call it to force a pass or to see "
            "that the population is in sync. Dormancy and retirement stay "
            "judgment moves (strategy_set_status)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    handler=lambda args, **kw: _strategy_status_sync(args),
    description="Code-owned graduation bookkeeping: sync status to tradeable.",
    emoji="🎯",
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
