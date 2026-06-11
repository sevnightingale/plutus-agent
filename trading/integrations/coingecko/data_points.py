"""CoinGecko data points — free, no API key required.

Rate limit: ~10-30 calls/min on free tier. Single calls per heartbeat cycle
are well within limits.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List

from trading.perception.core.data_point_registry import register_data_point

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
        "Top trending coins on CoinGecko (searches + views), up to 10, with "
        "name, symbol, market cap rank, and trending score (0 = hottest). "
        "Sentiment pulse — meme dominance = euphoria, majors trending = "
        "broad conviction."
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
            "score": item.get("score"),
        })
    return {"count": len(coins), "trending": coins}


def _historical_dominance(lookback_days: float, tolerance_days: float = 2.0):
    """BTC.D as of ~lookback_days ago, from our own data_point_snapshots.

    CoinGecko's free tier has no historical-dominance endpoint, but every
    coingecko_global / btc_dominance_velocity fetch is auto-snapshotted —
    the desk accumulates its own history just by perceiving. Returns the
    reading closest to the target time (within ±tolerance_days), or None
    when history doesn't reach back that far yet.
    """
    import time as _time

    from trading.lifecycle.db import get_db

    target = _time.time() - lookback_days * 86400
    lo = target - tolerance_days * 86400
    hi = target + tolerance_days * 86400
    rows = get_db().execute(
        "SELECT ts, value_json FROM data_point_snapshots "
        "WHERE name IN ('coingecko_global', 'btc_dominance_velocity') "
        "AND ts BETWEEN ? AND ? ORDER BY ABS(ts - ?) LIMIT 5",
        (lo, hi, target),
    ).fetchall()
    for ts, value_json in rows:
        try:
            value = json.loads(value_json)
            btc_d = value.get("btc_dominance_pct")
            if isinstance(btc_d, (int, float)):
                return {"btc_dominance_pct": float(btc_d),
                        "age_days": round((_time.time() - ts) / 86400, 1)}
        except (TypeError, ValueError):
            continue
    return None


@register_data_point(
    name="btc_dominance_velocity",
    category="market",
    source="coingecko",
    description=(
        "BTC dominance momentum: current BTC.D plus 7d and 30d change in "
        "percentage points (computed from the desk's own snapshot history; "
        "null with an honest note until enough history accumulates). Rising "
        "BTC.D = risk-off / flight to safety. Falling BTC.D = risk-on / "
        "altcoin rotation. +2pp in 7d is a sharp regime shift; -3pp in 30d "
        "signals alt season building."
    ),
    params_schema={},
    tags=["dominance", "regime", "risk-appetite", "btc"],
    numeric_path="btc_dominance_pct",
)
def btc_dominance_velocity() -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{BASE_URL}/global", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        global_data = json.loads(resp.read())["data"]
    current_btc_d = float(global_data["market_cap_percentage"]["btc"])

    out: Dict[str, Any] = {
        "btc_dominance_pct": current_btc_d,
        "eth_dominance_pct": float(global_data["market_cap_percentage"]["eth"]),
        "total_market_cap_usd": global_data["total_market_cap"]["usd"],
    }
    missing = []
    for label, days in (("7d", 7), ("30d", 30)):
        past = _historical_dominance(days)
        if past is None:
            out[f"change_{label}_pp"] = None
            missing.append(label)
        else:
            out[f"change_{label}_pp"] = round(
                current_btc_d - past["btc_dominance_pct"], 2)
            out[f"baseline_{label}_age_days"] = past["age_days"]
    if missing:
        out["note"] = (
            f"no snapshot history yet for {'/'.join(missing)} baselines — "
            f"velocity fills in as the desk keeps perceiving (snapshots "
            f"accumulate automatically)"
        )
    return out
