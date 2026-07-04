#!/usr/bin/env python3
"""
Emergency data fetch — read latest snapshots from lifecycle.db
and dump key data points.
"""
import sys, os, json, time, sqlite3
from datetime import datetime, timezone

# Find the db
hermes_home = os.environ.get("PLUTUS_HOME") or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.plutus-agent")
db_path = os.path.join(hermes_home, "lifecycle.db")

if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    sys.exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get latest snapshot per data point name
rows = conn.execute("""
    SELECT name, params_json, value_json, source, ts, MAX(id) as max_id
    FROM data_point_snapshots
    WHERE ts > ?
    GROUP BY name, params_json
    ORDER BY ts DESC
    LIMIT 30
""", (time.time() - 86400,)).fetchall()

for r in rows:
    name = r["name"]
    params = json.loads(r["params_json"]) if r["params_json"] else {}
    value = json.loads(r["value_json"]) if r["value_json"] else {}
    ts = r["ts"]
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    
    if name == "hl_price":
        print(f"{dt.isoformat()} PRICE: ${value.get('price', '?')}")
    elif name == "hl_orderbook":
        bids = value.get("bids", [])
        asks = value.get("asks", [])
        bid_sz = sum(b.get("sz", 0) for b in bids[:10])
        ask_sz = sum(a.get("sz", 0) for a in asks[:10])
        ratio = bid_sz / ask_sz if ask_sz > 0 else 99
        print(f"{dt.isoformat()} ORDERBOOK: {ratio:.2f}x {'bid-heavy' if ratio > 1 else 'ask-heavy'} (bid={bid_sz:.1f} ask={ask_sz:.1f})")
    elif name == "hl_funding_and_oi":
        print(f"{dt.isoformat()} OI: {value.get('open_interest', 0):.0f} BTC, funding={float(value.get('funding', 0))*100:.4f}%, mark=${value.get('mark_px', '?')}, premium={value.get('premium', '?')}")
    elif name == "hl_candles":
        interval = params.get("interval", "?")
        candles = value.get("candles", [])
        if candles:
            last = candles[-1]
            first = candles[0]
            print(f"{dt.isoformat()} CANDLES({interval}): last(o={last['o']:.0f} h={last['h']:.0f} l={last['l']:.0f} c={last['c']:.0f} v={last['v']:.0f}) range=[{first['t']}..{last['t']}]")
    elif name == "hl_cvd":
        interval = params.get("interval", "?")
        summary = value.get("summary", json.dumps(value)[:100])
        print(f"{dt.isoformat()} CVD({interval}): {summary}")
    elif name.startswith("ta_"):
        interval = params.get("interval", "?")
        summary = value.get("summary", "")
        cur = value.get("current", {})
        vals = {k: cur[k] for k in ["value", "adx", "vi_plus", "vi_minus", "spread", "macd", "histogram"] if k in cur}
        print(f"{dt.isoformat()} {name}({interval}): summary={summary[:80]} vals={vals}")

conn.close()