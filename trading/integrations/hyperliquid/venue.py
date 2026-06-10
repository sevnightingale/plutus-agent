"""Register the Hyperliquid venue with the venue registry.

Provides ``place_order_fn`` / ``close_position_fn`` /
``modify_order_fn`` / ``cancel_order_fn`` / ``account_state_fn`` /
``outcome_compute_fn`` to the dispatchers.

Wallet key (HL_API_WALLET_KEY) is required for any of the write
functions. Reads (account_state_fn) only need HL_PUBLIC_ADDRESS.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from trading.lifecycle.db import get_lifecycle_db
from trading.perception.core.venue_registry import register_venue, RegistryError

from ._client import (
    get_info,
    get_exchange,
    resolve_account_address,
    HLConfigError,
)
from .outcomes import compute_outcome

logger = logging.getLogger(__name__)


# Default slippage tolerance for market orders, in fraction (0.05 = 5%).
# HL SDK default is conservative; we mirror it but expose for callers.
DEFAULT_MARKET_SLIPPAGE = 0.05

# Slippage tolerance for the worst-acceptable-execution price on isMarket=True
# trigger orders (SL / TP). HL requires limit_px on every order even when
# isMarket=True; for a stop-market we want the protective fill to actually
# happen even if price gapped through the trigger. 5% gives plenty of room
# for reasonably-liquid perps; tighten for thin alts via the stop_slippage
# kwarg if needed.
DEFAULT_TRIGGER_SLIPPAGE = 0.05


def _normalize_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Pluck fill_price / fill_size / order_id / fill_id from HL response."""
    statuses = (
        resp.get("response", {}).get("data", {}).get("statuses", [])
        if isinstance(resp, dict) else []
    )
    if not statuses:
        raise RuntimeError(
            f"Hyperliquid response had no statuses: {resp}"
        )

    status = statuses[0]
    if "filled" in status:
        fill = status["filled"]
        return {
            "fill_price": float(fill["avgPx"]),
            "size": float(fill["totalSz"]),
            "order_id": str(fill.get("oid")) if fill.get("oid") is not None else None,
            "fill_id": None,
            "slippage_bp": None,
            "raw": fill,
        }
    if "resting" in status:
        # Limit order resting on the book — no fill yet. We surface the
        # order ID so the agent can monitor / cancel; downstream
        # dispatcher will refuse because there's no fill_price.
        oid = status["resting"].get("oid")
        raise RuntimeError(
            f"order rested without fill (oid={oid}); "
            "use a market order or supply IoC tif to enforce immediate fill"
        )
    if "error" in status:
        raise RuntimeError(f"Hyperliquid order error: {status['error']}")
    raise RuntimeError(f"Unrecognised Hyperliquid status: {status}")


def _validate_bracket_prices(
    *, side: str, entry_px: float, sl: Optional[float], tp: Optional[float],
) -> None:
    """Refuse SL/TP prices on the wrong side of entry.

    Long: SL must be < entry; TP must be > entry. Short: opposite.
    Catches obvious operator/agent errors (transposed sl/tp, wrong sign,
    stale price reference) before they become open orders.
    """
    if sl is not None:
        if side == "long" and sl >= entry_px:
            raise ValueError(
                f"SL {sl} must be BELOW entry {entry_px} for long position "
                "(stops fire when price moves AGAINST you)."
            )
        if side == "short" and sl <= entry_px:
            raise ValueError(
                f"SL {sl} must be ABOVE entry {entry_px} for short position "
                "(stops fire when price moves AGAINST you)."
            )
    if tp is not None:
        if side == "long" and tp <= entry_px:
            raise ValueError(
                f"TP {tp} must be ABOVE entry {entry_px} for long position "
                "(takes profit when price moves WITH you)."
            )
        if side == "short" and tp >= entry_px:
            raise ValueError(
                f"TP {tp} must be BELOW entry {entry_px} for short position "
                "(takes profit when price moves WITH you)."
            )


def _bracket_limit_px(
    *, trigger_px: float, is_buy_close: bool, slippage: float,
) -> float:
    """Worst-acceptable-execution price for a stop-market trigger order.

    HL requires ``limit_px`` on every order even when ``isMarket=True``.
    For stop-markets we want the fill to happen even if price gapped
    THROUGH the trigger, so we pad in the unfavorable direction by
    ``slippage``. For a long-position SL (sell-side close), that means
    accepting prices BELOW the trigger; for a short-position SL (buy-side
    close), accepting prices ABOVE the trigger.
    """
    if is_buy_close:
        # Closing a short → buying. Accept higher prices than the trigger.
        return trigger_px * (1.0 + slippage)
    # Closing a long → selling. Accept lower prices than the trigger.
    return trigger_px * (1.0 - slippage)


def _round_px_for_hl(px: float) -> float:
    """Mirror HL SDK's price rounding (5 sig figs, max 6 decimal places).

    Without this, sub-$1 alts can hit "Price too many decimals" wire errors
    when we feed in computed slippage-adjusted prices.
    """
    # 5 sig figs
    if px == 0:
        return 0.0
    from math import floor, log10
    digits = 5 - int(floor(log10(abs(px)))) - 1
    rounded = round(px, max(digits, 0))
    # Cap at 6 decimal places (HL wire format constraint)
    return round(rounded, 6)


def _normalize_bulk_bracket_response(
    resp: Dict[str, Any], *, has_sl: bool, has_tp: bool,
) -> Dict[str, Any]:
    """Parse a bulk_orders response with [entry, tp?, sl?] order indices.

    Order layout (must match what we submitted in hl_place_order):
        index 0: entry
        index 1: TP trigger (if has_tp)
        index 1 or 2: SL trigger (if has_sl)

    Entry MUST be filled (we use IoC). Brackets MAY rest on the book
    (their triggerPx is in the future), so we accept "resting" status for
    them and capture the order ID. If a bracket failed, we surface a
    warning so the dispatcher / Plutus can decide whether to compensate.
    """
    statuses = (
        resp.get("response", {}).get("data", {}).get("statuses", [])
        if isinstance(resp, dict) else []
    )
    if not statuses:
        raise RuntimeError(
            f"Hyperliquid bracket response had no statuses: {resp}"
        )

    # Entry: must be filled.
    entry_status = statuses[0]
    if "filled" in entry_status:
        fill = entry_status["filled"]
        normalized: Dict[str, Any] = {
            "fill_price": float(fill["avgPx"]),
            "size": float(fill["totalSz"]),
            "order_id": str(fill.get("oid")) if fill.get("oid") is not None else None,
            "fill_id": None,
            "slippage_bp": None,
            "raw": fill,
        }
    elif "resting" in entry_status:
        oid = entry_status["resting"].get("oid")
        raise RuntimeError(
            f"entry order rested without fill (oid={oid}); use a market "
            "order or supply IoC tif to enforce immediate fill"
        )
    elif "error" in entry_status:
        raise RuntimeError(f"Hyperliquid entry order error: {entry_status['error']}")
    else:
        raise RuntimeError(f"Unrecognised entry status: {entry_status}")

    # Brackets in the order they were submitted: TP first, then SL.
    bracket_warnings: List[str] = []
    next_idx = 1
    if has_tp:
        normalized["tp_order_id"], _tp_warn = _extract_bracket_status(
            statuses, next_idx, "TP",
        )
        if _tp_warn:
            bracket_warnings.append(_tp_warn)
        next_idx += 1
    else:
        normalized["tp_order_id"] = None
    if has_sl:
        normalized["sl_order_id"], _sl_warn = _extract_bracket_status(
            statuses, next_idx, "SL",
        )
        if _sl_warn:
            bracket_warnings.append(_sl_warn)
    else:
        normalized["sl_order_id"] = None

    normalized["bracket_warnings"] = bracket_warnings
    return normalized


def _extract_bracket_status(
    statuses: list, idx: int, label: str,
) -> tuple[Optional[str], Optional[str]]:
    """Pull the bracket order ID out of a multi-status response slot.

    Returns (order_id, warning_message). order_id is None when the bracket
    failed to land. warning_message is None on success.
    """
    if idx >= len(statuses):
        return None, (
            f"{label}: response truncated (expected status at index {idx}, "
            f"got {len(statuses)} statuses)"
        )
    status = statuses[idx]
    if "resting" in status:
        oid = status["resting"].get("oid")
        return (str(oid) if oid is not None else None), None
    if "filled" in status:
        # Trigger orders shouldn't fill at submission time — would mean the
        # trigger was already in the money. Capture the order ID anyway.
        oid = status["filled"].get("oid")
        return (
            str(oid) if oid is not None else None,
            f"{label}: trigger fired immediately at submission (price already "
            "past trigger). Position may already be partially closed.",
        )
    if "error" in status:
        return None, f"{label}: {status['error']}"
    return None, f"{label}: unrecognised status: {status}"


def hl_place_order(
    *,
    symbol: str,
    side: str,
    size: float,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    order_type: str = "market",
    limit_px: Optional[float] = None,
    reduce_only: bool = False,
    tif: str = "Ioc",
    slippage: float = DEFAULT_MARKET_SLIPPAGE,
    stop_slippage: float = DEFAULT_TRIGGER_SLIPPAGE,
    **_extra: Any,
) -> Dict[str, Any]:
    """Open a perp position on Hyperliquid, optionally with bracket SL/TP.

    Returns a normalised dict the dispatcher writes into trades + decisions:
        {
          fill_price, size, order_id, fill_id, slippage_bp,
          sl_order_id, tp_order_id,           # None when not requested
          bracket_warnings: List[str],        # [] when all-or-none success
        }

    Bracket placement is **atomic** when sl or tp is provided: entry +
    trigger orders submit together via ``bulk_orders(grouping="normalTpsl")``
    so there's no naked window between fill and protection. Plutus's
    discipline skills can later modify brackets via cancel + replace.

    Constraints:
        - Bracket auto-placement is only wired for ``order_type="market"``.
          For ``order_type="limit"`` the bracket request is silently
          accepted and a warning surfaces in ``bracket_warnings`` —
          Plutus must place the brackets manually after the limit fills.
        - ``stop_slippage`` controls the worst-acceptable-execution price
          for stop-market triggers (default 5%). Tighten for thin alts.
    """
    ex = get_exchange()
    is_buy = (side == "long")
    sz = float(size)
    has_brackets = sl is not None or tp is not None

    # Limit-entry + brackets: surface a warning, place entry only.
    # Atomic bracketing requires an immediately-filling entry so the
    # trigger orders have a position to reduce against. Limit-with-IoC
    # could theoretically work but resting limits + bracket lifecycle
    # is a different problem — defer until Plutus actually wants it.
    if order_type == "limit" and has_brackets:
        if limit_px is None:
            raise ValueError("limit_px required for limit orders")
        order_type_payload = {"limit": {"tif": tif}}
        resp = ex.order(
            name=symbol, is_buy=is_buy, sz=sz, limit_px=float(limit_px),
            order_type=order_type_payload, reduce_only=reduce_only,
        )
        normalized = _normalize_response(resp)
        normalized["sl_order_id"] = None
        normalized["tp_order_id"] = None
        normalized["bracket_warnings"] = [
            "limit-entry brackets are not auto-placed; if/when this entry "
            "fills, place reduce-only trigger orders separately via "
            "place_order(reduce_only=True, ...) or as a follow-up."
        ]
        return normalized

    if order_type == "limit":
        if limit_px is None:
            raise ValueError("limit_px required for limit orders")
        order_type_payload = {"limit": {"tif": tif}}
        resp = ex.order(
            name=symbol, is_buy=is_buy, sz=sz, limit_px=float(limit_px),
            order_type=order_type_payload, reduce_only=reduce_only,
        )
        return _normalize_response(resp)

    if order_type != "market":
        raise ValueError(f"unknown order_type '{order_type}'")

    # Market entry, no brackets: keep the lean original path.
    if not has_brackets:
        resp = ex.market_open(
            name=symbol, is_buy=is_buy, sz=sz, slippage=slippage,
        )
        return _normalize_response(resp)

    # Market entry WITH brackets: atomic bulk via normalTpsl grouping.
    entry_px = ex._slippage_price(symbol, is_buy, slippage, None)
    _validate_bracket_prices(side=side, entry_px=entry_px, sl=sl, tp=tp)

    is_close_buy = not is_buy  # bracket orders close the position

    orders: List[Dict[str, Any]] = [
        {
            "coin": symbol,
            "is_buy": is_buy,
            "sz": sz,
            "limit_px": entry_px,
            "order_type": {"limit": {"tif": "Ioc"}},
            "reduce_only": False,
        }
    ]
    if tp is not None:
        tp_trigger = _round_px_for_hl(float(tp))
        tp_limit = _round_px_for_hl(
            _bracket_limit_px(
                trigger_px=tp_trigger, is_buy_close=is_close_buy,
                slippage=stop_slippage,
            )
        )
        orders.append({
            "coin": symbol,
            "is_buy": is_close_buy,
            "sz": sz,
            "limit_px": tp_limit,
            "order_type": {
                "trigger": {
                    "isMarket": True, "triggerPx": tp_trigger, "tpsl": "tp",
                }
            },
            "reduce_only": True,
        })
    if sl is not None:
        sl_trigger = _round_px_for_hl(float(sl))
        sl_limit = _round_px_for_hl(
            _bracket_limit_px(
                trigger_px=sl_trigger, is_buy_close=is_close_buy,
                slippage=stop_slippage,
            )
        )
        orders.append({
            "coin": symbol,
            "is_buy": is_close_buy,
            "sz": sz,
            "limit_px": sl_limit,
            "order_type": {
                "trigger": {
                    "isMarket": True, "triggerPx": sl_trigger, "tpsl": "sl",
                }
            },
            "reduce_only": True,
        })

    resp = ex.bulk_orders(orders, grouping="normalTpsl")
    return _normalize_bulk_bracket_response(
        resp, has_sl=(sl is not None), has_tp=(tp is not None),
    )


def _lookup_bracket_order_ids(position_id: int) -> Dict[str, Optional[str]]:
    """Read SL/TP venue order IDs from the opening decision's params_json.

    Returns ``{"sl": "<oid>" or None, "tp": "<oid>" or None}``. Empty when
    the position was opened without brackets or before bracket support.
    """
    db = get_lifecycle_db()
    row = db.conn().execute(
        "SELECT d.params_json FROM positions p "
        "JOIN trades t ON t.id = p.opening_trade_id "
        "JOIN decisions d ON d.id = t.decision_id "
        "WHERE p.id = ?",
        (position_id,),
    ).fetchone()
    if row is None or not row["params_json"]:
        return {"sl": None, "tp": None}
    try:
        params = json.loads(row["params_json"])
    except Exception:
        return {"sl": None, "tp": None}
    return {
        "sl": params.get("sl_order_id") or None,
        "tp": params.get("tp_order_id") or None,
    }


def _cancel_tracked_brackets(
    *, symbol: str, position_id: int,
) -> List[str]:
    """Cancel any SL/TP trigger orders tracked for this position.

    Called before market_close so the bracket can't fire mid-close. Failures
    are non-fatal (the bracket may already have been filled, cancelled
    out-of-band, or never landed) — we collect them as warnings and let the
    close proceed.
    """
    ex = get_exchange()
    warnings: List[str] = []
    ids = _lookup_bracket_order_ids(position_id)
    for label in ("sl", "tp"):
        oid = ids.get(label)
        if not oid:
            continue
        try:
            resp = ex.cancel(symbol, int(oid))
        except Exception as exc:
            warnings.append(
                f"{label.upper()} cancel oid={oid} raised: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        # HL cancel response shape: {"status": "ok", "response": {...}} on
        # success; an "error" embedded somewhere otherwise. We tolerate
        # "Order was never placed, already canceled, or filled" as benign.
        status = (
            resp.get("response", {}).get("data", {}).get("statuses", [])
            if isinstance(resp, dict) else []
        )
        for s in status:
            if isinstance(s, dict) and "error" in s:
                msg = str(s["error"]).lower()
                if any(p in msg for p in (
                    "never placed", "already canceled", "already cancelled",
                    "already filled",
                )):
                    continue  # benign — bracket already gone
                warnings.append(f"{label.upper()} cancel oid={oid}: {s['error']}")
    return warnings


def hl_close_position(
    *,
    symbol: str,
    position_id: int,
    slippage: float = DEFAULT_MARKET_SLIPPAGE,
    **_extra: Any,
) -> Dict[str, Any]:
    """Market-close the live HL position for ``symbol``.

    We trust the on-venue position state as truth (size may have drifted
    via partial fills, liquidation, or off-band actions). ``market_close``
    looks up the current size and routes a reducing market order.

    Cancels any tracked SL/TP trigger orders FIRST so the bracket can't
    fire mid-close (would race the market_close, leading to double-close
    rejections or worse — a flip-position on shorts). Cancel failures are
    non-fatal: surfaced via ``cancel_warnings`` for the caller to log.
    """
    cancel_warnings = _cancel_tracked_brackets(
        symbol=symbol, position_id=position_id,
    )
    ex = get_exchange()
    resp = ex.market_close(coin=symbol, slippage=slippage)
    normalized = _normalize_response(resp)
    if cancel_warnings:
        normalized["cancel_warnings"] = cancel_warnings
    return normalized


def hl_place_trigger(
    *,
    symbol: str,
    position_side: str,
    size: float,
    trigger_px: float,
    kind: str = "sl",
    slippage: float = DEFAULT_TRIGGER_SLIPPAGE,
    **_extra: Any,
) -> Dict[str, Any]:
    """Place a standalone reduce-only trigger (SL or TP) on an existing position.

    Use this when:
      * The atomic-bracket path in ``hl_place_order`` failed for one leg
        (e.g. ``_slippage_price`` inflated the entry estimate and the TP
        validation rejected the bracket request).
      * A position was opened without sl/tp and now needs protection added
        post-hoc.
      * Path B in the hl-risk-placement skill needs a true trigger order
        (``hl_place_order`` standalone only supports market/limit, not
        trigger).

    Args:
      symbol:        Coin symbol (e.g. "BTC", "ETH", "HYPE").
      position_side: "long" or "short" — the side of the position you're
                     protecting. The trigger order's ``is_buy`` is the
                     opposite (close direction).
      size:          Position size in coin units (matches the open
                     position's |szi|).
      trigger_px:    The trigger price. For long-SL, below market; for
                     long-TP, above market; mirror for short.
      kind:          "sl" or "tp". Sets the ``tpsl`` field for HL's
                     trigger-order classification.
      slippage:      Worst-acceptable-execution padding for the
                     ``limit_px`` companion field. Default 5% — matches
                     the bracket path. Trigger fires at ``trigger_px``;
                     fill happens within ``slippage`` of that.

    Returns:
      The normalized HL bulk_orders response. Caller is responsible for
      recording the resulting order id against the position so the
      bracket-cancel pass on close can clean it up.
    """
    if kind not in ("sl", "tp"):
        raise ValueError(f"kind must be 'sl' or 'tp', got {kind!r}")
    if position_side not in ("long", "short"):
        raise ValueError(
            f"position_side must be 'long' or 'short', got {position_side!r}"
        )

    is_close_buy = (position_side == "short")
    trigger_rounded = _round_px_for_hl(float(trigger_px))
    limit_rounded = _round_px_for_hl(
        _bracket_limit_px(
            trigger_px=trigger_rounded,
            is_buy_close=is_close_buy,
            slippage=slippage,
        )
    )

    order = {
        "coin": symbol,
        "is_buy": is_close_buy,
        "sz": float(size),
        "limit_px": limit_rounded,
        "order_type": {
            "trigger": {
                "isMarket": True,
                "triggerPx": trigger_rounded,
                "tpsl": kind,
            }
        },
        "reduce_only": True,
    }

    ex = get_exchange()
    resp = ex.bulk_orders([order], grouping="normalTpsl")
    normalized = _normalize_response(resp)
    normalized["trigger_px"] = trigger_rounded
    normalized["limit_px"] = limit_rounded
    normalized["kind"] = kind
    normalized["position_side"] = position_side
    return normalized


def hl_modify_order(
    *,
    venue_order_id: int,
    new_limit_px: float,
    new_size: Optional[float] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    tif: str = "Gtc",
    reduce_only: bool = False,
    **_extra: Any,
) -> Dict[str, Any]:
    """Modify a resting limit order's price / size."""
    ex = get_exchange()
    if symbol is None or side is None:
        raise ValueError("modify_order requires symbol + side to rebuild the order payload")
    is_buy = (side == "long")
    order_payload = {
        "coin": symbol,
        "is_buy": is_buy,
        "sz": float(new_size) if new_size is not None else None,
        "limit_px": float(new_limit_px),
        "order_type": {"limit": {"tif": tif}},
        "reduce_only": reduce_only,
    }
    if order_payload["sz"] is None:
        raise ValueError("modify_order requires new_size")
    resp = ex.modify_order(int(venue_order_id), order_payload)
    return {"raw": resp, "venue_order_id": venue_order_id}


def hl_cancel_order(
    *,
    venue_order_id: int,
    symbol: str,
    **_extra: Any,
) -> Dict[str, Any]:
    """Cancel a resting limit order."""
    ex = get_exchange()
    resp = ex.cancel(symbol, int(venue_order_id))
    return {"raw": resp, "venue_order_id": venue_order_id}


def hl_account_state(account_name: str = "hl_trading", **_extra: Any) -> Dict[str, Any]:
    """Composite state: equity + positions + open orders + drawdown.

    Equity reuses ``hl_total_equity`` so spot USDC is included (HL's
    ``user_state.marginSummary.accountValue`` only sees margin-allocated
    funds; with unified mode operators usually keep most balance in spot).
    """
    info = get_info()
    addr = resolve_account_address(account_name)
    state = info.user_state(addr)
    open_orders = info.frontend_open_orders(addr)

    from .data_points import hl_drawdown_from_peak, hl_total_equity
    try:
        equity = hl_total_equity(account_name)
    except HLConfigError:
        equity = {"equity_usd": 0.0, "spot_usdc": 0.0,
                  "perp_account_value": 0.0, "withdrawable_usd": 0.0}
    try:
        drawdown = hl_drawdown_from_peak(account_name)
    except HLConfigError:
        drawdown = None

    return {
        "account_name": account_name,
        "address": addr,
        "equity_usd": equity["equity_usd"],
        "spot_usdc": equity["spot_usdc"],
        "perp_account_value": equity["perp_account_value"],
        "withdrawable_usd": equity["withdrawable_usd"],
        "open_perp_positions": [
            ap.get("position") for ap in state.get("assetPositions", [])
            if float((ap.get("position") or {}).get("szi", 0)) != 0
        ],
        "open_orders": open_orders,
        "drawdown": drawdown,
    }


# ─── Registration ─────────────────────────────────────────────────────────

try:
    register_venue(
        name="hyperliquid",
        description=(
            "Hyperliquid perpetuals venue. Read-only methods work without "
            "a wallet key; place_order / close_position / modify_order / "
            "cancel_order require HL_API_WALLET_KEY in ~/.plutus-agent/.env."
        ),
        place_order_fn=hl_place_order,
        close_position_fn=hl_close_position,
        modify_order_fn=hl_modify_order,
        cancel_order_fn=hl_cancel_order,
        account_state_fn=hl_account_state,
        place_trigger_fn=hl_place_trigger,
        outcome_compute_fn=compute_outcome,
    )
except RegistryError:
    logger.debug("hyperliquid venue already registered")
