"""Calendar-gated data points: earnings + lock-up unlock schedules.

The desk's event-timing gates for the "must not fire into a print/unlock
window" rules (the standing NVDA earnings cap and the SPCX lock-up overhang
that generate declared via the self-extension hook, 2026-08-31).

Curated tables, following the FOMC-calendar precedent (scheduled news is
code): the table IS the source, with ``source`` + ``verified`` metadata so
staleness is visible. MAINTENANCE: extend/refresh when a date is confirmed
or printed — the desk's own board already tracks NVDA prints and the SPCX
unlock stages narratively; this DP makes the gate machine-readable.

**Where the gate is ENFORCED** (2026-08-31 review): ``register_prediction``
reads gate-tagged DPs at registration time. ``has_data=false`` refuses for
EVERY declaring book — a table gap fails CLOSED (extend the table or
undeclare the DP, never fire against an unknowable calendar). ``in_window``
refuses only for declarations marked ``event_gate: veto`` in the strategy
file — most declaring books are CATALYST books whose setup IS the window
(lockup fades, pre-print drift) and score ``days_to_next`` through their
normalizers instead; a blanket window refusal would gut them. This module
only *reports*; the dispatcher refuses.

``in_window`` uses conservative margins (earnings: 7d before the print;
lock-up: 14d before the unlock) and stays true through the day after the
event, so books stop registering into the window, not at it. When sources
DISAGREE on a date, the table carries every candidate as an event row —
the window then covers the union rather than trusting one source.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from trading.perception.core.data_point_registry import register_data_point

# --------------------------------------------------------------------------
# Curated calendars
# --------------------------------------------------------------------------

# Per-symbol next earnings print. Only tickers with a real earnings calendar
# belong here; everything else is has_data=false by construction.
_EARNINGS: Dict[str, Dict[str, Any]] = {
    "NVDA": {
        "last_printed": "2026-08-26",
        # Sources DISAGREE on the Q3 FY27 date (checked 2026-08-31): Wall
        # Street Horizon says 11-17, TipRanks/nextearningsdate say 11-25.
        # Both ride as event rows so the gate covers the union of windows;
        # collapse to one row when NVIDIA IR confirms.
        "next_status": "disputed",
        "events": [
            {"date": "2026-11-17", "note": "Q3 FY27 candidate (Wall Street Horizon)"},
            {"date": "2026-11-25", "note": "Q3 FY27 candidate (TipRanks, nextearningsdate)"},
        ],
        "source": "https://www.wallstreethorizon.com/nvidia-earnings-calendar",
        "verified": "2026-08-31",
        "note": "NVDA prints quarterly ~Aug/Nov/Feb/May; date disputed between sources — window covers both candidates.",
    },
}

# Per-symbol lock-up unlock stages (tokenized IPO/SPAC supply events).
# SPCX = tokenized SpaceX, IPO 2026-06-12 @ $135. Group-1 staged unlocks run
# Sep-Dec 2026; the 180-day full expiry is 2026-12-08 (the 455.8M price-
# trigger bonus shares rolled into it — SPCX never closed 30%+ above $135
# into the Q2 print); founder shares (~6.4B, ~63% of outstanding) unlock
# 2027-06-12.
_LOCKUPS: Dict[str, Dict[str, Any]] = {
    "SPCX": {
        "unlocks": [
            {"date": "2026-09-09", "shares_m": 319.0, "note": "Group-1 staged unlock (up to)"},
            {"date": "2026-09-24", "shares_m": None, "note": "Group-1 staged unlock"},
            {"date": "2026-10-09", "shares_m": None, "note": "Group-1 staged unlock"},
            {"date": "2026-10-24", "shares_m": None, "note": "Group-1 staged unlock"},
            {"date": "2026-12-08", "shares_m": None, "note": "180-day full expiry; 455.8M bonus shares rolled in"},
            {"date": "2027-06-12", "shares_m": 6400.0, "note": "Founder shares (~63% of outstanding)"},
        ],
        "source": "https://purepowerpicks.com/spacex-lockup-schedule",
        "verified": "2026-08-31",
        "note": "Staged unlocks per prospectus; amounts confirmed for 2026-09-09 (319.0M) only. Schedule corroborated 2026-08-31 against tokenomist.ai and investing.com.",
    },
}


def _iso_today() -> date:
    return datetime.now(timezone.utc).date()


def _days_until(event_date: str, today: date) -> float:
    d = date.fromisoformat(event_date)
    return float((d - today).days)


def _event_rows(events: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    rows = []
    for ev in events:
        rows.append({
            "date": ev["date"],
            "days": _days_until(ev["date"], today),
            "shares_m": ev.get("shares_m"),
            "note": ev.get("note", ""),
        })
    return rows


def _build(symbol: str, table: Optional[Dict[str, Any]], margin_days: int,
           kind: str) -> Dict[str, Any]:
    today = _iso_today()
    if table is None:
        return {
            "symbol": symbol,
            "has_data": False,
            "kind": kind,
            "next_event": None,
            "days_to_next": None,
            "in_window": False,
            "events": [],
            "source": None,
            "verified": None,
            "note": f"No {kind} calendar for {symbol}.",
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    events = (table.get("events") or table.get("unlocks")
              or [{"date": table["next"], "note": ""}])
    upcoming = [e for e in events if _days_until(e["date"], today) >= -1]
    next_ev = min(upcoming, key=lambda e: _days_until(e["date"], today)) if upcoming else None
    days = _days_until(next_ev["date"], today) if next_ev else None
    in_window = days is not None and -1 <= days <= margin_days
    return {
        "symbol": symbol,
        "has_data": True,
        "kind": kind,
        "next_event": next_ev["date"] if next_ev else None,
        "days_to_next": days,
        "in_window": in_window,
        "events": _event_rows(events, today),
        "source": table.get("source"),
        # Honest absence: a table without a verified stamp says so, rather
        # than a default fabricating a review date nobody performed.
        "verified": table.get("verified"),
        "note": table.get("note", ""),
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------
# Registered data points
# --------------------------------------------------------------------------


@register_data_point(
    name="earnings_calendar",
    category="macro",
    source="calendars",
    description=(
        "Next scheduled earnings print for a ticker (curated calendar; the "
        "desk's 'must not fire into earnings' gate, machine-readable). "
        "register_prediction refuses declaring books when has_data=false "
        "(no calendar for the symbol — a table gap fails closed) and, for "
        "declarations marked event_gate: veto, while in_window=true (print "
        "within the 7d pre-event margin, held through the day after). "
        "Catalyst books score days_to_next through their normalizers. "
        "Disputed dates ride as multiple event rows; the window covers all "
        "candidates."
    ),
    params_schema={"symbol": {"type": "string", "default": "NVDA", "required": False}},
    returns_schema={
        "symbol": "string",
        "has_data": "bool",
        "kind": "'earnings'",
        "next_event": "iso date|null",
        "days_to_next": "float|null (days until next print)",
        "in_window": "bool (within 7d pre-event margin)",
        "events": "list of {date, days, shares_m, note}",
        "source": "string|null",
        "verified": "string",
        "observed_at": "iso8601",
    },
    tags=("calendar", "earnings", "gate", "nvda", "freshness"),
    numeric_path="days_to_next",
)
def earnings_calendar(symbol: str = "NVDA") -> Dict[str, Any]:
    table = _EARNINGS.get(symbol.upper())
    return _build(symbol.upper(), table, margin_days=7, kind="earnings")


@register_data_point(
    name="ipo_lockup_calendar",
    category="market",
    source="calendars",
    description=(
        "Next lock-up unlock stage for a tokenized IPO/SPAC ticker (curated "
        "calendar; the standing SPCX supply-overhang gate). "
        "register_prediction refuses declaring books when has_data=false "
        "(no schedule for the symbol — fails closed) and, for declarations "
        "marked event_gate: veto, while in_window=true (unlock within the "
        "14d pre-event margin, held through the day after). Catalyst books "
        "score days_to_next through their normalizers."
    ),
    params_schema={"symbol": {"type": "string", "default": "SPCX", "required": False}},
    returns_schema={
        "symbol": "string",
        "has_data": "bool",
        "kind": "'lockup'",
        "next_event": "iso date|null",
        "days_to_next": "float|null (days until next unlock)",
        "in_window": "bool (within 14d pre-event margin)",
        "events": "list of {date, days, shares_m, note}",
        "source": "string|null",
        "verified": "string",
        "observed_at": "iso8601",
    },
    tags=("calendar", "lockup", "gate", "spcx", "supply"),
    numeric_path="days_to_next",
)
def ipo_lockup_calendar(symbol: str = "SPCX") -> Dict[str, Any]:
    table = _LOCKUPS.get(symbol.upper())
    return _build(symbol.upper(), table, margin_days=14, kind="lockup")
