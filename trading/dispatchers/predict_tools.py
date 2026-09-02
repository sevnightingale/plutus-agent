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

A declared data point may carry a structured ``normalizer`` spec
(``{name, params}`` against trading.conviction.normalizers) — those DPs are
scored DETERMINISTICALLY from the fresh numeric reading and skipped in the
LLM prompt entirely (no halo, no saturation, reproducible from the recorded
spec id; a normalizer-declared DP with no numeric reading scores 'missing'
loudly, never falls back to the LLM). DPs without a spec — narrative and
contextual evidence — keep the LLM path, with normalizer guidance in the
strategy's prose Trigger section as before.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from harness.tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


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


def _extract_json_from_message(msg) -> Optional[dict]:
    """Try every field a thinking model might put its answer in.

    Checks in order: tool_calls → content → reasoning_content.
    Returns the first JSON dict found, or None.
    """
    # 1. Tool-call (provider forced-emit — rare for thinking models).
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        try:
            out = json.loads(tcs[0].function.arguments)
            if isinstance(out, dict):
                return out
        except Exception:
            pass

    # 2. Regular content field.
    content = getattr(msg, "content", None)
    if content and str(content).strip():
        try:
            out = _parse_json_loose(str(content))
            if isinstance(out, dict):
                return out
        except Exception:
            pass

    # 3. Reasoning / thinking output (deepseek-v4-flash and other thinking
    #    models sometimes put the final answer here, leaving content null).
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning and str(reasoning).strip():
        try:
            out = _parse_json_loose(str(reasoning))
            if isinstance(out, dict):
                return out
        except Exception:
            pass

    return None


# ── Light-tier reasoning budgets (deliberately GENEROUS — see below) ──────────
#
# The light tier (deepseek-v4-flash) is a REASONING model whose latency and
# token use are dominated by a variable-length thinking phase that runs BEFORE
# it emits the JSON answer into ``content``. Stingy limits here are a primary
# failure source, and in two distinct ways:
#
#   • A too-small TOKEN budget truncates the model mid-thought
#     (``finish_reason="length"``, empty ``content``) → surfaces as "no content".
#   • A too-short TIMEOUT kills a call the model would have completed → surfaces
#     as "Request timed out". The shared auxiliary client defaults these trading
#     tasks to a 30s timeout (``_DEFAULT_AUX_TIMEOUT``) — far too short for
#     reasoning generation — so we ALWAYS pass our own generous timeout here.
#     Neither failure is a dead/wrong model or a credential problem.
#
# Sizing intent:
#   LIGHT_CALL_TIMEOUT_S — a per-call CEILING, not the expected latency (which
#     is well under it). Generous so a single conviction/predict call never
#     times out under normal endpoint latency. Note: ``rescore`` calls
#     conviction sequentially per strategy, so the realistic batch cost is
#     ``n_strategies × actual_latency`` — keep the ceiling clear of the agent
#     ``gateway_timeout`` (1800s) so a slow batch can't get killed mid-sweep.
#   LIGHT_MAX_TOKENS / _CAP — total (reasoning + answer) budget, and the
#     grow-on-truncation ceiling. Generous, BUT tokens DRIVE latency: a bigger
#     budget is not strictly safer (it lets the model think longer, which costs
#     wall-clock). The answer JSON is tiny; the budget exists for the reasoning.
LIGHT_CALL_TIMEOUT_S = 300.0
LIGHT_MAX_TOKENS = 8000
LIGHT_MAX_TOKENS_CAP = 16000
# Deep-effort overrides: at xhigh/max the thinking phase alone can exceed the
# normal cap (25k+ reasoning tokens observed live at max), so the budget, cap,
# and timeout all scale up. 32768 is a live-verified accepted max_tokens.
DEEP_EFFORT_MAX_TOKENS = 16000
DEEP_EFFORT_MAX_TOKENS_CAP = 32768
DEEP_EFFORT_TIMEOUT_S = 600.0


#: `desk_efforts` key that pins the auxiliary calls independently of the seat.
AUX_EFFORT_KEY = "plutus-predict-aux"


def _seat_effort() -> Optional[str]:
    """Reasoning effort for predict's own auxiliary calls.

    predict_draft/conviction_score are where the seat's heavy reasoning
    actually happens, so by default they follow the plutus-predict pin in
    `desk_efforts` (falling back to the global agent.reasoning_effort) —
    otherwise a seat set to max would still draft and score at provider
    default.

    That inheritance is the right default and was also, on 2026-09-01, the
    largest line on the bill. These calls run several hundred times a day
    (one conviction score per strategy per rescore), and at `max` each one
    buys a 32k-token thinking budget; the seat's own turns run a couple of
    dozen times. Inheriting one number for both couples a rare deep turn to
    a very common cheap one. `plutus-predict-aux` in `desk_efforts` breaks
    that coupling when set; absent, nothing changes.
    """
    try:
        from harness.cli.config import load_config
        from harness.constants import VALID_REASONING_EFFORTS, resolve_seat_effort
        cfg = load_config()
        pinned = (cfg.get("desk_efforts") or {}).get(AUX_EFFORT_KEY)
        eff = str(pinned).strip().lower() if pinned else resolve_seat_effort(
            cfg, "plutus-predict").lower()
        return eff if eff in VALID_REASONING_EFFORTS + ("none",) else None
    except Exception:
        return None


def _structured_call(*, task: str, system: str, user: str, schema: dict,
                     max_tokens: int = LIGHT_MAX_TOKENS, max_retries: int = 2,
                     timeout: float = LIGHT_CALL_TIMEOUT_S) -> dict:
    """One light-model completion returning a JSON object matching ``schema``.

    Uses strict-JSON prompting + content parsing — NOT forced tool calls. The
    light tier is a thinking-mode model and providers like DeepSeek reject
    ``tool_choice`` in thinking mode ("Thinking mode does not support this
    tool_choice"), which would 400 the whole call. The schema is communicated
    in-prompt.

    Token budget and request timeout are the dominant failure modes here — see
    the ``LIGHT_*`` constants above for the full rationale. In short: the light
    tier is a REASONING model, so both the token budget (reasoning + answer) and
    the timeout must accommodate a variable-length thinking phase. We pass an
    explicit ``timeout`` because the shared auxiliary client otherwise applies a
    30s default that is far too short for reasoning generation.
    ``_extract_json_from_message`` also checks ``reasoning_content`` as a last
    resort for models that leave the answer there.
    """
    from harness.agent.auxiliary_client import call_llm

    sys_full = (
        system + "\n\nReturn ONLY a single JSON object — no prose, no markdown "
        "code fences — matching this JSON schema:\n" + json.dumps(schema)
    )

    effort = _seat_effort()
    extra_body = {"reasoning_effort": effort} if effort else None
    budget_cap = LIGHT_MAX_TOKENS_CAP
    budget = max_tokens
    if effort in ("xhigh", "max"):
        budget = max(budget, DEEP_EFFORT_MAX_TOKENS)
        budget_cap = DEEP_EFFORT_MAX_TOKENS_CAP
        timeout = max(timeout, DEEP_EFFORT_TIMEOUT_S)
    for attempt in range(max_retries):
        resp = call_llm(
            task=task, model=_light_model(),
            messages=[{"role": "system", "content": sys_full},
                      {"role": "user", "content": user}],
            max_tokens=budget, temperature=0, timeout=timeout,
            extra_body=extra_body,
        )
        choice = resp.choices[0]
        out = _extract_json_from_message(choice.message)
        if out is not None:
            return out
        # Reasoning model truncated before emitting the answer — give it more
        # room on the next attempt instead of burning a retry at the same budget.
        if getattr(choice, "finish_reason", None) == "length":
            budget = min(budget * 2, budget_cap)

    raise ValueError(f"structured call returned no content in {max_retries} attempts")


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


# Per-DP byte caps for the conviction read path (Issue 4). A renderer's output
# is bounded by design, so it gets a generous cap; a renderer-LESS value over
# the raw cap is NOT silently clamped to a blinded reading — it becomes a loud
# <TRUNCATED … NO RENDERER> sentinel and is scored 'missing'.
_RENDERED_READING_CAP = 1500
_RAW_READING_CAP = 2000


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
    """(numeric, reading-string, missing_reason) for one declared data point, fresh.

    ``missing_reason`` is None when the reading is usable; a short tag otherwise
    ("fetch-failed", "render-failed", "no-renderer-truncated"). An unusable
    reading MUST be scored 'missing' (honest absence) — never byte-clamped to a
    blinded value the LLM would score ~0.5 neutral (Issue 4). The renderer
    (registry ``compact_fn``) produces a small, signal-dense view; without one,
    a value over the raw cap becomes a loud sentinel instead of silent garbage.
    """
    from trading.perception.core import data_point_registry
    from trading.strategies.files import _normalize_params
    name = dp["name"]
    params = _normalize_params(dp.get("params"))
    try:
        entry = data_point_registry.lookup(name)
        value = entry.fn(**params) if entry.fn else None
        numeric = data_point_registry.extract_numeric(value, entry.numeric_path)
    except Exception as exc:  # loud-but-soft: the score becomes 'missing'
        return None, f"<fetch failed: {exc}>", "fetch-failed"

    # A data point that RETURNS an error payload (e.g. a ta_* indicator on
    # insufficient candles, or a preprocessor that caught its own raise) is
    # unusable — mark it 'missing' deterministically, exactly like a fetch
    # failure, so it is never scored ~0.5 neutral (the Issue 4 invariant holds
    # for the error-payload shape too, not just the sentinel strings).
    if isinstance(value, dict) and value.get("error"):
        return numeric, _compact(value, limit=_RENDERED_READING_CAP), "fetch-error"

    if entry.compact_fn is not None:
        try:
            rendered = entry.compact_fn(value)
        except Exception as exc:  # a throwing renderer → missing (safe)
            return numeric, f"<render failed dp={name}: {exc}>", "render-failed"
        return numeric, _compact(rendered, limit=_RENDERED_READING_CAP), None

    # No renderer: keep the raw value if it fits the cap; otherwise emit a loud
    # sentinel and force the score 'missing' rather than blind the LLM with a
    # mid-structure byte clamp.
    try:
        raw = json.dumps(value, default=str)
    except Exception:
        raw = str(value)
    if len(raw) <= _RAW_READING_CAP:
        return numeric, raw, None
    return (numeric,
            f"<TRUNCATED dp={name} kept=0B/{len(raw)}B — NO RENDERER>",
            "no-renderer-truncated")


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
        numeric, compact, missing_reason = _fetch_reading(dp)
        readings.append({"dp_key": files._dp_key(dp), "dp": dp, "numeric": numeric,
                         "reading": compact, "missing_reason": missing_reason})

    # DECLARED NORMALIZERS score deterministically — no LLM, no halo, no
    # saturation, reproducible from the recorded spec id. A DP that declares
    # a normalizer but yields no numeric reading is a broken declaration and
    # scores 'missing' loudly — it never silently falls back to the LLM.
    from trading.conviction import normalizers as norm_mod
    det_scored: List[engine.ScoredInput] = []
    det_support = []
    llm_readings = []
    for r in readings:
        spec = (r["dp"].get("normalizer") if isinstance(r["dp"], dict) else None)
        if spec is not None and not isinstance(spec, dict):
            # Legacy prose field (predict ad-libbed string "normalizers" for
            # weeks before the structured spec existed; nothing consumed
            # them). Score via the LLM exactly as those strategies always
            # were — but loudly: write-time validation refuses non-dict
            # specs, so the field dies at the file's next upsert.
            logger.warning("%s: ignoring non-dict legacy normalizer %r on %s",
                           strategy_name, spec, r["dp_key"])
            spec = None
        if r["missing_reason"]:
            continue  # unusable reading → missing on either path
        if not spec:
            llm_readings.append(r)
            continue
        if r["numeric"] is None:
            r["missing_reason"] = "normalizer-declared-but-no-numeric"
            continue
        try:
            sc = norm_mod.apply(spec["name"], r["numeric"],
                                **(spec.get("params") or {}))
        except Exception as exc:  # loud-but-soft: this DP scores 'missing'
            r["missing_reason"] = f"normalizer-failed: {exc}"
            continue
        nid = norm_mod.spec_id(spec["name"], spec.get("params"))
        det_scored.append(engine.ScoredInput(
            dp_key=r["dp_key"], score=sc, kind="numerical", normalizer=nid))
        det_support.append({
            "data_point": r["dp_key"], "score": sc, "kind": "numerical",
            "normalizer": nid, "reading_json": r["reading"],
            "reasoning_md": None, "weight": r["dp"].get("weight"),
        })

    scored: List[engine.ScoredInput] = list(det_scored)
    support_scores = list(det_support)
    if llm_readings:  # LLM scores ONLY the DPs without a declared normalizer
        system = (
            "You are a disciplined quantitative analyst. For each data point, score "
            "how strongly its CURRENT reading SUPPORTS this specific strategy's "
            "thesis. Reason strictly in the strategy's context (a reading that helps "
            "one thesis may hurt another). Use the exact dp_key given. Provide "
            "reasoning for every score.\n\n"
            "SCORE WITH FULL GRANULARITY — use the whole 0–1 range in steps of "
            "0.05. Anchors: 0.0 directly contradicts the thesis · 0.2 leans "
            "against · 0.35 slightly against · 0.5 genuinely mixed/neutral (never "
            "a default) · 0.65 slightly supportive · 0.8 clearly supportive · 0.9 "
            "strong · 1.0 the STRONGEST reading this data point could possibly "
            "produce for this thesis. 1.0 should be RARE: if you can imagine this "
            "data point reading more favorably, score below 1.0. Two readings of "
            "different strength must get different scores — coarse scoring "
            "(everything 0.5 or 1.0) destroys the calibration record downstream.\n\n"
            "A reading shown as '<fetch failed …>', '<render failed …>', or "
            "'<TRUNCATED … NO RENDERER>' is UNAVAILABLE, not neutral — OMIT it "
            "from your scores entirely; do NOT emit 0.5. Honest absence beats a "
            "guessed middle."
        )
        reading_lines = "\n".join(
            f"- {r['dp_key']}: numeric={r['numeric']} reading={r['reading']}"
            for r in llm_readings)
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

        by_key = {s.get("dp_key"): s
                  for s in (out.get("scores") or []) if isinstance(s, dict)}
        for r in llm_readings:  # already excludes missing + normalizer-scored
            key = r["dp_key"]
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
            # data_point carries the CANONICAL key (name(params)) — the bare name
            # fragmented the calibration record and collided same-name declarations
            # under the (prediction_id, data_point) uniqueness contract.
            support_scores.append({
                "data_point": key, "score": sc, "kind": kind,
                "reasoning_md": reasoning or None, "weight": r["dp"].get("weight"),
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


PERCEPTION_FRESHNESS_SCHEMA = {
    "name": "perception_freshness",
    "description": (
        "Before authoring a prediction, check that the perception data the "
        "strategy needs is fresh enough to ground a zone + invalidation on. "
        "Returns the strategy's declared data points that are STALE (present but "
        "older than max(cache budget, 30 min)) and, separately, MISSING ones "
        "(declared missing_data_points are excluded). If 'fresh' is false there "
        "are stale points — do NOT register on this strategy; return "
        "perception_stale to main so it re-runs perception, then retry. "
        "register_prediction enforces the stale check as a hard backstop."
    ),
    "parameters": {
        "type": "object",
        "properties": {"strategy_name": {"type": "string"}},
        "required": ["strategy_name"],
    },
}


def _perception_freshness(args: Dict[str, Any]) -> str:
    from trading.perception import freshness
    name = args.get("strategy_name")
    strat = _load_strategy(name)
    if strat is None:
        return tool_error(f"unknown strategy {name!r} — no file at strategies/{name}.md")
    missing_declared = set(strat.missing_data_points or [])
    declared = [dp for dp in (strat.data_points or [])
                if isinstance(dp, dict) and dp.get("name") not in missing_declared]
    flagged = freshness.stale_data_points(declared,
                                          timescale=strat.timescale or None)
    stale = [e for e in flagged if e["reason"] == "stale"]
    missing = [e for e in flagged if e["reason"] == "missing"]
    return tool_result({
        "strategy_name": name,
        "fresh": not stale,          # stale blocks; missing is informational
        "stale": stale,
        "missing": missing,
        "checked": len(declared),
    })


registry.register(
    name="perception_freshness",
    toolset="conviction",
    schema=PERCEPTION_FRESHNESS_SCHEMA,
    handler=lambda args, **kw: _perception_freshness(args),
    description="Check a strategy's perception data is fresh enough to author on.",
    emoji="⏱️",
)
