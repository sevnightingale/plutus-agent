"""Typed writers — the ONLY code that inserts into lifecycle.db v2.

Every writer stamps ``agent`` + ``session_name`` provenance and runs in one
transaction. Failures raise; nothing is silently defaulted (no-fallback
doctrine). Embedding columns are filled by the caller when the embedder is
available — absence degrades similarity search, loudly, nothing else.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

from trading.lifecycle import criteria as criteria_mod
from trading.lifecycle.db import derive_timescale

VALID_RISK = ("low", "med", "high")
VALID_KINDS = ("strategy", "stress", "adhoc")
VALID_OUTCOMES = ("correct", "wrong", "ambiguous", "expired_unresolvable")


@dataclass
class SupportScore:
    data_point: str
    score: float
    kind: str                      # 'numerical' | 'narrative'
    reading_json: Optional[str] = None
    weight: Optional[float] = None
    normalizer: Optional[str] = None
    reasoning_md: Optional[str] = None


@dataclass
class PredictionDraft:
    claim_md: str
    horizon_ts: float
    success_criteria: dict
    conviction: float
    agent: str
    session_name: Optional[str] = None
    symbol: Optional[str] = None
    failure_criteria: Optional[dict] = None
    invalidation_criteria: Optional[dict] = None
    risk_tolerance: Optional[str] = None
    strategy_name: Optional[str] = None
    kind: str = "strategy"
    regime_tag: Optional[str] = None
    snapshot_ids: Sequence[int] = field(default_factory=tuple)
    support_scores: Sequence[SupportScore] = field(default_factory=tuple)
    ts: Optional[float] = None     # default: now


def record_prediction(
    conn: sqlite3.Connection,
    draft: PredictionDraft,
    *,
    known_data_points: Optional[set] = None,
) -> int:
    """Validate and insert a prediction (+ its support scores). Returns id.

    Refusals (raise ValueError):
    - success criteria that fail the machine-resolvable contract
    - horizon beyond the 30d cap / not after ts
    - kind='strategy' without a strategy_name (file-at-birth doctrine)
    - narrative support scores without recorded reasoning
    """
    ts = draft.ts if draft.ts is not None else time.time()

    problems = criteria_mod.validate(
        draft.success_criteria, known_data_points=known_data_points
    )
    if problems:
        raise ValueError(
            "unresolvable success criteria — prediction refused:\n  "
            + "\n  ".join(problems)
        )
    if draft.failure_criteria is not None:
        problems = criteria_mod.validate(
            draft.failure_criteria, known_data_points=known_data_points
        )
        if problems:
            raise ValueError(
                "unresolvable failure criteria — prediction refused:\n  "
                + "\n  ".join(problems)
            )

    timescale = derive_timescale(ts, draft.horizon_ts)  # raises on cap breach

    if draft.kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    if draft.kind == "strategy" and not draft.strategy_name:
        raise ValueError(
            "kind='strategy' requires strategy_name (file-at-birth: every "
            "hypothesis has a strategy file; use kind='stress'/'adhoc' otherwise)"
        )
    if draft.risk_tolerance is not None and draft.risk_tolerance not in VALID_RISK:
        raise ValueError(f"risk_tolerance must be one of {VALID_RISK}")
    if not 0.0 <= draft.conviction <= 1.0:
        raise ValueError("conviction must be in [0, 1]")

    for s in draft.support_scores:
        if s.kind == "narrative" and not (s.reasoning_md or "").strip():
            raise ValueError(
                f"narrative support score for {s.data_point!r} has no recorded "
                "reasoning — refused (the reasoning IS the audit trail)"
            )
        if not 0.0 <= s.score <= 1.0:
            raise ValueError(f"support score for {s.data_point!r} outside [0, 1]")

    cur = conn.execute(
        """INSERT INTO predictions (
            session_name, agent, ts, horizon_ts, timescale, symbol, claim_md,
            success_criteria_json, failure_criteria_json,
            invalidation_criteria_json, risk_tolerance, conviction,
            strategy_name, kind, regime_tag, snapshot_ids_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            draft.session_name, draft.agent, ts, draft.horizon_ts, timescale,
            draft.symbol, draft.claim_md,
            json.dumps(draft.success_criteria),
            json.dumps(draft.failure_criteria) if draft.failure_criteria else None,
            json.dumps(draft.invalidation_criteria) if draft.invalidation_criteria else None,
            draft.risk_tolerance, draft.conviction,
            draft.strategy_name, draft.kind, draft.regime_tag,
            json.dumps(list(draft.snapshot_ids)) if draft.snapshot_ids else None,
        ),
    )
    prediction_id = cur.lastrowid
    for s in draft.support_scores:
        conn.execute(
            """INSERT INTO support_scores (
                prediction_id, data_point, score, kind, reading_json,
                weight, normalizer, reasoning_md, ts
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                prediction_id, s.data_point, s.score, s.kind, s.reading_json,
                s.weight, s.normalizer, s.reasoning_md, ts,
            ),
        )
    conn.commit()
    return prediction_id


def resolve_prediction(
    conn: sqlite3.Connection,
    prediction_id: int,
    outcome: str,
    *,
    resolved_by: str,
    notes_md: Optional[str] = None,
    realized_value: Optional[dict] = None,
    snapshot_ids: Sequence[int] = (),
    ts: Optional[float] = None,
) -> None:
    """Record a resolution and bump the strategy mirror counters atomically."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")
    ts = ts if ts is not None else time.time()

    row = conn.execute(
        "SELECT strategy_name, resolved_at FROM predictions WHERE id=?",
        (prediction_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"prediction {prediction_id} does not exist")
    if row["resolved_at"] is not None:
        raise ValueError(f"prediction {prediction_id} is already resolved")

    conn.execute(
        """UPDATE predictions SET resolved_at=?, outcome=?, resolved_by=?,
           resolution_notes_md=?, realized_value_json=?,
           resolution_snapshot_ids_json=? WHERE id=?""",
        (
            ts, outcome, resolved_by, notes_md,
            json.dumps(realized_value) if realized_value else None,
            json.dumps(list(snapshot_ids)) if snapshot_ids else None,
            prediction_id,
        ),
    )
    if row["strategy_name"]:
        col = {
            "correct": "n_correct",
            "wrong": "n_wrong",
            "ambiguous": "n_ambiguous",
            "expired_unresolvable": "n_ambiguous",
        }[outcome]
        conn.execute(
            f"""UPDATE strategies SET n_resolved=n_resolved+1, {col}={col}+1,
               last_resolved_at=? WHERE name=?""",
            (ts, row["strategy_name"]),
        )
    conn.commit()


def record_thesis(
    conn: sqlite3.Connection,
    *,
    prediction_id: int,
    symbol: str,
    text_md: str,
    agent: str,
    session_name: Optional[str] = None,
    sl_price: Optional[float] = None,
    sl_rationale_md: Optional[str] = None,
    structured_tags: Optional[dict] = None,
    snapshot_ids: Sequence[int] = (),
    ts: Optional[float] = None,
) -> int:
    """A thesis is a funded prediction — prediction_id is mandatory."""
    pred = conn.execute(
        "SELECT strategy_name FROM predictions WHERE id=?", (prediction_id,)
    ).fetchone()
    if pred is None:
        raise ValueError(f"prediction {prediction_id} does not exist")
    cur = conn.execute(
        """INSERT INTO theses (
            prediction_id, session_name, agent, ts, symbol, text_md,
            strategy_name, sl_price, sl_rationale_md, structured_tags_json,
            snapshot_ids_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            prediction_id, session_name, agent,
            ts if ts is not None else time.time(),
            symbol, text_md, pred["strategy_name"], sl_price, sl_rationale_md,
            json.dumps(structured_tags) if structured_tags else None,
            json.dumps(list(snapshot_ids)) if snapshot_ids else None,
        ),
    )
    conn.commit()
    return cur.lastrowid


def record_decision(conn, *, thesis_id, action, agent, conviction=0.5,
                    params=None, ts=None) -> int:
    cur = conn.execute(
        "INSERT INTO decisions (thesis_id, agent, ts, action, params_json, conviction)"
        " VALUES (?,?,?,?,?,?)",
        (thesis_id, agent, ts if ts is not None else time.time(), action,
         json.dumps(params) if params else None, conviction),
    )
    conn.commit()
    return cur.lastrowid


def record_trade(conn, *, decision_id, venue, symbol, side, size, fill_price,
                 slippage_bp=None, venue_order_id=None, venue_fill_id=None,
                 ts=None) -> int:
    cur = conn.execute(
        """INSERT INTO trades (decision_id, ts, venue, symbol, side, size,
           fill_price, slippage_bp, venue_order_id, venue_fill_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (decision_id, ts if ts is not None else time.time(), venue, symbol,
         side, size, fill_price, slippage_bp, venue_order_id, venue_fill_id),
    )
    conn.commit()
    return cur.lastrowid


def open_position(conn, *, venue, symbol, side, size, opening_trade_id,
                  opened_at=None, entry_account_value=None,
                  leverage=None) -> int:
    cur = conn.execute(
        """INSERT INTO positions (venue, symbol, side, size, opening_trade_id,
           status, opened_at, entry_account_value, leverage)
           VALUES (?,?,?,?,?,'open',?,?,?)""",
        (venue, symbol, side, size, opening_trade_id,
         opened_at if opened_at is not None else time.time(),
         entry_account_value, leverage),
    )
    conn.commit()
    return cur.lastrowid


def close_position(conn, *, position_id, closing_trade_id, closed_at=None,
                   perceived_at=None) -> None:
    n = conn.execute(
        """UPDATE positions SET closing_trade_id=?, status='closed',
           closed_at=?, perceived_at=? WHERE id=? AND status='open'""",
        (closing_trade_id, closed_at if closed_at is not None else time.time(),
         perceived_at, position_id),
    ).rowcount
    if n == 0:
        raise ValueError(f"position {position_id} not open (or missing)")
    conn.commit()


def record_evaluation(conn, *, position_id, conviction, agent,
                      thesis_status=None, active_thesis_id=None,
                      rationale_md=None, snapshot_ids=(),
                      recommended_action=None, session_name=None,
                      ts=None) -> int:
    cur = conn.execute(
        """INSERT INTO position_evaluations (
            session_name, agent, ts, position_id, conviction, thesis_status,
            active_thesis_id, rationale_md, snapshot_ids_json, recommended_action
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (session_name, agent, ts if ts is not None else time.time(),
         position_id, conviction, thesis_status, active_thesis_id,
         rationale_md, json.dumps(list(snapshot_ids)) if snapshot_ids else None,
         recommended_action),
    )
    conn.commit()
    return cur.lastrowid


def record_outcome(conn, *, position_id, **fields) -> None:
    """Insert the outcome row (one per position). Derived conviction stats
    are computed by the caller from position_evaluations."""
    allowed = {
        "realized_pnl_usd", "realized_pnl_pct", "r_multiple", "holding_minutes",
        "mae_pct", "mfe_pct", "entry_efficiency", "exit_efficiency",
        "slippage_total_bp", "exit_reason", "conviction_at_entry",
        "conviction_at_exit", "conviction_min_during_hold",
        "conviction_max_during_hold", "conviction_volatility",
        "conviction_evaluations_count", "invalidation_triggered_at",
        "invalidation_to_exit_minutes",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown outcome fields: {sorted(unknown)}")
    cols = ["position_id", *fields.keys()]
    conn.execute(
        f"INSERT INTO outcomes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        (position_id, *fields.values()),
    )
    conn.commit()


def record_reflection(conn, *, text_md, agent, reflection_kind=None,
                      error_class=None, strategy_name=None, position_ids=(),
                      thesis_ids=(), prediction_ids=(), session_name=None,
                      ts=None) -> int:
    valid_errors = (None, "forecast", "execution", "sizing", "regime",
                    "variance", "process_violation")
    if error_class not in valid_errors:
        raise ValueError(f"error_class must be one of {valid_errors[1:]}")
    cur = conn.execute(
        """INSERT INTO reflections (
            session_name, agent, ts, text_md, position_ids_json,
            related_thesis_ids_json, related_prediction_ids_json,
            reflection_kind, error_class, strategy_name
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (session_name, agent, ts if ts is not None else time.time(), text_md,
         json.dumps(list(position_ids)) if position_ids else None,
         json.dumps(list(thesis_ids)) if thesis_ids else None,
         json.dumps(list(prediction_ids)) if prediction_ids else None,
         reflection_kind, error_class, strategy_name),
    )
    conn.commit()
    return cur.lastrowid


def record_observation(conn, *, text_md, agent, kind=None, symbol=None,
                       strategy_name=None, thesis_ids=(), prediction_ids=(),
                       snapshot_ids=(), structured_tags=None,
                       session_name=None, ts=None) -> int:
    cur = conn.execute(
        """INSERT INTO observations (
            session_name, agent, ts, symbol, kind, text_md, strategy_name,
            related_thesis_ids_json, related_prediction_ids_json,
            snapshot_ids_json, structured_tags_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (session_name, agent, ts if ts is not None else time.time(), symbol,
         kind, text_md, strategy_name,
         json.dumps(list(thesis_ids)) if thesis_ids else None,
         json.dumps(list(prediction_ids)) if prediction_ids else None,
         json.dumps(list(snapshot_ids)) if snapshot_ids else None,
         json.dumps(structured_tags) if structured_tags else None),
    )
    conn.commit()
    return cur.lastrowid


def record_snapshot(conn, *, name, value_json, agent, params_json=None,
                    source=None, session_name=None, ts=None) -> int:
    cur = conn.execute(
        """INSERT INTO data_point_snapshots (
            session_name, agent, ts, name, params_json, value_json, source
        ) VALUES (?,?,?,?,?,?,?)""",
        (session_name, agent, ts if ts is not None else time.time(), name,
         params_json, value_json, source),
    )
    conn.commit()
    return cur.lastrowid


def record_action_run(conn, *, action_type, agent, ok=True, notes_md=None,
                      session_name=None, ts=None) -> int:
    cur = conn.execute(
        """INSERT INTO action_runs (action_type, ts, agent, session_name, ok, notes_md)
           VALUES (?,?,?,?,?,?)""",
        (action_type, ts if ts is not None else time.time(), agent,
         session_name, 1 if ok else 0, notes_md),
    )
    conn.commit()
    return cur.lastrowid
