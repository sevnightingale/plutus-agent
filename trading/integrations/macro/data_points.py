"""Macro data points — direct web-sourced economic readings via context.dev.

Each DP reads ONE number from a canonical source (MarketWatch, BLS, Farside)
through context.dev's ``web.extract`` (structured JSON extraction), classifies
it into a regime bucket deterministically, and returns the value + label. Like
the CoinGecko/DefiLlama direct DPs, these are auto-snapshotted and cached per
the macro staleness budget (4h) — context.dev is hit at most ~once/4h per DP.

This replaced the older "agentic query blueprint" design, where the perception
agent ran web_search + web_extract and reasoned out the number. Direct +
deterministic is cheaper (no agent loop), more reliable (no parse drift), and
removes macro reads from the web-tool search backend entirely.
"""

from __future__ import annotations

from typing import Any, Dict

from trading.integrations.macro._context_client import classify, extract_value
from trading.perception.core.data_point_registry import register_data_point

_INF = float("inf")

# ── Regime buckets: (lo, hi, label, narrative), half-open [lo, hi) ───────────

_VIX_BUCKETS = [
    (0, 15, "low_volatility", "Complacency — markets expect calm. Trend continuation likely."),
    (15, 20, "moderate", "Normal — no fear premium. Standard risk-taking environment."),
    (20, 30, "elevated", "Caution — hedging demand rising. Tighten stops, reduce size."),
    (30, _INF, "extreme", "Fear/panic — expect violent moves. Defensive posture warranted."),
]

_DXY_BUCKETS = [
    (0, 95, "weak", "USD weak — risk-on tailwind for crypto."),
    (95, 100, "neutral", "USD in normal range. No directional signal."),
    (100, 105, "strong", "USD strengthening — risk-off headwind for crypto."),
    (105, _INF, "extreme", "USD extremely strong — liquidity squeeze risk."),
]

_CPI_BUCKETS = [
    (0, 2.5, "low", "Below Fed target range. Dovish — rate cuts likely. Risk-on."),
    (2.5, 3.5, "moderate", "Near Fed target. Neutral — data-dependent. Watch labor market."),
    (3.5, 5.0, "elevated", "Above target. Hawkish — rates higher for longer. Risk-off pressure."),
    (5.0, _INF, "extreme", "Inflation crisis. Aggressive tightening. Severe risk-off."),
]

_ETF_FLOW_BUCKETS = [
    (float("-inf"), -500, "heavy_outflows", ">$500M/day outflows — sustained institutional distribution. Strong risk-off signal."),
    (-500, -100, "moderate_outflows", "$100-500M/day outflows — mild distribution pressure. Bearish but not extreme."),
    (-100, 100, "flat", "Flows near zero — no directional institutional signal. ETF capital is sidelined."),
    (100, 500, "moderate_inflows", "$100-500M/day inflows — steady accumulation. Bullish institutional tailwind."),
    (500, _INF, "heavy_inflows", ">$500M/day inflows — aggressive institutional buying. Strong risk-on signal."),
]


@register_data_point(
    name="macro_vix",
    category="macro",
    source="context.dev",
    description=(
        "CBOE Volatility Index (VIX) — the market's 'fear gauge.' Read live "
        "from MarketWatch and classified into a risk regime "
        "(low_volatility|moderate|elevated|extreme)."
    ),
    params_schema={},
    returns_schema={"value": "float", "risk_regime": "string", "source": "string"},
    numeric_path="value",
    tags=["macro", "volatility", "risk"],
)
def macro_vix() -> Dict[str, Any]:
    data = extract_value(
        primary_url="https://www.marketwatch.com/investing/index/vix",
        fallback_urls=[
            "https://www.cnbc.com/quotes/.VIX",
            "https://finance.yahoo.com/quote/%5EVIX/",
        ],
        schema={"type": "object", "properties": {"value": {"type": "number"}}},
        instructions="Extract the current CBOE Volatility Index (VIX) last price as a number.",
    )
    value = float(data["value"])
    regime = classify(value, _VIX_BUCKETS)
    return {
        "value": value,
        "risk_regime": regime["label"],
        "narrative": regime["narrative"],
        "source": data.get("source"),
    }


@register_data_point(
    name="macro_dxy",
    category="macro",
    source="context.dev",
    description=(
        "US Dollar Index (DXY) — USD strength vs a basket of currencies. Rising "
        "DXY = risk-off / crypto headwind; falling = risk-on / tailwind. Read "
        "live from MarketWatch, classified (weak|neutral|strong|extreme)."
    ),
    params_schema={},
    returns_schema={"value": "float", "strength": "string", "source": "string"},
    numeric_path="value",
    tags=["macro", "forex", "dollar"],
)
def macro_dxy() -> Dict[str, Any]:
    data = extract_value(
        primary_url="https://www.marketwatch.com/investing/index/dxy",
        fallback_urls=[
            "https://www.cnbc.com/quotes/.DXY",
            "https://www.tradingview.com/symbols/TVC-DXY/",
        ],
        schema={"type": "object", "properties": {"value": {"type": "number"}}},
        instructions="Extract the current US Dollar Index (DXY) last price as a number.",
    )
    value = float(data["value"])
    regime = classify(value, _DXY_BUCKETS)
    return {
        "value": value,
        "strength": regime["label"],
        "narrative": regime["narrative"],
        "source": data.get("source"),
    }


@register_data_point(
    name="macro_cpi",
    category="macro",
    source="context.dev",
    description=(
        "US Consumer Price Index — latest headline year-over-year inflation. "
        "Rising CPI = Fed hawkish = risk-off; falling = dovish = risk-on. Read "
        "from the BLS release, classified (low|moderate|elevated|extreme)."
    ),
    params_schema={},
    returns_schema={
        "value": "float",
        "regime": "string",
        "period": "string",
        "source": "string",
    },
    numeric_path="value",
    tags=["macro", "inflation", "fed"],
)
def macro_cpi() -> Dict[str, Any]:
    data = extract_value(
        primary_url="https://www.bls.gov/news.release/cpi.nr0.htm",
        fallback_urls=[
            "https://tradingeconomics.com/united-states/inflation-cpi",
            "https://www.usinflationcalculator.com/inflation/current-inflation-rates/",
        ],
        schema={
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "period": {"type": "string"},
            },
        },
        instructions=(
            "Extract the headline US CPI year-over-year inflation rate as a "
            "percent number (e.g. 3.3 for 3.3%), and the data period "
            "(month and year, e.g. 'May 2026')."
        ),
    )
    value = float(data["value"])
    regime = classify(value, _CPI_BUCKETS)
    return {
        "value": value,
        "regime": regime["label"],
        "narrative": regime["narrative"],
        "period": data.get("period"),
        "source": data.get("source"),
    }


@register_data_point(
    name="btc_etf_netflow_daily",
    category="macro",
    source="context.dev",
    description=(
        "Daily aggregate net flow for US spot Bitcoin ETFs in USD millions. "
        "Positive = net inflows (institutional buying); negative = outflows "
        "(distribution). Read from Farside (T+1 reporting), classified "
        "(heavy_outflows|moderate_outflows|flat|moderate_inflows|heavy_inflows)."
    ),
    params_schema={},
    returns_schema={
        "value": "float",
        "flow_regime": "string",
        "date": "string",
        "source": "string",
    },
    numeric_path="value",
    tags=["macro", "etf", "flows", "institutional"],
)
def btc_etf_netflow_daily() -> Dict[str, Any]:
    data = extract_value(
        primary_url="https://farside.co.uk/btc/",
        fallback_urls=[
            "https://sosovalue.com/assets/btc",
            "https://www.coinglass.com/bitcoin-etf",
        ],
        schema={
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "date": {"type": "string"},
            },
        },
        instructions=(
            "Extract the AGGREGATE total net flow across all US spot Bitcoin "
            "ETFs (IBIT, FBTC, GBTC, ARKB, BITB, etc.) for the most recent "
            "reported day, in USD millions (negative = net outflow). Use the "
            "'Total' row at the bottom of the table. Also extract the date of "
            "that flow (T+1 reporting — the most recent prior trading day)."
        ),
    )
    value = float(data["value"])
    regime = classify(value, _ETF_FLOW_BUCKETS)
    return {
        "value": value,
        "flow_regime": regime["label"],
        "narrative": regime["narrative"],
        "date": data.get("date"),
        "source": data.get("source"),
    }


# ── Rates (2026-08-09, the multi-asset seeding pass) ─────────────────────────
# Gold and equity mechanisms need the rates complex the crypto desk never
# did: the 10y nominal yield is the equity discount-rate driver, and the 10y
# TIPS REAL yield is gold's inverse driver (gold pays no coupon — its
# carrying cost IS the real yield). Buckets classify level, not change;
# strategies read direction off the snapshot history.

_US10Y_BUCKETS = [
    (float("-inf"), 3.5, "accommodative", "Long rates low — duration assets breathe. Equity multiple tailwind."),
    (3.5, 4.25, "neutral", "Long rates mid-range. No strong valuation signal either way."),
    (4.25, 5.0, "restrictive", "Long rates high — discount-rate pressure on equities, USD support."),
    (5.0, _INF, "squeeze", "Long rates extreme — something usually breaks. Risk-off across duration."),
]

_US10Y_REAL_BUCKETS = [
    (float("-inf"), 0.0, "negative", "Negative real yields — gold's strongest tailwind; cash loses purchasing power."),
    (0.0, 1.0, "low", "Mildly positive real yields — modest carrying cost for gold, neutral."),
    (1.0, 2.0, "elevated", "Real yields elevated — real return competes with gold; headwind."),
    (2.0, _INF, "high", "Real yields high — strong gold headwind; TIPS pay you to wait."),
]


@register_data_point(
    name="macro_us10y",
    category="macro",
    source="context.dev",
    description=(
        "US 10-Year Treasury nominal yield (%) — the discount rate on long-"
        "duration assets. Rising = equity multiple compression + USD support; "
        "falling = duration relief. Read live, classified "
        "(accommodative|neutral|restrictive|squeeze)."
    ),
    params_schema={},
    returns_schema={"value": "float", "rate_regime": "string", "source": "string"},
    numeric_path="value",
    tags=["macro", "rates", "bonds", "equities"],
)
def macro_us10y() -> Dict[str, Any]:
    data = extract_value(
        primary_url="https://www.marketwatch.com/investing/bond/tmubmusd10y?countrycode=bx",
        fallback_urls=[
            "https://www.cnbc.com/quotes/US10Y",
            "https://finance.yahoo.com/quote/%5ETNX/",
        ],
        schema={"type": "object", "properties": {"value": {"type": "number"}}},
        instructions=(
            "Extract the current US 10-Year Treasury note yield as a number "
            "in percent (e.g. 4.32)."
        ),
    )
    value = float(data["value"])
    regime = classify(value, _US10Y_BUCKETS)
    return {
        "value": value,
        "rate_regime": regime["label"],
        "narrative": regime["narrative"],
        "source": data.get("source"),
    }


@register_data_point(
    name="macro_us10y_real",
    category="macro",
    source="context.dev",
    description=(
        "US 10-Year TIPS REAL yield (%) — gold's carrying cost and inverse "
        "driver (gold pays no coupon; when TIPS pay a real return, gold "
        "competes against it). Read live, classified "
        "(negative|low|elevated|high)."
    ),
    params_schema={},
    returns_schema={"value": "float", "real_rate_regime": "string", "source": "string"},
    numeric_path="value",
    tags=["macro", "rates", "real-yield", "gold"],
)
def macro_us10y_real() -> Dict[str, Any]:
    data = extract_value(
        primary_url="https://www.cnbc.com/quotes/US10YTIP",
        fallback_urls=[
            "https://fred.stlouisfed.org/series/DFII10",
            # NO nominal-yield fallback: a plausible wrong number is worse
            # than a loud failure.
        ],
        schema={"type": "object", "properties": {"value": {"type": "number"}}},
        instructions=(
            "Extract the current US 10-Year Treasury Inflation-Protected "
            "Securities (TIPS) REAL yield as a number in percent (e.g. "
            "1.85). This is the inflation-adjusted yield, NOT the nominal "
            "10-year yield."
        ),
    )
    value = float(data["value"])
    regime = classify(value, _US10Y_REAL_BUCKETS)
    return {
        "value": value,
        "real_rate_regime": regime["label"],
        "narrative": regime["narrative"],
        "source": data.get("source"),
    }
