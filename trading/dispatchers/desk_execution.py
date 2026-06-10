"""Desk execution tools (toolset: desk-execution) — plutus-trade's hands.

Thin wrappers over the sacrosanct venue layer (hyperliquid/venue.py — atomic
normalTpsl brackets, bracket_warnings) that write the v2 lifecycle chain
around the venue call. The single-position law and the funded-prediction
requirement are enforced HERE, in code.

desk_open_position: prediction → thesis → decision → venue order (+brackets)
→ trades + positions rows. Refuses without a prediction_id (a thesis is a
funded prediction) or while any position is open.

desk_close_position: cancels tracked brackets, market-closes, writes the
closing decision/trade, computes the outcome row.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

OPEN_SCHEMA = {
    "name": "desk_open_position",
    "description": (
        "Open a position funding a prediction. Writes thesis (citing the "
        "prediction), decision, venue order WITH on-venue SL bracket "
        "(mandatory — a naked position is a critical failure), trade and "
        "position rows. Refuses while a position is open (one at a time) "
        "and without sl. After this returns, POST-ENTRY VERIFY on-venue "
        "(account_state) before reporting success upstream."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prediction_id": {"type": "integer"},
            "symbol": {"type": "string"},
            "side": {"type": "string", "enum": ["long", "short"]},
            "size": {"type": "number", "description": "Position size in coin units."},
            "sl": {"type": "number", "description": "Stop price (volatility-derived, risk_tolerance-scaled)."},
            "tp": {"type": "number"},
            "thesis": {"type": "string", "description": "Execution narrative (markdown)."},
            "sl_rationale": {"type": "string"},
            "conviction": {"type": "number"},
        },
        "required": ["prediction_id", "symbol", "side", "size", "sl", "thesis"],
    },
}

CLOSE_SCHEMA = {
    "name": "desk_close_position",
    "description": (
        "Close the open position: cancels tracked SL/TP brackets first, "
        "market-closes on-venue, writes the closing decision/trade and the "
        "outcome row (realized PnL, R-multiple vs the entry stop, holding "
        "time, conviction trajectory stats)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "position_id": {"type": "integer"},
            "exit_reason": {"type": "string",
                            "description": "sl|tp|invalidation|main_decision|..."},
        },
        "required": ["position_id", "exit_reason"],
    },
}


def _desk_open(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.integrations.hyperliquid.venue import hl_place_order
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    if queries.open_position(conn) is not None:
        return tool_error("a position is already open — one at a time is law")

    pred = queries.prediction(conn, int(args["prediction_id"]))
    if pred is None:
        return tool_error(f"prediction {args['prediction_id']} does not exist")
    if pred["resolved_at"] is not None:
        return tool_error(f"prediction {pred['id']} is already resolved — fund a live one")

    symbol = args["symbol"]
    side = args["side"]
    size = float(args["size"])
    sl = float(args["sl"])
    conviction = float(args.get("conviction", pred["conviction"]))
    session = session_id_from_context()

    try:
        fill = hl_place_order(
            symbol=symbol, side=side, size=size,
            sl=sl, tp=float(args["tp"]) if args.get("tp") else None,
        )
    except Exception as exc:
        return tool_error(f"venue order failed: {type(exc).__name__}: {exc}")

    thesis_id = write.record_thesis(
        conn, prediction_id=pred["id"], symbol=symbol,
        text_md=args["thesis"], agent="plutus-trade",
        sl_price=sl, sl_rationale_md=args.get("sl_rationale"),
        session_name=session,
    )
    decision_id = write.record_decision(
        conn, thesis_id=thesis_id,
        action="open_long" if side == "long" else "open_short",
        agent="plutus-trade", conviction=conviction,
        params={
            "sl": sl, "tp": args.get("tp"),
            "sl_order_id": fill.get("sl_order_id"),
            "tp_order_id": fill.get("tp_order_id"),
        },
    )
    trade_id = write.record_trade(
        conn, decision_id=decision_id, venue="hyperliquid", symbol=symbol,
        side=side, size=fill.get("size", size),
        fill_price=fill["fill_price"], slippage_bp=fill.get("slippage_bp"),
        venue_order_id=str(fill.get("order_id") or ""),
        venue_fill_id=str(fill.get("fill_id") or ""),
    )
    position_id = write.open_position(
        conn, venue="hyperliquid", symbol=symbol, side=side,
        size=fill.get("size", size), opening_trade_id=trade_id,
    )

    return tool_result({
        "ok": True,
        "position_id": position_id,
        "thesis_id": thesis_id,
        "fill": {"price": fill["fill_price"], "size": fill.get("size", size),
                 "slippage_bp": fill.get("slippage_bp")},
        "sl": {"price": sl, "order_id": fill.get("sl_order_id"),
               "on_venue": bool(fill.get("sl_order_id"))},
        "tp": {"price": args.get("tp"), "order_id": fill.get("tp_order_id")},
        "bracket_warnings": fill.get("bracket_warnings") or [],
    })


def _desk_close(args: Dict[str, Any]) -> str:
    from trading.integrations.hyperliquid.venue import hl_close_position
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    position_id = int(args["position_id"])
    pos = queries.open_position(conn)
    if pos is None or pos["id"] != position_id:
        return tool_error(f"position {position_id} is not the open position")

    try:
        close = hl_close_position(symbol=pos["symbol"], position_id=position_id)
    except Exception as exc:
        return tool_error(f"venue close failed: {type(exc).__name__}: {exc}")

    thesis = pos.get("thesis") or {}
    decision_id = write.record_decision(
        conn, thesis_id=thesis.get("id"), action="close",
        agent="plutus-trade", conviction=0.5,
        params={"exit_reason": args["exit_reason"]},
    )
    close_trade_id = write.record_trade(
        conn, decision_id=decision_id, venue="hyperliquid",
        symbol=pos["symbol"], side="close",
        size=close.get("size", pos["size"]),
        fill_price=close["fill_price"], slippage_bp=close.get("slippage_bp"),
        venue_order_id=str(close.get("order_id") or ""),
    )
    write.close_position(conn, position_id=position_id,
                         closing_trade_id=close_trade_id)

    outcome = _compute_outcome_fields(conn, pos, close, args["exit_reason"])
    write.record_outcome(conn, position_id=position_id, **outcome)

    return tool_result({
        "ok": True, "position_id": position_id,
        "fill": {"price": close["fill_price"], "size": close.get("size")},
        "cancel_warnings": close.get("cancel_warnings") or [],
        "outcome": outcome,
    })


def _compute_outcome_fields(conn, pos: dict, close: dict, exit_reason: str) -> dict:
    import time as _time

    entry = None
    row = conn.execute("SELECT fill_price, ts FROM trades WHERE id=?",
                       (pos["opening_trade_id"],)).fetchone()
    if row:
        entry = float(row["fill_price"])
    exit_px = float(close["fill_price"])
    size = float(pos["size"])
    sign = 1.0 if pos["side"] == "long" else -1.0
    pnl_usd = (exit_px - entry) * size * sign if entry else None
    pnl_pct = ((exit_px / entry) - 1.0) * 100 * sign if entry else None

    sl_price = (pos.get("thesis") or {}).get("sl_price")
    r_multiple = None
    if entry and sl_price and abs(entry - float(sl_price)) > 1e-12:
        r_multiple = (exit_px - entry) * sign / abs(entry - float(sl_price))

    evals = conn.execute(
        "SELECT conviction FROM position_evaluations WHERE position_id=? ORDER BY ts",
        (pos["id"],)).fetchall()
    convs = [e["conviction"] for e in evals]

    holding_minutes = None
    if row:
        holding_minutes = round((_time.time() - float(row["ts"])) / 60.0, 1)

    return {
        "realized_pnl_usd": round(pnl_usd, 4) if pnl_usd is not None else None,
        "realized_pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else None,
        "r_multiple": round(r_multiple, 3) if r_multiple is not None else None,
        "holding_minutes": holding_minutes,
        "exit_reason": exit_reason,
        "conviction_at_entry": convs[0] if convs else None,
        "conviction_at_exit": convs[-1] if convs else None,
        "conviction_min_during_hold": min(convs) if convs else None,
        "conviction_max_during_hold": max(convs) if convs else None,
        "conviction_evaluations_count": len(convs),
    }


registry.register(
    name="desk_open_position",
    toolset="desk-execution",
    schema=OPEN_SCHEMA,
    handler=lambda args, **kw: _desk_open(args),
    description="Open a position funding a prediction (atomic SL bracket, lifecycle chain).",
    emoji="📈",
)

registry.register(
    name="desk_close_position",
    toolset="desk-execution",
    schema=CLOSE_SCHEMA,
    handler=lambda args, **kw: _desk_close(args),
    description="Close the open position (brackets cancelled first, outcome computed).",
    emoji="📉",
)
