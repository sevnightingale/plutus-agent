---
name: pre-mortem
description: Auto-fires before high-conviction (>0.7) place_order — argue against the thesis, capture counter-arguments. Optionally pre-register the trade as a prediction first if conviction is borderline (0.55-0.70).
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, pre-trade, discipline]
    related_skills: [deep-research, strategy-author, prediction-tracker]
---

# Pre-mortem

Cheap blindspot check before commitment. When my conviction is above 0.7, I am most prone to overlooking the obvious counter-argument. This skill spends a small token amount to make a fast model argue against me.

## When

- Auto-fire when conviction > 0.7 BEFORE calling `place_order`
- Manual: any time I feel the urge to size up
- Borderline cases (conviction 0.55-0.70): consider pre-registering as a PREDICTION instead of trading. See "Borderline path" below.

## Borderline path (conviction 0.55-0.70)

If conviction is in the gray zone — high enough to consider acting, not high enough to size with confidence — register a prediction first:

```
record_prediction(
  claim_md="<thesis claim>",
  horizon_hours=<setup's typical resolution time>,
  success_criteria={...},
  failure_criteria={...},
  conviction=<your conviction>,
  strategy_name="<the strategy>",
  regime_tag="<current regime>",
  snapshot_ids=[...]
)
```

Then DON'T trade. Wait for the resolution. If the prediction resolves correct, the strategy's calibration evidence grows; you can act with more confidence next time. If wrong, you saved capital.

This is the AI-edge play: I don't have to take every borderline setup because the next one is coming. Patience is structural.

## Standard path (conviction > 0.7)

### Step 1 — package the thesis

I should have:
- The thesis text (from `record_event("thesis", ...)` I just wrote)
- The invalidation criteria
- Conviction
- Proposed position (side, size, entry, sl, tp)
- Strategy name + regime tag

### Step 2 — call the aux LLM via delegate

```
delegate({
  prompt: "<see below>",
  model: "fast",
  max_turns: 1
})
```

Prompt template:

> You are a sharp adversarial trader. Below is a trading thesis another agent is about to act on with conviction X via strategy Y in regime Z. Your job: make the strongest possible counter-argument. What's the obvious blindspot? What's the historical pattern this resembles that DIDN'T work? What's the catalyst that would invalidate this fast? Are there better setups currently available being overlooked? (250 words.)
>
> Thesis: ...
> Strategy: ...
> Regime perceived: ...
> Invalidation criteria: ...
> Position: ...

### Step 3 — read the rebuttal carefully

The aux LLM's response is a counter-thesis, not a verdict. Read it asking:
- Is there a concrete risk it raises that I haven't addressed?
- Does it surface a historical pattern that genuinely matters?
- Does it identify a fast-acting catalyst not in my invalidation criteria?
- Is the strategy's regime_applicability honestly correct here?

### Step 4 — record the rebuttal

```
record_event("reflection", {
  "reflection_kind": "ad_hoc",
  "text_md": "Pre-mortem for thesis #<id> (strategy <name>, conviction <X>):\n\n<rebuttal text>\n\n**My response**: <whether I proceed and why>",
  "related_thesis_ids": [<thesis_id>],
  "strategy_name": "<name>"
})
```

On the record either way. `find_similar_reflections` will surface this on future similar setups.

### Step 5 — decide

Three outcomes:
1. **Rebuttal raised something serious** → revise: lower conviction, tighten invalidation, smaller size, OR skip entirely. Record the new decision.
2. **Rebuttal was generic / didn't connect** → proceed as planned. The check is on record.
3. **Rebuttal added invalidation criteria I should incorporate** → call `record_event("thesis", ...)` to update the invalidation criteria before placing.

DON'T proceed UNCHANGED if the rebuttal raised something concrete — that's pre-mortem theater, not pre-mortem discipline.

## Pitfalls

- ❌ **Don't skip pre-mortem on >0.7 conviction trades.** That's the entire point.
- ❌ **Don't reflexively reject the rebuttal** because I'm already invested in the trade. The bias I'm checking against is exactly that one.
- ❌ **Don't auto-trade borderline setups (0.55-0.70).** Predict first, act when the strategy's prediction calibration justifies it.
- ❌ **Don't skip pre-mortem because "the strategy is well-tested."** Active strategies still need pre-mortem on high-conviction trades — calibration is statistical; individual trades still surprise.
