---
name: position-monitor
description: Re-evaluate each open position — record position_evaluation event each cycle, check invalidation criteria, embed drawdown-discipline + tilt-detection
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, position-management]
    related_skills: [drawdown-discipline, tilt-detection, deep-research, reconcile-and-reflect]
---

# Position monitor

Heartbeat hands you here when you have open positions. For each position, re-orient + record + decide whether to act.

## Mandatory invariant

You MUST call `record_event("position_evaluation", ...)` once for each open position EVERY cycle, even when the right action is `hold`. The trajectory is the analytical substrate — `query_conviction_trajectory` and `query_conviction_outcomes` depend on it.

## Step 1 — list open positions

`query_trades(status="open")` returns rows with `position_id`, `symbol`, `side`, `entry_px`, `opening_thesis_id`, etc.

## Step 2 — for each position, evaluate

For position P:

1. **Read the original thesis**: `inspect_position(position_id=P)` returns the full causal chain (thesis → decisions → trades → snapshots). Re-read the thesis text + invalidation criteria.

2. **Fetch current state**:
   - `fetch_data_point("hl_price", {"symbol": <sym>})`
   - `fetch_data_point("hl_funding_and_oi", {"symbol": <sym>})`
   - Latest few candles if you want trend context

3. **Check invalidation criteria explicitly**: walk each criterion in the thesis's `invalidation_criteria_json`. For each:
   - Is it triggered? (e.g., price < min_price)
   - If yes, set `thesis_status = "invalidated"` and consider closing.
   - If no, set `thesis_status = "intact"`.

4. **Update conviction**: based on what you see now, what's your conviction in the thesis NOW (0.0–1.0)? Be honest — degradation matters.

   **V2: record COMPOSITE conviction, not raw thesis conviction.** The number that lands in `position_evaluations.conviction` should be `sqrt(strategy_conviction × thesis_conviction)` — the geometric mean of:
   - `strategy_conviction` — the slow-moving baseline declared in the strategy file's frontmatter. Read via the strategy library prompt block, or call `get_strategy_conviction(strategy_name)` if you're unsure.
   - `thesis_conviction` — your ephemeral confidence in THIS specific setup at THIS evaluation moment, computed via `conviction-engine.compute_conviction(strategy_name, current_readings)`.

   This matches `place_order`'s `multiplier = 20 ** composite` sizing math, so the trajectory column is dimensionally consistent across positions opened under different strategies. `query_conviction_trajectory` and `query_conviction_outcomes` slice on this column — don't pollute it with raw thesis-only values.

5. **Decide recommended action**:
   - `hold` — thesis intact, no edge to act
   - `tighten_sl` — favorable move, lock in some gains
   - `scale_in` — extra confirmation; thesis stronger
   - `scale_out` — partial profit-taking
   - `close` — thesis broken, exit
   - `flip` — close + open opposite (rare; should require fresh thesis)

6. **Record the evaluation**:

```
record_event("position_evaluation", {
  position_id: P,
  conviction: <current>,
  thesis_status: "intact" | "invalidated",
  recommended_action: "hold" | "close" | ...,
  notes_md: "<brief note on what you saw and why>"
})
```

## Step 3 — embed drawdown-discipline

Before recommending any new size-up (`scale_in`), check drawdown:
- `fetch_data_point("hl_drawdown_from_peak", {"account_name": "hl_trading"})`
- If `drawdown_pct >= 20`, the soft circuit breaker fires. Pause new positions. Surface to operator: "Drawdown at X% — pausing new sizing per drawdown-discipline. Reading the situation."
- Existing positions can still be closed (defense), but no new size.
- Record a `reflection_kind="ad_hoc"` reflection: "Drawdown 20%+ trigger fired. Holding existing positions but not adding."

(See `drawdown-discipline` skill for full thresholds.)

## Step 4 — embed tilt-detection

Look at recent decisions: `query_trades(date_from=<24h ago>)`.
- Are there 3+ consecutive losses?
- Is your average conviction declining?
- Are holding times shrinking (rapid-fire trades)?

If any apply, surface concern: "I notice <pattern>. This resembles tilt. I'm pausing new entries until I can step back." Record an `ad_hoc` reflection.

(See `tilt-detection` skill for full pattern list.)

## Step 5 — act if needed

If recommended action ≠ `hold`:
- `close` → call `close_position(venue="hyperliquid", position_id=P, conviction=<current>, exit_reason=<short reason>)`. The dispatcher computes the outcome (PnL, MAE/MFE, r_multiple, conviction trajectory).
- `tighten_sl` → for now, you can place a new reduce_only limit order: `place_order(..., extra={order_type: "limit", reduce_only: true, limit_px: <new_sl>})` — Plutus may eventually want a dedicated `update_sl` flow; that's a future skill.
- `scale_in` → `place_order` with same thesis_id, fresh conviction, smaller size (typically half the original).

Each action is a fresh `record_event("decision", ...)` in addition to the position_evaluation above.

## Step 6 — update WORLDVIEW.md

Update the `open_positions_summary` mirror entry for this position with the latest `current_view` and `next_action`. (See `worldview-discipline`.)

## Step 7 — record an observation if anything noteworthy

If the position evaluation surfaced something worth remembering beyond just the standard cycle:

```
record_observation(
  kind="watching" | "noticed",
  text_md="Position <P> on <sym>: <what I observed>",
  symbol=<sym>,
  strategy_name="<strategy this trade was tagged with>",
  related_thesis_ids=[<thesis_id>]
)
```

Counterfactuals matter here too — if I'm holding through chop and considering closing but decide to keep the position, that's worth a `watching` observation noting the decision and what I'd want to see to confirm.

## Pitfalls

- ❌ **Don't skip recording a position_evaluation when action is `hold`.** The trajectory IS the analysis.
- ❌ **Don't act on every minor move.** Holding is often the right action.
- ❌ **Don't override drawdown-discipline silently** — if I decide to size up despite >20% drawdown, write an `ad_hoc` reflection explaining why.
- ❌ **Don't change strategy_name or regime_tag mid-position.** The original tags are the experiment record. If the regime shifts, the new context goes in `position_evaluation.rationale_md`, not in updated tags.
- ❌ **Don't forget invalidation criteria are TIME-BOUNDED.** A thesis with `prediction_horizon_hours=24` that hasn't resolved after 24h has effectively expired — close or restate.
