"""DefiLlama data points — free, no API key required.

TVL data for chains and protocols. Shows where capital is flowing
in the DeFi ecosystem. Complements CoinGecko global for regime context.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List

from trading.perception.core.data_point_registry import register_data_point

BASE_URL = "https://api.llama.fi"

# Chains we care about (filter from 440+ to these + top-N by TVL)
WATCH_CHAINS = {
    "Ethereum", "Solana", "Bitcoin", "Base", "Arbitrum", "Polygon",
    "Optimism", "Avalanche", "BNB Chain", "Tron", "Sui", "Hyperliquid",
}


def _get(path: str) -> Any:
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


@register_data_point(
    name="defillama_tvl_chains",
    category="on_chain",
    source="defillama",
    description=(
        "Per-chain Total Value Locked in DeFi. Returns TVL for major chains "
        "(Ethereum, Solana, Bitcoin, Base, Arbitrum, etc.) plus top-5 by TVL. "
        "Shows where capital is deployed across the ecosystem."
    ),
    params_schema={},
    tags=["tvl", "defi", "on-chain", "capital-flow"],
)
def defillama_tvl_chains() -> Dict[str, Any]:
    data = _get("/v2/chains")

    watch: Dict[str, float] = {}
    others: List[Dict[str, Any]] = []

    for chain in data:
        name = chain.get("name", "")
        tvl = float(chain.get("tvl", 0))
        if name in WATCH_CHAINS:
            watch[name] = tvl
        elif tvl > 0:
            others.append({"name": name, "tvl": tvl})

    # Top-5 outside watchlist
    others.sort(key=lambda x: x["tvl"], reverse=True)
    top_others = {o["name"]: o["tvl"] for o in others[:5]}

    return {
        "chains_watched": watch,
        "top_by_tvl": top_others,
        "total_chains": len(data),
    }


@register_data_point(
    name="defillama_tvl_protocols",
    category="on_chain",
    source="defillama",
    description=(
        "Top DeFi protocols by Total Value Locked. Returns top-20 with name, "
        "TVL, chain, category, and 1d change. Useful for validating DeFi "
        "theses and tracking protocol-level capital flows."
    ),
    params_schema={},
    tags=["tvl", "defi", "protocols", "capital-flow"],
)
def defillama_tvl_protocols() -> Dict[str, Any]:
    data = _get("/protocols")

    protocols: List[Dict[str, Any]] = []
    for p in data[:20]:
        protocols.append({
            "name": p.get("name"),
            "tvl": float(p.get("tvl", 0)),
            "chain": p.get("chain"),
            "category": p.get("category"),
            "change_1d_pct": p.get("change_1d") or 0.0,
            "change_7d_pct": p.get("change_7d") or 0.0,
        })

    return {"count": len(protocols), "protocols": protocols}


# ── stablecoins ────────────────────────────────────────────────────────────

STABLECOINS_URL = "https://stablecoins.llama.fi"


@register_data_point(
    name="defillama_stablecoin_supply",
    category="on_chain",
    source="defillama",
    description=(
        "Total stablecoin market cap across all chains, top stablecoins by "
        "circulating supply, and 30d change. Stablecoin supply growth = fresh "
        "dry powder entering crypto (bullish liquidity signal). Shrinking = "
        "capital exiting (risk-off). One of the purest on-chain regime signals."
    ),
    params_schema={},
    tags=["stablecoins", "liquidity", "regime", "dry-powder"],
    numeric_path="total_circulating_usd",
)
def defillama_stablecoin_supply() -> Dict[str, Any]:
    # Current snapshot
    req = urllib.request.Request(f"{STABLECOINS_URL}/stablecoins?includePrices=false")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    pegged: List[Dict[str, Any]] = []
    total_circ = 0.0
    for a in data.get("peggedAssets", []):
        circ = float(a.get("circulating", {}).get("peggedUSD", 0) or 0)
        pegged.append({
            "symbol": a.get("symbol"),
            "name": a.get("name"),
            "circulating_usd": circ,
            "peg_type": a.get("pegType", "pegged"),
        })
        total_circ += circ

    pegged.sort(key=lambda x: x["circulating_usd"], reverse=True)

    return {
        "total_circulating_usd": total_circ,
        "top_stablecoins": pegged[:5],
        "stablecoin_count": len(pegged),
    }


@register_data_point(
    name="defillama_stablecoin_chains",
    category="on_chain",
    source="defillama",
    description=(
        "Stablecoin supply broken down by blockchain. Shows where dry powder "
        "is deployed: Ethereum dominance = mature, Solana/Base growth = "
        "speculative rotation, Hyperliquid L1 = perp-native capital. "
        "Top-10 chains by circulating stablecoins."
    ),
    params_schema={},
    tags=["stablecoins", "chains", "liquidity", "capital-flow"],
)
def defillama_stablecoin_chains() -> Dict[str, Any]:
    req = urllib.request.Request(f"{STABLECOINS_URL}/stablecoinchains")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    chains: List[Dict[str, Any]] = []
    total = 0.0
    for c in data:
        circ = float(c.get("totalCirculatingUSD", {}).get("peggedUSD", 0) or 0)
        chains.append({"name": c.get("name"), "circulating_usd": circ})
        total += circ

    chains.sort(key=lambda x: x["circulating_usd"], reverse=True)

    return {
        "total_circulating_usd": total,
        "top_chains": chains[:10],
        "chain_count": len(chains),
    }
