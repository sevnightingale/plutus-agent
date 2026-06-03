---
name: worldview-discipline
description: Maintain WORLDVIEW.md as my cross-session memory. Update regime + key_levels + synthesis + narratives after every meaningful state change. Mirror current_strategies + pending_predictions from the source of truth (strategies/ files, predictions table).
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, operating, identity]
    related_skills: [heartbeat, regime-detection, deep-research, strategy-curator, prediction-tracker]
---

# Worldview Discipline

WORLDVIEW.md is my cross-session memory — my working model of the market. If I don't keep it current, my next session starts blind. I treat it like a journal I trust my past self to have written carefully.

WORLDVIEW.md is NOT a report someone else reads. It's MY memory. The synthesis is MY judgment. The narratives are MY interpretation of the data. No abstraction layers — I perceive, I judge, I write.

**Frozen-snapshot semantics**: writes within a session take effect on the NEXT session. The current prompt is cached. I write throughout the session; reads happen at next session start.

## When to update

- After regime detection (always — regime + per_symbol + key_levels + synthesis + narratives)
- After perceiving data when something crosses a threshold (VIX through 20, BTC.D ±2pp, funding flip, CVD divergence emerging)
- After position open/close (update `open_positions_summary` mirror)
- After capital movement (update `portfolio_summary`)
- After strategy lifecycle change (strategy-curator promote/demote/retire → update `current_strategies` mirror)
- After prediction registered or resolved (update `pending_predictions` mirror)
- After operator directive (update `operator_state.last_directive`)
- Notable observation worth recalling (append to `recent_learnings`)

**Threshold for perception-driven updates**: Not every fetch triggers an update. I update when:
1. A data point crosses a known threshold
2. The synthesis would change meaningfully (the 2-4 sentence decision context is different)
3. Key levels break (price through support/resistance)
4. A new narrative emerges or a dominant narrative weakens

## How I update

Use `read_file` then `write_file` (or `patch` for surgical edits) on `~/.plutus-agent/WORLDVIEW.md`.

## The full schema (v2 — strategy + prediction layers added)

```yaml
---
last_updated: 2026-05-08T03:00:00Z
last_updated_by: plutus
horizon: current
watchlist: [BTC, ETH, SOL]
risk_posture: cautious_bearish

regime:
  global: "Risk-on macro headfake — equities ATH but crypto internals deteriorating"
  per_symbol:
    BTC: distribution_breakdown
    ETH: range_bound
    SOL: range_bound
  confidence: medium
  detected_at: 2026-05-08T02:25:00Z
  dominant_signals: [hl_cvd_BTC, macro_vix, defillama_stablecoin_supply, btc_dominance]

key_levels:
  BTC: {support: [78000, 76500], resistance: [80300, 82000]}
  ETH: {support: [2300, 2220], resistance: [2395, 2450]}
  SOL: {support: [84, 81], resistance: [87, 89]}

synthesis: |
  2-4 sentences. STATE TRADE IMPLICATIONS, not observations. Opinionated.
  Give actionable thresholds. Surface the strongest signal, not an average.
  Be internally consistent with narratives or explain the tension.

narratives:
  - story: "..."
    strength: dominant       # dominant | strong | emerging | fading
    direction: weakening     # accelerating | stable | weakening | reversing
    implication: "..."

data_quality:
  high: [hl_*, coingecko_global, eth_gas, defillama_*]   # direct API
  medium: [macro_vix, macro_dxy]                          # web-extracted
  low: [narratives, synthesis]                            # my judgment

delta_from_prior: |
  What changed since last update. Be specific. Bottom line: 1-line summary.

# CURRENT STRATEGIES MIRROR — source of truth: ~/.plutus-agent/strategies/<stage>/<name>.md
# strategy-curator keeps this in sync. Heartbeat reads to know what playbooks I'm running.
current_strategies:
  active:
    - {name: arbiter-confluence, activated: 2026-05-08, regimes: [distribution_breakdown], perf: "trades=1, hit_rate=1.0, +0.025R"}
  trial: []
  observation:
    - {name: cvd-divergence-fade, activated: 2026-05-08, gate_to_trial: "20 resolved predictions", perf: "predictions=0"}
  retired: []

# PENDING PREDICTIONS MIRROR — source of truth: predictions table WHERE resolved_at IS NULL
pending_predictions:
  count: 0
  by_strategy: {}
  next_horizon: null

# ACTIVE THESES MIRROR (open positions only) — source: theses joined to open positions
active_hypotheses:
  - thesis_id: 59
    symbol: BTC
    side: short
    strategy_name: arbiter-confluence
    regime_tag: distribution_breakdown
    conviction: 0.72
    summary: "BTC Distribution Breakdown..."

# OPEN POSITIONS MIRROR — source: query_trades(status='open') + venue check
open_positions_summary:
  - position_id: 57
    symbol: BTC
    side: short
    size: 0.00013
    entry: 79468
    sl: 80300
    tp: 78000
    thesis_id: 59
    strategy_name: arbiter-confluence
    opened_at: "2026-05-08T02:25:16Z"

# PORTFOLIO MIRROR — source: latest hl_total_equity snapshot + hl_holdings
portfolio_summary:
  total_equity_usd: 25.01
  by_account:
    hl_trading: {usdc: 23.98, perp_account_value: 1.03}
  open_perp_unrealized_pnl_usd: 0.00026
  last_snapshot_at: 2026-05-08T02:25:00Z

operator_state:
  last_directive: "trade up to $25 on HL"
  capital_at_risk_usd: 25
  participate_in_dgclaw: true

# Bounded ~20 — older entries archived to learnings_archive.md
# RAW STREAM lives in the observations table; this is the synthesis-grade keepers
recent_learnings:
  - {date: 2026-05-08, text: "..."}
---
```

## Mirror discipline

The MIRROR sections (`current_strategies`, `pending_predictions`, `active_hypotheses`, `open_positions_summary`, `portfolio_summary`) are derived from sources of truth elsewhere. ALWAYS fetch the truth first, then write the mirror.

| Mirror | Source of truth |
|---|---|
| `current_strategies` | Files under `~/.plutus-agent/strategies/<stage>/` |
| `pending_predictions` | `predictions` table where `resolved_at IS NULL` (`query_predictions`) |
| `active_hypotheses` | `theses` table joined to open positions |
| `open_positions_summary` | `query_trades(status='open')` + venue check |
| `portfolio_summary` | latest `hl_total_equity` snapshot + `hl_holdings` |

If you ever spot drift (mirror disagrees with source), that's a discipline failure. Note in `recent_learnings`.

## Recent learnings — bounded, market-only

`recent_learnings` is the curated synthesis-grade list (~20 entries max). The raw observation stream lives in the `observations` table — query via `query_observations`. Don't dump everything into recent_learnings; keep it focused on **market observations**, not dev/build notes.

When `recent_learnings` grows past ~20 entries, archive older to `~/.plutus-agent/learnings_archive.md`.

## Pitfalls

- ❌ **Don't reload WORLDVIEW.md mid-session expecting changes to take effect.** Frozen-snapshot. Writes are for next session.
- ❌ **Don't skip the data_quality block.** Tagging signal reliability prevents over-indexing on weak signals.
- ❌ **Don't pollute recent_learnings with dev-meta** ("Phase A complete: 21 TA indicators..."). Market observations only.
- ❌ **Don't write narratives like a market commentary.** Be opinionated about implications.
- ❌ **Don't forget to update `current_strategies` after strategy-curator runs.** Prompt-injected summary needs to match the file system.
- ❌ **Don't try to update WORLDVIEW from heartbeat directly.** Routing is heartbeat's job; updating is worldview-discipline's job.
