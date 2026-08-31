"""Regime labels computed from numeric data points — the plutus-regime
seat, as arithmetic.

The vocabulary is closed (``write.REGIME_*``), and a closed label set over
numeric readings is an invitation to code, not to judgment: the TA
preprocessors already emit regime-shaped analysis — ADX carries a
directional bias and declared strength thresholds, ATR a volatility
percentile, EMA a trend consensus — at 1h/4h/1d, which is exactly the
intraday/swing/position split. This module maps those onto the vocabulary
per symbol × timescale, writes through the same validating
``write.record_regime`` (source="classifier"), and re-renders REGIME.md
with the existing board renderer. Everything downstream — eligibility,
``current_regime``, both regime integrity invariants — is untouched.

**Hysteresis, because a boundary case is not a flip.** A changed label is
written only after it holds for two consecutive passes; until then the
standing label is re-asserted and the pending change is held in a small
state file. A confirmed change lands with ``flipped=True`` — the row the
event engine's predict predicate reads (the DB is the event log).

**Honest absence.** A timescale whose readings are missing or older than
``MAX_AGE_FACTOR ×`` the interval is skipped with the reason recorded —
the standing label ages rather than being refreshed from stale data, and
the board's staleness invariant will say so if it goes on too long.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TIMESCALE_INTERVALS = {"intraday": "1h", "swing": "4h", "position": "1d"}
_INTERVAL_S = {"1h": 3600.0, "4h": 4 * 3600.0, "1d": 24 * 3600.0}
MAX_AGE_FACTOR = 2.0

# Direction: the ADX preprocessor's own declared thresholds (weak 20,
# strong 25). Below the trend threshold the tape is ranging; in the weak
# band [20, 25) a contradicting EMA consensus demotes to ranging.
ADX_TREND_MIN = 20.0
ADX_STRONG_MIN = 25.0
# Volatility: ATR percentile within its own lookback.
ATR_COMPRESSED_MAX_PCTL = 25.0
ATR_ELEVATED_MIN_PCTL = 75.0
# Macro (position scale only): classic VIX bands.
VIX_RISK_ON_MAX = 15.0
VIX_RISK_OFF_MIN = 25.0

STATE_FILENAME = "regime_classifier_state.json"
SOURCE = "classifier"

_EMA_AGREES = {"trending-up": "rising", "trending-down": "falling"}


def _state_path() -> Path:
    from harness.constants import get_hermes_home
    return get_hermes_home() / STATE_FILENAME


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        _state_path().write_text(json.dumps(state, indent=1),
                                 encoding="utf-8")
    except Exception:
        logger.exception("could not persist classifier state")


def _freshest_entry(cache_state: Dict[str, Any], name: str,
                    params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Freshest cache entry whose stored params superset ``params`` — the
    same matching rule perception freshness uses (an exact key match misses
    every TA point, whose stored keys carry fetch-signature defaults)."""
    from trading.perception.freshness import _params_subset, _parse_cache_key

    best = None
    for key, entry in (cache_state.get("data_points") or {}).items():
        k_name, stored = _parse_cache_key(key)
        if k_name != name or not _params_subset(params, stored):
            continue
        if best is None or entry.get("fetched_at", 0) > best.get("fetched_at", 0):
            best = entry
    return best


def _dig(value: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(value, dict):
            return None
        value = value.get(p)
    return value


def compute_labels(*, adx: Optional[float], adx_bias: Optional[str],
                   ema_consensus: Optional[str],
                   atr_percentile: Optional[float],
                   vix: Optional[float] = None,
                   want_macro: bool = False) -> Optional[Dict[str, Any]]:
    """Pure label arithmetic — None when the required readings are absent.

    ``adx``/``atr_percentile`` are required; ``ema_consensus`` only refines
    the weak band; ``vix`` is consulted only when ``want_macro``.
    """
    if adx is None or atr_percentile is None:
        return None

    if adx < ADX_TREND_MIN:
        direction = "ranging"
    elif adx_bias == "bullish":
        direction = "trending-up"
    elif adx_bias == "bearish":
        direction = "trending-down"
    else:
        direction = "ranging"
    if direction != "ranging" and adx < ADX_STRONG_MIN:
        expected = _EMA_AGREES[direction]
        if ema_consensus is not None and ema_consensus != expected:
            direction = "ranging"  # weak trend, conflicted evidence

    if atr_percentile <= ATR_COMPRESSED_MAX_PCTL:
        volatility = "compressed"
    elif atr_percentile >= ATR_ELEVATED_MIN_PCTL:
        volatility = "elevated"
    else:
        volatility = "normal"

    macro = None
    if want_macro and vix is not None:
        if vix <= VIX_RISK_ON_MAX:
            macro = "risk-on"
        elif vix >= VIX_RISK_OFF_MIN:
            macro = "risk-off"
        else:
            macro = "neutral"

    # Advisory 0-10: strong trend and agreeing evidence read higher.
    conviction = 4.0
    if direction != "ranging":
        conviction += 2.0 if adx >= ADX_STRONG_MIN else 1.0
        if ema_consensus == _EMA_AGREES.get(direction):
            conviction += 1.0
    elif adx < ADX_TREND_MIN - 5:
        conviction += 1.0  # decisively trendless is also a clear reading
    evidence = (f"adx={adx:.1f} bias={adx_bias} ema={ema_consensus} "
                f"atr_pctl={atr_percentile:.0f}"
                + (f" vix={vix:.1f}" if want_macro and vix is not None else ""))
    return {"direction": direction, "volatility": volatility, "macro": macro,
            "conviction": min(conviction, 8.0), "evidence": evidence}


def _readings_for(cache_state: Dict[str, Any], symbol: str,
                  interval: str, now: float) -> Tuple[Optional[Dict], str]:
    """Extract (readings, skip_reason) for one symbol × interval."""
    max_age = MAX_AGE_FACTOR * _INTERVAL_S[interval]
    out: Dict[str, Any] = {}
    # Extraction paths verified against the LIVE cached payloads
    # (2026-08-31, first dry-run tick): the registered fetchers return a
    # slimmer rendering than the preprocessor source suggests — ATR's
    # percentile lives under levels.volatility, and EMA carries no trend
    # consensus at all, so its corroboration is the sign of price-vs-EMA
    # distance (price above the EMA agrees with an up-trend).
    def _ema_dir(v):
        dist = _dig(v, "current", "price_distance_pct")
        if not isinstance(dist, (int, float)) or dist == 0:
            return None
        return "rising" if dist > 0 else "falling"

    for name, extract in (
        ("ta_adx", lambda v: {"adx": _dig(v, "current", "adx"),
                              "adx_bias": _dig(v, "context",
                                               "directional_bias")}),
        ("ta_ema", lambda v: {"ema_consensus": _ema_dir(v)}),
        ("ta_atr", lambda v: {"atr_percentile": _dig(v, "levels",
                                                     "volatility",
                                                     "percentile_rank")}),
    ):
        entry = _freshest_entry(cache_state, name,
                                {"symbol": symbol, "interval": interval})
        if entry is None:
            return None, f"{name}@{interval} missing from perception cache"
        age = now - float(entry.get("fetched_at") or 0)
        if age > max_age:
            return None, (f"{name}@{interval} is {age / 3600:.1f}h old "
                          f"(max {max_age / 3600:.1f}h)")
        out.update(extract(entry.get("value")))
    return out, ""


_CLASSIFIER_DPS = ("ta_adx", "ta_ema", "ta_atr")


def _ensure_fresh(symbols: List[str]) -> None:
    """Refresh the classifier's own inputs through the cache — the same
    self-sufficiency predict has (#658). Within-budget entries are served
    from cache, so this costs HTTP only when readings actually aged out.
    Failures are tolerated here and surface as skips in ``_readings_for``."""
    from trading.perception.fetch_core import fetch_and_snapshot

    for sym in symbols:
        for interval in TIMESCALE_INTERVALS.values():
            for name in _CLASSIFIER_DPS:
                try:
                    fetch_and_snapshot(name,
                                       {"symbol": sym, "interval": interval},
                                       session_id=SOURCE, tier="ops")
                except Exception:
                    logger.warning("classifier refresh of %s %s@%s failed",
                                   name, sym, interval)
    try:
        fetch_and_snapshot("macro_vix", {}, session_id=SOURCE, tier="ops")
    except Exception:
        logger.warning("classifier refresh of macro_vix failed")


def run(conn, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Classify every symbol × timescale, with hysteresis; render the board.

    Records an ``action_type="regime"`` run so the staleness floor the old
    seat satisfied stays satisfied by the classifier.
    """
    from trading.lifecycle import queries, regime_board, write
    from trading.perception.cache import read_perception_state
    from trading.perception.panels import watchlist_from_config

    now = time.time()
    symbols = symbols or watchlist_from_config()
    _ensure_fresh(symbols)
    cache_state = read_perception_state()
    pending = _load_state()

    vix_entry = _freshest_entry(cache_state, "macro_vix", {})
    vix = None
    if vix_entry is not None:
        v = vix_entry.get("value")
        vix = v.get("value") if isinstance(v, dict) else (
            float(v) if isinstance(v, (int, float)) else None)

    written = 0
    flips: List[str] = []
    skipped: Dict[str, str] = {}
    for sym in symbols:
        standing = queries.current_regime(conn, symbol=sym)
        for timescale, interval in TIMESCALE_INTERVALS.items():
            readings, why = _readings_for(cache_state, sym, interval, now)
            if readings is None:
                skipped[f"{sym}/{timescale}"] = why
                continue
            computed = compute_labels(
                want_macro=(timescale == "position"), vix=vix, **readings)
            if computed is None:
                skipped[f"{sym}/{timescale}"] = "incomplete readings"
                continue

            cur = standing.get(timescale) or {}
            cur_tuple = (cur.get("direction"), cur.get("volatility"),
                         cur.get("macro"))
            new_tuple = (computed["direction"], computed["volatility"],
                         computed["macro"])
            key = f"{sym}/{timescale}"
            first_assessment = cur.get("direction") is None

            if first_assessment or new_tuple == cur_tuple:
                labels, flipped = new_tuple, False
                pending.pop(key, None)
            elif pending.get(key) == list(new_tuple):
                labels, flipped = new_tuple, True  # held two passes — confirmed
                flips.append(key)
                pending.pop(key, None)
            else:
                # A boundary case is not a flip: re-assert the standing label,
                # remember what wants to change.
                labels, flipped = cur_tuple, False
                pending[key] = list(new_tuple)

            write.record_regime(
                conn, symbol=sym, timescale=timescale,
                direction=labels[0], volatility=labels[1],
                macro=labels[2] if timescale == "position" else None,
                conviction=computed["conviction"], flipped=flipped,
                source=SOURCE, session_name=SOURCE,
                notes_md=computed["evidence"]
                + (" [pending flip held]" if labels != new_tuple else ""))
            written += 1

    _save_state(pending)
    board = regime_board.write_board(conn, by=SOURCE)
    result = {"written": written, "flips": flips, "skipped": skipped,
              "board_ok": bool(board.get("ok"))}
    write.record_action_run(
        conn, action_type="regime", agent=SOURCE, ok=written > 0,
        session_name=SOURCE, notes_md=json.dumps(result))
    return result
