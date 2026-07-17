# ARCHITECTURE.md — how Plutus works

The settled, tracked description of the running system. `TRADING.md` *governs*
execution (if anything here contradicts it, TRADING.md wins); this document
*explains* everything around it. Setup lives in `SETUP.md`.

## The thesis

Plutus is a seven-agent trading desk where **trading is calibration-gated**:
nothing places a trade until a strategy has publicly earned the right to, on a
machine-verified prediction record. The desk's north star is not this
account's P&L — it is a credible, verifiable public track record (on-chain
history + legible rationale) that rents other people's capital via the Degen
Arena council. Patience is structural: a fresh desk runs predictions-only for
weeks, and that is the system working.

Three design laws shape everything below:

1. **One binary gate.** Strategy graduation — simulated net EXPECTANCY
   (the strategy's resolved book run through the actual trade geometry, at N≥15)
   clearing a multiplicity-deflated hurdle, and not currently decaying —
   is the only yes/no on trading. Above the global conviction threshold (0.50),
   conviction is a *sizing dial* — risk-budget bands — never a veto.
2. **Honest absence.** Failed readings stay FAILED and are treated as missing;
   nothing is defaulted, mocked, or silently fallen back.
3. **Records live in lifecycle.db, via tools.** Predictions, decisions,
   trades, positions, outcomes — all structured rows written through
   validating dispatchers. The only markdown the desk maintains is the
   blackboards.

## The desk

Seven agents in a **star topology**: plutus-main orchestrates; specialists
never spawn each other (no-nesting is enforced — the `spawn` toolset is
main-only). Execution is NOT an agent — it is a deterministic tool main calls
directly (see Execute). Each specialist is defined by an `agents/<name>/AGENT.md`
recipe:
frontmatter (model tier, toolsets, `reads:`, output contract) plus a procedure
the spawned agent follows. The output contract is fulfilled through a
`submit_report` tool the spawn runner injects into every contracted run —
the payload validates against `RETURN_CONTRACTS` (harness/spawn.py) at the
tool layer, so a malformed report bounces back for the model to retry, and
the agent's final text message stays human-readable prose. (Final-message
JSON still parses as a fallback.)

| Agent | Role | Trigger | Tier |
|---|---|---|---|
| **plutus-main** | PM and operator voice — the persistent gateway session | wake queue, operator messages, eod | standard |
| **plutus-perception** | eyes — refreshes PERCEPTION.md from data points | spawned when stale or pre-decision | light |
| **plutus-regime** | classifies regime per timescale → REGIME.md | spawned on staleness / perception flips | light |
| **plutus-predict** | forward brain — evaluates the live book, registers predictions | spawned on beats and escalations | standard |
| **plutus-generate** | research brain — authors strategies, surveys the evidence space | generation floor (7d) + gap reports | standard |
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
weekly or 3 unreflected closes · generation 7d (plutus-generate). Ops
enforces the floors.

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

1. **Generate.** plutus-generate — the research brain, the desk's ONLY
   strategy author (split from predict 2026-07-17: authored-in-the-margins
   generation had produced a TA monoculture and never used the
   self-extension hook) — keeps the hypothesis pool full AND the evidence
   base diverse: each session surveys the full data-point registry against
   the live book, prefers hypotheses over under-used signal sources, and
   declares `missing_data_points` when the data a mechanism needs doesn't
   exist yet. Every
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
2. **Predict.** predict is the REGISTRATION engine — an ORCHESTRATOR over
   cheap scoped tools; it reports population gaps for main to route to
   generate but never authors strategies itself. For each
   regime-matched strategy below its open cap (3; 5 for an *incubating* book —
   net-positive above costs but not yet clearing the deflated hurdle — so
   promising strategies build evidence faster), `predict_draft` (light model)
   proposes a **price zone** — a signed % move from the current price with a
   near edge (correctness floor) and a far edge (target), plus a horizon ≤ 720h
   — and `conviction_score` (self-fetching the strategy's declared
   data points) returns the conviction + per-DP support scores, aggregated
   deterministically by the engine. A data point declaring a structured
   `normalizer` ({name, params} from `trading/conviction/normalizers.py`) is
   scored DETERMINISTICALLY from its fresh numeric reading — the light-model
   analyst scores only the normalizer-less (narrative/contextual) DPs, so
   numerical evidence is reproducible, halo-free, and costs no inference. `register_prediction` captures the entry
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
4. **Graduate.** A strategy is promoted test → **active** the moment its
   **simulated net EXPECTANCY clears the hurdle at N≥15** — its whole resolved
   book run through the actual trade geometry (TP = the book's `best_target`
   edge, SL = the all-resolutions MAE stop), pessimistic on path-dependence,
   with the win signal = the trade actually TAGGED its target
   (`strategy_expectancy.tradeable`). The test↔active flip is **code-owned**: a
   deterministic status sync runs after every resolution batch (and on demand
   via `strategy_status_sync`) — plutus-reflect verifies and narrates the
   moves, and owns the judgment moves the sync never makes (dormancy,
   retirement, population pruning). This replaces the old win-rate + RR>1 bar,
   which was survivorship-biased (median MFE/MAE on winners only overstates
   tradeability). Two hardenings (imported
   from the trading design notes): the hurdle is **multiplicity-deflated** — cost margin
   + √(2·ln M)·σ/√n over the M **serious** sibling trials ever tried at the
   timescale (books of ≥6 resolutions; retired siblings still count, but a
   one-resolution noise book never raises the bar), so the survivor of thirty
   real trials needs more
   proof than a lone hypothesis. The premium shrinks with the strategy's own
   √n, so a real edge above cost always converges —
   `strategy_expectancy.n_to_clear` projects the book size where the current
   edge clears (None = at/below cost, never) — and a **hazard check** re-simulates the
   trailing 10 resolutions: a full negative window (`decaying`) blocks
   `tradeable` immediately, so a dead edge cannot coast on its historical wins
   (the record itself is never rewritten). Revoke when the edge is gone at N≥20
   (expectancy ≤ 0 — reflect's call; decaying → demote to test, never retire); a book still positive but
   under the deflated hurdle is auto-demoted to test to keep earning n. No
   manual graduation, no hand-seeded actives — the bar is the bar.
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
   target from the edge the strategy **graduated on** (`best_target` — near or
   far, a fixed zone level; the gate's geometry and the placed bracket always
   match), and the size from
   the risk budget; it enforces every mechanical guard in-tool (HALT ·
   one-position · staleness · ACTIVE status · trade-path readiness) and applies
   the per-setup expectancy gate (RR > (1−p)/p at the
   live price, p = wins/n including scratches — refusing negative-EV setups),
   then places via
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
   weights from outcomes and the conviction trajectory (via
   `strategy_update_weights`, which resolves keys against the strategy's
   declared data points and refuses unresolvable ones loudly — bare-name
   updates used to be silent no-ops), runs
   graduation/revocation checkpoints, prunes over-full (timescale × regime)
   cells so each niche stays a real champion/challenger contest, reviews sizing
   and the stop envelope via `sizing_performance` (proposes band/percentile
   retunes, operator decides), curates the 12-lesson cap, and seeds
   next-generation hypotheses. Every pass also runs **`conviction_fit`**
   (`trading/calibration/`) — the REPORT-ONLY ML harness: a purged
   walk-forward fit of P(correct) over the whole resolved record
   (chronological folds; a training row must have RESOLVED before its fold
   opens) scored against honest baselines (base rate · stored conviction ·
   isotonic-recalibrated conviction), writing a versioned plain-JSON
   artifact to `models/conviction/` and self-reporting its trend vs the
   previous run. Live scoring consumes nothing from it yet; the run where
   its edge over the recalibrated baseline turns significant, reflect
   escalates a wire-in proposal (calibrated conviction → sizing bands —
   operator decision, like band retunes).

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

- **Registries** (in `trading/perception/core/`): data points (59+ —
  `hl_*` incl. book imbalance + funding z-score, `ta_*`, `macro_*`,
  `poly_*` (Polymarket odds), `session_context`, defillama, coingecko,
  dgclaw…), venues,
  accounts, alerts, identity. Integrations under
  `trading/integrations/<source>/` contribute entries via decorators;
  capability scales by registry depth, not tool count.
- **Dispatchers** (`trading/dispatchers/`): the agent-facing tools —
  `fetch_data_point` (auto-snapshots every read), `account_state`,
  `register_prediction`, `strategy_upsert`, `desk_open/close_position`,
  `lifecycle_query`, `record`, `spawn_desk_agent`, `enqueue_wake`,
  `resolve_due_predictions`, `conviction_fit`. Validation lives in the
  writers; agents that fight a refusal are wrong by definition.
  Calibration integrity is code-owned at the write path (2026-07-16):
  support-score keys are canonicalized against the strategy's DECLARED
  data points, declared weights are pinned server-side, and the stored
  conviction is the engine's recomputed aggregate — never the agent's
  transcription (free-form keys had fragmented the record; 13% of stored
  convictions had drifted; lifecycle.db v5 canonicalized the history).
- **Toolsets** compose by name (`harness/toolsets.py`); recipes request
  function-shaped sets (perception, prediction-write, desk-execution,
  resolution…). `record()` fans out: lifecycle.db + journal always; forum
  posts per-target with logged per-target failures.
- **Watchers** (`plutus-watchers` pm2 process): polls registered alerts
  (position changes, total-equity changes, price ranges) into the wake
  queue — and runs the prediction resolver every ~5s (it writes
  lifecycle.db; it is a second resident interpreter, not just a poller).
- **Stale-code law**: the gateway and the watchers import `harness/` +
  `trading/` once at boot and cache them for life — a repo patch is NOT
  live until both restart (2026-07-03: five fills aborted on a stale
  `venue.py` while the verified fix sat on disk). The sanctioned reload is
  the `request_desk_restart` tool: queues a resume wake, recycles the
  watchers, drain-restarts the gateway (pm2 revives both).
- **Memory safety**: every memory mutation (including the pre-compression
  flush, whose artifacts never appear in transcripts) is appended to
  `memories/audit.jsonl` with its source; entries that would require
  operator approval for execution are rejected at write time (the
  2026-07-03 flush-poisoning incident).

## Runtime anatomy (`~/.plutus-agent/`)

| Path | What |
|---|---|
| `PLUTUS.md` / `REGIME.md` / `PERCEPTION.md` | blackboards |
| `strategies/*.md` | strategy files (truth; db mirror synced) |
| `lifecycle.db` | predictions, decisions, trades, positions, outcomes, snapshots |
| `models/conviction/` | versioned calibration artifacts (plain JSON; report-only until wired) |
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
