"""REGIME.md's table, rendered from the database.

plutus-regime hand-wrote this file with the ``file`` toolset until 2026-07-27,
which is why no code could read the regime and why the template's ``since``
column quietly disappeared from the live board — a hand-written record drifts
from its own specification and nothing notices.

The database is truth; this renders it. Same arrangement ``live_state.py``
already uses for PLUTUS.md, and the same discipline: the tool owns the table,
the agent owns the prose. ``## Assessment notes`` below the table is
plutus-regime's and is never touched here — those notes carry the reasoning
behind a flip, which no renderer can reconstruct.

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


def render_table(regime: dict, updated_at: Optional[str] = None,
                 by: str = "plutus-regime") -> str:
    """The header + 3-row table, exactly as the live board renders it.

    ``regime`` is ``current_regime()``'s shape. A timescale with no
    observation renders ``(unassessed)`` rather than being dropped: the board
    always shows three rows, and an absent reading must look absent.
    """
    stamp = updated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# REGIME",
        f"updated_at: {stamp} UTC    by: {by}",
        "",
        "| timescale | direction | volatility | macro |",
        "|---|---|---|---|",
    ]
    for ts in TIMESCALES:
        cell = regime.get(ts) or {}
        lines.append(
            f"| {ts} | {cell.get('direction') or '(unassessed)'} "
            f"| {cell.get('volatility') or '(unassessed)'} "
            f"| {cell.get('macro') or ABSENT} |")
    return "\n".join(lines) + "\n"


def write_board(conn, path: Optional[Path] = None, symbol: str = "BTC",
                by: str = "plutus-regime") -> dict:
    """Rewrite REGIME.md's table from the database, preserving the notes.

    The table is everything before the first ``## `` heading; the notes are
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

    table = render_table(current_regime(conn, symbol=symbol), by=by)
    resolved = str(path.resolve())
    with file_state.lock_path(resolved):
        text = path.read_text(encoding="utf-8")
        m = _NOTES_RE.search(text)
        notes = text[m.start():] if m else ""
        new_text = table + ("\n" + notes if notes else "")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
    return {"ok": True, "path": str(path), "error": None}


def board_matches_db(conn, path: Optional[Path] = None,
                     symbol: str = "BTC") -> bool:
    """Does the rendered table still agree with the database?

    The Live State zone froze for a month because a writer failed and nothing
    compared the file to its source. This is the comparison, and the integrity
    check calls it.
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
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) == 4 and parts[0] in TIMESCALES:
            rows[parts[0]] = parts[1:]
    live = current_regime(conn, symbol=symbol)
    for ts in TIMESCALES:
        cell = live.get(ts)
        if not cell:
            continue                     # never assessed — nothing to disagree with
        want = [cell["direction"], cell["volatility"], cell["macro"] or ABSENT]
        if rows.get(ts) != want:
            return False
    return True
