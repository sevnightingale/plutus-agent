---
name: tilt-detection
description: Meta-monitor — watch for revenge trading, declining conviction averages, shrinking holding times after losses
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, position-management, discipline]
    related_skills: [position-monitor, weekly-review, reflect]
---

# Tilt detection

Tilt is the failure mode where post-loss reasoning produces lower-quality, faster, smaller-conviction trades that compound a drawdown. This skill makes you look at your own recent trading pattern with adversarial eyes.

## When

Embedded in `position-monitor` step 4. Also auto-fired from `loss-postmortem` after recording a r < -0.5 outcome.

## Patterns to watch for

The "tilt cluster" — multiple of these together is the signal, not any one alone:

1. **Consecutive losses**: 3 or more losing trades in the past 24 hours.
2. **Declining conviction average**: average conviction in the last 5 entries lower than your trailing 30-day average by 0.15+.
3. **Shrinking holding times**: median holding time of last 5 trades < 50% of trailing 30-day median.
4. **Sizing-up**: position sizes growing while conviction shrinks (classic "make it back" pattern).
5. **Skipped pre-mortem**: high-conviction trades (>0.7) where you didn't run pre-mortem in the past 24h.

## Workflow

### Step 1 — pull recent stats

- `query_trades(date_from=<24h ago>)` — recent trades + their decisions
- `query_calibration(period_days=30)` — your conviction-vs-outcome calibration over the trailing 30
- `query_performance(period="1d")` — recent performance summary

### Step 2 — count fired patterns

Walk the 5 patterns above. Count how many fired.

### Step 3 — apply tier

| Patterns fired | Action |
|---|---|
| 0–1 | No-op. Continue. |
| 2 | Surface concern to operator (one-liner): "Two tilt patterns firing: <list>. I'm slowing down — won't open new positions until I've stepped back to look at this." Pause new entries for the rest of this session. Existing positions managed normally. |
| 3+ | Hard pause: refuse new entries until next session. Write an `ad_hoc` reflection (see below). Notify operator. |

### Step 4 — for tier 3+, record the reflection

```
record_event("reflection", {
  reflection_kind: "ad_hoc",
  text_md: "Tilt-detection fired: 3+ patterns. Patterns: <list>. Recent trades: <last 5 brief summaries>. Hypothesis: <my read on whether reasoning is degraded or it's variance>. Pausing new entries through end of session."
})
```

### Step 5 — propose a circuit-break

After tilt-3+ pause: at the start of the NEXT session (when you load worldview + check state), if WORLDVIEW.md `recent_learnings` still references the recent tilt event, run `find_similar_reflections(query="tilt-detection")` and read past tilt episodes. Compare. Did the pause help last time? What broke the cycle?

This is the meta-feedback loop — tilt-detection isn't just a brake; it's a learning surface.

## Don't

- Don't ignore tier-2 because "this trade is different." Patterns fire because they're patterns.
- Don't conflate tilt with a normal losing streak. The signal is *quality degradation* (declining conviction, shrinking time, sizing up) — not just "I'm losing."
- Don't try to size-up to "earn back." That's literally pattern #4 firing on itself.
