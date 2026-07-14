"""Deterministic status sync — the one binary gate, code-owned.

``strategy_expectancy(...).tradeable`` IS the graduation bar; this makes the
status column FOLLOW it mechanically instead of waiting on reflect's weekly
judgment. Two failure modes it closes: a strategy that clears the bar but is
left in ``test`` idles the desk forever (``best_actionable_prediction`` joins
on ``status='active'``), and an ``active`` strategy that stops clearing
(decay, a raised hurdle, eroded expectancy) keeps its funding eligibility
until someone notices.

Runs after every resolution batch — the only moment books (and therefore
``tradeable``) change — via the resolution watcher, and on demand through the
``strategy_status_sync`` tool.

Deliberately narrow: only ``test ↔ active``. Dormancy is regime judgment
(reflect rotates it) and retirement is a research call (reflect, dead edge at
N ≥ 20) — code never makes those moves. Reflect keeps weights, lessons,
population pruning, and the NARRATIVE of every move; the sync only does the
arithmetic bookkeeping.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def sync_strategy_statuses(conn: sqlite3.Connection) -> list:
    """Promote ``test`` → ``active`` where tradeable; demote ``active`` →
    ``test`` where not. Returns the change records (empty when in sync).

    A strategy whose file is missing (mirror row without a file) is skipped
    with a warning — the file is truth and code must not invent one."""
    from trading.lifecycle import queries
    from trading.strategies import loader

    rows = conn.execute(
        "SELECT name, status FROM strategies WHERE status IN ('test', 'active')"
    ).fetchall()
    changes = []
    for r in rows:
        exp = queries.strategy_expectancy(conn, r["name"])
        if exp["tradeable"] and r["status"] == "test":
            new = "active"
        elif not exp["tradeable"] and r["status"] == "active":
            new = "test"
        else:
            continue
        try:
            loader.set_status(r["name"], new, conn)
        except FileNotFoundError as exc:
            logger.warning("status sync skipped %s (%s)", r["name"], exc)
            continue
        change = {"strategy": r["name"], "from": r["status"], "to": new,
                  "expectancy_pct": exp["expectancy_pct"],
                  "hurdle_pct": exp["hurdle_pct"], "n": exp["n"],
                  "decaying": exp["decaying"]}
        changes.append(change)
        logger.info("status sync: %s %s → %s (exp %s vs hurdle %s, n=%s%s)",
                    r["name"], r["status"], new, exp["expectancy_pct"],
                    exp["hurdle_pct"], exp["n"],
                    ", decaying" if exp["decaying"] else "")
    return changes
