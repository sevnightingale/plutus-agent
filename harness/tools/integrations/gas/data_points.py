"""Ethereum gas fee data point via Blocknative free API.

No API key required. Returns base fee, priority fee estimates at
multiple confidence levels, and a network activity classification.

Gas regime signals:
- <5 gwei:   dead — no on-chain activity, NFT season is over
- 5-20 gwei: normal — healthy but not euphoric
- 20-50:     elevated — DeFi/NFT activity picking up
- 50-100:    high — on-chain mania, ape season
- >100:      extreme — probably a mint or airdrop frenzy
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict

from harness.tools.core.data_point_registry import register_data_point

API = "https://api.blocknative.com/gasprices/blockprices"


def _classify_gas(base_fee: float) -> Dict[str, Any]:
    if base_fee < 5:
        regime, signal = "dead", "bearish — no on-chain demand, NFT/DeFi hibernation"
    elif base_fee < 20:
        regime, signal = "normal", "neutral — healthy baseline activity"
    elif base_fee < 50:
        regime, signal = "elevated", "bullish — DeFi/NFT rotation picking up"
    elif base_fee < 100:
        regime, signal = "high", "euphoric — ape season, congestion risk"
    else:
        regime, signal = "extreme", "mania — likely airdrop or mint frenzy, expect reversal"
    return {"regime": regime, "signal": signal}


@register_data_point(
    name="eth_gas",
    category="on_chain",
    source="blocknative",
    description=(
        "Ethereum gas fees (gwei) from Blocknative: base fee, priority fee at "
        "multiple confidence tiers (99/95/90/80/70), gas regime classification. "
        "Rising gas → on-chain mania → can precede volatility in perps. "
        "Falling gas → disinterest → correlates with ranging/declining markets."
    ),
    params_schema={},
    tags=["gas", "ethereum", "on-chain", "network-activity", "regime"],
)
def eth_gas() -> Dict[str, Any]:
    req = urllib.request.Request(API)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    bp = data["blockPrices"][0]
    base_fee = bp["baseFeePerGas"]

    tiers: Dict[str, float] = {}
    for est in bp["estimatedPrices"]:
        tiers[f"p{est['confidence']}"] = est["price"]

    classification = _classify_gas(base_fee)

    return {
        "base_fee_gwei": base_fee,
        "priority_fee_gwei": tiers,
        "block_number": data["currentBlockNumber"],
        "estimated_txns_next_block": bp["estimatedTransactionCount"],
        "gas_regime": classification["regime"],
        "gas_signal": classification["signal"],
    }
