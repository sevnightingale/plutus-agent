"""Strategy file primitives — frontmatter parsing, validation, writing.

File-is-truth doctrine: one flat ``~/.plutus-agent/strategies/`` directory,
frontmatter ``status`` IS the lifecycle stage, the DB row is a derived mirror
synced by loader.write_strategy / loader.set_status — never independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from harness.constants import get_hermes_home

VALID_STATUS = ("test", "active", "dormant", "retired")
VALID_TIMESCALE = ("intraday", "swing", "position")
VALID_FAMILY = ("momentum", "mean_reversion", "flow", "event", "narrative")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def strategies_dir() -> Path:
    """Resolved at call time (the 45a6cc9 lesson)."""
    return get_hermes_home() / "strategies"


@dataclass
class Strategy:
    name: str
    status: str
    timescale: str
    mechanism_family: str
    file_path: Path
    # One symbol per strategy (2026-08-08, the multi-asset turn) — the same
    # law as one cell: a hypothesis about BTC flow is not a hypothesis about
    # gold, and a book spanning symbols averages markets it never trades
    # together. Dex-qualified as the venue writes them ("xyz:GOLD").
    symbol: str = "BTC"
    parent_strategy: Optional[str] = None
    variant_tweak: Optional[str] = None
    regime_applicability: dict = field(default_factory=dict)
    data_points: list = field(default_factory=list)
    missing_data_points: list = field(default_factory=list)
    created: Optional[str] = None
    retired: Optional[str] = None
    retirement_reason: Optional[str] = None
    body_md: str = ""

    @property
    def weights(self) -> dict:
        return {
            _dp_key(dp): float(dp.get("weight", 0.0)) for dp in self.data_points
        }

    def body_section(self, heading: str) -> Optional[str]:
        """Extract a `# Heading` section from the body (case-insensitive)."""
        pattern = re.compile(
            rf"^#\s+{re.escape(heading)}\s*$(.*?)(?=^#\s+|\Z)",
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        m = pattern.search(self.body_md)
        return m.group(1).strip() if m else None


def _normalize_params(params: object) -> dict:
    """Coerce string-params ``"k=v,k2=v2"`` → ``{k: v, k2: v2}``.

    Strategy files sometimes store params as YAML strings (e.g.
    ``params: symbol=BTC``) instead of maps.  This defends every
    downstream reader — _dp_key, _fetch_reading, and any future
    iterator — from the ``TypeError: string indices must be integers``
    crash documented under BUG 1 (2026-06-16).
    """
    if isinstance(params, dict):
        return params
    if isinstance(params, str) and params.strip():
        result: dict = {}
        for part in params.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                result[k.strip()] = v.strip()
        return result
    return {}


def _dp_key(dp: dict) -> str:
    params = _normalize_params(dp.get("params"))
    if params:
        inner = ",".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{dp['name']}({inner})"
    return str(dp["name"])


_PAREN_KEY_RE = re.compile(r"^([A-Za-z0-9_]+)\((.*)\)$")
_SUFFIX_KEY_RE = re.compile(r"^(.+?)[_-](\d+[smhdw])$")


def resolve_dp_key(data_points: list, key: str) -> Optional[str]:
    """Resolve a free-form data-point reference to its declared canonical key.

    Agents render the same declared data point many ways — bare ``ta_vortex``,
    full ``ta_vortex(interval=4h,symbol=BTC)``, shorthand ``ta_vortex(4h)`` /
    ``ta_vortex_4h``. Free-form strings fragmented the calibration record
    (support_score_performance grouped them separately) and made bare-keyed
    weight updates silent no-ops. Resolution: exact canonical match; else
    match by name (unique → resolved); same-name declarations disambiguate
    on any parsed param hints (a bare ``(4h)`` reads as ``interval=4h``).

    Returns None when nothing — or more than one thing — matches. The caller
    decides whether that is a loud refusal (write paths) or a counted skip
    (the v5 migration): scoring only ever happens over declared data points,
    so an unresolvable key means the reference is broken, never that a new
    data point appeared.
    """
    key = (key or "").strip()
    if not key:
        return None
    canon = {}
    for dp in data_points:
        if isinstance(dp, dict) and dp.get("name"):
            canon.setdefault(_dp_key(dp), dp)
    if key in canon:
        return key

    base, hints = key, {}
    m = _PAREN_KEY_RE.match(key)
    if m:
        base = m.group(1)
        inner = m.group(2).strip()
        hints = _normalize_params(inner)
        if inner and not hints:  # positional shorthand: "ta_vortex(4h)"
            hints = {"interval": inner}
    candidates = {k: dp for k, dp in canon.items() if dp["name"] == base}
    if not candidates:
        sm = _SUFFIX_KEY_RE.match(key)  # "ta_ema_1d"
        if sm:
            base, hints = sm.group(1), {"interval": sm.group(2)}
            candidates = {k: dp for k, dp in canon.items() if dp["name"] == base}
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1 and hints:
        matched = [
            k for k, dp in candidates.items()
            if all(str(_normalize_params(dp.get("params")).get(h)) == str(v)
                   for h, v in hints.items())
        ]
        if len(matched) == 1:
            return matched[0]
    return None


def parse_strategy(path: Path) -> Strategy:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: no YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():]
    return Strategy(
        name=meta.get("name", path.stem),
        status=meta.get("status", "test"),
        timescale=meta.get("timescale", ""),
        mechanism_family=meta.get("mechanism_family", ""),
        file_path=path,
        symbol=str(meta.get("symbol") or "BTC"),
        parent_strategy=meta.get("parent_strategy"),
        variant_tweak=meta.get("variant_tweak"),
        regime_applicability=meta.get("regime_applicability") or {},
        data_points=meta.get("data_points") or [],
        missing_data_points=meta.get("missing_data_points") or [],
        created=str(meta.get("created")) if meta.get("created") else None,
        retired=str(meta.get("retired")) if meta.get("retired") else None,
        retirement_reason=meta.get("retirement_reason"),
        body_md=body,
    )


def validate_strategy(s: Strategy, *, known_data_points: Optional[set] = None) -> list:
    """Return problems (empty = valid). Enforced before any write."""
    problems = []
    if not _SLUG_RE.match(s.name):
        problems.append(f"name {s.name!r} is not a lowercase-kebab slug")
    if s.file_path.stem != s.name:
        problems.append(f"file stem {s.file_path.stem!r} != name {s.name!r}")
    if s.status not in VALID_STATUS:
        problems.append(f"status must be one of {VALID_STATUS}, got {s.status!r}")
    if s.timescale not in VALID_TIMESCALE:
        problems.append(f"timescale must be one of {VALID_TIMESCALE}")
    if s.mechanism_family not in VALID_FAMILY:
        problems.append(f"mechanism_family must be one of {VALID_FAMILY}")
    if not s.data_points:
        problems.append("data_points must be non-empty")
    total = 0.0
    for dp in s.data_points:
        if not isinstance(dp, dict) or "name" not in dp:
            problems.append(f"malformed data_point entry: {dp!r}")
            continue
        w = dp.get("weight")
        if not isinstance(w, (int, float)) or w < 0:
            problems.append(f"data_point {dp['name']!r}: weight must be ≥ 0")
        else:
            total += float(w)
        if (
            known_data_points is not None
            and dp["name"] not in known_data_points
            and dp["name"] not in s.missing_data_points
        ):
            problems.append(
                f"data_point {dp['name']!r} is not registered and not declared "
                "in missing_data_points (the self-extension hook)"
            )
        spec = dp.get("normalizer")
        if spec is not None:
            from trading.conviction import normalizers
            if not isinstance(spec, dict) or not spec.get("name"):
                problems.append(
                    f"data_point {dp['name']!r}: normalizer must be "
                    "{name, params?} — got " + repr(spec))
            else:
                problems.extend(
                    f"data_point {dp['name']!r}: {p}"
                    for p in normalizers.validate_spec(
                        spec["name"], spec.get("params")))
    if total > 1.0 + 1e-9:
        problems.append(f"weights sum to {total:.3f} (> 1.0)")
    if s.parent_strategy and not s.variant_tweak:
        problems.append("variants must state their one tweak (variant_tweak)")
    if not (s.body_section("Mechanism") or "").strip():
        problems.append(
            "Mechanism section is required — every hypothesis states WHY the "
            "edge should exist (who is on the other side)"
        )
    if not (s.body_section("Hypothesis") or "").strip():
        problems.append("Hypothesis section is required")
    return problems


def render_strategy(s: Strategy) -> str:
    """Serialize back to the canonical file format."""
    meta = {
        "name": s.name,
        "status": s.status,
        "symbol": s.symbol,
        "timescale": s.timescale,
        "mechanism_family": s.mechanism_family,
        "parent_strategy": s.parent_strategy,
        "variant_tweak": s.variant_tweak,
        "regime_applicability": s.regime_applicability,
        "data_points": s.data_points,
        "missing_data_points": s.missing_data_points,
        "created": s.created,
        "retired": s.retired,
        "retirement_reason": s.retirement_reason,
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n{s.body_md if s.body_md.startswith(chr(10)) else chr(10) + s.body_md}"
