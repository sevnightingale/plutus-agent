---
name: post-entry-verify
description: Mandatory post-entry verification — confirm position size, SL/TP orders on-chain before position is considered 'live'
version: 1.0.0
metadata:
  hermes:
    tags: [trading, plutus, risk-management, execution]
    related_skills: [conviction-engine, hl-risk-placement, position-monitor]
---

# Post-Entry Verification

After every `place_order`, the position is NOT live until this verification passes. No naked positions. No assumptions.

## Step 1 — Verify position exists

```
account_state(venue="hyperliquid")
```

Check `open_perp_positions`:
- Position exists for the expected symbol
- Size matches the placed order
- Entry price is within 0.5% of the fill shown in place_order result

If position is missing → the entry never filled. Check open orders. If it's resting as a limit, either wait or cancel.

## Step 2 — Verify SL/TP orders (if placed)

Check `open_orders` for resting trigger/limit orders:
- **Path A (atomic brackets)**: the `place_order` result includes `sl_order_id` and `tp_order_id`. Verify these appear in `open_orders`.
- **Path B (manual reduce-only)**: verify the limit orders you placed separately are resting.
- Check `bracket_warnings` from the place_order result — empty list means both landed.

| Position side | SL should exist? | TP should exist? | Verify |
|--------------|------------------|------------------|--------|
| LONG | If Path A: trigger order. If Path B: CANNOT place (manual only) | Limit sell above entry | TP must be in open_orders |
| SHORT | If Path A: trigger order. If Path B: limit buy above entry | If Path A: trigger order. If Path B: CANNOT place (manual only) | At minimum, SL must be in open_orders |

## Step 3 — Document missing brackets

If ANY bracket is missing:

1. **Can it be placed via Path B?**
   - LONG TP (sell limit above) → YES. Place it now.
   - SHORT SL (buy limit above) → YES. Place it now.
   - LONG SL / SHORT TP → NO. Document as manual-only monitoring.

2. **Record the gap** in a position_evaluation with `thesis_status="entry_verified"` and note what's missing:
   ```
   SL: on-chain ✓ (oid X) | TP: on-chain ✓ (oid Y)
   — or —
   SL: MANUAL ONLY (Path B can't place) | TP: on-chain ✓ (oid Z)
   ```

## Step 4 — If anything is missing and can't be placed

The position is LIVE but has a documented gap. The `position-monitor` skill must check for SL/TP breach on EVERY evaluation. If the monitored price is hit, close via market order immediately.

## Step 5 — Record verification

```
record_event("position_evaluation", {
  position_id: <P>,
  conviction: <same_as_entry>,
  thesis_status: "entry_verified",
  recommended_action: "hold",
  rationale_md: "Post-entry verification: position confirmed at <size> <symbol>. SL: <status>. TP: <status>. <any gaps noted>.",
  snapshot_ids: [<account_state_snapshot>]
})
```

**The position is now live.** Hand off to `position-monitor` for ongoing evaluation.

## Pitfalls

- ❌ **Never skip this step.** An early position once ran naked for 24h because the SL was assumed but never confirmed on-chain.
- ❌ **Don't trust `place_order` result alone.** The on-chain state is the truth. Always verify with `account_state`.
- ❌ **Don't proceed to monitor without documenting gaps.** If a bracket is missing, the next position-monitor tick must know.
- ❌ **Don't assume Path A always works.** A slippage-price estimation bug can reject brackets. Always check `bracket_warnings`.
