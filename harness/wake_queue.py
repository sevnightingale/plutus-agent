"""The serialized wake queue — four sources, one drain (§10).

Wake sources (self-schedule cron, operator message, ops staleness, watchers)
never fire turns directly; they ENQUEUE. The gateway drains one wake at a
time into plutus-main's persistent session, so a watcher event and an ops
staleness trigger in the same minute collapse into ONE turn instead of two
racing sessions.

Storage: ``~/.plutus-agent/wake_queue.jsonl`` — append-only enqueue under an
exclusive lock; drain pops ALL pending wakes at once (they collapse into one
turn by design) and truncates. Crash-safe: a wake survives a gateway restart
because it's on disk until drained.
"""

from __future__ import annotations

import fcntl
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)

VALID_REASONS = ("schedule", "operator", "staleness", "watcher", "escalation")


def _queue_path(home: Optional[Path] = None) -> Path:
    home = home if home is not None else get_hermes_home()
    return home / "wake_queue.jsonl"


def enqueue(reason: str, detail: str = "", source: str = "",
            home: Optional[Path] = None) -> dict:
    """Append a wake. Returns the enqueued record."""
    if reason not in VALID_REASONS:
        raise ValueError(f"wake reason must be one of {VALID_REASONS}")
    record = {"ts": time.time(), "reason": reason, "detail": detail,
              "source": source}
    path = _queue_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(record) + "\n")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)
    logger.info("wake enqueued: %s (%s)", reason, detail[:80])
    return record


def drain(home: Optional[Path] = None) -> List[dict]:
    """Pop ALL pending wakes atomically (empty list when quiet)."""
    path = _queue_path(home)
    if not path.exists():
        return []
    with open(path, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        lines = f.readlines()
        f.seek(0)
        f.truncate()
        fcntl.flock(f, fcntl.LOCK_UN)
    wakes = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            wakes.append(json.loads(line))
        except json.JSONDecodeError:
            logger.error("wake queue: dropping malformed line %r", line[:80])
    return wakes


def peek(home: Optional[Path] = None) -> int:
    """How many wakes are pending (no mutation)."""
    path = _queue_path(home)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def format_wake_prompt(wakes: List[dict]) -> str:
    """Render drained wakes as plutus-main's synthetic turn prompt."""
    lines = [
        f"[WAKE] {len(wakes)} pending trigger(s) collapsed into this turn:",
    ]
    for w in wakes:
        age = int(time.time() - w["ts"])
        src = f" via {w['source']}" if w.get("source") else ""
        lines.append(f"- {w['reason']}{src} ({age}s ago): {w.get('detail') or '(no detail)'}")
    lines.append(
        "\nHandle per your Procedure (PLUTUS.md doctrine): refresh what the "
        "wake needs by spawning, decide, record, and schedule the next wake "
        "before ending the turn."
    )
    return "\n".join(lines)
