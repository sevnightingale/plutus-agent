"""Signal-dense, bounded renderers for the conviction-scoring read path.

Issue 4: ``predict_tools._compact`` byte-truncated every declared reading to
400 bytes before the ``conviction_score`` LLM saw it — deleting the orderbook
ask side, ~190 of 200 candles, the CVD trend/divergence tail, and every TA
zone/pattern field. Blinded readings then scored ~0.5 neutral and polluted the
conviction substrate every beat. These renderers attach to data points via the
registry's ``compact_fn`` and emit a small, structured, signal-preserving view
instead of a raw byte clamp.

A renderer MUST be:
  - bounded — a few hundred bytes; never the raw series;
  - signal-dense — keep or compute what the thesis turns on, drop the bulk;
  - total — never raise on shape drift. A raised renderer is caught at
    ``_fetch_reading`` and the reading becomes ``missing`` (honest absence),
    which is safe but loses the signal — so prefer returning a partial view.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(x: Any) -> Optional[float]:
    """Tolerant float — None on anything non-numeric (renderers stay total)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ── orderbook ────────────────────────────────────────────────────────────────

def render_orderbook(v: Dict[str, Any]) -> Dict[str, Any]:
    """hl_orderbook → bid/ask volume imbalance, mid, spread, top-3 each side.

    Both sides survive (the byte clamp used to delete the ask side wholesale)."""
    bids = v.get("bids") or []
    asks = v.get("asks") or []
    bid_vol = round(sum((_f(b.get("sz")) or 0.0) for b in bids), 4)
    ask_vol = round(sum((_f(a.get("sz")) or 0.0) for a in asks), 4)
    tot = bid_vol + ask_vol
    imbalance = round((bid_vol - ask_vol) / tot, 4) if tot > 0 else None
    best_bid = _f(bids[0].get("px")) if bids else None
    best_ask = _f(asks[0].get("px")) if asks else None
    mid = round((best_bid + best_ask) / 2.0, 6) if (best_bid and best_ask) else None
    spread_bp = (round((best_ask - best_bid) / mid * 1e4, 2)
                 if (best_bid and best_ask and mid) else None)

    def _top3(side: List[dict]) -> List[list]:
        return [[_f(l.get("px")), _f(l.get("sz"))] for l in side[:3]]

    return {
        "symbol": v.get("symbol"),
        "mid": mid,
        "spread_bp": spread_bp,
        "bid_vol_top": bid_vol,
        "ask_vol_top": ask_vol,
        "imbalance": imbalance,  # +1 bid-heavy (support) … -1 ask-heavy (supply)
        "bids_top3": _top3(bids),
        "asks_top3": _top3(asks),
    }


# ── candles ──────────────────────────────────────────────────────────────────

def render_candles(v: Dict[str, Any]) -> Dict[str, Any]:
    """hl_candles → last 5 raw OHLCV + features over the FULL window.

    The byte clamp kept ~10 of 200 candles; this keeps the recent bars AND
    derives features the raw series can't show after truncation: pct change at
    several lags, window hi/lo and position in range, an ATR proxy, distance to
    SMA20, the up-bar ratio, and a volume trend."""
    candles = v.get("candles") or []
    n = len(candles)
    base = {"symbol": v.get("symbol"), "interval": v.get("interval"), "count": n}
    if n == 0:
        return base

    closes = [_f(c.get("c")) for c in candles]
    highs = [_f(c.get("h")) for c in candles]
    lows = [_f(c.get("l")) for c in candles]
    opens = [_f(c.get("o")) for c in candles]
    vols = [(_f(c.get("v")) or 0.0) for c in candles]
    closes_ok = [c for c in closes if c is not None]
    last = closes_ok[-1] if closes_ok else None

    def pct_change(k: int) -> Optional[float]:
        if last and n > k and closes[-1 - k]:
            return round((last / closes[-1 - k] - 1) * 100, 3)
        return None

    win_hi = max((h for h in highs if h is not None), default=None)
    win_lo = min((lo for lo in lows if lo is not None), default=None)
    ranges = [highs[i] - lows[i] for i in range(n)
              if highs[i] is not None and lows[i] is not None]
    atr_proxy_pct = (round((sum(ranges) / len(ranges)) / last * 100, 3)
                     if (ranges and last) else None)
    sma20 = (sum(closes_ok[-20:]) / min(20, len(closes_ok))) if closes_ok else None
    close_vs_sma20_pct = (round((last / sma20 - 1) * 100, 3)
                          if (sma20 and last) else None)
    up_bars = sum(1 for i in range(n)
                  if closes[i] is not None and opens[i] is not None
                  and closes[i] > opens[i])
    split = int(n * 0.8)
    recent_v = sum(vols[split:]) / max(1, n - split)
    prior_v = sum(vols[:split]) / max(1, split)
    vol_trend = round(recent_v / prior_v, 3) if prior_v > 0 else None
    last5 = [[c.get("o"), c.get("h"), c.get("l"), c.get("c"), c.get("v")]
             for c in candles[-5:]]

    base.update({
        "last_close": last,
        "pct_change_1": pct_change(1),
        "pct_change_5": pct_change(5),
        "pct_change_20": pct_change(20),
        "window_high": win_hi,
        "window_low": win_lo,
        "pos_in_range_pct": (round((last - win_lo) / (win_hi - win_lo) * 100, 1)
                             if (win_hi is not None and win_lo is not None
                                 and win_hi > win_lo and last is not None) else None),
        "atr_proxy_pct": atr_proxy_pct,
        "close_vs_sma20_pct": close_vs_sma20_pct,
        "up_bar_ratio": round(up_bars / n, 3),
        "volume_trend": vol_trend,  # >1 recent volume rising vs the window
        "last5_ohlcv": last5,
    })
    return base


# ── cvd ──────────────────────────────────────────────────────────────────────

def render_cvd(v: Dict[str, Any]) -> Dict[str, Any]:
    """hl_cvd → current level, trend, slope sign, and the divergence flag.

    The raw dict is ~22 flat fields (~660B); the byte clamp cut the tail — which
    is exactly cvd_trend, divergence, and the per-bar slope. Drop the raw volume
    totals, keep the read."""
    return {
        "cvd_current": v.get("cvd_current"),
        "cvd_trend": v.get("cvd_trend"),
        "cvd_percentile": v.get("cvd_percentile"),
        "buy_pressure_pct": v.get("buy_pressure_pct"),
        "recent_delta_per_bar": v.get("recent_delta_per_bar"),
        "prior_delta_per_bar": v.get("prior_delta_per_bar"),
        "divergence": v.get("divergence"),
        "price_change_pct_recent": v.get("price_change_pct_recent"),
    }


# ── ta_* (one renderer for all indicators) ────────────────────────────────────

def render_ta(v: Dict[str, Any]) -> Dict[str, Any]:
    """ta_* → {indicator, value, trend, zone, fired pattern codes, summary}.

    Each TA preprocessor already computes the signal into a large nested output
    (current / context / levels / patterns / evidence + a 200-bar series). The
    preprocessor's own ``to_compact`` produces the canonical signal-dense view,
    so we reuse it; a never-throw generic selector backs it up for any shape it
    can't handle."""
    if not isinstance(v, dict):
        return {"reading": str(v)[:240]}
    if v.get("error"):
        return {"error": v.get("error"), "message": v.get("message")}

    name = (v.get("indicator") or "").lower()
    try:
        from trading.integrations.ta.preprocessors import get_preprocessor
        pp = get_preprocessor(name) or get_preprocessor(name.replace("_", ""))
        if pp is not None:
            tf = v.get("timeframe") or v.get("interval") or ""
            compact = pp.to_compact(v, tf)
            if isinstance(compact, dict):
                if isinstance(compact.get("analysis"), str):
                    compact["analysis"] = compact["analysis"][:240]
                return compact
    except Exception:
        pass  # fall through to the generic selector — never lose the read
    return _render_ta_generic(v)


def _render_ta_generic(v: Dict[str, Any]) -> Dict[str, Any]:
    """Indicator-agnostic selector over the universal preprocessor output."""
    current = v.get("current") or {}
    trend = (v.get("context") or {}).get("trend") or {}
    value = current.get("value")
    if value is None:
        for k in ("macd", "adx", "k_percent", "price", "atr", "value"):
            if k in current:
                value = current.get(k)
                break
    patterns = v.get("patterns")
    if isinstance(patterns, dict):
        patterns = [k for k, hit in patterns.items() if hit]
    elif isinstance(patterns, list):
        patterns = [p.get("type") or p.get("name") or p.get("pattern")
                    if isinstance(p, dict) else p for p in patterns]
    return {
        "indicator": v.get("indicator"),
        "value": value,
        "trend": trend.get("direction"),
        "trend_strength": trend.get("strength"),
        "velocity": trend.get("velocity"),
        "patterns": (patterns or [])[:6],
        "summary": (v.get("summary") or "")[:240],
    }
