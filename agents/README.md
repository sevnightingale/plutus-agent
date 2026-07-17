# agents/ — the trading desk roster

One directory per desk agent, each holding an `AGENT.md` **context recipe**
read directly by the spawn mechanism (`harness/spawn.py`). The full picture —
how the desk fits together, the blackboards, the strategy lifecycle — is in
`ARCHITECTURE.md`; this file covers the roster and the recipe format.

## The seven agents

| Agent | Role | Tier | Runs |
|---|---|---|---|
| **plutus-main** | Portfolio manager and operator voice — the persistent gateway session. Orchestrates everything; the only agent that spawns others | standard | always on (gateway) |
| **plutus-perception** | The eyes — fetches every market reading, rewrites `PERCEPTION.md` | light | spawned by main |
| **plutus-regime** | Classifies market regime per timescale, maintains `REGIME.md`, detects flips | light | spawned by main |
| **plutus-predict** | The forward brain — evaluates the live book, registers machine-resolvable predictions, reports population gaps | standard | spawned by main |
| **plutus-generate** | The research brain — the desk's only strategy author; surveys the evidence space, fills matrix gaps, declares missing data points | standard | spawned by main (7d floor + gap reports) |
| **plutus-ops** | Back office + watchdog — resolves due predictions, checks trade readiness, enforces staleness floors | light | cron, every 30 min |
| **plutus-reflect** | The backward brain — calibration review, weight tuning, strategy graduation/demotion, lessons | standard | spawned by main |

**Execution is not an agent.** The hands — stop, size, place, verify, abort —
are a deterministic tool (`desk_open_position` / `desk_close_position`) that main
calls directly; the former `plutus-trade` sub-agent was retired (execution is
arithmetic + structured venue ops, not judgment under ambiguity).

`plutus-main` is special: it is **not spawned**. The gateway session (CLI /
Telegram) *is* main — its directory holds doctrine context, and its tool
surface comes from the `plutus-agent-cli` composite toolset.

## Recipe format

Each `AGENT.md` is YAML frontmatter + a markdown briefing:

```yaml
---
name: plutus-ops
model: light                  # tier sentinel: standard | light (or a literal model name)
toolsets: [perception, resolution, lifecycle-read]
reads:                        # context blocks injected at spawn time
  - PLUTUS.md#doctrine        #   a zone of a runtime blackboard file
  - PERCEPTION.md             #   a whole blackboard
  - lifecycle:due-predictions #   a named lifecycle.db query
returns: ops_report           # the JSON output contract (defined in the body)
spawned_by: [cron]            # who may spawn it
---
```

- **`model` tiers** resolve against the *user's* configured models at spawn
  time: `standard` → `model.default`, `light` → `model.light` (falls back to
  default). An operator can pin any agent's model via `desk_models:` in
  `config.yaml`. A literal model name in the recipe is used as-is.
- **`reads`** declares everything the agent sees beyond its briefing — the
  spawn mechanism assembles these into the system context. Specialists never
  go hunting for state; it arrives pre-read.
- **`returns`** names the one-JSON-object contract the agent's final message
  must satisfy; main consumes these returns (a run nobody consumes is wasted).
- **Toolsets are minimal by design** — perception can't trade, trade can't
  resolve predictions, only ops resolves. The separation is the safety model.

## Editing recipes

Recipes are read from this directory at every spawn, so edits take effect on
the next spawn without a restart (code changes in `harness/` or `trading/`
still need a gateway restart). Keep briefings tight: every line of a recipe
is tokens every run, forever.
