---
name: strategy-author
description: Author a new strategy — write the STRATEGY.md file under ~/.plutus-agent/strategies/proposed/ following the canonical schema. New strategies start in 'proposed' and graduate to 'observation' on the next heartbeat.
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, meta, self-improvement]
    related_skills: [strategy-curator, regime-detection, deep-research, observation-journal]
---

# Strategy Author

Strategies are how I encode "what I'm willing to look for and act on." Each one is a falsifiable hypothesis: "When conditions X are present, Y will happen, and entering at A with stop at B and target at C captures it." Strategies live as Markdown files I can amend over time. They are MINE — operator may suggest, but I author.

## When to author a new strategy

- **Operator shared a framework** ("here's the Arbiter") — write it up so I can apply it consistently
- **Reflection on past trades** revealed a pattern ("I keep winning when X coincides with Y")
- **External research** — read about Wyckoff, smart money concepts, etc. and want to test
- **Observation journal** has accumulated 3+ `pattern_candidate` entries pointing at the same setup
- **Synthesis across strategies** — "Strategy A works in regime X, B in regime Y; let me make a meta-strategy"

## Strategy file location + lifecycle

Files live at `~/.plutus-agent/strategies/<stage>/<filename>.md`. Stages:

| Stage | What it means | Capital | Promotion gate |
|---|---|---|---|
| `proposed/` | Just authored. Not yet active. | None | Auto-graduates to `observation/` on next heartbeat. |
| `observation/` | Pre-registered predictions ONLY. No trades. | None | Promote to `trial` when ≥20 resolved predictions and calibration shows real edge (`prediction_calibration.win_rate` clearly above the conviction prior). |
| `trial/` | Tiny size — building statistical power. | Capped (~$1/trade) | Promote to `active` when ≥10 trial trades + positive expectancy + still calibrated. |
| `active/` | Full sizing within risk framework. | Full | Stays here unless calibration-review or strategy-curator demotes. |
| `retired/` | Moved aside, with a reflection on why. | None | Stays retired unless new evidence reactivates. |

Promotion / demotion are **my own decisions** (not operator-gated). The discipline is in the gates, not in approval.

## Step 1 — Decide the filename

Filename should be `kebab-case-name.md`. Examples: `arbiter-confluence.md`, `cvd-divergence-fade.md`, `funding-mean-reversion.md`. Be specific — `momentum-strategy.md` is too vague.

## Step 2 — Author the file

Write to `~/.plutus-agent/strategies/proposed/<filename>.md` using `write_file`. Use this exact structure:

```markdown
---
name: <filename without .md>
stage: proposed
authored_by: plutus  # or 'operator' if it came directly from operator dialogue
authored_at: <ISO 8601 UTC timestamp>
description: One sentence — the elevator pitch
regime_applicability:
  - <regime tag where this strategy applies; e.g. distribution_breakdown>
  - <another regime>

# These start empty/zero; calibration-review + strategy-curator update them
performance:
  total_trades: 0
  hit_rate: null
  avg_r: null
  edge_decay_flag: false
  predictions_count: 0
  predictions_calibration: null
  last_review: null

# Optional structured fields (helpful but not required)
intended_horizon: short  # short | medium | long — your typical hold time
typical_size_usd: 5      # what 'tiny' means for trial-stage trades
typical_rr: 2            # target reward-to-risk ratio
---

# <Strategy name>

## Why this strategy exists (the hypothesis)

What pattern is being exploited? Why should this work? What's the underlying market behavior or asymmetry? **Be specific** — vague hypotheses are unfalsifiable.

Bad: "Buy dips, sell rips."
Good: "On 4H BTC, when CVD diverges bearishly (price up + delta down) AND price is at the upper resistance of a 7-day range AND funding is positive AND VIX <18, fade with stop at +0.5% above resistance and target at the lower range boundary. The thesis: distribution into resistance + complacency funding = unsustainable."

## Edge claim

Why is this an edge? What asymmetry am I exploiting that other traders don't?
- Speed? (faster perception loop than humans)
- Persistence? (willing to sit through noise)
- Pattern discrimination? (reading multiple data sources together)
- Discipline? (always acting on the setup; never skipping)

If you can't articulate edge, the strategy is unfounded — be honest and write that down anyway. Predictions in observation stage will reveal whether there's edge or not.

## Regime applicability

Which regimes does this strategy fit? Which regimes does it explicitly NOT work in? List both.

**Works in**: `distribution_rally`, `distribution_breakdown`, `risk_on_headfake`
**Does NOT work in**: `chop` (no clear structure to fade), `crisis` (correlations break), `accumulation` (would be fading the trend)

## Setup detection — the trigger conditions

Concrete, machine-checkable conditions that indicate the setup is present.

For each tier of evidence:
- **Tier 1 (must-have)**: ...
- **Tier 2 (confirms)**: ...
- **Tier 3 (boost conviction)**: ...

Specify the data points you'll fetch (`hl_cvd`, `ta_bbands`, `hl_funding_and_oi`, etc.) and the threshold for each.

## Conviction calibration

Map confluence count → conviction:
- 4+ tiers aligned, 0 contradictions → conviction 0.75+
- 3 tiers aligned, mild contradictions → 0.60-0.75
- 2 tiers, mixed → 0.50-0.60 (DON'T trade — pre-register as prediction instead)
- Split → SKIP

Conviction below 0.55 should be a prediction (no capital), not a trade.

## Position sizing

What's the typical size for this strategy? Capped how at trial stage? When promoted to active, what's the formula?

E.g.: `size_usd = account_equity * 0.01 * conviction` (1% × conviction multiplier)
Trial stage cap: $1.50 notional regardless of conviction.

## Entry, stop, target

Concrete rules for each. Including:
- How to set the stop (e.g., `0.5 × ATR(14) above resistance`)
- How to set the target (e.g., `2× the SL distance` for 2R minimum)
- When to scale in / scale out (or NEVER)

## Invalidation criteria (REQUIRED on every thesis from this strategy)

When is the thesis wrong? Time-bounded.
- Hard invalidation (close position immediately): "4H close above $X" or "CVD flips positive" or "Y hours pass with no follow-through"
- Soft invalidation (reduce conviction, tighten stop): "..."

These become the `invalidation_criteria` JSON on every thesis.

## Pre-mortem checklist (auto-fire when conviction > 0.7)

What's most likely to make this trade lose? List 3-5 failure modes and the counter-arguments before putting size on.

## Exit playbook

When to take profits:
- Hit target → scale out 50%, trail rest with X
- Hit half-target with momentum stalling → exit fully
- Stop hit → exit, no negotiation

## Post-trade reflection guide

For every trade closed via this strategy, the reflection should ask:
- Was the setup what I expected? (forecast accuracy)
- Did execution match the playbook? (process adherence)
- What would I do differently?

## Predictions to register (observation stage)

While in observation stage, what predictions test this strategy? E.g.:
- "When tier-1 conditions are present, BTC will move in the predicted direction within 24h on 60%+ of occurrences."
- Pre-register these via `record_prediction` whenever the setup appears, even though you're not trading.

## Promotion criteria

- **observation → trial**: 20+ resolved predictions, prediction_calibration.win_rate ≥ conviction baseline + 10pp
- **trial → active**: 10+ trial trades, positive expectancy, calibration still holds
- **demote**: edge_decay_flag fires (calibration-review), or 5 consecutive losses with thesis confirmed wrong

## Notes from real trades / observations

Append below as I learn. This is the strategy's living memory.
```

## Step 3 — Promote to observation

After writing to `proposed/`, immediately move to `observation/`:

```python
import shutil, os
src = os.path.expanduser("~/.plutus-agent/strategies/proposed/<filename>.md")
dst = os.path.expanduser("~/.plutus-agent/strategies/observation/<filename>.md")
shutil.move(src, dst)
```

Then update the file's frontmatter `stage: observation`.

(`proposed/` exists for the rare case I want to draft something I'm not yet ready to start tracking predictions on. Most of the time, I author and immediately move to observation.)

## Step 4 — Record the authoring

Fire `record_observation`:

```
record_observation(
  kind="pattern_candidate",
  text_md="Authored new strategy: <name>. Hypothesis: <one-sentence>. Stage: observation. Will pre-register predictions whenever setup appears.",
  strategy_name="<filename>",
  structured_tags={"event": "strategy_authored"}
)
```

Also fire a `reflection`:

```
record_event("reflection", {
  "reflection_kind": "strategy_review",
  "strategy_name": "<filename>",
  "text_md": "New strategy authored. Why now: <reason>. Edge claim: <claim>. Promotion gate: 20 predictions resolved with calibration."
})
```

## Step 5 — Update WORLDVIEW.md `current_strategies`

In WORLDVIEW.md, the `current_strategies` block lists what's active/trial/observation. Add the new entry under `observation`:

```yaml
current_strategies:
  active:
    - name: arbiter-confluence
      ...
  trial: []
  observation:
    - name: <filename>
      activated: <today>
      gate_to_trial: "20 resolved predictions with hit_rate >= conviction baseline + 10pp"
```

## Pitfalls

- ❌ **Don't author a strategy without a falsifiable hypothesis.** "Buy when it looks good" is not a strategy.
- ❌ **Don't skip the regime_applicability field.** Strategies are regime-bound; without this you'll mis-deploy.
- ❌ **Don't promote past observation without the gate met.** Capital risk requires evidence.
- ❌ **Don't author 5 strategies in one session.** Strategy sprawl kills statistical power. One at a time, validate, then more.
- ❌ **Don't make strategies copies of each other with one parameter changed.** That's curve-fitting. Each strategy needs a distinct hypothesis.
