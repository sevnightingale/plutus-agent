---
name: deep-research
description: Build a thesis on a symbol — fetch deeper data, articulate invalidation criteria, decide to open OR skip (record decision either way), trigger pre-mortem if conviction high
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, pre-trade]
    related_skills: [watchlist-scan, pre-mortem, position-monitor, worldview-discipline]
---

# Deep research

Watchlist-scan flagged a candidate. Now build conviction (or don't), articulate what would prove the thesis wrong, and decide.

## Step 1 — gather

For the candidate symbol, beyond what watchlist-scan already pulled:
- `fetch_data_point("hl_orderbook", {"symbol": <sym>, "depth": 20})` — current order flow
- `fetch_data_point("hl_candles", {"symbol": <sym>, "interval": "5m", "lookback_bars": 288})` — 24h of 5m bars
- `fetch_data_point("hl_candles", {"symbol": <sym>, "interval": "1d", "lookback_bars": 90})` — quarterly context
- `web_search` if there's news context worth understanding (catalyst events, on-chain movements, macro)
- `find_similar_theses(query="<your draft thesis text>")` — have you (or your past self) thought about this before? What happened? Read the LLM digest.
- Check holographic memory: `fact_store(action="probe", entity=<symbol>)` — what durable facts apply?

## Step 2 — articulate the thesis

Write a clear thesis statement. 2-4 sentences. Include:
- What you think will happen
- Why (the mechanism)
- Time horizon
- Approximate price targets (entry zone, target, ideal stop)

## Step 3 — articulate invalidation criteria (REQUIRED)

What would prove your thesis wrong? Specific, observable conditions. Examples:
- "BTC closes below 64k on 1h timeframe"
- "Funding flips positive >0.02%/8h within 6 hours"
- "Volume on the breakout candle is below 1.5× the 24h hourly average"

Express these as a JSON object that `position-monitor` can check:

```json
{
  "max_price": 65500,         // invalidate if price > 65500 (for short)
  "min_price": 64000,         // invalidate if price < 64000 (for long)
  "max_funding_8h": 0.0002,   // invalidate if funding spikes
  "max_holding_hours": 48,    // invalidate by time-out
  "narrative_check": "BTC must hold 65k support — otherwise structure changed"
}
```

**`place_order` will REFUSE without this.** This is the one rule the dispatcher enforces in code.

## Step 4 — record the thesis

```
record_event("thesis", {
  symbol: "<sym>",
  text_md: "<thesis text from step 2>",
  invalidation_criteria: <object from step 3>,
  strategy_id: <int or null — only if this fits an existing strategy in the strategy book>
})
```

The dispatcher embeds the thesis text via voyage-finance-2 and writes the embedding atomically — `find_similar_theses` will find this later.

The returned `thesis_id` is what you use in `place_order` (or in `record_event("decision", action="skip", thesis_id=...)`).

## Step 5 — assign conviction

Assign a conviction in [0.0, 1.0]. Be honest — this is a calibration signal you'll review weekly. Rough scale:
- 0.0–0.3: weak; mostly here for the data, not really expecting alpha
- 0.4–0.6: moderate; thesis is plausible but not strong
- 0.7–0.8: strong; clear setup, well-defined invalidation
- 0.9+: rare; high-confidence + multiple confirming signals

## Step 6 — decide: open or skip

### If skipping

```
record_event("decision", {
  thesis_id: <id>,
  action: "skip",
  conviction: <yours>,
  params: {reason: "<short reason>"}
})
```

Skipping is a real decision. Record it. `query_skip_outcomes` later tells you whether your skips were correct.

### If opening

If conviction > 0.7, **first run `pre-mortem`** as a cross-check. The pre-mortem skill calls an aux LLM to argue against the thesis. Read the rebuttal. Decide whether to proceed.

Then size the position:
- Default sizing: `risk = 0.01 × equity × conviction × 2`. So 1% of equity at conviction 0.5, 2% at 1.0. (Conservative — you can deviate but log the reason.)
- Size = risk_usd / (entry - sl) for longs, or (sl - entry) for shorts. Round to the venue's `szDecimals` (look at `hl_universe`).

Place the order:

```
place_order({
  venue: "hyperliquid",
  thesis_id: <id>,
  conviction: <yours>,
  side: "long" | "short",
  symbol: "<sym>",
  size: <computed>,
  sl: <stop>,
  tp: <target>,
  extra: {order_type: "market"}
})
```

The dispatcher writes decision + trade + position rows atomically.

## Step 7 — update WORLDVIEW.md

Add the new hypothesis to `active_hypotheses`. If position opened, add to `open_positions_summary` mirror. (See `worldview-discipline` for format.) Note: takes effect next session.

## Don't

- Don't open without articulated invalidation criteria — `place_order` will refuse anyway.
- Don't size by gut. Use the rubric. Deviations should be logged with reasoning in the decision params.
- Don't skip the pre-mortem when conviction > 0.7 — that's your blindspot check.
