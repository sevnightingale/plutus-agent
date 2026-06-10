# PLUTUS.md — V2

> The vision for the agent.
>
> `AGENTS.md` orients developers (and AI coding assistants) working on this repo.
> `LINEAGE.md` records where this project came from.
> **`TRADING.md` is the canonical source of truth for the trade-EXECUTION mechanics** — wallets, on-chain agent registration, the native `place_order` path, unified margin, the silent-failure mode, and the readiness check. This file (PLUTUS.md) covers the agent's mind; `TRADING.md` covers how orders actually reach Hyperliquid. **If you're diagnosing "why isn't Plutus trading," start with `TRADING.md`, not here.**
>
> **`PLUTUS.md` (this file) is the complete picture of what we're building** — the agent's mind, its lifecycle, how it perceives and acts and learns. The north star. When in doubt about how a piece fits together, this is the document that says how.
>
> **V2 status (2026-05-20):** This is the V2 rewrite. V1 covered the single-heartbeat operating model that ran from Phase 4 (2026-04-25) through 2026-05-19. V2 splits execution across three tiers (plutus-main / plutus-ops / plutus-thesis), introduces the perception cache, separates strategy and thesis conviction with multiplier-based position sizing, and centers the prediction factory as the discovery loop. The cognitive model (four strata, six registries, lifecycle schema, embeddings, memory layers) is preserved unchanged — V2 is an operating-model evolution, not a re-architecture of the mind.

---

## What Plutus is

**Plutus** (Greek god of wealth) is the operator's name for THIS deployment of plutus-agent. The repo and harness are named `plutus-agent`; the running agent the operator interacts with is named Plutus.

Plutus is an autonomous trading agent that:
- Lives on the plutus-agent harness (a lean fork of NousResearch's Hermes Agent)
- Trades live on Hyperliquid (real money, $25 starting risk capital)
- Decides for itself which markets, what strategies, when to act
- Self-modifies its own skills, memory, and worldview based on observed outcomes
- Has full filesystem write to `~/plutus-agent/` (codebase) and `~/.plutus-agent/` (state); extra read-only roots can be granted via `HERMES_READ_SAFE_ROOT`
- Communicates with the operator via Telegram (BotFather bot also named Plutus) and CLI
- Shares a chat called **Nightingale Manor** with Sebastian (the operator's personal AI assistant) — Sebastian provides market context and accountability check-ins

Plutus is **agent-first**. It is not a configurable rule engine that the operator preloads with strategies. It reads markets, reasons, and acts. Configuration is a thing the agent does for itself via skills, memory, and self-modification — not a thing the operator sets up in advance.

---

## plutus-agent (the harness) vs Plutus (the instance)

This document describes both, but the boundary matters:

- **`plutus-agent`** (this repo, eventually OSS) is a **trading-agent harness** — infrastructure, registries, dispatchers, lifecycle schema, default skill skeletons, and the Hyperliquid integration as the initial supported source. Additional venues, data sources, identity systems, and competitions arrive as integrations contributed against stable registry interfaces.
- **Plutus** (the operator's instance, `~/.plutus-agent/`) is **one specific instantiation** — one identity (SOUL.md), one current world model (WORLDVIEW.md), one perception cache (perception_state.json), one trade history (lifecycle.db), one cumulative memory (memories/), one set of self-evolved skills (skills/), one cron schedule, one operator, one peer-AI relationship (Sebastian). Private to the operator's machine.

The repo is the *template*. Plutus is the *deployment*. Setup wizard bridges them: idempotent, never overwrites operator-specific content.

---

## Core principles

These guide every architectural decision. If something violates one of these, we got it wrong.

1. **Agent-first, not platform-first.** Plutus is one agent serving one operator. Architecture choices that make sense for a multi-tenant platform (rigid schemas, ceremony, preemptive validation) often make NO sense for a single agent. We constantly resist platform-thinking.
2. **State is Plutus's perception, not reality.** Hyperliquid is reality. Plutus's lifecycle records are Plutus's *interpreted memory* of HL. They diverge when Plutus isn't looking. That's not a bug — it's the epistemic model.
3. **No background writes to interpretive state.** Tools, fired by Plutus's own actions, are the only writers to lifecycle.db, WORLDVIEW.md, and strategy files. Watcher daemons can *trigger* Plutus to wake up, but they can't *update* interpretive state on Plutus's behalf. (Perception-cache writes by plutus-ops/plutus-thesis are mechanical and allowed — see Principle 4 below.)
4. **Perception cache with per-DP staleness budgets.** (Revised in V2.) Every `fetch_data_point` call hits the source when the cached value is stale. `~/.plutus-agent/perception_state.json` holds the most recent reading of every data point with a `staleness_s` budget per DP type. plutus-main, plutus-ops, and plutus-thesis all populate the cache opportunistically based on what they're fetching for their own purposes. The cache is a fail-safe optimization — staleness budgets per DP ensure freshness where freshness matters (price=60s) and accept staleness where it doesn't (macro=4h). V1's "no caching at fetch layer" was a doctrine that turned out to be wrong: at a few beats/day for plutus-main, refetching every passive watchlist asset every beat is wasteful. (Macro lives in this cache too — the plutus-perception sub-agent resolves VIX/DXY/etc. each beat and writes them here; there is no separate macro-cache cron as of 2026-06-01.)
5. **Single source of truth per concept.** Data points are defined ONCE in the registry; everything that uses them reads from one in-memory table. Same for events, queries, venues, alerts, skills, **strategies** (files in `~/.plutus-agent/strategies/`), **predictions** + **observations** (lifecycle tables).
6. **Schema rigid, content loose.** Lifecycle tables have strict shapes (FKs, types). The CONTENT of a thesis or reflection is free-form markdown. Structured enough for ML, flexible enough for agency.
7. **Explicit tool calls write interpretive records.** No silent state mutations on lifecycle.db / WORLDVIEW.md / strategy files. Every row has a Plutus tool call as its proximate cause.
8. **Discipline via skills, not enforcement via code.** When we want Plutus to behave a certain way (record theses, update worldview, follow a checklist), we write a skill and trust the agent to follow it. We don't hardcode the behavior into the harness. The few exceptions (e.g., `place_order` refuses theses without invalidation criteria) are flagged explicitly.
9. **Registries + dispatchers as the scaling backbone.** When a capability scales (data sources, venues, event types, alerts), it lives behind a registry with a single agent-facing dispatcher tool. Tool surface stays small (~20 dispatchers) while capability grows arbitrarily. Adding a thing = one entry in one registry.
10. **Calibration is the master metric.** Conviction without calibration is superstition. Every conviction Plutus stakes — on a thesis or a prediction — must be measurable against actual outcomes. The calibration curve drives self-improvement; without it, "self-improvement" is just churn.
11. **Predictions ≠ trades ≠ observations.** Three distinct epistemic layers. Predictions are pre-registered falsifiable claims with no capital (cheap, high-volume calibration). Trades carry capital and require strategy + regime + invalidation tags. Observations are passive journal entries that compound into expertise. Conflating them destroys their respective signals.
12. **Plutus drives, operator contributes, Sebastian peers.** Strategy authoring, promotion, demotion, retirement, regime calls, trade execution — all autonomous. Operator gates NOTHING. Operator shares insights via observations + dialogue; Sebastian shares market context + accountability via the Manor chat; Plutus decides whether to act on either.
13. **The prediction factory IS the discovery loop.** (NEW in V2.) Predictions are not just a calibration substrate for existing strategies — they are how the system *discovers* new edges. Every plutus-main beat generates predictions across existing strategies, experimental combinations, and regime stress tests. Strategy graduation flows from prediction calibration, not from operator design or Plutus introspection. Edges are discovered by the data, not invented by the agent.
14. **Three tiers, three responsibilities, hard contract.** (NEW in V2.) plutus-main does heavy reasoning and orchestration. plutus-ops does bookkeeping and mechanical updates. plutus-thesis monitors invalidation criteria on open positions. Each tier has a hard contract on what it may write and what perception it may fetch. The contracts are doctrine, not code — but they're load-bearing doctrine.

---

## What makes Plutus structurally different from a human trader

These are the AI-specific edges. Every architectural decision should preserve them; if something undermines them, it's the wrong design.

- **Process consistency at scale.** The discipline humans burn out maintaining — pre-trade thesis every time, defined invalidation every time, post-trade reflection every time, weekly calibration — is Plutus's *baseline*.
- **Wide perception, narrow trade.** Plutus perceives the entire watchlist's state continuously (perception_state cache) and only deploys capital on its highest-conviction opportunities.
- **Compounding observation.** ~7,000+ ops cycles per year + ~1,500 main beats, each tagged with regime/indicators/narrative. Pattern library accumulates in a way no human journal can match.
- **Calibration as a primitive.** Conviction is measurable, not asserted. Predict → check → adjust runs continuously across both existing strategies AND experimental combinations.
- **Cross-domain synthesis in working memory.** Macro + on-chain + technical + narrative + cross-asset (crypto + equities via HL perps) held simultaneously without context-switch loss.
- **Counterfactual tracking.** Trades almost-taken-and-not are journaled with the same rigor as trades taken. The highest-information learning signal humans almost never capture.
- **No-cost patience.** Wait weeks for a setup → human gets bored, anxious, distracted. Plutus literally doesn't care.
- **Peer-AI context.** Sebastian (operator's personal AI) shares market observations and accountability checks via the Manor chat — a second observer feeding context that Plutus alone wouldn't surface.

NOT edge in: speed (HFT crushes us at the second scale), adversarial intuition, knowing when to throw out the playbook in novel regimes. Don't compete in those domains.

The closest human archetype is **a patient discretionary swing trader with an obsessive journaling habit and a research analyst's range** — except Plutus is that, all the time, on every market it watches.

---

## The three-tier execution architecture

V2's defining structural change. Single-cron hourly heartbeat (V1) is replaced by three coordinated session types, each with a different model, cadence, and isolation profile. This section is the doctrinal summary; the implementation lives in `skills/trading/plutus-{main,ops,perception}/SKILL.md` and `cron/scheduler.py`.

### plutus-main — heavy reasoning + orchestration

| | |
|---|---|
| Model | `kimi-k2.6` (256K context, OpenCode Go) |
| Cadence | **3×/day at 00:00, 08:00, 16:00 UTC** (reduced from 4×/day on 2026-06-01 to fit the $10/mo kimi budget) |
| Session | **Unified** with operator's Telegram chat (via `gateway.deliver_synthetic_message` synthetic injection — same path as V1's heartbeat) |
| Authority | Full. Trade execution, thesis authoring, strategy promotion/demotion/retirement, regime detection, prediction registration, cron orchestration, WORLDVIEW.md writes, strategy file edits, weight updates. |
| Perception scope | **Wide.** Reads the entire perception_state cache (filled by the plutus-perception sub-agent it spawns at Phase 0); does at-decision spot-refresh per trade candidate in Phase 4; on Sunday beat, broad scan for setup discovery. |
| Tool-call budget | ~40-55/beat (wide perception delegated to the spawned sub-agent). |

**plutus-main is the ONLY kimi consumer** (besides operator chat). Everything else — perception, ops, thesis monitoring — runs on `deepseek-v4-flash`. This is the cost design that lets the agent run a full month on OpenCode Go's $10/mo plan; see Cost model below.

plutus-main is the only tier that *thinks*. It's where regime is detected, where theses are formed, where capital is committed, where calibration is interpreted, where strategies are promoted or retired, where the prediction factory generates new hypotheses. It is the orchestrator: plutus-ops's existence and behavior are determined by what plutus-main writes (cron config, active-thesis-monitors.json, strategy files). plutus-thesis cron jobs are spawned by plutus-main when high-cadence monitoring is needed.

### plutus-ops — bookkeeping deputy

| | |
|---|---|
| Model | `deepseek-v4-flash` (cheap, 1M context, deterministic) |
| Cadence | Every 30 minutes |
| Session | **Isolated**, fresh per tick. Reads WORLDVIEW.md + lifecycle.db from disk; no carried context. |
| Authority | Read state, log, mechanical updates only. Resolves due predictions. Records position_evaluations. Refreshes perception cache for active positions + due predictions. Writes one ops_summary observation per tick. Detects escalation conditions. |
| Forbidden | `place_order`, `close_position`, `modify_order`, `place_trigger`, `record_event(type="thesis"/"decision")`, regime-detection, strategy-curator, calibration-review, strategy-author, loss-postmortem, weekly-review, `send_message` to operator, WORLDVIEW.md writes, strategy file edits, `conviction-engine.update_weights`. |
| Perception scope | **Narrow-shared.** Only data points needed for prediction resolution, position evaluation, equity snapshot, thesis monitor sweep. **No watchlist scanning** — that's plutus-main territory. |
| Tool-call budget | ~5-8/tick typical (most ticks are quiet bookkeeping). |

plutus-ops is the patient deputy. Most ticks are nothing-notable — it writes an ops_summary observation with all counts zero and all flags false. That zero-state IS signal: brain reading "10 quiet ops summaries in a row" knows the system is steady. When something interesting happens (prediction comes due, position closes, drift detected, equity moves >5%, near-liquidation), ops records it precisely without interpretation and flags it for brain to handle on the next main beat.

The patience-is-structural doctrine that lived in the V1 heartbeat skill ("most ticks the right answer is nothing") migrates here. plutus-main is no longer where stillness lives — plutus-ops is.

### plutus-thesis — per-thesis specialist

| | |
|---|---|
| Model | `deepseek-v4-flash` |
| Cadence | Variable. Default: 30-min ops sweep evaluates all entries in `~/active-thesis-monitors.json`. When a thesis needs higher cadence (breakout in progress, post-event window), plutus-main spawns a dedicated cron at 15-min cadence for a bounded number of runs. |
| Session | **Isolated.** Tiniest context. Knows only the thesis assigned to it. |
| Authority | Evaluate invalidation rules. Record position_evaluations. Recommend exits via `position_evaluations.recommended_action='exit'`. |
| Forbidden | Trade, override main's decisions, expand perception scope beyond declared `data_points_to_watch`. |
| Perception scope | **Narrow-targeted.** Only the data points declared in the thesis's monitor entry. |

Two flavors of plutus-thesis evaluation, same contract:

- **Flavor A (default):** plutus-ops handles all theses in `active-thesis-monitors.json` during its 30-min sweep. No separate cron. Sufficient for most theses.
- **Flavor B (high-cadence):** plutus-main spawns a dedicated `thesis-N-monitor` cron at 15-min cadence with `repeat=N` for a bounded window. Self-expires when count reached or thesis closed. Used when monitoring intensity warrants its own cadence.

Both flavors only **recommend** exits via the position_evaluation's `recommended_action` field. plutus-main is the only tier that closes positions. This is intentional — keeps the line between "the system noticed an invalidation" and "the system decided to act on it" clean and auditable.

### The hard contract on perception scope

| Tier | Scope |
|---|---|
| plutus-main | Full perception_state read. Refreshes stale entries for active/trial/experimental hypotheses + monitored positions + cross-asset macro. Sunday beat adds breadth scan. |
| plutus-ops | Refreshes only data points needed for: prediction resolution (per-prediction `snapshot_ids_json` shape), position evaluation (per-position thesis data points), equity snapshot. **No watchlist scanning.** |
| plutus-thesis | Refreshes only the `data_points_to_watch` declared in the monitor entry for its assigned thesis. |

If plutus-ops drifts into "let me also scan the watchlist while I'm here," the contract is broken and we've recreated V1's hourly heartbeat cost profile. This is enforceable in skill prompts but not in code; it's doctrine. The forbidden lists above are how the doctrine is encoded.

### Cost model (revised 2026-06-01 — fit a full month on $10/mo)

OpenCode Go monthly limits: kimi-k2.6 = 5,750 requests/mo, deepseek-v4-pro = 17,150/mo, deepseek-v4-flash = 158,150/mo.

**The design goal: the whole system runs for a calendar month on the $10/mo OpenCode Go plan.** The binding constraint is the kimi quota (5,750/mo). The cost design isolates kimi to a single consumer — plutus-main — and runs everything else on flash, whose budget is effectively unlimited at this volume.

| Tier | Cadence | Model | Sessions/mo | Avg req/session | Total/mo | Against limit |
|---|---|---|---|---|---|---|
| **plutus-main** | **3×/day** | kimi-k2.6 | ~90 | ~40-55 | ~4,000-5,000 | within 5,750 (tight but fits) |
| Operator turns (kimi) | variable | kimi-k2.6 | ~50-150 | ~10 | 500-1,500 | shared with main — the one watch-item |
| **plutus-perception** | spawned by main (3×/day) | **deepseek-v4-flash** | ~90 | ~50 | ~4,500 | trivial vs 158,150 |
| plutus-ops | 48×/day | deepseek-v4-flash | ~1,440 | ~6-8 | ~10,000 | trivial vs 158,150 |
| plutus-thesis (per-cron) | variable | deepseek-v4-flash | ~20-50 | ~5-10 | ~100-500 | trivial |
| One-shot future-checks | variable | deepseek-v4-flash | ~10-30 | ~5-10 | ~50-300 | trivial |

**Three changes (2026-06-01) brought main back under quota and ended the overage:**
1. **plutus-perception → deepseek-v4-flash** (was kimi). It's the heaviest call-volume sub-agent; moving it off kimi removed ~11k kimi req/mo AND fixed the per-run budget-wall flakiness it hit on kimi.
2. **plutus-main 4×/day → 3×/day** (00, 08, 16 UTC). ~25% fewer kimi beats.
3. **macro pipeline folded into perception** — the standalone `plutus-macro-cache` kimi cron was deleted; perception resolves macro itself (on flash) and writes it to the perception cache. Removed ~6 kimi req/day.

The only thing that can still push kimi over is heavy operator chat in the same session as main (they share the kimi quota). That's a deliberate, visible trade-off, not a silent overage. If it becomes a problem, route operator routine queries to a flash side-session (v1.1 idea, deferred).

---

## How trades actually reach Hyperliquid (the execution substrate)

> **Canonical, verified source of truth: `TRADING.md` (repo root, mirrored to `~/.plutus-agent/TRADING.md`).** This section is the architectural summary; `TRADING.md` is the operational detail + recovery runbook. This is the part of the system that once broke silently for two weeks during early development and was painful to diagnose — so it is now documented explicitly and guarded with a health check. **If anything contradicts `TRADING.md`, `TRADING.md` wins.**

Everything above is the agent's *mind*. This is how its *hands* work. Trading is the entire point; this substrate is sacrosanct.

### The native path

Plutus executes via the **native** dispatcher path:

```
place_order(venue="hyperliquid")  →  tools/integrations/hyperliquid/venue.py
                                  →  tools/integrations/hyperliquid/_client.py
                                  →  Hyperliquid Python SDK
```

**dgclaw is NOT the trade path.** dgclaw is an opt-in leaderboard/competition that is dormant in v1. `dgclaw_trade_*` tools exist but are not how Plutus executes. When diagnosing execution, dgclaw's `.env` / `trade.ts` / balance are irrelevant.

### The two-wallet model

| Wallet | Env vars | Role |
|---|---|---|
| **Master** | `HL_PUBLIC_ADDRESS` = `HL_MASTER_ADDRESS` | ACP/Privy-managed. **Holds all funds.** On-chain identity. Its private key is NOT on disk (it lives in the OS keychain, driven by the `acp` CLI). |
| **Agent / API** | `HL_API_WALLET_ADDRESS`, key `HL_API_WALLET_KEY` (in `~/.plutus-agent/.env`) | A plain EVM keypair. **Holds NO funds, cannot withdraw.** Its only job is to **sign trades on the master's behalf.** |

`_client.py` builds the SDK `Exchange` with `wallet = Account.from_key(HL_API_WALLET_KEY)` and `account_address = HL_PUBLIC_ADDRESS`. That is the entire auth model: **agent key signs, master is the account.**

### The registration requirement (the #1 failure mode)

For the agent wallet's signature to be accepted, it must be **registered on Hyperliquid as an approved agent of the master** — an on-chain `approveAgent` action signed by the master (performed by `add-api-wallet.ts`). The registration carries a **`validUntil` (~180 days)**.

**If the agent is unregistered or expired, EVERY trade fails silently** with `"User or API Wallet does not exist"` — inside the SDK call, invisible to the operator. This exact failure occurred during early development: the registration lapsed, every `place_order` failed silently, Plutus kept perceiving/predicting (those don't need the agent wallet) but `trades_executed` stayed 0, and Plutus **misdiagnosed** the structural block as "my entry filter is too strict" and tightened filters.

### Funds, spot, and unified margin

Funds live in the master's **spot** balance. Hyperliquid **unified account mode** (enabled at setup via `activate-unified.ts`) lets spot USDC collateralize perp positions directly.

- When flat, the perp clearinghouse `accountValue` reads **~0** with the money in spot. **This is NORMAL — it means "flat," not "unfunded."**
- Opening a position automatically draws margin from the unified balance. **Never run `usd_class_transfer` / spot→perp "deposits."**
- `hl_total_equity` sums spot + perp, so it reads "funded" even when the registration is dead. **Equity ≠ readiness.**

### The guard: health check + monitoring

The one command that proves trading works:
```bash
cd ~/plutus-agent && .venv/bin/python scripts/check_trade_readiness.py
```
It queries the live HL `extraAgents` registration for the master vs `HL_API_WALLET_ADDRESS` and prints READY / NOT READY with the reason. **plutus-ops runs it every tick (Step 0)** and escalates `trade_path_down` if it ever fails or is within 7 days of expiry; **plutus-main Phase 0 gates on it** before planning any trade. A dead trade path is now caught in ≤30 min, never again endured for weeks. Recovery runbook (re-register via `add-api-wallet.ts`, sync key into `.env`, restart gateway) is in `TRADING.md`.

**OSS note:** `TRADING.md` and `scripts/check_trade_readiness.py` are OSS-ship — every deployment needs this model documented and this guard running. The default SOUL template and the bootstrap-setup / plutus-ops / plutus-main skills all reference it.

---

## The four-stratum cognitive architecture

Plutus's mind has four strata. Each has a different *temporal character* and a different *mutation pattern* — and that's the point.

| Stratum | What it is | Mutation pattern | Where it lives |
|---|---|---|---|
| **0 · Identity** | Who Plutus is. Disposition, values, communication style, territory. | Rarely changes. | `~/.plutus-agent/SOUL.md` |
| **1 · Worldview** | Current world model. Regime reads, watch list, key levels, active hypotheses, focus. | Overwritten as understanding evolves; read fresh at the start of each plutus-main beat. | `~/.plutus-agent/WORLDVIEW.md` |
| **1.5 · Strategy library** | The playbook. Per-strategy hypothesis, data points, regime applicability, weights, conviction, threshold. | Authored / edited / promoted / retired by plutus-main. | `~/.plutus-agent/strategies/{active,trial,observation,proposed,retired}/<name>.md` |
| **1.7 · Perception state** | Current market state cache. Every tracked data point with last-queried timestamp. | Continuously updated by all three tiers based on what each fetches. | `~/.plutus-agent/perception_state.json` (NEW in V2) |
| **2 · Memory** | Cumulative learned facts. Operator preferences, market patterns, statistical insights. | Append-mostly, occasionally edited. Relevance-retrieved. | `~/.plutus-agent/memories/` + holographic plugin SQLite |
| **3 · Lifecycle** | The historical record. Every perception snapshot, thesis, decision, trade, position, evaluation, outcome, reflection, prediction, observation. | **Append-only event log.** Never overwritten. | `~/.plutus-agent/lifecycle.db` |

**The insight:** Identity is stable, worldview is current, strategy library is the playbook, perception is the cache, memory is cumulative, lifecycle is historical. Six storage shapes for six temporal characters. No overlap.

`state.db` (Hermes runtime infrastructure — sessions, skill caches, FTS5 over sessions) is not part of the cognitive model; it's harness substrate.

---

## Stratum 0 — SOUL.md

Identity slot. Already shipped (Phase 3). Auto-loaded by `agent/prompt_builder.py:load_soul_md()` at every turn (gateway constructs a fresh AIAgent per inbound). The harness ships a sparse trader-flavored default; Plutus's `~/.plutus-agent/SOUL.md` refines it with disposition specifics. No V2 changes.

---

## Stratum 1 — WORLDVIEW.md (the cross-session bridge)

The thing without which every session starts cold. Plutus's living world model — but only "live" between sessions, not within them.

### Semantics

- **Read at session start**, injected into the prompt alongside SOUL.md. Plutus boots with a coherent read-of-the-world it had been refining.
- **Written throughout the session** as Plutus updates its read. Writes within a session take effect on the **next** session (operator turn OR cron tick — both rebuild the prompt). This honors Hermes's prompt-caching invariant: system prompt is built once per session and not mutated mid-conversation.
- **Within-session "live" working memory lives in conversation context**, not in WORLDVIEW.md. WORLDVIEW.md is the bridge between sessions, not a real-time scratchpad.

### Format

YAML frontmatter for queryable state, markdown body for narrative. Single file. See actual schema in `plutus_cli/default_worldview.py:DEFAULT_WORLDVIEW_MD` and `agent/worldview_loader.py`.

Frontmatter keys (V2-current):
- `last_updated`, `last_updated_by`, `horizon`
- `tracked_assets` — currently `[BTC, HYPE, SPX]` (V2 change: dropped ETH, added SPX)
- `regime.global`, `regime.per_symbol`, `regime.confidence`, `regime.detected_at`, `regime.dominant_signals`
- `key_levels` per symbol
- `active_hypotheses` (with `strategy_name` + `regime_tag` + invalidation timing)
- `open_positions_summary` — MIRROR of lifecycle.db
- `portfolio_summary` — MIRROR of data_point_snapshots + accounts
- `operator_state` — operator directives, capital_at_risk_usd
- `current_strategies` mirror — synced by `strategy-curator`
- `pending_predictions` mirror — synced by `prediction-tracker` (now in plutus-ops)
- `manor_observations` — recent items from Sebastian via Nightingale Manor (NEW in V2)
- `recent_learnings` — bounded journal; older entries rotate to `~/.plutus-agent/learnings_archive.md`

The mirror sections are summaries of upstream truth — strategy files, lifecycle.db tables, and Sebastian's Manor messages are sources of truth. Drift is detectable (`SELECT positions WHERE id NOT IN worldview_mirror`) and surfaces as a measurable plutus-main discipline failure.

### Who writes WORLDVIEW.md

**Only plutus-main.** Phase 7 of every beat (synthesis + WORLDVIEW write) runs `worldview-discipline` skill. plutus-ops and plutus-thesis never touch this file — even when they detect regime shifts or position closes, they only WRITE OBSERVATIONS that plutus-main reads at next beat and uses to update WORLDVIEW.

---

## Stratum 1.5 — Strategies (the playbook library)

The system Plutus uses to *form* theses. Strategies are not theses themselves; they're the lens through which theses get formed.

### Strategies are hypothesis types, not rule engines

This is the architectural principle from V1, preserved. A strategy IS one named hypothesis pattern. Frontmatter declares: hypothesis, data points consulted, regimes where it applies, conviction model (weights per data point), entry threshold. No signal-count gate. Conviction is computed from current data point readings through the shared `conviction-engine` skill, anchored against a baseline hit rate.

The retired `signal-confluence` strategy demonstrated the failure mode of treating strategies as rule engines: six pre-screen attempts in five days, zero trades. Stripping the gatekeeper and re-framing strategies as narrow hypothesis patterns made the calibration substrate work.

### V2 additions: strategy_conviction + position multiplier

**The major V2 change to the strategy model.** V1 had a single conviction value computed per-thesis at entry. V2 splits conviction into two dimensions:

| Dimension | Range | Lives in | Updates | Speed |
|---|---|---|---|---|
| **strategy_conviction** | 0.0-1.0 | strategy frontmatter | Per resolved prediction: ±α (slow, α=0.02). Per closed trade: ±α larger (α=0.05). | Slow-moving, weeks-to-months to fully form |
| **thesis_conviction** (existing) | 0.0-1.0 | Computed per-thesis at entry via `conviction-engine` | Per current data-point readings × weights via sigmoid | Ephemeral, recomputed every beat |

Position sizing **compounds them**:

```
composite = strategy_conviction × thesis_conviction
multiplier = 20 ** composite       # 1x to 20x, exponential
notional = account_balance × multiplier
size_units = notional / current_price
```

Multiplier shape:

| composite | multiplier | notional @ $25 | risk at 2% SL |
|---|---|---|---|
| 0.0 | 1.0x | $25 | $0.50 (2% acct) |
| 0.25 | 2.11x | $53 | $1.05 |
| 0.5 | 4.47x | $112 | $2.24 (9%) |
| 0.75 | 9.46x | $237 | $4.73 (19%) |
| 1.0 | 20x | $500 | $10 (40%) |

The two-conviction model means **a new trial strategy with strategy_conviction=0.3 and a perfect setup (thesis_conviction=1.0) gives composite=0.3 → multiplier=2.6x → tiny safe position.** A proven strategy (`strategy_conviction=0.85`) with the same strong setup → multiplier=14.5x. New strategies get to trade real capital safely while they prove themselves; proven strategies get to deploy size.

Position sizing happens in the `place_order` dispatcher. The compounded multiplier is the single mechanism through which "is this strategy proven yet?" affects risk deployment — there's no separate gate, no separate "approval" step, just math.

### Cross-portfolio conviction-ranked allocation

Trade selection is **across the entire (tracked_asset × applicable_strategy) matrix**, not per-asset. Every plutus-main beat:

1. For each `(asset, strategy)` where `strategy.regime_applicability` matches current regime and required data points are present in perception_state:
   - `thesis_conv = conviction_engine.compute(strategy, perception_state[asset])`
   - `composite = strategy.strategy_conviction × thesis_conv`
2. Filter to candidates above `strategy.entry_threshold` (default 0.4)
3. Sort by composite, descending
4. Allocate capital top-down: open positions in order, skip assets with existing open positions (one strategy per asset for capital; multiple strategies can monitor predictions for the same asset)

This means Plutus always commits capital to its highest-conviction opportunities. No first-come-first-served. No "I happened to check BTC first so BTC got the capital." The allocation is principled.

v1 of allocation has no preemption: existing positions are not closed to fund higher-conviction new ones. v2 may add overtaking ("if a new opportunity's composite is >X higher than an existing position's current composite, close existing and open new"), but that introduces churn risk and is deferred until allocation dynamics are observed in live trading.

### Why strategies are FILES, not table rows

A strategy is a *living document* — a playbook that evolves with notes, amendments, lessons learned, performance histories. Lifecycle.db is for append-only events; the playbook needs to be edited in place. Files match.

The legacy `strategies` SQLite table still exists for backwards compat (the old `strategy_open` event type still works), but new theses use the `theses.strategy_name` column to link to a file by name. `query_strategy_stats` reads from `theses.strategy_name JOIN outcomes`, no longer from the table. Strategy_conviction lives in frontmatter (canonical) with optional caching to a column for fast cross-strategy SQL queries.

### Layout

```
~/.plutus-agent/strategies/
├── active/         # full sizing potential; strategy_conviction proven
├── trial/          # promoted from observation; building conviction through trades
├── observation/    # promoted from experimental; predictions only
├── proposed/       # just authored, not yet promoted
└── retired/        # moved aside with reflection on why
```

Plus: **experimental strategies have NO file.** They live only as predictions tagged with `strategy_name="experimental-<descriptor>"`. Strategy file gets authored at graduation (experimental → observation or experimental → trial). Examples: `experimental-cvd-macro`, `experimental-dominance-rotation`, `experimental-funding-momentum`. `query_calibration(strategy_name="experimental-cvd-macro")` works whether the strategy has a file or not — predictions are the source of truth.

### Lifecycle stages + graduation criteria

| Stage | What it means | Capital | Promotion gate |
|---|---|---|---|
| (experimental) | Predictions-only, no file | None | N≥10 resolved + calibration ≥55% → author file + promote to `observation` |
| observation | File exists, predictions accumulating | None | strategy_conviction ≥0.3 from prediction track record → promote to `trial` |
| trial | Tiny-to-moderate size via multiplier | Composite-scaled | strategy_conviction ≥0.65 organically → promote to `active` |
| active | Full multiplier range available | Composite-scaled | strategy_conviction <0.4 → demote to `observation` |
| retired | Moved aside, reflection written | None | Stays — may revisit later |

Asymmetric thresholds intentional: revoke fast (N≥20, calibration <30%, across ≥2 regimes → kill), promote slow (strategy_conviction organic accumulation, weeks-to-months). The "wait for N=50 predictions then commit" gate is replaced by "strategy_conviction encodes all the evidence, including regime diversity, sample size, recency, trade outcomes, all combined into one slow-moving number."

**Promotion / demotion / retirement are Plutus's own decisions.** Operator does not gate. Gates are encoded in the `strategy-curator` skill's logic, not in operator approval.

### Prompt injection

`agent/strategy_loader.py:build_strategy_prompt_block()` reads `active/`, `trial/`, `observation/` strategy files at session start and injects a SUMMARY block (name, stage, regime_applicability, strategy_conviction, recent performance) into the system prompt. Same frozen-snapshot semantics as SOUL/WORLDVIEW. Full bodies loaded on-demand via `read_file` when Plutus actually applies a strategy.

### Authoring + curating

- **strategy-author** skill: write a new strategy file in `proposed/`, then promote to `observation/`. Plutus authors autonomously, often from experimental graduation.
- **strategy-curator** skill: weekly + on-demand — refresh `performance:` block, apply strategy_conviction updates, promotions/demotions/retirements based on data.
- **calibration-review** skill: Sunday + on-demand — examine conviction calibration curve (overall + per strategy + experimentals), amend strategy file conviction logic where miscalibrated, decide experimental graduations.

---

## Stratum 1.7 — Perception state cache (NEW in V2)

The cache surface that makes wide perception affordable at the plutus-main cadence (3×/day). `~/.plutus-agent/perception_state.json` holds the most recent reading of every tracked data point, with a per-DP staleness budget, populated by all three tiers based on what each fetches for its own purposes.

### Shape

```json
{
  "last_updated": 1779000000.0,
  "tracked_assets": ["BTC", "HYPE", "SPX"],
  "assets": {
    "BTC": {
      "hl_price":         {"value": 76420.5, "queried_at": 1779000000, "staleness_s": 60, "source": "hyperliquid"},
      "hl_cvd":           {"value": {...},   "queried_at": 1778999700, "staleness_s": 300},
      "hl_funding_and_oi":{"value": {...},   "queried_at": 1778997000, "staleness_s": 1800},
      "ta_rsi:tf=1h":     {"value": 58.3,    "queried_at": 1778999700, "staleness_s": 1800,  "params": {"timeframe": "1h"}},
      "ta_rsi:tf=4h":     {"value": 56.0,    "queried_at": 1778996400, "staleness_s": 7200,  "params": {"timeframe": "4h"}}
    },
    "HYPE": {...},
    "SPX":  {...}
  },
  "macro": {
    "macro_vix":          {"value": 18.5, "queried_at": 1778985600, "staleness_s": 14400},
    "macro_dxy":          {"value": 104.2, ...},
    "macro_cpi":          {...}
  },
  "cross_asset": {
    "btc_dominance":          {"value": 53.2,   "queried_at": 1778996400, "staleness_s": 3600},
    "btc_dominance_velocity": {...},
    "coingecko_global":       {...}
  },
  "derived": {
    "btc_dominance_4h_velocity": {"value": -0.15, "computed_at": 1779000000, "inputs": ["btc_dominance"]}
  }
}
```

Each data point entry has its own staleness budget. The agent always sees the FULL perception_state in plutus-main's context regardless of how many entries were actually refreshed this beat.

### Per-DP staleness budgets

| Type | Refresh cost | Staleness budget |
|---|---|---|
| HL price | 1 call | 60s |
| HL CVD | 1-2 calls | 300s (5min) |
| HL funding/OI | 1 call | 1800s (30min) |
| HL orderbook | 1 call | 300s |
| TA indicator | 1-2 calls | candle_period / 2 (15m→450s, 1h→1800s, 4h→7200s, 1d→43200s) |
| Macro (VIX/DXY/CPI) | 5-10 calls (web_search + parse) | 14400s (4h) — resolved by plutus-perception each beat, cached here (no separate macro cron) |
| Coingecko global / BTC dominance | 1-2 calls | 3600s (1h) |
| Defillama TVL | 1-2 calls | 14400s (4h) |
| Gas (eth_gas) | 1 call | 1800s |
| News sentiment (FUTURE DP, not yet registered) | 10-20 calls (web_search + summarize) | 28800s (8h) |
| On-chain analysis (future DPs) | 5-15 calls | 21600s (6h) |

The staleness budget is declared in the data point registry entry (a new field added in V2 wiring). Refreshing a DP from the cache is the dispatcher's responsibility — if `cache[dp].queried_at + staleness_s > now`, return cached; else fetch fresh, update cache, return.

### Why the cache works across three tiers

The user's key insight resolving the "won't everything be stale every 7h?" puzzle: **the cache is populated by all three tiers continuously based on what each fetches for its own purposes.** So actively-monitored items stay fresh; passively-watched items accept whatever staleness their declared budget allows.

- **plutus-ops every 30min** refreshes DPs needed for prediction resolution + position evaluation → price/CVD/RSI for assets with open positions stays ≤30min stale
- **plutus-thesis (when a Flavor B cron is active)** refreshes the thesis's `data_points_to_watch` every 15min → fresh
- **plutus-main every ~8h** (3×/day) refreshes everything else needed for active/experimental hypotheses; on Sunday beats, broad refresh for setup discovery on passive watchlist

Net effect: BTC price for an open BTC position is always ~30min fresh (ops refreshes). HYPE price for a non-position asset gets a ~8h refresh at the next plutus-main beat. Daily TA on a passive watchlist asset gets 12h staleness allowance — fine, daily candles don't change faster than that. Macro is resolved by the plutus-perception sub-agent each beat (4h budget) and written to this cache — there is no separate macro-cache cron.

### Write contention

All three tiers may write to perception_state.json. Atomic-rename pattern is the only approved write path:

1. Read current state into memory
2. Apply updates
3. Write to `perception_state.json.tmp`
4. `os.rename(tmp, perception_state.json)` (atomic on POSIX)

No fancy locking. The infrequency of writes per second (a few per minute across all tiers at most) makes atomic-rename's last-writer-wins semantics safe — worst case is one tier's tiny update is lost and re-fetched next time. The cost is one extra fetch per millennium.

### Derived fields

The `derived` section holds computed fields plutus-main writes (synthetic indicators like `btc_dominance_4h_velocity` or `eth_btc_ratio_zscore` computed from raw cached values). These are useful for hypothesis evaluation but aren't raw data points. plutus-main may write them in Phase 7; plutus-ops + plutus-thesis treat them as read-only.

### Principle 4 revised

V1: "No caching at fetch layer. Every fetch hits the source. Freshness is correctness."

V2: "Perception cache with per-DP staleness budgets. Every `fetch_data_point` hits the source when the cached value is stale; returns cache otherwise. The cache exists but is fail-safe via staleness — freshness is correctness for items where it matters, staleness is acceptable for items where it doesn't."

The old macro-cache cron (4h staleness for VIX/DXY/etc.) was an early exception that proved the rule; V2 generalized the pattern with per-DP budgets, and 2026-06-01 folded macro resolution into the plutus-perception sub-agent (no standalone cron).

---

## Stratum 2 — memories/

(Preserved from V1, lightly extended for Sebastian.) Plutus's accumulated knowledge needs are heterogeneous — operator preferences, market patterns, statistical insights, procedural notes, peer-AI context from Sebastian. No single provider serves all of them well. Plutus uses a **tiered memory architecture** where each layer is matched to its data shape.

### Layers

| Layer | Provider | Data shape | Retrieval pattern |
|---|---|---|---|
| **1. Hot-path always-on context** | Built-in `MEMORY.md` + `USER.md` (Hermes default) | Curated ~5KB of always-relevant facts | Frozen snapshot injected into system prompt at session start |
| **2. Cross-session accumulated entity-keyed knowledge** | **Holographic plugin** (HRR + FTS5 + Jaccard hybrid; local SQLite, no external dep beyond numpy) | Markets, operator, peer (Sebastian), domain — entity-tagged, trust-scored | `fact_store(action='probe'|'search'|'reason'|'contradict', ...)` + `fact_feedback` |
| **3. Trade lifecycle (Stratum 3)** | `lifecycle.db` with voyage-finance-2 + sqlite-vec | Theses, reflections, predictions, observations | Hybrid FTS5 + vector via RRF, LLM-summarized digests |

Plus the **`consolidate-learnings` skill**: Sunday plutus-main beat reviews recent conversations + reflections + Manor exchanges, extracts durable facts via aux LLM, writes to Layer 2 with entity tags.

### Why holographic over alternatives

Locked decision from V1, preserved. Holographic chosen because: local + zero new deps, entity-centric retrieval matches market knowledge needs, trust scoring rewards patterns that hold, only one external memory provider allowed at a time. Honest limitation: HRR is *compositional*, not *semantic* — "crowded longs" and "elevated open interest" share zero phase signal. Mitigated by Layer 3 (voyage-finance-2 over theses/reflections handles semantic paraphrase).

### Hermes memory ≠ lifecycle.db

Worth restating: Stratum 2 (memory) and Stratum 3 (lifecycle) are distinct concerns:
- Memory = *cumulative knowledge* retrieved by relevance/entity ("what do I know about X?")
- Lifecycle = *historical events* retrieved by causal chain or semantic similarity ("what happened?")

The `consolidate-learnings` skill is the bridge: it turns lifecycle into memory.

---

## Stratum 3 — lifecycle.db (the traced lifecycle)

The historical record. Append-only, foreign-key-linked, queryable for both reflection and ML.

### Schema (unchanged in V2 — see `agent/lifecycle_db.py` for canonical schema)

V2 does NOT modify the lifecycle.db schema. The same tables that supported V1 carry V2 unchanged:

- `data_point_snapshots` — every fetch auto-snapshots here (Principle 4 revision doesn't change this; cache and snapshot are different concerns)
- `strategies` (legacy, backwards compat)
- `theses` — strategy_name + regime_tag + prediction_horizon_hours + invalidation_criteria_json + embedding + FTS5
- `decisions` — action + params + conviction
- `trades` — fill events
- `positions` — open/closed with venue-actual + perceived timestamps
- `position_evaluations` — the conviction trajectory
- `outcomes` — auto-computed at position close with derived conviction stats
- `reflections` — kinds: post_trade, loss_postmortem, weekly_review, calibration_review, strategy_review, ad_hoc + error_class on losses + embedding + FTS5
- `predictions` — pre-registered falsifiable claims with horizons + FTS5
- `observations` — journal kinds: noticed, watching, almost_traded, mental_model, pattern_candidate, edge_claim, edge_revoked, operator_input, regime_shift + FTS5
- `capital_movements`

Plus vec0 virtual tables (`theses_vec`, `reflections_vec`) for sqlite-vec semantic search.

### V2 conventions on the existing schema

- **`observations.structured_tags_json`** — V2 uses this column to carry provenance (`source_tier` ∈ main/ops/thesis_monitor, `source_model`, `tier_session_id`, `tick_at_unix`) without requiring a schema migration. ops_summary observations also use it for the canonical handshake shape (counts, flags, pending work for plutus-main). The exact tag schema is documented in `skills/trading/plutus-ops/SKILL.md`.
- **`observations.session_id`** — V2 depends on this being reliably populated. Currently 232 observations in the live DB have NULL session_id (Issue #0 in the V2 ship-blockers); root-cause + fix before V2 enables.
- **`predictions` with `strategy_name="experimental-*"`** — experimental strategies live ONLY as predictions; calibration queries work identically whether a strategy has a file or not.
- **`reflections.position_ids_json`** — V2 adds `query_unreflected_closes` dispatcher that uses JSON1 `json_each` to find closed positions not yet covered by any reflection. The naive LIKE pattern is unsafe for multi-digit position IDs.

### Embeddings on theses and reflections

(Preserved from V1.) voyage-finance-2 (1024 dims) via API. Synchronous inside `record_event` for thesis/reflection types — write atomicity means thesis and vector exist together. ~$6/year at Plutus's volume. `embedding_model` column tracks per-row provenance so re-embedding via script later is supported.

Storage + search via **sqlite-vec** extension. `find_similar_theses` / `find_similar_reflections` use **hybrid retrieval**: FTS5 BM25 + sqlite-vec cosine fused via reciprocal rank fusion (RRF), then optionally summarized via a cheap fast model before returning to Plutus.

### Conviction as a trajectory, not a point

(Preserved from V1.) `position_evaluations` records conviction every cycle (now: plutus-ops every 30min for default-flavor monitoring, plutus-thesis every 15min for Flavor B, plus plutus-main during Phase 4). The trajectory enables:

- "Did I exit on noise?" winners that closed after a brief conviction dip
- "Did I hold through warning signals?" losers where conviction declined steadily but Plutus stayed in
- "What does a winning trajectory look like?" aggregate curves by outcome bucket
- "Did invalidation fire, how fast did I respond?" via `invalidation_triggered_at` + `invalidation_to_exit_minutes`
- "Was my entry conviction calibrated?" via `query_calibration` correlating conviction-at-entry with realized R

V2 addition: every position_evaluation also records WHICH strategy_conviction was in effect at the time (via the position's linked thesis → strategy_name → that strategy's current strategy_conviction). The trajectory captures both ephemeral thesis conviction and slow-moving strategy conviction together.

### How rows get written

(Preserved from V1.) Auto-snapshot pattern: `fetch_data_point` dispatches to the fetcher AND persists a row to `data_point_snapshots` AND returns the value with snapshot id. Every perception is captured for free.

Auto-snapshot interacts with the V2 perception cache: when `fetch_data_point` returns a value (whether from cache or fresh fetch), it still writes a snapshot row. The cache short-circuits the source fetch, not the snapshot. ML queries that join positions → decisions → theses → snapshots still traverse cleanly.

All other writes via `record_event(...)`, `record_observation(...)`, `record_prediction(...)`, `resolve_prediction(...)`, `place_order(...)`, `close_position(...)`. See the registry section for the full dispatcher list.

---

## Conviction architecture (V2 addition)

The two-dimension model in detail. This is the single most important V2 change to how Plutus thinks about decisions and risk.

### Two dimensions

**`thesis_conviction`** — ephemeral, computed per-thesis at the moment of evaluation by `conviction-engine`. Reflects "how strongly does the current setup match this strategy's hypothesis?"

**`strategy_conviction`** — slow-moving, lives in strategy frontmatter. Reflects "how much faith do I have in this strategy ITSELF?" Encoded as a single 0.0-1.0 number derived from the strategy's lifetime track record.

### Computing thesis_conviction (unchanged from V1)

The strategy's `conviction-engine` computes per-data-point contributions (each value normalized through a per-DP normalizer to a signed contribution in [-1, +1]), takes a weighted sum, maps to [0, 1] via sigmoid, anchors against `inherited_baseline`. See `skills/trading/conviction-engine/SKILL.md` for the math.

### Computing strategy_conviction (NEW)

Updates happen during plutus-main beats (NEVER ops — interpretive call, not mechanical):

- **Per resolved prediction:** brain reads `weights_pending_update` from ops_summary, decides if the resolution was clean enough to count. If counted: strategy_conviction += α × sign(outcome) where α=0.02. Brain may skip the update if the resolution is ambiguous, mid-regime, or otherwise noisy.
- **Per closed trade:** strategy_conviction += α × sign(realized_r_multiple) where α=0.05. Larger alpha because trades carry capital and more information than predictions.
- **Bounds:** clamped to [0.05, 1.0]. Below 0.05, the strategy gets demoted to observation or retired (see strategy lifecycle stages).

### Position sizing as the only application

```
composite_conviction = strategy_conviction × thesis_conviction
position_multiplier = 20 ** composite_conviction        # 1x to 20x exponential
notional_usd = account_balance × position_multiplier
size_units = notional_usd / current_price
```

`place_order` dispatcher computes this when called with `(venue, thesis_id, conviction, ...)`. `conviction` parameter is `composite_conviction`; dispatcher infers strategy_conviction from the thesis's strategy_name lookup. SL/TP still placed atomically as on-venue brackets via HL native `bulk_orders(grouping="normalTpsl")`.

### Strategy_conviction history for ML

Each update writes a `record_event(type="strategy_conviction_change", ...)` row capturing: strategy_name, old_value, new_value, delta, alpha, reason (`prediction_correct|wrong`, `trade_win|loss`, `manual_curator_adjustment`), triggering_prediction_id (nullable), triggering_position_id (nullable), ts. ML downstream can reconstruct the full causal chain by joining strategy_conviction_change events with predictions + outcomes.

### Cross-portfolio conviction-ranked allocation

(Repeated from Stratum 1.5 because it's load-bearing.) Phase 4 of plutus-main enumerates every `(tracked_asset × applicable_strategy)` pairing, computes composite, filters to candidates above threshold, sorts descending, allocates capital top-down. No first-come-first-served; capital always goes to highest-conviction opportunities first. One strategy per asset for capital (no preemption in v1; v2 may add).

---

## The prediction factory — the discovery loop

This is the load-bearing innovation V2 makes explicit. V1 had predictions as a calibration substrate; V2 elevates the prediction factory to a core function of plutus-main and the primary mechanism by which new edges are discovered.

### Why predictions are the discovery loop

Predictions are free. Capital costs money. The calibration curve is the most valuable asset Plutus accumulates. **The system discovers edges through abundance, not invents them through cleverness.** Generate 100 predictions across 10 hypotheses, let calibration tell which 2 are non-random, formalize those as strategies.

V1's failure mode was treating predictions as scarce — Plutus generated ~5/day on average. V2 targets ~9-30/day (3-10 per plutus-main beat × 3 beats). The cost is essentially zero (predictions don't deploy capital); the information gain is massive.

### Three categories per beat

Every plutus-main beat Phase 5 registers 3-10 predictions across:

1. **Existing-strategy predictions (1-3 per beat).** For each active/trial strategy that didn't trigger a trade this beat but had a borderline setup, register a prediction tagged with that strategy's `strategy_name`. Keeps calibration fed even when conviction was just below entry threshold.

2. **Experimental predictions (2-4 per beat).** Identify untested data point combinations. Each tagged with provisional `strategy_name="experimental-<descriptor>"`. No strategy file yet — file gets authored at graduation. Examples Plutus might generate from current data:
   - `experimental-cvd-macro` — "BTC CVD percentile >85 + DXY trending down → BTC holds support for 6h"
   - `experimental-dominance-rotation` — "HYPE/BTC ratio inflecting up + alt CVD accumulation → alt outperformance for 12h"
   - `experimental-funding-momentum` — "funding rate flip + price momentum → trend continues for 8h"
   - `experimental-orderbook-imbalance` — "bid/ask depth ratio extreme → reversal within 4h"
   - `experimental-cross-asset-spx-btc` — "SPX up >0.5% in 1h + BTC flat → BTC catches up within 4h"

3. **Regime stress tests (1-2 per beat).** Tag an existing strategy with an unusual regime to probe edge boundaries. "Does support-hold work in momentum_continuation? Predict yes/no with 12h horizon, tagged strategy_name=support-hold + regime_tag=momentum_continuation."

### Each prediction includes

- `strategy_name` (real or experimental)
- `regime_tag` (current regime at registration)
- `claim_md` (the falsifiable claim, plain English)
- `success_criteria_json` (machine-checkable: data point name + comparison + value)
- `failure_criteria_json` (machine-checkable inverse, explicit)
- `horizon_ts` (when plutus-ops will resolve)
- `conviction` (predicted probability, 0-1)
- `snapshot_ids_json` (baseline data point readings — what conviction-engine needs for weight updates later)

### Resolution flow (plutus-ops)

Every 30 min, plutus-ops queries `query_predictions(status="due")` and resolves each by comparing claim's success/failure criteria against current readings:
- `correct` if success criteria met
- `wrong` if failure criteria met
- `ambiguous` if neither clearly met (e.g., partial match)
- `expired_unresolvable` if data needed for resolution isn't available

Resolution writes `predictions.outcome`, `resolved_at`, `realized_value_json`. Flags `weights_pending_update` in ops_summary so plutus-main can decide the weight update at next beat.

### Graduation flow

The `calibration-review` skill runs every Sunday plutus-main beat (and on-demand when ops flags an `experimental_graduation_candidate`). For each `experimental-*` strategy_name with N≥10 resolved predictions:

| Calibration | N threshold | Regime diversity | Action |
|---|---|---|---|
| <30% | N≥20 | 2+ regimes | **Revoke fast.** `record_observation(kind="edge_revoked", strategy_name="experimental-...", text_md=...)`. Stop tagging future predictions. |
| 30-45% | N≥20 | (any) | Continue observing. |
| 45-65% | N≥20 | (any) | Continue observing. |
| ≥55% | N≥10 | 1+ regime | **Promote.** `strategy-author` writes STRATEGY.md in `proposed/`, then promotes to `observation/`. Initial `strategy_conviction=0.2` (very conservative). |

After promotion, the strategy starts accumulating strategy_conviction through subsequent prediction resolutions + (once it crosses to `trial`) actual trades. The graduation criteria are intentionally asymmetric: kill dead ends fast, promote winners conservatively at low strategy_conviction so they trade safely until proven.

---

## External perception sources — Nightingale Manor and Sebastian (NEW in V2)

Plutus doesn't perceive markets in isolation. There's a peer-AI relationship that V2 makes architecturally explicit.

### Who Sebastian is

Sebastian is the operator's personal AI assistant — built on a separate agent stack, serving the operator's broader life (calendar, communications, research, general personal-AI roles). Sebastian is NOT a trading agent.

### The Manor chat

A shared chat called **Nightingale Manor** connects Plutus, Sebastian, and the operator. Routing is via the gateway (Telegram). Sebastian's role in the chat is bidirectional:

- **Market context provider.** Sebastian shares market observations, news summaries, conditions reports — material the operator wouldn't necessarily think to forward, but which Sebastian's broader awareness catches.
- **Accountability check-ins.** Sebastian asks Plutus about decisions, surfaces apparent inconsistencies between WORLDVIEW.md and actions taken, peer-reviews trade theses.

Sebastian does NOT trade and does NOT have authority over Plutus's decisions. The relationship is peer, not supervisory.

### How Manor messages flow into Plutus's perception

Messages arriving from the Manor chat appear in Plutus's session as inbound messages (synthetic injection or regular gateway path depending on routing config). Plutus records them via `record_observation(kind="operator_input", structured_tags={"source": "sebastian", "manor_chat": true, ...})`. plutus-main Phase 0 handshake includes a check for recent Manor-tagged observations alongside the ops_summary digest.

The WORLDVIEW.md frontmatter gains a `manor_observations` field (NEW in V2) that mirrors recent Manor input as a short bounded list. `consolidate-learnings` (Sunday weekly) extracts durable Manor-derived facts into holographic memory with entity tags including Sebastian-as-source so trust scoring tracks Sebastian's signal quality over time.

### Why this matters architecturally

Operator-only input has one observation rate and one signal character. Adding Sebastian-as-source doubles the rate of external context AND introduces an independent perspective. Sebastian sees things the operator doesn't mention; Sebastian frames things differently than the operator would; Sebastian's check-ins surface accountability gaps the operator might miss. Plutus benefits from both, with provenance attached so calibration can measure each source's signal quality independently.

---

## Foundational pattern: registries + dispatchers

(Preserved from V1.) The architectural backbone that lets the system scale to hundreds of capabilities without bloating the agent's tool surface.

### The pattern

1. **Registry** — Python decorator-driven catalog of capability entries (data points, event types, venues, alerts).
2. **Dispatcher** — single agent-facing tool that takes `name` + `**params` and dispatches to the registered entry.
3. **Discovery** — `list_*` tool lets the agent enumerate available entries.
4. **Integration files** — per-source modules register entries at module load.

Tool surface stays small. Capability scales via registry depth.

### The six registries

| Registry | Dispatcher | Discovery | What it catalogs |
|---|---|---|---|
| **data points** | `fetch_data_point(name, **params)` | `list_data_points(category?, source?)` | Anything fetchable: prices, funding, OI, indicators, on-chain stats, news (future), leaderboard rank, wallet balance, holdings, total equity |
| **event types** | `record_event(type, **params)` | `list_event_types()` | thesis, decision, reflection, position_evaluation, capital_movement, strategy_open/pause/retire, **strategy_conviction_change** (NEW in V2) |
| **venues** | `place_order`/`close_position`/`modify_order`/`cancel_order`/`account_state`(`venue`, ...) | `list_venues()` | Hyperliquid (initial); future: bybit, dydx, etc. |
| **accounts** | `list_accounts(purpose?)` | `list_accounts()` | ACP wallets, exchange accounts, cold storage, staking, LP |
| **alerts** | (declarative — fired by watcher, not invoked by agent) | `list_alerts()` | position_status_change, price_threshold_breach, funding_spike, account_balance_change |
| **identity systems** | direct tools per system | `list_identity_systems()` | ACP, future identity systems |

### Currently registered data points (V2 inventory)

(Sourced from `tools/integrations/*/data_points.py` as of 2026-05-20.)

- **hyperliquid:** hl_price, hl_candles, hl_orderbook, hl_funding_and_oi, hl_universe, hl_holdings, hl_total_equity, hl_drawdown_from_peak
- **flow** (CVD): hl_cvd
- **ta** (15 indicators): ta_rsi, ta_stochastic, ta_williams_r, ta_cci, ta_mfi, ta_roc, ta_macd, ta_adx, ta_aroon, ta_trix, ta_vortex, ta_sma, ta_ema, ta_vwap, ta_bbands
- **coingecko:** coingecko_global, coingecko_trending, btc_dominance_velocity
- **defillama:** defillama_tvl_chains, defillama_tvl_protocols, defillama_stablecoin_supply, defillama_stablecoin_chains
- **macro:** macro_vix, macro_dxy, macro_cpi
- **gas:** eth_gas
- **acp:** acp_wallet_balance, acp_browse_offerings, acp_chain_list
- **dgclaw:** dgclaw_leaderboard, dgclaw_leaderboard_agent, dgclaw_forums, dgclaw_forum, dgclaw_forum_posts, dgclaw_forum_unreplied, dgclaw_token_info

**Not yet registered (V2 future additions):**
- **news sentiment** — would live as `tools/integrations/news/data_points.py`; web_search backed; 8h staleness budget
- **on-chain analysis** — Etherscan/glassnode/dune backed; 6h staleness budget
- **funding sentiment cross-exchange** — Binance/Bybit/OKX funding rates for divergence signals
- **HL spot/equity perp coverage** (SPX): exists as `hl_price(symbol="SPX")` via the universal hl_price dispatcher if HL exposes it; needs liquidity verification at our position sizes

Each registry entry will gain a `staleness_s` field in V2 wiring (currently implicit; explicit in registry going forward).

### Example: adding a data point

```python
# tools/integrations/news/data_points.py  (FUTURE)
@register_data_point(
    name="news_sentiment_btc",
    category="sentiment",
    source="news_aggregator",
    description="Recent news sentiment for BTC via web_search + LLM summarization.",
    params_schema={"window_h": {"type": "integer", "required": False, "default": 6}},
    returns_schema={"sentiment_score": "float -1..+1", "key_themes": "array", "article_count": "int"},
    tags=["sentiment", "fundamentals"],
    staleness_s=28800,   # 8h — news cycle, expensive to refresh
)
def get_btc_sentiment(window_h: int = 6):
    return _agentic_news_query("BTC", window_h)
```

That's the entire change. Plutus discovers it on next `list_data_points()`. Auto-snapshots. Goes into perception_state cache with the declared staleness. No schema migration, no tool surface change.

---

## Tool surface

(Mostly preserved from V1; V2 additions noted.) Categorized by function, not by source. Total agent-visible surface: ~17 always-on dispatchers + ~28 inherited Hermes tools.

### `trader_core` (always on, inherited Hermes — ~28 tools)
filesystem, shell, web search, browser, cron, memory, session_search, send_message, todo, skill_manager, etc.

### `perception` (always on — ~4 tools)
- `fetch_data_point(name, **params)` — registry-dispatched; reads perception_state cache when fresh, fetches when stale, auto-snapshots
- `list_data_points(category?, source?)` — discovery
- `account_state(venue?)` — read venue account state, surface diffs against lifecycle.db
- `record_data_point_observation(name, value, params?)` — close the agentic-blueprint write-back loop for DPs whose fetch is an LLM/web flow rather than an API call

### `execution` (always on — ~5 tools)
- `place_order(venue, thesis_id, conviction, side, symbol, ...)` — atomically writes decision + trade + position rows. **V2 change: conviction parameter is now composite_conviction; dispatcher computes `multiplier = 20 ** conviction` and notional automatically.** SL/TP placed atomically as on-venue brackets via HL `bulk_orders(grouping="normalTpsl")`. Trigger order IDs persisted to `decisions.params_json`. Bracket warnings on partial-bracket-failure surface in result.
- `close_position(venue, position_id, thesis_id?, conviction, exit_reason, reflection_text?)` — atomically closes + writes records. Cancels tracked SL/TP triggers first.
- `modify_order`, `cancel_order`, `list_venues`

Trade tools are **ungated** — the agent is in full control. ($25 risk capital sets natural ceiling; strategy_conviction × thesis_conviction multiplier provides risk discipline.)

### `reflection` (always on — ~20 tools)

Event recording (registry-dispatched):
- `record_event(type, **params)` — thesis, decision, reflection, position_evaluation, capital_movement, strategy_open/pause/retire, **strategy_conviction_change** (V2)
- `list_event_types()` — discovery

Predictions + observations:
- `record_prediction(claim_md, horizon_hours, success_criteria, ...)` — pre-register
- `resolve_prediction(prediction_id, outcome, resolution_notes_md, ...)` — close out (plutus-ops uses this)
- `record_observation(text_md, kind, ...)` — append journal entry. V2 uses `structured_tags_json` heavily for provenance (source_tier, source_model, etc.)
- `query_predictions(status, ...)` — list predictions by status/symbol/strategy/regime
- `query_observations(kind, search, ...)` — read journal with FTS5 search
- `query_strategy_stats(strategy_name, include_predictions, ...)`

Lifecycle queries (direct, not dispatcher-style — each has a distinct return shape):
- `query_trades`, `query_performance`, `query_performance_attribution`
- `query_equity_curve`, `query_capital_movements`
- `query_calibration(strategy_name?, regime_tag?, include_predictions?, period?)`
- `query_skip_outcomes`
- `query_conviction_trajectory(position_id)`, `query_conviction_outcomes(group_by=...)`
- `query_strategy_book` (legacy), `query_unreflected_closes(since_ts)` (NEW in V2; uses JSON1 `json_each` for safe position_id matching)
- `find_similar_theses`, `find_similar_reflections`
- `inspect_position`

### `identity` (always on — ~3 tools + per-integration extensions)
- `list_identity_systems()`, `list_accounts(purpose?)`, `account_state(venue?)`

Per-integration identity tools (`acp_whoami`, `acp_signer_add`, etc.) live in integration modules and load when opted in.

### Integrations

Each integration is `tools/integrations/<name>/`. Contains `data_points.py`, optional `accounts.py`, `venue.py`, `events.py`, `alerts.py`, `identity.py`, `operations.py`. Initial integrations: `hyperliquid` (always on), `acp` (opt-in), `dgclaw` (opt-in, depends on acp). Future: `bybit`, `dydx`, `news`, etc.

### Operational notes from first live setup (2026-05-05)

(Preserved from V1 — lessons learned the hard way still apply.) Summary:

1. When upstream ships a `SKILL.md`, vendor it as a Hermes skill — don't subprocess-wrap.
2. Verify command shapes against `<cli> --help`, not docs summaries.
3. Subprocesses spawned inside the gateway die on pm2 restart — use operator-instruction returners for OAuth long-polls.
4. The local-vs-global CLI install split has a keychain trap.
5. Test fixtures must reset module-level singletons.
6. `hl_total_equity` must include spot USDC, not just margin equity.
7. Hardcoded data dir names break fork compatibility.

Full prose in V1 PLUTUS.md via git history; lessons encoded in `skills/<name>/UPSTREAM.md`.

---

## The plutus-main beat — 8 phases

Replaces V1's "heartbeat skill is the router" pattern. plutus-main is no longer a state-driven router that picks ONE phase skill — it's a full pipeline that ALWAYS runs all 8 phases in order, with each phase short-circuiting cleanly when nothing's pending.

The "patience is structural" doctrine ("most ticks the right answer is nothing") that lived in V1's heartbeat skill migrates to plutus-ops in V2. plutus-main is no longer where stillness lives — every plutus-main beat does meaningful work.

### Phase 0 — Read the handoff (mandatory, ~5 tool calls)

Wake up by asking "what happened while I was asleep?"

```
1. Check ~/.plutus-agent/escalation.flag (if exists, handle as entire beat; defer rest)
2. account_state(venue="hyperliquid")                  # ground truth
3. query_observations(kind="noticed", since_ts=<last_main_beat>, limit=50)
   → filter client-side for structured_tags.source_tier in ("ops", "thesis_monitor")
   → digest ops_summary entries: predictions resolved, drift, equity trend, pending work
   → digest any Manor-tagged observations from Sebastian
4. query_unreflected_closes(since_ts=<last_main_beat>)
5. query_predictions(status="due", limit=20)           # backup in case ops missed any
```

After 0, plutus-main knows: ops's bookkeeping summary, Sebastian's recent input, what positions closed, what predictions still need resolution.

### Phase 1 — Process pending interpretive work (variable; only if items exist)

Each item flagged by ops:
- **pending_reflections** → run `loss-postmortem` or `post-trade-reflection`, write reflection with `position_ids_json` covering closed position
- **weights_pending_update** → apply per `conviction-engine` (alpha + direction = plutus-main's judgment, NOT ops's); also update strategy_conviction per the V2 algorithm
- **experimental_graduation_candidates** → query calibration for that strategy_name, decide promote/observe/revoke per the graduation criteria
- **thesis_invalidations_flagged** (from plutus-thesis) → review whether to close, modify, or override

Each item with nothing-to-do short-circuits.

### Phase 2 — Regime check (conditional)

Read WORLDVIEW.md regime block. If `detected_at` >4h old OR ops flagged regime-relevant data point shifts → run `regime-detection` (reads macro cache, cheap). Otherwise skip.

### Phase 3 — Wide perception (~10-25 tool calls)

The expensive phase. plutus-main is the only tier with wide-perception authority. Algorithm:

```
1. Read perception_state.json
2. Compute "needed" set = union of:
     - data points declared in each active/trial strategy frontmatter
     - data points referenced by current experimental-* predictions
     - data points for tracked_assets not currently in positions (setup discovery)
     - cross-asset / macro context
3. Compute "stale" set = (needed) where (now - queried_at) > staleness_s
4. Fetch stale entries; update perception_state
5. Pass the FULL perception_state into plutus-main's context for downstream phases
```

Phase 3 is structurally bounded: only refreshes what hypotheses actually need, and only what's actually stale (ops/thesis having populated the cache for active items already). Lean beats refresh ~5-10 entries; Sunday beats do broad refresh for setup discovery.

### Phase 4 — Strategy work: cross-portfolio conviction-ranked allocation (~5-30 tool calls)

```
candidates = []
for asset in tracked_assets:
    for strategy in active_or_trial_strategies:
        if not strategy.regime_applicability_matches(current_regime): continue
        if required_dps_missing_from_perception(strategy, asset): continue
        thesis_conv = conviction_engine.compute(strategy, perception_state[asset])
        composite = strategy.strategy_conviction * thesis_conv
        if composite > strategy.entry_threshold:
            candidates.append((asset, strategy, composite, thesis_conv))

candidates.sort(key=composite_descending)
remaining_budget = account_balance - sum(currently_open_position_notionals)
for asset, strategy, composite, thesis_conv in candidates:
    if has_open_position(asset): continue            # one strategy per asset for capital
    notional = account_balance * (20 ** composite)
    if notional > remaining_budget: continue         # skip if insufficient capital
    record_event("thesis", strategy_name=strategy.name, regime_tag=..., ...)
    place_order(venue="hyperliquid", thesis_id=..., conviction=composite, ...)
    add_to_active_thesis_monitors(...)               # so plutus-ops/thesis can watch
    remaining_budget -= notional
```

Pre-check guards: `drawdown-discipline` + `tilt-detection` skills run before any new thesis authoring.

### Phase 5 — Prediction factory (~10-25 tool calls, LOAD-BEARING)

**The new core function.** Every plutus-main beat — even beats where no trades fired — generates 3-10 predictions per the three-category composition (existing-strategy / experimental / regime-stress-test). See "The prediction factory" section above for the full pattern.

Each prediction call:
```python
record_prediction(
    strategy_name="experimental-cvd-macro",
    regime_tag=current_regime,
    claim_md="BTC holds support above 76k for next 6h given CVD percentile=87 + DXY trending down",
    success_criteria_json={"data_point": "hl_price:BTC", "comparison": "gte", "value": 76000, "at_ts": "+6h"},
    failure_criteria_json={"data_point": "hl_price:BTC", "comparison": "lt", "value": 76000, "before_ts": "+6h"},
    horizon_ts=now + 6*3600,
    conviction=0.65,
    snapshot_ids_json=[<perception_state ids captured this beat>],
)
```

### Phase 6 — Cron orchestration (~3-10 tool calls, often skipped)

plutus-main is the only tier that touches the cron table:
- **New positions opened this beat** → add entry to `~/active-thesis-monitors.json` for plutus-ops default sweep. Optionally spawn dedicated `plutus-thesis-N-monitor` cron for high-cadence monitoring (15-min cadence, bounded repeat count).
- **Positions closed since last beat** → remove monitor entry
- **One-shot future checks** → spawn via `cronjob(schedule=<ISO>, model={'model':'deepseek-v4-flash'}, prompt='<self-contained>')` for specific moments ("check BTC at 14:00Z post-CPI")
- **plutus-ops cadence adjustment** (rare)

### Phase 7 — Synthesis + WORLDVIEW write (~5-10 tool calls)

- Run `worldview-discipline` skill — updates WORLDVIEW.md (regime, key_levels, active_hypotheses, open_positions_summary mirror, portfolio_summary mirror, manor_observations digest, recent_learnings)
- Record main-beat summary observation:

```python
record_observation(
    kind="noticed",
    text_md="<one-line digest of this beat>",
    structured_tags={
        "source_tier": "main",
        "source_model": "kimi-k2.6",
        "summary_type": "main_beat",
        "tick_at_unix": <ts>,
        "phases_executed": [0, 1, 3, 4, 5, 7],
        "phases_short_circuited": [2, 6],
        "predictions_registered": N,
        "trades_executed": M,
        "reflections_completed": K,
        "experimentals_graduated": [...],
        "experimentals_revoked": [...],
        "escalation_handled": false,
    },
)
```

### Sunday extras (Phase 7.5 — only on the Sunday 16Z beat, the last beat of the day)

Inserted between Phase 7 and finish:
- `weekly-review` → synthesize the week
- `calibration-review` → including experimental graduation analysis
- `strategy-curator` → promote/demote/retire based on accumulated strategy_conviction
- `consolidate-learnings` → compress week's reflections + Manor exchanges into holographic memory; rotate `recent_learnings` to `learnings_archive.md`

### Cost discipline

Target ~100 tool calls per beat, hard ceiling 120. Phase 0+2+7 are fixed-cost (~15 combined). Phase 1, 4, 6 are conditional (often 0). Phase 3, 5 are variable but bounded (10-25 each). Phase 3 leans on the perception_state cache to avoid refetching items ops/thesis already refreshed.

---

## The plutus-ops cycle

Self-contained prompt, every 30 minutes, deepseek-v4-flash, isolated session. Reads WORLDVIEW.md + lifecycle.db + perception_state.json from disk; no carried context.

### Mandatory every tick

1. `query_predictions(status="due", limit=20)` → resolve each
2. `account_state(venue="hyperliquid")` → fetch open positions
3. Quick lifecycle check (Python via `terminal`): `SELECT id, symbol, side, size, status FROM positions WHERE status='open'`; compare to venue. Detect drift.
4. For each open position with no `position_evaluation` in last 1h: fetch the thesis's `data_points` (updates perception_state), record `position_evaluation`
5. `fetch_data_point("hl_total_equity")` → equity snapshot
6. Read `~/.plutus-agent/active-thesis-monitors.json` → for each entry, fetch declared `data_points_to_watch` (updates cache), evaluate `invalidation_rules`, record `position_evaluation` + observation if any rule fires
7. (removed) Macro is folded into the plutus-perception sub-agent as of 2026-06-01 — no macro.json, no macro-cache cron. ops does not check macro freshness.

### Conditional

- **Lifecycle/venue drift:** record observation with `drift_detected=true`. DO NOT run reconcile — flag for plutus-main.
- **Equity drop >5%:** record observation, flag.
- **Catastrophic (>10% drop OR position SL within 2× ATR with conviction <0.4 OR near-liquidation):** Trigger escalation (see Escalation section).
- **Data point fetch error:** record observation with DP name, flag.
- **Any experimental-* strategy_name at N≥20 resolved:** flag `experimental_graduation_candidates`.

### Mandatory at end: ops_summary observation

Every tick ends with exactly one ops_summary observation carrying the canonical handshake shape (counts, flags, pending work for plutus-main). The exact `structured_tags_json` schema is in `skills/trading/plutus-ops/SKILL.md`.

### Forbidden (hard)

- `place_order`, `close_position`, `modify_order`, `place_trigger`
- `record_event(type="thesis"/"decision")`
- Skills: `regime-detection`, `strategy-curator`, `calibration-review`, `strategy-author`, `loss-postmortem`, `pre-mortem`, `weekly-review`, `consolidate-learnings`
- `send_message` to operator
- Writing WORLDVIEW.md or any strategy file
- Calling `conviction-engine.update_weights`
- Expanding perception beyond per-prediction / per-position scope

### Quiet ticks

Most ticks have nothing notable. ops_summary still writes (counts all 0, flags all false). plutus-main reading "10 quiet ops summaries in a row" is signal — system is steady.

---

## The plutus-thesis monitor

Two flavors, same contract:

### Flavor A — Dynamic list (default)

plutus-ops's step 6 above handles ALL theses in `~/active-thesis-monitors.json` during its 30-min sweep. No separate cron. Sufficient for any thesis where 30-min granularity is enough.

### Flavor B — Per-thesis cron (high cadence)

When plutus-main decides a thesis needs faster monitoring (breakout in progress, post-event watch), it spawns a dedicated cron via `cronjob(action='create')`:

```python
cronjob(
    action='create',
    name=f'plutus-thesis-{thesis_id}-monitor',
    schedule='*/15 * * * *',
    repeat=24,
    model={'model': 'deepseek-v4-flash', 'provider': 'opencode-go'},
    prompt=<self-contained: thesis ID, data_points_to_watch, invalidation_rules, action language>,
    deliver='local',
)
```

Both flavors use the same contract:
- Update perception_state for declared DPs
- Record `position_evaluation` per evaluation
- Record observation with `source_tier="thesis_monitor"` only when invalidation rule fires
- Never trade; never override; never expand perception scope
- Recommend exit via `position_evaluations.recommended_action='exit'`; plutus-main decides whether to execute

### `~/active-thesis-monitors.json` shape

```json
{
  "monitors": [
    {
      "thesis_id": 9,
      "position_id": 7,
      "symbol": "BTC",
      "side": "long",
      "strategy_name": "support-hold",
      "data_points_to_watch": ["hl_price", "hl_cvd", "ta_rsi:tf=1h"],
      "invalidation_rules": [
        {"rule": "hl_price < 75200", "action": "exit"},
        {"rule": "ta_rsi:tf=1h > 75 AND hl_cvd_z < -1.0", "action": "exit"}
      ],
      "horizon_ts": 1779700000.0,
      "added_at": 1779000000.0,
      "added_by_session_id": "<main session id>"
    }
  ]
}
```

### Write contention

plutus-main writes (rarely, Phase 6), plutus-ops reads (every tick). Atomic-rename pattern is the approved write path: write to `.tmp`, then `os.rename`. ~10 LOC; no fancy locking. Same pattern as perception_state.json.

---

## Sync contract between tiers

The handshake spec that prevents the tiers from fragmenting.

### Provenance via `structured_tags_json`

Every observation written by ops or thesis_monitor packs provenance into `structured_tags_json`:

```json
{
  "source_tier": "main" | "ops" | "thesis_monitor",
  "source_model": "kimi-k2.6" | "deepseek-v4-flash",
  "tier_session_id": "<isolated session id>",
  "tick_at_unix": <float>
}
```

V1 does not require schema migration. V2-cleanup (post-stabilization) may promote `source_tier` + `source_model` to first-class columns with indexes.

### Ops_summary canonical shape

Every plutus-ops tick ends with one observation, `kind="noticed"`, `structured_tags.summary_type="ops_tick"`, carrying:
- Counts: `predictions_resolved`, `position_evaluations_recorded`, `equity_snapshot_recorded`, `thesis_monitors_evaluated`
- Flags: `drift_detected`, `macro_cache_stale`, `escalation`
- Pending work for main: `pending_reflections`, `weights_pending_update`, `experimental_graduation_candidates`, `thesis_invalidations_flagged`
- Errors: `data_point_errors`

plutus-main Phase 0 client-side-filters `query_observations(kind="noticed")` results by `structured_tags.source_tier` to extract ops summaries.

### Reflection ownership

| Reflection type | Owner |
|---|---|
| Position close (mechanical write) | plutus-ops |
| Loss/win postmortem (interpretive) | plutus-main Phase 1 |
| Prediction resolution (mechanical) | plutus-ops |
| Weight update on resolved prediction (interpretive) | plutus-main Phase 1 |
| Strategy_conviction adjustment | plutus-main Phase 1 (per resolved prediction or closed trade) |
| Strategy weight stability analysis | plutus-main Sunday or on-demand |
| Weekly synthesis | plutus-main Sunday |
| Strategy stage change | plutus-main Sunday or escalation-triggered |
| Experimental graduation | plutus-main Sunday or when ops flags candidate |

**The line:** arithmetic on lifecycle.db = ops. Synthesis on lifecycle.db = main. Strategy file edits (including conviction-engine weight updates AND strategy_conviction updates) = main exclusively. WORLDVIEW.md = main exclusively.

---

## Escalation mechanism

Plutus is autonomous. The operator is NOT in the escalation loop. Escalation is internal: it wakes plutus-main early via a self-scheduled cron.

### Sentinel file (optional, for catastrophic only)

`~/.plutus-agent/escalation.flag` — JSON, set by plutus-ops on catastrophic detection:

```json
{
  "set_at": 1779000000.0,
  "set_by_tier": "ops",
  "set_by_session_id": "...",
  "reason": "near_liquidation",
  "details_md": "BTC long #7, price $74,820, liquidation $74,200, equity $20.50 (drop 14% in 30min)",
  "trigger_observation_id": 1234
}
```

### Wake mechanism: self-scheduled cron

When ops detects an escalation condition, it spawns a one-shot cron firing in ~1 minute:

```python
cronjob(
    action='create',
    name=f'plutus-main-emergency-{int(time.time())}',
    schedule=<now + 60s ISO>,
    repeat=1,
    model={'model': 'kimi-k2.6', 'provider': 'opencode-go'},
    prompt=(
        f"[ESCALATION] {reason}. {details_md}. "
        f"Triggering observation id={obs_id}. "
        f"This is an off-schedule emergency beat — assess, act, exit. "
        f"Next regular beat continues normally."
    ),
    deliver='local',
)
```

The one-shot cron fires via the existing synthetic-injection path. plutus-main wakes within ~1 min, handles, exits. No gateway changes. No operator notification. The escalation.flag file becomes optional metadata for the wake prompt; the cron itself is the wake mechanism.

### What triggers escalation (hard list)

ops should escalate ONLY for:
- **Near-liquidation:** position liquidation price within 1.5× ATR of current
- **Catastrophic equity drop:** >10% loss in single tick
- **SL approaching with conviction collapse:** price within 2× ATR of SL AND latest conviction <0.4
- **Total drift:** lifecycle.db says open, venue says closed (or vice versa) — suggests unrecorded fill or stale state
- **Watcher catastrophic alert:** account_balance_change >20% in 1h, HL position liquidation event detected

Everything else (routine drift, borderline predictions, ambiguous resolutions, normal volatility) waits for the next scheduled plutus-main beat.

### plutus-main handling of escalation beat

1. Read flag contents + triggering observation
2. Take urgent action (close, modify SL, etc.)
3. Atomic-delete the flag
4. Record `reflection_kind="escalation_response"` reflection
5. Defer rest of normal beat (Phases 1-7) to next scheduled regular beat

### Risk: operator asleep + position liquidation between beats

This is a real risk. Mitigations:
- HL native SL brackets are placed on-venue (`place_order` atomic SL/TP), so liquidation protection isn't dependent on Plutus reacting in real-time
- Drawdown-discipline skill caps composite_conviction × multiplier when accumulated drawdown is significant
- Per-strategy `max_size_pct` frontmatter caps strategy_conviction's influence on multiplier

V1 escalation behavior (Telegram alerts to operator) is explicitly removed in V2 per Principle 12 (Plutus drives, operator does not gate).

---

## The skill library

### Updated skill ownership (V2)

| Skill | Status in V2 | Owner |
|---|---|---|
| `heartbeat` | **Deprecated.** Replaced by plutus-main pipeline. Archive as reference. | — |
| `plutus-main` (NEW) | The 8-phase pipeline executed every 7h | plutus-main |
| `plutus-ops` (NEW) | The 30-min bookkeeping cycle | plutus-ops |
| `prediction-factory` (NEW) | Phase 5 of plutus-main: generate 3-10 predictions across three categories | plutus-main |
| `watchlist-scan`, `deep-research`, `anomaly-scan` | **Folded into Phase 3** (wide perception). No longer separate routing branches. | plutus-main |
| `regime-detection` | Phase 2 only | plutus-main |
| `position-monitor` | **Split.** Lightweight (data point fetch + position_evaluation record) → ops + thesis. Interpretive (exit decisions) → main Phase 1 | both |
| `reconcile-and-reflect` | **Split.** Reconcile (compare state) → ops. Reflect (loss-postmortem) → main Phase 1 | both |
| `prediction-tracker` | **Pure ops.** | plutus-ops |
| `loss-postmortem`, `post-trade-reflection`, `pre-mortem` | Main Phase 1 (interpretive) | plutus-main |
| `strategy-author`, `strategy-curator`, `calibration-review` | Main Sunday extras + on-demand graduation | plutus-main |
| `conviction-engine` | **Used by both.** ops calls `compute_conviction` for thesis evaluations. main calls `update_weights` and `compute_conviction` for entry decisions. | both |
| `worldview-discipline` | Main Phase 7 only. Only plutus-main writes WORLDVIEW.md. | plutus-main |
| `drawdown-discipline`, `tilt-detection` | Main pre-Phase-4 guards | plutus-main |
| `consolidate-learnings`, `weekly-review` | Sunday main beat only | plutus-main |
| `hl-risk-placement`, `post-entry-verify` | Main Phase 4 (embedded in trade execution) | plutus-main |
| `macro-cache` | Independent cron (4h cadence), not part of three-tier loop | (standalone) |
| `daily-check-in` | Folded into the daily main beats; standalone cron retired | plutus-main |
| `bootstrap-setup` | Operator-driven, on-demand only | (operator-driven) |
| `add-data-point`, `data-point-audit`, `add-python-deps`, `lifecycle-db-cleanup` | On-demand only | (operator-driven) |

### Key disciplines baked into skill content (V2)

(Most preserved from V1; V2 changes noted.)

1. **Invalidation must be articulated before entry.** `place_order` refuses theses without `invalidation_criteria_json`.
2. **Strategy + regime tagging required on every thesis and prediction.** Without these, calibration can't be sliced.
3. **Conviction must be recorded continuously.** `position-monitor` records a `position_evaluation` each cycle (now: plutus-ops 30min + plutus-thesis 15min when active).
4. **Pre-register predictions ~10× more often than you trade.** V2 increases target to 12-40 predictions/day across 3 categories.
5. **Pre-mortem before high-conviction trades.** Auto-fires when composite_conviction > 0.5 (lowered from V1's 0.7 because the multiplier scales aggressively with composite).
6. **Losses are studied harder than wins, AND categorized.** loss-postmortem mandatory; every loss tags `error_class`.
7. **Drawdown triggers a reflection moment.** Soft circuit breaker, embedded in plutus-main Phase 4 pre-check.
8. **The strategy library is curated, not accreted.** strategy_conviction's slow accumulation handles this automatically; strategy-curator promotes/demotes/retires based on data.
9. **Sunday plutus-main beat runs the full meta cycle:** weekly-review → calibration-review → strategy-curator → consolidate-learnings.
10. **Counterfactuals matter.** `record_observation(kind="almost_traded", ...)` after any setup passed on.
11. **Edge claims are inspectable.** `record_observation(kind="edge_claim"|"edge_revoked", ...)`.
12. **All three tiers must populate perception_state.** (V2) The cache only works if every fetch updates it.

### V2 cost-discipline default

Each plutus-main beat: target ~40-55 tool calls (wide perception is delegated to the spawned plutus-perception sub-agent, which runs on flash). Phase 0+2+7 fixed (~15). Phase 1, 4, 6 conditional. Phase 3 collapsed to reading the perception_digest (~5 calls) + at-decision spot-refresh. Phase 5 bounded. When a beat runs hot, the next beat's Phase 0 detects via the prior summary observation and bounds aggressively (skip experimental predictions, narrow scope).

---

## Cron landscape (current — 2026-06-01)

**Two standing crons only.** Everything kimi-class except plutus-main was removed or moved to flash to fit the $10/mo budget.

| Cron | Schedule | Model | Purpose |
|---|---|---|---|
| `plutus-main` | `0 0,8,16 * * *` (3×/day) | kimi-k2.6 (inherits operator session) | Three-tier orchestrator + the only reasoning tier. Spawns plutus-perception at Phase 0. |
| `plutus-ops` | `*/30 * * * *` | deepseek-v4-flash (cron model-override) | Bookkeeping + monitor sweep + trade-readiness health check. |

Spawned / ad-hoc (not standing crons):

| Job | Trigger | Model | Purpose |
|---|---|---|---|
| `plutus-perception` | spawned by main, Phase 0, each beat | deepseek-v4-flash | Wide fetch sweep incl. macro resolution → writes ONE perception_digest. |
| `plutus-thesis-N-monitor` | spawned by main Phase 6 (Flavor B) | deepseek-v4-flash | Per-thesis high-cadence monitor, auto-expires via `repeat=N`. |
| `plutus-main-emergency-N` | spawned by ops on escalation | kimi-k2.6 | One-shot self-scheduled escalation wake (NEVER an operator ping). |
| `<one-shot future-check>` | spawned by main Phase 6 | deepseek-v4-flash | Scheduled forward checks. |

**Retired (do NOT seed these in OSS setup):**
- `plutus-heartbeat` (V1 hourly) — replaced by the three-tier model.
- `plutus-daily-check-in` (`0 22 * * *`) — folded into the daily main beats.
- `plutus-weekly-review` (`0 18 * * 0`) — folded into the Sunday **16Z** main beat's Phase 7.5 extras.
- `plutus-macro-cache` (`7 */4 * * *`) — **deleted 2026-06-01**; macro resolution folded into the plutus-perception sub-agent (no standalone cron, no macro.json).

Net standing crons: **2** (plutus-main, plutus-ops).

---

## Session lifecycle — what loads when, what persists where

### Session creation paths (V2)

| Trigger | Result | Session ID pattern |
|---|---|---|
| Telegram message arrives, no active session | NEW unified session | platform-assigned |
| Telegram message arrives, active session exists | RESUMES | (same as before) |
| `plutus-main` cron fires | INJECTED into operator's persistent unified session via synthetic message | (same as chat session) |
| `plutus-ops` cron fires | ISOLATED session, fresh per tick | cron-isolated |
| `plutus-thesis-N` cron fires | ISOLATED session, narrow context | cron-isolated |
| `plutus-main-emergency` cron fires | INJECTED into unified session | (same as chat session) |
| One-shot future-check cron fires | ISOLATED session | cron-isolated |
| Watcher daemon emits wake event | Schedules one-shot cron (isolated OR injected depending on event type) | varies |
| Manor chat message from Sebastian | RESUMES unified session (treated as inbound like any chat) | (same as chat session) |
| `/reset` or `/new` in Telegram | Kills active session; next message creates new | (new) |
| `pm2 restart plutus-gateway` | Kills in-flight sessions; chat resumes as new on next message | (new on resume) |

**The unified-session model from V1 is preserved for plutus-main only.** plutus-ops and plutus-thesis are explicitly isolated fresh sessions — they can't carry brain context, can't pollute unified session, can't accidentally read operator's conversation history.

### What loads at the start of each turn (V2)

(Preserved from V1, with V2 additions.) The system prompt is rebuilt at every inbound turn. Within a single `run_conversation` call (one turn) the prompt is fixed and prefix-cached.

| Layer | Source | When loaded | V2 changes |
|---|---|---|---|
| SOUL.md | `~/.plutus-agent/SOUL.md` | Every turn | None |
| WORLDVIEW.md | `~/.plutus-agent/WORLDVIEW.md` | Every turn | Adds `manor_observations`, `tracked_assets`, V2 conviction-related mirrors |
| Strategy library summary | `~/.plutus-agent/strategies/{active,trial,observation}/*.md` frontmatter | Every turn | Adds `strategy_conviction` to each line |
| **perception_state.json snapshot** (NEW V2 — plutus-main only) | `~/.plutus-agent/perception_state.json` | plutus-main turns only | Pass full cache into context for Phase 3 reasoning |
| Hermes built-in memory (MEMORY.md / USER.md) | `~/.plutus-agent/memories/` | plutus-main turns (operator-bound) | plutus-ops/thesis sessions set `skip_memory=True` |
| Holographic memory (when probed) | Holographic plugin SQLite | Lazy | None |
| Tool schemas | `tools/registry.py` | Every turn | Adds new dispatchers (query_unreflected_closes, etc.) |
| Skill prompt block | On `skill_view`/`skill_manage` invocation | On invocation | Adds plutus-main, plutus-ops, prediction-factory skills |

### What gets WRITTEN during a session (effects classified)

| Write | Where | When effect is felt |
|---|---|---|
| Tool results, conversation context | session_db | Current + next turn (chat session continues) |
| WORLDVIEW.md edits | `~/.plutus-agent/WORLDVIEW.md` | NEXT turn (rebuild on next inbound) |
| **perception_state.json updates** (V2) | `~/.plutus-agent/perception_state.json` | NEXT turn for the snapshot; immediately observable to other tier sessions reading it |
| Strategy file amendments | `~/.plutus-agent/strategies/<stage>/<name>.md` | NEXT turn for prompt summary; immediately if read body via `read_file` |
| Strategy stage move | filesystem rename | NEXT turn for prompt summary |
| `record_event` (incl. strategy_conviction_change) | `lifecycle.db` | Permanent; queryable immediately |
| `record_prediction` / `resolve_prediction` | `predictions` table | Permanent; queryable immediately |
| `record_observation` | `observations` table + FTS5 | Permanent; queryable immediately |
| `record_data_point_observation` | `data_point_snapshots` | Permanent; visible to next find-latest-snapshot |
| `place_order` / `close_position` | Atomic write to decisions+trades+positions+outcomes | Permanent; venue immediate |
| `fact_store(action='add')` | Holographic memory SQLite | Permanent across sessions |
| `memory` tool (Hermes built-in) | MEMORY.md / USER.md | Persists; visible at next session start |
| `~/active-thesis-monitors.json` updates (Phase 6) | filesystem | NEXT plutus-ops or plutus-thesis tick |
| `~/.plutus-agent/escalation.flag` write (plutus-ops only) | filesystem | Triggers cron spawn that wakes plutus-main within ~1 min |

### How the strata flow into each other (V2)

```
Within a single plutus-main beat:
  prompt (frozen) ──► reasoning ──► tool calls ──► writes
                                                    │
                                                    ├──► lifecycle.db (immediately queryable mid-session)
                                                    ├──► perception_state.json (immediately readable by other tiers)
                                                    ├──► WORLDVIEW.md  (effect: NEXT plutus-main beat OR operator turn)
                                                    ├──► strategy files (effect: NEXT session for summary; mid-session if read body)
                                                    ├──► active-thesis-monitors.json (effect: NEXT ops/thesis tick)
                                                    ├──► holographic facts (immediately probable)
                                                    └──► memory store (effect: NEXT session)

Within a single plutus-ops tick (isolated session):
  prompt (rebuilt fresh) ──► reasoning ──► tool calls ──► writes
                                                            │
                                                            ├──► lifecycle.db (predictions resolved, position_evaluations, observations)
                                                            ├──► perception_state.json (refreshed DPs for monitored items)
                                                            └──► escalation.flag (only on catastrophic)

Between sessions (any cadence):
  Last beat's writes ──► next session's frozen prompt or cache state
                         (SOUL identity, WORLDVIEW synthesis, strategy library summary, perception cache, manor digest)

  Lifecycle DB           ──► Always queryable (no frozen snapshot — direct read)
  Holographic            ──► Probable on demand (no frozen snapshot)
  Observations           ──► Compounded into WORLDVIEW.md by worldview-discipline + consolidate-learnings
                             (raw stream queryable; synthesis frozen at next session start)
  Reflections            ──► Surfaced into next session via find_similar_reflections semantic search
  Manor observations     ──► Compounded into WORLDVIEW.manor_observations mirror + holographic memory
                             with entity-tagged Sebastian-as-source
```

---

## Self-improvement — three modes, all from the same data

(Preserved from V1.) The lifecycle schema enables three modes of self-improvement, all from the same underlying records:

1. **Pattern matching on own history (works from trade #1).** `find_similar_theses` and `find_similar_reflections` — hybrid FTS5 + voyage-finance-2 vector retrieval, fused via RRF, summarized by a fast LLM. Semantic paraphrase capture means "crowded longs" matches "elevated open interest" even on first trade.

2. **Statistical insight (works after ~20 trades).** Aggregations: "my long-side BTC trades have R = 1.6, my short-side R = -0.3." Pure SQL over `outcomes`. `query_performance`, `query_equity_curve`, `query_performance_attribution`.

3. **Quantitative analysis (sample size warrants).** A researcher pulls a feature/target dataframe in a few lines of SQL because every outcome FK-traces to thesis text, embeddings, conviction trajectory, strategy_conviction history, and snapshot values. Schema is ML-ready by construction.

**V2 addition:** strategy_conviction history (via `strategy_conviction_change` events) joined with outcomes enables a fourth analysis mode: **strategy-level performance trajectories.** "Show me each strategy's strategy_conviction over time vs realized R-multiple per trade." Reveals whether strategy_conviction is well-calibrated as a forward predictor of trade performance.

All four modes operate on the same data from day 1. We don't choose one. Capability deepens as Plutus's history grows.

---

## Trading paths

(Preserved from V1.) Two paths share the same trade tools. Difference is purely whether Plutus participates in the dgclaw leaderboard.

### Path A — HL trading, no dgclaw participation

```
1. acp configure                      # OAuth login → OS keychain
2. acp agent create --name Plutus
3. acp agent add-signer               # P256 keypair → OS keychain
4. Fund agent ACP wallet → bridge to HL
5. npx tsx scripts/activate-unified.ts  # required for perp trading
6. npx tsx scripts/add-api-wallet.ts    # produces HL_API_WALLET_KEY in .env
7. Plutus trades via execution toolset
```

### Path B — HL trading + dgclaw leaderboard

```
1-6. Same as above
7.   ./scripts/dgclaw.sh join          # registers, ~6 USDC min
8.   Plutus trades + competes
```

Tokenization is no longer required for Path B (Virtuals May 2026 announcement).

---

## Tech stack — concretely

**Zero new database services beyond V1. perception_state.json is a JSON file. Same SQLite + markdown + Python + Node CLIs (opt-in).**

| Component | Tech | V2 status |
|---|---|---|
| Identity store | `SOUL.md` (markdown) | Unchanged |
| Worldview store | `WORLDVIEW.md` (markdown + YAML frontmatter) | Extended frontmatter |
| **Perception cache** | `perception_state.json` (JSON, atomic-rename writes) | **NEW V2** |
| Memory store | Hermes `memories/` (SQLite) + Holographic plugin SQLite | Unchanged |
| Lifecycle store | `lifecycle.db` (SQLite) | Schema unchanged; structured_tags conventions new |
| FTS5 over text | Built into SQLite | Unchanged |
| Vector similarity | `sqlite-vec` + voyage-finance-2 | Unchanged |
| Data point registry | Python decorator + dict | Adds `staleness_s` field |
| Event registry | Python decorator + dict | Adds `strategy_conviction_change` event type |
| Venue/account/alert/identity registries | Python decorator + dict | Unchanged |
| Watcher daemon | pm2 process `plutus-watchers` | Unchanged |
| Cron / scheduling | Hermes `cronjob` tool | Same tool; V2 adds plutus-main + plutus-ops standing crons |
| Gateway / chat | Hermes gateway under pm2 | Unchanged; injects plutus-main into unified session as before |
| Trade execution | `hyperliquid-python-sdk` | Unchanged |
| ACP/dgclaw | Subprocess wrappers + vendored skills | Unchanged |

---

## File / directory layout (V2 updates)

```
~/.plutus-agent/                       # Plutus instance
├── SOUL.md                             # Stratum 0
├── WORLDVIEW.md                        # Stratum 1 — extended frontmatter (V2)
├── perception_state.json               # Stratum 1.7 — NEW V2
├── active-thesis-monitors.json         # Cross-tier thesis monitor list — NEW V2
├── escalation.flag                     # Catastrophic-only sentinel — NEW V2 (optional)
├── strategies/
│   ├── active/ trial/ observation/ proposed/ retired/
├── memories/                           # Stratum 2 (Hermes + holographic)
├── lifecycle.db                        # Stratum 3
├── learnings_archive.md
├── state.db                            # Hermes runtime infra
├── sessions/                           # Hermes session logs
├── skills/                             # Plutus's skills
├── cron/                               # Plutus's scheduled jobs
├── logs/
├── watcher_state.json                  # Alert throttle state
├── perception_state.json               # Perception cache (incl. macro, written by plutus-perception)
├── config.yaml
└── .env

~/plutus-agent/                # Repo (template; OSS-publishable)
├── agent/
│   ├── lifecycle_db.py                 # schema, connection, write helpers
│   ├── worldview_loader.py             # loads WORLDVIEW.md into context
│   ├── perception_cache.py             # NEW V2: read/write perception_state.json
│   └── ...
├── plutus_cli/
│   ├── default_worldview.py            # DEFAULT_WORLDVIEW_MD (V2: extended frontmatter)
│   ├── default_perception_state.py     # NEW V2: DEFAULT_PERCEPTION_STATE (empty cache shape)
│   └── ...
├── tools/
│   ├── core/                           # registries + embeddings
│   │   ├── data_point_registry.py      # V2: staleness_s field added
│   │   ├── event_registry.py
│   │   ├── venue_registry.py
│   │   ├── account_registry.py
│   │   ├── alert_registry.py
│   │   ├── identity_registry.py
│   │   └── embedder.py                 # voyage-finance-2 client
│   ├── dispatchers/                    # agent-facing registry-backed tools
│   │   ├── fetch_data_point.py         # V2: cache-check before fetch
│   │   ├── place_order.py              # V2: composite_conviction → multiplier sizing
│   │   └── ...
│   ├── lifecycle/                      # direct lifecycle query tools
│   │   ├── query_unreflected_closes.py # NEW V2: JSON1 json_each
│   │   └── ...
│   ├── integrations/                   # per-source registrations
│   │   ├── hyperliquid/ flow/ ta/ coingecko/ defillama/ macro/ gas/ acp/ dgclaw/
│   │   └── news/                       # FUTURE V2 addition: news_sentiment data points
│   └── ...
├── watchers/                           # alert daemon
├── skills/
│   └── trading/
│       ├── plutus-main/SKILL.md        # NEW V2: 8-phase pipeline
│       ├── plutus-ops/SKILL.md         # NEW V2: 30-min bookkeeping
│       ├── prediction-factory/SKILL.md # NEW V2: Phase 5 prediction generation
│       ├── conviction-engine/          # used by both main and ops
│       ├── strategy-author/ strategy-curator/ calibration-review/
│       ├── prediction-tracker/         # now pure ops
│       ├── position-monitor/           # split semantics
│       ├── reconcile-and-reflect/      # split semantics
│       ├── loss-postmortem/ post-trade-reflection/ pre-mortem/
│       ├── regime-detection/
│       ├── worldview-discipline/
│       ├── drawdown-discipline/ tilt-detection/
│       ├── consolidate-learnings/ weekly-review/
│       ├── macro-cache/                # standalone cron
│       ├── bootstrap-setup/
│       ├── heartbeat/                  # DEPRECATED; archive after V2 stable
│       └── ...
├── ecosystem.config.js
└── ...
```

---

## V2 migration sequence — concrete steps

### Ship-blockers (must land before V2 enables)

| # | Blocker | Priority | Notes |
|---|---|---|---|
| 0 | Root-cause 232 NULL session_id observations | **P0** | Provenance contract depends. ~30 min fix. |
| 1 | Cron model override smoke test | **P0** | Verify `model={"model":"deepseek-v4-flash"}` actually routes. ~5 min test. |
| 2 | `query_unreflected_closes` dispatcher (JSON1 safe) | **P1** | ~20 LOC in tools/lifecycle/ |
| 3 | Escalation wake mechanism (self-scheduled cron path) | **P1** | No gateway changes needed; ops calls cronjob directly |
| 4 | Skill stubs (plutus-main, plutus-ops, prediction-factory) | **P0** | Required before either cron can run |
| 5 | perception_state.json read/write helpers + dispatcher integration | **P0** | `agent/perception_cache.py` + `fetch_data_point` cache integration |
| 6 | `place_order` composite_conviction → multiplier sizing | **P0** | Replace V1 sizing logic |
| 7 | `strategy_conviction` frontmatter field + update logic | **P0** | conviction-engine skill extension |

### Order

1. Land all P0 ship-blockers (#0, #1, #4, #5, #6, #7)
2. Land P1 (#2, #3)
3. Rewrite PLUTUS.md as canonical V2 (this document) ✅
4. Pause plutus-heartbeat
5. Create plutus-ops, run solo for 6h, inspect ops_summary observations, confirm no forbidden actions taken
6. Create plutus-main at 3×/day (00,08,16 UTC) + plutus-ops at */30; first main beat does cold-start
7. Delete plutus-daily-check-in + plutus-weekly-review (folded into main)
8. First plutus-thesis Flavor B cron spawned when position warrants
9. One week of observation — cost, calibration accumulation, escalation reliability
10. Decide: ship V2 as OSS default OR keep V1 hourly heartbeat as OSS default + V2 as advanced setup

---

## Elegance test — the V2 bar

If we hit these, the architecture is right. If we miss any, we got it wrong somewhere.

(Updated from V1.)

1. **Adding a new data point** = one entry with `staleness_s` declared. Plutus discovers it; cache integrates; auto-snapshots. No tool surface change.
2. **Adding a new venue** = one folder under `tools/integrations/<venue>/`. `place_order(venue=...)` dispatches. No changes to multiplier sizing.
3. **Adding a new account** = one entry. Holdings/equity/capital movements work automatically.
4. **Adding a new event type** = one entry. `record_event(type=...)` dispatches.
5. **Adding a new alert** = one entry. Watcher picks up on restart.
6. **Adding a new tracked_asset** = update `WORLDVIEW.tracked_assets` list (plutus-main can do this during Phase 7). Perception cache auto-extends.
7. **Authoring a new experimental hypothesis** = `record_prediction(strategy_name="experimental-<name>", ...)`. No file. Calibration accumulates.
8. **Graduating an experimental to a real strategy** = `strategy-author` writes file in `proposed/`, promotes to `observation/` with `strategy_conviction=0.2`.
9. **Promoting a strategy through stages** = `strategy-curator` moves file + updates strategy_conviction. No operator approval.
10. **Every closed trade has a reconstructable causal chain** end-to-end. Plus V2: every strategy_conviction value at any point in time is reconstructable from `strategy_conviction_change` events.
11. **A researcher pulls a feature/target dataframe in 5 lines of SQL** because every outcome FK-traces to thesis text, embeddings, conviction trajectory, strategy_conviction history, regime, and snapshot values.
12. **Plutus answers "have I done this semantically before?"** with one tool call (`find_similar_theses`).
13. **Plutus answers "am I growing capital?"** with one tool call (`query_equity_curve`).
14. **Plutus answers "is my conviction calibrated?"** with one tool call (`query_calibration`).
15. **Plutus answers "which experimentals are ready to graduate?"** with one query (`query_calibration` over `experimental-*` strategy_names).
16. **Plutus answers "what does the current market look like across all tracked assets?"** by reading perception_state.json (no tool calls; in prompt for plutus-main beats).
17. **Tool surface stays at ~20 dispatchers** even as registries grow to hundreds of entries.
18. **plutus-main beat completes in ~100 tool calls** (P0 bounded; P95 ≤120).
19. **plutus-ops tick completes in ~5-8 tool calls** for quiet ticks.
20. **Swapping embedding provider** is a config change + a re-embed script.

---

## What's locked vs open

### Locked

**Cognitive architecture (V1-preserved)**
- Four-stratum model + V2's Stratum 1.5 (strategies) + V2's Stratum 1.7 (perception cache)
- WORLDVIEW.md as cross-session bridge with frozen-snapshot pattern
- Holographic plugin as Plutus's external Hermes-memory provider
- Conviction REQUIRED on every decision; conviction as trajectory via position_evaluations
- Invalidation criteria REQUIRED on theses driving open decisions
- Strategies as files, not table rows
- Voyage-finance-2 for embeddings; sqlite-vec for storage; FTS5+vector hybrid via RRF

**V2 operating model**
- Three tiers (plutus-main / plutus-ops / plutus-thesis) with hard perception scope contracts
- plutus-main at 3 beats/day (00, 08, 16 UTC) on kimi-k2.6
- plutus-ops at 30-min cadence on deepseek-v4-flash
- plutus-thesis: dynamic-list (Flavor A) by default, per-thesis cron (Flavor B) on demand
- Sync contract via `structured_tags_json` provenance (no schema migration for v1)
- ops_summary as canonical handshake; brain Phase 0 client-side-filters for source_tier
- Escalation via self-scheduled cron; operator NOT in escalation loop
- 8-phase plutus-main pipeline (always runs all 8, each phase short-circuits)

**Conviction architecture (V2)**
- Two-dimension conviction: strategy_conviction × thesis_conviction
- Position multiplier: 20^composite (1x to 20x, exponential)
- Cross-portfolio conviction-ranked allocation
- One strategy per asset for capital (no preemption in v1)
- Strategy_conviction update: α=0.02 per resolved prediction, α=0.05 per closed trade; brain decides direction

**Perception cache (V2)**
- `~/.plutus-agent/perception_state.json` as the cache surface
- Per-DP staleness budgets declared in registry
- All three tiers write the cache opportunistically (based on what each fetches for its purposes)
- Atomic-rename write pattern
- Cache and snapshot are distinct concerns (cache short-circuits fetch; snapshot still writes for ML chain)
- Principle 4 revised: "perception cache with per-DP staleness budgets" replaces V1's "no caching"

**Prediction factory (V2)**
- 3-10 predictions per plutus-main beat across three categories (existing, experimental, regime-stress)
- Target 12-40 predictions/day
- Experimental strategies have no file; live only as predictions tagged `experimental-<descriptor>`
- Graduation asymmetric: revoke at N≥20 with <30% calibration across 2+ regimes; promote at N≥10 with ≥55% calibration to `observation` with strategy_conviction=0.2

**External perception (V2)**
- Sebastian + Nightingale Manor as peer-AI context source
- Manor messages flow as inbound to unified session; recorded with `kind="operator_input"` + `structured_tags.source="sebastian"`
- WORLDVIEW.md gains `manor_observations` mirror
- `consolidate-learnings` extracts Sebastian-tagged facts into holographic memory

**Tool surface architecture**
- Six registries (data points, events, venues, accounts, alerts, identity systems)
- Function-shaped toolsets (perception, execution, reflection, identity)
- Trade tools ungated
- Capital-moving ops auto-record `capital_movement` events
- `place_order` requires `invalidation_criteria_json` on linked thesis (code-enforced)
- HL native SL/TP brackets via `bulk_orders(grouping="normalTpsl")`

**Trading paths**
- Path A (HL only), Path B (HL + dgclaw), share trade tools
- Tokenization NOT required for Path B
- ACP required for both paths in v1

**Repo / instance split**
- plutus-agent (repo) ships infrastructure + skill skeletons + Hyperliquid integration
- Plutus (instance) owns identity, worldview content, perception cache, strategy library, lifecycle history, learnings, self-modified skills, Manor chat membership

### Open

- **News sentiment data point integration** — `tools/integrations/news/data_points.py` not yet built. Will add when post-V2-stable surfaces a clear need.
- **On-chain analysis data point integrations** — Etherscan/glassnode/dune backed; future.
- **Cross-exchange funding sentiment** — Binance/Bybit/OKX divergence as edge signal; future.
- **SPX perp liquidity verification on HL** — needs operator-driven research/testing before SPX position sizes >$50 notional are deployed.
- **Operator-routine query routing to ops session** — when operator asks "equity?", route to fresh flash session for cheap factual answer. v1.1.
- **Strategy preemption** — close existing position to fund higher-conviction new opportunity. v2 (not v1) decision; needs live allocation data.
- **Cross-asset class regime modeling** — SPX vs BTC regime detection nuance. Will evolve as Plutus accumulates SPX data.
- **Sebastian's specific message format and routing nuances** — operator+Sebastian+Plutus chat semantics under load; need observation before formalizing.
- **Promoting `source_tier` to first-class column** — V2 packs into structured_tags_json; schema migration for v2-cleanup if three-tier proves itself.

---

## Connections to other docs

- **README.md** — what plutus-agent is, quick start
- **TRADING.md** — canonical trade-execution mechanics: wallets, registration, the native path, the readiness check
- **AGENTS.md** — development guide for contributors (and AI coding assistants)
- **LINEAGE.md** — where this project came from (hermes-agent fork, clean-cut rebrand, attribution)
- **PLUTUS.md (this file)** — the agent's mind: cognitive architecture, lifecycle, three-tier execution model, conviction architecture, self-improvement loop, vision

This is the document we return to when "how does this fit?" is the question. V2 is built around three intertwined ideas: tier the execution to separate thinking from bookkeeping, cache perception to make wide context affordable, split conviction into strategy + thesis to make graduation safe. Everything else is execution discipline around these three structural commitments.

---

*This is the V2 vision. The implementation lives in the skills, dispatchers, and registries of this repo. But the **why** lives here.*
