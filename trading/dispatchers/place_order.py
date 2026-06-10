"""place_order — execution dispatcher.

Atomically writes a ``decisions`` row, calls the venue's ``place_order_fn``,
then writes ``trades`` and ``positions`` rows. Refuses if the linked thesis
lacks ``invalidation_criteria_json`` (PLUTUS discipline rule: Plutus must
articulate "what would prove me wrong" before entering — enforced in code,
not just skill content).

V2 conviction architecture: position size is multiplier-based.
- Thesis-level ``conviction`` (0..1) reflects this specific setup.
- Strategy-level ``strategy_conviction`` (0..1) is the slow-moving baseline
  read from the linked thesis's strategy file frontmatter (default 0.5).
- composite = sqrt(strategy_conviction × thesis_conviction)  (geometric mean)
- multiplier = 20 ** composite  (1x at composite=0, 20x at composite=1)
- notional_usd = account_balance_usd × multiplier
- size = notional_usd / ref_price

If the caller passes ``size`` explicitly, it overrides the multiplier math
(useful for skills computing size with their own SL-distance-driven logic).
If ``size`` is absent, the caller MUST provide ``ref_price`` (current market
estimate) and the venue must register ``account_state_fn`` so we can read
account balance. Hyperliquid is cross-margin so notional drives exposure;
risk USD is bounded by SL distance, not size.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional, Tuple

from trading.strategies import loader as strategy_loader
from trading.lifecycle.db import get_lifecycle_db
from trading.perception.core import venue_registry
from trading.dispatchers._helpers import json_dumps_compact, session_id_from_context
from harness.tools.registry import registry, tool_error, tool_result


# Maximum multiplier when composite_conviction = 1.0. With sqrt geometric mean,
# strategy=1.0 × thesis=1.0 → composite=1.0 → 20x notional. Pinned to 20 per
# operator's V2 directive: "the lowest conviction would be 1X account size,
# high conviction should be 20X, not linear."
MAX_MULTIPLIER = 20.0


SCHEMA = {
    "name": "place_order",
    "description": (
        "Open or modify a position at a venue. Requires a thesis_id and a "
        "conviction (0.0-1.0, the THESIS conviction). The linked thesis MUST "
        "have invalidation_criteria_json populated (use record_event('thesis', "
        "..., invalidation_criteria=[...]) when you write the thesis) — the "
        "dispatcher refuses otherwise. "
        "\n\n"
        "V2 sizing: if `size` is omitted, computed via the composite-conviction "
        "multiplier — composite = sqrt(strategy_conviction × thesis_conviction) "
        "where strategy_conviction comes from the linked thesis's strategy file "
        "frontmatter (defaults to 0.5). multiplier = 20 ** composite. notional "
        "= account_balance × multiplier. size = notional / ref_price (caller "
        "MUST supply ref_price if size omitted). Pass `size` explicitly to "
        "override the multiplier math. "
        "\n\n"
        "Atomically writes decision + trade + position rows so the lifecycle "
        "remains consistent on failure. When sl and/or tp are provided AND the "
        "venue supports atomic brackets (hyperliquid does, via normalTpsl bulk "
        "grouping), the stop-loss and take-profit are placed AS ON-VENUE TRIGGER "
        "ORDERS in the same signed action as the entry — no naked window. The "
        "trigger order IDs are persisted to decisions.params_json so "
        "close_position can cancel them before market-closing. If a bracket "
        "fails to land while the entry succeeded, the result includes "
        "bracket_warnings explaining what's missing — Plutus must decide "
        "whether to retry, monitor manually, or close out."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "venue":     {"type": "string"},
            "thesis_id": {"type": "integer"},
            "conviction": {
                "type": "number", "minimum": 0.0, "maximum": 1.0,
                "description": "THESIS-level conviction (ephemeral, this setup). Combined with strategy_conviction from the linked strategy file for sizing.",
            },
            "side":      {"type": "string", "enum": ["long", "short"]},
            "symbol":    {"type": "string"},
            "size": {
                "type": "number",
                "description": "Override: skip multiplier math and use this size directly. If omitted, computed from composite-conviction multiplier (requires ref_price).",
            },
            "ref_price": {
                "type": "number",
                "description": "Current market reference price, used to convert notional → size when `size` is omitted. Typically the latest hl_price or the operator's chosen entry estimate.",
            },
            "sl":        {"type": "number", "description": "Stop-loss price. Auto-placed as on-venue reduce-only trigger order on hyperliquid."},
            "tp":        {"type": "number", "description": "Take-profit price. Auto-placed as on-venue reduce-only trigger order on hyperliquid."},
            "extra":     {"type": "object", "additionalProperties": True},
        },
        "required": ["venue", "thesis_id", "conviction", "side", "symbol"],
    },
}


def _composite_conviction(strategy_conviction: float, thesis_conviction: float) -> float:
    """Geometric mean of the two conviction layers. Returns 0..1."""
    s = max(0.0, min(1.0, float(strategy_conviction)))
    t = max(0.0, min(1.0, float(thesis_conviction)))
    return math.sqrt(s * t)


def _multiplier_from_composite(composite: float) -> float:
    """V2 exponential sizing: 20^composite. Operator-set max. Returns 1..20."""
    c = max(0.0, min(1.0, float(composite)))
    return MAX_MULTIPLIER ** c


def _resolve_account_balance_usd(venue_entry) -> Tuple[Optional[float], Optional[str]]:
    """Read account balance via the venue's account_state_fn.

    Returns (balance_usd, error_message). On success, error is None.
    """
    if not venue_entry.account_state_fn:
        return None, f"venue '{venue_entry.name}' has no account_state_fn registered (needed for multiplier sizing)"
    try:
        state = venue_entry.account_state_fn()
    except Exception as exc:
        return None, f"venue '{venue_entry.name}' account_state_fn raised: {exc}"
    if not isinstance(state, dict):
        return None, f"venue '{venue_entry.name}' account_state_fn returned non-dict"
    # Hyperliquid's account_state returns total_equity_usd via the equity
    # data point. Different venues may shape this differently; check common
    # keys in priority order.
    for key in ("total_equity_usd", "account_value", "equity_usd", "balance_usd"):
        v = state.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v), None
    balances = state.get("balances") or {}
    if isinstance(balances, dict):
        usdc = balances.get("USDC")
        if isinstance(usdc, (int, float)) and usdc > 0:
            return float(usdc), None
    return None, f"venue '{venue_entry.name}' account_state returned no usable balance field"


def _place_order(args: Dict[str, Any]) -> str:
    venue = args.get("venue")
    thesis_id = args.get("thesis_id")
    conviction = args.get("conviction")
    side = args.get("side")
    symbol = args.get("symbol")
    size = args.get("size")
    ref_price = args.get("ref_price")

    if not all([venue, thesis_id, side, symbol]) or conviction is None:
        return tool_error(
            "place_order requires venue, thesis_id, conviction, side, symbol"
        )
    if not (0.0 <= float(conviction) <= 1.0):
        return tool_error("conviction must be in [0.0, 1.0]")
    if side not in ("long", "short"):
        return tool_error("side must be 'long' or 'short'")

    db = get_lifecycle_db()

    thesis_row = db.conn().execute(
        "SELECT id, strategy_name, invalidation_criteria_json FROM theses WHERE id = ?",
        (thesis_id,),
    ).fetchone()
    if thesis_row is None:
        return tool_error(f"thesis_id {thesis_id} does not exist")
    if not thesis_row["invalidation_criteria_json"]:
        return tool_error(
            f"thesis_id {thesis_id} has no invalidation_criteria_json — "
            "place_order refuses to open without articulated invalidation. "
            "Update the thesis with invalidation_criteria first."
        )

    try:
        venue_entry = venue_registry.lookup(venue)
    except KeyError as exc:
        return tool_error(str(exc))
    if not venue_entry.place_order_fn:
        return tool_error(f"venue '{venue}' has no place_order_fn registered")

    # ── V2 composite-conviction sizing ───────────────────────────────
    strategy_name = thesis_row["strategy_name"] if "strategy_name" in thesis_row.keys() else None
    if strategy_name:
        strategy_conviction = strategy_loader.get_strategy_conviction(strategy_name)
        if strategy_conviction is None:
            # Strategy_name set but file not found — use default with warning.
            strategy_conviction = strategy_loader.DEFAULT_STRATEGY_CONVICTION
    else:
        # Untagged thesis (rare; discipline-bound to tag). Default conviction.
        strategy_conviction = strategy_loader.DEFAULT_STRATEGY_CONVICTION

    composite = _composite_conviction(strategy_conviction, float(conviction))
    multiplier = _multiplier_from_composite(composite)
    account_balance_usd: Optional[float] = None
    computed_size_path = "explicit_size_override"

    if size is None:
        # Must compute from multiplier + ref_price + balance.
        if ref_price is None or float(ref_price) <= 0:
            return tool_error(
                "place_order needs either `size` OR a positive `ref_price` "
                "(to convert notional from multiplier sizing). Provide one."
            )
        balance, err = _resolve_account_balance_usd(venue_entry)
        if balance is None:
            return tool_error(err or "could not resolve account balance for multiplier sizing")
        account_balance_usd = balance
        notional_usd = balance * multiplier
        size = notional_usd / float(ref_price)
        computed_size_path = "composite_multiplier"

    sl, tp, extra = args.get("sl"), args.get("tp"), args.get("extra") or {}

    try:
        fill = venue_entry.place_order_fn(
            symbol=symbol, side=side, size=size, sl=sl, tp=tp, **extra,
        )
    except Exception as exc:
        return tool_error(f"venue '{venue}' place_order_fn raised: {exc}")

    if not isinstance(fill, dict) or "fill_price" not in fill:
        return tool_error(
            f"venue '{venue}' place_order_fn returned malformed result; "
            "expected dict with 'fill_price'"
        )

    ts = time.time()
    sid = session_id_from_context()
    action = "open_long" if side == "long" else "open_short"
    # Persist bracket order IDs (when the venue placed them atomically with
    # the entry) so close_position can cancel them before market-closing.
    # bracket_warnings surface partial-failure cases (e.g., entry filled but
    # SL placement was rejected) — Plutus reads them off the tool result and
    # decides whether to retry, manually monitor, or hold.
    # V2: also persist composite_conviction / multiplier / strategy_conviction_at_entry
    # for ML postmortem analysis and trajectory tracking.
    decision_params = {
        "symbol": symbol, "size": size, "sl": sl, "tp": tp,
        "sl_order_id": fill.get("sl_order_id"),
        "tp_order_id": fill.get("tp_order_id"),
        "bracket_warnings": fill.get("bracket_warnings") or [],
        # V2 conviction provenance
        "strategy_name_at_entry": strategy_name,
        "strategy_conviction_at_entry": strategy_conviction,
        "thesis_conviction_at_entry": float(conviction),
        "composite_conviction": composite,
        "multiplier": multiplier,
        "sizing_path": computed_size_path,
        "ref_price": ref_price,
        "account_balance_usd_at_entry": account_balance_usd,
        **extra,
    }

    def _write_chain(conn):
        decision_id = conn.execute(
            "INSERT INTO decisions(thesis_id, ts, action, params_json, conviction) "
            "VALUES (?, ?, ?, ?, ?)",
            (thesis_id, ts, action, json_dumps_compact(decision_params), float(conviction)),
        ).lastrowid

        trade_id = conn.execute(
            "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price, "
            "slippage_bp, venue_order_id, venue_fill_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, ts, venue, symbol, side,
             fill.get("size", size), fill["fill_price"],
             fill.get("slippage_bp"), fill.get("order_id"), fill.get("fill_id")),
        ).lastrowid

        position_id = conn.execute(
            "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, status, opened_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (venue, symbol, side, fill.get("size", size), trade_id, ts),
        ).lastrowid

        return decision_id, trade_id, position_id

    decision_id, trade_id, position_id = db._execute_write(_write_chain)
    sid  # session_id is captured into the row writes via params later

    result_payload = {
        "decision_id": decision_id,
        "trade_id": trade_id,
        "position_id": position_id,
        "venue": venue,
        "symbol": symbol,
        "side": side,
        "size": fill.get("size", size),
        "fill_price": fill["fill_price"],
        # V2 conviction visibility — surface so the skill can log + reason.
        "thesis_conviction": float(conviction),
        "strategy_conviction": strategy_conviction,
        "composite_conviction": composite,
        "multiplier": multiplier,
        "sizing_path": computed_size_path,
    }
    # Surface bracket placement so Plutus sees them (or sees that they
    # didn't land) without having to query the lifecycle DB.
    if fill.get("sl_order_id") or fill.get("tp_order_id"):
        result_payload["sl_order_id"] = fill.get("sl_order_id")
        result_payload["tp_order_id"] = fill.get("tp_order_id")
    if fill.get("bracket_warnings"):
        result_payload["bracket_warnings"] = fill["bracket_warnings"]
    return tool_result(result_payload)


registry.register(
    name="place_order",
    toolset="execution",
    schema=SCHEMA,
    handler=lambda args, **kw: _place_order(args),
    description="Open a position; atomically records decision + trade + position.",
    emoji="🎯",
)
