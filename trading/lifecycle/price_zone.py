"""Price-zone prediction math — pure, deterministic, no IO.

A prediction is a **% move from an entry reference price**: a *near edge*
(the correctness floor — the minimum move that still counts as correct) and a
*far edge* (the optimistic target). Both are SIGNED — bullish positive,
bearish negative — with ``|far| > |near|``. Direction is implied by the sign.

Resolution is path-based: a prediction is CORRECT the moment the *favorable
excursion* (the best move in the thesis direction, as a positive magnitude)
reaches the near edge; WRONG once the horizon passes without reaching it. The
*profit score* grades how far into ``[near, far]`` the favorable excursion ran
— 0 at the floor, 1.0 at the target, >1 on a blow-past.

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


def resolve_zone(near_edge_pct, far_edge_pct, mfe_pct, horizon_passed) -> Optional[str]:
    """``'correct'`` | ``'wrong'`` | ``None`` (still open).

    ``mfe_pct`` is the favorable-excursion magnitude (≥0 when price moved in the
    thesis direction) over ``[birth, now]``. Correct as soon as it reaches the
    near edge magnitude; wrong only once the horizon has passed without it.
    """
    if mfe_pct is not None and mfe_pct >= abs(near_edge_pct):
        return "correct"
    if horizon_passed:
        return "wrong"
    return None


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
