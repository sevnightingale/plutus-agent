# DEPLOY.md — fresh runtime deployment (rebuild)

The rebuild deploys to a FRESH `~/.plutus-agent/` — calibration starts from
zero (locked). The old runtime's data stays useful as reference; do not
delete it blind.

## 0. Back up the old runtime — DO THIS FIRST

`~/.plutus-agent/.env` contains **`HL_API_WALLET_KEY` — the on-chain-registered
agent wallet** (TRADING.md fact #3). Wipe it without a backup and every trade
fails silently until the wallet is re-registered via the `approveAgent` flow.

```bash
cp -a ~/.plutus-agent ~/plutus-runtime-v1-backup
```

## 1. Stop and clear the old fleet

```bash
pm2 delete plutus-gateway plutus-watchers
rm -rf ~/.plutus-agent
```

## 2. Install the rebuilt code

```bash
cd ~/plutus-agent && git pull && uv pip install -e ".[all]" --python .venv/bin/python
.venv/bin/plutus-agent --version
```

## 3. Run the wizard

```bash
.venv/bin/plutus-agent setup
```

Single path: model provider → Telegram → **watchlist (≤3 symbols; pick BTC
for the calibration phase)** → Hyperliquid wallets → optional embeddings →
first boot (creates PLUTUS.md / REGIME.md / PERCEPTION.md / strategies/ /
ledger/ / lifecycle.db v2 and seeds the desk crons).

When prompted for wallet values, restore `HL_PUBLIC_ADDRESS`,
`HL_API_WALLET_ADDRESS`, `HL_API_WALLET_KEY` from
`~/plutus-runtime-v1-backup/.env`. If you deliberately want a fully fresh
wallet instead, that means re-running the `approveAgent` registration —
see TRADING.md's recovery runbook.

## 3b. Restore the rest from the backup

After the wizard, merge the FULL backup `.env` — every key in it except
`HL_MASTER_ADDRESS` is still read by the rebuilt code (`DGCLAW_API_KEY`
powers the Arena forum fan-out; `TELEGRAM_ALLOWED_USERS` /
`TELEGRAM_HOME_CHANNEL` gate messaging; `PLUTUS_*_SAFE_ROOT` gate the file
tools; `FIRECRAWL_API_KEY`, `OPENCODE_GO_API_KEY`, `VOYAGE_API_KEY`).
Append whatever the wizard didn't already write; no duplicate keys.

Identity continuity (optional, recommended):

- `SOUL.md` — still injected into main's system prompt. Copy from the
  backup AFTER pruning sections the desk now owns (cron architecture,
  session mechanics, cognitive architecture — PLUTUS.md + AGENT.md
  recipes replace them). Keep the identity sections.
- `memories/` (MEMORY.md, USER.md) — built-in memory tool store; copy as-is.
- `auth.json` — provider credentials; copy back to skip re-auth.

Do NOT copy: `config.yaml` (the old one holds the `session_reset: none`
death-spiral setting; the wizard's fresh one carries the rebuilt
defaults), v1 `strategies/` (incompatible dir-status format — mine as
operator seeds later), v1 `lifecycle.db` (calibration starts from zero,
locked), `price_alerts.json` (still read, but its ranges are stale market
levels — set fresh ones).

## 4. Verify trade readiness

```bash
.venv/bin/python scripts/check_trade_readiness.py   # must print READY
```

## 5. Start the fleet

```bash
pm2 list                       # confirm the expected processes BEFORE saving
pm2 start ecosystem.config.js --only plutus-gateway
pm2 start ecosystem.config.js --only plutus-watchers
pm2 save
```

## 6. First-hour smoke checklist

- [ ] Message Plutus on Telegram → coherent reply that cites PLUTUS.md doctrine.
- [ ] `pm2 logs plutus-gateway --nostream --lines 40` — no errors; runtime
      bootstrap logged the blackboard files it created.
- [ ] One ops tick lands within 30 min (`plutus-agent cron list` shows
      plutus-ops-tick; `~/.plutus-agent/ledger/<today>/` gains a
      `cron-…-plutus-ops-…` transcript).
- [ ] `~/.plutus-agent/PERCEPTION.md` updated after you ask main to refresh
      perception once.
- [ ] **Zero trades** — no graduated strategies exist yet, so
      predictions-only is CORRECT behavior, not a fault.

## What the desk does from here

ops ticks every 30 min (resolve / evaluate / watchdog); main wakes on the
queue (watchers, staleness, escalations, your messages) and orchestrates
perception → regime → predict; predict fills the 10 slots with file-at-birth
hypotheses and registers machine-resolvable predictions; reflect graduates
strategies only past the statistical bars (N≥15, ≥10 correct). The first
trade happens when a strategy earns `active` — patience is structural.
