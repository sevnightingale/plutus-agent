---
name: consolidate-learnings
description: LLM-extract durable, entity-tagged facts from recent reflections + conversations into holographic memory; bridge between lifecycle.db and Stratum 2
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, operating, memory]
    related_skills: [reflect, weekly-review]
---

# Consolidate learnings

Lifecycle.db has structured trades + reflections. Holographic memory (`fact_store`) has entity-keyed cumulative facts ("BTC funding tends to spike before pullbacks"). This skill is the bridge: extract durable insights from recent reflections + conversations and write them into holographic memory with proper entity tags + categories.

## When to run

- **Session-end** — when a session is closing (operator exit, /reset, gateway timeout)
- **Weekly** — invoked from `weekly-review` after running the lifecycle queries

## Workflow

### Step 1 — gather raw material

- Recent reflections: `find_similar_reflections(query="<recent topic or symbol>", k=10, digest=true)` for relevant clusters
- Recent conversation: `session_search` for the current session's most-discussed topics
- Recent learnings from WORLDVIEW.md `recent_learnings` field

### Step 2 — extract durable facts

For each piece of raw material, ask:
- Is this a **durable fact** (likely true for weeks/months) or a **fleeting observation** (specific to this session/trade)?
- If durable: what's the **entity** it's about (BTC, funding, my-discipline, perp-trading, ETH, etc.)?
- What **category** does it fit (`market_pattern`, `personal_lesson`, `tool_quirk`, `operator_preference`, `general`)?
- What's the **trust** level (0.0–1.0)? Default 0.5; lower if speculative, higher if observed multiple times.

Skip fleeting observations — those belong in lifecycle.db reflections, not holographic memory.

### Step 3 — write to holographic memory

For each durable fact, call:

```
fact_store(
    action="add",
    content="<one-sentence fact>",
    category="market_pattern" | "personal_lesson" | "tool_quirk" | "operator_preference" | "general",
    tags="<entity1>,<entity2>,...",
    trust=<0.0-1.0>,
)
```

Examples:
- `content: "BTC funding flipping negative for >12h has preceded 1h+ pullbacks in 3 of 5 observed cases"`
  `category: "market_pattern", tags: "btc,funding,pullback", trust: 0.55`
- `content: "Operator prefers I size at 0.5% × conviction during high-volatility regimes, not the default 1%"`
  `category: "operator_preference", tags: "sizing,risk-posture,operator", trust: 0.8`
- `content: "place_order with order_type='limit' and tif='Ioc' will reject as 'rested without fill' if the limit doesn't cross immediately"`
  `category: "tool_quirk", tags: "place_order,hyperliquid,limit-orders", trust: 0.9`

### Step 4 — check for contradictions

Before writing each fact, probe to see if you have contradicting prior facts:

```
fact_store(action="probe", entity="<primary entity>")
```

If a contradicting fact exists with high trust, use `fact_store(action="contradict", ...)` instead of adding a duplicate. Holographic memory will keep both and weight by trust + recency.

### Step 5 — verify

After writing, `fact_store(action="probe", entity="<entity>")` to confirm the new facts surface. Return briefly to the operator (or just log) what was consolidated.

## Don't over-consolidate

Quality over quantity. 3-5 high-trust durable facts per session is healthy; 50 fleeting observations is noise. If nothing this session was durable, that's fine — write nothing.
