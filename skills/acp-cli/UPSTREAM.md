# acp-cli skill — vendored from upstream

`SKILL.md` is vendored from
[Virtual-Protocol/acp-cli](https://github.com/Virtual-Protocol/acp-cli)
so Plutus loads it natively as a Hermes skill. It documents the full
ACP job lifecycle (event listening, drain loop, watch, legacy vs
non-legacy providers, offerings, subscriptions, resources) — far more
detail than my Python wrappers in `tools/integrations/acp/` could
capture.

## When Plutus loads this vs. uses our `acp_*` Python tools

**Load this skill** when:
- First-time setup (acp configure, agent create, add-signer)
- Hiring another agent for a non-trivial job (event-streaming workflow)
- Authoring offerings / subscriptions / resources of its own
- Debugging an ACP job that's stuck in `budget_set` or `submitted`

**Use the `acp_*` Python tools** when:
- Reading data points (`acp_wallet_balance`, `acp_browse_offerings`,
  `acp_chain_list`) — auto-snapshotted via `fetch_data_point`
- Sending ACP wallet payments (`acp_wallet_send`) — auto-records
  `capital_movement` event in lifecycle.db as a side effect
- Quick identity checks (`acp_whoami`, `acp_agent_list`)
- Routine job creation/funding (`acp_client_create_job`,
  `acp_client_fund`) — they wrap the SDK without state-management
  features, so they're fine for one-shot legacy-style flows

## Upstream version

Vendored from acp-cli `v1.0.5` (per `package.json` in the source
checkout). To refresh:

```bash
cd ~/acp-cli && git pull
cp ~/acp-cli/SKILL.md ~/plutus-agent/skills/acp-cli/SKILL.md
git diff -- skills/acp-cli/SKILL.md
```

## Important behaviors documented in upstream that we don't currently encode

1. **`--chain-id` is required** on most `acp` commands — the chain id
   comes from the job event. Our wrappers don't currently pass it
   (works for default-chain flows, breaks on multi-chain). Future
   polish: thread chain_id through the wrappers.
2. **Non-legacy providers require `acp events listen` to be running
   BEFORE creating a job.** Without it, the job stalls because the
   buyer can't react to the budget proposal. Our `events.py` ships a
   `start_event_stream()` helper but doesn't auto-invoke. If Plutus
   hires a non-legacy agent, it must start the listener first
   (per the skill).
3. **`acp job watch --job-id <id>`** is a clean per-job blocking
   alternative to events listen + drain. We don't wrap it — Plutus
   uses it directly via terminal when needed.
4. **Subscriptions** are a Virtuals primitive (7/15/30/90-day
   reusable access packages) we don't currently model. Plutus can
   subscribe via `acp client create-job --package-id` per the skill.
5. **`acp client create-custom-job`** is for freeform jobs without an
   offering (just a description + provider). Not wrapped; useful when
   hiring agents who haven't published an offering for what's needed.
6. **OAuth subprocess fragility**: `acp configure` and `acp agent
   add-signer` long-poll for browser approval. If the gateway
   restarts during the wait, the subprocess dies. **Operator must
   run these manually** in their own terminal; the
   `bootstrap-setup` skill calls this out explicitly.
