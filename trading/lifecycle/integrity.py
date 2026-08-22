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

import json
import logging
import re
import time
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
# A wake key's ``consecutive`` counter resets only on its NEXT fire, so after
# the condition clears the last count lingers in wake_suppression.json
# indefinitely. While a condition holds, the floor re-fires every ops tick
# (30 min) — so a key silent for 2.5 ticks has stopped looping, whatever its
# counter still reads. Found 2026-08-09: 'staleness:perception' reported
# fired-9x for ~90 minutes after the 06:38Z run cleared it (board #480).
WAKE_LOOP_STALE_S = 4500
# Runtime disk bound. The 1.2 GB accretion that motivated the original
# maintenance beat is exactly what this catches returning.
RUNTIME_DISK_MAX_MB = 2048
# Watcher-daemon fd ceiling. The daemon is a long-lived holder of
# lifecycle.db connections, so an unclosed get_db() shows up here first.
# Soft limit is typically 1024. The bound is set against a MEASURED healthy
# baseline, not reasoned down from the limit: a fixed daemon holds 6
# descriptors steady (measured 2026-08-22, flat across a 7-minute watch,
# against 416 and climbing before the leak was closed). 250 is therefore
# ~40x headroom over health and well under the limit — a breach means a
# leak, not a busy day.
WATCHER_FD_MAX = 250
# Resolutions a book needs before its lifetime expectancy is evidence enough
# to retire on. Mirrors the reflect protocol's bar; kept here because this is
# where it is ENFORCED, and retirement now moves the graduation hurdle.
RETIREMENT_MIN_N = 20

# Tables that MUST have rows on a desk that has been running. Emptiness here
# is the signature of an unreachable table — reflections carried it for 12
# reflect passes, capital_movements since inception.
EXPECTED_NONEMPTY = ("strategies", "predictions", "action_runs")
# Under file_read_max_chars (100k) so an agent can read its own board.
# PERCEPTION.md hit 133k on 2026-08-12 and perception's read_file bounced.
BLACKBOARD_MAX_CHARS = 80_000
# A full-tier symbol with no recent observation cannot be predicted into —
# regime_eligible is None, which predict correctly treats as "not a candidate".
REGIME_SYMBOL_MAX_AGE_S = 24 * 3600


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
        if len(text) > BLACKBOARD_MAX_CHARS:
            out.append(_violation(
                "blackboard_oversized",
                f"{name} is {len(text):,} chars (cap {BLACKBOARD_MAX_CHARS:,}) "
                f"— it no longer fits in a read_file and rides into every "
                f"regime/predict/generate spawn"))
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
    now = time.time()
    for key, entry in sorted(state.items()):
        n = int((entry or {}).get("consecutive") or 0)
        if n < WAKE_LOOP_CONSECUTIVE:
            continue
        age = now - float((entry or {}).get("last_fired_ts") or 0)
        if age > WAKE_LOOP_STALE_S:
            continue    # the loop has stopped firing — cleared, not declined
        out.append(_violation(
            "wake_loop",
            f"'{key}' has fired {n} times consecutively without the "
            f"condition clearing (last fire {age/60:.0f}m ago) — it is "
            f"being declined, not handled"))
    return out


# Tables the desk treats as append-only history. Twice now rows have
# vanished from one of them via an unrecorded hand-repair (board #481:
# regime_observations lost its 180 backfilled rows 07-28 and again before
# 08-02, both times silently — the occupancy instrument thought it had 90
# days and had 12). Row counts are checkpointed in append_only_counts.json;
# a count that goes DOWN is a violation naming the table and the delta.
# Deliberate operator wipes will trip it once — loud is the point.
APPEND_ONLY_TABLES = ("regime_observations", "observations", "reflections",
                      "capital_movements", "predictions")


def _check_append_only(conn, home: Path) -> List[Dict[str, Any]]:
    import json
    path = home / "append_only_counts.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8") or "{}") \
            if path.exists() else {}
    except Exception:
        state = {}
    counts, out = dict(state.get("counts") or {}), []
    events = list(state.get("events") or [])
    for table in APPEND_ONLY_TABLES:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            continue                    # tables_reachable owns that failure
        prev = counts.get(table)
        if prev is not None and n < prev:
            out.append(_violation(
                "append_only_shrunk",
                f"{table} shrank {prev} → {n} ({n - prev}) since the last "
                f"tick — an append-only table lost rows and nothing "
                f"recorded doing it"))
            events.append({"ts": time.time(), "table": table,
                           "from": prev, "to": n})
        counts[table] = n
    try:
        path.write_text(json.dumps(
            {"counts": counts, "events": events[-20:]}, indent=1),
            encoding="utf-8")
    except Exception as exc:
        logger.warning("append_only_counts.json write failed: %s", exc)
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


def _check_no_dormant(conn, home: Path) -> List[Dict[str, Any]]:
    """Dormant is abolished. Any leftover row is a writer that did not hear.

    Parked-and-still-on-the-bar was a one-way tax. Withdrawn books are
    retired: they leave M, the files stay, generate reads them.
    """
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM strategies WHERE status = 'dormant'")]
    except Exception as exc:
        return [_violation("dormant_unreadable",
                           f"strategies: {type(exc).__name__}: {exc}")]
    if not names:
        return []
    sample = ", ".join(names[:8])
    extra = f" (+{len(names) - 8} more)" if len(names) > 8 else ""
    return [_violation(
        "dormant_status",
        f"{len(names)} book(s) still marked dormant (abolished 2026-08-13): "
        f"{sample}{extra} — retire them")]


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


def _check_tool_registry(conn, home: Path) -> List[Dict[str, Any]]:
    """Every declared tool must actually exist, and every agent's toolsets resolve.

    The failure this exists for is silent by construction. A dispatcher that
    fails to import takes its toolset with it; the agent declaring that toolset
    still spawns, just without the tool; and its procedure — written around a
    tool that is no longer there — gets done by hand instead. Nothing errors.
    The desk simply stops recording something and carries on looking healthy.

    That is not hypothetical: `record_regime` shipped on 2026-07-27 importing
    `harness.tools.result`, a module that has never existed. Discovery logged
    one warning and moved on, the full suite stayed green (the discovery test
    mocks `import_module`, so it asserts the file *says* register, never that it
    imports), and plutus-regime spent the night hand-patching REGIME.md while
    `regime_observations` went stale behind it.

    Two assertions, both cheap: nothing failed to import, and no AGENT.md
    declares a toolset the registry cannot serve.
    """
    out: List[Dict[str, Any]] = []
    try:
        from harness.tools.registry import (
            builtin_discovery_ran, builtin_import_failures, registry)
    except Exception as exc:
        return [_violation("tool_registry_unreadable", f"{type(exc).__name__}: {exc}")]

    for mod_name, err in builtin_import_failures():
        out.append(_violation(
            "tool_module_import_failed",
            f"{mod_name} declares tools but failed to import ({err}). Every "
            f"tool it registers is silently absent — any agent whose procedure "
            f"depends on one is doing that work by hand, or not at all.",
            "critical"))

    # Where discovery has not run the registry is incomplete by construction —
    # absence of evidence, not evidence of absence. Assert nothing rather than
    # accuse every agent of declaring a phantom toolset.
    if builtin_discovery_ran():
        from harness.spawn import AGENTS_DIR, load_agent
        for agent_dir in sorted(Path(AGENTS_DIR).glob("plutus-*")):
            if not (agent_dir / "AGENT.md").exists():
                continue
            try:
                spec = load_agent(agent_dir.name)
            except Exception as exc:
                out.append(_violation(
                    "agent_spec_unreadable",
                    f"{agent_dir.name}: {type(exc).__name__}: {exc}"))
                continue
            for ts in spec.toolsets:
                if (not registry.get_tool_names_for_toolset(ts)
                        and not registry.get_toolset_alias_target(ts)):
                    out.append(_violation(
                        "agent_toolset_missing",
                        f"{agent_dir.name} declares toolset '{ts}', which "
                        f"resolves to no registered tool. It will spawn short "
                        f"the tools its procedure assumes.",
                        "critical"))
    return out


def _check_file_db_status(conn, home: Path) -> List[Dict[str, Any]]:
    """File-is-truth; the db is a mirror. A writer that updates one surface
    leaves the other lying, and predict loads from files — so 42 parents
    the 2026-07-27 cell-split marked parents withdrawn in the db and
    left the files on test — predict loads from files."""
    from trading.strategies.files import parse_strategy

    base = Path(home) / "strategies"
    if not base.is_dir():
        return []
    db_status = {r[0]: r[1] for r in conn.execute(
        "SELECT name, status FROM strategies")}
    mismatches = []
    for path in sorted(base.glob("*.md")):
        try:
            s = parse_strategy(path)
        except Exception:
            continue
        db_s = db_status.get(s.name)
        if db_s is not None and db_s != s.status:
            mismatches.append(f"{s.name} file={s.status} db={db_s}")
    if not mismatches:
        return []
    sample = "; ".join(mismatches[:8])
    extra = f" (+{len(mismatches) - 8} more)" if len(mismatches) > 8 else ""
    return [_violation(
        "file_db_status",
        f"{len(mismatches)} strateg(ies) disagree between file and db "
        f"(file is truth): {sample}{extra}")]


def _check_regime_symbols(conn, home: Path) -> List[Dict[str, Any]]:
    """Full-tier symbols with no recent observation: predict cannot match
    their books. Silent on a desk that has never assessed anything (cold
    start), loud when the desk is assessing some symbols and skipping
    others — the 2026-08-12 shape, 66 BTC rows and zero for the other six."""
    try:
        any_ts = conn.execute(
            "SELECT max(ts) FROM regime_observations").fetchone()[0]
    except Exception:
        return []
    if not any_ts:
        return []
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM strategies "
            "WHERE status IN ('test','active') AND symbol IS NOT NULL"
        ).fetchall()
    except Exception:
        return []
    worked = {str(r[0]) for r in rows if r[0]}
    if not worked:
        return []
    now = time.time()
    out = []
    for sym in sorted(worked):
        last = conn.execute(
            "SELECT max(ts) FROM regime_observations WHERE symbol=?",
            (sym,)).fetchone()[0]
        if last is None or (now - float(last)) > REGIME_SYMBOL_MAX_AGE_S:
            age = "never" if last is None else f"{(now - float(last))/3600:.1f}h ago"
            out.append(_violation(
                "regime_symbol_unassessed",
                f"{sym} has live books but no regime observation "
                f"({age}) — predict cannot match them"))
    return out


def _check_tool_schema_shape(conn, home: Path) -> List[Dict[str, Any]]:
    """The OpenAI wire key is `parameters`. A schema published under
    `input_schema` reaches the model as an empty argument list — which is
    how record_regime KeyError'd on 'timescale' for two days (#419)."""
    from harness.tools.registry import builtin_discovery_ran, registry

    if not builtin_discovery_ran():
        return []
    out = []
    for name in registry.get_all_tool_names():
        schema = registry.get_schema(name) or {}
        if "input_schema" in schema and "parameters" not in schema:
            out.append(_violation(
                "tool_schema_shape",
                f"{name} publishes `input_schema` (Anthropic) instead of "
                f"`parameters` — the model will not see its fields",
                "critical"))
        elif "parameters" not in schema and schema.get("name"):
            # A few harness tools are argument-free; they still ship
            # parameters: {type:object, properties:{}}. Flag only when
            # the schema looks like a function but has neither key.
            pass
    return out




def _watcher_pid() -> Optional[int]:
    """PID of the watcher daemon, or None if no process manager knows it.

    Deployment-agnostic on purpose. The manor runs the daemon as a systemd
    unit; the OSS tree runs it under pm2 (``ecosystem.config.js``, and
    ``harness/watchers/run.py``'s own docstring). A check hardcoded to one of
    them is dead on every install using the other, which is a check that
    cannot go red pretending to be coverage.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", "plutus-watchers"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        pid = int(out or 0)
        if pid > 0:
            return pid
    except Exception as exc:
        logger.debug("systemd watcher pid lookup failed: %s", exc)

    try:
        out = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            for proc in json.loads(out.stdout):
                if proc.get("name") == "plutus-watchers":
                    pid = int((proc.get("pid") or 0))
                    if pid > 0:
                        return pid
    except Exception as exc:
        logger.debug("pm2 watcher pid lookup failed: %s", exc)

    return None


def _check_watcher_fds(conn, home: Path) -> List[Dict[str, Any]]:
    """Descriptor pressure in the watcher daemon.

    ``get_db()`` hands back a NEW sqlite connection every call and no caller
    closes it. In the short-lived subagent processes that is invisible: the
    OS reclaims at exit. The watcher daemon runs for days, so an unclosed
    connection on a polling path leaks one descriptor per tick.

    It matters because of HOW it fails. On 2026-08-16 two Hyperliquid pollers
    exhausted the limit and the daemon began logging "watcher_state corrupt"
    — it could no longer open its own state file. That reads like a data
    problem, so it sat for six days and 1,326 log lines while the alert path
    was intermittently blind. This invariant names the real cause before the
    misleading symptom appears.

    Linux-only by construction (/proc); silently skipped elsewhere.

    Scoped to the REAL runtime home. This is the one invariant that inspects
    a live host process rather than the database in front of it, so running
    it against a temp home would make a unit test's verdict depend on the
    state of whatever machine it runs on.
    """
    try:
        from harness.constants import get_hermes_home
        if home.resolve() != get_hermes_home().resolve():
            return []          # not the live runtime — nothing to inspect
    except Exception as exc:
        logger.debug("runtime home resolution failed: %s", exc)
        return []

    pid_path = Path("/proc")
    if not pid_path.exists():
        return []
    pid = _watcher_pid()
    if pid is None:
        # NOT all-clear, but not a violation either: a stopped daemon is a
        # different check's business, and shouting here would fire on every
        # install where the daemon is simply down. The distinction that
        # matters — "we asked both process managers and neither knows it" —
        # is what _watcher_pid() already logs.
        return []
    fd_dir = pid_path / str(pid) / "fd"
    try:
        n = len(list(fd_dir.iterdir()))
    except OSError as exc:
        # Cannot read another process's fd table (permissions). A check that
        # cannot resolve its target must not report all-clear.
        return [_violation(
            "watcher_fds",
            f"could not read {fd_dir} ({exc}) — descriptor pressure UNKNOWN, "
            f"not verified clear")]
    if n > WATCHER_FD_MAX:
        return [_violation(
            "watcher_fds",
            f"plutus-watchers holds {n} descriptors (bound {WATCHER_FD_MAX}) — "
            f"a polling path is opening lifecycle.db without closing it; "
            f"expect 'watcher_state corrupt' next, which will look like a "
            f"data fault and is not one",
            severity="error")]
    return []


# Consecutive parseable predict reports that must carry an escalation before
# agent_escalations fires — one report can be a transient (a single feed
# lagging); two in a row is a condition.
ESCALATION_CONSECUTIVE_N = 2


def _check_agent_escalations(conn, home: Path) -> List[Dict[str, Any]]:
    """A specialist saying "I am blocked" must reach the operator loop.

    plutus-predict's report contract carries ``escalation_findings`` and
    ``perception_stale`` precisely so a blocked beat can say so — and on
    2026-08-20..21 it did, three beats running, naming the sweep-manifest gap
    exactly, into a column nothing read. Every run was ok=1 so every watchdog
    saw health (board #657 — the writer worked, the READER was missing: the
    same family as reflections and capital_movements, from the other side).

    This is the reader. The newest ESCALATION_CONSECUTIVE_N parseable ok=1
    predict rows all carrying non-empty escalation_findings — or all carrying
    non-empty perception_stale, which since #658 means refresh FAILURES
    rather than plain staleness — is a violation, which ops escalates into
    main's wake queue like any other. Legacy prefix-truncated rows do not
    parse and are skipped; they age out of the window on their own.
    """
    try:
        rows = conn.execute(
            "SELECT notes_md FROM action_runs "
            "WHERE action_type = 'predict' AND ok = 1 "
            "ORDER BY ts DESC LIMIT 10").fetchall()
    except Exception as exc:
        return [_violation("agent_escalations_unreadable",
                           f"action_runs: {type(exc).__name__}: {exc}")]
    reports: List[Dict[str, Any]] = []
    for r in rows:
        try:
            doc = json.loads(r[0] or "")
        except (ValueError, TypeError):
            continue
        if isinstance(doc, dict):
            reports.append(doc)
        if len(reports) >= ESCALATION_CONSECUTIVE_N:
            break
    if len(reports) < ESCALATION_CONSECUTIVE_N:
        return []
    out = []
    for key, label in (("escalation_findings", "escalation findings"),
                       ("perception_stale", "perception refresh failures")):
        if all(doc.get(key) for doc in reports):
            newest = reports[0][key]
            sample = newest[0] if isinstance(newest, list) and newest else newest
            out.append(_violation(
                "agent_escalations",
                f"plutus-predict reported {label} on "
                f"{ESCALATION_CONSECUTIVE_N} consecutive runs and nothing has "
                f"acted — newest: {str(sample)[:300]}"))
    return out


CHECKS: Dict[str, Callable] = {
    "tool_registry": _check_tool_registry,
    "tool_schema_shape": _check_tool_schema_shape,
    "regime_board": _check_regime_board,
    "regime_symbols": _check_regime_symbols,
    "file_db_status": _check_file_db_status,
    "blackboard_bloat": _check_blackboard_bloat,
    "blackboard_zones": _check_blackboard_zones,
    "live_state_fresh": _check_live_state_fresh,
    "lessons_cap": _check_lessons_cap,
    "staleness_ceiling": _check_staleness_ceiling,
    "tables_reachable": _check_tables_reachable,
    "capital_recorded": _check_capital_recorded,
    "no_dormant": _check_no_dormant,
    "wake_loop": _check_wake_loop,
    "runtime_disk": _check_runtime_disk,
    "append_only": _check_append_only,
    "watcher_fds": _check_watcher_fds,
    "agent_escalations": _check_agent_escalations,
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
