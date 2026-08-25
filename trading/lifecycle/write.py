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
from trading.lifecycle import price_zone
from trading.lifecycle import queries
from trading.lifecycle.db import derive_timescale

VALID_RISK = ("low", "med", "high")
VALID_KINDS = ("strategy", "stress", "adhoc")
VALID_OUTCOMES = ("correct", "wrong", "ambiguous", "expired_unresolvable")

# Max OPEN predictions per strategy. Simultaneous predictions from one
# strategy in one regime window are correlated trials — they resolve off the
# same conditions, inflating N toward the graduation bar (N>=15, >=2/3)
# without independent evidence. Three allows an honest spread (e.g. a level
# ladder) while keeping the track record statistically meaningful.
MAX_OPEN_PER_STRATEGY = queries.BASE_PREDICTION_OPEN_CAP

# Incubation fast lane: a book already proving out — net expectancy above the
# cost margin but not yet `tradeable` (under the multiplicity-deflated hurdle
# or short of N), and not decaying — earns a wider cap. Evidence velocity is
# the honest lever the deflated gate leaves: the selection premium shrinks as
# √n, so a real edge clears sooner, at zero trading risk. The price is some
# extra correlation between simultaneous trials (the reason the base cap
# exists); a bump to five keeps that bounded while roughly doubling
# throughput for exactly the books where evidence is worth the most.
INCUBATION_OPEN_CAP = queries.INCUBATION_PREDICTION_OPEN_CAP


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
    entry_ref_price: float          # spot at registration; the % zone is relative to this
    near_edge_pct: float            # correctness floor — signed % move (bullish +, bearish -)
    far_edge_pct: float             # optimistic target — signed % move, |far| > |near|
    conviction: float
    agent: str
    session_name: Optional[str] = None
    symbol: Optional[str] = None
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
    resolvable_data_points: Optional[set] = None,
) -> int:
    """Validate and insert a prediction (+ its support scores). Returns id.

    A prediction is a PRICE ZONE: a near edge (correctness floor) and far edge
    (target), both signed % moves from ``entry_ref_price`` (spot captured at
    registration by the caller, never the LLM). Direction is implied by sign.

    Refusals (raise ValueError):
    - a malformed price zone (zero/mismatched-sign edges, |far| ≤ |near|)
    - missing/non-positive entry_ref_price (the % zone is meaningless without it)
    - invalidation criteria that fail the machine-resolvable contract (unknown
      or perception-only data points)
    - horizon beyond the 30d cap / not after ts
    - kind='strategy' without a strategy_name (file-at-birth doctrine)
    - strategy already at its open cap of undecided open predictions
      (correlated trials inflate N without independent evidence; win-locked
      rows — ``reached_near_at`` stamped, outcome already decided — don't
      count, so a winning strategy is never capped out of its next setup).
      The cap is MAX_OPEN_PER_STRATEGY, or INCUBATION_OPEN_CAP for a book in
      the incubation fast lane (net-positive above the cost margin, not yet
      tradeable, not decaying — proving out toward the deflated hurdle)
    - narrative support scores without recorded reasoning
    """
    ts = draft.ts if draft.ts is not None else time.time()

    zone_problems = price_zone.validate_zone(draft.near_edge_pct, draft.far_edge_pct)
    if zone_problems:
        raise ValueError(
            "invalid price zone — prediction refused:\n  " + "\n  ".join(zone_problems)
        )
    if draft.entry_ref_price is None or float(draft.entry_ref_price) <= 0:
        raise ValueError(
            "entry_ref_price (spot at registration) is required and must be > 0 "
            "— the % zone is meaningless without it"
        )

    if draft.invalidation_criteria is not None:
        # Normalise BEFORE validating: a leaf that omits the symbol is bound
        # to the prediction's own, so what lands in the row is self-describing
        # and readable at resolution. Anything still missing a required param
        # after binding is refused here rather than dying silently for weeks.
        draft.invalidation_criteria = criteria_mod.bind_symbol(
            draft.invalidation_criteria, draft.symbol)
        problems = criteria_mod.validate(
            draft.invalidation_criteria, known_data_points=known_data_points,
            resolvable_data_points=resolvable_data_points,
        )
        if problems:
            raise ValueError(
                "unresolvable invalidation criteria — prediction refused:\n  "
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
    if draft.strategy_name:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM predictions "
            "WHERE strategy_name = ? AND resolved_at IS NULL AND reached_near_at IS NULL",
            (draft.strategy_name,),
        ).fetchone()[0]
        capacity = queries.strategy_prediction_capacity(
            conn, draft.strategy_name, open_count=open_count)
        cap = capacity["open_cap"]
        if open_count >= cap:
            raise ValueError(
                f"strategy {draft.strategy_name!r} already has {open_count} "
                f"undecided open predictions (cap {cap}) — "
                f"refused. Simultaneous undecided predictions from one strategy "
                f"in one regime window are correlated trials: they resolve "
                f"together and inflate N toward graduation without independent "
                f"evidence. Win-locked predictions (near edge reached, outcome "
                f"already decided) don't count against the cap. "
                f"Wait for resolutions, or register for a different strategy."
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

    zone_json = json.dumps({
        "entry_ref_price": float(draft.entry_ref_price),
        "near_edge_pct": float(draft.near_edge_pct),
        "far_edge_pct": float(draft.far_edge_pct),
    })
    cur = conn.execute(
        """INSERT INTO predictions (
            session_name, agent, ts, horizon_ts, timescale, symbol, claim_md,
            entry_ref_price, near_edge_pct, far_edge_pct,
            success_criteria_json, invalidation_criteria_json, risk_tolerance,
            conviction, strategy_name, kind, regime_tag, snapshot_ids_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            draft.session_name, draft.agent, ts, draft.horizon_ts, timescale,
            draft.symbol, draft.claim_md,
            float(draft.entry_ref_price), float(draft.near_edge_pct),
            float(draft.far_edge_pct),
            zone_json,
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
    reached_near_at: Optional[float] = None,
    reached_far_at: Optional[float] = None,
    ts: Optional[float] = None,
) -> bool:
    """Record a resolution and bump strategy mirror counters — race-safe.

    Returns True if THIS call resolved the prediction, False if it was already
    resolved (the conditional UPDATE matched 0 rows). Two resolvers — the
    watcher alert and the ops safety-net sweep — can race; only the winner's
    UPDATE matches, so counters bump exactly once. An invalidation is recorded
    as ``outcome='wrong'`` with ``realized_value['resolution_mode']='invalidated'``
    (so every existing win-rate query keeps working).

    ``reached_near_at`` / ``reached_far_at`` stamp the resolution markers when
    not already set (``COALESCE`` keeps an earlier near touch). A ``'target'``
    resolution passes both (far reached now); a ``'horizon'`` correct passes
    only near; a wrong resolution passes neither.
    """
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}")
    ts = ts if ts is not None else time.time()

    row = conn.execute(
        "SELECT strategy_name FROM predictions WHERE id=?",
        (prediction_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"prediction {prediction_id} does not exist")

    cur = conn.execute(
        """UPDATE predictions SET resolved_at=?, outcome=?, resolved_by=?,
           resolution_notes_md=?, realized_value_json=?,
           resolution_snapshot_ids_json=?,
           reached_near_at=COALESCE(reached_near_at, ?),
           reached_far_at=COALESCE(reached_far_at, ?)
           WHERE id=? AND resolved_at IS NULL""",
        (
            ts, outcome, resolved_by, notes_md,
            json.dumps(realized_value) if realized_value else None,
            json.dumps(list(snapshot_ids)) if snapshot_ids else None,
            reached_near_at, reached_far_at,
            prediction_id,
        ),
    )
    if cur.rowcount != 1:
        # Another resolver already won (or the row vanished) — no double-count.
        conn.commit()
        return False
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
    return True


def mark_reached_near(conn: sqlite3.Connection, prediction_id: int,
                      ts: Optional[float] = None) -> bool:
    """Stamp ``reached_near_at`` once — the moment the win is LOCKED.

    Race-safe and idempotent: the conditional UPDATE only matches an open,
    not-yet-stamped row, so the watcher and the ops sweep can both detect the
    near touch and exactly one stamps it. Returns True if THIS call stamped it.
    The prediction stays open — only the far edge (early) or the horizon
    resolves it.
    """
    cur = conn.execute(
        "UPDATE predictions SET reached_near_at=? "
        "WHERE id=? AND reached_near_at IS NULL AND resolved_at IS NULL",
        (ts if ts is not None else time.time(), prediction_id),
    )
    conn.commit()
    return cur.rowcount == 1


def record_prediction_evaluation(
    conn: sqlite3.Connection,
    *,
    prediction_id: int,
    conviction: float,
    support_scores_json: Optional[str] = None,
    regime_tag: Optional[str] = None,
    agent: Optional[str] = None,
    session_name: Optional[str] = None,
    ts: Optional[float] = None,
) -> int:
    """Append a conviction-trajectory point for an OPEN prediction.

    Written by ops each re-score (the cheap conviction_score tool). The curve
    is reflect's raw material for entry-timing and calibration-v2.
    """
    cur = conn.execute(
        """INSERT INTO prediction_evaluations (
            prediction_id, session_name, agent, ts, conviction,
            support_scores_json, regime_tag
        ) VALUES (?,?,?,?,?,?,?)""",
        (
            prediction_id, session_name, agent,
            ts if ts is not None else time.time(),
            conviction, support_scores_json, regime_tag,
        ),
    )
    conn.commit()
    return cur.lastrowid


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


def record_capital_movement(conn, *, ts, token, amount_token, movement_type,
                            amount_usd_at_time=None, from_account=None,
                            to_account=None, tx_hash=None, note=None,
                            session_name=None) -> Optional[int]:
    """Record one deposit/withdrawal/transfer. Idempotent on ``tx_hash``.

    Returns the new row id, or None when the movement was already recorded —
    which is the normal case on every reconciliation after the first, not an
    error. The uniqueness is enforced by ux_capital_movements_tx rather than a
    read-then-write, so concurrent reconcilers cannot both insert.
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO capital_movements
             (session_name, ts, from_account, to_account, token,
              amount_token, amount_usd_at_time, movement_type, tx_hash, note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (session_name, ts, from_account, to_account, token,
         amount_token, amount_usd_at_time, movement_type, tx_hash, note),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


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


# ── Regime ─────────────────────────────────────────────────────────────────
# The taxonomy is deliberately small and CLOSED. A writer that accepts
# "choppy" or "mildly-trending" destroys the only property that makes cells
# comparable to each other, and the multiplicity premium is now scoped to a
# cell — so an invented label does not merely blur a report, it silently
# changes whose bar a strategy is measured against.
REGIME_DIRECTIONS = ("trending-up", "trending-down", "ranging")
REGIME_VOLATILITIES = ("compressed", "normal", "elevated")
REGIME_MACROS = ("risk-on", "neutral", "risk-off")
REGIME_TIMESCALES = ("intraday", "swing", "position")


def record_regime(conn, *, timescale, direction, volatility, macro=None,
                  symbol="BTC", conviction=None, flipped=False,
                  session_name=None, notes_md=None, ts=None,
                  source="observed") -> int:
    """Record the regime at one timescale. Append-only; latest row wins.

    Refuses anything outside the closed vocabulary rather than coercing it —
    the desk's standing law that validation lives in the writer, applied to a
    record that until 2026-07-27 was freeform markdown no code could read.

    ``macro`` belongs to the position scale alone. Passing it elsewhere is a
    refusal, not a silent drop: a caller that thinks intraday has a macro
    label has misunderstood the taxonomy and should hear so.
    """
    if timescale not in REGIME_TIMESCALES:
        raise ValueError(
            f"timescale {timescale!r} — one of {list(REGIME_TIMESCALES)}")
    if direction not in REGIME_DIRECTIONS:
        raise ValueError(
            f"direction {direction!r} — one of {list(REGIME_DIRECTIONS)}. The "
            f"taxonomy is closed; a new label needs a doctrine change, not a "
            f"write.")
    if volatility not in REGIME_VOLATILITIES:
        raise ValueError(
            f"volatility {volatility!r} — one of {list(REGIME_VOLATILITIES)}")
    if macro is not None:
        if timescale != "position":
            raise ValueError(
                f"macro is a position-scale label only; got {macro!r} at "
                f"{timescale}")
        if macro not in REGIME_MACROS:
            raise ValueError(f"macro {macro!r} — one of {list(REGIME_MACROS)}")
    cur = conn.execute(
        """INSERT INTO regime_observations
             (ts, symbol, timescale, direction, volatility, macro, conviction,
              flipped, source, session_name, notes_md)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ts if ts is not None else time.time(), symbol, timescale, direction,
         volatility, macro, conviction, 1 if flipped else 0, source,
         session_name, notes_md))
    conn.commit()
    return cur.lastrowid
