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
   `trading/integrations/hyperliquid/venue.py` → `_client.py` → Hyperliquid Python SDK.
   **dgclaw is NOT the trade path.** dgclaw is an opt-in leaderboard/competition and is
   dormant. `dgclaw_trade_*` tools exist but are not how Plutus executes. dgclaw's `.env`,
   dgclaw's `trade.ts`, dgclaw's anything are **irrelevant** to whether Plutus can trade.
   **The ACP CLI is NOT the trade path either** — `acp` does have HL perp/spot order
   commands, but they route through the Virtuals backend (it builds the tx, the
   keychain signer signs). Capable, unused: a third-party backend on the money path,
   policy-gated signing, and no atomic SL/TP brackets. Plutus signs orders itself.

2. **Two wallets, distinct jobs.**
   - **ACP agent wallet — the MASTER** (`ACP_AGENT_WALLET`) — the Virtuals ACP agent's
     managed wallet (ACP/Privy). **Holds all funds**, has the on-chain identity. Its key
     is NOT on this machine (it lives in the OS keychain, driven by the `acp` CLI). It
     can sign via `acp wallet sign-typed-data`.
   - **API wallet — the SIGNER** (`HL_API_WALLET_ADDRESS`, key = `HL_API_WALLET_KEY`) — a
     plain EVM keypair in `~/.plutus-agent/.env`. **Holds NO funds, cannot withdraw.** Its
     only job is to **sign trades on the master's behalf.**

   ⚠️ Naming collision: Hyperliquid's own docs call the API wallet an "agent wallet."
   In this repo, "ACP agent wallet" always means the master; the signer is always
   called the **API wallet**.

3. **The API wallet MUST be REGISTERED with Hyperliquid.** Registration is an on-chain
   `approveAgent` action signed by the master (done by `add-api-wallet.ts`). It carries a
   **`validUntil` (~180 days)**. **If the API wallet is not registered (or expired), EVERY
   trade fails silently** with `"User or API Wallet does not exist"` — inside the SDK call,
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
   reads "funded" even when the API-wallet registration is dead. **Equity ≠ readiness.** The
   only thing that proves trading works is **a registered, unexpired API wallet** (see health
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
- on a schedule (plutus-ops fetches the same verdict as the `hl_trade_readiness`
  data point every tick — see below). Plutus itself should use the data point.

---

## The canonical wallet/identity env vars

In `~/.plutus-agent/.env` (the file the native trade path reads):

| Var | Role | Shape |
|---|---|---|
| `ACP_AGENT_WALLET` | **Master** account address — the ACP agent's managed wallet (holds funds, on-chain identity) | `0x…` (42 chars) |
| `HL_API_WALLET_ADDRESS` | **API wallet** address (the registered signer) | `0x…` (42 chars) |
| `HL_API_WALLET_KEY` | **API wallet** private key (signs trades; no funds) | `0x…` (66 chars) |

(Renamed 2026-06-11: `ACP_AGENT_WALLET` was previously `HL_PUBLIC_ADDRESS`, with
`HL_MASTER_ADDRESS` as a script alias. Neither old name is read anymore. The
external dgclaw-skill's own `.env` still uses `HL_MASTER_ADDRESS` — that file
belongs to its scripts, not to us.)

`trading/integrations/hyperliquid/_client.py` builds the SDK `Exchange` with
`wallet = Account.from_key(HL_API_WALLET_KEY)` and `account_address = ACP_AGENT_WALLET`.
That is the entire auth model: **API-wallet key signs, master address is the account.**

Secrets discipline: the API-wallet key is the only HL signing secret on disk, scoped to
`~/.plutus-agent/.env` (0600). The master key is NOT on disk — it's in the OS keychain via
ACP/Privy. Never hardcode either anywhere; never commit either.

---

## The money measures (glossary — canonical definitions)

Every number the codebase reports about money is one of these. Code computes them in
ONE place (`equity_breakdown` in `trading/integrations/hyperliquid/data_points.py`);
everything else reuses it.

| Measure | Definition | Used by |
|---|---|---|
| `equity_usd` | `spot_usdc + perp_account_value` — **THE account-worth number** for the whole unified cross-margin account | sizing base (risk-budget bands), Live State snapshot, drawdown, the balance-change alert |
| `spot_usdc` | spot clearinghouse USDC total — where idle funds *display* under unified mode | legibility split in account_state |
| `perp_account_value` | perp-side `marginSummary.accountValue` (margin in use + unrealized PnL) — **≈ 0 when flat is NORMAL** | legibility split; never used alone for account worth |
| `withdrawable_usd` | what could leave the venue right now | operator info |
| `entry_account_value` | `equity_usd` measured at fill time, written on the position row | denominator for realized leverage; reflect's sizing review |
| `leverage` (realized) | `notional / entry_account_value`, measured post-fill — bands are doctrine, code measures | positions table, `sizing_performance` |
| drawdown | current `equity_usd` vs its peak over snapshot history | `hl_drawdown_from_peak` |

Anti-confusions, permanently settled: "balance moved" on a position open/close is margin
*display* shifting inside the one unified balance, not a transfer; `hl_holdings`'
`account_value` is the perp-side number, not account worth; and no equity figure, however
healthy, says anything about whether trading *works* (that's `hl_trade_readiness`).

---

## How a trade actually executes (the happy path)

1. Plutus calls `place_order(venue="hyperliquid", thesis_id=..., conviction=..., side=...,
   symbol=..., ref_price=..., sl=..., tp=...)`.
2. The venue dispatcher resolves account balance, computes size from the conviction
   multiplier (or uses explicit `size`), floors it to the asset's `szDecimals` (the
   SDK *rejects* finer precision rather than rounding), and calls the HL SDK signing
   with the **API-wallet key**.
3. Hyperliquid verifies the API wallet is a **registered** signer for the master → accepts
   → fills.
4. SL/TP are placed atomically as on-venue bracket triggers
   (`bulk_orders(grouping="normalTpsl")`).
5. Lifecycle rows (decision → trade → position) are written in `lifecycle.db`.

Funds never move between spot and perp explicitly. Unified mode handles collateral.

---

## How capital enters the account (the deposit path)

Adding USDC goes through the **`perp_deposit` ACP job on the Degen Claw agent**
(provider `0xd478a8B40372db16cA8045F28C6FE07228F3781A`) — the `dgclaw` skill has the
exact two-command sequence (create job + fund). The provider handles the entire
Base → Arbitrum → Hyperliquid bridge; the desk never touches chains, bridges, or raw
transfers. Minimum 6 USDC, SLA ~30 min. First used 2026-07-02 ($60, landed in ~30 min).

Two things this is NOT:
- **Not the trade path.** dgclaw's `trade.ts` exists but trades go through
  `place_order(venue="hyperliquid")` only (fact #2).
- **Not a spot→perp transfer.** The deposit lands in the unified balance; collateral
  handling stays automatic (fact #4).

Deposits require live ACP auth (`acp_auth_readiness` — ops watches it every tick).
A dead ACP auth blocks deposits but never trading; the HL API-wallet registration is
a separate, on-chain credential.

---

## The failure mode that WILL bite you (read this so it never recurs)

**How it plays out** (this exact sequence happened during early development and went
undiagnosed for two weeks): trading works fine for a stretch — then the API wallet's HL
registration lapses or is removed. From that moment **every `place_order` fails at the
signing step** with `"User or API Wallet does not exist"` — but the error is buried inside
the SDK call and never surfaces to the operator. The agent keeps perceiving and predicting
(those don't need the API wallet) but cannot execute. Worse, the agent **misdiagnoses**
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
the API wallet's HL `role` reads `"missing"`. No registered signer → no trades.

---

## Recovery runbook (when the health check says NOT READY)

1. **Ensure ACP is authenticated** (the master signs the re-registration through ACP):
   ```bash
   .venv/bin/python -c "from trading.integrations.acp import _cli; print(_cli.acp('agent','whoami'))"
   ```
   If it errors `NOT_AUTHENTICATED`, the operator must run `acp configure` (interactive
   browser OAuth) in a terminal. Cannot be automated.
2. **Re-register a fresh API wallet** (generates keypair, master-signs `approveAgent` via
   ACP, broadcasts to HL, writes key to dgclaw's `.env`):
   ```bash
   cd ~/dgclaw-skill && npx tsx scripts/add-api-wallet.ts --name plutus-trader
   ```
   (The script lives in the dgclaw repo only because that's where Virtuals shipped it. It is
   NOT "trading via dgclaw" — it's just the registration helper. The wallet it produces is
   used by the **native** path.)
3. **Sync the new key into `~/.plutus-agent/.env`** — copy `HL_API_WALLET_KEY` and
   `HL_API_WALLET_ADDRESS` from `~/dgclaw-skill/.env` as-is, and dgclaw's
   `HL_MASTER_ADDRESS` value into our `ACP_AGENT_WALLET`, replacing any stale values.
   Back up `.env` first. Never echo the key.
4. **Verify**: `scripts/check_trade_readiness.py` must print READY (new agent in
   `extraAgents`, `validUntil` in the future).
5. **Reload the running agent**: `pm2 restart plutus-gateway` then `/reset` in Telegram
   — the gateway caches the env key + system prompt at startup, so a live gateway keeps using
   the OLD (dead) key until restarted.

---

## Monitoring — why this can never silently recur

`plutus-ops` (every 30 min, cheap deepseek-v4-flash) fetches the **`hl_trade_readiness`**
data point every tick — the same verdict logic as the operator script (shared in
`trading/integrations/hyperliquid/readiness.py`). If the API-wallet registration is
missing or expires within 7 days, ops enqueues an escalation wake (NEVER an operator ping
for the wake itself, per escalation doctrine) so plutus-main re-registers or surfaces it.
A dead trade path is a **catastrophic** condition, not a quiet one. See
`agents/plutus-ops/AGENT.md`.

---

## Quick reference: what is NOT the problem

When "Plutus isn't trading," it is almost always **#3 (API-wallet registration)**. Before going
anywhere else, run the health check. Do **not** re-derive the wallet model from scratch, do
**not** theorize about spot/perp, do **not** touch dgclaw. The model is settled and lives
here.
