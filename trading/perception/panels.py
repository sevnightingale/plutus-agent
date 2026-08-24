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
import logging
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

# Symbols with a registered Polymarket price ladder — the ladder is a
# crypto-market instrument; equity/commodity symbols have none and asking
# would fail every sweep.
POLYMARKET_SYMBOLS = frozenset({"BTC", "ETH"})


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
    ])
    if symbol in POLYMARKET_SYMBOLS:
        panel.append(("poly_price_ladder", {"symbol": symbol}))
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
        ("macro_us10y", {}),
        ("macro_us10y_real", {}),
        ("btc_dominance_velocity", {}),
        # The account is not symbol-scoped, so no per-symbol panel reaches it
        # and it was on no poller at all: between 2026-08-06 and 2026-08-24
        # nothing wrote an equity snapshot, the balance drifted $3.40 behind
        # the venue, and every surface reading it said $75 with conviction.
        # hl_drawdown_from_peak derives from this history too.
        ("hl_total_equity", {"account_name": "hl_trading"}),
    ]


def normalize_symbol(s: str) -> str:
    """Canonical symbol form: dex prefix lowercase, asset uppercase.

    Hyperliquid names builder-dex assets "xyz:GOLD" — a blanket .upper()
    would mangle the dex half into a symbol the venue does not know.
    """
    s = str(s).strip()
    if ":" in s:
        dex, asset = s.split(":", 1)
        return f"{dex.lower()}:{asset.upper()}"
    return s.upper()


def watchlist_from_config() -> List[str]:
    """The operator watchlist (config ``trading.watchlist``), default BTC."""
    try:
        from harness.cli.config import load_config
        wl = ((load_config().get("trading") or {}).get("watchlist")) or []
        wl = [normalize_symbol(s) for s in wl if str(s).strip()]
        return wl or ["BTC"]
    except Exception:
        return ["BTC"]


# Hard ceiling on declared entries added per symbol. The book is agent-authored
# and unbounded; a runaway generation round must not turn one sweep into
# thousands of HTTP fetches. Measured 2026-08-22: the live book needed 242
# extra fetches across 7 symbols (max 44 on one), so 120 is ~3x headroom over
# observed need while still bounding the pathological case. A truncation is
# LOGGED — a silent cap reads as "covered everything" when it did not.
MAX_DECLARED_PER_SYMBOL = 120

logger = logging.getLogger(__name__)


def declared_panel(conn, symbol: str) -> List[PanelEntry]:
    """Data points the live book declares for ``symbol`` that the standard
    panel does not already fetch.

    THE DRIFT THIS CLOSES. ``full_panel`` is a hand-written list; the book is
    authored by plutus-generate and moves away from it freely. When a
    strategy declares a parameterisation the sweep never fetches,
    ``register_prediction``'s freshness backstop refuses the registration and
    the strategy stops accumulating evidence — neither proved nor disproved.
    The desk then looks patient rather than blocked.

    Measured 2026-08-22: **242 declared entries unfetched across 7 symbols**
    against a 330-fetch sweep, with 46% of declared references carrying
    ``lookback_bars`` that the TA suite above never requests. predict had
    been reporting "24 of 26 candidate strategies" blocked for three days,
    and the median cached reading had aged to 34.5h against a 4h floor.

    Deriving the panel from the book closes the class: whatever generate
    authors next is fetched, without anyone remembering to edit a list.
    """
    base = {_entry_key(n, p) for n, p in full_panel(symbol)}
    base |= {_entry_key(n, p) for n, p in global_panel()}

    seen: set = set()
    out: List[PanelEntry] = []
    truncated = 0
    unregistered = 0
    for row_symbol, declared in _declared_rows(conn):
        if row_symbol != symbol:
            continue
        for dp in declared:
            name = dp.get("name")
            if not name:
                continue
            entry = _registry_entry(name)
            if entry is _UNREGISTERED:
                # Declared but unsourced — the self-extension hook's job
                # (``missing_data_points`` → a perception task), not the
                # sweep's. Fetching it would fail every time and drown the
                # sweep's failure list, which is how a real failure is missed.
                #
                # COUNTED, because the fail-open guard below only catches a
                # WHOLLY empty registry. A PARTIALLY populated one — one
                # integration failing to import — is non-empty, so this
                # filter engages and silently drops every ta_* entry.
                # Measured: a 26-entry registry yields 3 extras instead of
                # 242, with no signal. A silent 239-entry drop is the same
                # class of quiet the truncation log just closed.
                unregistered += 1
                continue
            # Bind the symbol from the fetcher's own signature. Books declare
            # the same point three ways — right symbol, a parent's symbol on a
            # clone variant, and none at all — and the third fetches nothing
            # ("missing 1 required positional argument"). fetch_and_snapshot
            # strips extras itself, so only the INJECT half is load-bearing;
            # when the registry is not yet populated we cannot tell, and both
            # guesses break a fetch, so the declared shape is preserved.
            params = dict(_normalize_params(dp.get("params")))
            if entry is _UNKNOWN_REGISTRY:
                if "symbol" in params:
                    params["symbol"] = symbol
            elif _fn_takes_symbol(entry):
                params["symbol"] = symbol
            key = _entry_key(name, params)
            if key in base or key in seen:
                continue
            if len(out) >= MAX_DECLARED_PER_SYMBOL:
                truncated += 1
                continue
            seen.add(key)
            out.append((name, params))
    if unregistered:
        logger.warning(
            "declared_panel(%s): %d declared entries skipped as unregistered "
            "(registry holds %d). A partially-imported registry looks exactly "
            "like a book full of unsourced points.",
            symbol, unregistered, _registry_size())
    if truncated:
        logger.warning(
            "declared_panel(%s): %d declared entries dropped at the %d cap — "
            "the book has outgrown one sweep", symbol, truncated,
            MAX_DECLARED_PER_SYMBOL)
    return out


def panel_for(conn, symbol: str, tier: str) -> List[PanelEntry]:
    """The COMPLETE panel for one symbol — standard tier plus declared extras.

    One builder, because the panel is consumed twice: the sweep fetches it and
    ``perception_render`` filters the Readings zone to it. Built separately,
    the two drift — and on 2026-08-22 they did, in the same change that fixed
    the first drift: declared extras were fetched and cached, then dropped
    from PERCEPTION.md because the renderer rebuilt the panel without them.
    A reading nothing renders is a reading predict cannot curate.
    """
    if tier == "full":
        return list(full_panel(symbol)) + declared_panel(conn, symbol)
    return list(passive_panel(symbol))


def _declared_rows(conn) -> List[Tuple[str, List[dict]]]:
    """(symbol, declared data points) for every live strategy.

    Symbols are normalised on the way out: nothing normalises on the WRITE
    side (``files.py`` stores frontmatter verbatim, and the v7 migration
    copied symbols out of data-point params), so an exact SQL match on a
    mirror row reading ``gold`` would silently return nothing — the same
    failure mode this module exists to remove.
    """
    rows: List[Tuple[str, List[dict]]] = []
    try:
        cur = conn.execute(
            "SELECT symbol, data_points_json FROM strategies "
            "WHERE status IN ('test','active') AND data_points_json IS NOT NULL")
    except Exception:
        return rows
    for sym, dp_json in cur:
        try:
            declared = json.loads(dp_json) or []
        except Exception:
            continue
        rows.append((normalize_symbol(sym or ""), declared))
    return rows



def _normalize_params(params: object) -> dict:
    """Coerce a YAML string-params declaration into a dict.

    Delegates to ``strategies.files._normalize_params`` — strategy files
    sometimes store ``params: symbol=BTC`` as a STRING, and ``dict()`` on
    that raises ValueError. Here the raise would escape ``declared_panel``
    and kill the whole sweep, all seven symbols, over one malformed book.
    Imported lazily: ``strategies.files`` pulls the strategy stack, and this
    module is imported by the renderer.
    """
    try:
        from trading.strategies.files import _normalize_params as _n

        return _n(params)
    except Exception:
        return params if isinstance(params, dict) else {}


# Sentinels for the two non-entry outcomes of a registry lookup, so callers
# branch on identity instead of juggling None-means-two-things.
_UNREGISTERED = object()
_UNKNOWN_REGISTRY = object()


def _registry_entry(name: str):
    """The registry entry for ``name``, or a sentinel.

    ``_UNKNOWN_REGISTRY`` when the registry is not populated — it fills as a
    side effect of dispatcher discovery, so a caller running before that sees
    it EMPTY. Filtering against an empty registry would drop every declared
    entry and return nothing at all, silently, which is precisely the failure
    this module exists to remove. Fail open.
    """
    try:
        from trading.perception.core import data_point_registry as reg

        if not reg.list_all():
            return _UNKNOWN_REGISTRY
        try:
            return reg.lookup(name)
        except KeyError:
            return _UNREGISTERED
    except Exception:
        return _UNKNOWN_REGISTRY



def _registry_size() -> int:
    """How many data points the registry currently holds (0 on failure)."""
    try:
        from trading.perception.core import data_point_registry as reg

        return len(reg.list_all())
    except Exception:
        return 0


def _fn_takes_symbol(entry) -> bool:
    """True when a registry entry's fetcher accepts ``symbol``."""
    fn = getattr(entry, "fn", None)
    if fn is None:
        return False
    try:
        import inspect

        return "symbol" in inspect.signature(fn).parameters
    except Exception:
        return False


def _entry_key(name: str, params: Dict[str, Any]) -> str:
    """Stable identity for a panel entry, symbol-independent.

    Deliberately NOT ``strategies.files._dp_key``: that is the canonical
    declared-DP key (parsed by ``resolve_dp_key``, stored in
    ``support_scores.data_point``) and it INCLUDES symbol. This one compares
    panel entries within a single symbol's panel, where the symbol is
    constant and carrying it would only add noise. Keep the two apart — a
    string that merely looks canonical and is not is worse than an obviously
    local one.
    """
    inner = ",".join(f"{k}={params[k]}" for k in sorted(params) if k != "symbol")
    return f"{name}({inner})"


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
            active.add(normalize_symbol(row[0]))
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
                        active.add(normalize_symbol(sym))
            except Exception:
                continue
    except Exception:
        pass

    tiers = {s: ("full" if s in active else "passive") for s in watchlist}
    if "full" not in tiers.values() and watchlist:
        tiers[watchlist[0]] = "full"
    return tiers
