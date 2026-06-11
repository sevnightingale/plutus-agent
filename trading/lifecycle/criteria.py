"""Structured prediction criteria — validation and deterministic resolution.

The §17 contract: the entire learning loop rests on resolution ground truth,
and resolution belongs to the cheapest mind on the desk (ops). So success
criteria are STRUCTURED and machine-resolvable; prose claims are commentary.
``validate()`` is enforced at write time — a prediction whose criteria can't
be evaluated by code is refused, full stop.

Grammar:
    criteria := leaf | {"all": [criteria, ...]} | {"any": [criteria, ...]}
    leaf := {
        "data_point": "<registered DP name>",
        "params": {...},                       # optional DP params
        "op": "gte"|"lte"|"crosses_above"|"crosses_below"|"within_range"|"outside_range",
        "threshold": <number>,                 # for gte/lte/crosses_*
        "range": [lo, hi],                     # for *_range
        "baseline": {"value": <num>, "ts": <unix>}   # required for crosses_*
    }

The deadline is the prediction's own ``horizon_ts`` — never repeated here.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_OPS = ("gte", "lte", "crosses_above", "crosses_below", "within_range", "outside_range")
_RANGE_OPS = ("within_range", "outside_range")
_CROSS_OPS = ("crosses_above", "crosses_below")


def validate(
    criteria: Any,
    *,
    known_data_points: Optional[set] = None,
    resolvable_data_points: Optional[set] = None,
) -> list:
    """Return a list of problems (empty = valid).

    ``known_data_points``: registered DP names; when provided, unknown names
    are rejected (the wiring passes the live registry's names).
    ``resolvable_data_points``: the subset with a declared numeric_path; when
    provided, criteria leaves on perception-only data points are rejected —
    resolution could never extract a number from them, so accepting the
    prediction would only manufacture an expired_unresolvable weeks later.
    """
    problems: list = []
    _validate_node(criteria, problems, known_data_points,
                   resolvable_data_points, path="criteria")
    return problems


def _validate_node(node, problems, known_dps, resolvable_dps, path):
    if not isinstance(node, dict):
        problems.append(f"{path}: must be an object, got {type(node).__name__}")
        return
    if "all" in node or "any" in node:
        if len(node) != 1:
            problems.append(f"{path}: combinator object must have exactly one key")
            return
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            problems.append(f"{path}.{key}: must be a non-empty list")
            return
        for i, child in enumerate(children):
            _validate_node(child, problems, known_dps, resolvable_dps,
                           f"{path}.{key}[{i}]")
        return

    dp = node.get("data_point")
    if not dp or not isinstance(dp, str):
        problems.append(f"{path}: missing data_point")
    elif known_dps is not None and dp not in known_dps:
        problems.append(f"{path}: unknown data_point {dp!r} (not registered)")
    elif resolvable_dps is not None and dp not in resolvable_dps:
        problems.append(
            f"{path}: data_point {dp!r} is perception-only (no numeric_path) — "
            f"resolution cannot extract a number from it; pick a resolvable "
            f"data point (list_data_points shows resolvable: true)"
        )

    op = node.get("op")
    if op not in _OPS:
        problems.append(f"{path}: op must be one of {_OPS}, got {op!r}")
        return

    if op in _RANGE_OPS:
        rng = node.get("range")
        if (
            not isinstance(rng, (list, tuple)) or len(rng) != 2
            or not all(isinstance(v, (int, float)) for v in rng)
            or not rng[0] < rng[1]
        ):
            problems.append(f"{path}: {op} requires range [lo, hi] with lo < hi")
    else:
        if not isinstance(node.get("threshold"), (int, float)):
            problems.append(f"{path}: {op} requires a numeric threshold")

    if op in _CROSS_OPS:
        base = node.get("baseline")
        if (
            not isinstance(base, dict)
            or not isinstance(base.get("value"), (int, float))
            or not isinstance(base.get("ts"), (int, float))
        ):
            problems.append(
                f"{path}: {op} requires baseline {{value, ts}} from registration time"
            )


def validate_json(criteria_json: str, **kwargs) -> list:
    try:
        criteria = json.loads(criteria_json)
    except (TypeError, json.JSONDecodeError) as exc:
        return [f"criteria: not valid JSON ({exc})"]
    return validate(criteria, **kwargs)


# ───────────────────────────────────────────────────────────────────────────
# Resolution
# ───────────────────────────────────────────────────────────────────────────
#
# fetch(data_point, params) -> current numeric reading, or None on failure.
# fetch_extreme(data_point, params, since_ts) -> (low, high) over the window,
#     or None — used by crosses_* ops, which must consider the path travelled
#     since baseline.ts, not just the latest reading.
#
# Any leaf that cannot be evaluated (fetch failed, DP unregistered) makes the
# whole resolution 'unresolvable' — never guessed, never defaulted.

Fetch = Callable[[str, Optional[dict]], Optional[float]]
FetchExtreme = Callable[[str, Optional[dict], float], Optional[tuple]]


def resolve(
    criteria: Any,
    fetch: Fetch,
    fetch_extreme: Optional[FetchExtreme] = None,
) -> str:
    """Evaluate criteria → 'correct' | 'wrong' | 'unresolvable'."""
    result = _resolve_node(criteria, fetch, fetch_extreme)
    return result


def _resolve_node(node, fetch, fetch_extreme) -> str:
    if "all" in node:
        results = [_resolve_node(c, fetch, fetch_extreme) for c in node["all"]]
        if "unresolvable" in results:
            return "unresolvable"
        return "correct" if all(r == "correct" for r in results) else "wrong"
    if "any" in node:
        results = [_resolve_node(c, fetch, fetch_extreme) for c in node["any"]]
        if any(r == "correct" for r in results):
            return "correct"
        if "unresolvable" in results:
            return "unresolvable"
        return "wrong"

    dp = node["data_point"]
    params = node.get("params")
    op = node["op"]

    if op in _CROSS_OPS:
        if fetch_extreme is None:
            logger.warning("crosses_* op needs fetch_extreme; marking unresolvable")
            return "unresolvable"
        window = fetch_extreme(dp, params, float(node["baseline"]["ts"]))
        if window is None:
            return "unresolvable"
        low, high = window
        threshold = float(node["threshold"])
        hit = high >= threshold if op == "crosses_above" else low <= threshold
        return "correct" if hit else "wrong"

    reading = fetch(dp, params)
    if reading is None:
        return "unresolvable"
    value = float(reading)

    if op == "gte":
        ok = value >= float(node["threshold"])
    elif op == "lte":
        ok = value <= float(node["threshold"])
    elif op == "within_range":
        lo, hi = node["range"]
        ok = lo <= value <= hi
    else:  # outside_range
        lo, hi = node["range"]
        ok = value < lo or value > hi
    return "correct" if ok else "wrong"
