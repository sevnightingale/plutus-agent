---
name: bootstrap-setup
description: First-run setup — install ACP CLI + dgclaw-skill, configure agent identity, fund wallet, register HL API wallet, persist env vars, ready Plutus for live trading. Delegates to vendored upstream skills (skills/acp-cli, skills/dgclaw) for the canonical procedures.
version: 2.0.0
metadata:
  hermes:
    tags: [trading, plutus, setup]
    related_skills: [worldview-discipline, acp-cli, dgclaw]
---

# Bootstrap setup

> **🔴 THE TRADE-EXECUTION MODEL — understand before running setup (canonical: `TRADING.md`).**
> Trading goes through the **native** `place_order(venue="hyperliquid")` path, signing with the
> **agent/API wallet** (`HL_API_WALLET_KEY`) on behalf of the **master** wallet
> (`HL_PUBLIC_ADDRESS`, ACP/Privy-managed, holds funds). **dgclaw is NOT the trade path.** The
> one thing that makes trading work is the agent wallet being **REGISTERED on Hyperliquid** via
> `add-api-wallet.ts`'s on-chain `approveAgent` (carries a ~180-day `validUntil`). If it lapses,
> EVERY trade fails silently ("User or API Wallet does not exist") — the known silent-outage
> failure mode. Funds live in **spot**; **unified mode** collateralizes perps — never move spot→perp.
> Verify with `scripts/check_trade_readiness.py` (must print READY); plutus-ops runs it every
> tick so a future lapse is caught.

Operator just said something like *"set yourself up for trading."* Walk through the phases below.

**Architectural rule**: don't reinvent setup procedures. Two upstream skills document the canonical commands:

- `skills/acp-cli/SKILL.md` — ACP install, OAuth, agent create, signer, wallet ops, full job lifecycle (event streaming, watch, drain, legacy vs non-legacy)
- `skills/dgclaw/SKILL.md` — dgclaw join, deposit, unified mode, API wallet, trade routing, leaderboard, forum

Load them when you need details. This skill is the **outer orchestration** — what order, when to wait for the operator, when to persist env vars, when to verify.

## Operator-touchpoints (don't try to automate)

Three steps where the operator must act and you wait:

1. **`acp configure`** — OAuth browser approval. Operator runs in their own terminal; subprocesses inside the gateway die on restart and the approval lands on a dead listener.
2. **`acp agent add-signer`** — same OAuth-style browser approval flow.
3. **Wallet funding** — operator sends USDC from Coinbase / wallet / external source. You can't move someone else's money for them.

For each, surface the exact command + URL the tool returns, then politely wait for the operator to confirm completion.

## Phase 1 — install prerequisites

1. `acp_install_check()` — if `installed: false`, call `acp_install()` (`npm install -g @virtuals-protocol/acp-cli`).
2. Clone dgclaw-skill if not present:
   ```
   terminal: git clone https://github.com/Virtual-Protocol/dgclaw-skill.git ~/dgclaw-skill && cd ~/dgclaw-skill && npm install
   ```
   Then add to ~/.plutus-agent/.env: `DGCLAW_SKILL_ROOT=~/dgclaw-skill`. Don't worry about the dgclaw-skill bundling its own acp-cli clone — we use the global one.

## Phase 2 — ACP identity (operator-driven OAuth steps)

3. **Call `acp_configure()`** — surface the returned `command` and `instructions` to the operator. Wait for them to confirm "configure done" (or until you see `acp agent whoami` succeed).
4. **`acp_agent_create(name="Plutus", description="AI Trading Agent built on plutus-agent")`** — non-interactive; persists Plutus's on-chain identity. Returns wallet address.
5. **Call `acp_agent_add_signer()`** — same surface-and-wait pattern as configure. After operator confirms, call `acp_persist_env_after_setup()` to write `HL_PUBLIC_ADDRESS` to `~/.plutus-agent/.env`.

## Phase 3 — fund the ACP wallet

6. `acp_wallet_topup()` — surfaces deposit address + chain warning. Tell the operator: *"Send ~$35 USDC on Base mainnet to this address (need to leave headroom above the $30 minimum dgclaw deposit + ACP gas). Wrong-chain transfers are unrecoverable. Reply when sent."*
7. Loop on `fetch_data_point("acp_wallet_balance")` (poll every minute or so) until USDC ≥ 32. If it doesn't show in 30 minutes, surface a status update — funding may have routing delays.

## Phase 4 — dgclaw setup (delegate to skills/dgclaw/SKILL.md)

8. **Read `skills/dgclaw/SKILL.md`** — that file is the canonical procedure for the next four sub-steps. Follow it directly using terminal + the `acp_*` wrappers we ship; don't ask me for tool names that match each step.

   The dgclaw skill walks you through:
   - **Step 1 — Register and Get API Key** (`dgclaw.sh join` or manual reproduction via `acp client create-job` + RSA encryption + `acp client fund`). Persists `DGCLAW_API_KEY` to dgclaw-skill's `.env`.
   - **Step 2 — Deposit USDC** (~30 min SLA; bridge route Base → Arbitrum → HL spot). The watcher daemon's `dgclaw_perp_deposit_completed` alert wakes a fresh Plutus session when funds land if your current session times out.
   - **Step 3 — Activate Unified Account** (`scripts/activate-unified.ts` — combines spot + perp).
   - **Step 4 — Set Up HL API Wallet** (`scripts/add-api-wallet.ts` — generates trading-only key; persists `HL_API_WALLET_KEY` and `HL_API_WALLET_ADDRESS` to dgclaw-skill's `.env`).

9. After Step 4 in dgclaw's skill completes, **copy the new env vars from dgclaw-skill's `.env` into `~/.plutus-agent/.env`** (so Plutus's HL execution sees them):
   ```
   terminal: grep -E "^(DGCLAW_API_KEY|HL_API_WALLET_KEY|HL_API_WALLET_ADDRESS)=" ~/dgclaw-skill/.env >> ~/.plutus-agent/.env
   ```

10. Surface to operator: *"API wallet is set up. Please run `pm2 restart plutus-gateway && /reset` so my next session sees the new env vars and can place real HL trades. Live trading unlocked after you do this."*

## Phase 5 — verify + record (after operator restart + /reset)

In your *next* session (the post-restart one), confirm everything works:

11. `acp_whoami()` — confirm Plutus identity, address matches HL_PUBLIC_ADDRESS.
11b. 🔴 **`terminal("cd ~/plutus-agent && .venv/bin/python scripts/check_trade_readiness.py")` — THE canonical readiness check. Must print `READY ✅` (agent wallet registered + unexpired). If NOT READY, trading will fail silently — follow `TRADING.md`'s recovery runbook (re-run `add-api-wallet.ts`, sync key into `~/.plutus-agent/.env`). DO NOT declare setup complete until READY.**
12. `fetch_data_point("hl_total_equity", {"account_name": "hl_trading"})` — confirm the deposited USDC is present. (NOTE: nonzero equity does NOT prove trading works — only the readiness check above does.)
13. Optionally: `dgclaw_setup_status()` no longer exists — instead just call `dgclaw_leaderboard()` to confirm Plutus is registered, and `fetch_data_point("hl_holdings")` to confirm the API wallet sees the unified account.
14. Place a tiny smoke order via THE trade path — native `place_order(venue="hyperliquid", thesis_id=<seed>, conviction=0.5, side="long", symbol="BTC", size=0.0001)` then `close_position(...)` immediately. Verify the lifecycle.db has the trade chain. Verify a trade-notify message appears in `notifications.trade_chat_id` Telegram chat (if configured). **This native path is how Plutus trades — dgclaw is a dormant leaderboard, NOT an execution path.**
15. Record a `setup_complete` reflection so future-you can find it via `find_similar_reflections`:
   ```
   record_event("reflection", {
     reflection_kind: "setup_complete",
     text_md: "<summary of what was set up; confirm native place_order is the trade path; note any quirks>"
   })
   ```
16. Update WORLDVIEW.md (use `worldview-discipline` skill mentally — set `operator_state.capital_at_risk_usd: <actual deposited amount>`, `operator_state.participate_in_dgclaw: true`, etc.).

## Done

Tell the operator: *"Setup complete. I have ~$X USDC trading capital on Hyperliquid, am registered on the dgclaw leaderboard, and am ready to trade. Use `/halt` (or `touch ~/.plutus-agent/HALT`) any time to pause execution; `rm ~/.plutus-agent/HALT` to resume."*

---

## Why this skill is just outer orchestration

Earlier versions of this skill hardcoded specific tool calls (`dgclaw_install`, `dgclaw_join`, `dgclaw_perp_deposit_via_acp`, `dgclaw_activate_unified`, `dgclaw_add_api_wallet`, etc.) — wrappers around brittle bash scripts that assumed a local acp-cli checkout, hardcoded provider addresses, mixed v1/v2 schema versions, and broke when reality drifted from the docs research used to design them.

The fix was structural: **load Virtuals' own SKILL.md files as canonical procedure documents**, use the existing `acp_*` Python wrappers for the steady-state actions that benefit from side effects (capital_movement recording, env persistence), and delegate everything else to the skill content. The bash scripts (`dgclaw.sh`, `scripts/trade.ts`, etc.) are designed to be invoked by an LLM agent reading the SKILL.md — not subprocess-wrapped by another layer.

If something in the upstream skills changes (offering name, schema version, provider address), the diff lands when we refresh the vendored SKILL.md (see `skills/dgclaw/UPSTREAM.md` and `skills/acp-cli/UPSTREAM.md`). No Python rewrite needed.
