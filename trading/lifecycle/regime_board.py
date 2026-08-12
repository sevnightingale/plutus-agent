"""REGIME.md's table, rendered from the database.

plutus-regime hand-wrote this file with the ``file`` toolset until 2026-07-27,
which is why no code could read the regime and why the template's ``since``
column quietly disappeared from the live board — a hand-written record drifts
from its own specification and nothing notices.

The database is truth; this renders it. Same arrangement ``live_state.py``
already uses for PLUTUS.md, and the same discipline: the tool owns the table,
the agent owns the prose. ``## Assessment notes`` below the table is
plutus-regime's — those notes carry the reasoning behind a flip, which no
renderer can reconstruct — but retention is bounded: each re-render keeps
the newest ``NOTES_KEEP`` dated entries and drops the rest (see
``_trim_notes``). The words are the agent's; the length is the renderer's.

The rendered table's SHAPE IS LOAD-BEARING. Four agents (predict, generate,
main, regime) read REGIME.md as prompt text, so a change to the format is a
change to four agents' behaviour at once. It reproduces the live layout
byte-for-byte, including the four-space gap in the header and the em dash for
an absent macro label.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from harness.tools import file_state

TIMESCALES = ("intraday", "swing", "position")
ABSENT = "—"          # em dash, as the board has always used
_NOTES_RE = re.compile(r"^##\s", re.MULTILINE)

# Dated assessment entries retained in the notes zone. The agent maintains
# notes newest-first; entries beyond this fall off at each re-render. Without
# a bound the zone grew to 215KB in nine days — ~55k tokens riding into every
# regime/predict/generate spawn — and per-symbol assessment only feeds it
# faster. The flip log and other undated sections are never trimmed.
NOTES_KEEP = 3
# A single dated entry of 15–20KB × NOTES_KEEP still blew REGIME.md past
# 100KB (2026-08-12). The words stay the agent's; the length is the
# renderer's. Truncate from the tail so the lede (the verdict) survives.
NOTE_MAX_CHARS = 4000
_DATED_NOTE_RE = re.compile(r"^## \d{2}:\d{2}Z\s")


def _trim_notes(notes: str, keep: int = NOTES_KEEP) -> str:
    """Keep the newest ``keep`` dated assessment entries; drop the rest.

    Sections split on ``^## ``. Dated entries (``## HH:MMZ …``) count against
    the cap in file order — newest-first, as plutus-regime writes them.
    Undated sections (``## Assessment notes``, the flip log) pass through
    untouched, wherever they sit.
    """
    parts = re.split(r"(?=^## )", notes, flags=re.MULTILINE)
    kept, dated = [], 0
    for p in parts:
        if _DATED_NOTE_RE.match(p):
            dated += 1
            if dated > keep:
                continue
            if len(p) > NOTE_MAX_CHARS:
                p = (p[:NOTE_MAX_CHARS].rstrip()
                     + "\n\n*(truncated by renderer)*\n\n")
        kept.append(p)
    return "".join(kept)


def _symbol_table(regime: dict) -> list:
    """The 3-row table body for one symbol (``current_regime()``'s shape).

    A timescale with no observation renders ``(unassessed)`` rather than
    being dropped: the board always shows three rows, and an absent reading
    must look absent.
    """
    lines = [
        "| timescale | direction | volatility | macro |",
        "|---|---|---|---|",
    ]
    for ts in TIMESCALES:
        cell = regime.get(ts) or {}
        lines.append(
            f"| {ts} | {cell.get('direction') or '(unassessed)'} "
            f"| {cell.get('volatility') or '(unassessed)'} "
            f"| {cell.get('macro') or ABSENT} |")
    return lines


def board_symbols(conn, window_days: float = 14.0) -> list:
    """Symbols the board renders: any with a regime observation in the
    window, BTC first, then alphabetical. Deterministic from the database."""
    import time as _time
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM regime_observations WHERE ts >= ?",
        (_time.time() - window_days * 86400,)).fetchall()
    syms = {str(r[0]) for r in rows} or {"BTC"}
    return sorted(syms, key=lambda s: (s != "BTC", s))


def render_table(regime_by_symbol: dict, updated_at: Optional[str] = None,
                 by: str = "plutus-regime") -> str:
    """Header + one table per symbol (``### <symbol>`` sections).

    ``regime_by_symbol`` maps symbol → ``current_regime()`` shape. Since
    2026-08-08 (the multi-asset turn) the board is per-symbol; ``### ``
    section heads are invisible to the notes regex (``^##\\s``), so the
    agent's ``## Assessment notes`` split is unchanged. The table shape
    within a section is byte-identical to the old single board — four
    agents read this as prompt text.
    """
    stamp = updated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# REGIME",
        f"updated_at: {stamp} UTC    by: {by}",
        "",
    ]
    for symbol in regime_by_symbol:
        lines.append(f"### {symbol}")
        lines.append("")
        lines.extend(_symbol_table(regime_by_symbol[symbol]))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_board(conn, path: Optional[Path] = None,
                by: str = "plutus-regime") -> dict:
    """Rewrite REGIME.md's tables from the database, preserving the notes.

    Renders every symbol with a recent observation (``board_symbols``). The
    tables are everything before the first ``## `` heading; the notes are
    that heading onward and belong to the agent. Runs under the shared
    per-path lock and lands atomically, so a concurrent notes edit is never
    torn or lost.

    Returns ``{ok, path, error}``. A missing file is honest failure, not a
    silent create — the bootstrap owns creation.
    """
    from trading.lifecycle.queries import current_regime

    if path is None:
        from harness.constants import get_hermes_home
        path = get_hermes_home() / "REGIME.md"
    path = Path(path)
    if not path.exists():
        return {"ok": False, "path": str(path),
                "error": f"{path.name} does not exist"}

    table = render_table(
        {s: current_regime(conn, symbol=s) for s in board_symbols(conn)},
        by=by)
    resolved = str(path.resolve())
    with file_state.lock_path(resolved):
        text = path.read_text(encoding="utf-8")
        m = _NOTES_RE.search(text)
        notes = _trim_notes(text[m.start():]) if m else ""
        new_text = table + ("\n" + notes if notes else "")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
    return {"ok": True, "path": str(path), "error": None}


def board_matches_db(conn, path: Optional[Path] = None) -> bool:
    """Do the rendered tables still agree with the database, per symbol?

    The Live State zone froze for a month because a writer failed and nothing
    compared the file to its source. This is the comparison, and the integrity
    check calls it. Rows are keyed (symbol, timescale) — a ``### <symbol>``
    head switches the current symbol; a bare table (the pre-multi-asset
    board) reads as BTC.
    """
    from trading.lifecycle.queries import current_regime

    if path is None:
        from harness.constants import get_hermes_home
        path = get_hermes_home() / "REGIME.md"
    path = Path(path)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    rows = {}
    section = "BTC"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            section = stripped[4:].strip()
            continue
        if stripped.startswith("## "):
            break                        # notes — tables end here
        parts = [p.strip() for p in stripped.strip("|").split("|")]
        if len(parts) == 4 and parts[0] in TIMESCALES:
            rows[(section, parts[0])] = parts[1:]
    for symbol in board_symbols(conn):
        live = current_regime(conn, symbol=symbol)
        for ts in TIMESCALES:
            cell = live.get(ts)
            if not cell:
                continue                 # never assessed — nothing to disagree with
            want = [cell["direction"], cell["volatility"], cell["macro"] or ABSENT]
            if rows.get((symbol, ts)) != want:
                return False
    return True
