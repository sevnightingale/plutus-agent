---
name: weekly-review
description: Sunday 18:00 UTC self-scheduled cron — full meta cycle (synthesize the week → calibration-review → strategy-curator → consolidate-learnings). Surfaces structural insights to operator.
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, periodic-review, discipline]
    related_skills: [calibration-review, strategy-curator, prediction-tracker, consolidate-learnings, loss-postmortem]
---

# Weekly Review

The discipline that surfaces calibration drift, edge decay, regime transitions, capital deployment patterns, and prediction-track-record patterns. Without this, I'm flying without instruments.

This is the Sunday meta cycle. It runs four sub-skills in sequence:
1. weekly-review (this skill) — synthesizes the week
2. calibration-review — refreshes calibration analysis
3. strategy-curator — applies promotion/demotion/retirement
4. consolidate-learnings — distills durable patterns into long-term memory

## Workflow — this skill (the synthesis)

### Step 0 — first, resolve any due predictions

Run `prediction-tracker` if `query_predictions(status="due")` returns ≥1. Calibration-review needs fresh resolutions.

### Step 1 — fetch the week's structural data

Run all of these (one round-trip each, fast):

- `query_performance(period_days=7)` — total PnL, win rate, R aggregate
- `query_performance(period_days=30)` — same, monthly view
- `query_performance_attribution(period_days=30, group_by="strategy_name")` — per-strategy contribution
- `query_performance_attribution(period_days=30, group_by="symbol")` — per-symbol contribution
- `query_calibration(since_ts=<7d ago>, include_predictions=true)` — fresh + predictions
- `query_calibration(since_ts=<30d ago>, include_predictions=true)` — wider window
- `query_strategy_stats(include_predictions=true)` — per-strategy stats with edge-decay flags
- `query_conviction_outcomes(period_days=30)` — by trajectory shape
- `query_capital_movements(since_ts=<7d ago>)` — deposits/withdrawals
- `query_skip_outcomes(period_days=30)` — were skips correct?
- `query_equity_curve(period_days=30)` — peak / trough / drawdown shape
- `query_predictions(status="resolved", limit=50)` — which predictions resolved this week
- `query_observations(since_ts=<7d ago>, limit=100)` — the week's journal stream

### Step 2 — quick equity overview

From `query_equity_curve`: starting equity, ending equity, peak, max drawdown, % growth. One sentence.

### Step 3 — calibration check (delegate to calibration-review skill)

Load `calibration-review` skill — it does the deep calibration analysis + adjusts strategy conviction logic if needed. Returns a summary that I weave into the weekly review reflection.

### Step 4 — strategy book audit (delegate to strategy-curator)

Load `strategy-curator` skill — it walks every strategy, refreshes performance, applies promotions/demotions/retirements. Returns a summary.

### Step 5 — predictions track record

From `query_predictions(status="resolved", limit=50)`:
- How many resolved correct vs wrong vs ambiguous?
- What's the prediction-only win rate per conviction bucket? (Already in calibration-review output, but worth surfacing here.)
- Which strategies' observation-stage predictions are accumulating? Any close to the 20-resolved gate for promotion?

### Step 6 — observations digest

From `query_observations(since_ts=<7d ago>)`:
- How many entries this week? By kind?
- Any `pattern_candidate` entries? (Triggers strategy-author consideration)
- Any `edge_revoked`? (Should have triggered curator already)
- Any `mental_model` entries worth promoting to durable memory?

### Step 7 — write the weekly review reflection

Structured format:

```markdown
# Weekly review — <ISO date>

## Equity
- Start: $X, End: $Y, Peak: $Z, Drawdown: X%
- Trades: N (W wins, L losses), Win rate: X%, Avg r: X.XX
- Capital movements: <summary>

## Calibration (from calibration-review)
- Trade conviction-r correlation: X.XX (vs prior 30d: Y.YY)
- Reliable buckets: [...]
- Miscalibrated buckets: [...]
- Adjustments made to strategies: [...]

## Predictions
- Resolved: N (correct: X, wrong: Y, ambiguous: Z)
- Per-strategy track record: [...]
- Strategies near promotion gate: [...]

## Strategy book (from strategy-curator)
- Active: [strategy names + perf summary]
- Promoted this week: [...]
- Demoted this week: [...]
- Retired this week: [...]
- New observation strategies authored: [...]

## Skips
- Skipped N. M would have hit target, P would have hit stop. Net: <correct/incorrect bias>

## Observations digest
- N entries by kind: ...
- Pattern candidates worth promoting: ...
- Mental models crystallized: ...

## Themes
- 2-3 paragraphs on what worked, what didn't, what's changing in the regime/edge picture

## Adjustments for next week
- 2-4 specific changes — sizing, watchlist, regime read, strategy promotions, etc.
```

Record:

```
record_event("reflection", {
  "reflection_kind": "weekly_review",
  "text_md": "<the review above>"
})
```

### Step 8 — consolidate-learnings

Load `consolidate-learnings` skill. The weekly view often surfaces durable patterns worth promoting from lifecycle.db reflections to holographic memory.

### Step 9 — surface to operator

Send a one-paragraph summary via `send_message`:

> Weekly review (week ending <date>): equity $X (Δ +/-Y%), N trades, win rate X%, calibration <stable/drifted>. Strategies: <changes>. Predictions: <count> resolved at <rate>. Full review at reflection #<id>. Notable: <2-3 bullet themes>.

Tag urgent items: drawdown > 15%, calibration drop > 0.2, strategy retirement, recurring loss patterns, edge claim revoked.

### Step 10 — update WORLDVIEW.md

`recent_learnings` gets one entry: "Weekly review YYYY-MM-DD: <one-line theme>". Older `recent_learnings` may rotate to `learnings_archive.md` if the list is full.

Also update `current_strategies` mirror to reflect any strategy-curator changes (it should have done this; double-check).

## Pitfalls

- ❌ **Don't skip the weekly review because "nothing happened."** Even quiet weeks have signal.
- ❌ **Don't write the review for the operator.** Write it for future-me. The operator gets the summary.
- ❌ **Don't retire a strategy on one bad week.** The edge-decay flag accounts for sample size; trust it.
- ❌ **Don't merge calibration-review and strategy-curator analyses into this skill** — they're separate for a reason. Run them, weave their outputs in.
- ❌ **Don't overwrite recent_learnings with the entire weekly review.** Just one line. The full review lives in the `reflections` table.
