---
name: observation-journal
description: Capture micro-observations that don't yet warrant a thesis or prediction. The trader's running journal that compounds into expertise. Run reactively (something noticed) or as a heartbeat sidecar.
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, meta, perception]
    related_skills: [strategy-author, regime-detection, watchlist-scan]
---

# Observation Journal

The journal is where I write down what I notice that isn't actionable yet. Counterfactuals ("almost took this trade and didn't"). Mental models ("when X coincides with Y, expect Z"). Edge claims I'm tentatively making. Patterns I'm starting to see.

This compounds. Three observations of the same pattern → strategy candidate. Fifty `noticed` entries about funding-spike behavior → mental model that becomes a strategy. The journal is not for the current session — it's for *future me*.

## When to write

This is the lowest-friction tool I have. Use it liberally. Triggers:

- Saw something interesting in the market that doesn't fit current strategies
- Almost took a trade and didn't — write down WHY (this is the most valuable kind)
- Pattern is starting to emerge across multiple observations — name it
- Operator shared an insight — record it so future-me has the context
- Strategy hypothesis confirmed (or disconfirmed) — note the data point
- Edge claim I want to make — write it down explicitly so I can evaluate later
- Edge claim I'm withdrawing — write that down explicitly too

## Choose the right `kind`

| kind | When | Example |
|---|---|---|
| `noticed` | Neutral observation about the market | "BTC funding flipped negative for first time in 3 days" |
| `watching` | A setup is developing, not yet triggered | "BTC at $79.5K, CVD weakening — distribution-rally setup forming" |
| `almost_traded` | Counterfactual — I almost entered, what stopped me? | "Almost shorted ETH at $2400 but ATR was at 7-day low; didn't want to pay BB squeeze breakout. Saw it dump $80 in next 4h." |
| `mental_model` | Crystallized heuristic | "Heuristic: when BTC.D rises 1pp+ in 24h with VIX flat, alts dump within 48h ~70% of time" |
| `pattern_candidate` | Possible new strategy | "Recurring pattern: high funding + low CVD + price grinding up = mean-reversion fade. 4 occurrences observed. Maybe a strategy?" |
| `edge_claim` | Asserting an edge I have | "Edge claim: I see CVD divergences faster than humans because I poll hourly with structured detection. Supportable." |
| `edge_revoked` | Withdrawing a previous edge claim | "Withdrawing edge claim about funding-spike-fading. Last 5 attempts: 1 win, 4 losses. Pearson on conviction → outcome is -0.05. No edge." |
| `operator_input` | Operator told me something | "Operator: focus on smaller-cap perps where dgclaw competition is thinner." |
| `regime_shift` | Detected regime change | "Regime shift: distribution_rally → distribution_breakdown. Driver: BTC lost $79K with high vol." |

## How to write a good observation

1. **Date-stamped concrete facts > vague impressions.** "BTC -2.5% in 4h" not "BTC weak."
2. **Name the data points** that drove the observation (snapshot_ids if available).
3. **State the implication** if there is one. "Noticed X. Implication: Y."
4. **For counterfactuals**, write what stopped you AND what happened after.
5. **Keep it short.** 1-3 sentences for most. The point is volume + diversity, not essays.

## Writing it

```
record_observation(
  kind="almost_traded",
  text_md="Almost took BTC short at $79.6K — Arbiter setup formed (CVD breakdown + key level break). Skipped because ATR was at 4-week low and I didn't trust low-vol breakouts. Outcome 6h later: BTC at $80.4K. SKIP was right.",
  symbol="BTC",
  strategy_name="arbiter-confluence",
  snapshot_ids=[1234, 1235, 1236],
  structured_tags={"outcome_after_skip": "skip_correct"}
)
```

## When the journal triggers other skills

- 3+ `pattern_candidate` entries on the same setup → consider running `strategy-author`
- Any `edge_revoked` on an active strategy → trigger `strategy-curator` to demote/retire
- Multiple `regime_shift` observations in 24h → trigger `regime-detection` re-pass
- Multiple `almost_traded` on same setup all turning out correct in retrospect → I'm being too cautious; consider lowering my conviction threshold OR converting to predictions

## When to query the journal

- Heartbeat tick: `query_observations(limit=10)` to see what I noticed last
- Before authoring a strategy: `query_observations(kind="pattern_candidate")`
- During calibration-review: `query_observations(kind="edge_claim", limit=20)` and check whether each is still supportable
- Weekly review: `query_observations(since_ts=<7 days ago>)` for the digest

## Pitfalls

- ❌ **Don't treat the journal as a place for noise.** "BTC went up" is not a useful observation. "BTC up 3% on no news, CVD divergent" is.
- ❌ **Don't skip the counterfactuals.** `almost_traded` is the highest-information class. If I never write these, I lose the most powerful learning signal.
- ❌ **Don't conflate observations with theses.** Observations don't drive trades. They build context.
- ❌ **Don't conflate observations with predictions.** Predictions are commitments to be checked. Observations are notes.
- ❌ **Don't let the journal become noise by writing 100 entries/day.** ~10-20/day is good volume. Quality > quantity.
