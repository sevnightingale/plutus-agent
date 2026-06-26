# ARCHITECTURE.md — how Plutus works

The settled, tracked description of the running system. `TRADING.md` *governs*
execution (if anything here contradicts it, TRADING.md wins); this document
*explains* everything around it. Setup lives in `SETUP.md`.

## The thesis

Plutus is a six-agent trading desk where **trading is calibration-gated**:
nothing places a trade until a strategy has publicly earned the right to, on a
machine-verified prediction record. The desk's north star is not this
account's P&L — it is a credible, verifiable public track record (on-chain
history + legible rationale) that rents other people's capital via the Degen
Arena council. Patience is structural: a fresh desk runs predictions-only for
weeks, and that is the system working.

Three design laws shape everything below:

1. **One binary gate.** Strategy graduation — positive simulated net EXPECTANCY
   (the strategy's resolved book run through the actual trade geometry, at N≥15)
   — is the only yes/no on trading. Above the global conviction threshold (0.50),
   conviction is a *sizing dial* — risk-budget bands — never a veto.
2. **Honest absence.** Failed readings stay FAILED and are treated as missing;
   nothing is defaulted, mocked, or silently fallen back.
3. **Records live in lifecycle.db, via tools.** Predictions, decisions,
   trades, positions, outcomes — all structured rows written through
   validating dispatchers. The only markdown the desk maintains is the
   blackboards.

## The desk

Six agents in a **star topology**: plutus-main orchestrates; specialists
never spawn each other (no-nesting is enforced — the `spawn` toolset is
main-only). Execution is NOT an agent — it is a deterministic tool main calls
directly (see Execute). Each specialist is defined by an `agents/<name>/AGENT.md`
recipe:
frontmatter (model tier, toolsets, `reads:`, output contract) plus a procedure
the spawned agent follows.

| Agent | Role | Trigger | Tier |
|---|---|---|---|
| **plutus-main** | PM and operator voice — the persistent gateway session | wake queue, operator messages, eod | standard |
| **plutus-perception** | eyes — refreshes PERCEPTION.md from data points | spawned when stale or pre-decision | light |
| **plutus-regime** | classifies regime per timescale → REGIME.md | spawned on staleness / perception flips | light |
| **plutus-predict** | forward brain — evaluates strategies, registers predictions, generates new hypotheses | spawned on beats and escalations | standard |
| **plutus-ops** | back office + watchdog | cron, every 30 min | light |
| **plutus-reflect** | backward brain — weights, graduation, lessons, sizing review | staleness floor: weekly or 3 unreflected closes | standard |

**Model tiers.** Recipes declare `standard` or `light`, resolved at spawn
against the *user's* config: `standard` → `model.default`, `light` →
`model.light` (falls back to default). `desk_models: {<agent>: <model>}` in
config.yaml pins any agent explicitly. A fresh install therefore runs the
whole desk on whatever provider the wizard configured.

**plutus-main is not spawned** — it *is* the gateway session (Telegram/CLI).
Its toolsets come from `platform_toolsets` in config.yaml (the
`plutus-agent-cli` composite carries its desk surface: spawn, record,
lifecycle-read, strategy-write, cronjob, perception, **desk-execution**).
Execution is main's now — a deterministic tool (`desk_open_position` /
`desk_close_position`) it calls directly; the plutus-trade sub-agent is retired.
This is a deliberate doctrine change: main gained a *deterministic capability*,
not trading discretion (the expectancy gate, sizing, and naked-abort live in the
tool, in code).

## Blackboards

Three markdown files in `~/.plutus-agent/` are the desk's shared memory,
created at first boot (wizard or gateway) and never overwritten:

- **PLUTUS.md** — the identity file, injected into every system prompt. Three
  zones, individually addressable by recipes (`reads: PLUTUS.md#doctrine`):
  - `## Doctrine` — operator-owned constitution: north star, hard
    constraints, the money model, cold-start rules, the desk roster, lineage.
    All six specialists read it; only main reads the whole file.
  - `## Live State` — tool-rewritten only: `equity_usd` snapshot, open
    position, strategy counts.
  - `## Lessons` — curated by reflect, hard-capped at 12; replace the
    weakest, never append past the cap.
- **REGIME.md** — a per-timescale table (intraday / swing / position ×
  direction / volatility, macro at position scale only) with a closed label
  vocabulary, maintained by plutus-regime. A strategy's
  `regime_applicability` refers to the regime *at its own timescale*.
- **PERCEPTION.md** — a readings table (data point, params, value,
  fetched_at) plus a narrative panel, maintained by plutus-perception. FAILED
  rows stay FAILED.

Staleness floors (doctrine): perception 4h · regime 8h · predict 8h · reflect
weekly or 3 unreflected closes · generation 7d. Ops enforces the floors.

## The loop

```
            every 30 min                    on wake
┌─────────┐  resolve/evaluate  ┌──────────┐  spawns   ┌────────────┐
│ plutus- │ ────────────────►  │   wake   │ ───────►  │ plutus-main│
│   ops   │  staleness/path    │  queue   │           │ (gateway)  │
└─────────┘  → enqueue_wake    └──────────┘           └─────┬──────┘
     ▲                              ▲                       │
     │ cron */30                    │ watchers, operator    ▼
     │                              │            perception → regime → predict
   23:55 plutus-eod: journal close; session rolls lazily on the next event
```

- **plutus-ops** ticks every 30 minutes on the cheapest model: a safety-net
  resolution sweep (the live watcher resolves price-zone predictions
  event-driven; ops catches anything it missed), a conviction re-score of the
  open predictions due per their timescale (the trajectory reflect mines),
  evaluates the open position against its thesis, checks staleness floors, and
  fetches `hl_trade_readiness`. Anything needing judgment becomes an
  `enqueue_wake(reason=…)` — ops never interprets, trades, or messages.
- **plutus-main** has *no standing cron*. It wakes when the queue has
  something (ops staleness/escalations, watcher alerts, your messages),
  orchestrates the relevant specialists, makes funding decisions, and may
  self-schedule one-off crons for concrete dated reasons.
- **plutus-eod** (23:55) closes the day's journal via `record(kind=eod)`;
  the gateway's session-reset policy starts the new day's session lazily on
  the first event after the boundary.

## The strategy lifecycle (idea → trade → learning)

1. **Generate.** plutus-predict keeps the hypothesis pool full. Every
   hypothesis is **file-at-birth**: `strategy_upsert` writes
   `~/.plutus-agent/strategies/<name>.md` (status=test) and syncs the
   lifecycle.db mirror atomically — frontmatter declares timescale, mechanism
   family (momentum / mean_reversion / flow / event / narrative),
   regime_applicability, and weighted `data_points`; the body states
   Hypothesis, Mechanism (*who is on the other side*), Trigger, Invalidation.
   Unregistered data-point names go in `missing_data_points` — the
   **self-extension hook**: sourcing that data point becomes a perception
   task. Operators can seed hypotheses through the same tool on the same
   terms (a good first conversation with Plutus).
2. **Predict.** predict runs on the heavy model as an ORCHESTRATOR: it spends
   its reasoning on STRATEGY GENERATION (filling gaps in the regime × timescale
   matrix) and offloads per-strategy work to cheap scoped tools. For each
   regime-matched strategy below its open cap, `predict_draft` (light model)
   proposes a **price zone** — a signed % move from the current price with a
   near edge (correctness floor) and a far edge (target), plus a horizon ≤ 720h
   — and `conviction_score` (light model, self-fetching the strategy's declared
   data points) returns the conviction + per-DP support scores, aggregated
   deterministically by the engine. `register_prediction` captures the entry
   price server-side, validates the zone, and accepts an optional
   machine-resolvable **invalidation** (resolvable-data-point leaves — the
   thesis breaking, never a price wiggle). Price alone defines correct; data
   points live in conviction and invalidation, never in success. The limiting
   factor is the per-(timescale × regime) strategy population (≈ 2 active +
   6 test per cell), not a slot budget — prediction volume is cheap. predict
   authors only on FRESH perception: `perception_freshness` gates each strategy
   and `register_prediction` refuses stale data, so a stale beat returns
   `perception_stale` and main refreshes perception before predict retries.
3. **Resolve.** Resolution is FLOOR-CORRECT and CONTINUOUS: touching the near
   edge LOCKS the win but the prediction stays OPEN; touching the far edge
   resolves it CORRECT early (the live watcher detects touches within seconds);
   if only near is reached, the horizon backstops a CORRECT resolution; never
   reaching near by the horizon is WRONG; invalidation trips WRONG only BEFORE
   near. The ops sweep is a race-safe safety net. On resolution the price path
   over [birth, resolution] is measured (MAE / MFE / profit-score →
   `realized_value_json`); MAE sets stops and the resolved book's simulated
   expectancy is the graduation gate (see Graduate, Execute).
4. **Graduate.** plutus-reflect promotes test → **active** only when the
   strategy's **simulated net EXPECTANCY is positive at N≥15** — its whole
   resolved book run through the actual trade geometry (TP = far edge, SL = the
   all-resolutions MAE stop), pessimistic on path-dependence, with the win signal
   = the trade actually TAGGED its target (`strategy_expectancy.tradeable`). This
   replaces the old win-rate + RR>1 bar, which was survivorship-biased (median
   MFE/MAE on winners only overstates tradeability). Revoke when expectancy turns
   negative at N≥20. No manual graduation, no hand-seeded actives — the bar is
   the bar.
5. **Fund & size.** Selection is a deterministic query
   (`best_actionable_prediction` = the argmax-EV open prediction of a
   currently-tradeable active strategy). main funds it by calling
   `desk_open_position` directly (mechanical — flat · trade-ready · not-HALT).
   Sizing is RISK-BASED: conviction sets a risk BUDGET (% of equity risked if the
   stop hits — 0.50–0.60 → 1% · 0.60–0.70 → 3% · 0.70–0.80 → 7% · 0.80–1.00 →
   12%), and size = budget × equity ÷ stop-distance, capped at 10X leverage — so
   a wider stop auto-shrinks the position and risk-per-trade is constant per band.
6. **Execute.** `desk_open_position` is deterministic code, not an agent: it
   derives the hard SL from the strategy's empirical risk envelope
   (`mae_envelope` all-resolutions MAE percentile; ATR fallback while thin), the
   target from the prediction's far edge (a fixed zone level), and the size from
   the risk budget; it applies the per-setup expectancy gate (RR > (1−p)/p at the
   live price — refusing negative-EV setups), then places via
   `place_order(venue="hyperliquid")` (native SDK, API-wallet signed) with an
   atomic on-venue SL bracket and a ±0.3% slippage cap, and **post-entry verifies
   on-venue — a naked position (SL not resting) is auto-closed immediately** (the
   one money-critical guard). The fill is *measured*: realized leverage and
   `entry_account_value` are recorded on the position row.
6a. **Manage (the 4-target structure).** Every position carries two mechanical
   bounds (hard SL, far TP — they rest on-venue and fire without anyone) and two
   *alert* triggers inside them: **alert-up** at the near edge (the `hl_position_alert`
   watcher wakes main: take profit, or hold for far?) and **alert-down** at the
   winners'-MAE level (normal wobble, or thesis breaking — cut early?). On a wake,
   main calls `rescore_position` (re-scores conviction on fresh data, biased to
   ACT on a weakened premise) and closes via `desk_close_position` if warranted.
   Mechanism for the bounds + the alert firing; judgment only at the two ambiguous
   edges. Invalidation ≠ stop-loss — exits are recorded as distinct `exit_reason`s.
7. **Reflect.** Weekly (or after 3 unreflected closes): updates data-point
   weights from outcomes and the conviction trajectory, runs
   graduation/revocation checkpoints, prunes over-full (timescale × regime)
   cells so each niche stays a real champion/challenger contest, reviews sizing
   and the stop envelope via `sizing_performance` (proposes band/percentile
   retunes, operator decides), curates the 12-lesson cap, and seeds
   next-generation hypotheses.

One position at a time (cross-margin law). Invalidation ≠ stop-loss: thesis
breaks and risk exits are recorded as different exits.

## Money model (summary — TRADING.md governs)

Two wallets: the **ACP agent wallet** (`ACP_AGENT_WALLET`) is the master —
the Virtuals agent's managed wallet, holds all funds, key never on the
machine. The **API wallet** signs trades, holds $0 forever, and must stay
`approveAgent`-registered on-chain — unregistered/expired means every trade
fails *silently* (the #1 failure mode; ops watches `hl_trade_readiness` every
tick). One unified Hyperliquid balance, cross margin: spot USDC
collateralizes positions automatically; flat perp accountValue ≈ 0 is
display, not absence; never transfer spot→perp. **Equity ≠ readiness.** The
money measures (`equity_usd` = spot + perp accountValue, etc.) are defined
once in TRADING.md's glossary and computed once in code
(`equity_breakdown`).

## Plumbing

- **Registries** (in `trading/perception/core/`): data points (52+ —
  `hl_*`, `ta_*`, `macro_*`, defillama, coingecko, dgclaw…), venues,
  accounts, alerts, identity. Integrations under
  `trading/integrations/<source>/` contribute entries via decorators;
  capability scales by registry depth, not tool count.
- **Dispatchers** (`trading/dispatchers/`): the agent-facing tools —
  `fetch_data_point` (auto-snapshots every read), `account_state`,
  `register_prediction`, `strategy_upsert`, `desk_open/close_position`,
  `lifecycle_query`, `record`, `spawn_desk_agent`, `enqueue_wake`,
  `resolve_due_predictions`. Validation lives in the writers; agents that
  fight a refusal are wrong by definition.
- **Toolsets** compose by name (`harness/toolsets.py`); recipes request
  function-shaped sets (perception, prediction-write, desk-execution,
  resolution…). `record()` fans out: lifecycle.db + journal always; forum
  posts per-target with logged per-target failures.
- **Watchers** (`plutus-watchers` pm2 process): polls registered alerts
  (position changes, total-equity changes, price ranges) into the wake
  queue.

## Runtime anatomy (`~/.plutus-agent/`)

| Path | What |
|---|---|
| `PLUTUS.md` / `REGIME.md` / `PERCEPTION.md` | blackboards |
| `strategies/*.md` | strategy files (truth; db mirror synced) |
| `lifecycle.db` | predictions, decisions, trades, positions, outcomes, snapshots |
| `sessions/*.jsonl` | full message history per session (incl. Telegram) |
| `ledger/<date>/*.md` | one transcript per spawned desk agent |
| `logs/agent.log` | structured runtime log (API calls, tools, wakes) |
| `memories/` | MEMORY.md / USER.md (operator + agent memory) |
| `.env`, `config.yaml`, `auth.json`, `cron/` | credentials & config |

Debug order: `logs/agent.log` for *what ran*, `ledger/` for *what an agent
thought*, `lifecycle.db` for *what was recorded*, blackboards for *what the
desk believes*.

## Operator surface

- `plutus setup` — first-time wizard (provider → messaging → watchlist →
  wallets → optional integrations → first boot); `plutus setup trading`
  re-runs the desk pieces; `plutus setup-status` is the one-screen dashboard.
- `scripts/check_trade_readiness.py` — READY/NOT READY with the exact reason
  (same verdict the desk reads as `hl_trade_readiness`).
- `touch ~/.plutus-agent/HALT` pauses execution; `rm` resumes.
- Env vocabulary is `PLUTUS_*` (bridged internally); fleet is
  `pm2 start ecosystem.config.js` → `plutus-gateway` + `plutus-watchers`.
- The operator sets doctrine (PLUTUS.md Doctrine zone is yours), funds the
  account, and answers escalations — the desk does the rest.
