# dgclaw skill — vendored from upstream

This `SKILL.md` is vendored from
[Virtual-Protocol/dgclaw-skill](https://github.com/Virtual-Protocol/dgclaw-skill)
so Plutus loads it natively as a Hermes skill (rather than us
subprocess-wrapping the bash scripts).

## Why vendor instead of subprocess-wrap?

The dgclaw bash scripts (`dgclaw.sh`, `scripts/trade.ts`, etc.) are
designed to be invoked **by an AI agent reading this skill**, not
wrapped by another layer of Python tooling. Wrapping made setup
brittle (local-acp-cli keychain drift, hardcoded `$ACP_CLI_DIR`
paths, version mismatches). Loading the skill directly lets Plutus
follow Virtuals' own canonical procedure using its existing terminal
+ file tools + the `acp_*` wrappers we ship.

## Upstream version

Currently vendored from dgclaw-skill `v4.0` (per the `metadata.version`
in SKILL.md). To refresh:

```bash
cd ~/dgclaw-skill && git pull
cp ~/dgclaw-skill/SKILL.md ~/plutus-agent/skills/dgclaw/SKILL.md
git diff -- skills/dgclaw/SKILL.md
```

Review the diff for any breaking changes (different offering names,
new mandatory steps, etc.) before committing.

## What we still ship as Python tools

- **Data point reads** (`dgclaw_leaderboard`, `dgclaw_leaderboard_agent`,
  `dgclaw_forum_posts`, `dgclaw_forum_unreplied`) — quick discrete
  fetches, useful in the steady-state trading loop without needing
  Plutus to load the whole skill.
- **Forum operations** (`dgclaw_forum_reply`, `dgclaw_forum_create_post`)
  — same reason; reusable verbs.
- **Trade routing** (`dgclaw_trade_open`, `dgclaw_trade_close`,
  `dgclaw_trade_positions`, `dgclaw_trade_balance`) — alternative path
  to direct HL `place_order` if leaderboard counting requires
  dgclaw-routed trades. Default for v1 is direct HL via the venue
  registry; these stay as a fallback.
- **Alerts** (`dgclaw_leaderboard_rank_change`,
  `dgclaw_perp_deposit_completed`) — watcher daemon polls these.

## What we removed

The `dgclaw_install`, `dgclaw_join`, `dgclaw_perp_deposit_via_acp`,
`dgclaw_activate_unified`, `dgclaw_add_api_wallet`, and
`dgclaw_setup_status` tools have been deleted. The setup procedure
they hardcoded is documented in the vendored SKILL.md; Plutus follows
that on first run.

## Lessons from the first-run setup (2026-05-05)

Specifics learned the hard way during Plutus's initial bootstrap, so
future sessions don't relearn them:

### 1. The `perp_deposit` offering from Degen Claw is the right path

The vendored SKILL.md documents this correctly. The original
`dgclaw_perp_deposit_via_acp` wrapper was DOA — wrong call shape (no
`--provider`, no `--requirements`) — which led Plutus to discover an
unrelated `deposit_funds` offering from Arbital while debugging.
Arbital's offering had a separate on-chain revert (V2 payment flow
incompatibility) and was a red herring. The correct invocation, from
the SKILL.md:

```bash
acp client create-job --provider "0xd478a8B40372db16cA8045F28C6FE07228F3781A" \
  --offering-name "perp_deposit" --requirements '{"amount":"25"}' --legacy --json
acp client fund --job-id <jobId> --json
```

Note: `amount` MUST be a string (`"25"`) not a number — the offering's
JSON schema rejects numbers. The dgclaw provider is a v1 agent so
`--legacy` is required. Min deposit is 6 USDC per the SKILL.md; the
$30 minimum I claimed earlier was wrong (it was an Arbital-specific
constraint that doesn't apply to perp_deposit).

### 2. The local acp-cli checkout's auth doesn't work — workaround needed

The dgclaw scripts (`activate-unified.ts`, `add-api-wallet.ts`,
`trade.ts`) hardcode `$ACP_CLI_DIR/bin/acp.ts` and shell out to
`npx tsx`. The local `acp-cli` checkout's `cross-keychain` install can
not read OAuth tokens stored by the globally-installed `acp` binary
(version drift between v1.0.0 global and v1.0.5 local; different
package names → different keychain service entries).

**Durable fix** (recommended, document for operators):

```bash
# Point ACP_CLI_DIR at the globally-installed package, not a local clone
echo "ACP_CLI_DIR=$(npm root -g)/@virtuals-protocol/acp-cli" >> ~/.plutus-agent/.env
```

The global install has its own `bin/acp.ts` and shares the keychain
entry the global binary writes during `acp configure`. The dgclaw
scripts then run against an authenticated install.

**Fragile fix** (Plutus tried this during first bootstrap): write a
shim at `<local-checkout>/bin/acp.ts` that `spawnSync`'s the global
`acp` binary. Works once but gets clobbered on any `npm install` or
`git pull` of the local checkout.

### 3. Read access to the source repos

`HERMES_READ_SAFE_ROOT` should include `~/dgclaw-skill/` and
`~/acp-cli/` so Plutus can `read_file` the upstream source
when debugging — currently it has to `grep` via the `terminal` tool
which is slower and less ergonomic. One-line fix in
`~/.plutus-agent/.env`.
