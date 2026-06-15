"""Price-zone prediction math — pure, deterministic, no IO.

A prediction is a **% move from an entry reference price**: a *near edge*
(the correctness floor — the minimum move that still counts as correct) and a
*far edge* (the optimistic target). Both are SIGNED — bullish positive,
bearish negative — with ``|far| > |near|``. Direction is implied by the sign.

Resolution is path-based and FLOOR-CORRECT: reaching the *near edge* (the best
move in the thesis direction, as a positive magnitude) LOCKS the win but keeps
the prediction open; reaching the *far edge* resolves it CORRECT early; if only
the near edge is reached, the horizon backstops a CORRECT resolution; the
horizon passing without the near edge is WRONG; an invalidation can fire only
BEFORE the near edge is reached. The *profit score* grades how far into
``[near, far]`` the favorable excursion ran — 0 at the floor, 1.0 at the
target, >1 on a blow-past.

These functions never touch the network or the DB; the caller supplies the
windowed favorable-excursion magnitude (from candles) and whether the horizon
has passed.
"""

from __future__ import annotations

from typing import Optional


def direction_of(near_edge_pct: float, far_edge_pct: float) -> int:
    """+1 bullish (zone above entry), -1 bearish (below) — the sign of the edges."""
    return 1 if far_edge_pct >= 0 else -1


def validate_zone(near_edge_pct, far_edge_pct) -> list:
    """Return a list of problems (empty = valid). Enforced at write time.

    The two edges must be nonzero, share a direction, and the far edge must lie
    strictly beyond the near edge (a real target past the correctness floor).
    """
    problems: list = []
    if not isinstance(near_edge_pct, (int, float)) or isinstance(near_edge_pct, bool):
        problems.append("near_edge_pct must be a number")
    if not isinstance(far_edge_pct, (int, float)) or isinstance(far_edge_pct, bool):
        problems.append("far_edge_pct must be a number")
    if problems:
        return problems
    if near_edge_pct == 0 or far_edge_pct == 0:
        problems.append("edges must be nonzero % moves")
    if (near_edge_pct > 0) != (far_edge_pct > 0):
        problems.append("near and far edges must share a direction (same sign)")
    if abs(far_edge_pct) <= abs(near_edge_pct):
        problems.append(
            "|far_edge_pct| must exceed |near_edge_pct| (the target lies beyond "
            "the correctness floor)"
        )
    return problems


def favorable_pct(direction: int, low_pct: float, high_pct: float) -> float:
    """Best move IN the thesis direction, as a positive magnitude.

    ``low_pct``/``high_pct`` are ``(extreme - entry_ref)/entry_ref * 100`` over
    the window. Bullish → how far up (``high_pct``); bearish → how far down
    (``-low_pct``). Positive when price moved favorably.
    """
    return high_pct if direction > 0 else -low_pct


def adverse_pct(direction: int, low_pct: float, high_pct: float) -> float:
    """Worst move AGAINST the thesis, signed ≤ 0 (the MAE)."""
    return low_pct if direction > 0 else -high_pct


def classify(
    near_edge_pct,
    far_edge_pct,
    mfe_pct,
    horizon_passed,
    *,
    invalidation_tripped: bool = False,
    near_already_reached: bool = False,
) -> str:
    """Resolution decision for the floor-correct / target-accelerated model.

    ``mfe_pct`` is the favorable-excursion magnitude (≥0 when price moved in the
    thesis direction) over ``[birth, now]``. Returns one of:

    - ``'target'``      — far edge reached → CORRECT, resolve early (a full
                          target beats a simultaneous invalidation).
    - ``'horizon'``     — horizon passed with the near edge reached → CORRECT at
                          the backstop (the move happened but never hit far).
    - ``'expired'``     — horizon passed without the near edge → WRONG.
    - ``'invalidated'`` — thesis broke BEFORE the near edge was reached → WRONG.
    - ``'mark_near'``   — near edge just reached (win LOCKED) but far not yet hit
                          and horizon not passed → stay open, stamp reached_near.
    - ``'open'``        — still developing (incl. near already stamped, awaiting
                          the far edge or the horizon).

    Once the near edge is reached the win is locked: invalidation can no longer
    flip it; only the far edge (early) or the horizon resolves it.
    """
    near_mag, far_mag = abs(near_edge_pct), abs(far_edge_pct)
    reached_near_now = mfe_pct is not None and mfe_pct >= near_mag
    reached_near = near_already_reached or reached_near_now
    reached_far = mfe_pct is not None and mfe_pct >= far_mag

    if reached_far:
        return "target"
    if horizon_passed:
        return "horizon" if reached_near else "expired"
    if reached_near:
        return "open" if near_already_reached else "mark_near"
    if invalidation_tripped:
        return "invalidated"
    return "open"


def profit_score(near_edge_pct, far_edge_pct, mfe_pct) -> Optional[float]:
    """Where the favorable excursion landed in ``[near, far]``, as a fraction.

    0 at the near edge (correct but marginal), 1.0 at the far edge, >1 on a
    blow-past. Clamped at 0 below the floor. ``None`` if ``mfe_pct`` is unknown.
    """
    if mfe_pct is None:
        return None
    near_mag, far_mag = abs(near_edge_pct), abs(far_edge_pct)
    span = far_mag - near_mag
    if span <= 0:
        return 0.0
    return max(0.0, (mfe_pct - near_mag) / span)
