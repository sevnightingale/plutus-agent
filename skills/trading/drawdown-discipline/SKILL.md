---
name: drawdown-discipline
description: Soft circuit breaker — at 20% drawdown from peak, pause new positions and surface to operator before re-engaging
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, position-management, discipline]
    related_skills: [position-monitor, weekly-review]
---

# Drawdown discipline

Cumulative drawdown is the single biggest predictor of "agent walked off a cliff with no one looking." This skill enforces a soft pause point — discipline-via-skill, NOT enforced in code (per PLUTUS principle 8). You CAN override; the override is on the record.

## Default thresholds

- **Soft pause at 20%** drawdown from peak equity (last 90 days)
- **Hard pause at 35%** — same as soft, but stronger language to operator + write a `reflection_kind="ad_hoc"` reflection regardless of action

Plutus may tune these in WORLDVIEW.md `operator_state.drawdown_thresholds` once the operator has a stronger view; defaults are conservative.

## When the check runs

Embedded in `position-monitor` step 3. Also runs on-demand from the operator.

## Workflow

### Step 1 — fetch drawdown

`fetch_data_point("hl_drawdown_from_peak", {"account_name": "hl_trading", "lookback_days": 90})`

Returns `{drawdown_pct, drawdown_usd, peak_equity_usd, current_equity_usd}`.

### Step 2 — apply tier

| drawdown_pct | Action |
|---|---|
| < 20% | No-op. Continue. |
| 20–35% | Soft pause: refuse `scale_in` and new `place_order` for fresh theses. Existing positions can still be closed. Surface a clear note to operator. |
| ≥ 35% | Hard pause: same as soft, plus write a `reflection_kind="ad_hoc"` reflection summarizing the recent trades that contributed to drawdown + your read on whether reasoning broke or it was variance. Wait for explicit operator green-light before resuming. |

### Step 3 — surface to operator

For soft + hard, send a one-line message to the operator (use `send_message` if available, otherwise it'll surface in the next session anyway):

> Drawdown at X.X% (peak $Y.YY → current $Z.ZZ). Per drawdown-discipline I've paused new sizing. Existing positions remain managed normally. Tell me when to resume.

For hard, append: "Wrote a reflection at <reflection_id>. Recommend you read it before approving resume."

### Step 4 — record the discipline event

Always:

```
record_event("reflection", {
  reflection_kind: "ad_hoc",
  text_md: "Drawdown discipline triggered at <pct>% (tier: soft|hard). Drawdown=$X, peak=$Y, current=$Z. Posture: <pause description>. Operator notified."
})
```

This goes into the searchable record. `weekly-review` will pull these and report.

### Step 5 — defense rules while paused

While paused:
- ✅ Allowed: `close_position` (defensive exits), `tighten_sl`, `scale_out`
- ❌ Refused: `place_order` for fresh theses, `scale_in` on existing
- Refusal is YOUR job — the dispatcher won't enforce it. If you find yourself wanting to override, write an `ad_hoc` reflection FIRST explaining why.

## Don't

- Don't auto-resume. Operator's call. The whole point of the pause is to break momentum.
- Don't take "I've thought about it more" as an override reason. The pause exists because in-the-moment reasoning is exactly what got us to drawdown.
