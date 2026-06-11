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
for the calibration phase)** → Hyperliquid wallets → optional desk
integrations (Degen Arena `DGCLAW_API_KEY`, Firecrawl, Voyage embeddings —
all skippable, paste from the backup `.env`) → first boot (creates
PLUTUS.md / REGIME.md / PERCEPTION.md / strategies/ / ledger/ /
lifecycle.db v2 and seeds the desk crons). The wizard ends with a
Desk Integrations summary showing what was skipped and what each skip
costs; re-run any trading-specific step later with `plutus setup trading`.

When prompted for wallet values, restore `HL_PUBLIC_ADDRESS`,
`HL_API_WALLET_ADDRESS`, `HL_API_WALLET_KEY` from
`~/plutus-runtime-v1-backup/.env`. If you deliberately want a fully fresh
wallet instead, that means re-running the `approveAgent` registration —
see TRADING.md's recovery runbook.

Do NOT re-run the Virtuals/ACP provisioning (acp-cli `configure` /
`agent create` / `add-signer`, `dgclaw.sh join`, `add-api-wallet.ts`).
That one-time flow is where these keys came from — the ACP agent's
managed wallet IS the HL master, and `add-api-wallet.ts` generated and
on-chain-registered the agent wallet — and all of its state survives the
runtime wipe: the ACP agent lives on Virtuals' side, the registration is
on-chain, and the tooling lives outside the wipe path (`~/acp-cli`,
`~/.config/acp-cli`, `~/dgclaw-skill`). Re-running `add-api-wallet.ts`
would register a fresh agent wallet and invalidate the backed-up
`HL_API_WALLET_KEY`.

## 3b. Restore the rest from the backup

After the wizard, merge what's left of the backup `.env` — every key in it
except `HL_MASTER_ADDRESS` is still read by the rebuilt code. The wizard
now collects the provider key, wallets, and the optional integrations
(`DGCLAW_API_KEY`, `FIRECRAWL_API_KEY`, `VOYAGE_API_KEY`), so the merge
remainder is: `TELEGRAM_ALLOWED_USERS` / `TELEGRAM_HOME_CHANNEL` (gate
messaging), `PLUTUS_READ_SAFE_ROOT` / `PLUTUS_WRITE_SAFE_ROOT` (gate the
file tools), `PLUTUS_CRON_TIMEOUT`, `DGCLAW_SKILL_ROOT`. Append whatever
the wizard didn't already write; no duplicate keys.

Identity continuity (optional, recommended):

- `SOUL.md` is DEAD — PLUTUS.md is the identity file the prompt builder
  injects now. Do not copy it back. If the old SOUL.md has personality
  lines worth keeping (nature, disposition, relationships), fold them into
  the new PLUTUS.md "## Doctrine" zone by hand — that zone is yours.
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
