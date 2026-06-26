# Planning — Deterministic Execution + a Profitability-First Trade Architecture

*Drafted 2026-06-25 by Sebastian (Manor side) after a live execution incident.
**Scoped and substantially revised 2026-06-25 → 2026-06-26** (Sev + CC session)
after grounding every assumption against the live lifecycle DB. What began as
"collapse `plutus-trade` into a tool" grew, under the data, into a redesign of
execution, risk, and strategy graduation around a single profitability model.
This is the plan of record. Canonical specs to read alongside: `ARCHITECTURE.md`,
`TRADING.md`, `MAP.md`.*

> **STATUS — IMPLEMENTED 2026-06-26 (Push A + Push B).** The collapse, the
> expectancy gate, risk-based sizing, the RR/EV entry gate, the SL-population fix,
> the 20-min actionable staleness cap, and the full 4-target alert structure
> (`hl_position_alert` watcher + `rescore_position`) are all in the repo and the
> runtime. The active strategy was de-graduated to `test`; the desk is correctly
> idle (0 active) until a strategy graduates under the new gate. Implementation
> detail + verification: `~/.claude/plans/i-want-you-to-linear-gem.md`. The
> sections below are the design rationale, retained as the record.*

---

## Why this doc exists

The desk's execution layer misbehaved: a light model (`deepseek-v4-flash`) doing
TP arithmetic by hand fumbled the signed-edge sign (wrong-side TP, twice), caught
only by the venue validator + retries. The root realization, from Sev: **trade
*entry* is deterministic** — arithmetic and structured venue ops, no judgment.
Handing it to the cheapest model on the desk is a category error.

The investigation that followed (grounded against the live DB, not the recipe's
description) revised the original "collapse the agent" plan into something larger
and more correct. Two findings forced the expansion:

1. The execution geometry we were about to formalize (TP=far, a flat RR>1 gate)
   was **structurally sabotaging** the desk's only active strategy.
2. The strategy graduation gate is **survivorship-biased** — it graduates
   strategies that aren't actually tradeable.

## The core thesis — refined under scrutiny

The line for whether a desk role is an LLM agent: **does this step require
judgment under ambiguity?** The thesis held, but the data *sharpened* it:

- **Trade ENTRY** (size, stop, target, order placement) — no judgment.
  Mechanize it. This is the original collapse.
- **In-flight MANAGEMENT** ("the move reached its first target — more, or is the
  premise gone?" / "price dipped to where winners bottom — wobble or break?") —
  **this IS judgment under ambiguity**, and the live position-evaluations doing it
  are genuinely sharp. Keep it as a judgment call — but **trigger it
  deterministically** (alerts at meaningful levels) instead of by polling or
  agent whim.

So execution isn't "all deterministic." It's *deterministic bounds + judgment at
the two ambiguous edges.* That distinction is the spine of the new design.

---

## The profitability model (the spine — everything hangs off this)

A trade's worth is **expectancy**, not reward:risk:

```
expectancy = p · avg_win  −  (1 − p) · avg_loss
```

where `p` = the rate the move reaches its target before the stop. Equivalently,
positive expectancy ⟺ **RR > (1 − p) / p**:

| win rate p | required RR |
|---|---|
| 50% | 1.00 |
| 64% | 0.56 |
| 70% | 0.43 |
| 77% | 0.30 |

**This kills the flat "RR > 1" gate.** RR>1 is only correct at p=0.5. A
high-win-rate / small-target strategy is profitable at RR≈0.4 — and a flat RR>1
would refuse *every one of its trades*. The gate must be **win-rate-aware**,
using the strategy's own calibrated `p`.

The same calculation runs at two scales — this is the unification:
- **Graduation gate** = does this strategy have positive expectancy historically?
- **Entry gate** = does this specific setup have positive expectancy now?

One model, two timescales. (See the two dedicated sections below.)

---

## The 4-target trade structure

Every position carries **four levels: two mechanical bounds + two alert/wake
triggers.** The mechanical bounds are deterministic backstops; the alerts fire
*inside* them and invoke judgment.

For a long (mirror for a short):

```
   TP  (far edge)            ── mechanical hard exit (the occasional runner)
   alert-up  (near edge)     ── WAKE: take profit, or hold for far?
 ─ entry ─────────────────────────────────────────────
   alert-down (winners' MAE) ── WAKE: normal wobble, or thesis breaking?
   SL  (all-resolutions MAE) ── mechanical hard exit (capital preservation)
```

- **Mechanical TP = far edge.** The hard, best-case exit. Lets a genuine runner
  reach the optimistic target without the agent needing to act.
- **Mechanical SL = all-resolutions MAE percentile** (the catastrophe bound,
  wide enough to spare typical winners, tight enough to cap the loss). With
  risk-based sizing, this distance sets the position size (below).
- **alert-up = the near edge.** When the predicted move's first target is hit, it
  fires a wake: pull fresh perception, re-score conviction, decide take-profit vs
  hold-for-far. This is the fix for the pos#4 failure (below).
- **alert-down = winners' MAE.** When price dips to where *winning* setups
  typically bottom, it fires a wake: is this a normal winner wobble, or is the
  thesis breaking (cut early, before the hard SL)?

**Why this is the right shape, not just clever:**
- It dissolves the TP=near-vs-far deadlock. TP stays at far for the runner; *near
  is an alert*, so judgment can take profit when the move is exhausting — without
  hardcoding either answer.
- Both MAE populations get a home (ending our winners-vs-all debate): **winners'
  MAE → alert-down; all-resolutions MAE → hard SL.** Between them lies the
  ambiguous zone (winners have mostly bottomed, losers keep going) — exactly
  where judgment earns its keep. The alert opens the judgment window; the SL
  closes it.
- It composes primitives that already exist — bracket orders, price alerts
  (`integrations/hyperliquid/alerts.py`), wake-on-watcher, position
  re-evaluation. Wiring, not new machinery.
- It passes the judgment test: mechanism for the bounds and the *firing* of
  alerts; judgment only for the in-flight "act on this alert?" call.

**The one real risk — the judgment over-holds.** The thing that actually lost
money (pos#4) was the in-flight review saying `hold` as conviction decayed
0.917 → 0.6 → 0.45 → 0.3. So the alert-review must have a **bias to act**, and it
must be **scaffolded by a conviction re-score** (semi-deterministic): on a wake,
re-run conviction on fresh data; if the *dominant support that triggered entry*
has reversed, or conviction has dropped materially below entry, the **default
flips to exit** and holding becomes the thing requiring justification. That turns
"check for invalidation" into a concrete test and fixes the over-hold.

**Degradation:** alerts fire inside the mechanical bounds, so a slow/asleep
wake is not catastrophic — the hard SL/TP still bound the trade. The only cost of
latency is missing a near-edge profit-take, which the structure is designed to
prompt promptly. The trade degrades gracefully toward the mechanical bounds.

---

## Settled decisions

1. **Collapse `plutus-trade`** (the spawned LLM sub-agent) into deterministic
   tools (`desk_open_position` / `desk_close_position`). The light-model entry
   recipe goes away.
2. **main becomes the verifier of its own tool call**, not an orchestrator of a
   sub-agent. It still orchestrates the rest of the desk; only the execution
   *handoff* changes.
3. **main's call: `desk_open_position(prediction_id, thesis_md)`** — no `budget`
   arg; size is derived in-tool. Funding gate stays mechanical: *flat ·
   trade-ready · not-HALT · best actionable*.
4. **Selection is a deterministic query**, not predict's prose: *among open,
   unresolved, active-strategy predictions clearing the entry gate, the argmax
   expectancy* (tiebreak: higher expectancy, then earlier). Kills the
   dropped-handoff failure mode (the Jun-24 mode) — "what to trade" is a query
   over rows, not a payload predict must remember.
5. **Two verifications, two homes.** Venue safety verify + naked-position abort →
   **inside the tool, in-turn, before it returns** (net-new code; the verify
   currently lives only in the about-to-be-deleted recipe). Outcome verify (vs the
   Hyperliquid source of truth) + forum + ledger → main, after.
6. **Two recordings, two homes.** Lifecycle chain (thesis→decision→trade→position)
   written atomically in the tool; thesis *prose* authored by main and passed in;
   forum/ledger written by main after.
7. **Risk-based sizing** (not leverage bands). `size = (risk_budget% × equity) /
   stop_distance%`; leverage is derived and capped. (Numbers below.)
8. **Entry gate = positive expectancy** (`RR > (1−p)/p`), NOT a flat RR>1.
9. **4-target trade structure** (above): mechanical SL/TP + alert-down/alert-up,
   with conviction-re-score-scaffolded judgment at the alerts.
10. **Marketable-limit orders, slippage cap 0.30%** (not pure market — caps
    slippage by construction).
11. **Graduation gate = expectancy** (below), replacing the survivorship-biased
    `strategy_rr`.

---

## Risk caps (settled)

Risk-based sizing; **conviction → risk budget** (% of equity lost if stopped),
**superlinear** so tuned conviction earns meaningfully more:

| conviction | risk budget |
|---|---|
| 0.50–0.60 | 1% |
| 0.60–0.70 | 3% |
| 0.70–0.80 | 7% |
| 0.80–1.00 | 12% |

`leverage = risk_budget / stop_distance` falls out; **hard cap 10X** (subsumes a
separate position-%-of-equity cap — with one position in unified margin,
notional/equity *is* leverage). The cap only binds on sub-~1.2% stops and, when
it binds, *reduces* risk below budget (safe direction).

- **Slippage:** marketable-limit at `current × (1 ± 0.30%)`.
- **`risk_tolerance` buffer:** deferred to the Reflect geometry layer; v1 uses one
  base stop buffer (see SL section).
- **Eyes-open flags Sev accepted:** (a) superlinear × a *provisional* conviction
  substrate (render fix landed `2026-06-25T10:42:57Z`, ~zero post-fix resolved
  outcomes) is the riskiest combination — recommend Reflect validate the 0.80+
  hit rate before the top band runs at full 12%; (b) 12% losses compound fast
  (~5 in a row ≈ 47% DD) — mitigated by top-band rarity, per-trade bounding, and
  one-position-at-a-time, but a violent gap *through* the stop can exceed budget.

---

## Stop / SL derivation (deterministic, the load-bearing dependency)

The hard SL and alert-down both come from empirical MAE — but from **different
populations**, which is the resolution of our long winners-vs-all debate:

- **alert-down = winners' MAE** (`outcome`-reaching-target only), median-anchored.
- **hard SL = all-resolutions MAE** (catches losers, spares typical winners).

`mae_envelope` today is **winners-only** (`outcome='correct'` + profitable
positions) and uses a raw p80 — both wrong for the hard SL, and the raw p80 is
fragile on fat-tailed low-n samples. The fixes (all deterministic, in code):

1. **Median-anchored statistic:** `stop = M × p50`. On a fat-tailed handful, p80
   is just "the second-highest sample"; the median is robust. **M default = 3.0,
   Reflect-tunable per strategy.** (Raw-p80-with-buffer ≈ the current broken
   behavior — it silenced the strategy; decisively out.)
2. **Min-n gate = 6** reached-target winners (the active strategy has 9; n=10
   would force ATR fallback even for it, since the trade-win population is ~43% of
   resolved, not all of it). Pool per-regime → strategy-wide; ATR fallback below.
3. **Defined ATR fallback in code** (`k × ATR%`) — today the `n<5 → fall back`
   rule lives only in the recipe; `mae_envelope` enforces nothing.
4. **Right population for each use:** reached-target winners for the stop/alert
   geometry — NOT `outcome='correct'`, which (via floor-correct + horizon)
   includes "wins" that round-tripped 5%+ and poison the envelope.

---

## Graduation gate fix (one gate = profitability)

**`strategy_rr` (median MFE / median MAE on `outcome='correct'`) is
survivorship-biased and must be replaced.** Measured on the active strategy:

| population | med MFE | med MAE | ratio |
|---|---|---|---|
| CORRECT only (`strategy_rr`) | 1.07% | 0.60% | **1.79** ✓ |
| ALL resolved (what you face) | 0.87% | 1.08% | **0.81** ✗ |

It graduates strategies on the geometry of trades that *already won*. Replace with
**simulated net expectancy**: run the strategy's resolved predictions through its
actual trade geometry, graduate iff `expectancy > cost margin` with `n ≥`
threshold. Same calculation as the entry gate (one profitability model, two
scales).

Caveats, both conservative-correct:
- **Lower bound:** a deterministic backtest can simulate only the mechanical
  bounds, not the alert-judgment value-add. Don't graduate a strategy that needs
  good judgment to break even — judgment is unreliable (pos#4).
- **Path-dependence:** when a realized path hits both target and stop, graduate on
  the *pessimistic* (loss) assumption.

---

## Empirical grounding (the evidence backbone — all read-only from the live DB)

Active strategy `orderbook-imbalance-intraday`: 21 resolved, 15 correct / 5 wrong
(~75% *prediction* hit rate). But as **trades**, the live record is **0 wins / 6
losses, ≈ −$1.55**:

| pos | pred | exit | PnL | R | note |
|---|---|---|---|---|---|
| #1 | 267 | invalidation | −$0.15 | −0.20 | genuine loser, cut correctly |
| #2 | 297 | invalidation | −$0.64 | −0.37 | wrong-way then recovered *after* exit |
| #3 | 306 | sizing_violation | −$0.02 | — | operational glitch (0.4m) |
| #4 | 310 | thesis_break | −$0.59 | −0.43 | **winner mid-trade, lost to TP=far** |
| #5 | 328 | main_decision | −$0.01 | — | operational glitch (0.4m, double-open) |
| #6 | 328 | thesis-break | −$0.14 | −0.20 | never reached near |

Key findings the data forced:

1. **TP=far is wrong for this strategy.** Recent traded: **0/5 reached far, 3/5
   reached near.** pos#4 is the proof — near reached at conviction 0.917, but TP
   sat at far, the desk held (`rec=hold`) as the premise reversed, and a winner
   became −0.43R. → near must be an *alert*, not ignored.
2. **A flat RR>1 gate would refuse this strategy entirely.** Its edge is
   high-win-rate / small-target (RR<1, positive EV). → the EV-based gate.
3. **Winners and losers are separable, but not by a fixed price stop alone:**
   winner MAE p50 0.60% vs loser MAE p50 3.20%. A price stop tight enough for RR
   shakes out winners; wide enough to spare them lets losers run. → the real
   loss-cutter is the *thesis-break* judgment at the alert, not the price level.
4. **Invalidation IS in use — discretionarily, and well.** 0/21 predictions
   carried machine `invalidation_criteria`, but positions close on in-flight
   thesis-break judgment, and the evals are sharp (tracking the orderbook premise
   reversing). → keep the judgment; trigger it via alerts; scaffold it with the
   conviction re-score.
5. **Backtests are inconclusive on profitability.** TP=near lifts win rate to
   62–77% but nets ~breakeven (−0.2 to −0.6%) with 5–7 of 21 path-dependent;
   TP=far is also ~breakeven. **At n=21 with that much path-dependence, we cannot
   say this strategy is profitable.** Honest status: marginal, not proven either
   way — and under the new expectancy gate it is **borderline-to-negative and
   probably should not be graduated on current data** (consistent with 0/6 live).

**The big lesson:** our intended config (TP=far + flat RR>1) was making a
*marginal* strategy look *definitely unprofitable*. Fixing the config doesn't
guarantee profit — it stops the desk from structurally sabotaging strategies that
win small-and-often. **The desk's profitability constraint is upstream geometry +
graduation, not execution mechanics.**

---

## Per-strategy geometry via Reflect (now central, not a "later optimization")

The trade geometry is one coupled, per-strategy object — Reflect governs it,
deterministic code consumes it (the conviction-weight pattern: live stats +
Reflect-curated durable params):

- **Target structure** (does TP=far ever pay, or is near the realistic target?).
- **Stop `M`** and the **alert levels** (winners-MAE percentile, near placement).
- **The entry gate's `p`** (the strategy's calibrated win rate) → its EV threshold.

Discipline: slow/bounded/min-n like the weight update; overfitting on n≈9 is real.
Reflect is the **governor, not the calculator** — it must not hand-pick stop
multiples from eyeballed MAE (the TP-bug error one level up). Storage: stats live,
judgment decisions durable in the strategy `.md`.

---

## Invariants that must NOT be lost

- **Mandatory on-venue SL** + naked-position abort (verified flat after the abort).
- **One position at a time** (cross-margin law; tool enforces).
- **Pre-fill (flat) equity** for sizing (the in-position `equity_breakdown`
  double-count is unfixed, `TODO(verify-live)`).
- **On-venue bracket verification** post-entry; never trust the fill alone.
- **Keep the venue pre-trade validator** as the last-line safety net.
- **Honest absence** — failed equity/envelope reads degrade loudly, never silently
  default.
- **Invalidation ≠ stop-loss** — record thesis-break, alert-driven, and hard-stop
  exits as distinct `exit_reason`s (enumerate; today they're free-form, with
  inconsistent spellings `thesis-break`/`thesis_break`).
- **Alerts fire inside the mechanical bounds**; the hard SL/TP always bound the
  trade regardless of wake latency.

---

## Blast radius

- `trading/lifecycle/queries.py` — `mae_envelope` population + median-anchored
  statistic + min-n; new **expectancy** query (graduation + entry); new
  best-actionable selection query; replace/retire survivorship-biased
  `strategy_rr` as the gate.
- `trading/dispatchers/desk_execution.py` — `desk_open_position` gains derivation,
  the EV entry gate, risk-based sizing, marketable-limit, in-turn verify +
  naked-abort, **and the 4-target setup (2 brackets + 2 alerts)**.
  `desk_close_position` — enumerate `exit_reason`.
- Alerts / wake / position-eval wiring — the alert-up (near) and alert-down
  (winners-MAE) triggers; the conviction-re-score-scaffolded review on wake.
- `trading/conviction/engine.py` — bands become **risk budgets** (1/3/7/12%);
  add the re-score-on-wake path; the 0.50 threshold + budgets are provisional
  (post-fix recalibration via Reflect).
- `agents/plutus-trade/AGENT.md` — **delete.**
- `agents/plutus-main/AGENT.md` — spawn → tool call; toolset gains
  `desk-execution` (deliberate doctrine change: a *deterministic capability*, not
  discretion); main owns alert-wake reviews.
- `agents/plutus-predict/AGENT.md` — selection → query; consider authoring
  machine `invalidation_criteria`; graduation now gated on expectancy.
- `agents/plutus-reflect/AGENT.md` — owns per-strategy geometry governance + the
  graduation-expectancy review.
- `ARCHITECTURE.md` + `PLUTUS.md` roster — **seven agents → six**.
- `harness/spawn.py` — remove `plutus-trade` from the spawnable set.
- Tests — deterministic tool tests: TP side both ways, SL placement, **naked abort
  + verify-flat**, one-position refusal, pre-fill equity, risk-based sizing + 10X
  cap, **EV gate pass/refuse**, median-anchored stop, the 4-target setup,
  expectancy graduation.
- Runtime `~/.plutus-agent/PLUTUS.md` doctrine roster + the sevs-space skill
  `.claude/skills/plutus-agent/SKILL.md`.
- **Operational glitches to investigate separately:** pos#3 `sizing_violation`
  and pos#5 `main_decision` double-open on pred#328 (both ~0.4min) look like bugs,
  not strategy outcomes.

---

## Sequencing

Each phase lands behind tests + a dry/preview path before driving the live
gateway; Sev pulls the `pm2 restart`.

- **Phase 0 — profitability primitives.** Fix `mae_envelope` (population,
  median-anchored, min-n, ATR fallback). Build the **expectancy** query. Replace
  the graduation gate. Test in isolation — the gates are meaningless until these
  are right.
- **Phase 1 — deterministic open + EV gate + 4-target.** `desk_open_position`
  derives size (risk-based) / hard SL / hard TP, applies the EV entry gate, places
  marketable-limit + 2 brackets + 2 alerts, verifies + naked-aborts in-turn.
  Additive (keep explicit-args override so the agent still works). Behind tests +
  dry path.
- **Phase 2 — alert-driven management.** The wake reviews (conviction re-score
  scaffold, bias-to-act) on alert-up / alert-down. Enumerate `exit_reason`.
- **Phase 3 — selection-as-query + cutover.** main gains `desk-execution`, calls
  the tool, drops the spawn, verifies vs HL.
- **Phase 4 — delete + doctrine.** Delete `plutus-trade` + the explicit-args
  override; roster 7→6; spawn cleanup; port tests.
- **Phase 5 — Reflect governance.** Per-strategy geometry tuning (target
  structure, `M`, alert levels, per-strategy `p`/EV threshold); recalibrate the
  provisional bands on post-fix data.

---

## Unresolved (honestly)

- **Does the alert-judgment add enough alpha to make marginal strategies
  profitable?** Unprovable until it runs live — the backtest can't simulate the
  judgment. This is the genuine bet in the design.
- **Is any *current* strategy profitable?** The only active one is
  borderline-to-negative on n=21. The desk may correctly trade rarely until
  predict generates strategies whose moves clear their own noise. That relocates
  the real work upstream — and is the right outcome, not a failure.
- **Machine `invalidation_criteria` vs discretionary thesis-break** — whether to
  mechanize the close-side thesis check (resolvable structural level-breaks) or
  keep it as scaffolded judgment. Lean: scaffolded judgment now, mechanize where a
  clean resolvable trigger exists.

## Pointers

- Incident + remediation: `~/.claude/plans/yes-conditional-entry-should-stateful-flute.md`,
  `workshop/plutus-agent/remediation-2026-06-25.md`, ambient memory
  `project_plutus_5issue_investigation_2026-06-25.md`. Backstop revert: `e2f2b8a`.
- Execution: `trading/dispatchers/desk_execution.py`,
  `trading/integrations/hyperliquid/venue.py`, `…/hyperliquid/alerts.py`.
- Stops / expectancy / selection / graduation: `trading/lifecycle/queries.py`
  (`mae_envelope`, `strategy_rr`).
- Conviction bands / threshold / re-score: `trading/conviction/engine.py`.
- Signed-edge convention: `trading/dispatchers/register_prediction.py`.
- Selection rule today (→ query): `agents/plutus-predict/AGENT.md` step 5 +
  `actionable` output field.
