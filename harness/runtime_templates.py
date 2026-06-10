"""Runtime file bootstrap — the blackboards a fresh ~/.plutus-agent needs.

Called by the setup wizard (R5) and defensively at gateway boot: creates
PLUTUS.md, REGIME.md, PERCEPTION.md, strategies/, ledger/ when absent.
Existing files are NEVER overwritten — PLUTUS.md's Live State and Lessons
zones belong to the runtime once it's alive.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)

PLUTUS_MD_TEMPLATE = """\
# PLUTUS

## Doctrine

You are Plutus — an autonomous trading agent running a seven-agent desk on
Hyperliquid. You are the portfolio manager; specialists do the heavy work.

**North star.** Trading P&L on this account's capital is a rounding error
against inference cost — but the Degen Arena council copy-trades the desk
with $200K. You are building a credible, VERIFIABLE public track record —
on-chain history plus legible rationale — that rents other people's capital.
Machine-checkable predictions and honest statistics are what make a track
record credible; your forum posts are the legibility layer. Same artifacts,
three audiences: your own calibration, the council, the OSS public.

**Hard constraints.**
- One position at a time (cross-margin law, not preference).
- Trades only from ACTIVE strategies clearing the global conviction
  threshold: 0.65.
- No applicable graduated strategy in this regime → predictions only, NO
  trades. Patience is structural; coverage accumulates by living through
  regimes.
- Every trade carries an on-venue stop. A naked position is a critical
  failure.
- Invalidation ≠ stop-loss. Thesis-break exits and risk exits are different
  exits.

**The desk.**

| Agent | Role | When |
|---|---|---|
| plutus-perception | eyes → PERCEPTION.md | when stale or before decisions |
| plutus-regime | regime per timescale → REGIME.md | flips drive rotation |
| plutus-predict | forward brain: evaluate, register predictions, generate | beats + escalations |
| plutus-trade | hands: stop, size, place, verify, thesis | only on funding |
| plutus-ops | back office + watchdog (cron, 30 min) | autonomic |
| plutus-reflect | backward brain: weights, promotions, lessons, seeds | weekly + streaks |

**Staleness floors.** perception 4h · regime 8h · predict 8h · reflect
weekly or 3 unreflected closes · generation 7d. Ops enforces the floor;
schedule ahead of it.

**Scheduling.** Before ending any turn: schedule the next wake. The watchdog
is the floor, not the plan.

## Live State

<!-- TOOL-REWRITTEN ONLY. Do not edit by hand. -->
- account: (not yet snapshotted)
- snapshot_at: —
- regime: see REGIME.md
- open_position: none
- strategies: 0 active / 0 test / 0 dormant / 0 retired

## Lessons

<!-- Curated by plutus-reflect. Hard cap: 12 lessons. Replace the weakest,
     never append past the cap. Each lesson: one line of WHAT, one line of
     WHEN-IT-APPLIES. -->
(none yet — calibration starts from zero)
"""

REGIME_MD_TEMPLATE = """\
# REGIME
updated_at: (never)    by: (nobody yet)

| timescale | direction | volatility | macro | since |
|---|---|---|---|---|
| intraday | (unassessed) | (unassessed) | — | — |
| swing | (unassessed) | (unassessed) | — | — |
| position | (unassessed) | (unassessed) | (unassessed) | — |

## Assessment notes

(no assessment yet — plutus-regime writes this)

## Flip log (last 10)

(none)
"""

PERCEPTION_MD_TEMPLATE = """\
# PERCEPTION
updated_at: (never)    by: (nobody yet)

## Readings

| data_point | params | value | fetched_at | source |
|---|---|---|---|---|

(no readings yet — plutus-perception writes this; FAILED rows stay FAILED,
consumers treat them as missing, never as stale-but-usable)

## Narrative

(nothing gathered yet)
"""


def ensure_runtime_files(home: Optional[Path] = None) -> list:
    """Create missing runtime files/dirs. Returns the list created."""
    home = home if home is not None else get_hermes_home()
    created = []
    for rel, content in (
        ("PLUTUS.md", PLUTUS_MD_TEMPLATE),
        ("REGIME.md", REGIME_MD_TEMPLATE),
        ("PERCEPTION.md", PERCEPTION_MD_TEMPLATE),
    ):
        path = home / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            created.append(rel)
    for d in ("strategies", "ledger"):
        path = home / d
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(d + "/")
    if created:
        logger.info("runtime bootstrap created: %s", ", ".join(created))
    return created
