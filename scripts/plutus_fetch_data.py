#!/usr/bin/env python3
"""Fetch fresh Hyperliquid data for regime assessment."""
import sys, os, json, time
sys.path.insert(0, '/home/sev/plutus-agent')

from trading.integrations.hyperliquid.data_points import (
    hl_price,
    hl_candles,
    hl_orderbook,
    hl_funding_and_oi,
)

symbol = "BTC"

print("=== HL_PRICE ===")
try:
    p = hl_price(symbol)
    print(json.dumps(p, indent=2))
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== HL_ORDERBOOK ===")
try:
    ob = hl_orderbook(symbol, depth=10)
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    bid_depth = sum(b["sz"] for b in bids)
    ask_depth = sum(a["sz"] for a in asks)
    ratio = bid_depth / ask_depth if ask_depth > 0 else 0
    print(f"Bid depth top10: {bid_depth:.2f}")
    print(f"Ask depth top10: {ask_depth:.2f}")
    print(f"Ratio: {ratio:.2f}x {'bid-heavy' if ratio > 1 else 'ask-heavy'}")
    print(f"Top bid: {bids[0] if bids else 'none'}")
    print(f"Top ask: {asks[0] if asks else 'none'}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== HL_FUNDING_AND_OI ===")
try:
    foi = hl_funding_and_oi(symbol)
    print(f"Funding: {foi['funding']*100:.4f}%")
    print(f"Premium: {foi.get('premium', 'N/A')}")
    print(f"Mark: {foi.get('mark_px', 'N/A')}")
    print(f"OI: {foi.get('open_interest', 0):.0f} BTC")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== HL_CANDLES 1h (last 10) ===")
try:
    c1 = hl_candles(symbol, "1h", lookback_bars=15)
    candles = c1.get("candles", [])
    for c in candles[-10:]:
        print(f"  {c['t']} o={c['o']:.1f} h={c['h']:.1f} l={c['l']:.1f} c={c['c']:.1f} v={c['v']:.0f}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== HL_CANDLES 4h (last 10) ===")
try:
    c4 = hl_candles(symbol, "4h", lookback_bars=20)
    candles = c4.get("candles", [])
    for c in candles[-10:]:
        print(f"  {c['t']} o={c['o']:.1f} h={c['h']:.1f} l={c['l']:.1f} c={c['c']:.1f} v={c['v']:.0f}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== HL_CANDLES 1d (last 5) ===")
try:
    cd = hl_candles(symbol, "1d", lookback_bars=10)
    candles = cd.get("candles", [])
    for c in candles[-5:]:
        print(f"  {c['t']} o={c['o']:.1f} h={c['h']:.1f} l={c['l']:.1f} c={c['c']:.1f} v={c['v']:.0f}")
except Exception as e:
    print(f"ERROR: {e}")
