"""Desk integrity — deterministic assertions about the desk's own health.

Every other beat on this desk acts on the MARKET. plutus-ops resolves and
watches, predict forecasts, reflect judges outcomes. Nothing asks whether the
desk itself is well, and on 2026-07-26 that gap cost eleven hours: perception
stale at nearly 3x its floor, the same wake fired thirteen times, a table with
a schema and no writer, an identity file quietly growing 138 blank lines. Each
was individually visible in a log nobody was reading, and collectively
invisible.

This is the missing check. Deliberately NOT an LLM beat — the earlier
`plutus-tend` was one, ran for three days, died at the seven-agent rebuild and
was never noticed precisely because judging one's own health is the thing an
agent is worst at and code is best at. These are assertions. They are silent
when they hold, cost nothing on the cheapest model, and every one of them was
written against a failure that actually happened.

Each check is independently fault-tolerant: one that cannot run reports itself
as a violation rather than passing quietly, because a health check that fails
open is worse than none at all.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# A blank run longer than this in a blackboard zone is accretion, not layout.
MAX_BLANK_RUN = 20
# Live State older than this means sync_live_state or its caller is stuck.
LIVE_STATE_MAX_AGE_S = 12 * 3600
# Doctrine caps the curated lessons; drift past it means reflect is appending.
LESSONS_CAP = 12
# Wake keys that keep escalating past this are a loop, not a condition.
WAKE_LOOP_CONSECUTIVE = 8
# Runtime disk bound. The 1.2 GB accretion that motivated the original
# maintenance beat is exactly what this catches returning.
RUNTIME_DISK_MAX_MB = 2048
# Resolutions a book needs before its lifetime expectancy is evidence enough
# to retire on. Mirrors the reflect protocol's bar; kept here because this is
# where it is ENFORCED, and retirement now moves the graduation hurdle.
RETIREMENT_MIN_N = 20

# Tables that MUST have rows on a desk that has been running. Emptiness here
# is the signature of an unreachable table — reflections carried it for 12
# reflect passes, capital_movements since inception.
EXPECTED_NONEMPTY = ("strategies", "predictions", "action_runs")


def _violation(name: str, detail: str, severity: str = "warn") -> Dict[str, Any]:
    return {"check": name, "severity": severity, "detail": detail}


# ── individual checks ──────────────────────────────────────────────────────
# Each returns a list of violations (empty when healthy).

def _check_blackboard_bloat(conn, home: Path) -> List[Dict[str, Any]]:
    out = []
    for name in ("PLUTUS.md", "REGIME.md", "PERCEPTION.md"):
        path = home / name
        if not path.exists():
            out.append(_violation("blackboard_missing",
                                  f"{name} does not exist", "critical"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        longest = max((len(m) for m in re.findall(r"\n[ \t]*(?:\n[ \t]*)+", text)),
                      default=0)
        if longest > MAX_BLANK_RUN:
            out.append(_violation(
                "blackboard_bloat",
                f"{name} carries a run of ~{longest} blank lines — a writer is "
                f"accreting rather than replacing"))
    return out


def _check_blackboard_zones(conn, home: Path) -> List[Dict[str, Any]]:
    """PLUTUS.md's zones must exist. replace_zone returns False when a zone is
    missing, so a lost heading makes every future Live State write a silent
    no-op — the file simply stops updating and nothing says why."""
    path = home / "PLUTUS.md"
    if not path.exists():
        return []          # already reported by _check_blackboard_bloat
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [z for z in ("Doctrine", "Live State", "Lessons")
               if not re.search(rf"^##\s+{z}\s*$", text, re.MULTILINE | re.IGNORECASE)]
    if missing:
        return [_violation("blackboard_zone_missing",
                           f"PLUTUS.md has no {', '.join(missing)} zone — "
                           f"writes to it will silently no-op", "critical")]
    return []


def _check_live_state_fresh(conn, home: Path) -> List[Dict[str, Any]]:
    import time
    from datetime import datetime, timezone

    path = home / "PLUTUS.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^-\s*snapshot_at:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})",
                  text, re.MULTILINE)
    if not m:
        return [_violation("live_state_unstamped",
                           "Live State carries no parseable snapshot_at")]
    stamped = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc)
    age = time.time() - stamped.timestamp()
    if age > LIVE_STATE_MAX_AGE_S:
        return [_violation(
            "live_state_stale",
            f"Live State snapshot is {age / 3600:.1f}h old (bound "
            f"{LIVE_STATE_MAX_AGE_S / 3600:.0f}h) — sync_live_state or its "
            f"caller is stuck")]
    return []


def _check_lessons_cap(conn, home: Path) -> List[Dict[str, Any]]:
    path = home / "PLUTUS.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^##\s+Lessons\s*$(.*?)(?=^##\s+|\Z)",
                  text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    n = len(re.findall(r"^\s*[-*]\s+\S", m.group(1), re.MULTILINE))
    if n > LESSONS_CAP:
        return [_violation("lessons_over_cap",
                           f"{n} lessons recorded, doctrine caps at "
                           f"{LESSONS_CAP} — reflect is appending, not replacing")]
    return []


def _check_staleness_ceiling(conn, home: Path) -> List[Dict[str, Any]]:
    """The floors are main's judgement; the ceilings are not.

    A floor that can be declined indefinitely is not a floor — main declined
    perception thirteen times running on 2026-07-26 and the desk went blind.
    """
    import time
    from trading.dispatchers.wake import STALENESS_CEILINGS
    from trading.lifecycle import queries

    last = queries.last_action_runs(conn)
    now = time.time()
    out = []
    for action, ceiling in STALENESS_CEILINGS.items():
        ts = last.get(action)
        if ts is None:
            continue          # never run is a cold start, not a breach
        age = now - ts
        if age > ceiling:
            out.append(_violation(
                "staleness_ceiling_breached",
                f"{action} last ran {age / 3600:.1f}h ago, ceiling is "
                f"{ceiling / 3600:.0f}h — the enforcer should have fired",
                "critical"))
    return out


def _check_tables_reachable(conn, home: Path) -> List[Dict[str, Any]]:
    """A table with a schema and no rows on a running desk is usually a table
    with no WRITER. reflections carried that for 12 reflect passes;
    capital_movements since the day it was created."""
    try:
        desk_has_run = conn.execute(
            "SELECT COUNT(*) FROM action_runs").fetchone()[0] > 0
    except Exception:
        return [_violation("integrity_check_failed",
                           "action_runs unreadable", "critical")]
    if not desk_has_run:
        return []             # cold install: empty is correct

    out = []
    for table in EXPECTED_NONEMPTY:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception as exc:
            out.append(_violation("table_unreadable",
                                  f"{table}: {type(exc).__name__}", "critical"))
            continue
        if n == 0:
            out.append(_violation(
                "table_empty_on_running_desk",
                f"{table} has 0 rows though the desk has been running — "
                f"suspect an unreachable table (no writer, no tool grant)"))
    return out


def _check_capital_recorded(conn, home: Path) -> List[Dict[str, Any]]:
    """Equity without recorded capital means the P&L is unknowable."""
    try:
        n = conn.execute("SELECT COUNT(*) FROM capital_movements").fetchone()[0]
    except Exception as exc:
        return [_violation("capital_unreadable",
                           f"capital_movements: {type(exc).__name__}", "critical")]
    if n:
        return []
    try:
        traded = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    except Exception:
        traded = 0
    if traded:
        return [_violation(
            "capital_unrecorded",
            "capital_movements is empty although positions exist — every "
            "performance figure is gross of unknown deposits; run "
            "capital_reconcile")]
    return []


def _check_wake_loop(conn, home: Path) -> List[Dict[str, Any]]:
    import json
    path = home / "wake_suppression.json"
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return [_violation("wake_state_corrupt",
                           "wake_suppression.json is unreadable")]
    out = []
    for key, entry in sorted(state.items()):
        n = int((entry or {}).get("consecutive") or 0)
        if n >= WAKE_LOOP_CONSECUTIVE:
            out.append(_violation(
                "wake_loop",
                f"'{key}' has fired {n} times consecutively without the "
                f"condition clearing — it is being declined, not handled"))
    return out


def _check_runtime_disk(conn, home: Path) -> List[Dict[str, Any]]:
    total = 0
    for sub in ("sessions", "ledger", "logs", "request_dumps"):
        d = home / sub
        if not d.exists():
            continue
        try:
            total += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError as exc:
            logger.debug("runtime disk walk failed for %s: %s", sub, exc)
    mb = total / (1024 * 1024)
    if mb > RUNTIME_DISK_MAX_MB:
        return [_violation(
            "runtime_disk",
            f"runtime data is {mb:.0f} MB (bound {RUNTIME_DISK_MAX_MB} MB) — "
            f"the retention sweep is not keeping up")]
    return []


def _check_retirement_evidence(conn, home: Path) -> List[Dict[str, Any]]:
    """No book may be retired while its lifetime expectancy is still positive.

    Retirement stopped being a bookkeeping move on 2026-07-27. Retired books
    are excluded from the multiplicity count, so retiring one LOWERS the
    graduation hurdle for every surviving strategy at its timescale — which
    makes retirement a dial on the desk's own bar, the same vector a
    cell-scoped M was rejected for on 2026-07-07.

    The protocol closes it by allowing only one reason to retire: demonstrated
    non-positive lifetime expectancy at n >= RETIREMENT_MIN_N. Every
    judgement-based pruning move goes to dormancy instead, and dormant books
    keep counting toward the bar. This check is the enforcement — a retirement
    that does not meet the evidence bar is a violation, stated plainly, and
    the desk cannot quietly lower its own hurdle.

    Books retired before this rule existed are exempt on evidence, not on
    date: they are reported only when the book is large enough to judge.
    """
    try:
        from trading.lifecycle.queries import strategy_cell_expectancy
        names = [r[0] for r in conn.execute(
            "SELECT name FROM strategies WHERE status = 'retired'")]
    except Exception as exc:
        return [_violation("retirement_unreadable",
                           f"strategies: {type(exc).__name__}: {exc}")]
    out = []
    for name in names:
        try:
            r = strategy_cell_expectancy(conn, name)
        except Exception:
            continue  # an unsimulatable book cannot be judged either way
        if (r["blended_n"] or 0) < RETIREMENT_MIN_N or r["dead"] is not False:
            continue
        best = r["best_cell"]
        out.append(_violation(
            "retired_while_profitable",
            f"'{name}' is retired, but it is not dead: "
            f"{best['regime_tag']} runs {best['expectancy_pct']:+.4f}% over "
            f"{best['n']} resolutions (lifetime blend "
            f"{r['blended_expectancy_pct']:+.4f}% hides it). A strategy is "
            f"dead when NO cell clears, not when the average does not — and "
            f"excluding it from the multiplicity count has lowered the hurdle "
            f"for every sibling at its timescale on a false premise. Move it "
            f"to dormant, or narrow it to the cell that works."))
    return out


def _check_regime_board(conn, home: Path) -> List[Dict[str, Any]]:
    """REGIME.md's table must still agree with the database.

    The database became truth on 2026-07-27 and the board a rendering. That
    arrangement has exactly one failure mode: a row lands, the render fails or
    is bypassed, and four agents go on reading a stale table while the desk
    believes it fresh. The Live State zone froze for a month in precisely this
    way, with no check to notice — so the check ships with the renderer, not
    after it.

    Silent when the desk has never assessed a regime: nothing to disagree with
    is not a disagreement.
    """
    try:
        from trading.lifecycle.queries import current_regime
        if not current_regime(conn):
            return []
        from trading.lifecycle.regime_board import board_matches_db
        if board_matches_db(conn, home / "REGIME.md"):
            return []
    except Exception as exc:
        return [_violation("regime_board_unreadable",
                           f"{type(exc).__name__}: {exc}")]
    return [_violation(
        "regime_board_stale",
        "REGIME.md's table disagrees with regime_observations — the board is "
        "a rendering of the database, so either a render failed or the file "
        "was hand-edited. Every agent reads the table as prompt text, so they "
        "are reasoning against a regime the desk no longer believes. Re-run "
        "record_regime, or read the notes to see who wrote over it.")]


CHECKS: Dict[str, Callable] = {
    "regime_board": _check_regime_board,
    "blackboard_bloat": _check_blackboard_bloat,
    "blackboard_zones": _check_blackboard_zones,
    "live_state_fresh": _check_live_state_fresh,
    "lessons_cap": _check_lessons_cap,
    "staleness_ceiling": _check_staleness_ceiling,
    "tables_reachable": _check_tables_reachable,
    "capital_recorded": _check_capital_recorded,
    "retirement_evidence": _check_retirement_evidence,
    "wake_loop": _check_wake_loop,
    "runtime_disk": _check_runtime_disk,
}


def check_integrity(conn, home: Optional[Path] = None) -> Dict[str, Any]:
    """Run every invariant. Silent when the desk is well.

    Returns ``{ok, violations, checks_run, checks_failed}``. A check that
    raises becomes a violation in its own right — a health check that fails
    open is worse than no health check, because it reports health.
    """
    if home is None:
        from harness.constants import get_hermes_home
        home = get_hermes_home()
    home = Path(home)

    violations: List[Dict[str, Any]] = []
    failed = []
    for name, fn in CHECKS.items():
        try:
            violations.extend(fn(conn, home))
        except Exception as exc:
            failed.append(name)
            violations.append(_violation(
                "integrity_check_failed",
                f"{name} raised {type(exc).__name__}: {exc}", "critical"))

    return {
        "ok": not violations,
        "violations": violations,
        "checks_run": len(CHECKS),
        "checks_failed": failed,
    }
