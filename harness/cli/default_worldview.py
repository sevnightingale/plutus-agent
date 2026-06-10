"""Default WORLDVIEW.md template seeded into HERMES_HOME on first run.

WORLDVIEW.md is the cross-session bridge (PLUTUS Stratum 1): Plutus's
read-of-the-world it had refined in prior sessions. Read at session
start, written throughout, takes effect on the next session — this
honors the prompt-caching invariant (system prompt built once per
session, not mutated mid-conversation).

The seeded skeleton has the full YAML schema present with empty/zero
values so Plutus's writes always know what shape to keep. Body has
section headers but no narrative — Plutus fills as it builds its view.

v2 schema (2026-05-08+) adds: regime.confidence, regime.detected_at,
regime.dominant_signals, current_strategies (mirror), pending_predictions
(mirror), strategy_name + regime_tag on active_hypotheses.
"""

DEFAULT_WORLDVIEW_MD = """\
---
last_updated: null
last_updated_by: harness-seed
horizon: current
watchlist: []
risk_posture: moderate

regime:
  global: ""
  per_symbol: {}
  confidence: low
  detected_at: null
  dominant_signals: []

key_levels: {}

synthesis: ""               # 2-4 sentence opinionated trade context

narratives: []              # ordered list of {story, strength, direction, implication}

data_quality:
  high: []                  # direct API data
  medium: []                # web-extracted
  low: [synthesis, narratives]   # LLM judgment

delta_from_prior: ""        # what changed since last update

# CURRENT STRATEGIES MIRROR
# Source of truth: ~/.plutus-agent/strategies/<stage>/<name>.md
# strategy-curator keeps this in sync after promotions/demotions/retirements.
current_strategies:
  active: []
  trial: []
  observation: []
  retired: []

# PENDING PREDICTIONS MIRROR
# Source of truth: predictions table WHERE resolved_at IS NULL
# Updated when registering / resolving. prediction-tracker syncs.
pending_predictions:
  count: 0
  by_strategy: {}
  next_horizon: null

# Hypotheses currently driving open positions (theses joined to open positions)
active_hypotheses: []        # [{thesis_id, symbol, side, strategy_name, regime_tag, conviction, summary}]

open_positions_summary: []   # MIRROR — lifecycle.db source of truth

portfolio_summary:           # MIRROR — derived from data_point_snapshots + accounts
  total_equity_usd: 0.0
  starting_equity_usd: 0.0
  pct_growth_since_start: 0.0
  pct_growth_30d: 0.0
  max_drawdown_30d_pct: 0.0
  by_account: {}
  open_perp_unrealized_pnl_usd: 0.0
  last_snapshot_at: null

operator_state:
  last_directive: ""
  capital_at_risk_usd: 0.0
  participate_in_dgclaw: false

recent_learnings: []         # bounded ~20; older rotate to learnings_archive.md
                             # MARKET observations only — not dev/build notes
                             # Raw stream lives in observations table
---

# Synthesis (free-form notes)

(no synthesis yet)

# Active strategies (running playbooks)

(no strategies authored yet — use strategy-author skill to draft)

# Open hypotheses (deep)

(no open hypotheses yet)
"""
