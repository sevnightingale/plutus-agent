# Plutus architecture & graduation review

**Date:** 2026-07-09  
**Scope:** Desk architecture critique (code + docs) and live analysis of the multiplicity / sibling graduation bar.  
**Status:** ~~Review only~~ **RESOLVED 2026-07-09** — every finding was independently verified against code + the live DB, then implemented same-day (see Resolution below).

---

## Resolution (2026-07-09)

All findings verified (three minor imprecisions noted below), then fixed. Decisions taken per the doc's own recommendations:

| Item | Decision | Where |
|---|---|---|
| A (P0) | desk tools added to `TRADE_TOOLS` **+** in-tool HALT checks in `_desk_open`/`_desk_close` (naked-abort exempt — an unprotected position outranks the pause) | plugin + `desk_execution.py` |
| B (P0) | `refused:` codes in-tool for HALT, `hl_trade_readiness` (unverifiable also refuses), `status != 'active'` | `desk_execution.py` |
| C (P1) | **C1**: mechanical TP = `best_target` edge; near-target books get no redundant near alert; gate reward/p match the placed TP | `desk_execution.py` |
| D (P1) | **D1**: gate `p = wins/n` (scratches are non-wins) in both `desk_open` and `best_actionable_prediction` | `desk_execution.py`, `queries.py` |
| E (P1) | Deterministic test↔active sync (`trading/lifecycle/graduation.py`); auto-run after every resolution via shared `resolver.resolve_open_predictions` (watcher **and** ops) + `strategy_status_sync` tool; dormancy/retirement stay reflect's | `graduation.py`, `resolver.py`, `strategy_tools.py` |
| M1 (P1) | Siblings = **serious trials**: books ≥ `SERIOUS_TRIAL_MIN_N` (=6) resolutions, any status incl. retired | `queries.py` |
| F (P2) | Missing conviction while risk is open → `exit_now` (`take_profit` on a near alert); `rescore_position` takes `alert=` | `desk_execution.py` |
| G (P2) | `register_prediction` enqueues a wake when an ACTIVE strategy registers (backstop vs in-turn deferral) | `register_prediction.py` |
| H/J (P3) | Recipe hygiene (main/reflect AGENT.md, `execution` toolset description, doctrine dual-edit) + `desk_status` lifecycle query (gaps, HALT, readiness, fundable window) | recipes, `toolsets.py`, `lifecycle_query.py` |
| M5 | Regime-cell M: still **not done**, per doctrine | — |
| §3.8 doctrine diff alert | **Skipped deliberately**: the runtime PLUTUS.md legitimately diverges (Live State, Lessons), so a whole-file diff would always fire — noise, not signal | — |

**Live effect of M1 (2026-07-09):** pullback-to-ema-intraday M 29 → **11**, hurdle 1.17% → **1.01%** against expectancy +0.63% (n=16) — still correctly not tradeable (gap 0.38%); nothing graduates spuriously, and the live population shows zero status/tradeable mismatches.

**Verification corrections to this doc (found during review-of-the-review):**
1. §3.1 "Docs and main's recipe" — TRADING.md makes **no** HALT claim; the false claims lived in `agents/plutus-main/AGENT.md` and `ARCHITECTURE.md` only (both now true after A/B).
2. §3.7 — predict is spawned **synchronously by main**, so main is by construction awake at registration; the 20-min window is lost to in-turn deferral, not sleep. G still helps as a backstop.
3. §4.5 — constant-exp/σ math gives **n ≈ 73** (M=29) and **n ≈ 46** (M=8) at the live numbers, not ≈80/≈50; the conclusion (grouping alone doesn't unlock) stands. Appendix numbers also drifted one resolution by fix time (pullback n=16, exp +0.6265, σ 1.5684).

**Canonical docs this review is grounded against:** `ARCHITECTURE.md`, `TRADING.md`, `CLAUDE.md`, `agents/*/AGENT.md`, `trading/lifecycle/queries.py`, `trading/dispatchers/desk_execution.py`, live `~/.plutus-agent/lifecycle.db`.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [System snapshot (what Plutus is)](#2-system-snapshot-what-plutus-is)
3. [Architecture findings & recommendations](#3-architecture-findings--recommendations)
4. [Multiplicity / graduation deep dive](#4-multiplicity--graduation-deep-dive)
5. [Prioritized backlog](#5-prioritized-backlog)
6. [What not to change](#6-what-not-to-change)
7. [Appendix: live numbers (2026-07-09)](#7-appendix-live-numbers-2026-07-09)

---

## 1. Executive summary

### Architecture (desk design)

The desk’s spine is strong: **calibration-gated trading**, **judgment vs mechanism**, star topology, deterministic entry, multiplicity + hazard on graduation, honest absence, sacrosanct trade path docs. The main issues are not “the thesis is wrong” — they are **gates that lived in recipes after the execution collapse**, **geometry/`p` inconsistencies**, and **operability** (HALT, readiness, doctor/status).

### Graduation bar (multiplicity)

Live code groups siblings by **timescale only** (`M` = same timescale, any status, any non-empty resolved book). For `pullback-to-ema-intraday` (closest to tradeable):

| Metric | Value |
|--------|------:|
| n | 15 |
| expectancy | +0.668% |
| σ (PnL) | 1.61% |
| M (intraday) | 29 |
| premium | 1.08% |
| hurdle | **1.23%** |
| tradeable | **no** |

Plutus’s operator-facing table (M=3/5 would graduate) **understated the premium** given real σ. With live stats, only **M ≤ 2** clears today. Mechanism-only grouping (M≈8) still does **not** graduate this book. Thin books pad M: **17/29** intraday “siblings” have n &lt; 5.

**Recommended direction:** keep multiplicity; fix **what counts as a sibling** (serious trials) before lowering the formula or switching to regime-scoped M (already rejected for gaming).

---

## 2. System snapshot (what Plutus is)

Six-agent star desk on a Hermes-fork harness:

| Agent | Role |
|-------|------|
| **plutus-main** | Gateway session; orchestrates; funds via query + tools |
| **plutus-perception** | PERCEPTION.md |
| **plutus-regime** | REGIME.md |
| **plutus-predict** | Hypotheses + machine-resolvable predictions |
| **plutus-ops** | 30m watchdog; resolve; readiness; wakes |
| **plutus-reflect** | Weights, graduation bookkeeping, lessons |

**Laws that shape the system:**

1. **One binary gate** — graduation via simulated expectancy (multiplicity-deflated, not decaying).
2. **Honest absence** — failed readings stay failed; no silent fallbacks.
3. **Records via tools** — lifecycle.db writers validate; blackboards are shared belief only.

**Money path (TRADING.md governs):**  
`desk_open_position` → Hyperliquid native SDK → API wallet signs, master holds funds. Equity ≠ readiness.

**Separation of judgment vs mechanism (deliberate):**  
Entry sizing/SL/TP/brackets = code. Alert-up / alert-down management = judgment, scaffolded by `rescore_position`.

---

## 3. Architecture findings & recommendations

### 3.1 P0 — HALT no longer covers the real trade path

**Finding.** After retiring `plutus-trade`, fills go through `desk_open_position` → `hl_place_order(...)` in-process. The HALT plugin only blocks:

```text
place_order, close_position, modify_order, cancel_order,
acp_wallet_send, dgclaw_trade_open, dgclaw_trade_close
```

(`harness/plugins/plutus-trade-safety/__init__.py`)

There is **no** HALT check under `trading/dispatchers/desk_execution.py`. Docs and main’s recipe still claim “not-HALT” is a mechanical guard.

**Risk.** `touch ~/.plutus-agent/HALT` may **not** stop desk opens/closes.

**Suggested solution (A):**

1. Add `desk_open_position` / `desk_close_position` (and any other money tools) to `TRADE_TOOLS`.
2. Refuse inside `_desk_open` / `_desk_close` if `HALT` exists (defense in depth).
3. Optional: refuse at `hl_place_order` / `hl_close_position` so every path is covered.
4. Extend plugin + desk_execution tests.

**Effort:** small. **Doctrine impact:** none — code matches existing claims.

---

### 3.2 P0 — “Mechanical” guards for readiness & active status are recipe-only

**Finding.** `desk_open_position` enforces:

- one open position  
- prediction age ≤ 20 min (`ACTIONABLE_MAX_AGE_S`)  
- strategy `tradeable`  
- live-price EV gate  
- conviction risk-budget band  
- pre-fill equity  
- venue orphan positions  
- naked SL abort  

It does **not** enforce:

| Guard (claimed) | Enforced today |
|-----------------|----------------|
| HALT | Plugin only on old tools — broken for desk path |
| `hl_trade_readiness` | main recipe / ops only |
| `status == active` | `best_actionable_prediction` SQL only |

So a confused main (or any caller with a `prediction_id`) can fund a **test** strategy that happens to be `tradeable`, or open while readiness is dead.

**Suggested solution (B):**  
Inside `_desk_open`, refuse with explicit `refused:` codes for:

1. HALT present  
2. `hl_trade_readiness.ready != true`  
3. strategy `status != 'active'`  

Main’s recipe then *describes* the tool; it does not *implement* the money gate.

**Effort:** small. **Doctrine impact:** none — strengthens “mechanical means mechanical.”

---

### 3.3 P1 — Geometry: graduation can optimize **near**; execution hard-TPs **far**

**Finding.** `strategy_expectancy` graduates on the **best of** far vs near simulation (`best_target`). `best_actionable_prediction` uses that target for EV reward. But `desk_open_position` always sets mechanical TP from **far**:

```text
tp = entry_ref * (1 + far_edge_pct/100)
```

…and the live RR gate uses **far distance** with `p = exp["win_rate"]` (win rate of the *best* book, which may be near).

Reflect’s recipe says near-edge strategies “trade on near (alert-up take-profit)” — **nothing forces that exit**. If main holds for far (pos#4-class failure), the live trade may never match the geometry that graduated.

**Suggested solution (C)** — pick one:

| Option | Behavior |
|--------|----------|
| **C1 (preferred)** | If `best_target == "near"`, mechanical TP = near edge; far is research/optional later. |
| **C2** | Keep far TP; on `kind=near` alert, default close unless rescore is strongly hold (semi-deterministic near-exit for near books). |
| **C3** | Only `best_target == "far"` strategies may go `active`; near-only stays incubating. |

**Invariant:** entry-gate reward and `p` must match the **mechanical TP** actually used.

**Effort:** medium. **Doctrine impact:** medium (especially C1/C3).

---

### 3.4 P1 — `win_rate` used as `p` excludes scratches; expectancy includes them

**Finding.** In `_sim`, scratches get PnL 0 and count in `n` for expectancy, but:

```text
win_rate = wins / (wins + losses)   # scratches out
```

Entry EV uses that `p`. Scratches inflate apparent hit rate vs true expectancy.

**Suggested solution (D):**

- **D1:** `p = wins / n` (include scratch as non-wins), or  
- **D2:** re-sim this setup’s geometry against the book for the gate, or  
- **D3:** gate primarily on strategy expectancy + consistent geometry (align with graduation).

**Effort:** small (D1) to medium (D2/D3).

---

### 3.5 P1 — Graduation to `active` is LLM-mediated; the bar is already code

**Finding.** `strategy_set_status` does **not** check `strategy_expectancy(...).tradeable` before promoting to `active`. Reflect’s recipe describes the bar; code does not enforce it.

- Wrong graduation → funding still blocked by `tradeable` (partial safety).  
- Failed graduation (`tradeable` true, status still `test`) → desk stays **idle forever** (`best_actionable` joins on `status = 'active'`).

**Suggested solution (E):**  
Deterministic status sync (ops tick or dedicated tool), not weekly LLM:

```text
if tradeable and status in (test, eligible_dormant) → active
if active and (not tradeable / decaying / dead at N≥20) → test or retired per rules
```

Reflect still owns weights, lessons, population pruning, and **narrative** of moves.

**Effort:** medium. **Doctrine impact:** low — “one binary gate” becomes code-owned.

---

### 3.6 P2 — `rescore_position` holds when conviction is missing

**Finding.**

```text
if conv is None:
    rec = "hold"   # "missing data — hold, re-check"
```

That fights **honest absence** while risk is open. Main may also ignore `recommended_action`.

**Suggested solution (F):**

- Adverse alert + missing conviction → `exit_now` (or `exit_preferred`).  
- Near alert + missing conviction → prefer take-profit if price already at near.  
- Optional: auto-close on `exit_now` for adverse alerts; leave near-edge as the main judgment surface.

**Effort:** small–medium.

---

### 3.7 P2 — 20-minute actionable window vs ~8h predict floor

**Finding.** `ACTIONABLE_MAX_AGE_S = 1200` is correct for entry-condition drift. Predict’s staleness floor is ~8h. Funding only works if main wakes **within 20 minutes** of registration. No automatic “fundable window” wake on register.

**Suggested solution (G):**  
On `register_prediction` for an active/tradeable (or near-tradeable) strategy: `enqueue_wake(...)` immediately; optional second wake at T+15m if still open/unfunded.

**Effort:** small.

---

### 3.8 P3 — Multi-truth, recipe drift, harness mass, cold-start legibility

| Issue | Detail | Suggestion |
|-------|--------|------------|
| **Doctrine dual-write** | Template in `runtime_templates.py` seeds once; live `PLUTUS.md` never auto-updated | Boot/doctor **diff alert** (never auto-overwrite) |
| **Recipe/toolset drift** | Main Role still says “place orders (trade does)”; `"execution"` toolset description still mentions dead place_order tools | Hygiene edits only |
| **Harness mass** | ~189k lines `harness/` vs ~26k `trading/` | Declare desk-core surface; optional `plutus[desk]` vs full packaging; bar for harness edits |
| **Cold-start opacity** | Patience is correct; “broken vs patient” is hard to see | Deterministic `desk_status` query/CLI: tradeable gaps, readiness, HALT, prediction ages, next unlock |

**Suggested solution (H/I/J):** doctor/status + recipe hygiene + packaging boundary over time.

---

## 4. Multiplicity / graduation deep dive

### 4.1 Formula (code of record)

\[
\text{hurdle} = 0.15\% + \sqrt{2 \ln M} \cdot \frac{\sigma}{\sqrt{n}}
\]

```text
tradeable ⇔ stop estimable
           ∧ expectancy > hurdle
           ∧ n ≥ 15
           ∧ not decaying
```

- **M** = same **timescale**, any status (incl. retired), any strategy with ≥1 resolved book with `realized_value_json`.  
- **σ** = this strategy’s simulated PnL stdev.  
- Retired siblings **must not** shrink M (anti-laundering).  
- Hazard: trailing 10 resolutions negative → `decaying` blocks tradeable.

### 4.2 Design intent (why timescale, not regime)

From `agents/plutus-reflect/AGENT.md` (settled 2026-07-07):

> M is timescale-scoped **BY DESIGN** — do not re-propose grouping siblings by **regime cell**: the premium counts how many chances the desk gave itself to find a lucky book… **regime_applicability is self-declared**, so a cell-scoped M would let a strategy **narrow its declared regime to lower its own bar**.

Two coherent philosophies:

| View | M should count | Risk if wrong |
|------|----------------|---------------|
| **Search budget** (current) | Every try at this timescale | Over-penalize unrelated mechanisms; slow graduation |
| **Hypothesis family** | Same mechanism (± regime) | Under-correct if many families each graduate a “lucky” champion |

Regime-cell M: **rejected** (gaming). Mechanism family: **not banned**, softer middle ground.

### 4.3 Operator conversation — corrections

Plutus told Sev that at M=3/5, pullback would clear. **Using live σ and n, that is false:**

| M | Premium | Hurdle | exp +0.668 clears? |
|---|--------:|-------:|:------------------:|
| 1 | 0.00% | 0.15% | yes |
| 2 | 0.49% | 0.64% | yes |
| 3 | 0.62% | 0.77% | **no** |
| 8 (intraday×momentum live) | 0.85% | 1.00% | **no** |
| 14 (Plutus’s momentum guess) | 0.96% | 1.11% | **no** |
| 29 (current) | 1.08% | 1.23% | **no** |

Live **intraday × momentum** with any resolved book: **8**, not 14.

### 4.4 Thin-trial inflation of M

Intraday strategies with any resolved book: **29**.

| Book size | Count |
|-----------|------:|
| n &lt; 5 | **17** |
| n ≥ 10 | 7 |
| n ≥ 15 | 4 |

A one-resolution sibling full-counts toward M. That is aggressive relative to “independent full trials of the same kind of statistic.”

### 4.5 Path to clear for pullback (constant exp & σ)

**At M = 29:** need roughly **n ≈ 80** to clear (~5× more resolutions).  
**At M = 8 (mechanism):** need roughly **n ≈ 50**.  
**Serious-trial M (n≥10 → M≈7):** still does **not** clear at n=15; shortens the path vs 29 but does not unlock today.

**Conclusion:** for this book, the binding constraint is **modest edge vs own σ**, not only “29 vs 8.” Grouping changes alone are not a free graduation.

### 4.6 Multiplicity options (discipline ↔ tradeability)

| # | Option | Pros | Cons | Recommendation |
|---|--------|------|------|----------------|
| **M1** | **Serious-trial M** — only count siblings with `n ≥ N0` (e.g. 6 = `HARD_SL_MIN_N`) | Keeps timescale/search-budget story; stops noise padding; low gaming | Won’t graduate pullback *today* | **Do first** |
| **M2** | **Timescale × mechanism_family** | Aligns with different hypotheses; M~8 for pullback | Multi-family false-positive rate; family is self-declared (coarse enum mitigates) | Second step if still never graduates |
| **M3** | Keep M=timescale; accelerate evidence | Pure discipline; incubation already helps | ~n=80 path may be product-hostile | Valid if patience is absolute |
| **M4** | Cap `M_eff = min(M, K)` | Simple; stops exploration death spiral | Weak theory | Acceptable soft landing |
| **M5** | Regime-cell M | Smallest M | **Rejected** — self-declared regime gaming | **Do not** |
| **M6** | Shrink the √(2 ln M) coefficient | Faster graduation | Undermines trading-design import | Last resort; prefer fixing what M counts |

**Process complement (as important as the formula):**  
Every new thin sibling raises the bar for leaders. Enforce population caps hard; prefer fewer long-lived books over 40 half-finished trials.

### 4.7 Recommended multiplicity stance

1. **Do not** drop multiplicity or the 0.15% cost margin.  
2. **Do** redefine siblings as **serious trials at timescale** (M1).  
3. **Optionally** move to timescale × mechanism (M2) only if, after serious books exist, the desk still cannot graduate anything with real edges.  
4. **Do not** use regime-scoped M.  
5. Treat pullback’s +0.67% / n=15 / σ=1.6 as **under-evidenced relative to its own noise**, not as proof the bar is “broken.”

---

## 5. Prioritized backlog

| Priority | ID | Item | Effort | Notes |
|----------|-----|------|--------|-------|
| **P0** | A | HALT covers desk path (+ in-tool check) | S | Safety claim currently false |
| **P0** | B | readiness + `status=active` inside `desk_open` | S | Mechanical = code |
| **P1** | C | Geometry: `best_target` matches mechanical TP / gate | M | Doctrine choice |
| **P1** | D | Consistent `p` (scratches) | S–M | Align EV with expectancy |
| **P1** | E | Deterministic status sync from `tradeable` | M | LLM shouldn’t own the binary gate |
| **P1** | M1 | Serious-trial sibling count | S–M | Best multiplicity fix first |
| **P2** | F | Rescore missing-data + optional adverse auto-exit | S–M | pos#4 surface |
| **P2** | G | Fundable-window wakes on register | S | Use the 20m gate productively |
| **P2** | M2 | Optional mechanism-scoped M | S | Only if M1 insufficient |
| **P3** | H/J | Recipe hygiene + `desk_status` / doctor | S | Operability |
| **P3** | I | Harness vs desk packaging boundary | ongoing | Maintainability |
| **P3** | M3/M4 | Patience / M cap | policy | Operator preference |

---

## 6. What not to change

These are working design choices; critiques above assume they stay:

- Star topology + no-nesting (spawn main-only)  
- Deterministic entry, atomic SL, naked-position abort  
- Multiplicity **idea** + hazard/decay (even if M definition is refined)  
- File-at-birth strategies + validating lifecycle writers  
- Honest absence on failed data points (extend to rescore; don’t weaken)  
- One position + risk-budget sizing spine  
- TRADING.md as sacrosanct execution truth  
- Public track record / Arena as north star over short-term account P&L  

**One-line spine (still right):**  
*LLMs propose and narrate; code decides whether the world has earned a trade; the track record is the product.*

---

## 7. Appendix: live numbers (2026-07-09)

Source: `~/.plutus-agent/lifecycle.db` via `trading.lifecycle.queries.strategy_expectancy`.

### Population

| Timescale | Strategies (any) | With resolved book |
|-----------|-----------------:|-------------------:|
| intraday | 40 | 29 |
| swing | 28 | 23 |
| position | 24 | 19 |

### Intraday × mechanism (with book)

| Mechanism | Count |
|-----------|------:|
| flow | 13 |
| momentum | 8 |
| mean_reversion | 6 |
| event | 2 |

### Closest strategies (gap = hurdle − expectancy)

| Strategy | n | exp | hurdle | M | gap |
|----------|--:|----:|-------:|--:|----:|
| pullback-to-ema-intraday | 15 | +0.668 | 1.232 | 29 | +0.56 |
| ema20-pivot-swing | 31 | +0.301 | 1.105 | 23 | +0.80 |
| cvd-divergence-swing | 21 | +0.304 | 1.192 | 23 | +0.89 |
| trend-continuation-swing | 13 | +0.619 | 1.589 | 23 | +0.97 |
| donchian-cascade-swing | 8 | +1.306 | 2.498 | 23 | +1.19 (also n&lt;15) |

### pullback-to-ema-intraday detail

| Field | Value |
|-------|------:|
| best_target | far |
| wins / losses / scratch | 8 / 3 / 4 |
| win_rate (ex-scratch) | 0.727 |
| stop_pct | 1.63 |
| expectancy_far / near | +0.668 / +0.012 |
| decaying | false |
| status | test |

---

## Next steps (for discussion, not committed)

1. Decide **P0** safety patches (A, B) — low controversy.  
2. Decide multiplicity path: **M1 only** vs **M1 → M2**.  
3. Decide geometry **C1/C2/C3** before more strategies graduate on near books.  
4. Schedule deterministic graduation **E** vs keep reflect-owned status for now.

When priorities are set, implementation can land as focused PRs with tests under `tests/trading/`.
