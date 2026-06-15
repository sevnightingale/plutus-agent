"""lifecycle.db v4 — the prediction-first event log (PLUTUS rebuild, R1).

The lifecycle of PREDICTIONS, some of which become trades. The chain:
prediction → (funding) → thesis (cites prediction_id) → decision → trade →
position → outcome. Resolution and calibration happen uniformly at the
prediction level; support_scores records the per-(prediction, data point)
conviction inputs including narrative LLM reasoning.

Fresh-create starts calibration from zero. Migrations chain in place: v2 → v3
added the price-zone columns and clean-slated the old forecast-accuracy counters
(graduation now measures the price-zone metric); v3 → v4 adds the
reached_near_at / reached_far_at resolution markers (floor-correct, target-
accelerated, horizon-backstopped). A pre-v2 (v1) file is still refused, never
migrated; the old runtime's file stays preserved as reference.

Write ownership (doctrine): plutus-main writes events from subagents'
structured returns; plutus-predict writes predictions; plutus-ops resolves
them. Blackboard files are written by their producers. Every row carries
``agent`` + ``session_name`` provenance.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from harness.constants import get_hermes_home

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4

# ───────────────────────────────────────────────────────────────────────────
# Schema
# ───────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- Perception audit trail
CREATE TABLE IF NOT EXISTS data_point_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    name TEXT NOT NULL,
    params_json TEXT,
    value_json TEXT,
    source TEXT
);

-- Derived mirror of strategy .md files (file is truth; synced atomically by
-- the same tool that edits the file; never written independently)
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,            -- slug == file stem under strategies/
    file_path TEXT NOT NULL,
    status TEXT NOT NULL,                 -- 'test' | 'active' | 'dormant' | 'retired'
    timescale TEXT NOT NULL,              -- 'intraday' | 'swing' | 'position'
    mechanism_family TEXT NOT NULL,       -- 'momentum'|'mean_reversion'|'flow'|'event'|'narrative'
    parent_strategy TEXT,                 -- champion/challenger lineage
    hypothesis_md TEXT,
    mechanism_md TEXT,                    -- WHY the edge exists
    regime_applicability_json TEXT,       -- regime labels AT THIS STRATEGY'S TIMESCALE
    data_points_json TEXT,                -- declared DPs + weights (frontmatter mirror)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    retired_at REAL,
    retirement_reason TEXT,
    n_resolved INTEGER NOT NULL DEFAULT 0,
    n_correct INTEGER NOT NULL DEFAULT 0,
    n_wrong INTEGER NOT NULL DEFAULT 0,
    n_ambiguous INTEGER NOT NULL DEFAULT 0,
    last_resolved_at REAL,
    embedding BLOB,
    embedding_model TEXT
);

-- THE SPINE
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    horizon_ts REAL NOT NULL,
    timescale TEXT NOT NULL,              -- derived bucket, stored for quotas/slicing
    symbol TEXT,
    claim_md TEXT NOT NULL,
    entry_ref_price REAL,                 -- spot captured at registration; the % zone is relative to this
    near_edge_pct REAL,                   -- correctness floor: signed % move (bullish +, bearish -)
    far_edge_pct REAL,                    -- optimistic target: signed % move, |far| > |near|
    reached_near_at REAL,                 -- favorable excursion first touched near → win LOCKED (stays open)
    reached_far_at REAL,                  -- touched far → early target (resolves correct, mode 'target')
    success_criteria_json TEXT NOT NULL,  -- serialized price zone (the explicit *_pct cols are truth)
    failure_criteria_json TEXT,
    invalidation_criteria_json TEXT,      -- optional machine-resolvable thesis-break (resolvable DP leaves)
    risk_tolerance TEXT,                  -- 'low' | 'med' | 'high'
    conviction REAL NOT NULL,             -- normalized 0-1 support-score aggregate
    strategy_name TEXT,                   -- NOT NULL unless kind in ('stress','adhoc')
    kind TEXT NOT NULL DEFAULT 'strategy',-- 'strategy' | 'stress' | 'adhoc'
    regime_tag TEXT,                      -- regime at THIS prediction's timescale
    snapshot_ids_json TEXT,
    resolved_at REAL,
    outcome TEXT,                         -- 'correct'|'wrong'|'ambiguous'|'expired_unresolvable' (invalidation → 'wrong' + realized_value_json.resolution_mode)
    resolved_by TEXT,
    resolution_notes_md TEXT,
    resolution_snapshot_ids_json TEXT,
    realized_value_json TEXT,
    embedding BLOB,
    embedding_model TEXT
);

-- Per-(prediction, data point) support scores — conviction audit trail
CREATE TABLE IF NOT EXISTS support_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    data_point TEXT NOT NULL,
    score REAL NOT NULL,                  -- 0.0 invalidates … 1.0 supports
    kind TEXT NOT NULL,                   -- 'numerical' | 'narrative'
    reading_json TEXT,
    weight REAL,
    normalizer TEXT,                      -- deterministic normalizer id (numerical)
    reasoning_md TEXT,                    -- REQUIRED for narrative kind
    ts REAL NOT NULL,
    UNIQUE (prediction_id, data_point)
);

-- Conviction trajectory: ops re-scores each OPEN prediction on a timescale-
-- aware cadence via the cheap conviction_score tool. One row per re-score —
-- the curve reflect mines for entry-timing, invalidation-by-decay, and
-- calibration-v2 (does trajectory shape predict outcome better than level?).
CREATE TABLE IF NOT EXISTS prediction_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    conviction REAL NOT NULL,
    support_scores_json TEXT,             -- the per-DP scores behind this re-score
    regime_tag TEXT                       -- regime at re-score time (may differ from birth)
);

-- A thesis is a FUNDED PREDICTION
CREATE TABLE IF NOT EXISTS theses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL REFERENCES predictions(id),
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    text_md TEXT NOT NULL,
    strategy_name TEXT,
    sl_price REAL,
    sl_rationale_md TEXT,
    structured_tags_json TEXT,
    snapshot_ids_json TEXT,
    embedding BLOB,
    embedding_model TEXT
);

-- Execution chain (carried from v1 + provenance)
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL REFERENCES theses(id),
    agent TEXT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    params_json TEXT,
    conviction REAL NOT NULL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES decisions(id),
    ts REAL NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    fill_price REAL NOT NULL,
    slippage_bp REAL,
    venue_order_id TEXT,
    venue_fill_id TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    opening_trade_id INTEGER NOT NULL REFERENCES trades(id),
    closing_trade_id INTEGER REFERENCES trades(id),
    status TEXT NOT NULL DEFAULT 'open',
    opened_at REAL NOT NULL,
    closed_at REAL,
    perceived_at REAL,
    entry_account_value REAL,             -- unified equity_usd measured at open
    leverage REAL                         -- notional_at_fill / entry_account_value
);

CREATE TABLE IF NOT EXISTS position_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    position_id INTEGER NOT NULL REFERENCES positions(id),
    conviction REAL NOT NULL,
    thesis_status TEXT,                   -- 'intact'|'strengthened'|'weakening'|'invalidated'
    active_thesis_id INTEGER REFERENCES theses(id),
    rationale_md TEXT,
    snapshot_ids_json TEXT,
    recommended_action TEXT,
    action_taken_decision_id INTEGER REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS outcomes (
    position_id INTEGER PRIMARY KEY REFERENCES positions(id),
    realized_pnl_usd REAL,
    realized_pnl_pct REAL,
    r_multiple REAL,
    holding_minutes REAL,
    mae_pct REAL,
    mfe_pct REAL,
    entry_efficiency REAL,
    exit_efficiency REAL,
    slippage_total_bp REAL,
    exit_reason TEXT,
    conviction_at_entry REAL,
    conviction_at_exit REAL,
    conviction_min_during_hold REAL,
    conviction_max_during_hold REAL,
    conviction_volatility REAL,
    conviction_evaluations_count INTEGER,
    invalidation_triggered_at REAL,
    invalidation_to_exit_minutes REAL
);

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    text_md TEXT NOT NULL,
    position_ids_json TEXT,
    related_thesis_ids_json TEXT,
    related_prediction_ids_json TEXT,
    reflection_kind TEXT,
    error_class TEXT,                     -- losses: 'forecast'|'execution'|'sizing'|'regime'|'variance'|'process_violation'
    strategy_name TEXT,
    embedding BLOB,
    embedding_model TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    agent TEXT,
    ts REAL NOT NULL,
    symbol TEXT,
    kind TEXT,
    text_md TEXT NOT NULL,
    strategy_name TEXT,
    related_thesis_ids_json TEXT,
    related_prediction_ids_json TEXT,
    snapshot_ids_json TEXT,
    structured_tags_json TEXT
);

CREATE TABLE IF NOT EXISTS capital_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    ts REAL NOT NULL,
    from_account TEXT,
    to_account TEXT,
    token TEXT NOT NULL,
    amount_token REAL NOT NULL,
    amount_usd_at_time REAL,
    movement_type TEXT NOT NULL,
    tx_hash TEXT,
    note TEXT
);

-- Staleness watchdog source: every run of every action type
CREATE TABLE IF NOT EXISTS action_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    ts REAL NOT NULL,
    agent TEXT,
    session_name TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    notes_md TEXT
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_dps_name_ts ON data_point_snapshots(name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);
CREATE INDEX IF NOT EXISTS idx_strategies_parent ON strategies(parent_strategy);
CREATE INDEX IF NOT EXISTS idx_predictions_due
    ON predictions(horizon_ts) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_predictions_strategy ON predictions(strategy_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_timescale ON predictions(timescale, ts DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_outcome ON predictions(outcome);
CREATE INDEX IF NOT EXISTS idx_predictions_regime ON predictions(regime_tag);
CREATE INDEX IF NOT EXISTS idx_support_prediction ON support_scores(prediction_id);
CREATE INDEX IF NOT EXISTS idx_support_dp ON support_scores(data_point);
CREATE INDEX IF NOT EXISTS idx_theses_prediction ON theses(prediction_id);
CREATE INDEX IF NOT EXISTS idx_theses_symbol_ts ON theses(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_thesis ON decisions(thesis_id);
CREATE INDEX IF NOT EXISTS idx_trades_decision ON trades(decision_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_evals_position_ts ON position_evaluations(position_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pred_evals_pred_ts ON prediction_evaluations(prediction_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_kind_ts ON reflections(reflection_kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_observations_kind_ts ON observations(kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_action_runs_type_ts ON action_runs(action_type, ts DESC);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS predictions_fts USING fts5(
    claim_md, content='predictions', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS predictions_ai AFTER INSERT ON predictions BEGIN
    INSERT INTO predictions_fts(rowid, claim_md) VALUES (new.id, new.claim_md);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    text_md, content='observations', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS reflections_fts USING fts5(
    text_md, content='reflections', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS reflections_ai AFTER INSERT ON reflections BEGIN
    INSERT INTO reflections_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS strategies_fts USING fts5(
    hypothesis_md, mechanism_md, content='strategies', content_rowid='id'
);
"""


def default_db_path() -> Path:
    """Resolved at CALL time, never at import (the 45a6cc9 lesson)."""
    return get_hermes_home() / "lifecycle.db"


def get_db(path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (creating if needed) the lifecycle database.

    Refuses a pre-v2 file: the rebuild does not migrate — fresh runtime,
    calibration from zero. The error message carries the runbook pointer.
    """
    db_path = Path(path) if path is not None else default_db_path()
    exists = db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if exists:
        row = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone() if _has_table(conn, "schema_version") else None
        version = row["version"] if row else None
        if version == SCHEMA_VERSION:
            return conn
        # Incremental migrations chain forward: v2 → v3 → v4.
        if version == 2:
            _migrate_v2_to_v3(conn)
            version = 3
        if version == 3:
            _migrate_v3_to_v4(conn)
            version = 4
        if version == SCHEMA_VERSION:
            logger.info("Migrated lifecycle.db → v%s at %s", SCHEMA_VERSION, db_path)
            return conn
        conn.close()
        raise RuntimeError(
            f"{db_path} has schema version {version!r}, expected {SCHEMA_VERSION}. "
            "Migrations chain v2 → v3 → v4; a pre-v2 (v1) file is fresh-create only. "
            "Back up and remove the old file (see SETUP.md, 'Redeploying "
            "a fresh runtime'), then rerun."
        )

    _create_fresh(conn)
    logger.info("Created fresh lifecycle.db v%s at %s", SCHEMA_VERSION, db_path)
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _create_fresh(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executescript(INDEXES_SQL)
    try:
        conn.executescript(FTS_SQL)
    except sqlite3.OperationalError as exc:
        # FTS5 missing from the sqlite build: degraded search, stated loudly.
        logger.warning("FTS5 unavailable (%s) — full-text search disabled.", exc)
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """In-place v2 → v3: price-zone predictions.

    Adds the explicit zone columns + the conviction-trajectory table, then
    CLEAN-SLATES — expires every open prediction (except any backing the open
    position) and zeroes the strategy mirror counters, because the old
    forecast-accuracy track record does not transfer to the price-zone metric.

    Idempotent (re-running on a partial file is a no-op) and serialized with
    ``BEGIN IMMEDIATE`` so a watcher/ops race on first open can't double-apply.
    """
    import time

    conn.execute("BEGIN IMMEDIATE")
    try:
        for col in ("entry_ref_price", "near_edge_pct", "far_edge_pct"):
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL")
            except sqlite3.OperationalError:
                pass  # column already present — idempotent

        conn.execute(
            """CREATE TABLE IF NOT EXISTS prediction_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL REFERENCES predictions(id),
                session_name TEXT,
                agent TEXT,
                ts REAL NOT NULL,
                conviction REAL NOT NULL,
                support_scores_json TEXT,
                regime_tag TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pred_evals_pred_ts "
            "ON prediction_evaluations(prediction_id, ts DESC)"
        )

        # Clean-slate: expire open predictions (keep any backing the open
        # position) WITHOUT bumping counters — these forecasts are discarded.
        conn.execute(
            """UPDATE predictions
                  SET resolved_at = ?, outcome = 'expired_unresolvable',
                      resolved_by = 'migration',
                      resolution_notes_md = 'clean-slate: price-zone (v3) migration'
                WHERE resolved_at IS NULL
                  AND id NOT IN (
                      SELECT t.prediction_id FROM theses t
                      JOIN decisions d ON d.thesis_id = t.id
                      JOIN trades tr ON tr.decision_id = d.id
                      JOIN positions p ON p.opening_trade_id = tr.id
                      WHERE p.status = 'open'
                  )""",
            (time.time(),),
        )

        # Reset strategy mirror counters — graduation re-measures on the new
        # metric. Strategy .md files are untouched; only the DB mirror resets.
        conn.execute(
            "UPDATE strategies SET n_resolved = 0, n_correct = 0, "
            "n_wrong = 0, n_ambiguous = 0, last_resolved_at = NULL"
        )

        conn.execute("UPDATE schema_version SET version = 3")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """In-place v3 → v4: add the resolution-progress markers.

    ``reached_near_at`` / ``reached_far_at`` back the floor-correct, target-
    accelerated, horizon-backstopped model: near touch LOCKS the win but keeps
    the prediction open; far touch resolves it correct early; otherwise the
    horizon backstops a correct resolution. No data backfill — existing resolved
    rows keep NULL markers (only forward predictions carry the trajectory).

    Idempotent (re-running on a partial file is a no-op) and serialized with
    ``BEGIN IMMEDIATE`` so a watcher/ops race on first open can't double-apply.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        for col in ("reached_near_at", "reached_far_at"):
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL")
            except sqlite3.OperationalError:
                pass  # column already present — idempotent
        conn.execute("UPDATE schema_version SET version = 4")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# Timescale derivation (locked taxonomy)
_DAY = 86400.0
TIMESCALE_MAX_S = {"intraday": _DAY, "swing": 7 * _DAY, "position": 30 * _DAY}


def derive_timescale(ts: float, horizon_ts: float) -> str:
    """Bucket a horizon into the locked taxonomy. Raises on the >30d cap."""
    span = horizon_ts - ts
    if span <= 0:
        raise ValueError("horizon_ts must be after ts")
    if span <= TIMESCALE_MAX_S["intraday"]:
        return "intraday"
    if span <= TIMESCALE_MAX_S["swing"]:
        return "swing"
    if span <= TIMESCALE_MAX_S["position"]:
        return "position"
    raise ValueError(
        f"horizon {span / _DAY:.1f}d exceeds the 30d cap — beyond that a "
        "prediction can't feed calibration (rebuild-architecture.md §18)."
    )
