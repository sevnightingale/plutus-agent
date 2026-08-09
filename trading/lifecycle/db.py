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
accelerated, horizon-backstopped); v4 → v5 canonicalizes
support_scores.data_point to the declared ``name(params)`` key form (free-form
agent strings had fragmented the per-DP calibration record). A pre-v2 (v1)
file is still refused, never migrated; the old runtime's file stays preserved
as reference.

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

SCHEMA_VERSION = 7

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
    symbol TEXT NOT NULL DEFAULT 'BTC',   -- one symbol per strategy (2026-08-08)
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

-- What the tape was doing, per timescale. Append-only: the current regime is
-- the latest row per (symbol, timescale), and the history it accumulates makes
-- cell OCCUPANCY a query instead of an inference from predictions.regime_tag
-- (which only ever sees the cells the desk happened to sample).
--
-- Regime lived solely as markdown in REGIME.md until 2026-07-27 — no code
-- anywhere could read it, so predict matched strategies to the tape in its
-- head and every cell-aware surface stopped at the prompt boundary. Third
-- record this month kept as freeform text with no writer, after reflections
-- and capital_movements.
--
-- `symbol` is defaulted and, for now, always 'BTC'. It is here so that
-- per-symbol regime needs no migration; nothing yet computes a second one.
-- `source` distinguishes an observation plutus-regime made from one derived
-- by backfill out of predictions.regime_tag, which is the regime AT
-- REGISTRATION for sampled cells only — real evidence, but not the same thing.
CREATE TABLE IF NOT EXISTS regime_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL DEFAULT 'BTC',
    timescale TEXT NOT NULL,
    direction TEXT NOT NULL,
    volatility TEXT NOT NULL,
    macro TEXT,
    conviction REAL,
    flipped INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'observed',
    session_name TEXT,
    notes_md TEXT
);
"""

INDEXES_SQL = """
-- The venue's tx hash is the natural key for a capital movement: it makes
-- reconciliation against the exchange ledger idempotent (INSERT OR IGNORE),
-- so the reconciler can run on every ops tick without duplicating history.
-- SQLite permits repeated NULLs in a UNIQUE index, so hand-recorded movements
-- with no hash are still allowed.
CREATE UNIQUE INDEX IF NOT EXISTS ux_capital_movements_tx
    ON capital_movements(tx_hash);
CREATE INDEX IF NOT EXISTS idx_capital_movements_ts ON capital_movements(ts);
CREATE INDEX IF NOT EXISTS idx_regime_obs
    ON regime_observations(symbol, timescale, ts DESC);
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

    # timeout: how long a lock wait may ride the busy handler before
    # OperationalError. The 5s default is too tight for concurrent first
    # opens — the WAL switch needs exclusive access and a migration holds
    # BEGIN IMMEDIATE for its whole run; on a loaded CI box that collision
    # exceeded 5s (test_migration_idempotent_under_concurrent_open).
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    if exists:
        row = conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone() if _has_table(conn, "schema_version") else None
        version = row["version"] if row else None
        if version == SCHEMA_VERSION:
            _ensure_indexes(conn)
            return conn
        # Incremental migrations chain forward: v2 → v3 → v4 → v5 → v6.
        if version == 2:
            _migrate_v2_to_v3(conn)
            version = 3
        if version == 3:
            _migrate_v3_to_v4(conn)
            version = 4
        if version == 4:
            _migrate_v4_to_v5(conn)
            version = 5
        if version == 5:
            _migrate_v5_to_v6(conn)
            version = 6
        if version == 6:
            _migrate_v6_to_v7(conn)
            version = 7
        if version == SCHEMA_VERSION:
            _ensure_indexes(conn)
            logger.info("Migrated lifecycle.db → v%s at %s", SCHEMA_VERSION, db_path)
            return conn
        conn.close()
        raise RuntimeError(
            f"{db_path} has schema version {version!r}, expected {SCHEMA_VERSION}. "
            "Migrations chain v2 → v3 → v4 → v5 → v6; a pre-v2 (v1) file is fresh-create only. "
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


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    """Apply INDEXES_SQL to an already-existing database.

    Indexes used to be created in ``_create_fresh`` and nowhere else, so an
    index added after a runtime's database already existed never arrived: the
    schema version was already current, the open path returned early, and no
    other code ran the block. The bug is silent by construction — the database
    works, just without the constraint.

    It cost real correctness on 2026-07-26. ``ux_capital_movements_tx`` was
    added that morning, and ``record_capital_movement`` rests its whole
    idempotency on it (``INSERT OR IGNORE``, deliberately not a
    read-then-write, so that concurrent reconcilers cannot both insert). The
    index was still absent from the live database hours later, so the capital
    reconciler re-inserted the same two deposits on every 30-minute ops tick:
    twelve rows for two movements, and a reported $497.86 of lifetime deposits
    against $82.98 of real ones — an 85% loss where the truth was 9%.

    Every statement is ``CREATE ... IF NOT EXISTS``, so running this on every
    open is cheap and idempotent. A UNIQUE index can still fail on a database
    that already holds duplicates; that is logged loudly and the open
    continues, because refusing to start the desk over a missing index is the
    worse of the two failures.
    """
    for chunk in INDEXES_SQL.split(";"):
        if "CREATE" not in chunk.upper():
            continue  # trailing whitespace or a standalone comment block
        try:
            conn.execute(chunk)
        except sqlite3.DatabaseError as exc:
            # DatabaseError, not OperationalError: duplicates in an existing
            # table raise IntegrityError, which is exactly the case this
            # tolerance exists for.
            name = next((w for w in chunk.split() if w.startswith(("idx_", "ux_"))),
                        "<unnamed>")
            logger.warning("index %s not applied: %s", name, exc)
    conn.commit()


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


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """In-place v4 → v5: canonicalize ``support_scores.data_point``.

    The column historically stored whatever string the registering agent
    passed — bare names (``ta_vortex``), full keys, or ad-hoc shorthand
    (``ta_vortex(4h)``, ``ta_ema_1d``) — fragmenting the per-DP calibration
    aggregates (support_score_performance) and colliding same-name
    declarations. Rewrites every resolvable row to the declared canonical
    ``name(params)`` key using the strategies mirror's ``data_points_json``.
    Unresolvable rows stay as-is (counted loudly, never guessed); a rewrite
    that would collide with an existing (prediction_id, data_point) row is
    skipped the same way. No new columns — v5 is a data repair.

    Idempotent (canonical rows resolve to themselves) and serialized with
    ``BEGIN IMMEDIATE`` like every migration in the chain.
    """
    import json

    from trading.strategies.files import resolve_dp_key

    conn.execute("BEGIN IMMEDIATE")
    try:
        strat_dps: dict = {}
        if _has_table(conn, "strategies"):
            for r in conn.execute("SELECT name, data_points_json FROM strategies"):
                try:
                    dps = json.loads(r["data_points_json"] or "[]")
                except (TypeError, ValueError):
                    dps = []
                strat_dps[r["name"]] = dps if isinstance(dps, list) else []

        changed = unresolved = collided = 0
        if _has_table(conn, "support_scores"):
            rows = conn.execute(
                """SELECT s.id, s.data_point, p.strategy_name
                     FROM support_scores s
                     JOIN predictions p ON p.id = s.prediction_id
                    WHERE p.strategy_name IS NOT NULL""").fetchall()
            for r in rows:
                canonical = resolve_dp_key(
                    strat_dps.get(r["strategy_name"]) or [], r["data_point"])
                if canonical is None:
                    unresolved += 1
                elif canonical != r["data_point"]:
                    cur = conn.execute(
                        "UPDATE OR IGNORE support_scores SET data_point = ? "
                        "WHERE id = ?", (canonical, r["id"]))
                    if cur.rowcount:
                        changed += 1
                    else:
                        collided += 1

        conn.execute("UPDATE schema_version SET version = 5")
        conn.commit()
        logger.info(
            "v5 support_scores canonicalization: %s rewritten, %s unresolved "
            "(left as-is), %s uniqueness collisions (left as-is)",
            changed, unresolved, collided)
    except Exception:
        conn.rollback()
        raise


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """In-place v5 → v6: regime gets a table.

    Regime lived only in REGIME.md, so no code could read it — predict matched
    strategies against the tape in its head and every cell-aware surface built
    on 2026-07-27 stopped at the prompt boundary. Additive: one new table, no
    existing column touched.

    The rows are BACKFILLED from ``predictions.regime_tag``, which is 100%
    populated and records the regime at registration. Marked
    ``source='derived'`` because that is what it is — the regime for the cells
    the desk sampled, not a reading of what the tape did — but starting empty
    would leave occupancy unmeasurable for a month, and an honest approximation
    beats a blind one. One row per (day, timescale, tag), which is the finest
    grain the tags support.
    """
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS regime_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                symbol TEXT NOT NULL DEFAULT 'BTC',
                timescale TEXT NOT NULL,
                direction TEXT NOT NULL,
                volatility TEXT NOT NULL,
                macro TEXT,
                conviction REAL,
                flipped INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'observed',
                session_name TEXT,
                notes_md TEXT
            );""")
        seeded = 0
        # `predictions.regime_tag` arrives with the fresh-create schema and no
        # migration ever added it, so a database old enough to be walking the
        # v2 chain does not have it. No history to derive is honest absence,
        # not an error — seed nothing and say so.
        has_tag = any(r["name"] == "regime_tag" for r in
                      conn.execute("PRAGMA table_info(predictions)"))
        if has_tag and not conn.execute(
                "SELECT 1 FROM regime_observations LIMIT 1").fetchone():
            rows = conn.execute(
                """SELECT MIN(ts) ts, regime_tag FROM predictions
                   WHERE regime_tag IS NOT NULL
                   GROUP BY date(ts,'unixepoch'), regime_tag
                   ORDER BY ts""").fetchall()
            for r in rows:
                parts = str(r["regime_tag"]).split("/")
                if len(parts) < 3:
                    continue          # unparseable tag — skipped, never guessed
                ts_, direction, volatility = parts[0], parts[1], parts[2]
                macro = parts[3] if len(parts) > 3 else None
                conn.execute(
                    """INSERT INTO regime_observations
                         (ts, timescale, direction, volatility, macro, source)
                       VALUES (?,?,?,?,?,'derived')""",
                    (r["ts"], ts_, direction, volatility, macro))
                seeded += 1
        conn.execute("UPDATE schema_version SET version = 6")
        conn.commit()
        logger.info("v6 regime_observations: %s rows backfilled from "
                    "predictions.regime_tag (source=derived)%s", seeded,
                    "" if has_tag else " — no regime_tag column, nothing to derive")
    except Exception:
        conn.rollback()
        raise


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """In-place v6 → v7: strategies gain a symbol (the multi-asset turn).

    Additive: one column, DEFAULT 'BTC'. Backfill inspects each book's
    declared data points — where every symbol-bearing param unanimously
    names one symbol, that symbol is recorded; anything mixed or absent
    stays BTC, which is what every book on this desk has ever traded.
    Idempotent: the ALTER is skipped when the column already exists.
    """
    import json as _json
    try:
        have = any(r["name"] == "symbol" for r in
                   conn.execute("PRAGMA table_info(strategies)"))
        if not have:
            try:
                conn.execute("ALTER TABLE strategies ADD COLUMN symbol TEXT "
                             "NOT NULL DEFAULT 'BTC'")
            except sqlite3.OperationalError as exc:
                # check-then-ALTER is not atomic: a concurrent open can win
                # the race between our PRAGMA and our ALTER (CI's 2-core
                # timing produced exactly this). The loser's error IS the
                # success condition — the column exists.
                if "duplicate column name" not in str(exc):
                    raise
        relabelled = 0
        for name, dp_json in conn.execute(
                "SELECT name, data_points_json FROM strategies "
                "WHERE data_points_json IS NOT NULL").fetchall():
            try:
                syms = {str((dp.get("params") or {}).get("symbol")).strip()
                        for dp in _json.loads(dp_json) or []
                        if isinstance(dp, dict)
                        and (dp.get("params") or {}).get("symbol")}
            except Exception:
                continue
            if len(syms) == 1:
                sym = syms.pop()
                if sym and sym != "BTC":
                    conn.execute(
                        "UPDATE strategies SET symbol=? WHERE name=?",
                        (sym, name))
                    relabelled += 1
        conn.execute("UPDATE schema_version SET version = 7")
        conn.commit()
        logger.info("v7 strategies.symbol: added (default BTC), "
                    "%s books relabelled from unanimous data-point params",
                    relabelled)
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
