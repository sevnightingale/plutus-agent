"""Deterministic normalizers — numerical reading → 0–1 support score.

Each normalizer is a pure function with a registered id; the id is persisted
in support_scores.normalizer so every numerical score is auditable and
reproducible. Strategies reference normalizers in their Trigger sections;
plutus-predict applies them when scoring a setup.

The library is deliberately small and interpretable (no sigmoids — §8 moved
to straight, explainable mappings). New normalizers are one registered
function — the registry pattern IS the extension surface.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

_NORMALIZERS: Dict[str, Callable[..., float]] = {}


def register(name: str):
    def deco(fn):
        if name in _NORMALIZERS:
            raise ValueError(f"normalizer {name!r} already registered")
        _NORMALIZERS[name] = fn
        return fn
    return deco


def apply(name: str, value: float, **params) -> float:
    if name not in _NORMALIZERS:
        raise KeyError(
            f"unknown normalizer {name!r} — registered: {sorted(_NORMALIZERS)}"
        )
    score = float(_NORMALIZERS[name](float(value), **params))
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"normalizer {name!r} produced {score} outside [0, 1]")
    return round(score, 4)


def names() -> list:
    return sorted(_NORMALIZERS)


def spec_id(name: str, params: Optional[dict] = None) -> str:
    """Compact audit id for a declared spec: ``linear_band(hi=20,lo=70)``.

    Persisted in support_scores.normalizer so every deterministic score is
    reproducible from the row alone.
    """
    params = params or {}
    if not params:
        return name
    inner = ",".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{name}({inner})"


def validate_spec(name: str, params: Optional[dict] = None) -> list:
    """Problems with a declared normalizer spec (empty = valid).

    Checks registration and probes the function once (value=0.0) so missing/
    unknown params and degenerate configs (lo == hi) refuse at STRATEGY WRITE
    time, not at the first scoring beat.
    """
    if name not in _NORMALIZERS:
        return [f"unknown normalizer {name!r} — registered: {sorted(_NORMALIZERS)}"]
    try:
        apply(name, 0.0, **(params or {}))
    except TypeError as exc:
        return [f"normalizer {name!r}: bad params {sorted((params or {}))} ({exc})"]
    except ValueError as exc:
        return [f"normalizer {name!r}: invalid config ({exc})"]
    return []


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@register("linear_band")
def linear_band(value: float, *, lo: float, hi: float, invert: bool = False) -> float:
    """Linear map of [lo, hi] → [0, 1], clamped. invert flips direction.

    Example: RSI support for a mean-reversion long — linear_band(rsi, lo=70,
    hi=20) reads oversold as support (note lo > hi flips automatically).
    """
    if lo == hi:
        raise ValueError("linear_band needs lo != hi")
    score = _clamp01((value - lo) / (hi - lo))
    return 1.0 - score if invert else score


@register("distance_from")
def distance_from(value: float, *, anchor: float, full_at: float,
                  direction: str = "above") -> float:
    """Support grows with distance from an anchor in one direction.

    0.5 at the anchor, 1.0 at anchor ± full_at (per direction), 0.0 at the
    same distance the other way. direction ∈ above | below.
    """
    if full_at <= 0:
        raise ValueError("full_at must be > 0")
    delta = (value - anchor) / full_at
    if direction == "below":
        delta = -delta
    elif direction != "above":
        raise ValueError("direction must be 'above' or 'below'")
    return _clamp01(0.5 + delta / 2.0)


@register("zscore")
def zscore(value: float, *, cap: float = 3.0, invert: bool = False) -> float:
    """A z-scored reading → support. 0.5 at z=0, 1.0 at +cap, 0.0 at −cap."""
    if cap <= 0:
        raise ValueError("cap must be > 0")
    score = _clamp01(0.5 + value / (2.0 * cap))
    return 1.0 - score if invert else score


@register("inside_band")
def inside_band(value: float, *, lo: float, hi: float) -> float:
    """1.0 inside [lo, hi], decaying linearly to 0.0 at one band-width out."""
    if lo >= hi:
        raise ValueError("inside_band needs lo < hi")
    width = hi - lo
    if lo <= value <= hi:
        return 1.0
    dist = (lo - value) if value < lo else (value - hi)
    return _clamp01(1.0 - dist / width)
