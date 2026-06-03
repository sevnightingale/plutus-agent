---
name: prediction-factory
description: V2 plutus-main Phase 5. Generate 3-10 falsifiable predictions per beat across existing-strategy / experimental / regime-stress categories. Predictions are the discovery loop — capital expensive, predictions free. Experimental strategies live only as predictions until they graduate.
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, v2, predictions, discovery]
    target_tier: plutus-main
    target_phase: 5
    related_skills: [plutus-main, calibration-review, strategy-author, strategy-curator]
---

# Prediction factory — the discovery loop

The prediction factory is the load-bearing pattern of V2. Predictions are free; capital is expensive. **The system discovers edges through abundance, not invents them through cleverness.** Generate 100 predictions across 10 hypotheses; let calibration tell which 2 are non-random; formalize those as strategies.

I'm called by plutus-main Phase 5 every beat. Composition target: 3-10 predictions per beat. Skip ONLY if previous beat exceeded 100 tool calls (aggressive bounding mode).

---

## What is and isn't a prediction

| Yes — register as prediction | No — record differently |
|---|---|
| "BTC will hold above $69,500 for next 6h" | "BTC is at $69,800" → observation |
| "Funding flip on BTC → trend continues 8h" | "Funding flipped on BTC" → observation |
| "If CVD percentile >85 AND DXY trending down, BTC holds support 6h" | "I see CVD percentile 87" → observation |
| "ETH/BTC ratio inflecting up → alt outperformance 12h" | Open ETH long → thesis + decision + trade |

**Predictions are pre-registered falsifiable claims with a horizon, no capital risk.** That's what makes them free + repeatable + comparable. Observations are passive notes. Theses drive trades. Don't conflate.

---

## Per-beat composition target

Three categories, each a different epistemic function:

### 1. Existing-strategy predictions (1-3 per beat)

For each active/trial strategy that DIDN'T trigger a trade this beat but had a borderline setup (composite conviction near threshold but below it), register a prediction tagged with that `strategy_name`. Maintains calibration even when conviction was just below trade-threshold.

Example:
```
strategy_name = "support-hold"
# Current state: BTC at $70,200, support at $69,800, CVD healthy, RSI 48 (not capitulating)
# Conviction borderline: composite ~0.42, threshold 0.45 — didn't trade
# But prediction is cheap:
record_prediction(
    strategy_name="support-hold",
    regime_tag="range_bound",
    claim_md="BTC support-hold setup. Price near $69,800 support with CVD healthy + RSI 48. Predict BTC holds above $69,500 for 6h.",
    success_criteria_json={"data_point": "hl_price", "params": {"symbol": "BTC"}, "compare": ">", "value": 69500, "at_or_after_ts": <now + 6h>},
    failure_criteria_json={"data_point": "hl_price", "params": {"symbol": "BTC"}, "compare": "<=", "value": 69500, "at_or_after_ts": <now + 6h>},
    horizon_ts=<now + 6h unix>,
    conviction=0.58,  # I'm ~58% confident
    snapshot_ids_json=[<current snapshots: hl_price, hl_cvd, ta_rsi>],
)
```

### 2. Experimental predictions (2-4 per beat)

Tagged with provisional `strategy_name="experimental-<descriptor>"`. **No strategy file exists** — the file is authored at graduation (N≥10 resolved + ≥55% calibration). Experimental strategies live only as accumulated predictions.

Bootstrap experimental names to iterate on (extend with more as patterns emerge):

| Experimental name | Pattern |
|---|---|
| `experimental-cvd-macro` | BTC CVD percentile >85 + DXY trending down → BTC holds 6h |
| `experimental-dominance-rotation` | ETH/BTC ratio inflecting up + alt CVD accumulation → alt outperformance 12h |
| `experimental-funding-momentum` | Funding rate flip + price momentum continuation → trend continues 8h |
| `experimental-orderbook-imbalance` | bid/ask depth ratio extreme → reversal within 4h |
| `experimental-eth-btc-ratio` | ETH/BTC ratio extreme + funding alignment → mean reversion 12h |
| `experimental-hype-rotation` | HYPE outperformance + BTC.D drop → HYPE continuation 12h |
| `experimental-macro-vix-spike` | VIX +20% in 1h → crypto vol expansion within 4h |
| `experimental-cross-tf-divergence` | Daily/15m RSI divergence + CVD confirmation → swing direction within 24h |

Plutus can author new experimental names freely — just keep them named cleanly and consistent. The first time an `experimental-<x>` name appears in a prediction, it's "registered" implicitly (calibration queries find it via `theses/predictions.strategy_name`).

Example experimental:
```
record_prediction(
    strategy_name="experimental-funding-momentum",
    regime_tag="momentum_continuation",
    claim_md="BTC funding flipped from -0.005% to +0.012% over last 4h AND price momentum healthy (1h close > 4h MA, 4h close > daily MA). Predict BTC trends up >1.5% within next 8h.",
    success_criteria_json={"data_point": "hl_price", "params": {"symbol": "BTC"}, "compare": ">", "value": <current * 1.015>, "at_or_after_ts": <now + 8h>},
    failure_criteria_json={"data_point": "hl_price", "params": {"symbol": "BTC"}, "compare": "<", "value": <current * 0.985>, "at_or_after_ts": <now + 8h>},  # asymmetric: a flat result is "ambiguous", not failed
    horizon_ts=<now + 8h>,
    conviction=0.60,
    snapshot_ids_json=[<funding snapshot, price snapshot, ta_sma snapshots>],
)
```

### 3. Regime stress tests (1-2 per beat)

Tag an EXISTING strategy with an UNUSUAL regime to test edge boundaries. "Does support-hold work in momentum_continuation? Predict yes/no with 12h horizon." Reveals when a strategy's regime_applicability list is too narrow OR too broad.

Example:
```
record_prediction(
    strategy_name="support-hold",      # existing strategy
    regime_tag="momentum_continuation", # NOT in its regime_applicability list (which is range_bound + accumulation)
    claim_md="Stress test: support-hold pattern in momentum_continuation regime. Conditions met (BTC at $70k support, CVD healthy) but regime is trending up. Predict whether support-hold's edge survives outside its declared regime — outcome will inform whether regime_applicability should expand or stay narrow.",
    success_criteria_json={"data_point": "hl_price", "params": {"symbol": "BTC"}, "compare": ">", "value": 69500, "at_or_after_ts": <now + 12h>},
    failure_criteria_json={"data_point": "hl_price", "params": {"symbol": "BTC"}, "compare": "<=", "value": 69500, "at_or_after_ts": <now + 12h>},
    horizon_ts=<now + 12h>,
    conviction=0.45,  # I'm uncertain — that's the point
    snapshot_ids_json=[<snapshots>],
)
```

---

## Every prediction MUST include

| Field | Why |
|---|---|
| `strategy_name` | Real or experimental. Calibration is sliced by this. Untagged predictions destroy the analytical substrate. |
| `regime_tag` | Current regime at registration. Lets calibration ask "does this strategy work across regimes?" |
| `claim_md` | The falsifiable claim in plain English. Forces honest articulation. |
| `success_criteria_json` | Machine-checkable: data point + comparison + value (+ optional `at_or_after_ts` for time-bounded checks). plutus-ops resolves on horizon by fetching the data point and comparing. |
| `failure_criteria_json` | Machine-checkable inverse. Use asymmetric for "ambiguous" middle band. |
| `horizon_ts` | When ops will resolve. |
| `conviction` | My predicted probability (0..1). This is what gets calibrated. |
| `snapshot_ids_json` | Baseline readings at registration. conviction-engine needs these for weight updates after resolution. |

---

## Graduation logic (referenced — executed by `calibration-review` skill)

`calibration-review` runs every Sunday OR when ops flags `experimental_graduation_candidates` (N≥10 in a single tick is the threshold for flagging).

For any `strategy_name` with N≥10 resolved predictions:

| Outcome | Action |
|---|---|
| Calibration ≥55% with ≥2 regime contexts | **Promote**: `strategy-author` skill writes `~/.plutus-agent/strategies/observation/<name>.md` at `strategy_conviction: 0.2`. From `observation/` it trades at tiny multipliers (composite ~0.45 → 4x notional) while accumulating real trade outcomes. |
| Calibration <30% with N≥20 and ≥2 regime contexts | **Revoke**: `record_observation(kind="edge_revoked", text_md="<x> failed to demonstrate edge. N=<n>, calib=<%>. Specific failure mode: <X>")`. Stop tagging new predictions with that name. |
| 30-55% with any N | **Continue observing.** No file, no action. |

**Rationale on the low N≥10 threshold:** experimental strategies graduate FAST to `observation/` stage at low `strategy_conviction=0.2`. The multiplier formula (`20^composite`) means even at thesis_conv=1.0, the composite is `sqrt(0.2 × 1.0) ≈ 0.447`, so multiplier ≈ 4x. That's a safe tiny size. Real trading outcomes refine `strategy_conviction` over time (via Sunday calibration-review). Operator override: "low strategy_conviction is the safety mechanism, not high N."

---

## Target volume

- 4 beats/day × 5 predictions average = 20/day
- Range: 12/day (quiet beats, single existing-strategy + 1 experimental + 1 stress) to 40/day (rich days, multiple existing + 4 experimental + 2 stress per beat)

If I'm consistently below 12/day, the discovery loop is starved. If I'm above 40/day, I'm padding (cost without information).

---

## Skip conditions

**Skip Phase 5 entirely** when plutus-main's previous beat exceeded 100 tool calls (the bounded mode). When skipped, the beat summary observation's `predictions_registered: 0` shows it — next beat will pick back up.

**Reduce experimental count to 1** when:
- Current beat has already executed 2+ trades (Phase 4 was busy → spend less on Phase 5)
- Macro cache is stale (regime context uncertain → fewer regime-tagged claims)

Existing-strategy predictions (category 1) are highest priority — always do at least 1 unless skipping all of Phase 5.

---

## Pitfalls

- ❌ **Registering predictions that aren't falsifiable.** "BTC will probably move" is not a prediction; "BTC > $X at time Y" is.
- ❌ **Conviction=0.5 on every prediction.** Be honest. 0.5 is "I have no edge here" and that's data — but if I'm registering everything at 0.5 I'm not calibrating, I'm checkbox-ing.
- ❌ **Forgetting `snapshot_ids_json`.** Without baseline readings at registration, conviction-engine can't compute weight updates after resolution. Always attach.
- ❌ **Spinning up an experimental name and using it once.** Names compound across beats — register multiple predictions under the same `experimental-<x>` over time so calibration accumulates.
- ❌ **Skipping regime stress tests because they "feel artificial."** They're the cheapest way to discover regime_applicability mistakes. 1-2 per beat is the minimum.
- ❌ **Authoring a strategy file before N≥10 + ≥55% calibration.** Premature. The file is the artifact of graduation, not the precursor to discovery.
