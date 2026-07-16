"""Conviction calibration — ML over the lifecycle prediction record.

Phase 1 (report-only): ``conviction_fit`` builds a leak-free feature matrix
from resolved predictions, evaluates a calibrated model against honest
baselines with purged walk-forward validation, and writes a versioned JSON
artifact. Nothing in the scoring path consumes the artifact yet — the desk
switches to calibrated conviction only after the report earns it.

Owned by plutus-reflect (the backward brain). All arithmetic lives here in
code; the agent invokes, reads the report, and narrates — the 2026-07-16
audit found what happens when an LLM hand-carries calibration numbers.
"""
