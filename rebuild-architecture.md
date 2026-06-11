# Plutus Rebuild — Architecture (working design)

*Living spec for a full rebuild of Plutus at the OSS layer (`plutus-agent` repo), then a fresh runtime spun up on the new architecture — calibration and learning start from zero. Old runtime stopped 2026-06-09 (`pm2 stop plutus-gateway plutus-watchers`; data preserved at `~/.plutus-agent/` for reference). This document hands off to a fresh CC session inside the repo when the design settles. Captured from Sev's design session(s), 2026-06-09.*

---

## 0. Why we are rebuilding (the diagnosis)

The old runtime didn't fail at trading judgment — it failed at runtime architecture. The 2026-06-09 transcript: the operator session **never resets** (`session_reset.mode: none`, verified), so it accumulated to ~156K tokens; `plutus-main` did all heavy reasoning **inline** in that never-resetting session; deepseek-v4-pro is verbose; the compaction threshold (0.5) plus a flaky 60s flash summarizer produced a **compaction death spiral** — ~18 minutes of cold-start thrashing before a beat could even begin, ending in the 1800s cron timeout. The beat still completed (position opened, predictions resolved) because the durable layer carried the real state — proof the conversation buffer was dead weight.

Structural messes underneath: the mega `plutus-main` skill loaded a dozen other skills **reactively mid-reasoning** (unreliable — the source of the bad bracket and wrong-thread posts); too many steps in one flow; the watchers/alert system was built, tested once, then abandoned (it had no consumer in a mega-beat model); a position's SL was never placed on-chain and ops could *detect* the breach but had no way to *act*.

**Conclusion:** patching won't fix a topology problem. Rebuild clean. Timing is right — 36-day-old runtime, equity ~$35, thin and unconverged calibration, so there's little sunk cost. Do it now, before learning compounds on a distorted architecture.

---

## 1. Core principles

- **Orchestrator, not doer.** `plutus-main` is a thin **persistent daily session** (rolled once per day, modelled on Sebastian's HB1 roll), holding a compact "book." All heavy work is delegated to **ephemeral specialist subagents** that return concise output and discard their own context. The persistent layer stays thin because it never reasons heavily — it schedules, reviews, and holds state.
- **Star topology + blackboard.** The orchestrator is the *only* spawner. (NOTE 2026-06-10: the old `max_spawn_depth: 1` guarantee lives in `tools/delegate_tool.py` — demolition DEFERRED its deletion because the kept `skills/software-development/` skill is built on `delegate_task`; it gets deleted during construction once that skill is reworked/retired. Either way the rebuild's spawn mechanism must own the no-nesting invariant itself, enforced by **toolset omission**: subagents are simply never granted the spawn tool. Make this explicit in the new spawn design; do not rely on the legacy depth counter.) Subagents never spawn each other and never re-derive each other's work; they communicate through **shared files**. This dissolves the sub-sub-agent question entirely.
- **Two orthogonal axes structure the desk:**
  - **decide / do / will** — who computes the edge, who executes it, who allocates.
  - **forward / backward** — `predict` looks forward (what will happen, what to do); `reflect`/`strategy` looks backward (what happened, what to learn). **Never mix them in one agent.**
- **The strategy/hypothesis spec is the single unit of truth.** Nobody re-derives an entry. The edge is computed in exactly one place (`predict`); everything else inherits or executes it. This is what makes the prediction track a valid estimator of trade performance — no second framework to drift from.
- **Durable layer is the memory; conversation is ephemeral.**

---

## 2. The trading desk (roster)

The architecture is a professional trading desk, not an assistant bot. Each role decides the one thing it is expert in, exactly once.

*(Names + roles locked 2026-06-09, second session. Seven agents. Every agent is an LLM agent — an agent = a context recipe + a tool reach; purely-code "agents" don't exist, they're tools.)*

| Agent | Desk role | Axis | Model |
|---|---|---|---|
| `plutus-main` | Portfolio manager — allocates, orchestrates, holds the book. **The desk's voice**: writes the ledger, makes the lifecycle.db event entries, writes the Arena forum posts (rationale = its own allocation reasoning) | will | deepseek-v4-pro |
| `plutus-predict` | Analyst desk — evaluates, calibrates conviction, ranks setups, sets invalidation + risk tolerance. Owns the **narrative support-score LLM reasoning** (§8) — it holds the strategy context | decide / **forward** | deepseek-v4-pro |
| `plutus-trade` (merged with `execute`) | Execution trader — risk, sizing, volatility SL, places & closes, writes the thesis | do | deepseek-v4-flash |
| `plutus-reflect` | Quant research — backward analysis, reweights data points, promote/demote. (Name locked: `reflect`, not `strategy`) | learn / **backward** | deepseek-v4-pro (provisional — weight tuning may degrade on flash) |
| `plutus-ops` | Back office — maintenance, prediction resolution, deterministic position/invalidation monitoring, staleness watchdog. **Escalates narrative-sensitive or borderline situations to a fresh `predict` spawn** (resolves §11) | autonomic | deepseek-v4-flash |
| `plutus-perception` | Gathers market data → PERCEPTION.md (deterministic fetching + LLM-needed news/sentiment gathering) | support | deepseek-v4-flash |
| `plutus-regime` | Assesses regime per timescale → REGIME.md; reads PERCEPTION.md | support | deepseek-v4-flash |

- `plutus-state` **dissolved** — account state is an `account_state` tool that main and ops call, not an agent.
- **Possible 8th agent, `plutus-scribe` [OPEN — lean no for now]:** to keep main's recording duties context-light, start with a consolidated `record()` **tool** instead — main hands it the narrative once plus a few fields; the tool fans out to lifecycle.db + ledger + forum as appropriate, so main never holds schemas/thread-IDs/formatting in context. Promote to a scribe agent only if the tool proves insufficient (topology unaffected).

---

## 3. Memory surfaces (files)

- **PLUTUS.md** — the single constitution **and** live-state file. Replaces *both* SOUL.md and WORLDVIEW.md. `plutus-main` reads it automatically at the start of the day. Contents: self-architecture / doctrine (the way Sebastian's CLAUDE.md carries the manor charter); the north star; account state + snapshot timestamp (so age is always known); current regime; the subagent roster; the scheduling system; current strategies + status + stats; the daily ledger (Sebastian pattern); orchestration instructions for running the day.
  - Zoning [proposed]: **one zoned file** — doctrine (stable: identity, principles, roster, orchestration) + a clearly-marked `## Live State` section that only tools rewrite (account snapshot + ts, regime, open position, strategy status). Main reads ONE file at day-start.
  - **Ledger = daily files (locked 2026-06-09):** two sibling layers under `~/.plutus-agent/ledger/`. (a) **Journal** `2026-06-10.md` — main's narrative; end-of-day entry triggered by an injected EOD message ("how the day went + observations"), plus notable intraday events. (b) **Transcripts** `2026-06-10/` — per-agent runtime logs (every prompt received, every message sent, every tool call + result), file-named by session. Journal references transcripts; together they are the audit trail — the journal is the readable layer, transcripts the verifiable layer. **Session naming:** main's persistent daily session is `2026-6-10-a`; same-day restart → `2026-6-10-b`, etc. Transcript files carry the session name.
- **REGIME.md** — the regime taxonomy (types of regime) + a standard assessment philosophy. Maintained by `plutus-regime`.
- **PERCEPTION.md** — every data point with its value and timestamp. Written by `plutus-perception` on a run; read by all other agents; staleness triggers a refresh. The shared market-data blackboard — the thing that lets agents avoid spawning perception as a child.
- **lifecycle.db** — append-only event log. **Predictions are the first-class spine (locked 2026-06-09): it is the lifecycle of PREDICTIONS, some of which become trades** — no longer a trade lifecycle with predictions on the side. The chain: prediction → (funding/graduation) → thesis (cites `prediction_id`) → decision → trade → position → outcome. Resolution + calibration happen uniformly at the prediction level. Narrative support-score reasoning (§8) is stored in lifecycle.db keyed to (prediction, data point) — score + recorded reasoning — so reflect can ask "which narrative reads were predictive?". Write ownership: `plutus-main` writes events from subagents' structured returns; `plutus-predict` writes its own predictions; `plutus-ops` resolves predictions. Blackboard files are written by their producer.

---

## 4. The daily flow

0. **`plutus-main` reads PLUTUS.md** (auto, start of day) — reconstitutes the book.
1. **`plutus-state`** → account state; main writes it + timestamp into PLUTUS.md.
2. **`plutus-perception`** → refreshes PERCEPTION.md (standard panel, or a targeted data-point list).
3. **`plutus-regime`** → regime **per timescale** (macro/market-cycle with asset-class modifiers · last month · last ~24h); maintains REGIME.md; reads PERCEPTION.md.
4. **`plutus-predict`** (the forward brain) → evaluates all 10 slots against PERCEPTION.md + REGIME.md, emits a prediction per slot, sets each prediction's invalidation criteria + risk tolerance, generates new hypotheses to fill empty slots, and surfaces the **best actionable setup** (highest-conviction *graduated* strategy that fired).
5. **`plutus-main` decides** — among predict's actionable candidates, which (if any) to fund, given the book, risk budget, and the conviction threshold. Wait / schedule / execute.
6. **`plutus-trade+execute`** (the hands) → if funded: derive the volatility-based SL, size by conviction, place on Hyperliquid, write the thesis. Also handles closes when an exit signal routes through main.
7. Main schedules the next wake (time / price alert / data-point watch) and/or relies on ops + watchers.

`plutus-strategy/reflect` and `plutus-ops` run on their own cadences (below), not in the entry flow.

---

## 5. Hypothesis → Strategy model

- **predictions ↔ hypotheses** (cheap, zero-capital discovery); **trades ↔ strategies** (capital-at-risk commitment). Coupled by being the *same object at different lifecycle stages*: a hypothesis is a proto-strategy; graduation flips its "may trade" bit, it does not hand the object to a different framework.
- **10 slots** — a mix of active and trial strategies plus test hypotheses, spanning timescales, filtered by current regime. A *test* hypothesis has <10 resolved predictions; an *active* hypothesis has 10+ at >50% win rate. Checkpoint every 10 resolved: <50% → discontinue; ≥50% → continue (20, 30, …). Enough correct predictions → graduate to a tradeable strategy. `plutus-predict` keeps predicting for graduated hypotheses too, AND tests brand-new ones. **Its job is to keep all 10 slots full** → continuous discovery (new data-point combinations, variations of winners).
- **`plutus-trade` only acts on graduated/active strategies. `plutus-predict` evaluates everything.** Pure test hypotheses throw off calibration but are not trade candidates.

### The matrix is the DISCOVERY SPACE, not the CALIBRATION GRID

Regime × timescale × strategy is the *space we sample from*, not a dense grid we must fill. A hypothesis is one point in it. The 10 slots are active probes. **Calibration stays per-hypothesis (1-D, well-fed)** — and because each hypothesis embeds its regime + timescale, per-hypothesis calibration *is* matrix-aware, while only ever maintaining ~10 well-fed cells instead of a sparse field of empty ones. This is the reconciliation with the existing engine's hard-won caution: it deliberately rejected regime-sliced weight matrices (2026-06) because they fragment sample size. Make **timescale a first-class field** on predictions (bucket the horizon: intraday / intraweek / longer) so predict can *enforce a mix* rather than clustering at the current 8–12h default.

---

## 6. Predictions, trades, theses — the unification

The prediction track only has value as a **capital-free estimator of the trade decision.** If predict and trade used different logic, calibration would transfer nothing and graduation would be meaningless. So they share the decision, not the agent:

- A **prediction** is the calibrated entry decision recorded with no capital — plus its invalidation criteria and a **risk tolerance** (low/med/high).
- A **trade** is that exact prediction, executed: the same edge, now carrying sizing, SL, and capital.
- A **thesis** is a *funded prediction* — the same claim, plus the execution envelope and invalidation, citing the prediction it came from so calibration traces straight through.
- **Unify the entry edge; keep execution separable.** Prediction calibration estimates the *entry edge*, not the full P&L. A trade adds execution (sizing, SL, slippage), measured on its own in `outcomes`. Prediction-correct means "the edge was real," not "the trade made money." (#41 on 2026-06-09: right-ish entry, botched execution — the missing stop, not the thesis, lost the money.)

---

## 7. Invalidation vs. Stop-Loss (two different exits)

These are **independent** and must not be conflated:

- **Invalidation** = the *basis of the strategy has fallen apart* — a fundamental thesis break, not mere price action. Criteria must be **seriously strong**. Set by `plutus-predict` on each prediction.
- **Stop-loss / risk** = volatility-based capital protection against an adverse price move *while the thesis is still alive*. Derived by `plutus-trade` from historical price volatility for the prediction's timescale + current price, **scaled by the prediction's risk tolerance**.

Why both: a prediction ("BTC hits X within 24h") can resolve **correct** even after a 12-hour adverse excursion that a tight SL would have stopped out. So risk tolerance per prediction calibrates how much adverse excursion to tolerate before cutting — a separate question from whether the thesis is broken. Exit on **invalidation** (thesis dead) OR on **SL** (risk limit hit), whichever comes first.

---

## 8. Conviction (the model — a departure from today's engine)

Conviction is **not fully deterministic.** Two kinds of data point:

- **Numerical** (hl_price, hl_cvd, ta_rsi, ta_atr, …) → deterministic normalisers map the reading to a support score (today's engine handles these).
- **Narrative / freeform** (news, macro narrative, sentiment) → **no clean numerical form.** An LLM must reason, *in the context of the specific strategy*, whether the data point supports or invalidates it, and output a 0–1 support score. **That reasoning must be recorded** (auditable). We do not have this today; the rebuild needs it.

**Scoring & normalisation:**
- Every (data point + strategy) gets a **0–1 score** for whether it supports or invalidates that strategy.
- Conviction = the strategy's **weight-normalised aggregate** of its data points' support scores (weighted by per-data-point weight, normalised by the count/weights involved).
- *Example:* 10 equally-weighted data points, 5 supporting → conviction 0.50. If one *supporting* data point carries a higher weight → conviction > 0.50, even though still only half the data points support.
- **Normalise across all strategies** so the 0–1 scale means the same thing everywhere → a **single global conviction threshold** becomes valid (replaces today's per-strategy thresholds; e.g., a trade call requires the best actionable conviction to clear the global bar).

This is a deliberate change from the current sigmoid + baseline-anchor engine toward a straight, interpretable weighted-average of support scores.

---

## 9. Calibration / weight tuning (`plutus-strategy` / `plutus-reflect`)

The backward-looking agent. Over the recent history/chain (e.g., the last week) it asks: **which data points at what level of support led to correct outcomes? Which at what level of invalidation led to failures?** It gives the more predictive data points more weight, so conviction is tuned over time, and normalises across strategies so the global threshold holds. Eventually this gains a machine-learning component over the conviction curve. It owns promotion/demotion. It is **separate from `plutus-predict`** — forward and backward are different cognitive modes; one agent doing both mixes tasks.

---

## 10. Orchestration control — waking, staleness, stream-crossing

**The "dead day" failure mode.** Risk: main finishes a beat without scheduling a next wake, then goes quiet all day. Guards:
- Every action *type* (state, regime, predict, trade-assessment, reflect) carries a **last-run timestamp + staleness factor** (max hours before it must run again). lifecycle.db already timestamps events; ops lifts this into a "last run per action type" view.
- **Ops is a watchdog, not just a recorder.** The 30-min ops tick checks each type against its staleness factor; anything overdue → ops **wakes plutus-main** by injecting a synthetic message (same mechanism as a Telegram message from Sev). The activity floor is guaranteed independent of whether main remembered to schedule itself.

**Four wake sources:** (1) self-schedule, (2) Sev messaging, (3) ops staleness trigger, (4) watchers/alerts. Keep watchers — they're real-time and deterministic on price/data-point events, which a 30-min poll can't match.

**Stream-crossing problem:** never fire two wakes at once. Need a **single serialised wake queue / lock** — triggers *enqueue*; the orchestrator drains one turn at a time, so a watcher and an ops-staleness in the same minute collapse into one turn, not two racing sessions. [DESIGN]

---

## 11. Monitoring open positions

Only ever **one position at a time** (see Constraints), but up to **10 active predictions** to monitor for invalidation. A **single fresh perception run across all data points** resolves this neatly — refresh PERCEPTION.md once, then check every prediction's invalidation criteria and the open position's SL against it.

**[RESOLVED 2026-06-09, second session] Who computes the ongoing conviction curve / does the monitoring?** Tiered: `plutus-ops` (frequent, cheap, every 30 min) does the *deterministic* majority (numerical invalidation, SL breach) and **escalates to a fresh `plutus-predict` spawn** only on narrative-sensitive or borderline situations — mirroring the watchdog-wakes-main pattern.

---

## 12. Constraints (locked)

- **BTC only is a calibration phase, NOT architecture** (~first week, to get things running smoothly). Nothing in the schema or agents hardcodes one symbol — the watchlist is config, chosen in the setup wizard (**new wizard step: pick initial watchlist, max 3 symbols for now**; Sev will pick BTC-only at setup). Expansion beyond the initial watchlist is a `plutus-reflect` promotion decision later.
- **One position at a time — mandatory.** Hyperliquid uses cross-margin; there is no way to hold multiple positions on the same symbol. Main picks the single best actionable candidate, and that setup's timescale sets how far out main schedules the next look.
- Calibration starts from zero.

---

## 13. Current system baseline (as-built — for the repo handoff)

**lifecycle.db** (row counts 2026-06-09, old runtime): data_point_snapshots 5227, observations 1344, predictions 373, position_evaluations 33, reflections 29, theses 14, trades 8, decisions 6, positions 4, strategies 4, outcomes 4, capital_movements 0. Plus FTS5 + sqlite-vec mirrors.

- `predictions`: id, ts (made), horizon_ts (resolve-by), symbol, claim_md, success/failure_criteria_json, conviction, strategy_name, regime_tag, snapshot_ids_json, resolved_at, outcome (`correct`|`wrong`|`ambiguous`|`expired_unresolvable`). **No explicit timescale field** — only implicit in `horizon_ts − ts`. ADD one. (Recent rows are `experimental-*` strategy_names — the inline hypothesis-probe loop in embryo.) Horizon currently defaults to a hardcoded 8h / 12h.
- `strategies`: id, name, description_md, hypothesis_md, regime_conditions_json, status (`active`|`paused`|`retired`), created_at, retired_at, retirement_reason. **Thin — no weights / threshold / stats** (those live in the strategy `.md` frontmatter). And the file *directory* (active/trial/observation/proposed/retired) is the real stage → a **second, separate status source** from the DB `status` column. Dual source of truth to unify.
- Execution chain: `theses` (carry `prediction_horizon_hours`, `regime_tag`, `invalidation_criteria_json`) → `decisions` → `trades` → `positions` → `outcomes` (rich: r_multiple, mae/mfe, conviction trajectory). **Worth carrying over largely intact.**

**Promotion ladder** (strategy_loader.py): proposed → observation (predictions only, gathering calibration, NO trades) → trial (small trades) → active (full sizing; calibration + edge gates) → retired. **Stage = directory.**

**Conviction today** (conviction-engine skill): per-strategy weighted sum of normalised data-point readings → steep sigmoid → anchored 50/50 to the strategy's `inherited_baseline`; weights updated in-place on resolved outcomes (alpha 0.05, cap 0.30, sum ≤ 1.0); no internal regime-awareness by design. The rebuild replaces the sigmoid/anchor with the normalised support-score model (§8) and adds narrative-data-point reasoning.

---

## 14. Open questions

1. ~~**Conviction-curve computation / monitoring home**~~ — RESOLVED: tiered, ops deterministic + escalate to predict. (§11)
2. ~~**PLUTUS.md structure**~~ — RESOLVED: ledger = daily journal files + per-agent transcript dirs (§3); zoning = one zoned file (doctrine + tool-rewritten `## Live State` section), exact section layout to be drafted at build time.
3. ~~**How narrative-data-point reasoning is stored**~~ — RESOLVED in direction: lifecycle.db table keyed to (prediction, data point) with support score + recorded reasoning, written as part of prediction writes. Exact schema at build time. (§8)
4. ~~**Strategy state, single source of truth**~~ — RESOLVED: the strategy **.md file is the truth** — `status` in frontmatter; the five-stage directory structure dies (one flat `strategies/` dir); DB `strategies` row is a derived mirror for joins/stats, synced atomically by the same tool that edits the file, never written independently. **Status gates context loading:** only live strategies (active/trial/test) are injected into predict/main context — dead/retired strategies stay on disk for reflect's historical queries but never pollute prediction context. (§13)
5. **Wake queue / lock design** — serialised single-drain queue for the four wake sources. Pure mechanism, no doctrine — settle during the build. (§10)
6. **Perception flow audit** — audit data-point outputs and how timescales are integrated end-to-end (perception output format → prediction timescale field → predict's enforced mix). Attach to the perception + predict build.

---

## 15. Resolved this session (locked)

- Full rebuild at the OSS layer; fresh runtime; calibration from zero; **BTC-only**.
- **One position at a time** (Hyperliquid cross-margin — hard constraint).
- Single **PLUTUS.md** replaces SOUL.md + WORLDVIEW.md. REGIME.md and PERCEPTION.md as blackboard files.
- **Star topology + blackboard**; orchestrator is the only spawner; agents coordinate through files.
- Orchestrator = thin persistent daily session; subagents ephemeral.
- **decide / do / will** and **forward / backward** as the two structuring axes.
- `predict` (forward) and `strategy`/`reflect` (backward) stay **separate**.
- Position management **decomposes**: predict sets invalidation criteria + risk tolerance; trade derives the volatility-based SL; ops monitors.
- `trade` and `execute` **merge** into one execution agent.
- **Invalidation ≠ SL** — strong thesis-break criteria vs volatility-based capital protection.
- Conviction = **normalised 0–1 support-score weighted average**, hybrid deterministic + recorded LLM reasoning, normalised across strategies → **global conviction threshold**.
- lifecycle.db writes: main writes events; predict writes predictions; ops resolves them.

## 16. Resolved 2026-06-09 (second session)

- **Roster locked at seven** — main / predict / trade / reflect / ops / perception / regime (see §2 table with models). `plutus-state` dissolved into an `account_state` tool. `reflect` (not `strategy`) is the name.
- **Main is the desk's voice** — forum posts, ledger, lifecycle.db entries all main's. Context-lightened via a consolidated `record()` tool (fan-out to db/ledger/forum); scribe agent only if the tool proves insufficient.
- **Models** — deepseek-v4-pro for main + predict (+ provisionally reflect); deepseek-v4-flash for trade, ops, perception, regime.
- **Monitoring is tiered** — ops deterministic, escalates narrative/borderline to predict. (Closed §11/§14.1.)
- **Holographic memory: ditched.** Memory architecture for the fresh runtime = lifecycle.db (structured, causal) + PLUTUS.md (working state) + blackboard files. No external Hermes memory provider.
- **BTC-only is a phase, not architecture** — watchlist is setup-wizard config (≤3 symbols initially). (§12 updated.)
- **Setup wizard: single path** — consolidate "quick" vs "full" into one setup process with clear next steps for optional/advanced pieces; add the watchlist-selection step.
- **Degen Arena is a first-class goal** (win it, over time). Council inputs = lifetime on-chain history + forum rationale posts → posting is doctrine, not optional. Realized P&L is the ranking metric (profit-taking realizes; account growth alone doesn't score). Reference: `~/.plutus-agent/ARENA.md`. Trade-cadence tension (one-position-at-a-time + small watchlist vs. top-agent volume) acknowledged — revisit after calibration phase.
- **Repo restructure ("radical")** — root level shows the trading architecture (PLUTUS.md, TRADING.md, agents/, trading/, docs/); ALL harness plumbing (agent loop, gateway, providers, cron, cli, memory) moves behind one package dir (e.g. `harness/`). Mechanical import sweep, one dedicated commit, sequenced AFTER demolition (delete first, then move) and BEFORE new construction. First-principles pass on the Hermes foundation happens during demolition — we own the code; delete what isn't earning its place.
- **Strategy single source of truth** — file-is-truth with frontmatter `status`; flat `strategies/` dir; DB row = synced mirror; status gates context injection (dead strategies never reach predict's context). (§14.4)
- **Prediction-first lifecycle** — lifecycle.db reframed as the lifecycle of predictions, some of which become trades. Theses cite `prediction_id`; calibration uniform at the prediction level; narrative support scores keyed (prediction, data point) with recorded reasoning. (§3, §14.3)
- **Ledger + sessions** — daily journal files + per-agent transcript dirs under `ledger/`; EOD injected message triggers main's journal entry; session naming `YYYY-M-D-a/-b`. Full-framework visibility: the ledger is the audit trail. (§3)
- **PLUTUS.md zoning** — one zoned file: doctrine + tool-rewritten `## Live State` section.
- **Wake queue** — deferred to build (mechanism, not doctrine). Staleness watchdog itself already locked (§10).
- **Chat platforms — curated set for OSS** — keep Telegram (ours) + Discord + Slack + webhook/api_server; delete the other ~19 platform adapters. All rebuild features (synthetic injection, wake queue, session roll) stay platform-agnostic at the gateway layer above the adapters.
- **Vision stays; image_gen dies** — chart analysis (incl. operator-shared TradingView screenshots) is a flagged future capability requiring vision.
- **Polymarket promoted** — out of `skills/research`, into the perception layer as registered data points.
- **Hermes built-in memory — INTERIM RESOLVED (2026-06-10):** disable-but-keep now (config defaults + toolset entries; ~10 lines, reversible) + delete the low-coupling pieces (plugins/memory providers incl. holographic, honcho extras, memory_setup CLI) during demolition. The HARD in-file surgery (run_agent.py prompt builder + gateway flush un-threading, ~12.4K LOC) is DEFERRED until the rebuild's **Lessons zone in PLUTUS.md** (curated by `plutus-reflect`) exists to replace the behavior. Built-in file-backed `_memory_store` (`memory`/`session_search` tools) survives untouched — it's inherited core, distinct from the provider subsystem.
- **Voice stays (2026-06-10):** operator decision — keep the full voice/STT/TTS cluster (Telegram voice notes to Plutus work; TTS output retained). Demolition Batch 8 skipped; `tools/xai_http.py` stays with it.
- **Docker deleted (2026-06-10):** prod is pm2; a stale Dockerfile is worse than none. Fresh accurate Dockerfile is a small post-rebuild task if OSS users want it.
- **Restructure decisions (locked 2026-06-10, third session):**
  - **Agents are first-class, not skills.** `agents/` at repo root, one dir per desk role: context recipe (`AGENT.md`) + tool manifest + model assignment, read directly by the spawn mechanism — NEVER via skills discovery. A skill is optional library context (procedures: Arena posting how-to, research workflows, coding guides); an agent is a fixed context recipe assembled deterministically at spawn. `ls agents/` IS the roster. Most of `skills/trading/` dies into agent definitions; a handful of procedures survive as library skills.
  - **`run_agent.py` moves whole** — no seams during the move; never mix moves with refactors. Seams get cut later, one at a time, as construction needs them (F3 census = the seam map).
  - **Inside `harness/`: names preserved**, with ONE sanctioned exception — the confusing `plutus_*` plumbing renames: `plutus_state`→`harness/state.py`, `plutus_constants`→`harness/constants.py`, `plutus_logging`→`harness/log.py`, `plutus_time`→`harness/clock.py`, `cli.py`(REPL)→`harness/repl.py`, `plutus_cli/`→`harness/cli/`.
  - **`trading/` shape:** `conviction/` (new §8 engine) · `lifecycle/` · `perception/` · `dispatchers/` · `integrations/<source>/` (source shape preserved per doctrine). `tools/registry.py` AST discovery gains a second root (`trading/`).
  - **Two commits, each green:** (A) `harness/` move + renames + import sweep + entry points; (B) `trading/` extraction + registry dual-root + root-docs skeleton. **No compatibility shims** — we own every caller; clean cut with green tests.
  - Sequenced: after demolition completes, before any construction.
- **Demolition COMPLETE (2026-06-10):** 13 commits, −108,657 lines net (282 files), rebased onto origin/main and pushed (`3f9195d` tip). Full report in `demolition-execution.md`. **Three deferrals carried forward:** (1) **image_gen** — inventory under-scoped it; it's entangled with the Nous-subscription managed-feature system shared by kept web/tts/browser → needs a dedicated re-scope (post-restructure, paths will have moved); (2) **delegation** (`delegate_tool.py`) — kept `skills/software-development/` is built on `delegate_task` → delete during construction when that skill is reworked for the self-extension workflow; (3) **memory ABC + memory_manager + run_agent prompt-builder/gateway-flush surgery** → lands in R2 with the Lessons-zone replacement (`sanitize_context` is imported unconditionally; the chain can't be cut piecemeal). 5 pre-existing tests/gateway failures documented (rebrand string drift + flakes — not demolition's).
- **post-entry-verify (from origin, 2026-06-08):** `skills/trading/post-entry-verify/SKILL.md` — mandatory on-chain verification of position size + SL/TP brackets after every place_order. The skill file dies with `skills/trading/`, but the DISCIPLINE gets absorbed into `plutus-trade`'s AGENT.md procedure in R4. Don't lose it.

## 17. Edge doctrine + hardening (adopted 2026-06-09, second session)

- **Where the edge lives.** Not in TA pattern-matching (the most arbitraged space in finance — deterministic TA points are *context*, not edge). The plausible edges for an LLM desk: (1) **novel hybrid combinations of data points** — e.g. derivatives positioning (funding+OI) × spot flow (CVD) × vol regime (bbwidth/ATR) × macro backdrop — joints single-domain algos don't watch; (2) **regime + timescale adaptivity** — an algo can't re-derive its own applicability when conditions shift; an agent can; (3) **narrative/event interpretation** — news, macro shifts, sentiment regime changes, where reasoning is the comparative advantage; (4) cross-domain reach — Arena's `xyz:` equities/commodities/FX, where the crypto-native agent fleet isn't looking.
- **Narrative data-point gap (perception work item).** Current registry (~50 DPs) is numeric-heavy: 21 TA + HL market structure + macro + DeFiLlama/CoinGecko/gas/dominance. Almost no narrative DPs registered. The §8 narrative conviction half needs first-class registered narrative data points: news digest, **Polymarket markets/odds** (locked: promote from skills/research), sentiment, ETF flows. Without them the hybrid-combination thesis has nothing narrative to combine.
- **Statistical honesty at graduation.** Raw win rate at N=10 graduates coin flips (a true 50/50 shows ≥7/10 ~17% of the time, and ten concurrent slots = multiple testing). `plutus-reflect`'s mandate includes confidence intervals / a simple binomial test, and a stricter bar (or larger N) specifically for the trade-enabling transition. Slower is fine; calibrating on mirages is not.
- **Machine-checkable resolution (highest-leverage hardening in the system).** The entire learning loop rests on resolution ground truth, and resolution belongs to ops (cheapest model). Therefore: predictions carry **structured success criteria** (symbol, direction, threshold, deadline) that resolve deterministically against PERCEPTION.md — prose is commentary only. **`plutus-predict` refuses to register a prediction whose criteria can't be evaluated by code.** Mis-resolved labels poison calibration, weights, and graduation silently.
- **North star (for the new PLUTUS.md).** With $35 of capital, trading P&L is a rounding error vs. inference cost — but the Arena pot copy-trades the desk, so edge is levered by the council's $200K. **Plutus is building a credible, verifiable public track record — on-chain history + legible rationale — that rents other people's capital.** Machine-checkable predictions and honest statistics are exactly what make a track record credible; main's forum posts are the legibility layer. Same artifacts, three audiences: Plutus's own calibration, the council, the OSS public.
- **Self-extension is core vision (the other half of the Hermes-foundation rationale).** Self-improvement ≠ only weight tuning — Plutus extends its own infrastructure: builds new data-point integrations, new tools, new code, using the full coding harness (terminal/file/browser/skills). The **registry pattern is the extension surface** — a new data point is one registered fetcher; the architecture's job is to keep that surface trivial. Guardrails (sandbox/test-gate for self-written code) to be designed as a post-launch workstream; demolition must not strip the coding capabilities that make this possible. Especially important as OSS — this is the differentiator.

## 18. Strategy generation (scoping draft — the under-built critical piece, to be designed)

The hypothesis generator sets the system's quality ceiling: the loop is generate → calibrate → select → vary, and selection can only choose among what generation proposes. Scoping frame:

**Generative sources (predict draws on all six):**
1. **Variation of winners** — mutate a promising/graduated strategy: swap one data point, shift timescale, narrow/widen regime, invert. Genetic operators: mutate / crossover (combine two strategies' data points) / specialize / generalize.
2. **Near-miss mining** — reflect's backward pass surfaces seeds from lifecycle.db: data points with high support in *winning* predictions that no strategy uses; almost-fired setups that would have won; recurring observations.
3. **Anomaly-driven** — perception flags an unusual reading (e.g. deeply negative funding while price rises) → predict forms a hypothesis to explain/exploit it.
4. **Event templates** — "after event X with reading Y, symbol tends Z within H hours" (CPI, FOMC, ETF flows, regime flips).
5. **Hybrid combination search** — deliberate novel joints across data-point families (the core edge thesis).
6. **Operator seeds** — Sev's ideas/screenshots enter as hypotheses with NO special treatment; they earn graduation like everything else.

**Discipline on the generator:**
- **Slot ecology** — the 10 slots carry enforced diversity quotas (timescale mix is already locked §5; add mechanism-family mix: momentum / mean-reversion / flow / event / narrative). Prevents clustering on one idea family.
- **Mechanism requirement** — every hypothesis states WHY the edge should exist (who is on the other side; what inefficiency). Forces causal reasoning over pattern-mining. Part of the structured template: mechanism, trigger conditions (data points + thresholds), timescale, regime applicability, falsifiable prediction form.
- **Graveyard memory** — retired strategies keep stats; new hypotheses are similarity-checked against the graveyard (embeddings exist already). Re-testing a dead family requires a stated reason (e.g. different regime).
- **Roles** — reflect produces the evidence (seed reports, data-point predictiveness, kill/promote calls); **predict authors** (generation is forward-looking). The forward/backward axis is preserved.

**Resolved 2026-06-10 (third session):**
- **File at birth (locked).** Every hypothesis gets a strategy `.md` file at creation with `status: test`. One object through the whole lifecycle; graduation is a frontmatter status change, never a migration between frameworks. The 10 slots = the files predict actively evaluates.
- **Hard diversity quotas (locked)** — training wheels for a zero-calibration runtime; loosen once reflect has evidence. Quota axes: timescale mix + mechanism-family mix (momentum / mean-reversion / flow / event / narrative).
- **Timescale taxonomy (locked 2026-06-10):** three buckets mapping to genuinely different market mechanics and data-point families — **intraday (≤24h)**: flows, funding, liquidations, CVD, orderbook, news reaction; **swing (1–7d)**: positioning cycles, weekly structure, event calendar (= one Arena season); **position (1–4w)**: macro narrative, trend regime, ETF flows, dominance. Predictions store exact horizon ts; bucket is derived. Hard cap ~30d (beyond that it can't feed calibration). Realized-P&L doctrine biases the book toward intraday+swing; position is the minority quota.
- **Regime ⟂ timescale symmetry (locked):** regime is assessed at the SAME three timescales, each from scale-native data points (intraday: hourly candles/funding/vol compression; swing: daily structure/event calendar; position: macro/dominance/flows). **A strategy's `regime_applicability` refers to the regime at its own timescale.** REGIME.md = a 3-row live table maintained by `plutus-regime`. Taxonomy kept brutally small for stable calibration slicing: direction (trending-up/trending-down/ranging) × volatility (compressed/normal/elevated), + macro overlay (risk-on/neutral/risk-off) at position scale only. [taxonomy labels = proposed, refine at build]
- **Regime change ⇒ dormancy + rotation + generation burst (locked):** out-of-regime strategies → `status: dormant` (file + stats intact, leave the active slots); dormant strategies matching the new regime wake; the flip is a wake event for main AND a generation trigger (predict runs a hypothesis burst for the new conditions). Slots = "the active book given current regime."
- **Coverage gaps are correct behavior (locked):** a never-experienced regime with no applicable graduated strategy ⇒ **predictions only, NO trades.** You can't trade an edge you haven't calibrated; patience is structural. Coverage accumulates by living through regimes.
- **Champion/challenger A/B (locked):** reflect's seed report proposes variants of existing strategies — tuning tweaks AND regime-applicability widenings (regime-boundary testing is just another challenger). Variants are files-at-birth with `parent_strategy` lineage, exempt from graveyard similarity (must state their one tweak), counted against family quota, promoted by head-to-head calibration over the same window.
- **Generation cadence (locked):** no hard weekly cron. Main orchestrates the research session (a dedicated predict spawn whose only job is generation, seed report in hand); on-slot-empty fills holes with cheap variation operators between sessions. "Generation/research" is an action type with a staleness factor (§10) — the ops watchdog guarantees the floor.
- **Self-extension hook (locked):** hypothesis files may declare `missing_data_points` → queues an infrastructure request instead of blocking. Builds happen in coding sessions (NOT a desk agent — topology stays clean); operator-reviewed at first, autonomy is a post-launch graduation.

**[STILL TO DESIGN]:** exact quota numbers; hypothesis template schema (fields + frontmatter); seed-report format reflect→predict; regime-taxonomy label set finalization.

## 19. Construction plan (locked 2026-06-10, third session)

Runs after demolition + restructure. Five phases, dependency-ordered:

- **R1 — Data layer.** lifecycle.db v2 schema: prediction-first spine, `timescale` field, structured machine-resolvable success criteria, narrative support-score table keyed (prediction, data point), `strategies` as derived mirror, status vocabulary `test/active/dormant/retired`. Plus the strategy file format + status-gated loader. Gets the most care — everything else reads/writes this.
- **R2 — Skeleton.** Blackboard contracts (PLUTUS.md zoning, PERCEPTION.md, REGIME.md formats) + the spawn mechanism and `agents/` loader, including automatic transcript-writing into `ledger/YYYY-MM-DD/` (audit trail is free, not a discipline). The deferred memory surgery (memory_manager + ABC + `_build_system_prompt` hooks + gateway flush) lands here, replaced by the PLUTUS.md Lessons zone.
- **R3 — Organs.** Conviction engine (`trading/conviction/`) + the `record()` fan-out tool.
- **R4 — The desk**, dependency order: perception + regime first (everyone reads their blackboards) → predict (biggest: conviction integration, 10 slots, generation) → trade → ops → reflect → **main last**, together with the wake queue and daily session roll.
- **R5 — Launch.** Wizard consolidation (single path + watchlist step), fixture-based dry runs of each agent in isolation, fresh deployment.

**Agent definition format (locked):** single `agents/<name>/AGENT.md`, YAML frontmatter + markdown body (mirrors strategy files). Frontmatter: `name`, `model`, `toolsets`, `reads`, `returns` (named output schema main validates before writing lifecycle events), `spawned_by`. Body: Role / Procedure / Output contract.
- **Declarative `reads:` = deterministic context assembly at spawn.** The spawn mechanism injects doctrine zone + fresh blackboard files + live strategy files BEFORE the model sees a token. No mid-reasoning skill loading, ever — the structural fix for the V2 failure mode, encoded in the format rather than in discipline.
- **Zone-addressable PLUTUS.md**: `reads:` entries name zones (`PLUTUS.md#doctrine`, `#live-state`, `#lessons`). Most subagents get `#doctrine` (slim constitution: identity, north star, hard constraints) + task-relevant zones; **only main reads the whole file.** Writing the new PLUTUS.md is partly designing an API, not just prose.
- **No-nesting by omission**: the spawn tool is simply absent from every subagent's `toolsets`. (There is no depth counter anymore — see §1 note.)

## §20. Construction log (running)

- **Part 0 complete (2026-06-10/11):** demolition tip → Commit A `62d2b85` (harness/
  move + category C incl. a data-dir/protocol over-rewrite class: backup.py
  cron/jobs.json, MCP "tools/call", SOUL prose) → `077093f` suite stabilization
  (sys.modules split-brain hygiene fixture incl. parent-attr restoration; discord
  always-real; safe-root env blanking; production fixes: COMPONENT_PREFIXES,
  systemd ExecStart, legacy-unit markers, status.py pm2 pattern, doctor hardening)
  → Commit B `7b10968` (trading/ extraction, registry dual-root, census-identical
  92 tools, root skeleton). All pushed. Full-suite signal: 471 → ~85 documented
  baseline.
- **R1 decisions (2026-06-11):**
  - **Clean-cut doctrine applied to v1 trading machinery.** v2 data layer
    (db/criteria/write/queries + strategies files/loader) REPLACES v1 outright;
    v1 queries/, write dispatchers (record_event/record_prediction/
    resolve_prediction/record_observation), execution wrappers (place_order/
    close_position/modify_order/cancel_order/place_trigger), spawn_subagent
    dispatcher, worldview_loader, and the v1 CLI lifecycle views are DELETED, not
    kept alive between phases — nothing runs until R5 and keeping them alive is
    work that throws itself away. **The sacrosanct venue layer
    (integrations/hyperliquid/venue.py brackets + _client.py) is untouched** —
    R3/R4 rebuild thin wrappers over it. Survivors (fetch_data_point,
    record_data_point_observation, account_state, list_*) rewired to v2.
  - **v2 schema as staged** (prediction-first spine; timescale stored + horizon
    exact, 30d hard cap enforced at write; support_scores with mandatory
    narrative reasoning; agent+session provenance everywhere; action_runs
    watchdog table; strategies mirror with resolution counters synced by
    resolve_prediction).
  - **Criteria contract:** leaf/all/any grammar; gte/lte/crosses_*/range ops;
    crosses_* resolve against the high/low window since baseline.ts; any
    unevaluable leaf → unresolvable, never guessed. record_prediction REFUSES
    invalid criteria, strategyless kind='strategy', unreasoned narrative scores.
  - **Strategy files:** frontmatter as staged (variant_tweak required with
    parent_strategy; missing_data_points as the self-extension hook); loader
    write-through mirror sync is the ONLY status-change path; context block
    explicitly says "predictions only, NO trades" when the live book is empty.
  - run_agent's WORLDVIEW/strategy prompt-injection seams cut (R2's zone
    assembly replaces); toolset bundles trimmed to surviving tools pending the
    R3/R4 toolset rebuild.
- **R2 complete (`6e437f8`):** spawn mechanism (harness/spawn.py — AGENT.md
  recipes, deterministic reads: assembly with zone-addressable PLUTUS.md,
  no-nesting by omission + force-disabled spawn/cron/messaging on children,
  validated return contracts, automatic ledger transcripts); runtime
  blackboard bootstrap (harness/runtime_templates.py, gateway-boot +
  wizard); memory surgery executed in full (external provider scaffolding
  out of run_agent; memory_manager/memory_provider deleted; built-in memory
  tool untouched; Lessons zone is the replacement); escalation.py deleted
  (wake queue replaces). DEFERRED with reason: delegate_tool deletion (35
  refs deep in run_agent incl. interrupt-propagation machinery; inert for
  the desk since spawn owns no-nesting; post-launch cleanup item).
- **R3 complete (`06dd99d`):** conviction engine v2 (weighted support-score
  average, global threshold 0.65, missing-readings-excluded-never-defaulted,
  narrative-requires-reasoning; weight cap CLARIFIED: 0.30 stops growth,
  never confiscates — equal-weight 3-DP books legitimately sit at 0.33);
  interpretable normalizer registry (linear_band/distance_from/zscore/
  inside_band); record() fan-out (db + journal + Arena forum, per-target
  error reporting); register_prediction tool.
- **R4 complete (`3a5ebbd`):** seven AGENT.md recipes (roster = ls agents/);
  desk tool surface (resolution, wake/check_staleness with floors 4h/8h/8h/
  7d/7d, desk_execution over the sacrosanct venue layer with one-position +
  funded-prediction laws in code, strategy_tools through the loader's single
  write path, lifecycle_query, spawn_desk_agent main-only); serialized wake
  queue (jsonl + lock, drain-all-into-one-turn, re-enqueue on failed
  delivery); cron `agent:` jobs spawn AGENT.md recipes directly (Path 0,
  silent); seed-desk = plutus-ops-tick (*/30) + plutus-eod (23:55); watchers
  enqueue wakes instead of creating cron jobs; skills/trading/ (21 v1
  skills) deleted. TOPOLOGY CALL ⚑: ops never spawns — escalation = wake
  for main; main spawns predict (star topology holds).
- **R5 complete:** single-path first-time wizard (provider → messaging →
  watchlist ≤3 with best-effort HL validation → two-wallet env step with the
  TRADING.md registration warning, skippable for research-only → first boot:
  blackboards + lifecycle.db v2 + desk crons); watchlist rendered into the
  PLUTUS.md doctrine at first creation; ecosystem.config.js hardened (venv
  binary direct, gateway log files); DEPLOY.md runbook (leads with the
  .env/HL_API_WALLET_KEY backup warning); dry-run tests: money path with
  mocked venue (6) + spawn_agent end-to-end against the real roster file
  with mocked AIAgent (2).
- **Sizing redesign (operator-directed, 2026-06-10):** GLOBAL_CONVICTION_
  THRESHOLD 0.65 → 0.50 — graduation is the binary gate (the strategy
  proved its edge); conviction above the threshold is the SIZING dial, not
  a veto. Conviction-banded target leverage on unified account value
  (LEVERAGE_BANDS in conviction/engine.py, single source): 0.50–0.60 → 2X ·
  0.60–0.70 → 5X · 0.70–0.80 → 7X · 0.80–1.00 → 10X. Bands live in trade's
  procedure (doctrine, capped by main's budget + venue max), NOT as a code
  law — code MEASURES instead: desk_open_position records
  entry_account_value + realized leverage on the position row (schema
  amended in place — v2 is fresh-create-only and pre-deployment; a failed
  equity read never blocks the lifecycle write after a real fill, recorded
  as missing + warned). Reflect closes the loop: new sizing_performance
  query (per conviction band: n/wins/avg+max leverage/PnL/avg+worst R/MAE),
  mandatory SIZING step + sizing_review key in the reflect_report contract;
  band retunes are operator decisions proposed with evidence. ALSO:
  graduation bar fixed to "N ≥ 15 AND win rate ≥ 2/3" (the old "≥10
  correct" letter passed 10-of-20 = 50%, and its ~p .06 annotation was
  wrong — 10/15 is p≈.15); with the gate at 0.50 graduation is the ONLY
  binary gate, so its statistical integrity matters more, not less.
- **SOUL.md → PLUTUS.md cutover (operator-directed, 2026-06-10):** PLUTUS.md
  IS the identity file now — load_plutus_md() takes the system-prompt
  identity slot (#1) that load_soul_md() held; build_context_files_prompt's
  skip_soul → skip_identity. The legacy default-identity seeding machinery
  is DELETED (default_soul.py, default_worldview.py, both _ensure_default_*
  seeders in cli/config.py — ensure_hermes_home seeds NO identity files;
  runtime_templates.ensure_runtime_files at wizard first-boot/gateway boot
  is the only creator, since it renders the watchlist). doctor --fix now
  calls ensure_runtime_files instead of writing a soul stub; profile clone
  list carries PLUTUS.md. Old runtime's SOUL.md is NOT transferred
  (DEPLOY.md 3b updated) — personality lines worth keeping get folded into
  the Doctrine zone by hand. claw.py legacy-workspace migration and the
  skills_guard .hermes path pattern intentionally keep their SOUL.md
  references (they handle OLD artifacts).
- **Optional desk integrations (operator-directed, 2026-06-10):** declarative
  OPTIONAL_INTEGRATIONS table in cli/setup.py (arena/DGCLAW_API_KEY,
  firecrawl/FIRECRAWL_API_KEY, embeddings/VOYAGE_API_KEY) — single source of
  truth for three consumers: a first-time wizard step (between wallets and
  first boot; Enter skips, never blocks), the new `plutus setup trading`
  section (watchlist + wallets + integrations, in SETUP_SECTIONS and the
  returning-user menu), and the end-of-setup Desk Integrations summary that
  states what each skip costs + the redo path. Pattern for future optional
  integrations: add one table entry, get all three surfaces.
- **ACP_AGENT_WALLET rename + SETUP.md from-zero guide (operator-directed,
  2026-06-11):** `HL_PUBLIC_ADDRESS` → `ACP_AGENT_WALLET` everywhere (code,
  wizard, setup-status, readiness script, tests, docs) — the master IS the
  Virtuals ACP agent's managed wallet, so the name now says so. The
  `HL_MASTER_ADDRESS` legacy fallback in check_trade_readiness is deleted
  (clean cut; neither old name is read). Canonical vocabulary set in
  TRADING.md: "ACP agent wallet" = master (holds funds), "API wallet" =
  signer — with an explicit warning that HL's own docs call the API wallet
  an "agent wallet". The external dgclaw-skill's .env keeps ITS names
  (HL_MASTER_ADDRESS etc. — its scripts' contract); skills/dgclaw/SKILL.md
  gained the mapping note. New SETUP.md: complete from-absolute-zero path
  (prereqs → install → provider/Telegram keys → ACP provisioning → dgclaw
  join → funding → unified+API wallet → wizard → post-wizard env → verify →
  fleet), with the agent-assisted alternative noted. Stale references swept
  while in there: setup_status checked pre-rebuild cron names
  (plutus-heartbeat/weekly-review → plutus-ops-tick/plutus-eod, remedy
  `cron seed-desk`) and pointed at dropped tools (dgclaw_install/dgclaw_join
  → skill procedure); _client.py error referenced nonexistent
  dgclaw_add_api_wallet; TRADING.md/readiness docstring had pre-restructure
  tools/ paths and a dead skills/trading/plutus-ops path; README's
  "trading/bootstrap-setup skill" and repo-root PLUTUS.md pointers fixed
  (→ SETUP.md, agents/README.md, seven-agent desk description).
- **Money model made canonical for human AND agent (operator-directed,
  2026-06-11):** verified against HL docs (account-abstraction modes:
  unified account shows all balances/holds in spot clearinghouse state;
  cross margin default; approveAgent validUntil ≤180d, same-name re-approve
  REPLACES) and acp-cli docs (Privy-managed agent wallet; acp CAN place HL
  orders via the Virtuals backend — capable, unused). Three surfaces now
  carry one model: (1) PLUTUS.md template Doctrine gained a "Money model
  (canonical)" block — two wallets, unified/cross display semantics, native
  trade path only, equity ≠ readiness — which reaches all six specialists
  via reads: PLUTUS.md#doctrine and main via identity injection; (2)
  SETUP.md gained "The money model in 60 seconds" for the operator; (3)
  TRADING.md fact #1 now also kills the ACP-CLI-trades red herring.
  Implementation gap closed: TRADING.md promised ops runs the readiness
  check every tick but NOTHING implemented it — the verdict logic moved to
  trading/integrations/hyperliquid/readiness.py (script now delegates),
  registered as the hl_trade_readiness data point (9th), and ops AGENT.md
  gained step 4 TRADE PATH (fetch + escalate on not-ready/expiring).
  Stale two-alerts baseline test fixed (hl_price_range).
- **Money measures glossary + one equity implementation (operator-directed,
  2026-06-11):** TRADING.md gained "The money measures (glossary)" — the
  canonical definition of equity_usd / spot_usdc / perp_account_value /
  withdrawable_usd / entry_account_value / realized leverage / drawdown,
  plus the settled anti-confusions (display vs transfer; hl_holdings'
  account_value is perp-side; equity ≠ readiness). Code computes equity in
  ONE place now: equity_breakdown(addr) in hyperliquid/data_points.py;
  hl_total_equity, hl_account_state (via reuse), and the balance alert all
  draw from it. REAL BUG fixed in the sweep: hl_account_balance_change
  polled perp-only marginSummary.accountValue — under unified mode it
  missed spot deposits/withdrawals entirely and fired on margin display
  shifts at every open/close; now watches equity_usd (regression test:
  spot 100→60 + perp 0→40 must NOT fire). LLM-facing semantics added to
  the account_state tool description, hl_account_state docstring,
  hl_holdings description; OPS_TICK_PROMPT now names the trade-path check;
  Live State template field renamed account: → equity_usd: so the field IS
  the defined term.
- **Hermes-residue audit + stale-test fixes (operator-asked, 2026-06-11):**
  the "pre-existing baseline" test failures were stale hermes-era
  assertions — code prints/resolves plutus, tests asserted hermes strings.
  Fixed: argv resolver tests (which("plutus")), "plutus gateway" /
  "plutus chat" / "plutus config set" assertions, acp browse --chain-ids
  drift. Latent bug killed: _resolve_hermes_chat_argv (now
  _resolve_plutus_chat_argv) fell back on find_spec("plutus_cli") — a
  package that never existed, so the module fallback could never fire;
  now checks harness.cli. scripts/run_tests.sh was broken on uv venvs
  (no pip) and silently preferred the dormant ~/.hermes venv as fallback
  — fixed (uv install path, fallback dropped, plutus header). Naming
  doctrine confirmed: user-facing = plutus only (binaries, ~/.plutus-agent,
  PLUTUS_* env); internals keep HERMES_* names behind the documented
  _alias_plutus_env bridge (147 internal env names, ~187 files) — renaming
  them is optional refactoring, not correctness. Full suite: 12,700 pass;
  ~58 red = (a) stale upstream assertions (~/.hermes default home, removed
  "matrix" extra, demolished VM cleanup, openclaw migration mocks),
  (b) order-dependent xdist flakes (cron scheduler + heartbeat files pass
  solo), (c) 2-core timing flakes (mcp parallel-shutdown 3.0s vs 2.5s).
  Predates this session — the canonical runner had never successfully run
  on this box. Dedicated cleanup pass recommended post-deployment.
- **Felt-surface de-hermesing (operator-directed, 2026-06-11):** doctrine —
  hermes must not appear anywhere users FEEL; internal symbols + honest
  lineage stay. Done in four phases: (1) generic PLUTUS_*→HERMES_* env
  bridge (constants.alias_plutus_env, re-run after reload_env) so the
  operator vocabulary is PLUTUS_* for ALL vars; .env.example renamed.
  (2) Felt strings: argparse prog="plutus", every printed command example,
  welcome banner, worktree/branch prefixes, printed product prose.
  EXCLUDED on purpose: anthropic masquerade needles, X-Hermes-Session-Token
  (prebuilt web frontend sends it), upstream-lineage prose, internal
  identifiers. (3) Nous Portal removed from CANONICAL_PROVIDERS (picker) —
  plumbing intact so configured installs still resolve; FULL nous deletion
  deferred (15+ files entangled: auxiliary_client fallback chains,
  nous_subscription.py, auth flows) — its stale test
  test_nous_when_no_openrouter dies with that pass. (4) tests/hermes_cli →
  tests/cli (merged, zero collisions), test_hermes_* files deprefixed.
