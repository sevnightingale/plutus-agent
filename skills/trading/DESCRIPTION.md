# Trading skills

The default skill set Plutus runs on top of the plutus-agent harness. Fifteen skills cover the trade lifecycle:

- **bootstrap-setup** — first-run setup workflow (ACP + dgclaw + HL API wallet, agent-driven).
- **heartbeat** — hourly cron tick that routes to the appropriate phase skill.
- **worldview-discipline** — keeps WORLDVIEW.md current after every meaningful state change.
- **consolidate-learnings** — LLM-extracts durable facts into holographic memory at session end + weekly.
- **watchlist-scan** — daily candidate identification when no positions / no active hypotheses.
- **deep-research** — builds conviction, articulates invalidation criteria, decides to open OR skip.
- **pre-mortem** — auto-fires before `place_order` at conviction > 0.7. Records counter-arguments.
- **position-monitor** — re-evaluates each open position; records `position_evaluation` events; checks invalidation criteria.
- **drawdown-discipline** — soft 20% circuit breaker; embedded in position-monitor.
- **tilt-detection** — meta-monitor for revenge trading patterns; embedded in position-monitor.
- **reconcile-and-reflect** — alert-fired on perceived position close; triggers reflection.
- **loss-postmortem** — auto-fires on outcomes with r_multiple < -0.5. Mandatory.
- **reflect** — opportunistic on wins, mandatory on losses (uses loss-postmortem).
- **weekly-review** — Sunday 18:00 UTC cron. Runs the lifecycle queries, writes a structured weekly reflection, may pause/retire strategies.
- **strategy-curator** — opens/pauses/retires strategies in the strategy book based on observed performance.

Conservative defaults baked in (drawdown 20%, pre-mortem at 0.7, loss-postmortem at r < -0.5, position-sizing 1% × conviction). Plutus refines these to its operator's risk posture as it learns.
