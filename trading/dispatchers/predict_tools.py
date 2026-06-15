"""Cheap-LLM scoring tools the predict agent orchestrates (and ops reuses).

The expensive predict agent spends its reasoning on strategy generation; the
per-strategy work is offloaded here to *scoped* cheap-model completions:

  - ``predict_draft``  — given a strategy + curated readings, propose a single
    price zone (near/far % + horizon). Read-only. [toolset: prediction-write]
  - ``conviction_score`` — SELF-FETCH a strategy's declared data points fresh,
    score each 0–1 in the strategy's context, then aggregate DETERMINISTICALLY
    via ``conviction.engine.compute_conviction`` with the declared weights.
    Read-only and reusable: predict calls it at birth, ops every beat to build
    the conviction trajectory. [toolset: conviction]

Each tool runs ONE focused completion on the light model tier, so a frontier
model never juggles many strategies at once — the scoping is what makes the
cheap model sufficient. Structured output is forced via a single function tool
where the provider supports it, with a strict-JSON fallback (Codex can't force).

Normalizer specs live in the strategy's prose Trigger section, not the
structured ``data_points`` declaration, so the cheap LLM applies them from
context; the engine only aggregates. (A future structured-normalizer field
could move numerical scoring fully deterministic.)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from harness.tools.registry import registry, tool_error, tool_result


# ── cheap-model plumbing ─────────────────────────────────────────────────────

def _light_model() -> Optional[str]:
    """The light-tier model id (config ``model.light`` → ``model.default``)."""
    try:
        from harness.cli.config import load_config
        mc = load_config().get("model") or {}
        return mc.get("light") or mc.get("default") or None
    except Exception:
        return None


def _parse_json_loose(text: str) -> dict:
    """Best-effort JSON out of a model's text reply (strip fences / prose)."""
    if not text:
        raise ValueError("empty content")
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in content: {text[:120]!r}")
    return json.loads(t[start:end + 1])


def _structured_call(*, task: str, system: str, user: str, schema: dict,
                     max_tokens: int = 1200) -> dict:
    """One light-model completion returning a JSON object matching ``schema``.

    Uses strict-JSON prompting + content parsing — NOT forced tool calls. The
    light tier is a thinking-mode model and providers like DeepSeek reject
    ``tool_choice`` in thinking mode ("Thinking mode does not support this
    tool_choice"), which would 400 the whole call. The schema is communicated
    in-prompt; if a provider happens to emit a tool call anyway we accept it.
    """
    from harness.agent.auxiliary_client import call_llm

    sys_full = (
        system + "\n\nReturn ONLY a single JSON object — no prose, no markdown "
        "code fences — matching this JSON schema:\n" + json.dumps(schema)
    )
    resp = call_llm(
        task=task, model=_light_model(),
        messages=[{"role": "system", "content": sys_full},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens, temperature=0,
    )
    msg = resp.choices[0].message
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        try:
            out = json.loads(tcs[0].function.arguments)
            if isinstance(out, dict):
                return out
        except Exception:
            pass
    content = getattr(msg, "content", None)
    if not content:
        raise ValueError("structured call returned no content")
    out = _parse_json_loose(content)
    if not isinstance(out, dict):
        raise ValueError(
            f"structured call did not return a JSON object (got {type(out).__name__})")
    return out


def _load_strategy(name: str):
    from trading.strategies import files
    if not name:
        return None
    path = files.strategies_dir() / f"{name}.md"
    if not path.exists():
        return None
    return files.parse_strategy(path)


def _compact(value: Any, limit: int = 400) -> str:
    try:
        s = json.dumps(value, default=str)
    except Exception:
        s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


# ── predict_draft ────────────────────────────────────────────────────────────

PREDICT_DRAFT_SCHEMA = {
    "name": "predict_draft",
    "description": (
        "Offload to a cheap model: given a strategy and curated current readings, "
        "draft ONE price-zone setup — a signed % move from current price with a "
        "near edge (correctness floor) and a far edge (target, |far|>|near|, same "
        "sign) plus a horizon. Returns {near_pct, far_pct, horizon_hours, "
        "rationale}; the caller passes these to register_prediction (which "
        "captures the entry price server-side)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "strategy_name": {"type": "string"},
            "symbol": {"type": "string"},
            "readings": {
                "type": "string",
                "description": "Curated data-point readings (text or JSON) the analyst selected.",
            },
            "regime": {"type": "string", "description": "Current regime context at the strategy's timescale."},
        },
        "required": ["strategy_name", "symbol"],
    },
}

_DRAFT_OUT_SCHEMA = {
    "type": "object",
    "properties": {
        "near_pct": {"type": "number", "description": "Correctness floor, signed % move."},
        "far_pct": {"type": "number", "description": "Target, signed % move, same sign, |far|>|near|."},
        "horizon_hours": {"type": "number", "description": "Hours until the zone must be touched (≤720)."},
        "rationale": {"type": "string"},
    },
    "required": ["near_pct", "far_pct", "horizon_hours", "rationale"],
}


def _predict_draft(args: Dict[str, Any]) -> str:
    strat = _load_strategy(args.get("strategy_name"))
    if strat is None:
        return tool_error(f"unknown strategy {args.get('strategy_name')!r}")
    symbol = args.get("symbol")
    if not symbol:
        return tool_error("symbol is required")
    system = (
        "You are a disciplined trading analyst. Propose exactly ONE price-zone "
        "forecast for the given symbol that expresses this strategy's thesis: a "
        "signed % move from the CURRENT price (bullish positive, bearish "
        "negative), with a near edge (the smallest move that still confirms the "
        "thesis) and a far edge (the realistic target, |far|>|near|, same sign), "
        "and a horizon in hours (≤720). Do not set a stop — that is the trade "
        "agent's job. Be realistic: the zone should reflect the move you actually "
        "expect at this timescale, not a hope."
    )
    user = (
        f"# Strategy: {strat.name} ({strat.timescale}/{strat.mechanism_family})\n"
        f"## Hypothesis\n{strat.body_section('Hypothesis') or '(none)'}\n"
        f"## Mechanism\n{strat.body_section('Mechanism') or '(none)'}\n"
        f"## Trigger\n{strat.body_section('Trigger') or '(none)'}\n\n"
        f"Symbol: {symbol}\nRegime: {args.get('regime') or '(unspecified)'}\n"
        f"Curated readings:\n{args.get('readings') or '(none provided)'}\n"
    )
    try:
        out = _structured_call(task="predict_draft", system=system, user=user,
                               schema=_DRAFT_OUT_SCHEMA)
    except Exception as exc:
        return tool_error(f"predict_draft failed: {exc}")
    return tool_result({
        "strategy_name": strat.name, "symbol": symbol,
        "near_pct": out.get("near_pct"), "far_pct": out.get("far_pct"),
        "horizon_hours": out.get("horizon_hours"), "rationale": out.get("rationale"),
    })


# ── conviction_score ─────────────────────────────────────────────────────────

CONVICTION_SCORE_SCHEMA = {
    "name": "conviction_score",
    "description": (
        "Offload to a cheap model: score how strongly each of a strategy's "
        "DECLARED data points supports its thesis RIGHT NOW. Self-fetches the "
        "declared data points fresh, scores each 0–1 in the strategy's context "
        "(reasoning recorded), then aggregates to a single conviction "
        "deterministically with the strategy's declared weights. Reusable: "
        "predict calls it at registration, ops every beat to track the "
        "conviction trajectory. Returns {conviction, support_scores, missing}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "strategy_name": {"type": "string"},
            "regime": {"type": "string"},
        },
        "required": ["strategy_name"],
    },
}

_SCORE_OUT_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dp_key": {"type": "string", "description": "Exact data-point key as given."},
                    "score": {"type": "number", "description": "0.0 contradicts … 1.0 strongly supports."},
                    "kind": {"type": "string", "enum": ["numerical", "narrative"]},
                    "reasoning": {"type": "string", "description": "Why, in this strategy's context."},
                },
                "required": ["dp_key", "score", "reasoning"],
            },
        },
    },
    "required": ["scores"],
}


def _fetch_reading(dp: dict):
    """(numeric, compact-reading-string) for one declared data point, fresh."""
    from trading.perception.core import data_point_registry
    name = dp["name"]
    params = dp.get("params") or {}
    try:
        entry = data_point_registry.lookup(name)
        value = entry.fn(**params) if entry.fn else None
        numeric = data_point_registry.extract_numeric(value, entry.numeric_path)
        return numeric, _compact(value)
    except Exception as exc:  # loud-but-soft: the score becomes 'missing'
        return None, f"<fetch failed: {exc}>"


def score_strategy(strategy_name: str, regime: Optional[str] = None) -> dict:
    """Self-fetch + cheap-LLM-score + deterministically aggregate a strategy's
    conviction RIGHT NOW. Shared core: the conviction_score tool wraps it, and
    ops's rescore loop calls it to build the conviction trajectory.

    Returns ``{strategy_name, conviction, support_scores, missing}``.
    Raises ValueError on an unknown / data-point-less strategy or a scoring
    failure (the caller decides whether to skip or surface).
    """
    from trading.conviction import engine
    from trading.strategies import files

    strat = _load_strategy(strategy_name)
    if strat is None:
        raise ValueError(f"unknown strategy {strategy_name!r}")
    if not strat.data_points:
        raise ValueError(f"strategy {strat.name!r} declares no data points")

    readings = []
    for dp in strat.data_points:
        numeric, compact = _fetch_reading(dp)
        readings.append({"dp_key": files._dp_key(dp), "numeric": numeric, "reading": compact})

    system = (
        "You are a disciplined quantitative analyst. For each data point, score "
        "how strongly its CURRENT reading SUPPORTS this specific strategy's "
        "thesis: 0.0 = contradicts, 0.5 = neutral, 1.0 = strongly supports. "
        "Reason strictly in the strategy's context (a reading that helps one "
        "thesis may hurt another). Use the exact dp_key given. Provide reasoning "
        "for every score."
    )
    reading_lines = "\n".join(
        f"- {r['dp_key']}: numeric={r['numeric']} reading={r['reading']}" for r in readings)
    user = (
        f"# Strategy: {strat.name} ({strat.timescale}/{strat.mechanism_family})\n"
        f"## Hypothesis\n{strat.body_section('Hypothesis') or '(none)'}\n"
        f"## Mechanism\n{strat.body_section('Mechanism') or '(none)'}\n"
        f"## Trigger\n{strat.body_section('Trigger') or '(none)'}\n\n"
        f"Regime: {regime or '(unspecified)'}\n"
        f"Score these data points:\n{reading_lines}\n"
    )
    out = _structured_call(task="conviction_score", system=system, user=user,
                           schema=_SCORE_OUT_SCHEMA)

    by_key = {s.get("dp_key"): s for s in (out.get("scores") or []) if isinstance(s, dict)}
    scored: List[engine.ScoredInput] = []
    support_scores = []
    for dp in strat.data_points:
        key = files._dp_key(dp)
        s = by_key.get(key)
        if not s:
            continue
        try:
            sc = max(0.0, min(1.0, float(s.get("score"))))
        except (TypeError, ValueError):
            continue
        kind = s.get("kind") or "narrative"
        reasoning = (s.get("reasoning") or "").strip()
        if kind == "narrative" and not reasoning:
            continue  # unreasoned narrative score → treat as missing, never guess
        scored.append(engine.ScoredInput(
            dp_key=key, score=sc, kind=kind, reasoning_md=reasoning or None))
        support_scores.append({
            "data_point": dp["name"], "score": sc, "kind": kind,
            "reasoning_md": reasoning or None, "weight": dp.get("weight"),
        })

    result = engine.compute_conviction(strat.weights, scored)
    return {
        "strategy_name": strat.name,
        "conviction": result.conviction,
        "support_scores": support_scores,
        "missing": result.missing,
    }


def _conviction_score(args: Dict[str, Any]) -> str:
    try:
        result = score_strategy(args.get("strategy_name"), args.get("regime"))
    except ValueError as exc:
        return tool_error(str(exc))
    except Exception as exc:  # noqa: BLE001 — scoring/LLM failure, surface it
        return tool_error(f"conviction_score failed: {exc}")
    return tool_result(result)


registry.register(
    name="predict_draft",
    toolset="prediction-write",
    schema=PREDICT_DRAFT_SCHEMA,
    handler=lambda args, **kw: _predict_draft(args),
    description="Cheap-model draft of a price-zone setup for a strategy.",
    emoji="✍️",
)

registry.register(
    name="conviction_score",
    toolset="conviction",
    schema=CONVICTION_SCORE_SCHEMA,
    handler=lambda args, **kw: _conviction_score(args),
    description="Cheap-model conviction score over a strategy's declared data points.",
    emoji="🎯",
)
