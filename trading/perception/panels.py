"""Perception panels — the standard sweep as data, tiered per symbol.

The panel that lived as prose in plutus-perception's AGENT.md becomes code
here, so a sweep is drivable without an LLM in the loop and a watchlist can
scale without multiplying agent context. Three tiers:

- **full** — symbols the desk is actively working (open position, or a live
  strategy declaring the symbol in its data points): all three regime
  timescales, microstructure, and positioning — the standard sweep.
- **passive** — watchlist symbols merely watched: a cheap pulse (price,
  funding, daily volatility and structure).
- **global** — symbol-independent macro points, fetched once per sweep.

Every entry is ``(name, params)`` where params are the exact fetcher kwargs.
``tests/trading/test_perception_panels.py`` pins panel↔registry
compatibility, so a renamed data point or changed signature fails loudly at
test time instead of silently fetching nothing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

PanelEntry = Tuple[str, Dict[str, Any]]

# The TA suite fetched per timescale on full-tier symbols. Params beyond
# symbol/interval ride the fetcher defaults (length=14/20 etc. — the same
# values the agent-driven sweeps used).
_TA_SUITE = (
    "ta_atr", "ta_ema", "ta_rsi", "ta_macd", "ta_obv", "ta_psar",
    "ta_vortex", "ta_adx", "ta_stochastic", "ta_donchian",
)
_TIMESCALE_INTERVALS = ("1h", "4h", "1d")


def full_panel(symbol: str) -> List[PanelEntry]:
    """The standard sweep for one actively-worked symbol (~45 fetches)."""
    panel: List[PanelEntry] = [
        ("hl_price", {"symbol": symbol}),
        ("hl_orderbook", {"symbol": symbol, "depth": 10}),
        ("hl_book_imbalance", {"symbol": symbol, "band_bps": 50}),
        ("session_context", {"symbol": symbol}),
        ("hl_funding_and_oi", {"symbol": symbol}),
        ("hl_funding_zscore", {"symbol": symbol, "lookback_days": 30}),
    ]
    for interval in _TIMESCALE_INTERVALS:
        panel.append(("hl_candles", {"symbol": symbol, "interval": interval,
                                     "lookback_bars": 200}))
        panel.append(("hl_cvd", {"symbol": symbol, "interval": interval,
                                 "lookback_bars": 200}))
        for ta in _TA_SUITE:
            panel.append((ta, {"symbol": symbol, "interval": interval}))
    # Intraday extras beyond the per-timescale suite.
    panel.extend([
        ("ta_bbands", {"symbol": symbol, "interval": "1h"}),
        ("ta_mfi", {"symbol": symbol, "interval": "1h"}),
        ("ta_vwap", {"symbol": symbol, "interval": "1h", "anchor": "D"}),
        ("ta_vwap", {"symbol": symbol, "interval": "1d", "anchor": "W"}),
        ("poly_price_ladder", {"symbol": symbol}),
    ])
    return panel


def passive_panel(symbol: str) -> List[PanelEntry]:
    """A cheap pulse for watched-but-not-worked symbols (4 fetches)."""
    return [
        ("hl_price", {"symbol": symbol}),
        ("hl_funding_and_oi", {"symbol": symbol}),
        ("ta_atr", {"symbol": symbol, "interval": "1d"}),
        ("hl_candles", {"symbol": symbol, "interval": "1d",
                        "lookback_bars": 31}),
    ]


def global_panel() -> List[PanelEntry]:
    """Symbol-independent macro points, fetched once per sweep."""
    return [
        ("macro_vix", {}),
        ("macro_dxy", {}),
        ("macro_cpi", {}),
        ("btc_dominance_velocity", {}),
    ]


def watchlist_from_config() -> List[str]:
    """The operator watchlist (config ``trading.watchlist``), default BTC."""
    try:
        from harness.cli.config import load_config
        wl = ((load_config().get("trading") or {}).get("watchlist")) or []
        wl = [str(s).strip().upper() for s in wl if str(s).strip()]
        return wl or ["BTC"]
    except Exception:
        return ["BTC"]


def derive_tiers(conn, watchlist: List[str]) -> Dict[str, str]:
    """Map each watchlist symbol to 'full' | 'passive'.

    Full when the desk is actively working the symbol: an open position, or
    any test/active strategy declaring it in a data point's params. If
    nothing qualifies, the first watchlist symbol is full — the desk never
    goes entirely passive-blind.
    """
    active: set = set()
    try:
        for row in conn.execute(
                "SELECT DISTINCT symbol FROM positions WHERE status='open'"):
            active.add(str(row[0]).upper())
    except Exception:
        pass
    try:
        for (dp_json,) in conn.execute(
                "SELECT data_points_json FROM strategies "
                "WHERE status IN ('test','active') AND data_points_json IS NOT NULL"):
            try:
                for dp in json.loads(dp_json) or []:
                    sym = ((dp.get("params") or {}).get("symbol"))
                    if sym:
                        active.add(str(sym).upper())
            except Exception:
                continue
    except Exception:
        pass

    tiers = {s: ("full" if s in active else "passive") for s in watchlist}
    if "full" not in tiers.values() and watchlist:
        tiers[watchlist[0]] = "full"
    return tiers
