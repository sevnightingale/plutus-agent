---
name: reflect
description: Post-trade reflection — opportunistic on wins (capture what worked), structured on losses (delegates to loss-postmortem). Always categorize the error_class on losses.
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, exit]
    related_skills: [loss-postmortem, consolidate-learnings, calibration-review, strategy-curator]
---

# Reflect

Lighter-weight than `loss-postmortem`. For wins and modest losses (-0.5 ≤ r), this captures whatever insight is worth keeping. For r < -0.5, route to `loss-postmortem` (mandatory).

## When

After every position close, called from `reconcile-and-reflect`. Also can be invoked manually any time I want to reflect on something.

## Step 1 — route

`inspect_position(position_id=P)` — get the outcome.

If `r_multiple < -0.5`: load `loss-postmortem` and continue there. STOP this skill.

Otherwise continue.

## Step 2 — quick read

For wins and small losses, ask:
- **Did I act on the thesis or did I just get carried by the market?** Honest answer.
- **Was the exit timing good?** MFE vs exit_efficiency from the outcome — left money on the table?
- **Did the conviction trajectory match what happened?** If conviction stayed stable but the trade lost, maybe my conviction model is miscalibrated.
- **What did I learn about <symbol> / <pattern> that I didn't know before?**
- **Was the strategy attribution correct?** This trade was tagged as `strategy_name=X`. Did it really fit X's setup, or did I force-fit?

Short. 3-6 sentences. The ratio of reflection effort to trade size matters — small wins don't need treatises.

## Step 3 — categorize error_class (REQUIRED on losses, even small)

For ANY loss (negative r), tag the error_class:

| error_class | When |
|---|---|
| `forecast` | Thesis was wrong — direction or magnitude prediction failed |
| `execution` | Thesis correct, entry/exit was poor (slippage, late entry, premature exit) |
| `sizing` | Right call but oversized — small thesis, big exposure |
| `regime` | Strategy works, but applied in wrong regime (regime detection failed) |
| `variance` | Right call, right execution, right size — market noise |
| `process_violation` | I skipped a required step (no pre-mortem, no invalidation criteria, etc.) |

Be honest. Most losses default-blamed on "variance" are actually `forecast` or `regime`. The categorization shapes future improvement.

## Step 4 — record

```
record_event("reflection", {
  "reflection_kind": "post_trade",
  "text_md": "<your reflection>",
  "related_thesis_ids": [<thesis id>],
  "position_ids": [<P>],
  "error_class": "<class>",      # only on losses
  "strategy_name": "<name>"      # the strategy this trade was tagged with
})
```

## Step 5 — durable lesson?

If the reflection contains something durable (likely true for weeks/months, not just this trade), queue it for `consolidate-learnings` — either inline now or at session-end. Examples of durable:
- "ETH 4h candles around 8 UTC tend to be liquidation-driven"
- "When BTC funding flips positive after a multi-day negative streak, watch for short-squeeze setup"

If the lesson suggests modifying the strategy file (changed conviction logic, new pre-mortem item, refined invalidation criteria), use `read_file` + `patch` to amend `~/.plutus-agent/strategies/<stage>/<name>.md` directly.

## Step 6 — update WORLDVIEW.md

If the reflection changes how I'll position similar trades in the future, update WORLDVIEW.md `recent_learnings` with a one-liner.

## Step 7 — observation linkage

Look for related observations from before the trade (especially `watching` and `almost_traded`). Linking back via `related_thesis_ids` in the original observation rows tightens the journal-to-trade loop.

## Pitfalls

- ❌ **Don't skip error_class on losses.** Even small losses. The categorization is the learning signal.
- ❌ **Don't write a postmortem on every $0.50 win.** Calibrate effort to trade size.
- ❌ **Don't conflate "luck" with "skill."** A win can be lucky; a loss can be skillful. The honest read matters more than the dollar sign.
- ❌ **Don't blame "variance" without doing the math.** If conviction-bucket calibration says you should win at 70% and you lost a 0.7-conviction trade, ONE loss is consistent with calibration. But three in a row at high conviction is not — it's `forecast` error.
- ❌ **Don't forget to tag strategy_name.** Without it, query_strategy_stats can't attribute properly.
