# Security Policy

plutus-agent is an autonomous trading agent with shell, filesystem, browser, and
wallet-signing capabilities. Treat every deployment as production: it can spend
money and execute commands.

## Reporting a vulnerability

plutus-agent does **not** operate a bug bounty program. Please report security
issues via [GitHub Security Advisories](https://github.com/sevnightingale/plutus-agent/security/advisories/new).
**Do not open public issues for security vulnerabilities.**

Include where possible:

- **Environment:** output of `plutus --version`, commit SHA, OS, Python version.
- **Reproduction:** minimal steps or a proof-of-concept.
- **Impact:** what an attacker gains (key disclosure, fund movement, RCE, prompt
  injection → tool execution, etc.).

If the issue clearly originates in inherited upstream code, consider also
reporting it to the upstream project this began as a fork of
([NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/security/advisories/new)),
so both lineages get fixed.

## Scope notes

- **Operator-owned state:** reports that require pre-existing write access to
  `~/.plutus-agent/` (`.env`, `config.yaml`, `SOUL.md`, the SQLite databases)
  are generally out of scope — those files are owner-trusted by design.
- **Prompt injection:** the agent reads web content, market data, and inbound
  chat messages. Reports demonstrating untrusted content escalating into
  dangerous tool execution **are in scope** and welcome.
- **Trading logic:** losing trades are not vulnerabilities. Unauthorized
  trades, signature misuse, or key exposure are.

## Hardening checklist for operators

- API keys and tokens belong **exclusively** in `~/.plutus-agent/.env` — never
  in `config.yaml`, never committed. `chmod 600 ~/.plutus-agent/.env`.
- **Wallet model:** the Hyperliquid *agent wallet* key on disk can only sign
  trades — it holds no funds. The *master wallet* key should never exist on the
  machine at all. Never paste master-wallet private keys into the agent, its
  config, or its chat.
- Run as a non-root user (the Docker image uses UID 10000).
- Review Skills Guard output before installing third-party skills
  (`tools/skills_guard.py`; install audit log at
  `~/.plutus-agent/skills/.hub/audit.log`).
- The kill switch: `touch ~/.plutus-agent/HALT` pauses trade execution
  immediately; `rm ~/.plutus-agent/HALT` resumes.
- Scope the agent's filesystem reach with `HERMES_WRITE_SAFE_ROOT` /
  `HERMES_READ_SAFE_ROOT` (colon-separated roots).
