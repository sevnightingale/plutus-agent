"""Polymarket Gamma API client — public market-data reads, no auth.

The Gamma API is Polymarket's public-but-undocumented data layer; it can
change shape without notice, so every parse here FAILS LOUDLY: no active
event, an unreachable API, or an unrecognized payload raises — odds must
be real or absent, never guessed. The perception cache (15 min budget)
keeps request volume trivial.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests

GAMMA_BASE = "https://gamma-api.polymarket.com"
_TIMEOUT_S = 20


def _get(path: str, params: Dict[str, Any]) -> Any:
    resp = requests.get(f"{GAMMA_BASE}{path}", params=params, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def _parse_iso(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def current_event(series_slug: str) -> Dict[str, Any]:
    """The soonest-ending active event in a recurring series.

    A series ('btc-multi-strikes-weekly', 'fomc') lists several future
    events at once; the current one is the earliest endDate still ahead.
    """
    events = _get("/events", {
        "series_slug": series_slug,
        "active": "true",
        "closed": "false",
        "limit": 12,
    })
    if not isinstance(events, list) or not events:
        raise RuntimeError(
            f"no active Polymarket events for series '{series_slug}' — "
            "check the slug (e.g. btc-multi-strikes-weekly, fomc, cpi)"
        )
    now = datetime.now(timezone.utc)
    upcoming = sorted(
        ((end, ev) for ev in events
         if (end := _parse_iso(ev.get("endDate"))) and end > now),
        key=lambda pair: pair[0],
    )
    if not upcoming:
        raise RuntimeError(
            f"every listed event for '{series_slug}' is past its endDate"
        )
    return upcoming[0][1]


def outcome_prices(market: Dict[str, Any]) -> List[Tuple[str, float]]:
    """(label, price) pairs from a market's outcomes/outcomePrices.

    Gamma serializes both as JSON-encoded strings; tolerate real lists too.
    """
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not isinstance(outcomes, list) or not isinstance(prices, list) \
            or len(outcomes) != len(prices) or not outcomes:
        raise RuntimeError(
            f"unrecognized outcome shape on market "
            f"'{market.get('slug', '?')}': {outcomes!r} / {prices!r}"
        )
    return [(str(o), float(p)) for o, p in zip(outcomes, prices)]


def yes_price(market: Dict[str, Any]) -> float:
    """The 'Yes' price of a binary market — its implied probability."""
    for label, price in outcome_prices(market):
        if label.strip().lower() == "yes":
            return price
    raise RuntimeError(
        f"market '{market.get('slug', '?')}' has no 'Yes' outcome"
    )
