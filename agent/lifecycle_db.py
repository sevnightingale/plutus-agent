#!/usr/bin/env python3
"""SQLite-backed lifecycle store for plutus-agent (Plutus, Stratum 3).

Persists the trade lifecycle: data point snapshots, strategies, theses,
decisions, trades, positions, conviction trajectories (position_evaluations),
outcomes, reflections, and capital movements. Plus FTS5 over text fields and
sqlite-vec virtual tables for vector similarity search over thesis/reflection
embeddings.

Mirrors the design of ``plutus_state.SessionDB`` (see that file for rationale):
- WAL mode for concurrent readers + one writer
- ``BEGIN IMMEDIATE`` + jitter retry for write transactions
- ``schema_version`` table for forward-compatible migrations
- FTS5 virtual tables backed by content tables with insert/delete/update triggers

Vector storage uses the sqlite-vec loadable extension. Connections must enable
extension loading before opening the lifecycle DB (handled in ``__init__``).
The vec0 virtual tables (``theses_vec``, ``reflections_vec``) hold the
search-optimized copy of each embedding; the canonical bytes also live on the
parent row as ``embedding BLOB`` so embeddings remain queryable without the
vec extension loaded.
"""

import logging
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import sqlite_vec

from plutus_constants import get_hermes_home

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_DB_PATH = get_hermes_home() / "lifecycle.db"

SCHEMA_VERSION = 2


TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- Stratum 3 — append-only trace of every perception, decision, and outcome.

CREATE TABLE IF NOT EXISTS data_point_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,
    name TEXT NOT NULL,
    params_json TEXT,
    value_json TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description_md TEXT,
    hypothesis_md TEXT,
    regime_conditions_json TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'paused' | 'retired'
    created_at REAL NOT NULL,
    retired_at REAL,
    retirement_reason TEXT
);

CREATE TABLE IF NOT EXISTS theses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,
    symbol TEXT,
    text_md TEXT NOT NULL,
    strategy_id INTEGER REFERENCES strategies(id),  -- legacy; new code uses strategy_name
    strategy_name TEXT,                             -- name of STRATEGY.md file under ~/.plutus-agent/strategies/
    regime_tag TEXT,                                -- regime perceived at thesis formation (e.g., 'distribution_breakdown')
    prediction_horizon_hours REAL,                  -- explicit time bound; null = open-ended (discouraged)
    structured_tags_json TEXT,
    snapshot_ids_json TEXT,
    invalidation_criteria_json TEXT,
    embedding BLOB,
    embedding_model TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL REFERENCES theses(id),
    ts REAL NOT NULL,
    action TEXT NOT NULL,           -- 'open_long' | 'open_short' | 'close' | 'modify_sl' | 'skip' | 'hold' | ...
    params_json TEXT,
    conviction REAL NOT NULL DEFAULT 0.5  -- 0.0-1.0
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES decisions(id),
    ts REAL NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,             -- 'long' | 'short' | 'close'
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
    side TEXT NOT NULL,             -- 'long' | 'short'
    size REAL NOT NULL,
    opening_trade_id INTEGER NOT NULL REFERENCES trades(id),
    closing_trade_id INTEGER REFERENCES trades(id),
    status TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'closed'
    opened_at REAL NOT NULL,         -- venue-actual open ts
    closed_at REAL,                  -- venue-actual close ts (null while open)
    perceived_at REAL                -- when Plutus saw it closed (may lag closed_at)
);

CREATE TABLE IF NOT EXISTS position_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,
    position_id INTEGER NOT NULL REFERENCES positions(id),
    conviction REAL NOT NULL,        -- 0.0-1.0
    thesis_status TEXT,              -- 'intact' | 'strengthened' | 'weakening' | 'invalidated'
    active_thesis_id INTEGER REFERENCES theses(id),
    rationale_md TEXT,
    snapshot_ids_json TEXT,
    recommended_action TEXT,         -- 'hold' | 'exit_now' | 'tighten_sl' | 'scale_in' | 'scale_out'
    action_taken_decision_id INTEGER REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS outcomes (
    position_id INTEGER PRIMARY KEY REFERENCES positions(id),
    realized_pnl_usd REAL,
    realized_pnl_pct REAL,
    r_multiple REAL,
    holding_minutes REAL,
    mae_pct REAL,                    -- max adverse excursion %
    mfe_pct REAL,                    -- max favorable excursion %
    entry_efficiency REAL,
    exit_efficiency REAL,
    slippage_total_bp REAL,
    exit_reason TEXT,
    -- conviction trajectory derived stats:
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
    session_id TEXT,
    ts REAL NOT NULL,
    text_md TEXT NOT NULL,
    position_ids_json TEXT,
    related_thesis_ids_json TEXT,
    related_prediction_ids_json TEXT,
    reflection_kind TEXT,            -- 'post_trade' | 'loss_postmortem' | 'weekly_review' | 'ad_hoc' | 'calibration_review' | 'strategy_review' | 'setup_complete'
    error_class TEXT,                -- on losses: 'forecast' | 'execution' | 'sizing' | 'regime' | 'variance' | 'process_violation'
    strategy_name TEXT,              -- the strategy this reflection is about (if applicable)
    embedding BLOB,
    embedding_model TEXT
);

-- ─────────────────────────────────────────────────────────────────────────
-- Predictions (PLUTUS Stratum 3, observation track)
--
-- Pre-registered falsifiable claims with NO associated trade. The point
-- is to build calibration without putting capital at risk: Plutus says
-- "I think X will happen by Y", time passes, Plutus checks whether X
-- happened, and the resolution feeds the calibration curve.
--
-- Distinct from theses (which drive trades) and observations (passive
-- journal entries). A thesis can be derived from a prediction once it
-- triggers; the prediction stays as the original epistemic record.
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,                       -- when made
    horizon_ts REAL NOT NULL,               -- when it must be resolved by
    symbol TEXT,                            -- optional; null for non-symbol claims (e.g., 'BTC.D rises 1pp')
    claim_md TEXT NOT NULL,                 -- the falsifiable claim
    success_criteria_json TEXT NOT NULL,    -- machine-checkable definition of 'correct'
    failure_criteria_json TEXT,             -- machine-checkable definition of 'wrong' (else: success criteria flipped)
    conviction REAL NOT NULL DEFAULT 0.5,   -- 0.0-1.0, the prior
    strategy_name TEXT,                     -- which strategy generated the prediction (often null = freeform observation)
    regime_tag TEXT,                        -- regime when prediction made
    snapshot_ids_json TEXT,                 -- supporting data points
    structured_tags_json TEXT,

    -- Resolution (set when prediction is checked):
    resolved_at REAL,
    outcome TEXT,                           -- 'correct' | 'wrong' | 'ambiguous' | 'expired_unresolvable'
    resolution_notes_md TEXT,
    resolution_snapshot_ids_json TEXT,      -- supporting data points at resolution
    realized_value_json TEXT                -- what actually happened (for retrospective)
);

-- ─────────────────────────────────────────────────────────────────────────
-- Observations (PLUTUS Stratum 3, journal stream)
--
-- Dated micro-notes — the trader's running journal. Cheap to write,
-- FTS-indexed for retrieval. NOT a replacement for theses or predictions;

-- this is the "I noticed X" / "I'm watching Y" / "almost took this trade
-- and didn't because Z" stream that compounds into expertise over time.
--
-- WORLDVIEW.md captures the SYNTHESIS; observations are the RAW STREAM.
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,
    symbol TEXT,                            -- optional symbol focus
    kind TEXT,                              -- 'noticed' | 'watching' | 'almost_traded' | 'mental_model' | 'pattern_candidate' | 'edge_claim' | 'edge_revoked'
    text_md TEXT NOT NULL,
    strategy_name TEXT,                     -- if observation is about a strategy
    related_thesis_ids_json TEXT,
    related_prediction_ids_json TEXT,
    snapshot_ids_json TEXT,
    structured_tags_json TEXT
);

CREATE TABLE IF NOT EXISTS capital_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,
    from_account TEXT,
    to_account TEXT,
    token TEXT NOT NULL,
    amount_token REAL NOT NULL,
    amount_usd_at_time REAL,
    movement_type TEXT NOT NULL,     -- 'deposit' | 'withdrawal' | 'internal_transfer' | 'venue_transfer'
    tx_hash TEXT,
    note TEXT
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_data_point_snapshots_name_ts
    ON data_point_snapshots(name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_data_point_snapshots_ts
    ON data_point_snapshots(ts DESC);
CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);
CREATE INDEX IF NOT EXISTS idx_theses_symbol_ts ON theses(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_theses_strategy ON theses(strategy_id);
CREATE INDEX IF NOT EXISTS idx_theses_strategy_name ON theses(strategy_name);
CREATE INDEX IF NOT EXISTS idx_theses_regime ON theses(regime_tag);
CREATE INDEX IF NOT EXISTS idx_theses_ts ON theses(ts DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_thesis ON decisions(thesis_id);
CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(action);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_decision ON trades(decision_id);
CREATE INDEX IF NOT EXISTS idx_trades_venue_symbol_ts
    ON trades(venue, symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_venue_symbol
    ON positions(venue, symbol);
CREATE INDEX IF NOT EXISTS idx_positions_opened ON positions(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_position_evaluations_position_ts
    ON position_evaluations(position_id, ts);
CREATE INDEX IF NOT EXISTS idx_reflections_kind_ts
    ON reflections(reflection_kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_ts ON reflections(ts DESC);
CREATE INDEX IF NOT EXISTS idx_reflections_strategy ON reflections(strategy_name);
CREATE INDEX IF NOT EXISTS idx_reflections_error_class ON reflections(error_class);
CREATE INDEX IF NOT EXISTS idx_predictions_unresolved
    ON predictions(horizon_ts) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_predictions_strategy
    ON predictions(strategy_name);
CREATE INDEX IF NOT EXISTS idx_predictions_outcome
    ON predictions(outcome);
CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(ts DESC);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts DESC);
CREATE INDEX IF NOT EXISTS idx_observations_kind ON observations(kind);
CREATE INDEX IF NOT EXISTS idx_observations_symbol ON observations(symbol);
CREATE INDEX IF NOT EXISTS idx_observations_strategy ON observations(strategy_name);
CREATE INDEX IF NOT EXISTS idx_observations_session_id ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_capital_movements_ts
    ON capital_movements(ts DESC);
CREATE INDEX IF NOT EXISTS idx_capital_movements_type
    ON capital_movements(movement_type);
"""


FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS theses_fts USING fts5(
    text_md,
    content=theses,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS theses_fts_insert AFTER INSERT ON theses BEGIN
    INSERT INTO theses_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;

CREATE TRIGGER IF NOT EXISTS theses_fts_delete AFTER DELETE ON theses BEGIN
    INSERT INTO theses_fts(theses_fts, rowid, text_md) VALUES('delete', old.id, old.text_md);
END;

CREATE TRIGGER IF NOT EXISTS theses_fts_update AFTER UPDATE ON theses BEGIN
    INSERT INTO theses_fts(theses_fts, rowid, text_md) VALUES('delete', old.id, old.text_md);
    INSERT INTO theses_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS reflections_fts USING fts5(
    text_md,
    content=reflections,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS reflections_fts_insert AFTER INSERT ON reflections BEGIN
    INSERT INTO reflections_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;

CREATE TRIGGER IF NOT EXISTS reflections_fts_delete AFTER DELETE ON reflections BEGIN
    INSERT INTO reflections_fts(reflections_fts, rowid, text_md) VALUES('delete', old.id, old.text_md);
END;

CREATE TRIGGER IF NOT EXISTS reflections_fts_update AFTER UPDATE ON reflections BEGIN
    INSERT INTO reflections_fts(reflections_fts, rowid, text_md) VALUES('delete', old.id, old.text_md);
    INSERT INTO reflections_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS predictions_fts USING fts5(
    claim_md,
    content=predictions,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS predictions_fts_insert AFTER INSERT ON predictions BEGIN
    INSERT INTO predictions_fts(rowid, claim_md) VALUES (new.id, new.claim_md);
END;

CREATE TRIGGER IF NOT EXISTS predictions_fts_delete AFTER DELETE ON predictions BEGIN
    INSERT INTO predictions_fts(predictions_fts, rowid, claim_md) VALUES('delete', old.id, old.claim_md);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    text_md,
    content=observations,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS observations_fts_insert AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, text_md) VALUES (new.id, new.text_md);
END;

CREATE TRIGGER IF NOT EXISTS observations_fts_delete AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, text_md) VALUES('delete', old.id, old.text_md);
END;
"""


VEC_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS theses_vec USING vec0(
    thesis_id INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);

CREATE VIRTUAL TABLE IF NOT EXISTS reflections_vec USING vec0(
    reflection_id INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);
"""


def _open_connection(db_path: Path) -> sqlite3.Connection:
    """Open a sqlite3 connection with sqlite-vec loaded and pragmas set.

    Each connection needs sqlite-vec loaded individually — extension state
    does not persist across connections.
    """
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        timeout=1.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class LifecycleDB:
    """SQLite-backed Plutus lifecycle store with FTS5 + sqlite-vec.

    Thread-safe for the gateway pattern (multiple reader threads, single writer
    via WAL). Each method runs through ``_execute_write`` for writes, which
    serializes via a Python lock and BEGIN IMMEDIATE, with jittered retry on
    ``database is locked``. Reads are concurrent.
    """

    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020
    _WRITE_RETRY_MAX_S = 0.150
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._write_count = 0
        self._conn = _open_connection(self.db_path)

        self._init_schema()

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run a write transaction with BEGIN IMMEDIATE + jittered retry.

        See ``plutus_state.SessionDB._execute_write`` for the rationale (avoids
        SQLite's deterministic backoff convoy under multi-process write
        contention).
        """
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.commit()
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" in msg or "busy" in msg:
                    last_err = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        time.sleep(random.uniform(
                            self._WRITE_RETRY_MIN_S,
                            self._WRITE_RETRY_MAX_S,
                        ))
                        continue
                raise
        raise last_err or sqlite3.OperationalError(
            "lifecycle.db locked after max retries"
        )

    def _try_wal_checkpoint(self) -> None:
        """Best-effort PASSIVE WAL checkpoint. Never blocks, never raises."""
        try:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    def close(self) -> None:
        """Close the connection (best-effort PASSIVE checkpoint first)."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass
                self._conn.close()
                self._conn = None

    def conn(self) -> sqlite3.Connection:
        """Return the underlying connection for read queries.

        Writes must go through ``_execute_write`` to get the write lock +
        retry behavior; direct execution against this connection is safe for
        SELECTs only.
        """
        return self._conn

    def _init_schema(self) -> None:
        """Create tables, FTS5, and vec0 virtual tables; record schema version.

        Idempotent: re-running on an initialized DB is a no-op apart from the
        schema_version row check. v1→v2 migrations run if needed (additive
        ALTER TABLEs only — never destructive).
        """
        cursor = self._conn.cursor()

        # Detect whether this is an existing v1 DB before applying v2 schema.
        # An existing DB has tables; a fresh DB does not.
        existing_tables = {
            r[0] for r in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        had_theses = "theses" in existing_tables
        had_reflections = "reflections" in existing_tables

        # Order matters:
        #   1. CREATE TABLE IF NOT EXISTS (preserves v1 shape if existing)
        #   2. ALTER TABLE for missing columns (v1 → v2 migration)
        #   3. CREATE INDEX IF NOT EXISTS (now references existing columns)
        #   4. FTS5 + vec0 virtual tables
        cursor.executescript(TABLES_SQL)

        if had_theses:
            self._add_column_if_missing(cursor, "theses", "strategy_name", "TEXT")
            self._add_column_if_missing(cursor, "theses", "regime_tag", "TEXT")
            self._add_column_if_missing(cursor, "theses", "prediction_horizon_hours", "REAL")
        if had_reflections:
            self._add_column_if_missing(cursor, "reflections", "related_prediction_ids_json", "TEXT")
            self._add_column_if_missing(cursor, "reflections", "error_class", "TEXT")
            self._add_column_if_missing(cursor, "reflections", "strategy_name", "TEXT")

        cursor.executescript(INDEXES_SQL)
        cursor.executescript(FTS_SQL)
        cursor.executescript(VEC_SQL)

        cursor.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif row[0] < SCHEMA_VERSION:
            cursor.execute(
                "UPDATE schema_version SET version = ?",
                (SCHEMA_VERSION,),
            )
            logger.info(
                "lifecycle.db migrated %d → %d (additive)",
                row[0], SCHEMA_VERSION,
            )
        self._conn.commit()

    @staticmethod
    def _add_column_if_missing(cursor, table: str, column: str, type_: str) -> None:
        existing = {
            r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")
            logger.info("lifecycle.db: added %s.%s %s", table, column, type_)


# ─── Module-level singleton ──────────────────────────────────────────────────

_db_singleton: Optional[LifecycleDB] = None
_singleton_lock = threading.Lock()


def get_lifecycle_db(db_path: Optional[Path] = None) -> LifecycleDB:
    """Return the process-wide LifecycleDB singleton.

    First call optionally takes ``db_path`` (typically only set in tests).
    Subsequent calls return the same instance regardless of ``db_path``.
    """
    global _db_singleton
    with _singleton_lock:
        if _db_singleton is None:
            _db_singleton = LifecycleDB(db_path=db_path)
        return _db_singleton


def reset_lifecycle_db_singleton() -> None:
    """Test-only: drop the singleton so the next ``get_lifecycle_db`` rebuilds.

    Closes the existing connection if present.
    """
    global _db_singleton
    with _singleton_lock:
        if _db_singleton is not None:
            _db_singleton.close()
        _db_singleton = None
