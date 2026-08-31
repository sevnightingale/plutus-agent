"""Free, deterministic sources for the macro data points.

Replaced context.dev (paid AI web-extraction) on 2026-08-23: every macro
number the desk reads has a free, structured source, so each fetcher here
hits a JSON/CSV endpoint (or one fixed markdown table) and parses it
deterministically — no extraction model, no API key, no credit meter.

We FAIL LOUDLY: an unreadable source raises rather than returning a fallback
or a guessed value — a macro reading must be real or absent, never
fabricated. ``first_of`` runs a fallback chain of *independent* sources and
raises when every one fails.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Callable, Dict, List, Sequence, Tuple

_UA = "Mozilla/5.0 (X11; Linux x86_64) plutus-agent/1.0"


def _http_get(url: str, timeout: int = 30, headers: Dict[str, str] | None = None) -> str:
    # Accept/Accept-Encoding are load-bearing: FRED holds the connection open
    # for ~forever when they are absent (urllib sends neither by default;
    # measured 2026-08-23 — 20s timeout without them, 0.2s with).
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "*/*", "Accept-Encoding": "identity",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_post_json(url: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": _UA, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def first_of(sources: Sequence[Tuple[str, Callable[[], Dict[str, Any]]]]) -> Dict[str, Any]:
    """Run each ``(source_name, fetcher)`` until one returns a dict containing
    a numeric ``value``; tag the result with ``source``. Raises when every
    source fails — never guesses."""
    last_err: Exception | None = None
    for name, fetch in sources:
        try:
            data = fetch()
            if data and isinstance(data.get("value"), (int, float)):
                data["source"] = name
                return data
        except Exception as exc:  # noqa: BLE001 — try the next independent source
            last_err = exc
            continue
    names = [n for n, _ in sources]
    raise RuntimeError(f"every macro source failed for {names}: {last_err}")


# ── Yahoo Finance chart API (unofficial but stable; live quotes) ─────────────

def yahoo_last(symbol: str) -> Dict[str, Any]:
    """Last regular-market price for ``symbol`` via the v8 chart endpoint."""
    from urllib.parse import quote
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}"
        "?range=1d&interval=1d"
    )
    meta = json.loads(_http_get(url))["chart"]["result"][0]["meta"]
    return {"value": float(meta["regularMarketPrice"])}


# ── FRED CSV (no key; official; T+1 lag — fine for level classification) ─────

def fred_latest(series_id: str) -> Dict[str, Any]:
    """Most recent non-missing observation of a FRED series via fredgraph.csv."""
    # fredgraph.csv streams the full series history and can be slow — give
    # it more rope than the JSON endpoints.
    csv = _http_get(
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", timeout=60
    )
    for line in reversed(csv.strip().splitlines()[1:]):  # [0] is the header
        date, _, raw = line.partition(",")
        raw = raw.strip()
        if raw and raw != ".":
            return {"value": float(raw), "date": date}
    raise RuntimeError(f"FRED series {series_id} contained no observations")


# ── BLS public API (no key): headline CPI YoY computed from the index ────────

def bls_cpi_yoy() -> Dict[str, Any]:
    """Headline CPI-U YoY %, computed from index levels (CUUR0000SA0).

    Computing YoY from the index is more honest than scraping a headline
    figure — the arithmetic is the definition.
    """
    from datetime import datetime, timezone
    year = datetime.now(timezone.utc).year
    resp = _http_post_json(
        "https://api.bls.gov/publicAPI/v1/timeseries/data/",
        {"seriesid": ["CUUR0000SA0"], "startyear": str(year - 2), "endyear": str(year)},
    )
    if resp.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API refused: {resp.get('status')} {resp.get('message')}")
    def _parses(raw: str) -> bool:
        try:
            float(raw)
            return True
        except ValueError:  # BLS placeholders like "-" for pending months
            return False

    rows = [r for r in resp["Results"]["series"][0]["data"]  # newest first
            if r["period"].startswith("M") and r["period"] != "M13"
            and _parses(r["value"])]
    if not rows:
        raise RuntimeError("BLS returned no parseable monthly CPI observations")
    monthly = {(r["year"], r["period"]): float(r["value"]) for r in rows}
    latest = rows[0]
    prior_key = (str(int(latest["year"]) - 1), latest["period"])
    if prior_key not in monthly:
        raise RuntimeError(f"BLS: no year-ago index for {latest['periodName']} {latest['year']}")
    value = (float(latest["value"]) / monthly[prior_key] - 1.0) * 100.0
    return {"value": round(value, 2), "period": f"{latest['periodName']} {latest['year']}"}


# ── Synthetic DXY from ECB reference rates (frankfurter.dev; no key) ─────────

# The fixed ICE trade-weights: DXY = 50.14348112 · EURUSD^-0.576 · USDJPY^0.136
#   · GBPUSD^-0.119 · USDCAD^0.091 · USDSEK^0.042 · USDCHF^0.036
_DXY_CONST = 50.14348112
_DXY_WEIGHTS = {"EUR": 0.576, "JPY": 0.136, "GBP": 0.119,
                "CAD": 0.091, "SEK": 0.042, "CHF": 0.036}


def synthetic_dxy() -> Dict[str, Any]:
    """DXY computed from ECB daily reference rates via the ICE formula.

    All frankfurter rates are quoted USD→CCY, and the ICE formula's negative
    exponents on EURUSD/GBPUSD flip them to USD-per-CCY — so every term here
    is simply rate^weight. Daily fix, not live ticks; a level classifier
    doesn't care.
    """
    rates = json.loads(_http_get(
        "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR,JPY,GBP,CAD,SEK,CHF"
    ))["rates"]
    value = _DXY_CONST
    for ccy, weight in _DXY_WEIGHTS.items():
        value *= rates[ccy] ** weight
    return {"value": round(value, 2)}


# ── Farside BTC ETF flows via the Jina Reader proxy (free tier, no key) ──────

_FARSIDE_ROW = re.compile(
    r"^\|\s*(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})\s*\|(.*)\|\s*$"
)


def _flow_number(cell: str) -> float:
    """Farside cells: '306.7', '(27,528)' (negative), '-', ''."""
    cell = cell.strip().replace(",", "")
    if not cell or cell in {"-", "–"}:
        return 0.0
    if cell.startswith("(") and cell.endswith(")"):
        return -float(cell[1:-1])
    return float(cell)


def farside_btc_netflow() -> Dict[str, Any]:
    """Most recent day's aggregate US spot-BTC-ETF net flow (USD millions).

    Farside blocks datacenter IPs directly; the Jina Reader proxy
    (r.jina.ai) renders the page as markdown, and the LAST dated row's final
    column is the day's total (the 'Total' *row* is the all-time cumulative
    — not what this DP reads).
    """
    md = _http_get("https://r.jina.ai/https://farside.co.uk/btc/", timeout=60)
    latest: Tuple[str, float] | None = None
    for line in md.splitlines():
        m = _FARSIDE_ROW.match(line)
        if not m:
            continue
        cells = [c for c in m.group(2).split("|")]
        if not cells:
            continue
        latest = (m.group(1), _flow_number(cells[-1]))
    if latest is None:
        raise RuntimeError("Farside table had no dated rows (page shape changed?)")
    return {"value": latest[1], "date": latest[0]}


# ── Regime bucketing (unchanged from the context.dev era) ────────────────────

def classify(value: float, buckets: List[tuple]) -> Dict[str, str]:
    """Map ``value`` to its regime bucket. ``buckets`` are ``(lo, hi, label,
    narrative)`` with half-open ``[lo, hi)`` ranges (use ``float('-inf')`` /
    ``float('inf')`` for open ends). Returns ``{label, narrative}``; the last
    bucket is the fallback if nothing matches.
    """
    for lo, hi, label, narrative in buckets:
        if lo <= value < hi:
            return {"label": label, "narrative": narrative}
    lo, hi, label, narrative = buckets[-1]
    return {"label": label, "narrative": narrative}


# ── EIA Weekly Petroleum Status Report (keyless ir.eia.gov CSV) ──────────────

_WPSR_TABLE1_URL = "https://ir.eia.gov/wpsr/table1.csv"


def eia_wpsr_stocks() -> Dict[str, Any]:
    """Stocks section of WPSR Table 1 — the weekly oil-inventory print.

    ir.eia.gov serves the current report's summary tables as keyless CSVs
    (verified 2026-08-31; the EIA v2 API needs a registered key and FRED
    dropped the EIA weekly series). Table 1's first block is the petroleum
    stocks balance in MILLION barrels: column 1 is the latest week-ending
    date, column 2 the prior week, column 3 the week-over-week difference —
    the "Commercial (Excluding SPR)" difference IS the headline build/draw
    the market trades.

    Parsing stops at the second STUB_1 header row (the supply block that
    follows repeats labels in different units). A missing label or a
    reshaped header raises — the print is real or absent, never guessed.
    """
    import csv as _csv
    import io as _io
    from datetime import datetime as _dt

    body = _http_get(_WPSR_TABLE1_URL, timeout=45)
    rows = list(_csv.reader(_io.StringIO(body)))
    if not rows or not rows[0] or rows[0][0] != "STUB_1":
        raise RuntimeError("WPSR table1.csv: unexpected header shape")

    def _iso(mdy: str) -> str:
        return _dt.strptime(mdy.strip(), "%m/%d/%y").date().isoformat()

    week_ending, prior_week = _iso(rows[0][1]), _iso(rows[0][2])

    stocks: Dict[str, Dict[str, float]] = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        if r[0] == "STUB_1":  # the supply block begins — stocks are done
            break
        try:
            level = float(r[1].replace(",", ""))
            change = float(r[3].replace(",", ""))
        except (ValueError, IndexError):
            continue  # spacer/percent-only rows carry no stock reading
        stocks[r[0].strip()] = {"level_mbbl": level, "change_mbbl": change}

    required = ("Commercial (Excluding SPR)", "Total Motor Gasoline",
                "Distillate Fuel Oil", "Strategic Petroleum Reserve (SPR)")
    missing = [k for k in required if k not in stocks]
    if missing:
        raise RuntimeError(f"WPSR table1.csv: rows not found: {missing}")

    return {"week_ending": week_ending, "prior_week_ending": prior_week,
            "stocks": stocks, "source": _WPSR_TABLE1_URL}


def next_wpsr_release() -> Dict[str, Any]:
    """Next scheduled WPSR release: Wednesdays 10:30 ET (UTC-converted).

    APPROXIMATE by design — federal-holiday weeks slip to Thursday and the
    schedule note says so rather than pretending otherwise.
    """
    from datetime import datetime as _dt, timedelta as _td
    from zoneinfo import ZoneInfo as _Zi

    et = _Zi("America/New_York")
    now = _dt.now(et)
    candidate = now.replace(hour=10, minute=30, second=0, microsecond=0)
    candidate += _td(days=(2 - candidate.weekday()) % 7)  # next Wednesday
    if candidate <= now:
        candidate += _td(days=7)
    return {
        "next_release_utc": candidate.astimezone(
            _Zi("UTC")).isoformat(timespec="minutes"),
        "days_to_next_release": round(
            (candidate - now).total_seconds() / 86400.0, 2),
        "schedule_note": ("Wednesdays 10:30 ET; federal-holiday weeks "
                          "slip to Thursday — this estimate does not "
                          "track holidays."),
    }
