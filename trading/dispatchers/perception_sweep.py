"""perception_sweep — batch panel fetches + code-rendered Readings.

Phase 1 of the multi-asset plan. Two tools:

- ``sweep_data_points`` — fetch a whole tiered panel (watchlist-aware) in
  ONE tool call. Results land in the perception cache and the snapshot
  table exactly as individual ``fetch_data_point`` calls would (shared
  fetch core), but the values never transit the agent's context — the
  return is a compact per-symbol summary. This is what makes a watchlist
  affordable: the LLM cost of a sweep no longer scales with panel size.
- ``render_perception`` — rewrite the ``## Readings`` zone of PERCEPTION.md
  from the cache (per-symbol tables, compact_fn-rendered, FAILED rows from
  the sweep sidecar). The perception agent keeps narrative sections only.

The sweep writes a sidecar (``perception_sweep.json``) recording what was
attempted and what failed — honest absence, machine-readable.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from harness.gateway.session_context import get_synthetic_kind
from trading.dispatchers._helpers import session_id_from_context
from harness.tools.registry import registry, tool_error, tool_result

SWEEP_SCHEMA = {
    "name": "sweep_data_points",
    "description": (
        "Fetch the standard perception panel for the whole watchlist in one "
        "call — tiered per symbol (full panel for actively-worked symbols, "
        "cheap pulse for the rest, global macro once). Values go to the "
        "perception cache and snapshot table; the return is a compact "
        "summary only. Follow with render_perception to rewrite the "
        "Readings zone. Use fetch_data_point for ad-hoc extras."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbols": {
                "type": "array", "items": {"type": "string"},
                "description": "Override the config watchlist (trading.watchlist).",
            },
            "tier_overrides": {
                "type": "object", "additionalProperties": {"type": "string"},
                "description": "Per-symbol tier override: {symbol: 'full'|'passive'}.",
            },
            "include_global": {
                "type": "boolean", "default": True,
                "description": "Fetch the symbol-independent macro panel too.",
            },
            "force_fresh": {
                "type": "boolean", "default": False,
                "description": "Bypass the perception cache for every fetch.",
            },
        },
    },
}

RENDER_SCHEMA = {
    "name": "render_perception",
    "description": (
        "Rewrite the '## Readings' zone of PERCEPTION.md from the perception "
        "cache — per-symbol tables, compact-rendered, FAILED rows included "
        "from the last sweep. The zone is tool-owned; hand-edits to it are "
        "overwritten. Narrative sections are untouched."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _sweep(args: Dict[str, Any]) -> str:
    from trading.perception import panels
    from trading.perception.fetch_core import fetch_and_snapshot
    from trading.lifecycle.db import get_db
    from trading.lifecycle.perception_render import sidecar_path

    t0 = time.time()
    force_fresh = bool(args.get("force_fresh", False))
    include_global = args.get("include_global", True)

    symbols = [str(s).strip().upper() for s in (args.get("symbols") or [])
               if str(s).strip()]
    if not symbols:
        symbols = panels.watchlist_from_config()

    tiers = panels.derive_tiers(get_db(), symbols)
    for sym, tier in (args.get("tier_overrides") or {}).items():
        if str(tier) in ("full", "passive"):
            tiers[str(sym).upper()] = str(tier)

    sid = session_id_from_context()
    kind = get_synthetic_kind()
    tier_label = kind if kind else "operator"

    def run_panel(panel: List) -> Dict[str, Any]:
        ok, failed = 0, []
        for name, params in panel:
            res = fetch_and_snapshot(
                name, params, force_fresh=force_fresh,
                session_id=sid, tier=tier_label)
            if res.get("ok"):
                ok += 1
            else:
                failed.append({"name": name, "params": params,
                               "error": res.get("error")})
        return {"ok": ok, "failed": failed}

    per_symbol: Dict[str, Any] = {}
    for sym in symbols:
        tier = tiers.get(sym, "passive")
        panel = (panels.full_panel(sym) if tier == "full"
                 else panels.passive_panel(sym))
        result = run_panel(panel)
        per_symbol[sym] = {"tier": tier, **result}

    global_result = run_panel(panels.global_panel()) if include_global \
        else {"ok": 0, "failed": []}

    sidecar = {
        "ts": time.time(),
        "symbols": per_symbol,
        "global": global_result,
    }
    try:
        sidecar_path().write_text(
            json.dumps(sidecar, indent=1, default=str) + "\n",
            encoding="utf-8")
    except Exception:
        pass  # the sweep result itself still reports; sidecar is best-effort

    total_ok = sum(v["ok"] for v in per_symbol.values()) + global_result["ok"]
    all_failed = [f["name"] for v in per_symbol.values() for f in v["failed"]]
    all_failed += [f["name"] for f in global_result["failed"]]

    return tool_result({
        "symbols": {s: {"tier": v["tier"], "ok": v["ok"],
                        "failed": [f["name"] for f in v["failed"]]}
                    for s, v in per_symbol.items()},
        "global_ok": global_result["ok"],
        "global_failed": [f["name"] for f in global_result["failed"]],
        "fetched": total_ok,
        "failed_total": len(all_failed),
        "duration_s": round(time.time() - t0, 1),
    })


def _render(args: Dict[str, Any]) -> str:
    from trading.lifecycle.perception_render import write_readings
    try:
        result = write_readings()
    except Exception as exc:
        return tool_error(f"render_perception failed: {exc}")
    if not result.get("replaced"):
        return tool_error(
            "render_perception: PERCEPTION.md or its '## Readings' zone is "
            "missing — not created silently. Restore the blackboard first.")
    return tool_result(result)


registry.register(
    name="sweep_data_points",
    toolset="perception",
    schema=SWEEP_SCHEMA,
    handler=lambda args, **kw: _sweep(args),
    description="Batch-fetch the tiered watchlist panel; compact summary out.",
    emoji="📡",
)

registry.register(
    name="render_perception",
    toolset="perception",
    schema=RENDER_SCHEMA,
    handler=lambda args, **kw: _render(args),
    description="Code-render the Readings zone of PERCEPTION.md from the cache.",
    emoji="🖨️",
)
