"""Desk execution tools (toolset: desk-execution) — the deterministic hands.

main calls these directly (the plutus-trade sub-agent is retired): execution is
arithmetic + structured venue ops, not judgment, so it lives in code. Thin over
the sacrosanct venue layer (hyperliquid/venue.py — atomic normalTpsl brackets)
which it wraps with the v2 lifecycle chain. The single-position law, the
funded-prediction requirement, the expectancy gate, risk-based sizing, and the
naked-position abort are all enforced HERE, in code.

desk_open_position: given a prediction_id (+ thesis), DERIVES side, live entry,
stop (empirical MAE envelope, ATR fallback), target (the edge the strategy
GRADUATED on — best_target near or far, a fixed zone level), and risk-based
size from the conviction band; enforces every mechanical guard in-tool (HALT,
one-position, staleness, ACTIVE status, trade readiness, expectancy gate);
places a market order with an atomic on-venue SL bracket; verifies on-venue and
ABORTS (auto-closes) a naked position. Explicit sl/tp/size
override derivation (transition / tests). See PLANNING-trade-execution-collapse.md.

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
        "Open a position funding a prediction — DETERMINISTIC. Given a "
        "prediction_id (+ thesis_md narrative), derives side, live entry, stop "
        "(empirical MAE envelope, ATR fallback), target (the edge the strategy "
        "graduated on — best_target near or far, a fixed zone level), and "
        "risk-based size from the conviction band; applies the expectancy gate "
        "(refuses negative-EV / non-tradeable setups); places a market order "
        "with an atomic on-venue SL bracket (mandatory); verifies on-venue and "
        "ABORTS (auto-closes) a naked position. ALL mechanical guards are "
        "enforced in-tool: refuses under HALT, while a position is open (one "
        "at a time), if the prediction is stale (>20 min), if the strategy is "
        "not ACTIVE, if the trade path is not READY (hl_trade_readiness), or "
        "if the setup is below the expectancy gate. You only supply the "
        "prediction_id and a short thesis."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prediction_id": {"type": "integer"},
            "thesis_md": {"type": "string", "description": "Execution narrative (markdown), authored by main."},
        },
        "required": ["prediction_id", "thesis_md"],
    },
}

ADOPT_SCHEMA = {
    "name": "desk_adopt_position",
    "description": (
        "Adopt an on-venue position that lifecycle.db doesn't know about "
        "(orphaned fills from failed opens, out-of-band entries) so the desk "
        "can manage it: writes the thesis→decision→trade→position chain from "
        "VENUE TRUTH (live account_state — side, size, entry price are read "
        "from the venue, never supplied). Requires the venue to show exactly "
        "one open position and the DB to show flat. prediction_id links the "
        "prediction these fills were funding (a thesis is a funded "
        "prediction — mandatory). Pass the protecting sl/tp order ids + "
        "prices if known so close-time bracket cancel works."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prediction_id": {"type": "integer"},
            "thesis_md": {"type": "string", "description": "Why this position is being kept (markdown)."},
            "sl_order_id": {"type": "string", "description": "On-venue stop trigger oid protecting this position (from account_state open_orders)."},
            "tp_order_id": {"type": "string", "description": "On-venue TP trigger oid."},
            "sl_price": {"type": "number"},
            "tp_price": {"type": "number"},
            "conviction": {"type": "number", "description": "Conviction carried on the decision (default 0.5)."},
        },
        "required": ["prediction_id", "thesis_md"],
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
            "exit_reason": {
                "type": "string",
                "description": (
                    "Why the position closed (invalidation ≠ stop-loss): "
                    "sl | tp | invalidation | thesis_break | alert_take_profit | "
                    "main_decision | naked_position_abort"
                ),
            },
        },
        "required": ["position_id", "exit_reason"],
    },
}


MARKET_SLIPPAGE = 0.003   # marketable-limit ±0.3% cap (the market path's IoC limit)
ATR_STOP_MULT = 1.5       # ATR-multiple stop when the MAE envelope is too thin
_ATR_INTERVAL = {"intraday": "1h", "swing": "4h", "position": "1d"}


def _halt_reason():
    """Operator kill-switch, checked IN the money tools (defense in depth
    under the plutus-trade-safety plugin hook, which only sees registered
    tool calls). None when trading is live; the HALT note ('' if none)
    when the operator has paused."""
    from harness.constants import get_hermes_home
    path = get_hermes_home() / "HALT"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        return ""


def _pilot_armed() -> bool:
    """Operator pilot mandate (Sev, 2026-08-22): while armed, a TEST-book
    prediction above the global conviction threshold may fund — graduation
    gates size-with-evidence, the pilot gates existence. Armed by touching
    ``~/.plutus-agent/PILOT`` (the HALT pattern, inverted); disarm with rm.
    Every pilot decision is tagged ``pilot: true`` so reflect and calibration
    can slice pilot trades from graduated trades forever."""
    from harness.constants import get_hermes_home
    return (get_hermes_home() / "PILOT").exists()


def _fresh_price(symbol: str) -> float:
    """Live mark price, fetched server-side (never from a stale LLM view)."""
    from trading.perception.core import data_point_registry
    entry = data_point_registry.lookup("hl_price")
    px = data_point_registry.extract_numeric(entry.fn(symbol=symbol), entry.numeric_path)
    if not px or px <= 0:
        raise ValueError(f"hl_price for {symbol} unavailable")
    return float(px)


def _trade_readiness() -> Dict[str, Any]:
    """Live on-chain agent-wallet registration check (TRADING.md fact #3)."""
    from trading.integrations.hyperliquid.data_points import hl_trade_readiness
    return hl_trade_readiness()


def _derive_stop_pct(conn, strategy_name, symbol, timescale, current):
    """Hard SL distance (% from entry): empirical all-resolutions MAE, with an
    ATR-multiple fallback when the envelope is too thin. Returns
    ``(stop_pct, rationale)`` — ``(None, reason)`` when neither is available
    (honest absence: the caller then refuses rather than guessing a stop)."""
    from trading.lifecycle import queries
    if strategy_name:
        stop = queries.hard_stop_pct(conn, strategy_name)
        if stop:
            return stop, (f"empirical all-resolutions MAE "
                          f"p{int(queries.HARD_SL_PERCENTILE * 100)} = {stop}%")
    try:
        from trading.perception.core import data_point_registry
        e = data_point_registry.lookup("ta_atr")
        interval = _ATR_INTERVAL.get(timescale, "1h")
        atr = data_point_registry.extract_numeric(
            e.fn(symbol=symbol, interval=interval), e.numeric_path)
        if atr and atr > 0 and current > 0:
            stop = round(ATR_STOP_MULT * (atr / current) * 100.0, 4)
            return stop, (f"ATR fallback {ATR_STOP_MULT}×ATR({interval})={stop}% "
                          f"(envelope < n={queries.HARD_SL_MIN_N})")
    except Exception as exc:
        return None, f"no stop: envelope thin AND ATR failed ({type(exc).__name__}: {exc})"
    return None, "no stop: envelope thin and ATR unavailable"


def _sl_rests_on_venue(state, symbol, sl_order_id, sl_price=None) -> bool:
    """On-venue truth: is the SL trigger actually resting? Match by order id
    when we have one; otherwise by a trigger order at the SL price
    (frontend_open_orders reports triggerPx). The price match is required —
    "any trigger on the symbol" would let a resting TP masquerade as the SL."""
    orders = (state or {}).get("open_orders") or []
    if sl_order_id:
        for o in orders:
            if str(o.get("oid")) == str(sl_order_id):
                return True
    for o in orders:
        if o.get("coin") != symbol:
            continue
        if not (o.get("isTrigger") or o.get("triggerPx")
                or "trigger" in str(o.get("orderType", "")).lower()):
            continue
        if sl_price is None:
            return True
        try:
            trigger_px = float(o.get("triggerPx"))
        except (TypeError, ValueError):
            continue
        # 0.15% tolerance absorbs HL's 5-sig-fig price rounding; SL and TP
        # levels are always far wider apart than this.
        if abs(trigger_px - float(sl_price)) <= 0.0015 * float(sl_price):
            return True
    return False


def _desk_open(args: Dict[str, Any]) -> str:
    from trading.conviction.engine import (MAX_LEVERAGE, MIN_NOTIONAL_USD,
                                           target_notional_multiple)
    from trading.dispatchers._helpers import session_id_from_context
    from trading.integrations.hyperliquid.venue import hl_account_state, hl_place_order
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    halt = _halt_reason()
    if halt is not None:
        return tool_result({"ok": False,
                            "refused": "HALT is set — operator paused trading",
                            **({"halt_note": halt} if halt else {})})
    if queries.open_position(conn) is not None:
        return tool_error("a position is already open — one at a time is law")

    pred = queries.prediction(conn, int(args["prediction_id"]))
    if pred is None:
        return tool_error(f"prediction {args['prediction_id']} does not exist")
    if pred["resolved_at"] is not None:
        return tool_error(f"prediction {pred['id']} is already resolved — fund a live one")

    far = pred.get("far_edge_pct")
    entry_ref = pred.get("entry_ref_price")
    symbol = args.get("symbol") or pred["symbol"]
    side = args.get("side") or ("long" if (far or 0) > 0 else "short")
    conviction = float(args.get("conviction", pred["conviction"]))
    strategy_name = pred.get("strategy_name")
    timescale = pred.get("timescale")
    thesis = args.get("thesis_md") or ""
    session = session_id_from_context()

    # DERIVE everything from the prediction + live market — one path, fully gated.
    # RECENCY guard (defense-in-depth vs main's best_actionable filter): never
    # fund a prediction whose entry conditions have aged out.
    import time as _t
    age_s = _t.time() - float(pred["ts"])
    if age_s > queries.ACTIONABLE_MAX_AGE_S:
        return tool_result({"ok": False, "refused": "prediction too stale to fund",
                            "age_s": round(age_s),
                            "max_age_s": queries.ACTIONABLE_MAX_AGE_S})
    try:
        current = _fresh_price(symbol)
    except Exception as exc:
        return tool_error(f"price read failed for {symbol}: {type(exc).__name__}: {exc}")
    if far is None:
        return tool_error(f"prediction {pred['id']} has no far_edge_pct — cannot derive target")
    far = float(far)
    if not entry_ref or float(entry_ref) <= 0:
        return tool_error(f"prediction {pred['id']} has no entry_ref_price — cannot derive TP")

    stop_pct, sl_rationale = _derive_stop_pct(conn, strategy_name, symbol, timescale, current)
    if not stop_pct:
        return tool_error(f"no stop available ({sl_rationale}) — refusing (honest absence)")
    sl = (current * (1 - stop_pct / 100.0) if side == "long"
          else current * (1 + stop_pct / 100.0))

    # Expectancy gate: the strategy must be ACTIVE (graduated — status is the
    # binary gate, not just a selection filter), tradeable, AND this setup must
    # be +EV at the LIVE price (RR > (1−p)/p — the staleness + worth-it gate).
    # PILOT lane (operator mandate 2026-08-22): while ~/.plutus-agent/PILOT
    # exists, a TEST book may fund too — graduation keeps gating the evidence-
    # backed lane, the pilot gates existence. Retired books never fund.
    if not strategy_name:
        return tool_result({"ok": False, "refused": "prediction has no strategy — cannot gate"})
    srow = conn.execute("SELECT status FROM strategies WHERE name=?",
                        (strategy_name,)).fetchone()
    status = srow["status"] if srow else None
    pilot_trade = False
    if status != "active":
        if _pilot_armed() and status == "test":
            pilot_trade = True
        else:
            return tool_result({"ok": False,
                                "refused": "strategy not ACTIVE — only graduated "
                                           "strategies fund"
                                           + (" (pilot lane takes test books "
                                              "only)" if _pilot_armed() else
                                              " (pilot not armed)"),
                                "strategy": strategy_name, "status": status})
    exp = queries.strategy_expectancy(conn, strategy_name)
    if not pilot_trade and not exp["tradeable"]:
        return tool_result({"ok": False, "refused": "strategy not tradeable",
                            "expectancy_pct": exp["expectancy_pct"], "n": exp["n"],
                            "hurdle_pct": exp["hurdle_pct"],
                            "decaying": exp["decaying"]})
    # GEOMETRY INVARIANT: the mechanical TP is the edge the strategy GRADUATED
    # on (best_target) — the graduation sim, the entry gate, and the placed
    # bracket must share one geometry. A near-edge book takes profit at near.
    tp_target = exp["best_target"] or "far"
    tp_edge = far if tp_target == "far" else pred.get("near_edge_pct")
    if tp_edge is None:
        return tool_error(f"prediction {pred['id']} has no {tp_target}_edge_pct "
                          "— cannot derive TP")
    tp = float(entry_ref) * (1.0 + float(tp_edge) / 100.0)   # FIXED zone target level
    # p counts scratches as non-wins (wins/n) so the gate's p is consistent
    # with expectancy, which carries scratches at PnL 0 — the scratch-free
    # win_rate overstates the hit rate the book actually delivers.
    # A pilot book with no resolved history gets a NEUTRAL prior (p=0.5): the
    # threshold then reads "reward must beat stop plus round-trip costs" —
    # the gate still kills fee-thin setups without demanding history the
    # book cannot have yet.
    p = (exp["wins"] / exp["n"]) if exp["n"] else (0.5 if pilot_trade else None)
    reward_pct = abs(tp - current) / current * 100.0
    rr = reward_pct / stop_pct if stop_pct else 0.0
    # rr > (1−p)/p is p·reward > (1−p)·stop; the extra term charges the
    # estimated round-trip cost so a fee-thin setup fails the gate.
    threshold = ((1.0 - p) / p
                 + queries.ESTIMATED_ROUND_TRIP_COST_PCT / (p * stop_pct)
                 if p and stop_pct else None)
    if threshold is None:
        return tool_result({"ok": False, "refused": "no win-rate calibration"})
    if rr <= threshold:
        return tool_result({"ok": False, "refused": "setup below expectancy gate",
                            "rr": round(rr, 3), "rr_threshold": round(threshold, 3),
                            "p_win": round(p, 3), "tp_target": tp_target})

    multiple = target_notional_multiple(conviction)
    if multiple is None:
        return tool_result({"ok": False, "refused": "conviction below global threshold",
                            "conviction": conviction})

    # Trade-path readiness (TRADING.md fact #3): an unregistered/expired agent
    # wallet makes every order fail SILENTLY on-venue — refuse loudly here
    # instead. Unverifiable readiness also refuses (honest absence): if the
    # check can't run, the order would most likely fail anyway.
    try:
        readiness = _trade_readiness()
    except Exception as exc:
        return tool_result({"ok": False,
                            "refused": "trade readiness unverifiable",
                            "error": f"{type(exc).__name__}: {exc}"})
    if not readiness.get("ready"):
        return tool_result({"ok": False, "refused": "trade path not READY",
                            "reason": readiness.get("reason")})
    sizing: Dict[str, Any] = {
        "mode": "notional_based", "notional_multiple": multiple,
        "stop_pct": stop_pct,
        # loss-at-stop as a fraction of equity — reflect's sizing-review raw
        # material now that risk varies with stop width by design
        "risk_at_stop_pct": round(multiple * stop_pct, 3),
        "rr": round(rr, 3), "rr_threshold": round(threshold, 3),
        "pilot": pilot_trade,
        "current_price": current}

    # PRE-ENTRY (flat) equity BEFORE the fill (Issue 3): the denominator for
    # sizing + realized leverage. In-position equity_breakdown double-counts
    # collateral, so this MUST read flat. Notional sizing NEEDS it — a failure
    # blocks the open (can't size honestly without equity; honest absence).
    entry_account_value = leverage = None
    sizing_warning = None
    try:
        pre_state = hl_account_state()
        equity = float(pre_state["equity_usd"])
        if equity > 0:
            entry_account_value = equity
        else:
            sizing_warning = "equity_usd <= 0"
        # Venue preflight: lifecycle.db is not the only truth. A crash or
        # timeout window can leave a live on-venue position the DB never
        # recorded — stacking a new fill on top doubles exposure silently.
        untracked = [(pp or {}).get("coin")
                     for pp in (pre_state.get("open_perp_positions") or [])]
        if untracked:
            return tool_error(
                f"venue shows open position(s) {untracked} but lifecycle.db "
                "shows flat — refusing to stack exposure; reconcile first: "
                "desk_adopt_position (keep it, books catch up) or flatten "
                "manually")
    except Exception as exc:
        sizing_warning = f"account_state failed ({type(exc).__name__}: {exc})"

    if entry_account_value is None:
        return tool_error(f"cannot size: pre-fill equity unavailable ({sizing_warning})")
    # Notional-based: position = multiple × equity (operator bands, 2026-08-22).
    # Floor at the venue minimum ($10 — HL rejects below it; the deviation is
    # recorded and only binds under ~$20 equity), cap at the leverage backstop.
    notional = multiple * entry_account_value
    if notional < MIN_NOTIONAL_USD:
        notional = MIN_NOTIONAL_USD
        sizing["min_notional_floored"] = True
    if notional > MAX_LEVERAGE * entry_account_value:
        notional = MAX_LEVERAGE * entry_account_value
        sizing["leverage_capped"] = True
    size = notional / current
    sizing["notional_usd"] = round(notional, 2)
    sizing["size_coin"] = round(size, 8)

    try:
        fill = hl_place_order(symbol=symbol, side=side, size=size, sl=sl, tp=tp,
                              order_type="market", slippage=MARKET_SLIPPAGE)
    except Exception as exc:
        return tool_error(f"venue order failed: {type(exc).__name__}: {exc}")

    notional_filled = fill["fill_price"] * fill.get("size", size)
    if entry_account_value:
        leverage = round(notional_filled / entry_account_value, 3)

    # Entry delta (Issue 5): fill drift vs the prediction's entry_ref_price —
    # reflect's raw material for wait-vs-immediate entry. Predictions stay
    # immutable; this lives on the decision's free-form params.
    entry_delta_pct = (round((fill["fill_price"] - float(entry_ref)) / float(entry_ref) * 100, 4)
                       if entry_ref else None)

    # 4-target ALERT levels (the two judgment triggers inside the mechanical
    # SL/TP bounds). Stored on the decision; poll_hl_position_alert fires a wake
    # when price crosses one, and main re-scores + decides (take-profit / cut /
    # hold). alert-up = the near edge (fixed zone level); alert-down = the
    # winners' MAE (median-anchored), anchored to the fill. Derive path only.
    alert_near_px = alert_adverse_px = None
    if entry_ref:
        near_pct = pred.get("near_edge_pct")
        # On a near-target book the mechanical TP already SITS at the near
        # edge — an alert there would just race the TP fill. Far-target only.
        if near_pct is not None and tp_target == "far":
            alert_near_px = round(float(entry_ref) * (1 + float(near_pct) / 100.0), 6)
        wmae = queries.mae_envelope(
            conn, strategy_name=strategy_name,
            population="reached_target_winners", statistic="median_anchored",
            median_multiplier=3.0, min_n=queries.HARD_SL_MIN_N)["suggested_sl_pct"]
        if wmae:
            fp = fill["fill_price"]
            alert_adverse_px = round(
                fp * (1 - wmae / 100.0) if side == "long" else fp * (1 + wmae / 100.0), 6)

    thesis_id = write.record_thesis(
        conn, prediction_id=pred["id"], symbol=symbol, text_md=thesis,
        agent="plutus-main", sl_price=sl, sl_rationale_md=sl_rationale,
        session_name=session)
    decision_id = write.record_decision(
        conn, thesis_id=thesis_id,
        action="open_long" if side == "long" else "open_short",
        agent="plutus-main", conviction=conviction,
        params={"sl": sl, "tp": tp, "sl_order_id": fill.get("sl_order_id"),
                "tp_order_id": fill.get("tp_order_id"),
                "entry_delta_pct": entry_delta_pct, "sizing": sizing,
                "pilot": pilot_trade,
                "alert_near_px": alert_near_px,
                "alert_adverse_px": alert_adverse_px})
    trade_id = write.record_trade(
        conn, decision_id=decision_id, venue="hyperliquid", symbol=symbol,
        side=side, size=fill.get("size", size), fill_price=fill["fill_price"],
        slippage_bp=fill.get("slippage_bp"),
        venue_order_id=str(fill.get("order_id") or ""),
        venue_fill_id=str(fill.get("fill_id") or ""))
    position_id = write.open_position(
        conn, venue="hyperliquid", symbol=symbol, side=side,
        size=fill.get("size", size), opening_trade_id=trade_id,
        entry_account_value=entry_account_value, leverage=leverage)

    # POST-ENTRY VERIFY + NAKED-POSITION ABORT — the one money-critical guard.
    # Brackets are atomic (normalTpsl), but a leg can still be rejected.
    # RESPONSE signal: a rejected SL leg produces an "SL..." bracket warning.
    # Acceptance often carries NO oid — trigger orders come back as the bare
    # string "waitingForTrigger" — so a missing order id is NOT failure; a
    # warning is. ON-VENUE truth (frontend_open_orders) then rules in BOTH
    # directions whenever readable: a resting stop at the SL price clears the
    # fill, a missing one aborts it. If on-venue state is unreadable, the
    # response verdict stands. Naked → auto-close immediately.
    sl_order_id = fill.get("sl_order_id")
    bracket_warns = fill.get("bracket_warnings") or []
    sl_leg_warned = any(str(w).startswith("SL") for w in bracket_warns)
    naked = sl is not None and sl_leg_warned
    position_on_venue = None
    venue_verified = False
    try:
        st = hl_account_state()
        position_on_venue = any((pp or {}).get("coin") == symbol
                                for pp in (st.get("open_perp_positions") or []))
        orders = st.get("open_orders")
        if sl is not None and orders is not None:
            rests = _sl_rests_on_venue(st, symbol, sl_order_id, sl_price=sl)
            naked = not rests
            venue_verified = rests
    except Exception:
        pass  # can't verify on-venue — the response verdict stands

    if naked:
        abort_close = json.loads(_desk_close(
            {"position_id": position_id, "exit_reason": "naked_position_abort"}))
        if not abort_close.get("ok"):
            # The abort-close itself failed: the position is LIVE with no
            # verified stop. Never report a clean abort — say exactly that.
            return tool_result({
                "ok": False,
                "aborted_reason": (
                    "naked_position: SL did not rest on-venue AND the "
                    "abort-close FAILED — the position may still be OPEN and "
                    "UNPROTECTED on-venue. Check account_state and close "
                    "manually NOW."),
                "abort_close_failed": True,
                "abort_close_error": abort_close.get("error"),
                "position_id": position_id, "thesis_id": thesis_id,
                "fill": {"price": fill["fill_price"], "size": fill.get("size", size)},
                "bracket_warnings": bracket_warns,
            })
        return tool_result({
            "ok": False, "aborted_reason": "naked_position: SL did not rest on-venue",
            "position_id": position_id, "thesis_id": thesis_id,
            "fill": {"price": fill["fill_price"], "size": fill.get("size", size)},
            "bracket_warnings": bracket_warns,
        })

    return tool_result({
        "ok": True,
        "position_id": position_id,
        "thesis_id": thesis_id,
        # verified means CONFIRMED ON-VENUE. "response_only" = the venue
        # accepted the bracket but the open-orders re-read wasn't available —
        # the stop almost certainly rests (atomic bulk order), it just wasn't
        # independently confirmed. Do NOT re-place a stop over it.
        "verified": bool(venue_verified),
        "position_on_venue": position_on_venue,
        "fill": {"price": fill["fill_price"], "size": fill.get("size", size),
                 "slippage_bp": fill.get("slippage_bp")},
        "sizing": {**sizing, "entry_account_value": entry_account_value,
                   "leverage": leverage,
                   **({"warning": sizing_warning} if sizing_warning else {})},
        "sl": {"price": sl, "order_id": fill.get("sl_order_id"),
               "on_venue": bool(venue_verified),
               "verification": "on_venue" if venue_verified else "response_only",
               "rationale": sl_rationale},
        "tp": {"price": tp, "order_id": fill.get("tp_order_id"),
               "target": tp_target},
        "bracket_warnings": bracket_warns,
    })


def _desk_adopt(args: Dict[str, Any]) -> str:
    from trading.integrations.hyperliquid.venue import hl_account_state
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    if queries.open_position(conn) is not None:
        return tool_error("lifecycle.db already has an open position — "
                          "nothing to adopt (one at a time is law)")

    thesis_md = (args.get("thesis_md") or "").strip()
    if not thesis_md:
        return tool_error("thesis_md is required — why is this position kept?")

    try:
        st = hl_account_state()
    except Exception as exc:
        return tool_error(f"cannot adopt: account_state failed "
                          f"({type(exc).__name__}: {exc})")
    live = [pp for pp in (st.get("open_perp_positions") or []) if pp]
    if not live:
        return tool_error("venue shows no open position — nothing to adopt")
    if len(live) > 1:
        return tool_error(f"venue shows {len(live)} open positions — adopt "
                          "supports exactly one; flatten the others first")

    pp = live[0]
    symbol = pp.get("coin")
    szi = float(pp.get("szi", 0))
    side = "long" if szi > 0 else "short"
    size = abs(szi)
    entry_px = float(pp.get("entryPx"))

    try:
        thesis_id = write.record_thesis(
            conn, prediction_id=int(args["prediction_id"]), symbol=symbol,
            text_md=thesis_md, agent="plutus-main",
            sl_price=args.get("sl_price"),
            sl_rationale_md="adopted — protection pre-existing on venue")
    except ValueError as exc:
        return tool_error(str(exc))
    decision_id = write.record_decision(
        conn, thesis_id=thesis_id,
        action="adopt_long" if side == "long" else "adopt_short",
        agent="plutus-main",
        conviction=float(args.get("conviction") or 0.5),
        params={"adopted": True,
                "provenance": "on-venue position adopted from venue truth",
                "sl": args.get("sl_price"), "tp": args.get("tp_price"),
                "sl_order_id": args.get("sl_order_id"),
                "tp_order_id": args.get("tp_order_id")})
    trade_id = write.record_trade(
        conn, decision_id=decision_id, venue="hyperliquid", symbol=symbol,
        side=side, size=size, fill_price=entry_px,
        venue_order_id="adopted")
    # entry_account_value/leverage stay NULL — the pre-fill flat equity is
    # unknowable after the fact; honest absence beats reconstruction.
    position_id = write.open_position(
        conn, venue="hyperliquid", symbol=symbol, side=side, size=size,
        opening_trade_id=trade_id)

    return tool_result({
        "ok": True, "position_id": position_id, "thesis_id": thesis_id,
        "adopted": {"symbol": symbol, "side": side, "size": size,
                    "entry_px": entry_px,
                    "unrealized_pnl": pp.get("unrealizedPnl")},
        "note": ("books now match the venue; manage via rescore/close as "
                 "normal. Stray triggers beyond the recorded sl/tp ids are "
                 "NOT tracked — cancel them (hl_cancel_order) or they fire "
                 "as reduce-only orders later."),
    })


def _desk_close(args: Dict[str, Any]) -> str:
    from trading.integrations.hyperliquid.venue import hl_close_position
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    # HALT blocks closes too (matching the legacy close_position gate) — with
    # ONE exception: the naked-position abort must ALWAYS run, because an
    # unprotected live position is strictly worse than overriding the pause.
    if args.get("exit_reason") != "naked_position_abort":
        halt = _halt_reason()
        if halt is not None:
            return tool_result({"ok": False,
                                "refused": "HALT is set — operator paused trading",
                                **({"halt_note": halt} if halt else {})})
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
        agent="plutus-main", conviction=0.5,
        params={"exit_reason": args["exit_reason"],
                # True when the venue was already flat (an on-venue SL/TP
                # fired or it was closed out-of-band) and the fill below was
                # recovered from venue history rather than a fresh close.
                **({"venue_already_flat": True}
                   if close.get("already_flat") else {})},
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
        "venue_already_flat": bool(close.get("already_flat")),
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


RESCORE_SCHEMA = {
    "name": "rescore_position",
    "description": (
        "Re-score the OPEN position's conviction on fresh data and get a "
        "recommended action — call this when a position alert wakes you, "
        "passing which alert fired (alert='near' for the near-edge alert-up, "
        "'adverse' for the winners'-MAE alert-down). Re-runs the strategy's "
        "conviction on live readings, records the evaluation, and returns "
        "recommended_action with a BIAS TO ACT: exit_now if conviction has "
        "decayed materially below entry or fallen below the global threshold "
        "(the premise is gone); when the re-score returns NO conviction "
        "(missing data) the premise is unverifiable while risk is open — "
        "take_profit on a near alert, exit_now otherwise; else hold. You make "
        "the final call (take-profit / cut / hold) and close via "
        "desk_close_position if warranted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "position_id": {"type": "integer"},
            "alert": {"type": "string", "enum": ["near", "adverse"],
                      "description": "Which position alert triggered this re-score."},
        },
        "required": ["position_id"],
    },
}

# How far conviction may decay from entry before the alert-review biases to exit.
RESCORE_EXIT_DROP = 0.10


def _opening_conviction(conn, position_id: int):
    row = conn.execute(
        "SELECT d.conviction FROM positions p "
        "JOIN trades t ON t.id = p.opening_trade_id "
        "JOIN decisions d ON d.id = t.decision_id WHERE p.id = ?",
        (position_id,)).fetchone()
    return row["conviction"] if row else None


def _rescore_position(args: Dict[str, Any]) -> str:
    from trading.conviction.engine import GLOBAL_CONVICTION_THRESHOLD
    from trading.dispatchers._helpers import session_id_from_context
    from trading.dispatchers.predict_tools import score_strategy
    from trading.lifecycle import queries, write
    from trading.lifecycle.db import get_db

    conn = get_db()
    position_id = int(args["position_id"])
    pos = queries.open_position(conn)
    if pos is None or pos["id"] != position_id:
        return tool_error(f"position {position_id} is not the open position")
    strat = (pos.get("thesis") or {}).get("strategy_name")
    if not strat:
        return tool_error("position has no strategy — cannot re-score")

    regime = (pos.get("prediction") or {}).get("regime_tag")
    try:
        scored = score_strategy(strat, regime=regime)
    except Exception as exc:
        return tool_error(f"re-score failed: {type(exc).__name__}: {exc}")

    conv = scored.get("conviction")
    entry_conv = _opening_conviction(conn, position_id)
    # Bias to act — the pos#4 over-hold fix: default to exit when the premise has
    # weakened, not to hold. Missing conviction is honest absence WHILE RISK IS
    # OPEN: a premise that can't be re-verified is treated as gone, never held.
    if conv is None:
        if args.get("alert") == "near":
            rec, why = ("take_profit",
                        "re-score returned no conviction (missing data) at the "
                        "near edge — take the profit rather than hold blind")
        else:
            rec, why = ("exit_now",
                        "re-score returned no conviction (missing data) while "
                        "risk is open — honest absence biases to exit, not hold")
    elif conv < GLOBAL_CONVICTION_THRESHOLD:
        rec, why = "exit_now", f"conviction {conv} below threshold {GLOBAL_CONVICTION_THRESHOLD}"
    elif entry_conv is not None and conv <= entry_conv - RESCORE_EXIT_DROP:
        rec, why = "exit_now", f"conviction decayed {entry_conv}→{conv} (≥{RESCORE_EXIT_DROP})"
    else:
        rec, why = "hold", f"conviction {conv} holding (entry {entry_conv})"

    write.record_evaluation(
        conn, position_id=position_id, conviction=conv if conv is not None else 0.0,
        agent="plutus-main",
        thesis_status="weakening" if rec == "exit_now" else "holding",
        recommended_action=rec, rationale_md=why,
        session_name=session_id_from_context())

    return tool_result({
        "ok": True, "position_id": position_id, "conviction": conv,
        "entry_conviction": entry_conv, "recommended_action": rec,
        "rationale": why, "support_scores": scored.get("support_scores"),
    })


registry.register(
    name="desk_open_position",
    toolset="desk-execution",
    schema=OPEN_SCHEMA,
    handler=lambda args, **kw: _desk_open(args),
    description="Open a position funding a prediction (atomic SL bracket, lifecycle chain).",
    emoji="📈",
)

registry.register(
    name="rescore_position",
    toolset="desk-execution",
    schema=RESCORE_SCHEMA,
    handler=lambda args, **kw: _rescore_position(args),
    description="Re-score the open position on fresh data; recommend hold/exit (bias to act).",
    emoji="🔁",
)

registry.register(
    name="desk_close_position",
    toolset="desk-execution",
    schema=CLOSE_SCHEMA,
    handler=lambda args, **kw: _desk_close(args),
    description="Close the open position (brackets cancelled first, outcome computed).",
    emoji="📉",
)

registry.register(
    name="desk_adopt_position",
    toolset="desk-execution",
    schema=ADOPT_SCHEMA,
    handler=lambda args, **kw: _desk_adopt(args),
    description="Adopt an untracked on-venue position into lifecycle.db from venue truth.",
    emoji="🤝",
)
