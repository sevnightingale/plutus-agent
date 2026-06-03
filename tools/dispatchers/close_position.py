"""close_position — execution dispatcher.

Closes an open position via the venue's ``close_position_fn`` and atomically
writes a closing trade, updates the position row, and (when the underlying
data is available) computes the outcome row including conviction-trajectory
derived stats from ``position_evaluations``.

Outcome computation (full PnL/R/MAE/MFE/efficiency math) is deferred to the
Hyperliquid execution wire-up in Phase 4l, which has the venue price history
needed to derive entry/exit efficiency and MAE/MFE. Phase 4a writes the
outcome shell with the conviction-trajectory derived stats — those depend
only on lifecycle.db data the dispatcher already has.
"""

from __future__ import annotations

import sqlite3
import statistics
import time
from typing import Any, Dict, Optional

from agent.lifecycle_db import get_lifecycle_db
from tools.core import venue_registry
from tools.dispatchers._helpers import json_dumps_compact, session_id_from_context
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "close_position",
    "description": (
        "Close an open position. Atomically writes a closing trade row, "
        "updates the position to status='closed', and creates an outcome "
        "row including conviction-trajectory derived stats "
        "(min/max/volatility/count) from this position's "
        "position_evaluations history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "venue":       {"type": "string"},
            "position_id": {"type": "integer"},
            "thesis_id":   {"type": "integer", "description": "Thesis driving the close (optional)."},
            "conviction":  {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "exit_reason": {"type": "string"},
            "extra":       {"type": "object", "additionalProperties": True},
        },
        "required": ["venue", "position_id", "conviction", "exit_reason"],
    },
}


def _conviction_trajectory_stats(conn, position_id: int) -> Dict[str, Any]:
    """Aggregate conviction trajectory from position_evaluations for outcome columns."""
    rows = conn.execute(
        "SELECT conviction FROM position_evaluations "
        "WHERE position_id = ? ORDER BY ts",
        (position_id,),
    ).fetchall()
    if not rows:
        return {
            "conviction_min_during_hold": None,
            "conviction_max_during_hold": None,
            "conviction_volatility": None,
            "conviction_evaluations_count": 0,
        }
    convictions = [r["conviction"] for r in rows]
    return {
        "conviction_min_during_hold": min(convictions),
        "conviction_max_during_hold": max(convictions),
        "conviction_volatility": (
            statistics.stdev(convictions) if len(convictions) >= 2 else 0.0
        ),
        "conviction_evaluations_count": len(convictions),
    }


def _close_position(args: Dict[str, Any]) -> str:
    venue = args.get("venue")
    position_id = args.get("position_id")
    thesis_id = args.get("thesis_id")
    conviction = args.get("conviction")
    exit_reason = args.get("exit_reason")
    extra = args.get("extra") or {}

    if not all([venue, position_id, exit_reason]) or conviction is None:
        return tool_error(
            "close_position requires venue, position_id, conviction, exit_reason"
        )
    if not (0.0 <= float(conviction) <= 1.0):
        return tool_error("conviction must be in [0.0, 1.0]")

    db = get_lifecycle_db()

    pos = db.conn().execute(
        "SELECT id, venue, symbol, side, size, status, opened_at "
        "FROM positions WHERE id = ?",
        (position_id,),
    ).fetchone()
    if pos is None:
        return tool_error(f"position_id {position_id} does not exist")
    if pos["status"] != "open":
        return tool_error(
            f"position {position_id} is already {pos['status']}; cannot close"
        )

    try:
        venue_entry = venue_registry.lookup(venue)
    except KeyError as exc:
        return tool_error(str(exc))
    if not venue_entry.close_position_fn:
        return tool_error(f"venue '{venue}' has no close_position_fn registered")

    try:
        fill = venue_entry.close_position_fn(
            symbol=pos["symbol"], position_id=position_id, **extra,
        )
    except Exception as exc:
        return tool_error(f"venue '{venue}' close_position_fn raised: {exc}")
    if not isinstance(fill, dict) or "fill_price" not in fill:
        return tool_error(
            f"venue '{venue}' close_position_fn returned malformed result"
        )

    ts = time.time()
    sid = session_id_from_context()
    decision_params = {"position_id": position_id, "exit_reason": exit_reason, **extra}

    def _write_chain(conn):
        # Need a decision row to anchor the closing trade. If thesis_id is
        # supplied, link to it; otherwise create a "system close" decision
        # against a synthetic placeholder thesis row would violate the FK,
        # so we require thesis_id when present, and otherwise raise.
        if thesis_id is None:
            # Fall back to the opening trade's decision's thesis so the FK chain
            # holds end-to-end. This keeps lifecycle traceable when the agent
            # didn't articulate a fresh closing thesis.
            opener = conn.execute(
                "SELECT d.thesis_id FROM trades t "
                "JOIN decisions d ON d.id = t.decision_id "
                "WHERE t.id = (SELECT opening_trade_id FROM positions WHERE id = ?)",
                (position_id,),
            ).fetchone()
            close_thesis_id = opener["thesis_id"] if opener else None
            if close_thesis_id is None:
                raise sqlite3.IntegrityError(
                    "no thesis_id available for closing decision"
                )
        else:
            close_thesis_id = thesis_id

        decision_id = conn.execute(
            "INSERT INTO decisions(thesis_id, ts, action, params_json, conviction) "
            "VALUES (?, ?, ?, ?, ?)",
            (close_thesis_id, ts, "close",
             json_dumps_compact(decision_params), float(conviction)),
        ).lastrowid

        close_trade_id = conn.execute(
            "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price, "
            "slippage_bp, venue_order_id, venue_fill_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, ts, venue, pos["symbol"], "close",
             fill.get("size", pos["size"]), fill["fill_price"],
             fill.get("slippage_bp"), fill.get("order_id"), fill.get("fill_id")),
        ).lastrowid

        conn.execute(
            "UPDATE positions SET closing_trade_id = ?, status = 'closed', "
            "closed_at = ?, perceived_at = ? WHERE id = ?",
            (close_trade_id, ts, ts, position_id),
        )

        # Outcome shell — full PnL/R/MAE/MFE math lands with the HL execution
        # wire-up (Phase 4l) once venue price history is available. We can,
        # however, compute the conviction-trajectory derived stats now from
        # lifecycle.db alone.
        traj = _conviction_trajectory_stats(conn, position_id)
        opening_decision = conn.execute(
            "SELECT d.conviction FROM trades t "
            "JOIN decisions d ON d.id = t.decision_id "
            "WHERE t.id = (SELECT opening_trade_id FROM positions WHERE id = ?)",
            (position_id,),
        ).fetchone()
        conviction_at_entry = (
            opening_decision["conviction"] if opening_decision else None
        )

        conn.execute(
            "INSERT INTO outcomes(position_id, exit_reason, "
            "conviction_at_entry, conviction_at_exit, "
            "conviction_min_during_hold, conviction_max_during_hold, "
            "conviction_volatility, conviction_evaluations_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (position_id, exit_reason,
             conviction_at_entry, float(conviction),
             traj["conviction_min_during_hold"], traj["conviction_max_during_hold"],
             traj["conviction_volatility"], traj["conviction_evaluations_count"]),
        )

        return decision_id, close_trade_id

    decision_id, close_trade_id = db._execute_write(_write_chain)
    sid  # captured for future session_id columns

    # Venue-specific outcome enrichment (PnL/MAE/MFE/efficiencies/slippage).
    # Called OUTSIDE the transaction — venues may make network calls (e.g.
    # HL fetches candle history over the holding window). Failures are
    # logged but non-fatal: the shell outcome row still tells the story.
    enrichment_error: Optional[str] = None
    if venue_entry.outcome_compute_fn is not None:
        try:
            enrichment = venue_entry.outcome_compute_fn(position_id=position_id)
        except Exception as exc:  # pragma: no cover — venue impls vary
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "venue '%s' outcome_compute_fn raised: %s", venue, exc
            )
            enrichment_error = f"{type(exc).__name__}: {exc}"
        else:
            if enrichment:
                _ALLOWED = {
                    "realized_pnl_usd", "realized_pnl_pct", "r_multiple",
                    "holding_minutes", "mae_pct", "mfe_pct",
                    "entry_efficiency", "exit_efficiency",
                    "slippage_total_bp",
                    "invalidation_triggered_at", "invalidation_to_exit_minutes",
                }
                cols = [k for k in enrichment.keys() if k in _ALLOWED]
                if cols:
                    set_clause = ", ".join(f"{c} = ?" for c in cols)
                    params = [enrichment[c] for c in cols] + [position_id]

                    def _update(conn):
                        conn.execute(
                            f"UPDATE outcomes SET {set_clause} WHERE position_id = ?",
                            params,
                        )

                    db._execute_write(_update)

    return tool_result({
        "decision_id": decision_id,
        "close_trade_id": close_trade_id,
        "position_id": position_id,
        "fill_price": fill["fill_price"],
        "exit_reason": exit_reason,
        "conviction_at_exit": float(conviction),
        "outcome_enrichment_error": enrichment_error,
    })


registry.register(
    name="close_position",
    toolset="execution",
    schema=SCHEMA,
    handler=lambda args, **kw: _close_position(args),
    description="Close a position; writes trade + outcome with trajectory stats.",
    emoji="🛑",
)
