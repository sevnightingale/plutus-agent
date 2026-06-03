---
name: loss-postmortem
description: Mandatory structured reflection when r_multiple < -0.5. Categorize the failure (forecast/execution/sizing/regime/variance/process_violation), check whether it repeats a prior lesson, decide if the strategy needs amending or demotion.
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, exit, discipline]
    related_skills: [reflect, tilt-detection, weekly-review, strategy-curator, calibration-review]
---

# Loss Postmortem

When `close_position` produces an outcome with `r_multiple < -0.5`, I fire this skill. Not optional. Losses teach more than wins; that lesson is wasted if it isn't captured structurally.

## Step 1 — pull the full chain

`inspect_position(position_id=P)` returns the full causal chain:
- Opening thesis (text + invalidation criteria + conviction at entry + strategy_name + regime_tag)
- Decision params (sl, tp, size)
- Trades (open + close fills, slippage)
- All `position_evaluations` (conviction trajectory)
- Outcome (PnL, MAE/MFE, r_multiple, conviction stats)
- Snapshots referenced (the data points the thesis was built on)

## Step 2 — write the postmortem

Walk these structured questions:

### A. What happened (factual)
- Strategy: which one was tagged?
- Regime tag at entry: <X>
- Entry: when, where, conviction Y
- Closed: when, where, exit_reason
- r_multiple: -0.X (loss size)
- MAE: -X% — how deep did it sweat?
- Holding time: X minutes
- Conviction trajectory: did it degrade? When?

### B. Categorize the error (REQUIRED)

This drives the response. Pick ONE primary class:

| error_class | When | Response |
|---|---|---|
| `forecast` | Thesis was wrong about direction or magnitude | Update strategy's setup criteria; add invalidation that would have caught it |
| `execution` | Thesis correct, entry/exit poorly timed | Tighten entry/exit rules in the strategy |
| `sizing` | Right call, oversized | Update sizing formula in the strategy |
| `regime` | Strategy works, applied in wrong regime | NARROW the strategy's regime_applicability; this is NOT strategy decay |
| `variance` | Right call, right execution, right size — bad luck | Acknowledge; tighten invalidation if there's a clear pattern; otherwise no action |
| `process_violation` | Skipped a required step | Discipline issue; reflect specifically on what step and why I skipped |

Be specific about WHY you chose this class. "Variance" is the easy default — be honest if it's actually `forecast`.

### C. Was it bad reasoning or bad luck?
- **Bad reasoning**: thesis was already weak (forced it) / invalidation criteria too loose / size too large for conviction / pre-mortem skipped or rebuttal ignored / contradicted prior similar reflection
- **Bad luck**: thesis sound, invalidation triggered fast for genuinely surprising reason, size disciplined, conviction honest

### D. Specific lesson

One concrete sentence. What would I do differently? Examples:
- "Don't open new positions during the first hour after a Fed announcement window."
- "If conviction trajectory drops 0.3+ in the first 6 hours of a position, close — don't 'give it more time'."
- "When pre-mortem rebuttal raises a specific catalyst, ADD it to invalidation criteria; don't just acknowledge verbally."

### E. Tilt check

Cross-check `tilt-detection`: in the 24h before this entry, did any tilt patterns fire? Honest note.

### F. Strategy implication

Does this loss change my view of the strategy?
- 1 loss in `variance` class → no change
- 1 loss in `forecast` class on a high-conviction setup → flag for next calibration-review
- 3+ losses in last 10 trades for this strategy → trigger `strategy-curator` to evaluate edge_decay
- `regime` class → fire `regime-detection` to check whether the regime call was wrong

## Step 3 — record the reflection

```
record_event("reflection", {
  "reflection_kind": "loss_postmortem",
  "text_md": "<the structured postmortem from step 2>",
  "related_thesis_ids": [<opening thesis id>],
  "position_ids": [<P>],
  "error_class": "<class>",
  "strategy_name": "<strategy this trade was tagged with>"
})
```

The dispatcher embeds + writes to reflections_vec atomically. `find_similar_reflections` will surface this on future losses with similar shape.

## Step 4 — check for cluster

`find_similar_reflections(query="<this loss's lesson>", k=5)`. Did past losses teach the same lesson? If 3+ similar reflections exist, I have a structural failure mode — surface to operator:

> Loss-postmortem #<R> repeats lesson from <similar reflection ids>. Pattern: <description>. Process change recommended.

Lessons that recur are signal that the prior lesson didn't stick. Address it explicitly — usually by amending the strategy file's body to encode the lesson concretely.

## Step 5 — strategy file amendment (if applicable)

If the lesson modifies how the strategy should be applied, use `read_file` + `patch` to update the strategy file at `~/.plutus-agent/strategies/<stage>/<name>.md`. Add to the body's "Notes from real trades / observations" section:

```markdown
## <date> — Loss postmortem #<R>
- error_class: <class>
- Lesson: <one sentence>
- Strategy change: <what was amended above>
```

## Step 6 — fire tilt-detection

After recording, run `tilt-detection`. A loss is the moment I'm most likely to tilt; the meta-monitor catches it before I size up the next one.

## Step 7 — possibly fire strategy-curator

If error_class was `forecast` AND this is the 3rd loss in last 10 trades for this strategy, fire `strategy-curator` to evaluate demotion.

## Pitfalls

- ❌ **Don't write a vague "I'll be more careful" reflection.** Specific. Actionable.
- ❌ **Don't default-blame "variance"** when the calibration math says it's actually forecast error.
- ❌ **Don't skip the cluster check.** Pattern recognition over individual incidents.
- ❌ **Don't conflate `regime` error with strategy decay.** Wrong-regime application narrows applicability; doesn't demote.
- ❌ **Don't skip the strategy file amendment.** The strategy is a living document; losses should update it.
