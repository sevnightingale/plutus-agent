# LINEAGE.md — plutus-agent lineage & attribution

**plutus-agent** is an independent open-source trading agent. It **began as a fork** of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT licensed, Copyright 2025 Nous Research) and inherited that project's agent runtime as its starting point. As of the 2026-06-01 clean-cut rebrand it is maintained independently and does **not** track or cherry-pick from upstream.

## Inception (fork point)

- **Upstream**: NousResearch/hermes-agent
- **Upstream tag**: `v2026.4.23` · **version** `v0.11.0` · **SHA** `bf196a3fc0fd1f79353369e8732051db275c6276`
- **Fork date**: 2026-05-01 (as `hermes-trader`)

The upstream source was rsynced into a planning-docs repo and developed as `hermes-trader` through May 2026.

## Clean-cut rebrand (2026-06-01)

`hermes-trader` was renamed to **`plutus-agent`** and re-established as an independent project in a fresh repository:

- Internal modules renamed `hermes_cli` / `hermes_constants` / `hermes_state` / `hermes_time` / `hermes_logging` → `plutus_*`
- User data dir `~/.hermes-trader` → `~/.plutus-agent`; env vars `HERMES_*` → `PLUTUS_*` (legacy `HERMES_*` retained as a read fallback / migration bridge)
- CLI command → `plutus`; pm2 processes → `plutus-gateway` / `plutus-watchers`; ASCII **PLUTUS** boot banner
- Upstream-Hermes bloat (RL/training scaffolding, alternate web/TUI UIs, Hermes ACP IDE adapter, packaging for unrelated platforms) removed outright
- **Upstream tracking dropped.** There is no `upstream` cherry-pick relationship; plutus-agent owns its code.

## License

plutus-agent is **MIT** licensed. The original hermes-agent copyright (Copyright 2025 Nous Research) is preserved in `LICENSE`; plutus-agent additions are also MIT.

## Independence (required attribution)

> plutus-agent is an independent project that began as a fork of NousResearch's hermes-agent and is now maintained separately, reshaped around the trading domain. It is **not affiliated with or endorsed by NousResearch.**

## Optional integrations and their licenses

When the operator opts into the `acp` or `dgclaw` toolsets, plutus-agent subprocess-wraps two external tools from Virtual Protocol (neither is bundled in this repo):

- [`acp-cli`](https://github.com/Virtual-Protocol/acp-cli) — Agent Capability Protocol CLI. **ISC** licensed. On-chain agent identity, wallet, escrow jobs, agent discovery.
- [`dgclaw-skill`](https://github.com/Virtual-Protocol/dgclaw-skill) — Degenerate Claw competition. **MIT** licensed. Hyperliquid perpetuals leaderboard at `degen.virtuals.io`.
