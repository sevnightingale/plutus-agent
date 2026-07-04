# A Physical Design for a Trading Knowledge System

*A system for arriving at — and honestly maintaining — a profitable edge.*

## What survives, what inverts

The spine from the place-based design survives intact, because it was never about economic development — it was about maintaining revisable belief over an append-only record. One asserted store, three derived strata:

```
LEDGER      — append-only events: market observations, research trials,
              pre-registered predictions, orders, fills, retractions,
              definitions, successions
  ├─ CATALOG    — immutable, content-addressed definitions (propositions,
  │              signals, strategies, classifiers, cost models, forks)
  ├─ BELIEF     — (fork, proposition) → confidence structure; a total,
  │              snapshotted fold of ledger + priors
  └─ CACHE      — backtests, features, optimizations, risk models,
                 projections; partial, hash-keyed, evictable

REFERENCED  — the tape and vendor corpora, pinned by version hash,
              summarized into the cache, never re-hosted
```

The proposition/belief split carries over unchanged and remains the load-bearing move. What changes is the domain's physics, and three inversions reshape everything downstream of the spine:

**The enemy inverts.** The economic-development system fought evidence *scarcity*. Trading fights evidence *abundance*: with enough data and enough trials, a profitable-looking pattern is guaranteed to appear by chance, and the researcher who found it is the least qualified person alive to judge it. The central epistemic hazard is self-deception through multiple testing, so the architecture's first job is to make the system physically hard to fool itself — which turns out to mean *the ledger must record the research process, not just the market*.

**The world fights back.** Places do not adapt to being understood; markets do. An edge is a claim whose truth *decays as a consequence of being true* — exploitation erodes it, and adversaries hunt it. Belief therefore needs an axis the previous system never needed: a survival model.

**Projection inverts.** The previous system existed to share knowledge outward. Here secrecy is the asset, inbound data is the firehose — and the one outbound projection you cannot refuse is your own order flow, which prints a lossy image of your beliefs onto a public tape read by adversaries. Decision 9 is about minimizing a projection instead of publishing one.

The concept mapping, so the rest reads cleanly: *instrument* plays place (the identity claims attach to, with hierarchy from external registries — exchanges, sector taxonomies); *signal* plays intervention (an atomic tradable hypothesis: a condition and a position rule); *strategy* plays plan (a staged composite: signals, sizing, execution policy); *regime* is the workhorse classifier; the *edge claim* — `(signal, scope) → conditional return distribution` — plays the causal claim; and *observation* covers both what the market did and what we did to it.

---

## 1. One representation for all claims

**Decision: unchanged in structure — one epistemic envelope, typed payloads, no belief on the claim — with the predicate registry carrying the domain's claim kinds.**

The claim kinds are new but the shape is not. Instrument facts (point-in-time fundamentals, listing status, borrow availability); relationships (cointegration between pairs, lead–lag structure, factor loadings); edge claims (a signal's conditional return distribution within a scope); execution claims (a cost model's slippage and impact within a scope); capacity claims — all share the envelope:

```
Proposition (catalog entry, immutable, id = content hash)
  subject    — instrument, signal, strategy, relationship pair, cost model
  predicate  — ref into the predicate registry
  payload    — typed against the predicate's schema
  scope      — classifier ref: regime × universe × deployment-size
  valid      — the market-time interval claimed
```

One domain-specific note earns its place here rather than later: **capacity is not a separate construct — it is scope over a size dimension.** "This edge yields 4% at $10M and nothing at $500M" is not a special capacity field; it is the same proposition machinery with a classifier that ranges over deployed capital. The claim that decays with size and the claim that fails in high-volatility regimes are structurally identical: both are propositions whose scope is narrower than first believed, and both are discovered by the same sharpening mechanism (Decision 2). This collapse — capacity into scope — is the first place the trading domain rewards the classifier design rather than straining it.

Indexing follows the previous argument unchanged: envelope columns carry the hot queries ("every live edge claim whose scope this market state satisfies" is *the* trade-time query), payloads are schema-registered binary with per-predicate expression indexes, and immutability keeps the table append-only and engine-friendly at tens of millions of rows.

## 2. Confidence

**Decision: five axes — the previous four plus a hazard axis — with multiplicity accounting built into the update method, and disconfirmation sharpening scope exactly as before.**

```
Belief (key: fork, proposition_hash)
  likelihood  — posterior over the claimed quantity: conditional return /
                information-coefficient distribution for edges, cost
                curves for execution claims
  n_eff       — evidence weight discounted for dependence — and in this
                domain the discounts are brutal: overlapping return
                windows, cross-sectionally correlated instruments, and
                autocorrelated residuals mean ten thousand observations
                are often a few dozen effective ones
  coverage    — the fraction of scope actually probed: which regimes,
                which universe segments, which deployment sizes the
                edge has been tested in
  residual    — composite (strategy) claims: the gap between what the
                component signals predicted and what the book realized —
                implementation shortfall plus interaction effects
  hazard      — the survival model: a posterior over whether the edge
                is still alive, and its expected decay rate
  frontier    — ledger offset this row reflects
```

Two of these deserve their arguments made in full.

**The hazard axis** exists because non-stationarity in markets is not noise around a stable truth — it is the expected fate of every true edge. An edge claim's `likelihood` answers "was this real?"; its `hazard` answers "is it still?" — and conflating them into one number reproduces the classic failure where a genuinely dead edge coasts on the strength of its historical evidence. Physically, evidence is time-discounted through the hazard model when it updates `likelihood`, recent disconfirmation moves `hazard` before it moves `likelihood` (an edge failing this quarter is more likely dying than never-was, if its history is deep), and the axis gives the portfolio layer the number it actually needs: not "how sure are we this worked" but "what is the probability-weighted remaining life." Decay is inferred, never asserted — there is no `decay` event kind, because nobody observes an edge dying; the deriver reads it off the record. Decision 3 explains why decay must *not* be handled as a correction.

**Multiplicity accounting** is where the ledger is weaponized against self-deception. The update method for edge claims does not ask only "what did the evidence show?" but "out of how many trials was this the survivor?" — a backtest Sharpe of 2 means nothing without knowing whether it was the best of three variants or of thirty thousand. So the research platform emits a `trial` event to the ledger for *every* hypothesis evaluated, successful or not, carrying the hash of the search specification that generated it (the parameter grid, the feature pool, the selection rule). When a surviving signal's evidence is folded into belief, the update method deflates it by the recorded search breadth. The multiplicity correction method itself is a versioned catalog entry — swappable — but the *recording of trials* is structural: it is simply what the ledger is. A research process that hides its failed trials from the system is not running a different analysis; it is corrupting the evidence base, and the architecture makes the honest path the default path because trials are emitted by the platform, not filed by the researcher.

Sharpening under disconfirmation carries over whole, and the domain gives it its natural vocabulary: an edge that fails when volatility spikes does not merely get downgraded — the deriver detects that disconfirming evidence separates on regime features and emits child propositions with narrowed regime scopes. The original stands as broad-and-weak with honest coverage; the children are narrow-and-strong; the `refines` edges link the family. "It only works in calm, liquid markets" is not a diminished claim — it is a *sharper* one, and the mechanism that produces it is the same classifier-refinement machinery, unchanged.

## 3. Time

**Decision: bitemporal as before — but here point-in-time discipline is existential, and the as-of machinery is not a reporting feature: it *is* the backtesting substrate.**

In the previous system, "what did we believe last year" served audit and humility. In trading it serves survival, because the canonical catastrophic error of the field — lookahead bias — is precisely a violation of belief time: evaluating a signal against data that had not yet been published, restated fundamentals as if the restatement were always known, index membership as it is rather than as it was. The bitemporal split dissolves the problem structurally. *Valid time* is when a fact was true in the market; *belief time* is the ledger offset at which the system could have known it — and a backtest is, by construction, **a derivation that may only read the belief state as of the offsets it simulates**. Snapshot-plus-replay, built in the previous design for time travel, is here the data layer of the backtest engine itself: the same machinery, promoted from feature to foundation. A backtest that cannot commit lookahead bias because the storage layer refuses to answer out-of-time queries is worth more than any code-review checklist.

The event-kind asymmetry carries over with vivid domain instances. A **world change** — prices move, a company reports — appends `observe` events; history stands. A **correction** — a vendor restates earnings, a bad tick is withdrawn, a survivorship hole is discovered — appends `retract` events citing the withdrawn evidence; belief over the affected valid intervals recomputes, version digests move, and every backtest, risk model, and track record whose read-set touched those rows is orphaned by its own hash. Vendor restatements are not an annoyance to be patched over; they are first-class retractions, and the cascade they trigger is the system telling you exactly which conclusions were built on the bad data.

The refusal that matters most here: **decay is not a correction.** When an edge dies, the temptation is to treat its old evidence as somehow invalidated — it was never *really* there. That is epistemically wrong and operationally destructive: the edge *was* real, the fills happened, the track record is history, and retro-editing it would destroy the very adjudication machinery (Decision 6) that lets forks be compared on their records. Decay is a world change carried by the hazard axis and by valid-interval closure — never by retraction. The past profits were earned; the claim's validity interval simply ended.

**Succession** finds its domain-native form in corporate actions. Splits, mergers, spin-offs, ticker changes, and delistings are `succession` events over instrument identities: parents' claim intervals close, children's open, and the mapping weights (how a spin-off inherits its parent's history) are claims with confidence, because they are genuinely contestable. The structural payoff is that **survivorship bias becomes impossible to commit by accident**: an identity, once minted, is permanent — a delisted stock does not vanish from the universe, its identity persists with a closed interval and a terminal observation — so any backtest over "the universe as of belief-time t" mechanically includes the names that later died. The single most common way quantitative research lies to itself is eliminated by the identity model rather than by vigilance.

## 4. The classifier construct

**Decision: one construct, unchanged in form — an AST in the soft-predicate algebra — doing four jobs: regimes, universes, objectives, and the scopes of every edge claim. Regime taxonomy becomes an empirical discovery, not a house style.**

The spaces multiply but the construct does not. A **regime** is a classifier over market-state features (realized volatility, dispersion, funding spreads, breadth); a **universe** is a classifier over instrument features (liquidity, market cap, borrow cost); an **objective** is a classifier over return-distribution space (what this book counts as a good outcome — Sharpe-seeking, drawdown-averse, tail-hedged); and every edge claim's scope is a composition of these plus the size dimension. Same catalog entry, same algebra: soft thresholds, linear scores through link functions, t-norm combinators — legible, composable, content-addressed, and mechanically generatable, which Decision 2's regime-splitting requires just as scope-refinement did before.

The discipline about opaque models bites harder here, because the temptation is stronger: regime detection is a natural home for hidden Markov models and clustering. The line holds: a learned model may *propose* a regime, but what is stored as the classifier is its distillation into the algebra — because two forks must be able to reference provably-identical regime definitions by hash, because a strategy's applicability footprint must be constructible as a soft conjunction of its signals' scopes, and because when an edge claim says "only in calm markets," the definition of calm must be inspectable by the human who will bet on it. The non-distillable model lives on as a versioned method whose outputs are cache entries — useful, never definitional.

Generality is read off the corpus exactly as before, and the domain makes the readings meaningful. A regime's **breadth** is the fraction of market-time (weighted by the reference measure) it covers: "normal conditions" sits near the top of the lattice, "March 2020" near the bottom, and every named regime *earns* its position by evaluation rather than assertion. The empirically-derived dominance lattice then does real work in Decision 2's coverage axis: an edge tested only inside a narrow regime has provably low coverage of any broader scope it claims, and the lattice is what makes "provably" a computation. When someone renames or redefines "risk-off" — a new AST, a new hash — its lattice position re-derives, and every claim scoped to the old definition keeps meaning exactly what it meant. Definitions never drift under claims; they are replaced beside them.

## 5. Asserted versus derived, physically

**Decision: the keying discipline carries over unchanged, and its single most important application in this domain is a refusal: a backtest is a cache entry, never evidence — except through the trial gate.**

The taxonomy is as before: one asserted store (the ledger), three derived strata, derived rows addressable only by `H(method_version ‖ read_set_digest ‖ params)`, hand-edits unreachable, corrections cascading by orphaned keys, a standing regenerability audit rebuilding sampled slices of belief from the raw ledger.

What the trading domain adds is the sharpest possible test of the boundary. A backtest looks like evidence — it is a rigorous computation over real historical data, and entire firms have died treating it as such. In this design it is *physically incapable* of being evidence: it is a derivation, it lives in the cache, it carries no truth, and the belief deriver reads only the ledger. The **only** door through which research results influence belief is the `trial` event of Decision 2 — a registered record carrying its search-spec hash — where the multiplicity-deflating update method is waiting. An unregistered backtest can inform a human's curiosity; it cannot move a posterior, because there is no code path by which it could. The overfitting discipline that most shops enforce by culture and checklist is here enforced by the same keying mechanics that enforce everything else — which is the design's central bet: *make the epistemically honest path the only path that exists.*

The same boundary sorts the rest of the derived zoo without ceremony: feature computations, portfolio optimizations, risk decompositions, covariance estimates, track-record scorings — all cache entries, all frontier-stamped, all regenerable. A risk model is not truth about the market; it is a method version applied to a read-set, and when either changes, its old outputs remain inspectable beside its new ones.

## 6. The append-only ledger and forkable belief

**Decision: event-source everything including the firm's own actions; a fork is priors + cost model + universe + objective classifier; pre-registration via `predict` events is mandatory, and paper trading is just a fork whose predictions never meet fills.**

The event vocabulary settles as: `observe` (market data — mostly referenced, Decision 7), `trial` (every research evaluation, with search context), `predict` (a pre-registered, timestamped forecast or intended position), `order` and `fill` (the firm's own actions and their outcomes), `retract`, `define`, `succession`, `register_fork`. Two additions relative to the previous system — `trial`, `order`/`fill` — and each earns its keep.

`predict` does the heaviest lifting. A signal graduates from research to belief-that-matters only by committing forecasts to the ledger *before* outcomes are known; the scoring derivation then compares predictions to subsequent observations with no possibility of retrospective selection, because the ledger's ordering is the referee. This is the out-of-sample discipline made structural: paper trading is a fork that emits `predict` events and no orders; incubation is the period during which its scored track record accumulates enough `n_eff` to justify capital; and the difference between a backtest and a track record — the difference the whole industry turns on — is precisely the difference between a cache entry and a ledger prefix.

`order`/`fill` events record the entanglement the previous system never faced: the firm's observations of its own executions are *contaminated by its own impact* — the fill price includes the effect of the order. So fills are never folded into edge claims as if they were neutral market observations; they update **execution claims** (the cost model's slippage and impact posteriors) and the **residual** axis of strategy claims, while the edge's likelihood updates from the uncontaminated tape. Keeping alpha evidence and execution evidence in separate claim families is what lets the system distinguish "the edge is dying" from "we have grown too big to harvest it" — two diagnoses with opposite remedies, indistinguishable in a system that only watches PnL.

A fork is a catalog entry, slightly wider than before:

```
Fork (catalog entry)
  priors     — per-predicate hyperparameters; source-reliability priors
               (vendors have track records too); multiplicity method
  cost_model — the execution-claim set this fork prices trades with
  universe   — a classifier over instruments: what it may trade
  objective  — a classifier over return-distribution space: what
               winning means to this book
```

Desks, books, and candidate strategies are forks over the same shared evidence; the cost accounting is unchanged (a fork pays for its belief materialization and derivations — gigabytes and stream-compute, not a copy of the tape); and adjudication is the domain's native sport: every fork's `predict` events are scored against the shared record, by its own objective and by a neutral one, and capital allocation across forks becomes a decision over track records held in a common ledger rather than a negotiation over whose backtest deck was prettier. One further gift: positions and PnL are not stored state at all — they are a deterministic fold of `fill` events, derived, regenerable, and therefore never in dispute with the record.

## 7. Federating a corpus too large to hold

**Decision: host what the firm generates, reference the vendors' tape pinned by version, and index it as bars — noting that the bar is finance's four-hundred-year head start on the mergeable sketch.**

The full tape — every quote and trade across venues and years — is petabytes and belongs to vendors and exchanges. The split:

**Hosted**: the firm's own events — trials, predictions, orders, fills — plus the catalog, belief, and cache. Small, sovereign, and the only part whose loss is unrecoverable, which is exactly why it is the part that is one append-only log.

**Referenced**: vendor archives and exchange records, pinned as (identifier, locator, content hash of the vendor's release version). Version pinning matters more here than anywhere, because vendors restate *constantly* — corporate-action adjustments, corrected prints, revised fundamentals — and each vendor release is a distinct pinned version, with the deltas between versions ingested as `retract`-plus-`observe` pairs so that restatements flow through the correction machinery and orphan exactly the derivations they poison. Instrument registries and sector taxonomies are referenced the same way, their hierarchies imported as claims sourced to the registry version. And vendors, being sources, carry reliability priors in every fork: a vendor with a restatement habit contributes less `n_eff` per observation, mechanically.

**Indexed**: the aggregate layer, and here the domain delivers a small joke at the architecture's expense — the mergeable sketch over tick data was invented centuries before the terminology. A **bar** (open, high, low, close, volume) is precisely a fixed-size, associatively-mergeable summary of the underlying prints: minute bars merge into daily into monthly, per-venue into consolidated, exactly as the previous design's sketch cells rolled up regions and years. The index generalizes the bar where research needs it — quantile digests of trade sizes, spread and depth sketches, higher-moment summaries — but the principle is untouched: sketches are computed once at ingestion per pinned corpus version, keyed by (corpus hash, method version), stored columnar in the cache stratum at O(instruments × periods × measures) rather than O(prints), and merged at query time along the instrument-succession graph and the calendar. Coverage stamps carry the domain's honesty requirements: known exchange outages, venues not licensed, dark volume not in the feed — so an aggregate answers with its evidence basis attached, and a backtest over the index knows what it *couldn't* see. Drill-down to raw prints follows the pin to the vendor archive: slow, priced, rare.

## 8. Normalized truth versus fast reads

**Decision: the projection layer splits by clock — a research cache as before, and a trade-time state store with hard latency budgets — both owned by the core, both frontier-stamped, because the lookahead discipline must hold in production too.**

The previous system's readers tolerated milliseconds; a live trading loop does not. So the derived stratum serves two clocks. The **research cache** is unchanged: profile specs declared by consumers, documents materialized per (spec hash, entity, fork), maintained by the streaming deriver. The **trade-time state** is the same contract under a harsher physique: the live feature vector per instrument, current regime memberships, active edge claims in scope with their five confidence axes, and the fork's current risk state — precomputed, memory-resident, updated by the deriver on the market-data path, read by the execution logic in microseconds by key.

The frontier stamp, which in the previous design was an honesty nicety, is here a trading control. Every trade-time read carries the data timestamp it reflects, and the execution layer enforces a staleness floor — a signal computed on a frontier older than its declared tolerance does not fire. This is the *live* twin of backtest lookahead discipline: the backtest may not read the future, and the live system may not pretend a stale present is current. One temporal rule, both clocks.

Ownership does not move: applications (execution engines, risk dashboards, PMs' screens) declare specs; the core materializes; nothing downstream is writable. An execution engine that learns something — a fill, a rejected order, a venue anomaly — writes events, and sees them reflected when derivation catches up, with the latency of that loop itself measured and stamped. The trade-time store cannot drift into a second truth for the same three structural reasons as before, now with real money attached to each: it is hash-keyed and read-set-stamped, it has no write path, and its staleness is a number on every read rather than an ambient assumption.

## 9. Canonical store versus shared external formats

**Decision: the boundary rules are unchanged — projections strictly derived, the ledger the only inbound door — but the flow inverts: inbound is a firehose to be graded, and the one outbound projection that cannot be refused is the firm's own footprint on the tape.**

Inbound dominates. Vendor feeds, exchange data, and broker reports do not write into any internal structure; they are ingested as `observe` events attributed to their source and version, gated by that source's reliability prior, folded into belief like all evidence. Cross-vendor identity mapping — the same bond under three vendors' identifiers — is claims with confidence, because security master reconciliation is fallible record linkage, and every shop that has treated it as a lookup table has the scars. Deliberate outbound projections are few and mundane: regulatory reporting and administrator feeds are ordinary projection specs with format adapters, derived, stamped, one-way, audited by the project/re-ingest/diff loop as before.

The interesting projection is the one the previous system had no analog for: **every order the firm sends is an involuntary projection of its beliefs into a public, adversarially-read format.** The tape shows your prints; sophisticated counterparties reconstruct your intentions from them; your footprint *is* an external view of your belief state, published continuously, whether you like it or not. The architecture cannot prevent this projection — but it can treat it as one: execution policy becomes the adapter that governs how much information the forced projection leaks, execution claims (Decision 6) measure the leak's price as impact, and the design goal inverts cleanly from the previous system's *maximize fidelity of the projection* to *minimize the information content of the projection while still transacting*. Same boundary concept, opposite objective — which is the mark of a boundary drawn in the right place: it survives the sign flip.

## 10. The elegance spine

**Decision: the same five load-bearing structures — one asserted store, three derived strata, one pinning convention — with the domain's concepts mapping on without remainder.**

What collapses into what:

**Instruments, signals, strategies, regimes, universes, objectives, cost-model definitions, forks, predicates, methods, profile specs → the catalog.** All immutable, content-addressed definitions. A strategy is a DAG of signal refs plus sizing and execution policy; a fork is four fields; an instrument is a minted identity with lineage; none carries mutable state.

**Instrument facts, relationships, edge claims, execution claims, capacity (as size-scope) → propositions + beliefs.** One epistemics for "Apple's Q3 revenue was X," "these two names cointegrate," "this signal earns 30bps monthly in calm regimes below $50M deployed," and "our market orders in this name cost 8bps" — which is the uniformity the envelope was built for.

**Backtests, features, optimizations, risk models, covariance estimates, regime memberships, classifier breadths and the regime lattice, bar/sketch indexes, trade-time state, track-record scorings, positions and PnL → the cache.** The largest collapse, and the domain's most valuable one, because it puts *backtests and PnL* — the two numbers trading firms most habitually enshrine as truth — into the stratum that carries none: both regenerable, both frontier-stamped, both incapable of asserting anything.

**Ticks, trials, predictions, orders, fills, retractions, definitions, successions → the ledger**, a closed vocabulary in which the two domain additions (`trial`, `order`/`fill`) carry the multiplicity accounting and the impact entanglement respectively.

Where collapsing was **refused**:

*Backtest versus evidence.* The defining refusal of the trading instantiation. They look identical — both are performance numbers computed from real history — and treating them as one thing is the field's signature suicide. Here they are different strata: a backtest is a cache entry reachable only by its derivation key; evidence is a ledger prefix; and the sole bridge is the trial gate, where multiplicity deflation stands guard. The overfitting problem is not solved — no architecture can solve it — but it is *relocated* from researcher virtue to storage topology.

*Alpha versus execution.* An edge claim and a cost claim jointly determine profit, and PnL alone cannot tell you which one broke. Kept as separate claim families updated from different evidence — the uncontaminated tape versus the firm's own fills — the system can distinguish a dying edge from an outgrown one. Collapsed, every capacity problem masquerades as decay and every decay as slippage.

*Decay versus correction.* Both make an old claim stop guiding action; only one rewrites history. An edge's death closes its validity interval and moves its hazard; it never retracts the evidence of its life, because the track record — the adjudication currency of the entire fork system — must be incorruptible even by hindsight. Especially by hindsight.

*Own fill versus market observation.* Both are prints on a tape; one includes your own shadow. Folding fills into edge evidence launders impact into alpha estimates. They enter the ledger alike but are routed by the predicate registry to different claim families — a one-line distinction at ingestion that prevents a systematic bias no downstream statistics could remove.

*Signal versus strategy.* The atomic hypothesis and the deployed composite, as intervention versus plan before. The strategy's residual axis — parts-predicted versus book-realized — only exists if the parts are separately believed things, which a collapsed "the strategy" would erase.

*Position versus belief.* Tempting to treat the book as expressing the firm's beliefs. It is downstream of them through sizing, constraints, and costs — a deterministic fold of fills, a cache entry. Beliefs are what the fork holds; positions are what it did about them; the gap between the two is itself diagnostic and must remain visible.

---

## What stays swappable, and what does not

Swappable, behind registries and the keying discipline: engines (log, columnar, trade-time KV); bar and sketch definitions; distribution families and update methods per predicate; the multiplicity-correction method (deflated-Sharpe-style, false-discovery control — a versioned catalog choice, and forks may legitimately differ on it); the hazard model's form; regime-detection methods that propose classifiers; t-norms in the algebra; snapshot cadence and staleness floors. Every derivation names its versions, so any swap creates new keys beside old ones.

Not swappable — the commitments this design would be defended on: the ledger is the only thing asserted, and it records the research process, not just the market; propositions are immutable and content-addressed; belief is a pure per-fork function of ledger and priors; backtests live in the cache and reach belief only through the registered-trial gate; point-in-time reads are the only reads the temporal layer will answer, in backtests and in production alike; fills update execution claims, never alpha; and track records, being ledger prefixes, are beyond retroactive reach. Each of these converts a discipline that trading firms famously fail to maintain by policy into a property of the storage topology. That conversion — from virtue to structure — is the whole design.
