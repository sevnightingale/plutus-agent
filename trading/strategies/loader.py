"""Strategy loader — status-gated context, write-through DB mirror sync.

The single code path for strategy lifecycle changes: every write goes through
``write_strategy`` / ``set_status``, which edit the file AND sync the mirror
row in the same call. Status gates context: only test+active strategies reach
prediction context; dormant stays on disk for regime-flip rotation; retired
is graveyard (reflect-only).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from trading.strategies.files import (
    Strategy,
    parse_strategy,
    render_strategy,
    strategies_dir,
    validate_strategy,
)

logger = logging.getLogger(__name__)

LIVE_STATUSES = ("test", "active")


def load_strategies(
    statuses: tuple = LIVE_STATUSES, base_dir: Optional[Path] = None
) -> list:
    """Parse every strategy file with a matching status. Malformed files are
    reported loudly and skipped — never silently dropped."""
    base = base_dir if base_dir is not None else strategies_dir()
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.glob("*.md")):
        try:
            s = parse_strategy(path)
        except Exception as exc:
            logger.error("strategy file %s failed to parse: %s", path.name, exc)
            continue
        if s.status in statuses:
            out.append(s)
    return out


def strategy_context_block(base_dir: Optional[Path] = None) -> str:
    """The prompt-injection summary for predict/main — live strategies only.

    Dead strategies never pollute prediction context (locked §14.4).
    """
    live = load_strategies(LIVE_STATUSES, base_dir)
    if not live:
        return (
            "## Strategy book\n\n(no live strategies — predictions only, NO "
            "trades; generation fills the slots)\n"
        )
    lines = ["## Strategy book\n"]
    for s in live:
        regime = json.dumps(s.regime_applicability, sort_keys=True)
        dps = ", ".join(f"{k}:{w:.2f}" for k, w in s.weights.items())
        hypothesis = (s.body_section("Hypothesis") or "").strip().replace("\n", " ")
        lines.append(
            f"### {s.name} [{s.status}] {s.timescale}/{s.mechanism_family}\n"
            f"- regime: {regime}\n"
            f"- weights: {dps}\n"
            + (f"- parent: {s.parent_strategy} (tweak: {s.variant_tweak})\n"
               if s.parent_strategy else "")
            + (f"- missing data points: {', '.join(s.missing_data_points)}\n"
               if s.missing_data_points else "")
            + f"- hypothesis: {hypothesis}\n"
        )
    return "\n".join(lines)


def write_strategy(
    s: Strategy,
    conn: sqlite3.Connection,
    *,
    known_data_points: Optional[set] = None,
) -> None:
    """Validate, write the file, and sync the DB mirror — one call, one truth."""
    problems = validate_strategy(s, known_data_points=known_data_points)
    if problems:
        raise ValueError("strategy refused:\n  " + "\n  ".join(problems))
    s.file_path.parent.mkdir(parents=True, exist_ok=True)
    s.file_path.write_text(render_strategy(s), encoding="utf-8")
    _sync_mirror(s, conn)


def set_status(
    name: str,
    status: str,
    conn: sqlite3.Connection,
    *,
    reason: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Strategy:
    """Change a strategy's lifecycle stage (frontmatter edit + mirror sync)."""
    base = base_dir if base_dir is not None else strategies_dir()
    path = base / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no strategy file {path}")
    s = parse_strategy(path)
    s.status = status
    if status == "retired":
        s.retired = time.strftime("%Y-%m-%d")
        s.retirement_reason = reason
    path.write_text(render_strategy(s), encoding="utf-8")
    _sync_mirror(s, conn)
    return s


def _sync_mirror(s: Strategy, conn: sqlite3.Connection) -> None:
    now = time.time()
    conn.execute(
        """INSERT INTO strategies (
            name, file_path, status, symbol, timescale, mechanism_family,
            parent_strategy, hypothesis_md, mechanism_md,
            regime_applicability_json, data_points_json, created_at, updated_at,
            retired_at, retirement_reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            file_path=excluded.file_path,
            status=excluded.status,
            symbol=excluded.symbol,
            timescale=excluded.timescale,
            mechanism_family=excluded.mechanism_family,
            parent_strategy=excluded.parent_strategy,
            hypothesis_md=excluded.hypothesis_md,
            mechanism_md=excluded.mechanism_md,
            regime_applicability_json=excluded.regime_applicability_json,
            data_points_json=excluded.data_points_json,
            updated_at=excluded.updated_at,
            retired_at=excluded.retired_at,
            retirement_reason=excluded.retirement_reason
        """,
        (
            s.name, str(s.file_path), s.status, s.symbol, s.timescale,
            s.mechanism_family,
            s.parent_strategy, s.body_section("Hypothesis"),
            s.body_section("Mechanism"),
            json.dumps(s.regime_applicability, sort_keys=True),
            json.dumps(s.data_points), now, now,
            now if s.status == "retired" else None,
            s.retirement_reason,
        ),
    )
    conn.commit()
