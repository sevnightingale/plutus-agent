---
name: regime-detection
description: Detect the current market regime (per-symbol + global) by triangulating across data points. Set or update WORLDVIEW.md regime fields. Tag every thesis/prediction with the perceived regime so calibration can be sliced by regime later.
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, perception, regime]
    related_skills: [worldview-discipline, watchlist-scan, deep-research, strategy-curator]
---

# Regime Detection

Regime is the most important context you have. Strategies that work in distribution don't work in chop. Strategies that work in low-vol don't work in high-vol. **Tagging every thesis with the perceived regime is what makes self-improvement possible** — without it, you can't tell whether a strategy is broken or just deployed in the wrong regime.

This skill is your discipline for detecting and updating regime, and for being honest about regime *uncertainty*.

## When to run

- After cold-start (no WORLDVIEW.md yet)
- Beginning of every heartbeat tick when state is stale (last regime check >4h ago)
- When a data point you trust crosses a regime threshold (VIX through 20, BTC.D ±2pp, funding flip)
- Before authoring any thesis (regime is required on theses)
- When a strategy you're running starts misfiring (could be edge decay; could be regime mismatch)

## Two layers: global regime + per-symbol regime

**Global regime** is the macro frame. One per session. Examples:
- `risk_on` — VIX <15, DXY <96, BTC.D falling, funding positive
- `risk_off` — VIX >20, DXY >100, BTC.D rising, funding negative
- `risk_on_headfake` — equity strength but crypto internals deteriorating (the WORLDVIEW we have right now)
- `chop` — no clear directional bias across asset classes
- `crisis` — extreme dislocation; all bets off

**Per-symbol regime** is the local frame. One per watchlist symbol. Examples:
- `accumulation` — price flat or down, CVD up (smart money buying weakness)
- `distribution_breakdown` — price down, CVD down accelerating (breaking lower)
- `distribution_rally` — price UP but CVD divergent (smart money distributing into strength)
- `momentum_continuation` — price + CVD aligned and accelerating
- `range_bound` — bouncing between defined levels, no breakout
- `squeeze_setup` — BB width or volatility compressed, expansion imminent

These overlap intentionally. A symbol can be in `distribution_rally` while global is `risk_on_headfake` — both true, both relevant.

## Step 1 — Fetch the regime data points

In parallel (use `fetch_data_point`):
- **Macro layer**: `macro_vix`, `macro_dxy`, `coingecko_global` (gives BTC.D + total MC), `defillama_stablecoin_supply`
- **Per-symbol layer** (for each watchlist symbol):
  - `hl_price`, `hl_funding_and_oi`, `hl_cvd` (1h), `ta_bbwidth` (4h), `ta_atr` (4h)
- **On-chain layer**: `eth_gas`, `defillama_tvl_chains`

Don't over-fetch. ~10-15 data points is enough to triangulate. More is noise.

For agentic blueprints (macro_vix etc.): `fetch_data_point("macro_vix")` returns a CACHE HIT with the real value when the perception sub-agent resolved it within the 4h macro budget (which it does every beat — the macro pipeline lives in plutus-perception now; there is no separate macro-cache cron). On a cache hit, just use the value. Only on a stale/miss does it return a blueprint — then execute the web_search and call `record_data_point_observation` to write the value back (which re-warms the cache). Most regime checks will cache-hit macro and skip web_search entirely.

## Step 2 — Classify

For each layer, write down the classification + the threshold logic that triggered it:

| Layer | Reading | Classification | Threshold |
|---|---|---|---|
| VIX | 17.35 | `moderate` | <20 |
| DXY | 98.48 | `neutral` | 95-100 |
| BTC.D | 58.7% | `flat` | no 2pp change in 7d |
| Stablecoins | $320B | `liquid` | growing |
| ETH gas | 0.13 gwei | `dead` | <5 |
| BTC CVD 1h | -3796 | `distribution` | recent_delta < -2σ |
| BTC funding | -0.01%/8h | `mild_short_bias` | between ±0.05% |

## Step 3 — Synthesize regime

Layer classifications often disagree. The synthesis is *your judgment*, not a vote count.

**Heuristics for the global synthesis:**
- If macro layer is risk-on but crypto internals are deteriorating → `risk_on_headfake`
- If 3+ macro signals risk-off + crypto matching → `risk_off`
- If macro and crypto both risk-on → `risk_on`
- If everything is mid-range and no clear direction → `chop`
- If you see breakdown across multiple uncorrelated signals → `crisis`

**Heuristics for per-symbol:**
- Price ↑ + CVD ↓ (recent_delta deeply negative) → `distribution_rally`
- Price ↓ + CVD ↓ accelerating → `distribution_breakdown`
- Price ↓ + CVD ↑ → `accumulation`
- BB width <30th percentile + ATR ↓ → `squeeze_setup`
- Price oscillating in a range, no breakout in 5+ candles → `range_bound`
- Price + CVD + funding all aligned → `momentum_continuation`

## Step 4 — Honesty about confidence

Tag each regime call with confidence:
- `high` — 5+ signals aligned, strong threshold breaches
- `medium` — 3-4 signals aligned, mixed but tilting
- `low` — split signals, you're guessing

If confidence is `low`, say so explicitly in WORLDVIEW.md synthesis. Don't pretend you know the regime when you don't — overconfidence in regime calls is one of the biggest sources of strategy mis-deployment.

## Step 5 — Update WORLDVIEW.md

Update these fields:
- `regime.global` — string + 1-line synthesis
- `regime.per_symbol.<sym>` — string per watchlist symbol
- `regime.confidence` — high|medium|low
- `regime.detected_at` — ISO timestamp
- `regime.dominant_signals` — list of which data points drove the call (3-5 max)
- `key_levels.<sym>` — update support/resistance if breaks have occurred
- `delta_from_prior` — explicitly note "regime SHIFTED from X to Y because Z" if it changed

This is a frozen-snapshot write — takes effect next session. Within this session you carry the new understanding in working memory.

## Step 6 — Tag downstream events

Every `record_event("thesis", ...)`, `record_prediction(...)`, and `record_observation(...)` call from now on must include `regime_tag` matching what you just classified. If you don't, the calibration analysis can't slice by regime — and that's the analysis that tells you whether a losing strategy is *broken* or *mis-deployed*.

## Step 7 — Record an observation

After updating WORLDVIEW, fire `record_observation`:

```
record_observation(
  kind="regime_shift",  # or "noticed" if no shift
  text_md="Regime call: <global> | per-symbol: BTC=X, ETH=Y. Drivers: <signals>. Confidence: <level>.",
  structured_tags={"regime": "...", "confidence": "..."}
)
```

This goes into the journal stream so future regime-detection runs can see what you concluded last time and detect drift.

## Pitfalls

- ❌ **Don't classify based on price alone.** Price up doesn't mean risk-on; it might be distribution. Always triangulate.
- ❌ **Don't over-rotate on one indicator.** VIX dropped 2 points → "risk-on now" is too fast. Wait for confirmation across layers.
- ❌ **Don't forget to update key_levels.** Regime change usually coincides with a key level break.
- ❌ **Don't claim high confidence when you have it for the wrong reason.** "Confident" because 6 indicators agree is real. "Confident" because one indicator is screaming is overconfidence.
- ❌ **Don't skip this skill before authoring a thesis.** The thesis needs `regime_tag`. If you haven't done regime detection in the current session, do it before the thesis.
