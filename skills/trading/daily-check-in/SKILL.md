---
name: daily-check-in
description: "[V1 — DEPRECATED in V2] End-of-day report to the operator. V2 folds this into the 21Z plutus-main beat's natural synthesis — no separate cron, no separate skill flow. Kept for reference."
version: 1.1.0
metadata:
  hermes:
    tags: [trading, plutus, periodic-review, operator-comms, deprecated-in-v2]
    related_skills: [plutus-main, weekly-review, calibration-review, observation-journal]
---

> **V2 status (2026-05-20):** This skill is DEPRECATED. The dedicated `plutus-daily-check-in`
> cron was deleted in Phase D. End-of-day reporting now happens naturally in the 21Z
> plutus-main beat (Phase 7 synthesis) — Plutus speaks if anything noteworthy happened,
> stays silent if not. No separate skill needed.

# Daily Check-In

Once per day I send the operator a short report on what happened. Goal: operator stays in the loop without having to ask "what's going on?". Different from weekly-review — daily is light, narrative, conversational. Weekly is structural.

## When to run

Daily at 22:00 UTC (cron-fired). Adjustable per operator preference.

## Step 1 — gather the day's data

Pull in parallel via the existing query tools (use the last 24h window):

```
query_performance(period_days=1)               # PnL today, trades count
query_trades(date_from=<24h ago>)              # all trades opened/closed today
query_predictions(status="resolved", limit=50) # predictions resolved today
query_predictions(status="pending", limit=50)  # what's still outstanding
query_observations(since_ts=<24h ago>, limit=50)  # journal entries
query_capital_movements(since_ts=<24h ago>)    # any deposits/withdrawals
query_equity_curve(period_days=1)              # equity start/end, intraday peak/trough
fetch_data_point("hl_total_equity", {"account_name": "hl_trading"})  # right-now equity
```

Read WORLDVIEW.md current state from the prompt — already injected.

## Step 2 — synthesize

Write a short narrative (NOT a data dump). Aim for 4-8 sentences. The operator wants the *story* of the day, not a spreadsheet. Cover:

- **Today's headline** — what's the one thing that mattered? "Held BTC short through volatility" or "CVD divergence-fade prediction resolved correct" or "regime shifted from distribution_breakdown to chop"
- **Position status** — what's open, how is it doing, conviction state
- **Trades closed today** — wins/losses with brief context (which strategy, what happened)
- **Predictions registered + resolved** — count + notable outcomes
- **Regime read** — what regime am I in, did it shift today, any notable signals
- **Strategy library status** — anything moving stages (observation→trial, edge_decay flag firing)
- **Equity** — start/end/PnL today
- **Notable observations** — any `pattern_candidate`, `edge_revoked`, or operator-input entries worth flagging

## Step 3 — send via Telegram

Use the operator's notifications.trade_chat_id (or alert_chat_id if separate) — fall back to the gateway's default chat if not configured. Format with light markdown:

```
📅 Daily check-in — <YYYY-MM-DD>

<headline sentence>

**Open positions:** <P or "none">
**Trades today:** <N> (<W wins / L losses>, +/-$X)
**Predictions:** <registered count> registered, <resolved count> resolved (<correct/wrong breakdown>)
**Regime:** <global> | per-symbol: BTC=X, ETH=Y, SOL=Z (<changed | unchanged>)
**Equity:** $<start> → $<end> (<+/-X% today>)

<2-3 sentences synthesis: the story of the day, what's different from yesterday, what I'm watching tomorrow>
```

Keep total message under ~600 characters when possible.

## Step 4 — record the check-in

After sending, fire one observation:

```
record_observation(
  kind="noticed",
  text_md="Daily check-in sent: <one-line summary>",
  structured_tags={"event": "daily_check_in"}
)
```

This way next day's check-in can reference yesterday's via query.

## Step 5 — (optional) update WORLDVIEW.md recent_learnings

If the day produced a learning worth keeping in worldview (not just observations table), add a one-line entry:

```yaml
recent_learnings:
  - {date: <today>, text: "..."}
```

Don't add filler. Most days produce nothing for recent_learnings.

## Pitfalls

- ❌ **Don't dump every trade and observation into the message.** The operator wants the story, not the log.
- ❌ **Don't repeat the same observations every day.** If nothing changed, say so concisely. "Quiet day. Position #57 still open, conviction unchanged. No new setups in current regime."
- ❌ **Don't send the message if the gateway isn't configured for delivery.** Check `notifications.trade_chat_id` exists; if not, write the report to the journal as `kind="noticed"` and skip the send.
- ❌ **Don't run more than once per UTC day.** Cron handles this; don't manually re-fire.
- ❌ **Don't include token-cost / debug info in the operator message.** That's for the journal.
