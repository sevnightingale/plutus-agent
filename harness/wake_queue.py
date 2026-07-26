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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)

VALID_REASONS = ("schedule", "operator", "staleness", "watcher", "escalation")

# Backoff for keyed wakes. The first firing of a key is immediate; each
# subsequent one waits twice as long as the last, capped. With ops on a
# 30-minute tick a permanently-true condition therefore costs roughly 7 turns
# a day instead of 48, and the escalation shows up in the wake's CONTENT (the
# consecutive count) rather than in its frequency.
_BACKOFF_BASE_S = 1800          # one ops tick
_BACKOFF_MAX_S = 6 * 3600


def _queue_path(home: Optional[Path] = None) -> Path:
    home = home if home is not None else get_hermes_home()
    return home / "wake_queue.jsonl"


def _suppression_path(home: Optional[Path] = None) -> Path:
    home = home if home is not None else get_hermes_home()
    return home / "wake_suppression.json"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _backoff_for(consecutive: int) -> float:
    """Seconds to stay quiet after the `consecutive`-th firing of a key."""
    return min(_BACKOFF_BASE_S * (2 ** max(0, consecutive - 1)), _BACKOFF_MAX_S)


def _consider_key(key: str, home: Optional[Path]) -> dict:
    """Decide whether a keyed wake fires now, and update its state.

    Returns ``{"fire": bool, "consecutive": int, "suppressed": int}``. State
    lives in a sidecar rather than the queue itself because the queue is
    drained and truncated on every turn — it cannot remember what it has
    already said.
    """
    path = _suppression_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read().strip()
        try:
            state = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            logger.error("wake suppression state unreadable; resetting")
            state = {}
        entry = state.get(key) or {}
        last_fired = float(entry.get("last_fired_ts") or 0.0)
        consecutive = int(entry.get("consecutive") or 0)
        suppressed = int(entry.get("suppressed") or 0)

        # A quiet spell longer than the cap means the condition cleared and
        # came back; treat the next one as a fresh first, loud again.
        if last_fired and (now - last_fired) > _BACKOFF_MAX_S * 2:
            consecutive, suppressed = 0, 0

        fire = (not last_fired) or (now - last_fired) >= _backoff_for(consecutive)
        if fire:
            result = {"fire": True, "consecutive": consecutive + 1,
                      "suppressed": suppressed}
            entry = {"last_fired_ts": now, "consecutive": consecutive + 1,
                     "suppressed": 0}
        else:
            result = {"fire": False, "consecutive": consecutive,
                      "suppressed": suppressed + 1}
            entry = {"last_fired_ts": last_fired, "consecutive": consecutive,
                     "suppressed": suppressed + 1}

        state[key] = entry
        f.seek(0)
        f.truncate()
        f.write(json.dumps(state, indent=2, sort_keys=True))
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)
    return result


def enqueue(reason: str, detail: str = "", source: str = "",
            key: Optional[str] = None, home: Optional[Path] = None) -> dict:
    """Append a wake. Returns the enqueued record (or the suppression verdict).

    ``key`` opts a recurring condition into backoff — pass a stable string
    like ``"staleness:perception"``. Without it behaviour is unchanged: every
    call appends, which is right for genuinely novel events (a watcher firing
    on a new price is not the same wake twice).

    The opt-in exists because on 2026-07-26 ops re-enqueued the same
    perception-staleness wake every 30 minutes for eleven hours. main declined
    all thirteen and had to keep its own tally in prose ("11th identical") —
    the count is exactly the signal it lacked, so a fired wake now carries it.
    """
    if reason not in VALID_REASONS:
        raise ValueError(f"wake reason must be one of {VALID_REASONS}")

    if key:
        verdict = _consider_key(key, home)
        if not verdict["fire"]:
            logger.info("wake suppressed: %s key=%s (%d held since last)",
                        reason, key, verdict["suppressed"])
            return {"ok": True, "suppressed": True, "key": key,
                    "held": verdict["suppressed"]}
        if verdict["consecutive"] > 1:
            note = f"{_ordinal(verdict['consecutive'])} consecutive"
            if verdict["suppressed"]:
                note += f", {verdict['suppressed']} suppressed since the last"
            detail = f"[{note}] {detail}"

    record = {"ts": time.time(), "reason": reason, "detail": detail,
              "source": source}
    if key:
        record["key"] = key
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
    """Render drained wakes as plutus-main's synthetic turn prompt.

    The header carries an ABSOLUTE UTC stamp, not just the per-wake relative
    ages. main's only other clock is the session anchor, which is stamped once
    when the prompt is built and rebuilt only on compaction — so in the
    persistent gateway session (hours long, and the one session that outlives
    a date boundary) it drifts from "now" precisely as the day wears on. A
    wake is the moment main is asked to make a time-sensitive call, so it is
    the moment worth handing it a live clock.
    """
    stamp = datetime.now(timezone.utc).strftime("%A %Y-%m-%d %H:%M")
    lines = [
        f"[WAKE @ {stamp} UTC] {len(wakes)} pending trigger(s) "
        f"collapsed into this turn:",
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
