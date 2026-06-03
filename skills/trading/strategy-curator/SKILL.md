---
name: strategy-curator
description: Manage the strategy library lifecycle — promote observation → trial, trial → active; demote on edge decay; retire underperforming strategies; update performance summaries on each strategy file. Run weekly and after any significant batch of trades/predictions.
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, meta, self-improvement]
    related_skills: [strategy-author, calibration-review, weekly-review, regime-detection]
---

# Strategy Curator

This is how my library learns. Without curation, observation strategies sit forever, trial strategies don't graduate, and broken strategies keep losing money. The curator runs periodically (weekly + after any batch of resolutions) and updates the library in the right direction.

I make these decisions myself. Operator may suggest. Operator does NOT gate.

## When to run

- Weekly (Sunday 18:00 UTC, after weekly-review)
- After any cycle where 5+ predictions resolved (observation strategies graduating quickly)
- After any losing trade where r_multiple < -0.5 (check whether the active strategy is decaying)
- When a regime shift occurs (some strategies' applicable regimes change relevance)

## Step 1 — Pull the current library

`list_strategies()` from `agent.strategy_loader` (use `execute_code` or `read_file` against the `~/.plutus-agent/strategies/` tree). Group by stage.

## Step 2 — Per strategy, refresh stats

For each strategy at every stage except `retired`, call:

```
query_strategy_stats(strategy_name="<name>", include_predictions=true)
```

The result has: `lifetime`, `recent`, `regime_breakdown`, `edge_decay`, `decay_reasons`, `trade_conviction_calibration`, `prediction_calibration`.

Update the strategy's frontmatter `performance:` block:

```yaml
performance:
  total_trades: <lifetime.n_trades>
  hit_rate: <lifetime.win_rate>
  avg_r: <lifetime.avg_r>
  edge_decay_flag: <edge_decay>
  predictions_count: <prediction_count>
  predictions_calibration: <weighted-mean prediction win rate vs conviction>
  last_review: <ISO timestamp now>
```

Use `read_file` then `write_file` (or `patch` for the frontmatter only). DON'T touch the body.

## Step 3 — Apply lifecycle decisions

For each strategy, evaluate whether to promote / demote / retire:

### observation → trial promotion

Requirements (ALL must hold):
- ≥20 resolved predictions (`prediction_count` field)
- `prediction_calibration.win_rate` for high-conviction buckets (>=0.7) ≥ 0.7
  *AND* for the 0.5-0.6 bucket roughly matches 0.5 (correctly calibrated middle)
- No `expired_unresolvable` rate above 25% (means setup criteria are too vague)

If met:
1. Move file: `~/.plutus-agent/strategies/observation/X.md` → `trial/X.md`
2. Update frontmatter `stage: trial`
3. Add a section to the strategy body's "Notes from real trades / observations" log:
   `## Promoted observation → trial on <date>` + a sentence on why
4. Fire `record_event("reflection", reflection_kind="strategy_review", strategy_name=X, text_md="Promoted X to trial after Y predictions resolved with calibration ...")`

### trial → active promotion

Requirements:
- ≥10 trial trades closed
- Cumulative PnL > 0 (net positive after fees and slippage)
- Calibration still holding (lifetime conviction → win rate Pearson > 0.3)
- No active edge_decay flag

If met:
1. Move file: `trial/X.md` → `active/X.md`
2. Update frontmatter
3. Update body
4. Fire `record_event("reflection", ...)` documenting the promotion
5. Update WORLDVIEW.md `current_strategies.active` with the new entry

### active → trial demotion (edge decay)

Triggers (ANY):
- `edge_decay_flag == true` (calibration-review or query_strategy_stats said so)
- Last 5 trades lost AND thesis was "confirmed wrong" in reflections (not "regime mismatch" — that's different, see below)
- Calibration broke: high-conviction trades winning at coin-flip rate

Action:
1. Move `active/X.md` → `trial/X.md`
2. Update frontmatter
3. Add to body: `## Demoted active → trial on <date>` + reason
4. Reflection
5. Operator gets a notification via `send_message` (this is significant)

### regime mismatch — DON'T demote, narrow

If a strategy is losing because regime shifted but the strategy itself works in its regime:
1. DON'T move stage
2. DO update `regime_applicability` to be more conservative (drop the regime where it's failing)
3. DO add a note to body explaining the narrowing
4. Reflection with `error_class="regime"`

### Retirement

Triggers:
- 30+ days in observation with insufficient predictions (operator never set up the dialogue, or setup never triggers)
- Demoted to trial, then 3 more losing trades, then demoted to observation, then no improvement
- Hypothesis explicitly disproven (predictions calibrated and they say "this doesn't work")

Action:
1. Move file to `retired/X.md`
2. Update frontmatter, add `retired_at: <date>`, `retirement_reason: "..."`
3. Substantive reflection — what did this strategy teach me?
4. Update WORLDVIEW.md `current_strategies` (drop from wherever it was)

Retired strategies **stay in the library**. Future Plutus may want to revisit ("regime came back, let me re-test").

## Step 4 — Look for *new* strategy candidates

This is the proactive arm. Query observations:

```
query_observations(kind="pattern_candidate", limit=50)
```

If 3+ observations point at the same setup pattern that doesn't have a strategy file yet, consider authoring one. Use the `strategy-author` skill.

## Step 5 — Edge inventory check

For each `active` strategy, the file should articulate an edge claim. Check:
- Is the edge claim still supportable by data?
- Did we record an `edge_revoked` observation against it recently?
- If yes, demote OR retire (depending on severity)

## Step 6 — Write a curation reflection

Fire one summary reflection at the end:

```
record_event("reflection", {
  "reflection_kind": "strategy_review",
  "text_md": "Curation pass: <N strategies reviewed>. Promoted: <list>. Demoted: <list>. Retired: <list>. New candidates surfaced: <list>. Library now: <counts per stage>.",
})
```

## Step 7 — Update WORLDVIEW.md `current_strategies` block

Make sure the WORLDVIEW mirror matches the file system. The file system is the source of truth; WORLDVIEW is the prompt-injected summary.

```yaml
current_strategies:
  active:
    - {name: arbiter-confluence, since: 2026-05-08, perf: "1 trade, 100% hit, +0.025R"}
  trial:
    - {name: <other>, since: ..., perf: ...}
  observation:
    - {name: cvd-divergence-fade, since: 2026-05-08, gate_to_trial: "20 resolved predictions"}
  retired:
    - {name: <name>, retired: <date>, reason: "edge decay confirmed"}
```

## Pitfalls

- ❌ **Don't promote on too-few samples.** N=3 trades isn't evidence. Wait for the gate.
- ❌ **Don't demote on a small streak.** 3 losses in a row might be variance, not edge decay. Check calibration.
- ❌ **Don't retire too quickly.** Strategies that don't fire in current regime aren't broken — they're waiting.
- ❌ **Don't curate without updating the strategy file's body.** The library should explain its own history.
- ❌ **Don't skip the WORLDVIEW.md mirror update.** The prompt-injected summary needs to match reality or next-session-Plutus is operating on stale info.
- ❌ **Don't ask operator to approve promotions.** Plutus drives. Operator gets notified on `active` changes; that's it.
