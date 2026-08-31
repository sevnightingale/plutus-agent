"""Strategy loader — status-gated context, write-through DB mirror sync.

The single code path for strategy lifecycle changes: every write goes through
``write_strategy`` / ``set_status``, which edit the file AND sync the mirror
row in the same call. Status gates context: only test+active strategies reach
prediction context; retired is the graveyard generate reads before
authoring (do not redo, or variant from what failed). There is no
dormant — parked-and-still-on-the-bar was a one-way tax.
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


def _eligible_filter(live: list) -> Optional[tuple]:
    """Split ``live`` into (eligible, dark) against the lit regime cells.

    Eligibility is code's answer (queries.strategy_cells vs current_regime,
    per each book's OWN symbol) — the same computation
    ``strategies_by_timescale`` serves predict through its tools. Returns
    None when the regime is unknown or the DB is unreachable: the caller
    then falls back to the full book with the reason stated, because a desk
    that has never assessed regime must not blind predict.
    """
    try:
        from trading.lifecycle import queries
        from trading.lifecycle.db import get_db

        conn = get_db()
        lit_by: dict = {}
        for sym in {s.symbol for s in live}:
            reg = queries.current_regime(conn, symbol=sym) or {}
            lit_by[sym] = {
                ts: ((ts, v.get("direction"), v.get("volatility"),
                      v.get("macro")) if v else None)
                for ts, v in reg.items()
            }
        eligible, dark = [], []
        any_lit = False
        for s in live:
            cell = lit_by.get(s.symbol, {}).get(s.timescale)
            if cell is not None:
                any_lit = True
            cells = queries.strategy_cells(
                s.timescale, json.dumps(s.regime_applicability))
            (eligible if cell is not None and cell in cells
             else dark).append(s)
        if not any_lit:
            return None  # regime never assessed — don't fake a filter
        return eligible, dark
    except Exception:
        logger.exception("eligibility filter failed — serving the full book")
        return None


def strategy_context_block(base_dir: Optional[Path] = None,
                           compact: bool = False,
                           eligible_only: bool = False) -> str:
    """The prompt-injection summary for predict/main — live strategies only.

    Dead strategies never pollute prediction context (locked §14.4).

    ``compact`` drops the hypothesis line. At 194 books the full block
    measured 364KB (~91k tokens) riding into every predict call — and the
    hypothesis copy is redundant there: ``predict_draft`` loads the strategy
    FILE server-side and feeds Hypothesis/Mechanism/Trigger to the aux call
    itself, so predict's own context never needs the prose, only the
    orientation row (name, symbol, cell, weights, lineage).

    ``eligible_only`` keeps full rows only for books eligible in the lit
    regime cells and reduces the rest to a count. Measured 2026-08-31: 487
    live books rode ~146k tokens into every predict spawn at full-miss cache
    price, while only 46 were eligible — and predict can only author eligible
    books, so the dark rows were pure freight. The full population (with
    sampling counters) stays one `strategies_by_timescale` call away.
    """
    live = load_strategies(LIVE_STATUSES, base_dir)
    if not live:
        return (
            "## Strategy book\n\n(no live strategies — predictions only, NO "
            "trades; generation fills the slots)\n"
        )
    header = ""
    if eligible_only:
        split = _eligible_filter(live)
        if split is None:
            header = ("(eligibility unknown — regime unassessed or "
                      "unreadable; full live book follows)\n")
        else:
            eligible, dark = split
            header = (
                f"{len(eligible)} of {len(live)} live books are eligible in "
                f"the lit regime cells and listed below; the other "
                f"{len(dark)} sit in dark cells (correctly idle — "
                f"`strategies_by_timescale` has the full population with "
                f"counters).\n"
            )
            if not eligible:
                return ("## Strategy book\n\n" + header
                        + "\n(none eligible — population gaps belong in "
                          "your report, not new registrations)\n")
            live = eligible
    lines = ["## Strategy book\n"]
    if header:
        lines.append(header)
    for s in live:
        regime = json.dumps(s.regime_applicability, sort_keys=True)
        dps = ", ".join(f"{k}:{w:.2f}" for k, w in s.weights.items())
        head = (
            f"### {s.name} [{s.status}] {s.symbol} "
            f"{s.timescale}/{s.mechanism_family}\n"
            f"- regime: {regime}\n"
            f"- weights: {dps}\n"
            + (f"- parent: {s.parent_strategy} (tweak: {s.variant_tweak})\n"
               if s.parent_strategy else "")
            + (f"- missing data points: {', '.join(s.missing_data_points)}\n"
               if s.missing_data_points else "")
        )
        if compact:
            lines.append(head)
            continue
        hypothesis = (s.body_section("Hypothesis") or "").strip().replace("\n", " ")
        lines.append(head + f"- hypothesis: {hypothesis}\n")
    return "\n".join(lines)


def roster_context_block(base_dir: Optional[Path] = None) -> str:
    """One orientation line per live book — the population view for
    generate/reflect, whose job is the book's SHAPE (which mechanisms
    occupy which cells), not any single book's weights.

    The multi-line compact rows measured ~146k tokens at 487 books
    (2026-08-31) riding into every generate/reflect spawn; a line each is
    ~a tenth of that, and both seats read strategy FILES through their
    tools when they need one book's detail.
    """
    live = load_strategies(LIVE_STATUSES, base_dir)
    if not live:
        return (
            "## Strategy book\n\n(no live strategies — the population is "
            "empty; generation fills the slots)\n"
        )
    lines = [
        "## Strategy book\n",
        f"{len(live)} live books, one line each (name [status] symbol "
        f"timescale/family cell, + parent where variant). Read a strategy's "
        f"FILE for its hypothesis, weights and full declaration.\n",
    ]
    for s in live:
        regime = json.dumps(s.regime_applicability, sort_keys=True)
        lines.append(
            f"- {s.name} [{s.status}] {s.symbol} "
            f"{s.timescale}/{s.mechanism_family} cell={regime}"
            + (f" parent={s.parent_strategy}" if s.parent_strategy else "")
        )
    return "\n".join(lines) + "\n"


def retired_context_block(base_dir: Optional[Path] = None) -> str:
    """Compact graveyard for generate — names, cells, book, reason.

    The files stay so generate does not re-author a failed mechanism, or
    so a variant can name what failed and the one tweak. Expectancy is
    queried from the db when present; a missing row is honest absence.
    """
    retired = load_strategies(("retired",), base_dir)
    if not retired:
        return "## Retired\n\n(none)\n"
    lines = [
        "## Retired\n",
        "Withdrawn from the live book. Read before authoring. Do not "
        "re-author the same mechanism into the same cell unless the "
        "variant names what failed and the one thing that is different.\n",
    ]
    try:
        from trading.lifecycle.db import get_db
        from trading.lifecycle.queries import retired_book
        by_name = {r["name"]: r for r in retired_book(get_db())}
    except Exception:
        by_name = {}
    for s in retired:
        row = by_name.get(s.name) or {}
        regime = json.dumps(s.regime_applicability, sort_keys=True)
        n = row.get("n")
        exp = row.get("expectancy_pct")
        reason = s.retirement_reason or row.get("retirement_reason") or ""
        lines.append(
            f"- {s.name} [{s.symbol}] {s.timescale}/{s.mechanism_family} "
            f"n={n} exp={exp} cell={regime}"
            + (f" parent={s.parent_strategy}" if s.parent_strategy else "")
            + (f" — {reason}" if reason else "")
            + "\n"
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
    if status == "dormant":
        raise ValueError(
            "dormant is abolished — retire the book (withdrawn from the "
            "live set; generate reads it so the desk does not re-author "
            "the same loser)")
    if status not in ("test", "active", "retired"):
        raise ValueError(f"status must be test|active|retired, got {status!r}")
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
