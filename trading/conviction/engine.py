"""Conviction engine v2 — normalized support-score weighted average.

A deliberate replacement of the v1 sigmoid + baseline-anchor engine (§8 of
rebuild-architecture.md): conviction is the weight-normalized aggregate of
per-data-point support scores on a shared 0–1 scale, comparable across
strategies, gated by ONE global threshold.

Two kinds of input score:
- numerical: produced here by deterministic normalizers (auditable by id)
- narrative: produced by plutus-predict's recorded LLM reasoning IN STRATEGY
  CONTEXT — the engine only aggregates them; it never invents them.

Missing or failed readings contribute NOTHING (excluded from both sums) and
are flagged — never silently defaulted to neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

GLOBAL_CONVICTION_THRESHOLD = 0.65  # calibration-review retunes with evidence

# Weight-update discipline (inherited, evidence-tested in v1)
WEIGHT_ALPHA = 0.05
WEIGHT_CAP = 0.30
WEIGHT_SUM_MAX = 1.0


@dataclass
class ScoredInput:
    dp_key: str                    # files._dp_key form: name(params)
    score: float                   # 0.0 invalidates … 1.0 supports
    kind: str                      # 'numerical' | 'narrative'
    normalizer: Optional[str] = None
    reasoning_md: Optional[str] = None
    reading_json: Optional[str] = None


@dataclass
class ConvictionResult:
    conviction: Optional[float]    # None when nothing could be scored
    contributions: List[dict] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    weights_used: Dict[str, float] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.conviction is not None and self.conviction >= GLOBAL_CONVICTION_THRESHOLD


def compute_conviction(weights: Dict[str, float], scores: List[ScoredInput]) -> ConvictionResult:
    """conviction = Σ(wᵢ·sᵢ) / Σ(wᵢ) over the data points that were scored.

    ``weights`` is the strategy's declared per-data-point weight map
    (files.Strategy.weights). Declared data points with no score are listed
    in ``missing``; scores for undeclared data points are an error (the
    strategy declares its perception needs — scoring outside them means the
    caller drifted).
    """
    by_key = {}
    for s in scores:
        if s.dp_key not in weights:
            raise ValueError(
                f"score for undeclared data point {s.dp_key!r} — the strategy "
                f"declares {sorted(weights)}"
            )
        if not 0.0 <= s.score <= 1.0:
            raise ValueError(f"{s.dp_key}: score {s.score} outside [0, 1]")
        if s.kind == "narrative" and not (s.reasoning_md or "").strip():
            raise ValueError(f"{s.dp_key}: narrative score without recorded reasoning")
        by_key[s.dp_key] = s

    num = 0.0
    den = 0.0
    contributions = []
    missing = []
    for dp_key, w in weights.items():
        s = by_key.get(dp_key)
        if s is None:
            missing.append(dp_key)
            continue
        num += w * s.score
        den += w
        contributions.append({
            "dp_key": dp_key, "score": s.score, "weight": w, "kind": s.kind,
            "normalizer": s.normalizer,
        })

    conviction = (num / den) if den > 0 else None
    return ConvictionResult(
        conviction=round(conviction, 4) if conviction is not None else None,
        contributions=contributions,
        missing=missing,
        weights_used=dict(weights),
    )


def update_weights(
    weights: Dict[str, float],
    dp_performance: Dict[str, float],
) -> Dict[str, float]:
    """One reflect-pass weight update.

    ``dp_performance``: per-data-point signed edge in [-1, 1] — e.g.
    (avg score on correct) − (avg score on wrong) from
    queries.support_score_performance. Positive = the data point's support
    predicted success.

    Discipline (inherited): per-step alpha 0.05, per-DP cap 0.30, total ≤ 1.0
    (renormalized down when the update would exceed it). Unknown data points
    in dp_performance are ignored — weights only exist for declared DPs.
    """
    out = dict(weights)
    for dp_key, edge in dp_performance.items():
        if dp_key not in out:
            continue
        edge = max(-1.0, min(1.0, float(edge)))
        current = out[dp_key]
        proposed = max(0.0, current + WEIGHT_ALPHA * edge)
        # The cap stops GROWTH past 0.30 — it never confiscates existing
        # weight (equal-weighted 3-DP strategies legitimately start at 0.33).
        if proposed > current:
            proposed = min(proposed, max(WEIGHT_CAP, current))
        out[dp_key] = proposed
    total = sum(out.values())
    if total > WEIGHT_SUM_MAX and total > 0:
        out = {k: v * (WEIGHT_SUM_MAX / total) for k, v in out.items()}
    return {k: round(v, 4) for k, v in out.items()}
