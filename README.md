# plutus-agent

**An open-source autonomous trading agent.**

> plutus-agent began as a fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT) and is now an independent project, reshaped around the trading domain. It is **not affiliated with or endorsed by NousResearch.** See `LINEAGE.md` for the fork point and attribution.

---

## What this is

**plutus-agent** is a specialized agent for autonomous trading, capital management, and portfolio management. The agent — **Plutus** by default — runs on Hyperliquid, makes its own decisions about what to trade and how, converses with the operator (CLI / TUI / Telegram), and self-modifies its own tools, skills, and memory over time.

It carries forward a complete agent runtime (agent loop, memory manager, multi-LLM provider abstraction, gateway, sessions, FTS5 search, web/browser, autonomous skill creation) and centers trading as the first-class concern. The upstream Hermes lineage is the project's *inception*, not an ongoing dependency — plutus-agent is maintained independently.

## Status

⚠️ **Early-stage software that trades real money.** plutus-agent is functional and runs a live agent today, but it is young and evolving fast. It places live trades on Hyperliquid under the agent's own decisions — start small, read `TRADING.md` before going live, and treat every deployment as production.

See `PLUTUS.md` for the cognitive architecture and **`TRADING.md` for how orders actually reach Hyperliquid (read this before touching anything trade-related).**

## Vision

- **Trading-first.** Capital management, portfolio management, and trading are the focus.
- **Conversational, not just autonomous.** You chat with the agent, share a chart, discuss a thesis, collaborate on a skill.
- **Self-evolving.** The agent has filesystem and shell tools. It writes its own skills, refines its tools, updates its memory.
- **Self-scheduling.** The agent decides when to act — schedules are a function of strategy, market state, and risk posture, not blind cron.
- **Capability-modular.** Toolsets compose: trading core + `hyperliquid` always-on; `acp` (Virtuals on-chain identity) and `dgclaw` (perpetuals competition) are opt-in. New venues plug in as integrations against stable registry interfaces.

## Quick start

> Works on Linux, macOS, WSL2. (Native Windows not supported — use WSL2.)

```bash
# Clone plutus-agent
git clone https://github.com/sevnightingale/plutus-agent.git
cd plutus-agent

# Create venv + install
uv venv .venv
source .venv/bin/activate
uv pip install -e '.[all]'

# (optional) symlink for system-wide access
ln -sf "$(pwd)/.venv/bin/plutus" ~/.local/bin/plutus

# Run setup wizard (configure model, API keys, agent identity)
plutus setup

# Confirm the trade path is live
python scripts/check_trade_readiness.py

# Start chatting
plutus
```

Setup prompts for the LLM provider + API key and the agent's name (default **Plutus**). User data lives at `~/.plutus-agent/` (config, sessions, memories, skills, logs, `.env`) — fully isolated from any other agent install on the machine.

### First-run trading setup (agent-driven)

After install, the agent walks itself through Hyperliquid wallet setup. The operator confirms browser approvals + funds the wallet. Chat with Plutus and say **"set yourself up for trading"** — it loads the `trading/bootstrap-setup` skill and runs through ACP install/configure → agent + signer creation → wallet top-up → dgclaw join → perp deposit → unified-mode activation → API-wallet generation. The final step registers the trading-only **agent wallet** on Hyperliquid (`approveAgent`) — **this registration is what makes trading work; if it lapses, trades fail silently** (see `TRADING.md`). When `python scripts/check_trade_readiness.py` prints **READY**, live trading is unlocked.

Pause execution any time with `touch ~/.plutus-agent/HALT`; resume with `rm ~/.plutus-agent/HALT`.

## Architecture at a glance

The tool surface is **function-shaped** — `perception`, `execution`, `reflection`, `identity` — fed by **six registries + dispatchers** (data points, events, venues, accounts, alerts, identity). Sources/venues (Hyperliquid, ACP, dgclaw) are *integrations* under `tools/integrations/<name>/` that contribute registry entries; capability scales via registry depth, not tool-count bloat.

Execution runs in tiers — **plutus-main** (heavy reasoning + orchestration, 3×/day) spawns **plutus-perception** (wide market sweep) and is deputized by **plutus-ops** (30-min bookkeeping). Conviction is two-dimensional (slow strategy baseline × ephemeral thesis) and drives multiplier-based sizing. See `PLUTUS.md` for the full picture.

| Layer | Path | Contents |
|---|---|---|
| Session DB | `~/.plutus-agent/state.db` | Sessions, history, skill caches, FTS5 |
| Lifecycle DB | `~/.plutus-agent/lifecycle.db` | Theses, decisions, trades, positions, predictions, observations, reflections + sqlite-vec + FTS5 |
| Holographic memory | `~/.plutus-agent/memories/` | Entity-keyed cumulative facts (optional plugin) |

Run under pm2 as `plutus-gateway` (cron scheduler + Telegram) and `plutus-watchers` (alert daemon).

## Optional integrations

- **Virtuals ACP** — on-chain agent identity, wallet operations, event streaming. Subprocess-wraps [`acp-cli`](https://github.com/Virtual-Protocol/acp-cli) (ISC). Enable: `toolsets: ["plutus-agent-cli", "acp"]`.
- **Virtuals dgclaw** — the Degenerate Claw perpetuals competition. Requires `acp` plus [`dgclaw-skill`](https://github.com/Virtual-Protocol/dgclaw-skill) (MIT). Enable: `toolsets: ["plutus-agent-cli", "acp", "dgclaw"]`.

Both are optional — the default install does Hyperliquid trading without them.

## Documentation

- `PLUTUS.md` — the agent's mind: cognitive architecture, lifecycle, perception/action/learning
- `TRADING.md` — **canonical** trade-execution mechanics (wallets, on-chain registration, the silent-failure mode, recovery)
- `LINEAGE.md` — upstream fork point + attribution
- `SECURITY.md` — how to report vulnerabilities
- `docs/legacy/` — pre-rebuild docs (developer guide, contributing) — stale during the rebuild

## License

MIT. See `LICENSE` (original copyright 2025 Nous Research for the hermes-agent code this began from; plutus-agent additions also MIT).

## Acknowledgments

This project would not exist without [Nous Research's Hermes Agent](https://github.com/NousResearch/hermes-agent). Optional integrations build on [Virtual Protocol's ACP CLI](https://github.com/Virtual-Protocol/acp-cli) and [`dgclaw-skill`](https://github.com/Virtual-Protocol/dgclaw-skill). Thank you to all of them.
