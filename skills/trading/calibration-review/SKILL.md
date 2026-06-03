---
name: calibration-review
description: Compute and interpret the conviction calibration curve. Does my conviction actually predict outcomes? If not, adjust how I set conviction in the active strategy skills. Run weekly.
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, meta, self-improvement, calibration]
    related_skills: [strategy-curator, weekly-review, prediction-tracker, reflect]
---

# Calibration Review

This is the most important meta-skill I have. Conviction without calibration is superstition. Without measuring whether 0.7-conviction setups actually win 70% of the time, I'm just guessing in a number.

Run weekly (Sunday after weekly-review) and after any cycle where 5+ predictions resolve OR 3+ trades close.

## What calibration means in concrete terms

If I made 20 predictions at conviction 0.7, I claim "I'm right 70% of the time on these." If 14 of them resolved correct and 6 wrong, I'm calibrated (14/20 = 0.70). If 10/20 resolved correct, I'm overconfident (claimed 0.7, actually 0.5). If 18/20 correct, I'm underconfident (claimed 0.7, actually 0.9 — should be more aggressive).

A miscalibrated agent burns money in two ways:
- Overconfident → bets too big on bad setups
- Underconfident → passes on real opportunities

Calibration ≠ winrate. A calibrated trader can have 30% winrate at conviction 0.3 (correct), 60% at conviction 0.6, 80% at conviction 0.8. Linear-ish curve. Slope of the curve tells you whether you're tracking reality.

## Step 1 — Pull the calibration data

```
# Trade-based calibration (high-quality but slow to accumulate)
query_calibration(include_predictions=true)

# Per-strategy calibration (the most actionable view)
query_strategy_stats()  # all strategies; check each one's prediction_calibration + trade_conviction_calibration

# Recent vs lifetime
query_calibration(since_ts=<7 days ago>, include_predictions=true)
query_calibration(since_ts=<30 days ago>, include_predictions=true)
```

## Step 2 — Read the curve

The result has buckets like:

```
trades.buckets:
  - {bucket: 0.5-0.6, n_trades: 8, win_rate: 0.50, mean_r: 0.05}     # calibrated
  - {bucket: 0.6-0.7, n_trades: 5, win_rate: 0.40, mean_r: -0.10}    # overconfident
  - {bucket: 0.7-0.8, n_trades: 3, win_rate: 0.67, mean_r: 0.30}     # roughly calibrated
predictions.buckets:
  - {bucket: 0.5-0.6, n: 15, win_rate: 0.53}     # calibrated
  - {bucket: 0.7-0.8, n: 10, win_rate: 0.40}     # OVERCONFIDENT
```

For each bucket:
- `win_rate` should ≈ midpoint of the bucket (e.g., 0.7-0.8 bucket → win_rate ≈ 0.75)
- Tolerance: ±10pp on small samples (n<10), ±5pp on larger samples
- Outside tolerance = calibration issue

Also check `pearson_r`:
- > 0.4 → conviction tracks reality well
- 0.2-0.4 → conviction has signal but noisy
- < 0.2 → conviction is uncorrelated with outcome → I'm not actually estimating well

## Step 3 — Diagnose miscalibration

Three patterns to look for:

### A. Systematic overconfidence

ALL high-conviction buckets underperform their stated probability.
- Cause: I'm pattern-matching too aggressively, treating noisy signals as strong
- Action: Apply a conviction *deflator* in the active strategies. If 0.8 conviction empirically wins at 0.6, multiply all conviction outputs by ~0.75.
- Update each affected strategy's conviction calibration mapping

### B. Systematic underconfidence

ALL conviction buckets outperform.
- Cause: I'm being too conservative
- Action: Inflate conviction inputs. Sizing should grow.

### C. Specific bucket break

Mid-conviction (0.5-0.7) is fine but high-conviction (0.7+) is breaking.
- Cause: Edge case in the strategy's high-conviction trigger
- Action: Examine those specific trades' theses + reflections. Is there a common failure mode?

### D. Per-strategy miscalibration with global calibration intact

One strategy's calibration is broken, others are fine.
- Cause: That strategy's conviction logic is decaying
- Action: Update that strategy's conviction-calibration section in its STRATEGY.md, OR demote via strategy-curator

## Step 4 — Take action

For each diagnosed issue:

### Update strategy file conviction calibration

Use `read_file` + `patch` (or `write_file`) to amend the affected strategy's "Conviction calibration" section. Document the change with a dated note in the body's "Notes from real trades / observations" log.

### Adjust active strategies' sizing rules

If overconfident, lower the size multiplier. If underconfident, raise.

### Pre-register a new prediction

If you're unsure why calibration broke, register a prediction to test:
"For strategy X, the next 5 high-conviction setups will resolve at win_rate Y%."
This *tests your hypothesis about your own miscalibration*.

## Step 5 — Write the reflection

```
record_event("reflection", {
  "reflection_kind": "calibration_review",
  "text_md": "Calibration review (week ending <date>). Trade calibration: pearson=<r>, status=<calibrated|over|under>. Predictions calibration: <details>. Per strategy: <list>. Adjustments made: <list>. Pre-registered tests: <list>.",
})
```

## Step 6 — Look for the 'unknown unknowns'

A calibrated trader knows when they don't know. After computing the curve, ask:
- Are there domains where my calibration is essentially random (pearson < 0.1)?
- If yes, write `record_observation(kind="edge_revoked", text_md="...", strategy_name=...)` and consider whether to retire that strategy

This is the hardest discipline — admitting I have no edge in some area. But pretending edge that isn't there is the fastest way to lose money.

## Pitfalls

- ❌ **Don't compute calibration on n<10 in any bucket and act on it.** Variance dominates.
- ❌ **Don't conflate calibration with winrate.** A 30% winrate at 0.3 conviction is *correctly calibrated*.
- ❌ **Don't blame the market for miscalibration.** Calibration is about MY epistemic accuracy, not the market's "fairness."
- ❌ **Don't tweak conviction logic during a losing streak without doing the math.** 5 losses in a row could be variance. Always check the calibration curve first.
- ❌ **Don't skip writing the reflection.** This is where the learning lives. The data is just data; the reflection extracts the lesson.
