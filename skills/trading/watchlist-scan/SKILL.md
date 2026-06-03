---
name: watchlist-scan
description: Scan watchlist symbols, compute lightweight indicators, identify candidates that warrant deep research
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, pre-trade]
    related_skills: [heartbeat, deep-research]
---

# Watchlist scan

Heartbeat hands off here when you have no positions and no active hypotheses. Job: skim the watchlist, find anything worth researching deeply.

## Step 1 — read the watchlist

Pull `watchlist` from your WORLDVIEW.md (in the system prompt). If empty, default to `[BTC, ETH, SOL]` — the deepest-liquidity perps on HL. Add to your watchlist later via `worldview-discipline`.

## Step 2 — fetch lightweight signals per symbol

For each watchlist symbol:
- `fetch_data_point("hl_price", {"symbol": <sym>})` — current mark
- `fetch_data_point("hl_funding_and_oi", {"symbol": <sym>})` — funding rate + OI
- `fetch_data_point("hl_candles", {"symbol": <sym>, "interval": "1h", "lookback_bars": 168})` — 1 week of hourly bars

(Note: `fetch_data_point` auto-snapshots to lifecycle.db. Your perception history is captured for free; future similar-thesis search will see what you saw.)

## Step 3 — compute simple flags

You don't need fancy indicators here. Quick reads:
- **Range break**: latest close above 168-bar high or below 168-bar low? Worth investigating.
- **Funding extreme**: |funding rate| > 0.05%/8h? Often precedes mean-reversion.
- **OI building**: open interest growing >10% in last day with stable price? Imbalance accumulating.
- **Volatility expansion**: latest hour's range > 2× prior 24h average? Something's happening.

You can compute these in your head from the candle data — no need for indicator libraries.

## Step 4 — score candidates

Mentally rank the symbols. A "candidate" warrants deep research if at least ONE flag fired AND your initial read says there's a plausible story (not just noise).

If nothing's interesting today, that's fine — record a note in WORLDVIEW.md `recent_learnings` ("Watchlist scan: BTC range-bound 65k-66k, funding stable, OI flat. No candidates.") and end the session.

## Step 5 — hand off OR schedule

If you have 1-2 candidates: load `deep-research` skill and continue with the strongest candidate.

If you have 3+ candidates: pick the strongest, do deep-research on it now; schedule a self-cron for the others (e.g., `cron create --schedule "in 2 hours" --skill trading/deep-research --prompt "deep-research <symbol>"`). Don't run multiple deep-research passes back-to-back in one session — context bloat.

## Don't

- Don't open positions from this skill — that's `deep-research` + `place_order`.
- Don't fabricate flags. If nothing's interesting, say so. The watchlist exists to surface signal, not to manufacture it.
