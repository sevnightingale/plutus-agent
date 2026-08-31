"""The macro event calendar — scheduled news is code, not a news poll.

The sustainable-desk scoping's third event kind: events whose *timing* is
deterministic even though their content is not. v3.0 carries the FOMC
decision calendar only — the dates are published by the Fed a year ahead
and the decision statement lands at 14:00 ET on day two. CPI/PCE/earnings
calendars stay deferred with board #486 (their schedules move and deserve
a fetched source, not a table someone forgets to update).

MAINTENANCE: extend ``EVENTS`` when the Fed publishes the next year's
schedule. An empty upcoming list past the table's horizon is honest —
``horizon_note`` says so rather than pretending coverage.

Consumed by the event engine's predict predicate (a print since the last
predict run wakes a beat) — never by resolution criteria.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# (epoch seconds UTC of the print, label). FOMC 2026 per the published
# schedule; decision statement 14:00 ET on day two (18:00 UTC under EDT,
# 19:00 UTC under EST).
EVENTS: tuple = (
    (1769626800.0, "FOMC decision 2026-01-28 19:00Z"),
    (1773860400.0, "FOMC decision 2026-03-18 19:00Z"),
    (1777485600.0, "FOMC decision 2026-04-29 18:00Z"),
    (1781719200.0, "FOMC decision 2026-06-17 18:00Z"),
    (1785348000.0, "FOMC decision 2026-07-29 18:00Z"),
    (1789581600.0, "FOMC decision 2026-09-16 18:00Z"),
    (1793210400.0, "FOMC decision 2026-10-28 18:00Z"),
    (1796842800.0, "FOMC decision 2026-12-09 19:00Z"),
)


def upcoming(now: Optional[float] = None,
             within_s: float = 7 * 24 * 3600) -> List[Dict[str, Any]]:
    now = now or time.time()
    return [{"ts": ts, "label": label, "in_s": round(ts - now)}
            for ts, label in EVENTS if now < ts <= now + within_s]


def printed_since(since_ts: float,
                  now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The most recent event printed after ``since_ts`` and not in the
    future — the predict predicate's clause."""
    now = now or time.time()
    hits = [(ts, label) for ts, label in EVENTS if since_ts < ts <= now]
    if not hits:
        return None
    ts, label = max(hits)
    return {"ts": ts, "label": label, "ago_s": round(now - ts)}


def horizon_note(now: Optional[float] = None) -> Optional[str]:
    """Non-None when the table has run out of future events — the honest
    signal to extend it rather than silently covering nothing."""
    now = now or time.time()
    if any(ts > now for ts, _ in EVENTS):
        return None
    return ("macro calendar table has no future events — extend EVENTS in "
            "trading/integrations/macro/calendar.py from the Fed's published "
            "schedule")
