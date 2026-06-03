---
name: reconcile-and-reflect
description: Alert-fired on perceived position close — reconcile lifecycle.db with venue truth, record the close, trigger reflection. V2 splits this: mechanical reconcile = plutus-ops; interpretive reflect = plutus-main Phase 1.
version: 1.1.0
metadata:
  hermes:
    tags: [trading, plutus, exit, v2-split]
    related_skills: [plutus-main, plutus-ops, reflect, loss-postmortem, position-monitor]
---

> **V2 split (2026-05-20):**
> - **Mechanical reconcile section** (detect that a position closed off-script, record the
>   close in lifecycle.db, capture exit_reason) → **plutus-ops** does this in its 30-min
>   sweep (Step 3 drift check). ops does NOT call this skill directly — it flags
>   `pending_reflections` in ops_summary for main.
> - **Interpretive reflect section** (loss-postmortem with error_class, lessons,
>   WORLDVIEW updates) → **plutus-main Phase 1** picks up the flagged pending_reflections
>   and runs `loss-postmortem` / `post-trade-reflection` on each.
>
> This skill as a single combined flow is no longer called directly. Its content is
> preserved here as reference for both halves of the split.

# Reconcile and reflect

The `hl_position_status_change` watcher fires when the venue's set of open positions changes. Three causes:
1. You closed it (via `close_position`) — already recorded
2. SL/TP filled outside your direct action — needs catching up
3. Position liquidated — needs catching up + serious reflection

This skill catches up #2 and #3, AND triggers reflection.

## Step 1 — pull venue truth

`fetch_data_point("hl_holdings", {"account_name": "hl_trading"})` — current perp_positions list.

`query_trades(status="open")` — what lifecycle.db thinks is open.

## Step 2 — diff

For each position lifecycle says is open but venue doesn't have:
- That position closed outside our direct action.
- Need to record the close + outcome.

For each venue position lifecycle doesn't have:
- Manual entry by operator? Race condition? Investigate before assuming.
- Surface to operator: "Venue shows position in <symbol> not in lifecycle. Did you place this manually? If yes, I'll register it."

## Step 3 — for each unrecorded close

Pull recent fills from venue: `Info.user_fills_by_time(addr, since=<position open>, until=<now>)` — find the closing fill.

Record the close via the dispatcher (it auto-runs outcome computation):

```
close_position({
  venue: "hyperliquid",
  position_id: <P>,
  conviction: 0.5,                    # default — wasn't your active decision
  exit_reason: "venue_filled_sl" | "venue_filled_tp" | "liquidated",
  thesis_id: <opening thesis id>,
  extra: {ts: <fill ts>, fill_price: <fill px>}  # for the synthetic close trade
})
```

(Note: in Phase 4 the `close_position` dispatcher's venue close_fn is the real venue call. For perceived-close we need a slightly different path — call the dispatcher with `extra.skip_venue_call=true` if available, OR insert the close trade row manually via a reconciliation helper. As of v1 this may need manual SQL — flag in your reflection.)

## Step 4 — reflect on each close

After recording, fire `reflect` skill for each:

- If `r_multiple < -0.5` → use `loss-postmortem` (mandatory)
- Otherwise → use `reflect` (opportunistic on wins, structured on losses)

## Step 5 — update WORLDVIEW.md

Remove closed positions from `open_positions_summary` mirror. Update `portfolio_summary.total_equity_usd`.

## Step 6 — operator notification

For liquidations or SL hits, send a one-line message:

> Position <P> (<sym> <side>) closed — <SL hit | TP hit | liquidation>. r_multiple=<X>. Reflection: #<reflection_id>.

For routine TP hits with positive r, the trade-notify hook will send the message; you don't need to double-up.

## Don't

- Don't assume you closed it just because the venue says no position. Check fills history.
- Don't skip reflection on losses. r < -0.5 mandatorily fires loss-postmortem.
- Don't update lifecycle.db without the dispatcher — atomicity matters. If you must hand-write, wrap in a single transaction.
