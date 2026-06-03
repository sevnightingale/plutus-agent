"""Load strategy files (PLUTUS Stratum 1+: cross-session playbook bridge).

Strategies live as Markdown files under ``~/.plutus-agent/strategies/``,
organized by stage:

  strategies/
    active/         # full-size; meets calibration + edge gates
    trial/          # tiny size; building statistical power
    observation/    # NO trades, predictions only — gathering calibration
    proposed/       # just authored; not yet promoted to observation
    retired/        # moved aside with a reflection on why

Each file is a Markdown document with YAML frontmatter:

    ---
    name: arbiter-confluence
    stage: active
    authored_by: operator
    authored_at: 2026-05-08T02:25:00Z
    regime_applicability: [distribution_breakdown, distribution_rally]
    description: 4-tier confluence (trend/momentum/structure/sentiment)
    last_review: 2026-05-08T...
    performance:
      total_trades: 1
      hit_rate: 1.0
      ...
    ---

    # Body — the playbook itself

This loader injects a SUMMARY of active + trial strategies into the
session prompt at startup (frozen-snapshot semantics, like SOUL/WORLDVIEW).
The full strategy body is loaded on-demand via skill_view or read_file
when Plutus actually needs to apply the strategy.

Why a summary in the prompt? So that:
- Plutus knows what its current playbook is without a tool call
- Heartbeat routing can reference the active stance ("am I currently
  willing to trade Arbiter-confluence?")
- Pre-trade discipline ("does this thesis fit one of my active strategies?")
  doesn't require a separate query

Why NOT the full body? Token cost. Active strategies could be 5+ pages each.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from plutus_constants import get_hermes_home

logger = logging.getLogger(__name__)

STAGES = ("active", "trial", "observation", "proposed", "retired")
PROMPT_INJECTED_STAGES = ("active", "trial", "observation")


# V2: default strategy_conviction when missing from frontmatter. Existing
# strategy files written pre-V2 don't have the field; default to 0.5 so
# they keep behaving as before (multiplier = 20^0.5 ≈ 4.5x at thesis_conv=1.0,
# 1x at thesis_conv=0). New strategies authored post-V2 should set their own.
DEFAULT_STRATEGY_CONVICTION = 0.5


def _strategies_root() -> Path:
    return get_hermes_home() / "strategies"


def ensure_strategies_dir() -> Path:
    """Create the strategies/ directory tree if missing. Idempotent."""
    root = _strategies_root()
    for stage in STAGES:
        (root / stage).mkdir(parents=True, exist_ok=True)
    return root


def _parse_strategy_file(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a single STRATEGY.md file. Returns None on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("strategy file unreadable %s: %s", path, exc)
        return None
    if not text.startswith("---"):
        logger.warning("strategy file missing frontmatter: %s", path)
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("strategy file frontmatter malformed: %s", path)
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        logger.warning("strategy frontmatter YAML error in %s: %s", path, exc)
        return None
    body = parts[2].strip()
    return {
        "path": str(path),
        "filename": path.stem,
        "meta": meta,
        "body": body,
    }


def list_strategies(stage: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all strategy files, optionally filtered by stage."""
    root = _strategies_root()
    if not root.exists():
        return []
    stages = (stage,) if stage else STAGES
    out: List[Dict[str, Any]] = []
    for s in stages:
        stage_dir = root / s
        if not stage_dir.exists():
            continue
        for path in sorted(stage_dir.glob("*.md")):
            parsed = _parse_strategy_file(path)
            if parsed is not None:
                parsed["stage"] = s
                out.append(parsed)
    return out


def load_strategy(name: str) -> Optional[Dict[str, Any]]:
    """Find a strategy by name (the filename stem) across all stages."""
    for stage in STAGES:
        path = _strategies_root() / stage / f"{name}.md"
        if path.exists():
            parsed = _parse_strategy_file(path)
            if parsed is not None:
                parsed["stage"] = stage
                return parsed
    return None


def get_strategy_conviction(name: str) -> Optional[float]:
    """Read the ``strategy_conviction`` frontmatter field for a named strategy.

    V2: strategy_conviction is the slow-moving multiplier the strategy file
    declares (default 0.5 if missing). place_order's composite-conviction
    sizing reads this and multiplies it with the ephemeral thesis_conviction
    to get the position multiplier (notional = balance × 20^composite).

    Returns:
        Float in [0.0, 1.0]. None if the named strategy doesn't exist.
        Returns DEFAULT_STRATEGY_CONVICTION (0.5) if the strategy file
        exists but doesn't declare strategy_conviction.
    """
    strategy = load_strategy(name)
    if strategy is None:
        return None
    meta = strategy.get("meta") or {}
    raw = meta.get("strategy_conviction")
    if raw is None:
        return DEFAULT_STRATEGY_CONVICTION
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "strategy %r has non-numeric strategy_conviction=%r — using default %s",
            name, raw, DEFAULT_STRATEGY_CONVICTION,
        )
        return DEFAULT_STRATEGY_CONVICTION
    # Clamp to [0, 1] rather than reject — keeps autonomous edits robust.
    if value < 0.0 or value > 1.0:
        logger.warning(
            "strategy %r strategy_conviction=%s out of [0,1] — clamping",
            name, value,
        )
        value = max(0.0, min(1.0, value))
    return value


def build_strategy_prompt_block() -> Optional[str]:
    """Build the strategy-summary block injected into the session prompt.

    Compact: name, stage, regime_applicability, one-line description,
    and the latest performance summary. The full body is *not* included —
    Plutus loads it on-demand via skill_view / read_file when applying
    the strategy.

    Returns ``None`` when no strategies exist (e.g., before bootstrap).
    """
    strategies = [
        s for s in list_strategies()
        if s["stage"] in PROMPT_INJECTED_STAGES
    ]
    if not strategies:
        return None

    lines: List[str] = []
    lines.append("# CURRENT STRATEGY LIBRARY")
    lines.append("")
    lines.append(
        "These are the playbooks I currently have authored. The full body "
        "of each lives in ~/.plutus-agent/strategies/<stage>/<name>.md — "
        "load with read_file when actually applying. This prompt block is "
        "the index, not the playbook."
    )
    lines.append("")

    by_stage: Dict[str, List[Dict[str, Any]]] = {s: [] for s in PROMPT_INJECTED_STAGES}
    for s in strategies:
        by_stage[s["stage"]].append(s)

    stage_blurbs = {
        "active": "ACTIVE — full sizing; calibrated + edge confirmed",
        "trial": "TRIAL — tiny size; building statistical power",
        "observation": "OBSERVATION — predictions only, NO trades; gathering calibration",
    }

    for stage in PROMPT_INJECTED_STAGES:
        items = by_stage[stage]
        if not items:
            continue
        lines.append(f"## {stage_blurbs[stage]}")
        lines.append("")
        for s in items:
            meta = s["meta"]
            name = meta.get("name") or s["filename"]
            desc = (meta.get("description") or "").strip()
            regimes = meta.get("regime_applicability") or []
            perf = meta.get("performance") or {}
            n_trades = perf.get("total_trades") if isinstance(perf, dict) else None
            hit_rate = perf.get("hit_rate") if isinstance(perf, dict) else None
            avg_r = perf.get("avg_r") if isinstance(perf, dict) else None
            edge_decay = perf.get("edge_decay_flag") if isinstance(perf, dict) else None

            line = f"- **{name}**"
            if desc:
                line += f" — {desc}"
            lines.append(line)
            # V2: strategy_conviction is the slow-moving multiplier feeding
            # composite-conviction position sizing in place_order.
            strat_conv = meta.get("strategy_conviction")
            if strat_conv is not None:
                try:
                    lines.append(f"  - strategy_conviction: {float(strat_conv):.2f}")
                except (TypeError, ValueError):
                    lines.append(f"  - strategy_conviction: {strat_conv}")
            if regimes:
                lines.append(f"  - regime: {', '.join(regimes)}")
            stats_bits = []
            if n_trades is not None:
                stats_bits.append(f"trades={n_trades}")
            if hit_rate is not None:
                stats_bits.append(f"hit_rate={hit_rate:.2f}")
            if avg_r is not None:
                stats_bits.append(f"avg_r={avg_r:+.3f}")
            if edge_decay:
                stats_bits.append("⚠ EDGE DECAY")
            if stats_bits:
                lines.append(f"  - {' | '.join(stats_bits)}")
            n_pred = perf.get("predictions_count") if isinstance(perf, dict) else None
            pred_calib = perf.get("predictions_calibration") if isinstance(perf, dict) else None
            if n_pred:
                pred_bits = [f"predictions={n_pred}"]
                if pred_calib is not None:
                    pred_bits.append(f"calib={pred_calib:.2f}")
                lines.append(f"  - {' | '.join(pred_bits)}")
        lines.append("")

    lines.append(
        "DISCIPLINE: theses MUST tag strategy_name (one of the above OR a "
        "newly-authored strategy). Predictions SHOULD tag strategy_name when "
        "the prediction tests a strategy's hypothesis. Untagged theses make "
        "calibration unsliceable."
    )

    return "\n".join(lines).strip()
