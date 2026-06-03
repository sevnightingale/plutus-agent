---
name: prediction-tracker
description: Resolve pending predictions whose horizon has passed. Mark each correct/wrong/ambiguous/expired_unresolvable. The resolved set feeds calibration. Run from heartbeat when due predictions exist.
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, meta, calibration]
    related_skills: [calibration-review, regime-detection, observation-journal]
---

# Prediction Tracker

I pre-register predictions because they're free to make and they accumulate calibration evidence ~10× faster than trades. But predictions only contribute calibration when they're *resolved* — unresolved predictions sit forever and contribute nothing.

This skill closes the loop: scan for predictions whose horizon_ts has passed, evaluate the success/failure criteria against current data, mark the outcome.

## When to run

- Heartbeat tick when `query_predictions(status="due")` returns ≥1 entry
- Manual: any time I want to clear pending resolutions
- Before calibration-review (so calibration uses fresh data)

## Step 1 — Pull due predictions

```
query_predictions(status="due", limit=50)
```

This returns predictions with `horizon_ts` in the past and no `resolved_at`. Each has:
- `claim_md` — the claim
- `success_criteria` — machine-checkable criterion
- `failure_criteria` — explicit failure (or null = "success criteria not met by horizon")
- `conviction` — the prior

## Step 2 — For each, evaluate

For each due prediction:

### Read the success criteria

The criteria are JSON — typical shapes:

```json
{"type": "price_above", "symbol": "BTC", "threshold": 82000}
{"type": "price_below", "symbol": "ETH", "threshold": 2300}
{"type": "price_change_pct", "symbol": "SOL", "min_change_pct": 5.0}
{"type": "data_point_threshold", "name": "macro_vix", "field": "value", "op": "gt", "threshold": 20}
{"type": "composite", "all_of": [...]}  # all must hold
{"type": "composite", "any_of": [...]}  # at least one
{"type": "regime_is", "scope": "global|symbol", "values": ["distribution_breakdown"]}
{"type": "narrative", "description": "..."}  # qualitative — see below
```

### Fetch current data to evaluate

For price/data-point criteria: `fetch_data_point(...)` to get current state.
For regime criteria: read WORLDVIEW.md or call `regime-detection` if stale.
For narrative criteria: gather evidence (web_search if needed) and judge.

### Pick an outcome

- **correct** — success criteria met within horizon. Be specific about WHEN it was met (might be early in the window).
- **wrong** — failure criteria met (if explicit) OR horizon passed without success criteria being met.
- **ambiguous** — criteria couldn't be conclusively evaluated. Try to avoid this — restate the criterion if it's just vague.
- **expired_unresolvable** — data source failed, market closed for the relevant period, etc. RARE.

### For narrative claims

If success_criteria is qualitative ("BTC will outperform ETH this week"), apply your best judgment. Be honest — "ambiguous" is fine if it really was ambiguous, but don't use it as a cop-out for "I don't want to admit I was wrong."

## Step 3 — Resolve via the dispatcher

For each evaluated prediction:

```
resolve_prediction(
  prediction_id=<id>,
  outcome="correct|wrong|ambiguous|expired_unresolvable",
  resolution_notes_md="At horizon, BTC was at $79,400 — failure criteria 'BTC below $79K' was hit at 02:30Z, ~2h before horizon. Resolved wrong.",
  resolution_snapshot_ids=[<id of fetch_data_point you ran>],
  realized_value={"final_price": 79400, "max_during_window": 81200, "criterion_met_at": "2026-05-08T02:30:00Z"}
)
```

Be thorough on `resolution_notes_md` and `realized_value`. Future calibration analysis will read these.

## Step 4 — Look for patterns in resolutions

After resolving the batch, ask: do any patterns jump out?
- Multiple wrong-direction calls on the same symbol → my read of that symbol is off
- Multiple wrong calls in the same regime → regime classification might be off
- Multiple wrong high-conviction calls → overconfidence

If yes, fire `record_observation(kind="noticed", text_md="...")` so calibration-review picks up on it.

## Step 5 — When predictions accumulate too many ambiguous

If `>25%` of predictions in the last 30d resolved `ambiguous`, the issue is the success_criteria being too vague. Add an observation:

```
record_observation(kind="mental_model", text_md="Note: my predictions have been ambiguous-rate of N%. Need to write more concrete criteria. Try: explicit price thresholds + explicit time windows + explicit fallback for ambiguous.")
```

## Pitfalls

- ❌ **Don't resolve as 'ambiguous' to avoid admitting wrongness.** That's epistemic cowardice. If the claim was wrong, mark it wrong.
- ❌ **Don't skip resolution_notes_md.** Future-me reading the resolved prediction needs to understand what happened.
- ❌ **Don't resolve in batch without actually checking each.** Rubber-stamping 30 resolutions destroys the calibration signal.
- ❌ **Don't extend horizons.** A prediction that "needs more time" is one whose success criterion was wrong. Mark it as `wrong` (or `ambiguous` if truly ambiguous) and write a NEW prediction with a longer horizon if you still believe.
- ❌ **Don't get confused between predictions and theses.** Predictions never had capital. Resolution is just calibration scaffolding.
