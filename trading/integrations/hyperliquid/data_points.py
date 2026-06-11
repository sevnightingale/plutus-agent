"""Hyperliquid data points — nine read-only registrations.

Symbol-level data points (``hl_price``, ``hl_candles``, ``hl_orderbook``,
``hl_funding_and_oi``, ``hl_universe``) hit public endpoints and need no
credentials. Account-state data points (``hl_holdings``,
``hl_total_equity``, ``hl_drawdown_from_peak``, ``hl_trade_readiness``)
require the wallet env vars; they loud-fail when ``ACP_AGENT_WALLET``
isn't set.

``hl_drawdown_from_peak`` is a *derived* data point: it reads from
``data_point_snapshots`` rather than fetching from HL. Plutus gets the
peak-to-trough drawdown of its equity curve in a single call instead of
having to hand-roll the SQL.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from trading.perception.core.data_point_registry import register_data_point

from ._client import (
    get_info,
    interval_to_ms,
    resolve_account_address,
    HLConfigError,
)

logger = logging.getLogger(__name__)


# ─── Symbol-level data points (no credentials needed) ─────────────────────


@register_data_point(
    name="hl_price",
    category="market",
    source="hyperliquid",
    description="Latest mark price for a Hyperliquid perp symbol (e.g. BTC, ETH).",
    params_schema={"symbol": {"type": "string", "required": True}},
    returns_schema={"symbol": "string", "price": "float", "ts": "iso8601"},
    tags=["market", "price", "hyperliquid"],
)
def hl_price(symbol: str) -> Dict[str, Any]:
    info = get_info()
    mids = info.all_mids()
    if symbol not in mids:
        raise KeyError(
            f"symbol '{symbol}' not in Hyperliquid universe "
            f"(call hl_universe to list)"
        )
    return {"symbol": symbol, "price": float(mids[symbol]), "ts_ms": int(time.time() * 1000)}


@register_data_point(
    name="hl_candles",
    category="market",
    source="hyperliquid",
    description=(
        "OHLCV candles for a Hyperliquid perp symbol over a lookback window. "
        "Intervals: 1m,3m,5m,15m,30m,1h,2h,4h,8h,12h,1d,3d,1w."
    ),
    params_schema={
        "symbol":         {"type": "string", "required": True},
        "interval":       {"type": "string", "required": True},
        "lookback_bars":  {"type": "integer", "default": 200},
    },
    returns_schema={"candles": "list of {t,o,h,l,c,v}"},
    tags=["market", "ohlcv", "hyperliquid"],
)
def hl_candles(symbol: str, interval: str, lookback_bars: int = 200) -> Dict[str, Any]:
    info = get_info()
    bar_ms = interval_to_ms(interval)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - bar_ms * max(1, int(lookback_bars))
    raw = info.candles_snapshot(symbol, interval, start_ms, end_ms)
    candles = [
        {
            "t": int(c["t"]),
            "o": float(c["o"]),
            "h": float(c["h"]),
            "l": float(c["l"]),
            "c": float(c["c"]),
            "v": float(c["v"]),
        }
        for c in raw
    ]
    return {"symbol": symbol, "interval": interval, "count": len(candles), "candles": candles}


@register_data_point(
    name="hl_orderbook",
    category="market",
    source="hyperliquid",
    description="L2 orderbook snapshot (bid/ask levels) for a Hyperliquid perp.",
    params_schema={
        "symbol": {"type": "string", "required": True},
        "depth":  {"type": "integer", "default": 10},
    },
    returns_schema={"bids": "list", "asks": "list", "ts_ms": "int"},
    tags=["market", "orderbook", "hyperliquid"],
)
def hl_orderbook(symbol: str, depth: int = 10) -> Dict[str, Any]:
    info = get_info()
    book = info.l2_snapshot(symbol)
    levels = book.get("levels", [[], []])
    bids = [
        {"px": float(l["px"]), "sz": float(l["sz"]), "n": int(l["n"])}
        for l in levels[0][:depth]
    ]
    asks = [
        {"px": float(l["px"]), "sz": float(l["sz"]), "n": int(l["n"])}
        for l in levels[1][:depth]
    ]
    return {
        "symbol": symbol,
        "ts_ms": int(book.get("time", time.time() * 1000)),
        "bids": bids,
        "asks": asks,
    }


@register_data_point(
    name="hl_funding_and_oi",
    category="market",
    source="hyperliquid",
    description=(
        "Current funding rate, premium, mark price, and open interest for a "
        "Hyperliquid perp. One round-trip via meta_and_asset_ctxs()."
    ),
    params_schema={"symbol": {"type": "string", "required": True}},
    returns_schema={
        "symbol": "string", "funding": "float", "premium": "float",
        "mark_px": "float", "open_interest": "float",
    },
    tags=["market", "funding", "open_interest", "hyperliquid"],
)
def hl_funding_and_oi(symbol: str) -> Dict[str, Any]:
    info = get_info()
    meta, ctxs = info.meta_and_asset_ctxs()
    universe = meta.get("universe", [])
    for asset, ctx in zip(universe, ctxs):
        if asset.get("name") == symbol:
            return {
                "symbol": symbol,
                "funding": float(ctx.get("funding", 0.0)),
                "premium": float(ctx.get("premium", 0.0)) if ctx.get("premium") is not None else None,
                "mark_px": float(ctx.get("markPx", 0.0)) if ctx.get("markPx") is not None else None,
                "open_interest": float(ctx.get("openInterest", 0.0)),
            }
    raise KeyError(
        f"symbol '{symbol}' not in Hyperliquid universe "
        f"(call hl_universe to list)"
    )


@register_data_point(
    name="hl_universe",
    category="market",
    source="hyperliquid",
    description="List Hyperliquid perp markets (symbol + size decimals + leverage).",
    params_schema={},
    returns_schema={"universe": "list of {name, szDecimals, maxLeverage, onlyIsolated, isDelisted}"},
    tags=["market", "universe", "hyperliquid"],
)
def hl_universe() -> Dict[str, Any]:
    info = get_info()
    meta = info.meta()
    universe = [
        {
            "name": a.get("name"),
            "szDecimals": a.get("szDecimals"),
            "maxLeverage": a.get("maxLeverage"),
            "onlyIsolated": bool(a.get("onlyIsolated", False)),
            "isDelisted": bool(a.get("isDelisted", False)),
        }
        for a in meta.get("universe", [])
        if not a.get("isDelisted", False)
    ]
    return {"count": len(universe), "universe": universe}


# ─── Account-state data points (need ACP_AGENT_WALLET) ───────────────────


@register_data_point(
    name="hl_holdings",
    category="account",
    source="hyperliquid",
    description=(
        "Composite holdings for a Hyperliquid account: open perp positions + "
        "spot balances + USDC margin. NOTE: account_value here is the "
        "perp-side marginSummary.accountValue — ≈ 0 when flat under unified "
        "mode. For account worth, use hl_total_equity's equity_usd "
        "(TRADING.md money glossary)."
    ),
    params_schema={"account_name": {"type": "string", "required": True}},
    returns_schema={"perp_positions": "list", "spot_balances": "list", "usdc_withdrawable": "float"},
    tags=["account", "holdings", "hyperliquid"],
)
def hl_holdings(account_name: str) -> Dict[str, Any]:
    info = get_info()
    addr = resolve_account_address(account_name)
    state = info.user_state(addr)
    spot = info.spot_user_state(addr)

    perp_positions: List[Dict[str, Any]] = []
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        if not pos.get("szi") or float(pos.get("szi", 0)) == 0:
            continue
        leverage_raw = pos.get("leverage")
        if isinstance(leverage_raw, dict):
            lev_value = leverage_raw.get("value")
            lev_type = leverage_raw.get("type")
        else:
            lev_value = leverage_raw
            lev_type = None
        perp_positions.append({
            "coin": pos.get("coin"),
            "szi": float(pos.get("szi", 0)),
            "entry_px": float(pos["entryPx"]) if pos.get("entryPx") is not None else None,
            "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
            "leverage_value": float(lev_value) if lev_value is not None else None,
            "leverage_type": lev_type,
            "liquidation_px": float(pos["liquidationPx"]) if pos.get("liquidationPx") is not None else None,
            "margin_used": float(pos.get("marginUsed", 0)),
        })

    spot_balances = [
        {
            "coin": b.get("coin"),
            "total": float(b.get("total", 0)),
            "hold": float(b.get("hold", 0)),
            "entry_ntl": float(b["entryNtl"]) if b.get("entryNtl") is not None else None,
        }
        for b in spot.get("balances", [])
        if float(b.get("total", 0)) != 0
    ]

    margin_summary = state.get("marginSummary", {}) or {}
    return {
        "account_name": account_name,
        "address": addr,
        "perp_positions": perp_positions,
        "spot_balances": spot_balances,
        "usdc_withdrawable": float(state.get("withdrawable", 0)),
        "account_value": float(margin_summary.get("accountValue", 0)),
        "total_margin_used": float(margin_summary.get("totalMarginUsed", 0)),
        "total_ntl_pos": float(margin_summary.get("totalNtlPos", 0)),
    }


@register_data_point(
    name="hl_total_equity",
    category="account",
    source="hyperliquid",
    description=(
        "Total Hyperliquid equity for an account in USD: spot USDC + perp "
        "margin equity (accountValue, which already includes unrealized "
        "perp PnL). With unified mode the SAME USDC backs both — but HL's "
        "user_state.marginSummary.accountValue only counts margin-allocated "
        "USDC. The full picture needs spot_user_state too. We sum them; "
        "if there's accidental double-counting in extreme edge cases it's "
        "favorable (overstate equity → conservative drawdown alerts)."
    ),
    params_schema={"account_name": {"type": "string", "required": True}},
    returns_schema={
        "account_name": "string",
        "equity_usd": "float",
        "spot_usdc": "float",
        "perp_account_value": "float",
        "withdrawable_usd": "float",
    },
    tags=["account", "equity", "hyperliquid"],
)
def hl_total_equity(account_name: str) -> Dict[str, Any]:
    addr = resolve_account_address(account_name)
    return {"account_name": account_name, "address": addr,
            **equity_breakdown(addr)}


def equity_breakdown(addr: str) -> Dict[str, float]:
    """THE equity measure (TRADING.md money glossary), for one address.

    ``equity_usd = spot_usdc + perp_account_value``. Anything that needs
    "how much is this account worth" — sizing, snapshots, drawdown, the
    balance-change alert — uses THIS, never ``marginSummary.accountValue``
    alone: under unified mode that perp-side number is ≈0 when flat and
    only shows margin-allocated funds.
    """
    info = get_info()
    state = info.user_state(addr)
    spot = info.spot_user_state(addr)
    margin_summary = state.get("marginSummary", {}) or {}

    perp_account_value = float(margin_summary.get("accountValue", 0))
    spot_usdc = 0.0
    for b in spot.get("balances", []):
        if (b.get("coin") or "").upper() == "USDC":
            try:
                spot_usdc += float(b.get("total", 0))
            except (TypeError, ValueError):
                continue

    return {
        "equity_usd": spot_usdc + perp_account_value,
        "spot_usdc": spot_usdc,
        "perp_account_value": perp_account_value,
        "withdrawable_usd": float(state.get("withdrawable", 0)),
    }


@register_data_point(
    name="hl_drawdown_from_peak",
    category="account",
    source="hyperliquid",
    description=(
        "Current drawdown vs. peak equity, derived from data_point_snapshots "
        "history of hl_total_equity. Returns 0.0 when not yet drawing down."
    ),
    params_schema={
        "account_name":  {"type": "string", "required": True},
        "lookback_days": {"type": "integer", "default": 90},
    },
    returns_schema={
        "account_name": "string", "current_equity_usd": "float",
        "peak_equity_usd": "float", "drawdown_pct": "float", "drawdown_usd": "float",
        "samples": "int",
    },
    tags=["account", "equity", "drawdown", "derived"],
)
def hl_drawdown_from_peak(account_name: str, lookback_days: int = 90) -> Dict[str, Any]:
    from trading.lifecycle.db import get_db

    info = get_info()
    addr = resolve_account_address(account_name)
    # Use total equity (spot + perp), not just marginSummary.accountValue
    # which is 0 when there are no perp positions.
    total_eq = hl_total_equity(account_name)
    current = total_eq["equity_usd"]

    conn = get_db()
    cutoff_ms = int((time.time() - lookback_days * 86400) * 1000)
    rows = conn.execute(
        "SELECT value_json, ts FROM data_point_snapshots "
        "WHERE name = 'hl_total_equity' AND ts >= ? "
        "ORDER BY ts ASC",
        (cutoff_ms,),
    ).fetchall()

    import json as _json
    samples = []
    for r in rows:
        try:
            payload = _json.loads(r["value_json"])
            if payload.get("account_name") == account_name:
                samples.append(float(payload.get("equity_usd", 0)))
        except Exception:
            continue
    samples.append(current)

    peak = max(samples) if samples else current
    drawdown_usd = peak - current
    drawdown_pct = (drawdown_usd / peak * 100.0) if peak > 0 else 0.0
    return {
        "account_name": account_name,
        "current_equity_usd": current,
        "peak_equity_usd": peak,
        "drawdown_usd": drawdown_usd,
        "drawdown_pct": drawdown_pct,
        "samples": len(samples),
        "lookback_days": lookback_days,
    }


@register_data_point(
    name="hl_trade_readiness",
    category="account",
    source="hyperliquid",
    description=(
        "Is the trade path live RIGHT NOW? Live on-chain check that the API "
        "wallet is approveAgent-registered for the master and unexpired "
        "(TRADING.md fact #3). ready=false means EVERY trade fails silently "
        "with 'User or API Wallet does not exist' — escalate; do not "
        "theorize about filters, spot/perp, or dgclaw. Equity does NOT "
        "prove readiness."
    ),
    params_schema={"warn_days": {"type": "integer", "default": 7}},
    returns_schema={
        "ready": "bool", "reason": "string", "days_remaining": "float|null",
        "valid_until_iso": "string|null", "warn_expiring_soon": "bool",
    },
    tags=["account", "readiness", "watchdog", "hyperliquid"],
)
def hl_trade_readiness(warn_days: int = 7) -> Dict[str, Any]:
    import os

    from .readiness import check_registration

    result = check_registration(
        os.getenv("ACP_AGENT_WALLET") or "",
        os.getenv("HL_API_WALLET_ADDRESS") or "",
        os.getenv("HL_API_WALLET_KEY") or "",
        warn_days=warn_days,
    )
    result.pop("_exit", None)
    return result
