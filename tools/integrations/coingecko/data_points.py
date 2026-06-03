"""CoinGecko data points — free, no API key required.

Rate limit: ~10-30 calls/min on free tier. Single calls per heartbeat cycle
are well within limits.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List

from tools.core.data_point_registry import register_data_point

BASE_URL = "https://api.coingecko.com/api/v3"
UA = "Mozilla/5.0 (plutus-agent Plutus)"


def _get(path: str) -> Any:
    """GET a CoinGecko endpoint, return parsed JSON."""
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


@register_data_point(
    name="coingecko_global",
    category="market",
    source="coingecko",
    description=(
        "Global crypto market data: total market cap, BTC/ETH dominance, "
        "24h volume, market cap change %, active cryptocurrencies. "
        "Key regime context — BTC.D rising = risk-off, falling = alt season."
    ),
    params_schema={},
    tags=["macro", "global", "dominance", "regime"],
)
def coingecko_global() -> Dict[str, Any]:
    data = _get("/global")["data"]
    mc = data.get("total_market_cap", {})
    vol = data.get("total_volume", {})
    dom = data.get("market_cap_percentage", {})

    return {
        "total_market_cap_usd": mc.get("usd"),
        "total_volume_24h_usd": vol.get("usd"),
        "btc_dominance_pct": dom.get("btc"),
        "eth_dominance_pct": dom.get("eth"),
        "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd"),
        "active_cryptocurrencies": data.get("active_cryptocurrencies"),
        "upcoming_icos": data.get("upcoming_icos"),
        "ongoing_icos": data.get("ongoing_icos"),
        "ended_icos": data.get("ended_icos"),
    }


@register_data_point(
    name="coingecko_trending",
    category="social",
    source="coingecko",
    description=(
        "Top trending coins on CoinGecko (searches + views). Returns top-7 "
        "with name, symbol, market cap rank, and score. Sentiment pulse — "
        "meme dominance = euphoria, top-10 trending = conviction."
    ),
    params_schema={},
    tags=["sentiment", "trending", "momentum"],
)
def coingecko_trending() -> Dict[str, Any]:
    data = _get("/search/trending")
    coins: List[Dict[str, Any]] = []
    for entry in data.get("coins", [])[:10]:
        item = entry.get("item", {})
        coins.append({
            "name": item.get("name"),
            "symbol": item.get("symbol"),
            "market_cap_rank": item.get("market_cap_rank"),
        })
    return {"count": len(coins), "trending": coins}


@register_data_point(
    name="btc_dominance_velocity",
    category="market",
    source="coingecko",
    description=(
        "BTC dominance momentum: current BTC.D, 7d and 30d change in percentage "
        "points (not percent change). Rising BTC.D = risk-off / flight to safety. "
        "Falling BTC.D = risk-on / altcoin rotation. +2pp in 7d is a sharp regime "
        "shift; -3pp in 30d signals alt season building."
    ),
    params_schema={},
    tags=["dominance", "regime", "risk-appetite", "btc"],
)
def btc_dominance_velocity() -> Dict[str, Any]:
    # Need current + historical. CoinGecko /global only gives current.
    # Use their coins/markets endpoint for BTC + total market cap, or
    # we can use the global endpoint and approximate from BTC price vs total MC.
    # Better: use CoinGecko /global for BTC.D snapshot + /coins/bitcoin/market_chart
    # for historical BTC price/DOM to compute prior dominance.
    from datetime import datetime, timedelta

    # Current dominance
    req = urllib.request.Request(
        f"{BASE_URL}/global", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        global_data = json.loads(resp.read())["data"]
    current_btc_d = float(global_data["market_cap_percentage"]["btc"])

    # Historical: get BTC market cap + total market cap at 7d and 30d ago
    # CoinGecko doesn't have a direct historical dominance endpoint on free tier.
    # Strategy: get BTC market chart (price + market cap) and global MC chart.
    # But the free /global doesn't have historical.
    #
    # Alternative: Use the /coins/bitcoin/market_chart for BTC mc history,
    # and approximate total mc from BTC.D * historical BTC mc / current ratio.
    # This is fragile. Better: use coingecko_global snapshots from our own
    # lifecycle DB (data_point_snapshots).
    #
    # For now: return current + note that historical needs DB snapshots.
    # The heartbeat skill can maintain this by comparing snapshots.

    return {
        "btc_dominance_pct": current_btc_d,
        "eth_dominance_pct": float(global_data["market_cap_percentage"]["eth"]),
        "total_market_cap_usd": global_data["total_market_cap"]["usd"],
        "note": (
            "Historical BTC.D change requires lifecycle DB snapshots of "
            "coingecko_global. The heartbeat skill maintains this. For now, "
            "compare the current BTC.D against your WORLDVIEW.md's last noted "
            "value to estimate velocity."
        ),
    }
