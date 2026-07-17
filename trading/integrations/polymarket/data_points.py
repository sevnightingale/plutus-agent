"""Polymarket data points: price ladders + recurring-event odds.

``poly_price_ladder`` reads the weekly multi-strike event — a ladder of
binary markets "close above $X at the event date" — which is market-implied
P(price above strike): the same quantity Plutus's price-zone predictions
estimate, priced by real money. ``poly_event_odds`` reads any recurring
series (FOMC, CPI, daily up-or-down) as an outcome table.

The monthly hit-price series is deliberately NOT a ladder DP: "touch $X at
any point" probabilities are not monotone in strike and produce no clean
scalar — consume it via poly_event_odds if the narrative matters.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading.integrations.hyperliquid.data_points import hl_price as _hl_price
from trading.perception.core.data_point_registry import register_data_point

from ._client import _parse_iso, current_event, outcome_prices, yes_price

# Stable series slugs for the weekly close-above strike ladders.
_LADDER_SERIES = {
    "BTC": "btc-multi-strikes-weekly",
    "ETH": "ethereum-multi-strikes-weekly",
}


def _parse_strike(title: Any) -> Optional[float]:
    """'54,000' / '$54,000' / '58K' → float; None when not a price."""
    if not isinstance(title, str) or not title.strip():
        return None
    text = title.strip().replace("$", "").replace(",", "").replace(" ", "")
    mult = 1.0
    if text[-1:].lower() == "k":
        text, mult = text[:-1], 1000.0
    try:
        return float(text) * mult
    except ValueError:
        return None


def _build_ladder(markets: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    """[{strike, p_above}] ascending from a multi-strike event's markets."""
    rows = []
    for m in markets:
        strike = _parse_strike(m.get("groupItemTitle"))
        if strike is None:
            continue
        rows.append({"strike": strike, "p_above": yes_price(m)})
    if len(rows) < 2:
        raise RuntimeError(
            f"only {len(rows)} parseable strikes in event — not a ladder"
        )
    return sorted(rows, key=lambda r: r["strike"])


def _interp_p_above(ladder: List[Dict[str, float]], spot: float) -> Tuple[float, bool]:
    """P(above spot) linearly interpolated between bracketing strikes.

    Outside the ladder range the end probability is used and the read is
    flagged extrapolated — never invented beyond what the market priced.
    """
    if spot <= ladder[0]["strike"]:
        return ladder[0]["p_above"], True
    if spot >= ladder[-1]["strike"]:
        return ladder[-1]["p_above"], True
    for lo, hi in zip(ladder, ladder[1:]):
        if lo["strike"] <= spot <= hi["strike"]:
            frac = (spot - lo["strike"]) / (hi["strike"] - lo["strike"])
            return lo["p_above"] + frac * (hi["p_above"] - lo["p_above"]), False
    raise RuntimeError("spot fell through the ladder — strikes unsorted?")


def _implied_median(ladder: List[Dict[str, float]]) -> Optional[float]:
    """The strike where P(above) crosses 0.5 — the market's median close."""
    for lo, hi in zip(ladder, ladder[1:]):
        if lo["p_above"] >= 0.5 >= hi["p_above"]:
            span = lo["p_above"] - hi["p_above"]
            if span <= 0:
                return None
            frac = (lo["p_above"] - 0.5) / span
            return lo["strike"] + frac * (hi["strike"] - lo["strike"])
    return None


def _hours_to(end: Optional[datetime]) -> Optional[float]:
    if end is None:
        return None
    return round((end - datetime.now(timezone.utc)).total_seconds() / 3600.0, 1)


@register_data_point(
    name="poly_price_ladder",
    category="market",
    source="polymarket",
    description=(
        "Market-implied probability ladder from Polymarket's weekly "
        "multi-strike event: P(close above $strike at the event close) per "
        "strike, real-money priced. p_above_spot interpolates the ladder at "
        "the current HL price — the market's own probability that price "
        "holds above here; implied_median is the strike where P crosses "
        "0.5 (the market's median expected close). hours_to_close bounds "
        "the horizon (0-7d — suits intraday/swing theses). Evidence "
        "orthogonal to TA/flow; extrapolated=true means spot sat outside "
        "the priced strikes, so p_above_spot is a clamped end value."
    ),
    params_schema={
        "symbol": {"type": "string", "default": "BTC"},
    },
    returns_schema={
        "p_above_spot": "float 0-1", "extrapolated": "bool",
        "implied_median": "float|null", "spot": "float",
        "closes_at": "iso8601", "hours_to_close": "float",
        "ladder": "list of {strike, p_above} ascending",
    },
    tags=["market", "prediction-market", "odds", "positioning", "polymarket"],
    numeric_path="p_above_spot",
)
def poly_price_ladder(symbol: str = "BTC") -> Dict[str, Any]:
    series = _LADDER_SERIES.get(symbol.upper())
    if series is None:
        raise ValueError(
            f"no Polymarket strike-ladder series for '{symbol}' — "
            f"supported: {sorted(_LADDER_SERIES)}"
        )
    event = current_event(series)
    ladder = _build_ladder(event.get("markets") or [])
    spot = float(_hl_price(symbol.upper())["price"])
    p_above, extrapolated = _interp_p_above(ladder, spot)
    end = _parse_iso(event.get("endDate"))
    return {
        "symbol": symbol.upper(),
        "series": series,
        "event_slug": event.get("slug"),
        "closes_at": event.get("endDate"),
        "hours_to_close": _hours_to(end),
        "spot": spot,
        "p_above_spot": p_above,
        "extrapolated": extrapolated,
        "implied_median": _implied_median(ladder),
        "ladder": ladder,
    }


@register_data_point(
    name="poly_event_odds",
    category="market",
    source="polymarket",
    description=(
        "Outcome odds for the current event of any recurring Polymarket "
        "series, by stable series slug: 'fomc' (rate decision), 'cpi', "
        "'us-annual-inflation', 'btc-up-or-down-daily', "
        "'eth-up-or-down-daily'. Returns the outcome table sorted by "
        "implied probability; p_top is the consensus outcome's probability "
        "(e.g. P(no change) into an FOMC). Real-money event odds — use for "
        "event/narrative theses and catalyst positioning; an unknown slug "
        "fails loudly."
    ),
    params_schema={
        "series_slug": {"type": "string", "required": True},
    },
    returns_schema={
        "title": "string", "closes_at": "iso8601", "hours_to_close": "float",
        "outcomes": "list of {outcome, p} desc", "top_outcome": "string",
        "p_top": "float 0-1",
    },
    tags=["market", "prediction-market", "odds", "event", "macro", "polymarket"],
    numeric_path="p_top",
)
def poly_event_odds(series_slug: str) -> Dict[str, Any]:
    event = current_event(series_slug)
    markets = event.get("markets") or []
    if not markets:
        raise RuntimeError(f"event '{event.get('slug')}' has no markets")
    if len(markets) == 1:
        # Single-market event (e.g. up-or-down): its own outcome labels.
        rows = [{"outcome": label, "p": price}
                for label, price in outcome_prices(markets[0])]
    else:
        # Multi-market event (FOMC, strikes): one 'Yes' probability per
        # sub-market, labeled by its group title.
        rows = [
            {"outcome": str(m.get("groupItemTitle") or m.get("question") or "?"),
             "p": yes_price(m)}
            for m in markets
        ]
    rows.sort(key=lambda r: -r["p"])
    end = _parse_iso(event.get("endDate"))
    return {
        "series_slug": series_slug,
        "event_slug": event.get("slug"),
        "title": event.get("title"),
        "closes_at": event.get("endDate"),
        "hours_to_close": _hours_to(end),
        "outcomes": rows[:10],
        "top_outcome": rows[0]["outcome"],
        "p_top": rows[0]["p"],
    }
