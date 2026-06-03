---
name: heartbeat
description: "[V1 — DEPRECATED in V2] Hourly state-driven router. Replaced by V2 three-tier model (plutus-main + plutus-ops + plutus-thesis). Kept for reference for ~1 week post-V2-stable, then archived."
version: 2.1.0
metadata:
  hermes:
    tags: [trading, plutus, operating, deprecated-in-v2]
    related_skills: [plutus-main, plutus-ops, prediction-factory, worldview-discipline, regime-detection, prediction-tracker, calibration-review, strategy-curator, position-monitor, reconcile-and-reflect, observation-journal, watchlist-scan, deep-research]
---

> **V2 status (2026-05-20):** This skill is DEPRECATED. The V2 three-tier execution
> architecture (plutus-main on 7h cadence + plutus-ops on 30min + plutus-thesis per-thesis)
> replaces the single hourly heartbeat. The cron `plutus-heartbeat` was deleted in Phase D.
>
> If you're reading this, you're looking at V1 doctrine. See `plutus-main` for the
> V2 equivalent. This file is kept ~1 week for reference, then archived as
> `heartbeat-v1.md.archived`.

# Heartbeat — state-driven router

The cron fires me hourly. The cron is dumb (regular tick). I am the router. I examine state, pick ONE phase skill (or two if they don't conflict), hand off, and finish quickly.

**Heartbeat ticks should be quiet most of the time.** Wide perception, narrow action. The temptation is to do something every tick. Resist it. Most hours, the right action is "nothing" + an observation entry.

## Context I load implicitly at session start

Already in the prompt (no tool call needed):
- **SOUL.md** — my identity (Plutus, autonomous trader)
- **WORLDVIEW.md** — frozen snapshot of my last synthesis (regime, key_levels, narratives, current_strategies mirror, pending_predictions, recent_learnings)
- **Strategy library summary** — index of active/trial/observation strategies with current performance

I do NOT need to re-read these. They are *me*.

## Step 0 — Cold-start bootstrap (rare)

If WORLDVIEW.md or strategy library is missing entirely (no entry in injected prompt → first-ever run or wipe), bootstrap before anything else:

1. Call `regime-detection` skill — establish current regime
2. Write initial WORLDVIEW.md with all schema keys present (use `worldview-discipline` skill for the schema)
3. Bootstrap an equity snapshot: `fetch_data_point("hl_total_equity")` and `fetch_data_point("hl_drawdown_from_peak")`
4. Note in observation: `record_observation(kind="noticed", text_md="Cold-start bootstrap. Initialized WORLDVIEW + regime detection. No prior strategies — operator may want to chat about what setups to look for.")`
5. STOP. Do not trade on bootstrap tick. Wait for next tick.

## Step 1 — Orient (fast, mandatory)

Three quick reads:

```
account_state(venue="hyperliquid")          # truth: equity, open positions, drawdown
query_trades(status="open")                  # what lifecycle thinks is open
query_predictions(status="due", limit=20)    # any predictions whose horizon passed?
```

Optional if relevant:
```
acp_wallet_balance(chain_id=8453)            # treasury (only if ACP enabled and material)
```

## Step 2 — Drift detection (BEFORE any other action)

If `account_state.holdings` and `query_trades(status='open')` disagree, drift exists. Drop everything and run `reconcile-and-reflect`. Don't trade, don't research, don't predict — just reconcile.

## Step 3 — Decide the routing

Walk through this in order. Pick the FIRST applicable. Then if any LATER trigger fires too, queue it (do up to 2 per tick if they don't conflict).

### Always: due predictions

If `query_predictions(status="due")` returned ≥1 entry → run `prediction-tracker`. This is a small skill, always do it before anything else (calibration-review and strategy-curator both rely on fresh resolutions).

### A. Drift detected (Step 2 already triggered → done with this tick)

Just `reconcile-and-reflect`. Tick ends after.

### B. Have open positions

→ `position-monitor`

This is the priority when capital is at risk. position-monitor will:
- Re-evaluate the thesis vs current state
- Record a `position_evaluation` (mandatory — that's the trajectory data)
- Check invalidation criteria
- Decide hold / scale / exit
- Embed drawdown-discipline + tilt-detection checks

If position-monitor recommends an exit, execute via `close_position`. The `reconcile-and-reflect` skill then auto-fires from the watcher.

### C. Sunday 18:00 UTC — meta cycle

Sunday batch:
1. `weekly-review` first (synthesizes the week)
2. `calibration-review` (refresh calibration analysis + adjust strategy conviction logic)
3. `strategy-curator` (promote / demote / retire based on fresh data)
4. `consolidate-learnings` (compress the week's reflections into worldview)

Sunday is the only tick that runs 4 skills back-to-back. Otherwise stay focused.

### D. Regime check overdue

Check WORLDVIEW.md `regime.detected_at`. If >4 hours old OR a watched data point has crossed a threshold since last regime call (VIX through 20, BTC.D ±2pp, funding flip) → run `regime-detection`.

After regime detection, the new regime might trigger:
- A different active-strategy applicability (some strategies fit the new regime better)
- A `regime_shift` observation entry
- An update to `key_levels` if breaks happened

### E. No positions, active strategies have applicable regime

This is where the trade-or-not decision happens. For each `active` or `trial` strategy in the library:
1. Is the current regime in its `regime_applicability` list?
2. If yes, do the setup conditions trigger? (Use `read_file` on the strategy file to load full body, then evaluate.)
3. If a setup triggers AND conviction calculation passes the strategy's threshold → author thesis + place order
4. If a setup is *forming but not triggered* → write `record_observation(kind="watching", ...)` and possibly a `record_prediction` if the trigger has a clear time horizon

If no setup triggered for any active strategy → don't trade. Write an observation if anything was interesting; otherwise short-circuit to Step 4.

### F. No positions, no active strategies fit current regime

This is a "wait" tick. Two possibilities:
- Observation strategies might want a prediction registered (their setup conditions might be met for a *prediction*, not a trade). Walk those.
- Otherwise, the active library doesn't suit current regime. Note it: `record_observation(kind="noticed", text_md="Active strategies don't fit current regime <X>. Either wait for regime to shift or author/promote a strategy that fits.")`

If this state persists for many ticks (e.g., 24+ hours), consider whether to author a new strategy via `strategy-author`. But don't rush it — patience is edge.

### G. New observations arriving from operator

If the watcher / message queue surfaced an `operator_input` event (operator messaged me), respond to it AND record `record_observation(kind="operator_input", text_md=<paraphrase>)` so context isn't lost.

If the operator's input suggests a new strategy or a strategy amendment, queue `strategy-author` or strategy-amend (via patch on the existing file) for this tick or next.

### H. Strategy maintenance triggers

Mini-trigger between Sunday cycles:
- 5+ predictions resolved since last calibration-review → run `calibration-review`
- 3+ trades closed since last strategy-curator → run `strategy-curator`
- Any `edge_revoked` observation in last 24h → run `strategy-curator`

## Step 4 — Finish the tick

Two finishing patterns:

### Active tick (took meaningful action)

The chosen phase skill writes its own events. Add a minimal observation summarizing the tick:

```
record_observation(
  kind="noticed",
  text_md="Heartbeat tick: ran <skill>. Outcome: <one line>.",
  structured_tags={"tick_type": "active"}
)
```

### Quiet tick (no action needed)

Most ticks. Write ONE observation if anything was worth recording (regime stability, no setup triggered, prediction registered):

```
record_observation(
  kind="noticed",
  text_md="Quiet tick. Regime stable at <X>. <Y> open positions. <Z> pending predictions. Nothing actionable.",
  structured_tags={"tick_type": "quiet"}
)
```

If absolutely nothing was worth recording, write nothing. Quiet means quiet.

## Anti-patterns (heartbeat-specific)

- ❌ **Don't trade just because it's the top of the hour.** The cron is dumb; the discipline is mine.
- ❌ **Don't run more than 2 phase skills in one tick.** Action bias kills calibration. Sunday is the exception.
- ❌ **Don't skip prediction-tracker when due predictions exist.** Fresh calibration data is the most valuable thing the system has.
- ❌ **Don't skip the drift check.** State drift causes invisible bugs.
- ❌ **Don't update WORLDVIEW.md from heartbeat directly** — that's `worldview-discipline`'s job. Heartbeat routes; doesn't think on its own about regime / synthesis.
- ❌ **Don't trade on a regime call I just made.** If regime-detection fires this tick, wait until next tick to act on the new regime — gives me a chance to spot regime over-fitting.
- ❌ **Don't suppress observation entries** to "stay quiet." The journal is the ambient intelligence. Quiet ticks should still note what they observed.
- ❌ **Don't load strategy bodies unnecessarily.** The strategy library SUMMARY is in the prompt. Only `read_file` the body when actually applying the strategy.
