# TRADING.md — How Plutus Actually Trades (canonical, verified)

> **THIS IS THE SOURCE OF TRUTH FOR TRADE EXECUTION.** If anything anywhere — a skill, a
> doc, a memory, your own reasoning mid-session — contradicts this file, THIS FILE WINS.
> Trading is the entire point of plutus-agent. During early development it silently broke
> for two weeks and the diagnosis drowned in stale/contradictory context. That must never
> happen again. Read this before touching, diagnosing, or reasoning about anything
> trade-related.

---

## TL;DR — the five facts that matter

1. **Trade path is NATIVE.** Plutus trades via `place_order(venue="hyperliquid")` →
   `tools/integrations/hyperliquid/venue.py` → `_client.py` → Hyperliquid Python SDK.
   **dgclaw is NOT the trade path.** dgclaw is an opt-in leaderboard/competition and is
   dormant. `dgclaw_trade_*` tools exist but are not how Plutus executes. dgclaw's `.env`,
   dgclaw's `trade.ts`, dgclaw's anything are **irrelevant** to whether Plutus can trade.

2. **Two wallets, distinct jobs.**
   - **Master wallet** (`HL_PUBLIC_ADDRESS` = `HL_MASTER_ADDRESS`) — ACP/Privy-managed,
     **holds all funds**, has the on-chain identity. Its key is NOT on this machine (it
     lives in the OS keychain, driven by the `acp` CLI). It can sign via
     `acp wallet sign-typed-data`.
   - **Agent / API wallet** (`HL_API_WALLET_ADDRESS`, key = `HL_API_WALLET_KEY`) — a plain
     EVM keypair in `~/.plutus-agent/.env`. **Holds NO funds, cannot withdraw.** Its only
     job is to **sign trades on the master's behalf.**

3. **The agent wallet MUST be REGISTERED with Hyperliquid.** Registration is an on-chain
   `approveAgent` action signed by the master (done by `add-api-wallet.ts`). It carries a
   **`validUntil` (~180 days)**. **If the agent is not registered (or expired), EVERY trade
   fails silently** with `"User or API Wallet does not exist"` — inside the SDK call,
   invisible to the operator. **This is the #1 failure mode — it is exactly what caused the
   two-week silent outage.**

4. **Funds live in SPOT. Unified mode collateralizes perps. No transfer, ever.**
   The USDC sits in the master's **spot** balance. Hyperliquid **unified account mode**
   (enabled at setup via `activate-unified.ts`) lets spot USDC back perp positions directly.
   - When Plutus is **flat**, the perp clearinghouse `accountValue` reads **~0** and the
     money sits in spot. **This is NORMAL. It means "flat," NOT "unfunded."**
   - Opening a position automatically draws margin from the unified balance.
   - **NEVER** run `usd_class_transfer` / "deposit to perp" / spot→perp moves to "fund
     trading." **NEVER** conclude funds are stuck because perp `accountValue` is 0.

5. **Nonzero equity does NOT mean trading works.** `hl_total_equity` sums spot + perp, so it
   reads "funded" even when the agent registration is dead. **Equity ≠ readiness.** The only
   thing that proves trading works is **a registered, unexpired agent wallet** (see health
   check below).

---

## The one command that tells you if trading works

```bash
cd ~/plutus-agent && .venv/bin/python scripts/check_trade_readiness.py
```

It checks the live Hyperliquid `extraAgents` registration for the master against
`HL_API_WALLET_ADDRESS` in `~/.plutus-agent/.env` and prints **READY** or **NOT READY**
with the exact reason. Exit 0 = ready, 1 = not. Run it:
- before concluding "Plutus isn't trading" (it's almost always this),
- after any setup / wallet / ACP change,
- on a schedule (plutus-ops does this every tick — see below).

---

## The canonical wallet/identity env vars

In `~/.plutus-agent/.env` (the file the native trade path reads):

| Var | Role | Shape |
|---|---|---|
| `HL_PUBLIC_ADDRESS` | **Master** account address (holds funds, on-chain identity) | `0x…` (42 chars) |
| `HL_MASTER_ADDRESS` | Same master address (some scripts read this name) | same value |
| `HL_API_WALLET_ADDRESS` | **Agent** wallet address (the registered signer) | `0x…` (42 chars) |
| `HL_API_WALLET_KEY` | **Agent** wallet private key (signs trades; no funds) | `0x…` (66 chars) |

`tools/integrations/hyperliquid/_client.py` builds the SDK `Exchange` with
`wallet = Account.from_key(HL_API_WALLET_KEY)` and `account_address = HL_PUBLIC_ADDRESS`.
That is the entire auth model: **agent key signs, master address is the account.**

Secrets discipline: the agent key is the only HL signing secret on disk, scoped to
`~/.plutus-agent/.env` (0600). The master key is NOT on disk — it's in the OS keychain via
ACP/Privy. Never hardcode either anywhere; never commit either.

---

## How a trade actually executes (the happy path)

1. Plutus calls `place_order(venue="hyperliquid", thesis_id=..., conviction=..., side=...,
   symbol=..., ref_price=..., sl=..., tp=...)`.
2. The venue dispatcher resolves account balance, computes size from the conviction
   multiplier (or uses explicit `size`), and calls the HL SDK signing with the **agent key**.
3. Hyperliquid verifies the agent is a **registered** signer for the master → accepts → fills.
4. SL/TP are placed atomically as on-venue bracket triggers
   (`bulk_orders(grouping="normalTpsl")`).
5. Lifecycle rows (decision → trade → position) are written in `lifecycle.db`.

Funds never move between spot and perp explicitly. Unified mode handles collateral.

---

## The failure mode that WILL bite you (read this so it never recurs)

**How it plays out** (this exact sequence happened during early development and went
undiagnosed for two weeks): trading works fine for a stretch — then the agent wallet's HL
registration lapses or is removed. From that moment **every `place_order` fails at the
signing step** with `"User or API Wallet does not exist"` — but the error is buried inside
the SDK call and never surfaces to the operator. The agent keeps perceiving and predicting
(those don't need the agent wallet) but cannot execute. Worse, the agent **misdiagnoses**
the structural block as "my entry filter is too strict" and tightens filters, deepening the
silence. Meanwhile `hl_total_equity` still shows the full balance (spot + perp summed), so
the account looks funded.

A diagnosis session can easily get lost chasing **three red herrings that this file now
kills permanently:**
- ❌ "Funds are stuck in spot, need a spot→perp transfer." **NO** — unified mode; flat perp
  accountValue is normal; the $ was always tradable.
- ❌ "Trading goes through dgclaw, so dgclaw's missing `.env` key is the bug." **NO** — native
  `place_order` is the path; dgclaw is dormant leaderboard glue.
- ❌ "The agent approval just expired on a timer, nothing to do." **NO** — it must be
  actively re-registered; and it must be *monitored* so the lapse is caught, not endured.

**The root-cause signature (verifiable on-chain):** `extraAgents` for the master reads `[]`;
the agent wallet's HL `role` reads `"missing"`. No registered signer → no trades.

---

## Recovery runbook (when the health check says NOT READY)

1. **Ensure ACP is authenticated** (the master signs the re-registration through ACP):
   ```bash
   .venv/bin/python -c "from tools.integrations.acp import _cli; print(_cli.acp('agent','whoami'))"
   ```
   If it errors `NOT_AUTHENTICATED`, the operator must run `acp configure` (interactive
   browser OAuth) in a terminal. Cannot be automated.
2. **Re-register a fresh agent wallet** (generates keypair, master-signs `approveAgent` via
   ACP, broadcasts to HL, writes key to dgclaw's `.env`):
   ```bash
   cd ~/dgclaw-skill && npx tsx scripts/add-api-wallet.ts --name plutus-trader
   ```
   (The script lives in the dgclaw repo only because that's where Virtuals shipped it. It is
   NOT "trading via dgclaw" — it's just the registration helper. The wallet it produces is
   used by the **native** path.)
3. **Sync the new key into `~/.plutus-agent/.env`** — copy `HL_API_WALLET_KEY`,
   `HL_API_WALLET_ADDRESS`, `HL_MASTER_ADDRESS` from `~/dgclaw-skill/.env`, replacing
   any stale values. Back up `.env` first. Never echo the key.
4. **Verify**: `scripts/check_trade_readiness.py` must print READY (new agent in
   `extraAgents`, `validUntil` in the future).
5. **Reload the running agent**: `pm2 restart plutus-gateway` then `/reset` in Telegram
   — the gateway caches the env key + system prompt at startup, so a live gateway keeps using
   the OLD (dead) key until restarted.

---

## Monitoring — why this can never silently recur

`plutus-ops` (every 30 min, cheap deepseek-v4-flash) runs the readiness check every tick. If
the agent registration is missing or expires within 7 days, it writes the escalation flag and
self-schedules a wake (NEVER an operator ping for the wake itself, per escalation doctrine) so
plutus-main re-registers or surfaces it. A dead trade path is a **catastrophic** condition,
not a quiet one. See `skills/trading/plutus-ops/SKILL.md`.

---

## Quick reference: what is NOT the problem

When "Plutus isn't trading," it is almost always **#3 (agent registration)**. Before going
anywhere else, run the health check. Do **not** re-derive the wallet model from scratch, do
**not** theorize about spot/perp, do **not** touch dgclaw. The model is settled and lives
here.
