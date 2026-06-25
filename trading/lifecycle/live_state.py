"""Live State writer — the deterministic, no-LLM writer for PLUTUS.md's
``## Live State`` zone (Issue 2).

The zone is labelled ``<!-- TOOL-REWRITTEN ONLY -->`` but no writer ever
existed — only the bootstrap template created it, so it froze at install
(Jun 16). ``write_live_state`` recomputes the zone from lifecycle.db plus the
(Issue-3-fixed) pre-fill equity path and rewrites ONLY that zone, surgically:
``replace_zone`` mirrors ``spawn._read_zone``'s ``## Heading … next ## | EOF``
regex, so the Doctrine and Lessons zones main owns are never touched.

A failed equity read writes ``equity_usd: unavailable`` with the timestamp —
never a silently stale number.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Serialises this process's Live State writes (mirrors harness.tools.file_tools'
# module-level lock idiom); the atomic os.replace below guards against partial
# writes from any concurrent writer.
_LIVE_STATE_LOCK = threading.Lock()

_MARKER = "<!-- TOOL-REWRITTEN ONLY. Do not edit by hand. -->"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC"


def replace_zone(path: Path, zone: str, new_body: str) -> bool:
    """Replace the body of a ``## <zone>`` section, surgically and atomically.

    Mirrors ``spawn._read_zone``'s zone regex (``## Heading`` … up to the next
    ``## `` or EOF), swapping only the matched body and preserving the heading
    and every other zone. ``new_body`` is inserted literally (no regex
    backreference interpretation). Returns False if the file or the zone is
    absent — the caller surfaces that as honest failure, never a silent create.
    """
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(^##\s+{re.escape(zone.replace('-', ' '))}\s*$)(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not pattern.search(text):
        return False
    body = new_body.strip("\n")
    new_text = pattern.sub(lambda m: f"{m.group(1)}\n\n{body}\n\n", text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def build_live_state_body(conn) -> str:
    """Compute the ``## Live State`` body from lifecycle.db + the equity path."""
    from trading.lifecycle import queries

    # equity — the Issue-3-fixed path; a failed read is honest, never stale.
    try:
        from trading.integrations.hyperliquid.venue import hl_account_state
        equity = float(hl_account_state()["equity_usd"])
        equity_line = f"- equity_usd: ${equity:,.2f}"
        snapshot_line = f"- snapshot_at: {_utc_stamp()}"
    except Exception as exc:  # honest absence — surface the failure, don't guess
        equity_line = f"- equity_usd: unavailable ({type(exc).__name__})"
        snapshot_line = f"- snapshot_at: {_utc_stamp()} (equity read failed)"

    # open position (single-position law) — compact summary or 'none'.
    pos = queries.open_position(conn)
    if pos:
        thesis = pos.get("thesis") or {}
        last_eval = pos.get("last_evaluation") or {}
        parts = [f"{pos['symbol']} {pos['side']} size={pos['size']}"]
        if thesis.get("strategy_name"):
            parts.append(f"strat={thesis['strategy_name']}")
        if thesis.get("sl_price") is not None:
            parts.append(f"sl={thesis['sl_price']}")
        if last_eval.get("conviction") is not None:
            parts.append(f"conv={last_eval['conviction']}")
        open_position = " ".join(parts)
    else:
        open_position = "none"

    # strategy population by status (mirrors open_slot_counts' GROUP BY idiom).
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM strategies GROUP BY status").fetchall())
    strategies = (f"{counts.get('active', 0)} active / {counts.get('test', 0)} test / "
                  f"{counts.get('dormant', 0)} dormant / {counts.get('retired', 0)} retired")

    return "\n".join([
        _MARKER,
        equity_line,
        snapshot_line,
        "- regime: see REGIME.md",
        f"- open_position: {open_position}",
        f"- strategies: {strategies}",
    ])


def write_live_state(conn, path: Optional[Path] = None) -> dict:
    """Recompute and rewrite PLUTUS.md's ``## Live State`` zone.

    Returns ``{ok, path, body, error}``. ``ok`` is False (with ``error``) when
    PLUTUS.md or its Live State zone is missing — the writer never creates the
    zone, only refreshes the one the bootstrap template laid down."""
    if path is None:
        from harness.constants import get_hermes_home
        path = get_hermes_home() / "PLUTUS.md"
    body = build_live_state_body(conn)
    with _LIVE_STATE_LOCK:
        ok = replace_zone(Path(path), "live-state", body)
    return {
        "ok": ok,
        "path": str(path),
        "body": body if ok else None,
        "error": None if ok else f"{Path(path).name} has no '## Live State' zone",
    }
