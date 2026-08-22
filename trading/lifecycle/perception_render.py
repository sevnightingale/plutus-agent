"""perception_render — the Readings zone of PERCEPTION.md, code-written.

The regime move (DB truth, markdown rendering) applied to perception:
readings live in the perception cache (written by every fetch), and the
``## Readings`` zone of PERCEPTION.md becomes a rendering of that cache —
grouped per symbol, compacted via each data point's registered
``compact_fn``. The perception agent keeps the narrative sections; it never
hand-writes the table again.

Failures stay failures: the sweep sidecar (``perception_sweep.json``)
records what a sweep could not fetch, and those rows render as FAILED —
never substituted, never silently absent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading.lifecycle.live_state import replace_zone
from trading.perception import cache as perception_cache
from trading.perception.core import data_point_registry
from trading.perception.panels import normalize_symbol

READINGS_ZONE = "Readings"
SIDECAR_FILENAME = "perception_sweep.json"


def _hermes_home() -> Path:
    from harness.constants import get_hermes_home
    return Path(get_hermes_home())


def sidecar_path() -> Path:
    return _hermes_home() / SIDECAR_FILENAME


def _panel_cache_keys() -> Optional[set]:
    """Canonical cache keys for the current watchlist panels.

    Returns None when the panel cannot be built (no db, no config) so the
    renderer falls back to the freshness bound alone — tests that seed a
    fake cache, and a desk that has not yet swept, still render.
    """
    try:
        from trading.perception import panels
        from trading.perception.cache import _canonical_key
        from trading.lifecycle.db import get_db

        from contextlib import closing

        watchlist = panels.watchlist_from_config()
        keys: set = set()
        # panels.panel_for is the SINGLE panel builder, shared with the
        # sweep. Built separately the two drift: on 2026-08-22 the sweep
        # gained the book's declared extras and this renderer did not, so
        # 242 readings were fetched, cached, and then filtered out of the
        # Readings zone — invisible to the agent that needed them.
        # closing(): get_db() hands back an unclosed handle and this runs
        # on every render.
        with closing(get_db()) as conn:
            tiers = panels.derive_tiers(conn, watchlist)
            for sym, tier in tiers.items():
                for name, params in panels.panel_for(conn, sym, tier):
                    keys.add(_canonical_key(name, params))
        for name, params in panels.global_panel():
            keys.add(_canonical_key(name, params))
        return keys
    except Exception:
        return None


def read_sidecar() -> Dict[str, Any]:
    try:
        return json.loads(sidecar_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _compact(name: str, value: Any) -> str:
    """Render a cached value small via the DP's compact_fn, else terse JSON."""
    try:
        entry = data_point_registry.lookup(name)
        if entry.compact_fn is not None:
            value = entry.compact_fn(value)
    except Exception:
        pass
    try:
        text = json.dumps(value, separators=(",", ":"), default=str)
    except Exception:
        text = str(value)
    text = text.replace("|", "\\|")
    # 120, not 160: seven full-tier symbols at 160 chars/cell still made the
    # Readings zone 69KB and PERCEPTION.md ride over the 80k cap (read_file
    # limit). Compact_fn already extracted the signal; the tail is JSON
    # furniture. History stays in the cache.
    return text if len(text) <= 120 else text[:117] + "..."


def _age_label(fetched_at: float, now: Optional[float] = None) -> str:
    age = max(0.0, (now or time.time()) - float(fetched_at))
    if age < 90:
        return f"{age:.0f}s"
    if age < 5400:
        return f"{age / 60:.0f}m"
    return f"{age / 3600:.1f}h"


def build_readings_body(now: Optional[float] = None) -> Dict[str, Any]:
    """Build the Readings zone body from the cache + sweep sidecar.

    Returns ``{"body": str, "rows": int, "failed_rows": int, "symbols": [...]}``.
    """
    state = perception_cache.read_perception_state()
    entries = state.get("data_points") or {}
    now = now or time.time()

    # Group cache entries by symbol param; entries without one are Global.
    # Cache keys are canonical — "name" or "name:{sorted-params-json}"
    # (see cache._canonical_key); entries carry value/source/fetched_at only.
    #
    # Two bounds, both earned:
    # - FRESHNESS: the cache accumulates every param variant ever fetched,
    #   and rendering all of it re-creates blackboard bloat (first live
    #   dry-run: 300 rows, 125KB). An entry renders only within GRACE× its
    #   own staleness budget.
    # - PANEL: strategy-specific lookback variants (hl_candles lookback=5
    #   vs the panel's 200) stay in the cache for conviction to fetch; they
    #   do not belong on the board. After the watchlist went seven-wide the
    #   freshness bound alone still produced 118KB of Readings and
    #   perception could not read_file its own blackboard.
    GRACE = 2.0
    MIN_WINDOW_S = 900.0
    panel_keys = _panel_cache_keys()
    if panel_keys is not None and not (panel_keys & set(entries)):
        panel_keys = None          # fake/test cache — freshness only
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for key, ent in sorted(entries.items()):
        if not isinstance(ent, dict) or "value" not in ent:
            continue
        if panel_keys is not None and key not in panel_keys:
            continue
        fetched_at = float(ent.get("fetched_at") or 0)
        budget = perception_cache.get_staleness_budget(key)
        if (now - fetched_at) > max(GRACE * budget, MIN_WINDOW_S):
            continue
        name, _, params_json = key.partition(":")
        try:
            params = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError:
            params = {}
        raw_sym = params.get("symbol")
        symbol = normalize_symbol(raw_sym) if raw_sym else "GLOBAL"
        groups.setdefault(symbol, []).append({
            "name": name,
            "params": {k: v for k, v in params.items() if k != "symbol"},
            "value": ent["value"],
            "fetched_at": float(ent.get("fetched_at") or 0),
        })

    sidecar = read_sidecar()
    failed_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for sym, info in (sidecar.get("symbols") or {}).items():
        for f in info.get("failed") or []:
            failed_by_symbol.setdefault(normalize_symbol(sym), []).append(f)
    for f in (sidecar.get("global") or {}).get("failed") or []:
        failed_by_symbol.setdefault("GLOBAL", []).append(f)

    lines: List[str] = [
        "<!-- TOOL-RENDERED by render_perception. Do not edit by hand — "
        "narrative belongs in the sections below this zone. -->",
        "",
    ]
    rows = failed_rows = 0
    symbols = sorted(groups.keys() | failed_by_symbol.keys(),
                     key=lambda s: (s == "GLOBAL", s))
    for symbol in symbols:
        lines.append(f"### {symbol}")
        lines.append("")
        lines.append("| Data point | Params | Value | Age | Source |")
        lines.append("|---|---|---|---|---|")
        for item in groups.get(symbol, []):
            params_txt = " ".join(
                f"{k}={v}" for k, v in sorted(item["params"].items())) or "—"
            try:
                src = data_point_registry.lookup(item["name"]).source
            except Exception:
                src = "?"
            lines.append(
                f"| {item['name']} | {params_txt} "
                f"| {_compact(item['name'], item['value'])} "
                f"| {_age_label(item['fetched_at'], now)} | {src} |")
            rows += 1
        for f in failed_by_symbol.get(symbol, []):
            params_txt = " ".join(
                f"{k}={v}" for k, v in sorted((f.get("params") or {}).items())
                if k != "symbol") or "—"
            lines.append(
                f"| {f.get('name')} | {params_txt} "
                f"| **FAILED** — {str(f.get('error'))[:120]} | — | — |")
            failed_rows += 1
        lines.append("")

    return {"body": "\n".join(lines).rstrip() + "\n", "rows": rows,
            "failed_rows": failed_rows,
            "symbols": [s for s in symbols if s != "GLOBAL"]}


def write_readings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Render and section-replace the Readings zone of PERCEPTION.md."""
    path = Path(path) if path else _hermes_home() / "PERCEPTION.md"
    built = build_readings_body()
    replaced = replace_zone(path, READINGS_ZONE, built["body"])
    narrative_line = None
    if path.exists():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("## Narrative") or line.startswith("## Notes"):
                narrative_line = i
                break
    return {
        "path": str(path),
        "replaced": bool(replaced),
        "rows": built["rows"],
        "failed_rows": built["failed_rows"],
        "symbols": built["symbols"],
        "bytes": len(built["body"]),
        "narrative_offset_line": narrative_line,
    }
