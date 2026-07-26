"""Register the Hyperliquid venue with the venue registry.

Provides ``place_order_fn`` / ``close_position_fn`` /
``modify_order_fn`` / ``cancel_order_fn`` / ``account_state_fn`` /
``outcome_compute_fn`` to the dispatchers.

Wallet key (HL_API_WALLET_KEY) is required for any of the write
functions. Reads (account_state_fn) only need ACP_AGENT_WALLET.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

from trading.lifecycle.db import get_db
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


def _response_statuses(resp: Any) -> List[Any]:
    """Unwrap the exchange response envelope to its statuses list.

    Fails loudly with the venue's own words on action-level rejections,
    where HL returns HTTP 200 with ``{"status": "err", "response": "<string>"}``
    (expired/unregistered agent wallet, bad nonce, ...). Dict-indexing that
    string shape used to surface as a bare AttributeError that buried the
    actual venue error text.
    """
    if not isinstance(resp, dict):
        raise RuntimeError(f"Hyperliquid response had no statuses: {resp}")
    payload = resp.get("response")
    if resp.get("status") not in (None, "ok") or not isinstance(payload, dict):
        raise RuntimeError(
            f"Hyperliquid rejected the action: {payload if payload is not None else resp}"
        )
    return payload.get("data", {}).get("statuses", [])


def _normalize_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Pluck fill_price / fill_size / order_id / fill_id from HL response."""
    statuses = _response_statuses(resp)
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


def _round_sz_for_hl(symbol: str, sz: float) -> float:
    """Floor size to the asset's szDecimals (from the SDK's cached meta).

    The SDK's float_to_wire REJECTS (never rounds) a size finer than the
    asset allows — a raw risk-derived size like 0.00025113643744465553 BTC
    kills the order at the wire. Floor, never round-half-up: rounding up
    would breach the risk budget the size was derived from.
    """
    info = get_info()
    decimals = info.asset_to_sz_decimals[info.coin_to_asset[info.name_to_coin[symbol]]]
    step = 10 ** decimals
    # +1e-9 absorbs binary-float artifacts (0.29*100 == 28.999...996) without
    # letting any real sub-step excess round up.
    floored = math.floor(float(sz) * step + 1e-9) / step
    if floored <= 0:
        raise ValueError(
            f"size {sz} floors to 0 at {symbol}'s szDecimals={decimals} — "
            "position too small for this asset's size granularity")
    return floored


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
    statuses = _response_statuses(resp)
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
        try:
            normalized["tp_order_id"], _tp_warn = _extract_bracket_status(
                statuses, next_idx, "TP",
            )
        except Exception as e:
            normalized["tp_order_id"] = None
            _tp_warn = f"TP extraction crashed: {e}"
        if _tp_warn:
            bracket_warnings.append(_tp_warn)
        next_idx += 1
    else:
        normalized["tp_order_id"] = None
    if has_sl:
        try:
            normalized["sl_order_id"], _sl_warn = _extract_bracket_status(
                statuses, next_idx, "SL",
            )
        except Exception as e:
            normalized["sl_order_id"] = None
            _sl_warn = f"SL extraction crashed: {e}"
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

    Returns (order_id, warning_message). warning_message is None when the
    bracket landed. order_id may ALSO be None on success: trigger orders that
    land untriggered are reported as the BARE STRING "waitingForTrigger"
    (observed live 2026-07-03), with no oid in the response — the caller must
    verify the stop on-venue instead of relying on the id.
    """
    if idx >= len(statuses):
        return None, (
            f"{label}: response truncated (expected status at index {idx}, "
            f"got {len(statuses)} statuses)"
        )
    status = statuses[idx]
    if isinstance(status, str):
        if status in ("waitingForTrigger", "resting"):
            return None, None
        return None, f"{label}: unrecognised status: {status}"
    if "resting" in status or "waitingForTrigger" in status:
        val = status.get("resting") if "resting" in status else status.get("waitingForTrigger")
        oid = val.get("oid") if isinstance(val, dict) else val
        return (str(oid) if oid else None), None
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
    # Unknown status shapes MUST fail loudly (order_id None → the caller's
    # naked-position guard aborts). Never guess an oid out of the blob: a
    # fabricated id reads as "SL rests on-venue" and defeats the guard.
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
    sz = _round_sz_for_hl(symbol, float(size))
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
    row = get_db().execute(
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
        # Cancel problems are warnings BY DESIGN (the close must proceed) —
        # so even an action-level rejection becomes a warning here, never an
        # exception that blocks market_close.
        try:
            status = _response_statuses(resp)
        except RuntimeError as exc:
            warnings.append(f"{label.upper()} cancel oid={oid}: {exc}")
            continue
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
    if resp is None:
        # The SDK returns None when the venue has no position for this coin —
        # already flat: an on-venue SL/TP fired, or it was closed out-of-band.
        # Recover the actual closing fill from venue history so the books
        # settle on real numbers; raise if none can be found (no fabrication).
        normalized = _recover_flat_close(symbol)
    else:
        normalized = _normalize_response(resp)
    if cancel_warnings:
        normalized["cancel_warnings"] = cancel_warnings
    return normalized


def _recover_flat_close(symbol: str) -> Dict[str, Any]:
    """Settle a close when the venue is already flat (bracket fired or closed
    out-of-band): pull the most recent fill for the coin from venue history.

    Without this, every on-venue SL/TP fire left the lifecycle.db position
    permanently open — and the one-position law then deadlocked the desk.
    """
    info = get_info()
    addr = resolve_account_address("hl_trading")
    fills = [f for f in (info.user_fills(addr) or []) if f.get("coin") == symbol]
    if not fills:
        raise RuntimeError(
            f"venue reports no open {symbol} position and no {symbol} fills "
            "in recent history — cannot settle the close; reconcile manually"
        )
    last = max(fills, key=lambda f: f.get("time", 0))
    return {
        "already_flat": True,
        "fill_price": float(last["px"]),
        "size": float(last["sz"]),
        "order_id": str(last.get("oid")) if last.get("oid") is not None else None,
        "fill_id": str(last.get("tid")) if last.get("tid") is not None else None,
        "slippage_bp": None,
        "closed_pnl": last.get("closedPnl"),
        "fill_time_ms": last.get("time"),
    }


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
        "sz": _round_sz_for_hl(symbol, float(size)),
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
    # grouping "na": this is a standalone order, not a TP/SL pair attached to
    # a parent entry (normalTpsl semantics).
    resp = ex.bulk_orders([order], grouping="na")
    # A successfully-placed trigger comes back as "waitingForTrigger" (often
    # a bare string, no oid) — _normalize_response treats anything unfilled
    # as failure, which reported every SUCCESSFUL placement as an error.
    statuses = _response_statuses(resp)
    oid, warn = _extract_bracket_status(statuses, 0, kind.upper())
    if warn and "trigger fired immediately" not in warn:
        raise RuntimeError(f"trigger placement failed — {warn}")
    normalized: Dict[str, Any] = {
        "order_id": oid,
        "resting": warn is None,
        "warning": warn,
        "trigger_px": trigger_rounded,
        "limit_px": limit_rounded,
        "kind": kind,
        "position_side": position_side,
    }
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
    """Modify a resting LIMIT order's price / size.

    Trigger orders (SL/TP) are NOT supported here — this call rebuilds the
    order as a plain limit, which would silently strip the trigger semantics
    and the reduce-only flag off a stop. Cancel + re-place via
    ``hl_place_trigger`` instead.
    """
    ex = get_exchange()
    if symbol is None or side is None:
        raise ValueError("modify_order requires symbol + side to rebuild the order payload")
    if new_size is None:
        raise ValueError("modify_order requires new_size")
    is_buy = (side == "long")
    resp = ex.modify_order(
        int(venue_order_id),
        symbol,
        is_buy,
        float(new_size),
        float(new_limit_px),
        {"limit": {"tif": tif}},
        reduce_only,
    )
    statuses = _response_statuses(resp)
    for s in statuses:
        if isinstance(s, dict) and "error" in s:
            raise RuntimeError(f"modify failed: {s['error']}")
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

    Measures (TRADING.md money glossary): ``equity_usd`` reuses
    ``hl_total_equity`` = spot USDC + perp ``accountValue`` — the whole
    unified cross-margin account, and the desk's sizing base. The split
    is reported so the display semantics stay legible: ``spot_usdc`` is
    where idle funds show, ``perp_account_value`` ≈ 0 when flat (normal —
    margin is drawn from the unified balance only while positions are
    open), ``withdrawable_usd`` is what could leave the venue right now.
    """
    info = get_info()
    addr = resolve_account_address(account_name)
    state = info.user_state(addr)
    open_orders = info.frontend_open_orders(addr)

    from .data_points import hl_drawdown_from_peak, hl_total_equity
    # HLConfigError (missing credentials) propagates — a fabricated $0
    # equity reads as a real broke account everywhere downstream.
    equity = hl_total_equity(account_name)
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


def hl_capital_ledger(account_name: str = "hl_trading",
                      start_ms: int = 0,
                      **_extra: Any) -> List[Dict[str, Any]]:
    """Deposits, withdrawals and transfers for the account — the venue's own
    record of money entering and leaving.

    This is the source of truth for capital movements. The desk cannot derive
    them from its own trading records: equity moves for two unrelated reasons
    (PnL and funding events), and without the venue's ledger there is no way
    to tell "up $50 because it traded well" from "up $50 because the operator
    topped it up". lifecycle.db's ``capital_movements`` table existed from the
    beginning for exactly this and had no writer, so every P&L figure the desk
    could state was gross of unknown deposits.

    Normalised to one row per movement, oldest first. ``tx_hash`` is the
    venue's own hash and is what makes reconciliation idempotent. Funding
    payments are deliberately excluded by the endpoint — they are trading
    outcomes, not capital events.
    """
    info = get_info()
    addr = resolve_account_address(account_name).lower()
    raw = info.user_non_funding_ledger_updates(addr, start_ms) or []

    movements: List[Dict[str, Any]] = []
    for row in raw:
        delta = row.get("delta") or {}
        kind = delta.get("type")
        token = delta.get("token") or "USDC"
        amount = delta.get("amount")
        if amount is None:
            amount = delta.get("usdc")
        if amount is None:
            # Honest absence: an unparseable row is reported, never guessed at
            # and never silently dropped from a financial record.
            logger.warning("hl_capital_ledger: unparseable delta %s", delta)
            movements.append({
                "ts": (row.get("time") or 0) / 1000.0,
                "tx_hash": row.get("hash"),
                "movement_type": kind or "unknown",
                "token": token,
                "amount_token": None,
                "amount_usd_at_time": None,
                "from_account": delta.get("user"),
                "to_account": delta.get("destination"),
                "note": f"UNPARSED: {json.dumps(delta)[:400]}",
            })
            continue

        amount_f = float(amount)
        dest = (delta.get("destination") or "").lower()
        src = (delta.get("user") or "").lower()
        # Sign from the account's point of view: money arriving is positive.
        # A `send` where we are the destination is a deposit; where we are the
        # sender, a withdrawal.
        if kind == "send" and src == addr and dest != addr:
            amount_f = -abs(amount_f)
        elif kind in ("withdraw", "accountClassTransfer") and dest != addr:
            amount_f = -abs(amount_f)

        usd = delta.get("usdcValue")
        movements.append({
            "ts": (row.get("time") or 0) / 1000.0,
            "tx_hash": row.get("hash"),
            "movement_type": kind or "unknown",
            "token": token,
            "amount_token": amount_f,
            "amount_usd_at_time": (
                math.copysign(float(usd), amount_f) if usd is not None else None),
            "from_account": delta.get("user"),
            "to_account": delta.get("destination"),
            "note": None,
        })

    movements.sort(key=lambda m: m["ts"])
    return movements


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
