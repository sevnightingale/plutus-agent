# SETUP.md — from absolute zero to a running desk

The complete path for a brand-new operator: no repo, no accounts, no wallets,
nothing. Each step says what it creates and which credential falls out of it;
the wizard at the end collects those credentials. **Migrating an existing
runtime instead? Use `DEPLOY.md` — same wizard, but every credential already
exists in your backup.**

## The pieces (and why each exists)

| Piece | What it is | Yields |
|---|---|---|
| plutus-agent (this repo) | The desk runtime — seven agents, wizard, pm2 fleet | — |
| LLM provider account | The desk's reasoning | provider API key |
| Telegram bot (optional) | The operator channel | `TELEGRAM_BOT_TOKEN` |
| Virtuals ACP agent | Plutus's on-chain identity; its **managed wallet is the master** — it holds all funds, its key never touches this machine | `ACP_AGENT_WALLET` (an address) |
| Hyperliquid API wallet | The trade **signer** — a plain keypair that holds nothing and must be registered on-chain via `approveAgent` | `HL_API_WALLET_ADDRESS` + `HL_API_WALLET_KEY` |
| Degen Arena / dgclaw (optional) | Perps competition + public forum (the track record's legibility layer) | `DGCLAW_API_KEY` |
| Firecrawl / Voyage (optional) | Web research / semantic search | `FIRECRAWL_API_KEY`, `VOYAGE_API_KEY` |

The two-wallet model is TRADING.md fact #2 — read TRADING.md before going
live. Steps 3–6 are the trading provisioning; **all of them are skippable**
— skip them and the desk runs research-only (predictions, no trades) until
you come back.

## 0. Prerequisites (the box)

Linux, macOS, or WSL2 (no native Windows). You need: `git`, Python ≥ 3.11,
[`uv`](https://docs.astral.sh/uv/), Node ≥ 18 + `npm` (for the Virtuals
tooling), and `pm2` (`npm install -g pm2`) for the fleet.

## 1. Install plutus-agent

```bash
git clone https://github.com/sevnightingale/plutus-agent.git ~/plutus-agent
cd ~/plutus-agent
uv venv .venv
uv pip install -e '.[all]' --python .venv/bin/python
.venv/bin/plutus-agent --version
```

(`plutus` and `plutus-agent` are the same CLI; symlink either into
`~/.local/bin` if you want it on PATH.)

## 2. Accounts that are just "sign up, copy a key"

- **LLM provider** — e.g. an OpenRouter key. Required; the wizard's first step.
- **Telegram bot** — message @BotFather, `/newbot`, copy the token. Optional;
  without it Plutus is CLI-only.
- **Firecrawl** (firecrawl.dev) and **Voyage** (voyageai.com) keys. Optional;
  the wizard tells you exactly what skipping each one costs.

## 3. Virtuals ACP — create the agent and the master wallet (one-time)

This is the step that creates Plutus's on-chain identity and the wallet that
will hold the money.

```bash
npm install -g @virtuals-protocol/acp-cli
acp configure                       # browser OAuth — creates/links your Virtuals account
acp agent create --name Plutus      # creates the ACP agent + its managed wallet
acp agent add-signer                # P256 signing keys (browser approval; key → OS keychain)
acp agent whoami --json             # note walletAddress
```

That `walletAddress` **is your `ACP_AGENT_WALLET`** — the master. Its private
key lives in Virtuals/Privy custody and the OS keychain, never in any `.env`.
CLI state lands in `~/.config/acp-cli`; both `acp configure` and `add-signer`
print a browser URL and long-poll for your approval — run them in your own
terminal, not through an agent session.

## 4. dgclaw-skill — Arena membership and the registration helpers

```bash
git clone https://github.com/Virtual-Protocol/dgclaw-skill.git ~/dgclaw-skill
cd ~/dgclaw-skill && npm install
./scripts/dgclaw.sh join            # registers in Degen Claw → DGCLAW_API_KEY
```

`join` is only needed for Arena participation — but clone the repo even if
you skip the Arena: `activate-unified.ts` and `add-api-wallet.ts` (steps 5–6)
are the wallet-provisioning helpers Virtuals ships, and they live here. The
full procedure reference is `skills/dgclaw/SKILL.md`.

## 5. Fund the master wallet

Top up the ACP wallet with USDC on **Base** (`acp wallet topup`, or send USDC
to the `acp agent whoami` address — Base mainnet ONLY; wrong-chain transfers
are unrecoverable). Then deposit into Hyperliquid via the ACP `perp_deposit`
job (bridges Base → Arbitrum → Hyperliquid) — exact commands in
`skills/dgclaw/SKILL.md` Step 2. Start small.

## 6. Unified mode + the API wallet (one-time, in `~/dgclaw-skill`)

```bash
npx tsx scripts/activate-unified.ts              # spot USDC collateralizes perps (TRADING.md fact #4)
npx tsx scripts/add-api-wallet.ts --name plutus-trader
```

`add-api-wallet.ts` generates the API-wallet keypair, has the master sign the
on-chain `approveAgent` registration via ACP, and writes `HL_API_WALLET_KEY` /
`HL_API_WALLET_ADDRESS` into `~/dgclaw-skill/.env`. **This registration is
what makes trading work** — it expires (~180 days), and an unregistered API
wallet fails every trade silently (TRADING.md fact #3). After this, funds
live in SPOT and stay there — never run spot→perp transfers.

## 7. The setup wizard

```bash
.venv/bin/plutus-agent setup
```

Single first-time path; paste as you go:

1. **Model & provider** — the LLM key from step 2.
2. **Messaging** — the Telegram token (or skip; `plutus setup gateway` later).
3. **Watchlist** — ≤3 symbols; BTC is the right answer for calibration.
4. **Hyperliquid wallets** — `ACP_AGENT_WALLET` (from `acp agent whoami`),
   then `HL_API_WALLET_ADDRESS` / `HL_API_WALLET_KEY` (from
   `~/dgclaw-skill/.env`). Enter skips any of them (research-only mode).
5. **Optional desk integrations** — `DGCLAW_API_KEY`, Firecrawl, Voyage; all
   skippable, each skip's cost stated in the end-of-setup summary.
6. **First boot** — creates PLUTUS.md / REGIME.md / PERCEPTION.md,
   `lifecycle.db`, and seeds the desk crons (`plutus-ops-tick`, `plutus-eod`).

Re-run any trading piece later with `plutus setup trading`.

## 8. The keys the wizard doesn't ask for

Append to `~/.plutus-agent/.env` as needed:

- `TELEGRAM_ALLOWED_USERS` / `TELEGRAM_HOME_CHANNEL` — gate who can talk to
  the gateway and where it reports home.
- `PLUTUS_READ_SAFE_ROOT` / `PLUTUS_WRITE_SAFE_ROOT` — gate the file tools.
- `DGCLAW_SKILL_ROOT` — only if the clone isn't at `~/dgclaw-skill`.
- `PLUTUS_CRON_TIMEOUT` — only to override the cron-run timeout.

## 9. Verify before starting anything

```bash
.venv/bin/python scripts/check_trade_readiness.py   # must print READY
.venv/bin/plutus-agent setup-status                 # one-screen dashboard of every piece
```

NOT READY almost always means the API-wallet registration (TRADING.md
recovery runbook). READY with skipped trading steps is impossible — research-
only mode is the expected state until steps 3–6 are done.

## 10. Start the fleet

```bash
cd ~/plutus-agent
pm2 start ecosystem.config.js --only plutus-gateway
pm2 start ecosystem.config.js --only plutus-watchers
pm2 save
```

Then run the first-hour smoke checklist in `DEPLOY.md` §6. Expect **zero
trades** at first: no strategy has graduated yet, so predictions-only is
correct behavior, not a fault. Pause execution any time with
`touch ~/.plutus-agent/HALT`.

---

**Agent-assisted alternative for steps 3–6:** once the wizard has a provider
key, you can chat with Plutus and ask it to walk you through trading setup.
It has ACP tools (`acp_install`, `acp_configure`, `acp_agent_create`,
`acp_agent_add_signer`, `acp_wallet_topup`, `acp_persist_env_after_setup`)
that either run the non-interactive steps directly or hand you the exact
command for the browser-OAuth ones, plus the vendored `skills/dgclaw` skill
for the rest. `plutus-agent setup-status` shows progress either way.
