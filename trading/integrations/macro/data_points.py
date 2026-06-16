"""Macro data points — agentic query blueprints for web-sourced economic data.

Unlike direct-HTTP data points (CoinGecko, DefiLlama) which return raw data,
these return *instructions* for the agent to execute with its native
web_search / web_extract tools. The agent calls fetch_data_point, gets back
a query blueprint, executes it, and folds the result into its reasoning.

The _type field distinguishes agentic queries from direct data so the agent
knows to execute rather than consume.

Auto-snapshots capture the blueprint (what was asked). The agent records
actual observed values separately via record_event.
"""

from __future__ import annotations

from typing import Any, Dict

from trading.perception.core.data_point_registry import register_data_point


@register_data_point(
    name="macro_vix",
    category="macro",
    source="web_search",
    description=(
        "CBOE Volatility Index (VIX) — the market's 'fear gauge.' "
        "Returns a query blueprint: web_search for current VIX, extract "
        "the number from MarketWatch, classify risk regime."
    ),
    params_schema={},
    tags=["macro", "volatility", "risk", "agentic"],
)
def macro_vix() -> Dict[str, Any]:
    return {
        "_type": "agentic_query",
        "description": "CBOE Volatility Index — measures expected S&P 500 volatility via options prices.",
        "search": "current VIX level today",
        "primary_source": "https://www.marketwatch.com/investing/index/vix",
        "fallback_sources": [
            "https://www.cnbc.com/quotes/.VIX",
            "https://finance.yahoo.com/quote/%5EVIX/",
        ],
        "extract_hint": "Look for 'Last Price' or 'VIX' followed by a number like 17.35.",
        "classify": {
            "field": "risk_regime",
            "buckets": [
                {"range": [0, 14.99], "label": "low_volatility", "narrative": "Complacency — markets expect calm. Trend continuation likely."},
                {"range": [15, 19.99], "label": "moderate", "narrative": "Normal — no fear premium. Standard risk-taking environment."},
                {"range": [20, 29.99], "label": "elevated", "narrative": "Caution — hedging demand rising. Tighten stops, reduce size."},
                {"range": [30, 100], "label": "extreme", "narrative": "Fear/panic — expect violent moves. Defensive posture warranted."},
            ],
        },
        "ttl_hint": "Check every 4 hours. VIX is slow-moving context, not a trigger.",
        "output_schema": {
            "value": "float (VIX level)",
            "risk_regime": "string (low_volatility|moderate|elevated|extreme)",
            "trend": "string (rising|falling|flat — vs prior reads)",
            "source": "string (URL used)",
        },
    }


@register_data_point(
    name="macro_dxy",
    category="macro",
    source="web_search",
    description=(
        "US Dollar Index (DXY) — measures USD strength vs basket of currencies. "
        "DXY rising = risk-off / crypto headwind. DXY falling = risk-on / crypto tailwind."
    ),
    params_schema={},
    tags=["macro", "forex", "dollar", "agentic"],
)
def macro_dxy() -> Dict[str, Any]:
    return {
        "_type": "agentic_query",
        "description": "US Dollar Index — basket of EUR, JPY, GBP, CAD, SEK, CHF vs USD.",
        "search": "current DXY US dollar index level today",
        "primary_source": "https://www.marketwatch.com/investing/index/dxy",
        "fallback_sources": [
            "https://www.cnbc.com/quotes/.DXY",
            "https://www.tradingview.com/symbols/TVC-DXY/",
        ],
        "extract_hint": "Look for 'Last Price' followed by a number like 98.45.",
        "classify": {
            "field": "strength",
            "buckets": [
                {"range": [0, 95], "label": "weak", "narrative": "USD weak — risk-on tailwind for crypto."},
                {"range": [95, 100], "label": "neutral", "narrative": "USD in normal range. No directional signal."},
                {"range": [100, 105], "label": "strong", "narrative": "USD strengthening — risk-off headwind for crypto."},
                {"range": [105, 200], "label": "extreme", "narrative": "USD extremely strong — liquidity squeeze risk."},
            ],
        },
        "ttl_hint": "Check every 4-8 hours. DXY moves slowly relative to crypto.",
        "output_schema": {
            "value": "float (DXY level)",
            "strength": "string (weak|neutral|strong|extreme)",
            "trend": "string (rising|falling|flat — vs prior reads)",
            "source": "string (URL used)",
        },
    }


@register_data_point(
    name="macro_cpi",
    category="macro",
    source="web_search",
    description=(
        "US Consumer Price Index — latest inflation reading. Rising CPI = Fed "
        "hawkish = risk-off. Falling CPI = Fed dovish = risk-on. "
        "Monthly data — check after BLS release (mid-month)."
    ),
    params_schema={},
    tags=["macro", "inflation", "fed", "agentic"],
)
def macro_cpi() -> Dict[str, Any]:
    return {
        "_type": "agentic_query",
        "description": "US Consumer Price Index — headline year-over-year inflation rate.",
        "search": "latest CPI inflation rate United States",
        "primary_source": "https://www.bls.gov/news.release/cpi.nr0.htm",
        "fallback_sources": [
            "https://tradingeconomics.com/united-states/inflation-cpi",
            "https://www.usinflationcalculator.com/inflation/current-inflation-rates/",
        ],
        "extract_hint": (
            "Look for 'rose X.X percent for the 12 months ending [Month]' or "
            "'inflation rate' followed by a percentage like 3.3%."
        ),
        "classify": {
            "field": "regime",
            "buckets": [
                {"range": [0, 2.5], "label": "low", "narrative": "Below Fed target range. Dovish — rate cuts likely. Risk-on."},
                {"range": [2.5, 3.5], "label": "moderate", "narrative": "Near Fed target. Neutral — data-dependent. Watch labor market."},
                {"range": [3.5, 5.0], "label": "elevated", "narrative": "Above target. Hawkish — rates higher for longer. Risk-off pressure."},
                {"range": [5.0, 100], "label": "extreme", "narrative": "Inflation crisis. Aggressive tightening. Severe risk-off."},
            ],
        },
        "ttl_hint": "Check monthly (after BLS release ~15th). CPI changes slowly.",
        "output_schema": {
            "value": "float (headline CPI year-over-year %)",
            "release_date": "string (month of data, e.g. 'March 2026')",
            "previous_value": "float (prior month's reading)",
            "regime": "string (low|moderate|elevated|extreme)",
            "source": "string (URL used)",
        },
    }


@register_data_point(
    name="btc_etf_netflow_daily",
    category="macro",
    source="web_search",
    description=(
        "Daily aggregate net flow for US spot Bitcoin ETFs in USD. "
        "Positive = net inflows (institutional buying pressure). "
        "Negative = net outflows (distribution). Sourced from Farside "
        "Investors, SoSoValue, or CoinGlass — all publish next-day T+1 data. "
        "Returns a query blueprint: web_search for today's/latest BTC ETF "
        "flow, extract the aggregate number, classify flow regime."
    ),
    params_schema={},
    tags=["macro", "etf", "flows", "institutional", "agentic"],
)
def btc_etf_netflow_daily() -> Dict[str, Any]:
    return {
        "_type": "agentic_query",
        "description": (
            "Aggregate daily net flow (in USD millions) across all US spot "
            "Bitcoin ETFs (IBIT, FBTC, GBTC, ARKB, BITB, etc.). "
            "T+1 reporting: today's data is for the prior trading day."
        ),
        "search": "BTC ETF net flow daily USD millions today",
        "primary_source": "https://farside.co.uk/btc/",
        "fallback_sources": [
            "https://sosovalue.com/assets/btc",
            "https://www.coinglass.com/bitcoin-etf",
        ],
        "extract_hint": (
            "Look for 'Total Net Flow', 'Aggregate', or 'All ETFs' row. "
            "Number is in USD millions — e.g. '+$245.3M' (inflow) or "
            "'-$89.7M' (outflow). Farside shows 'Total' at the table bottom. "
            "SoSoValue shows a headline aggregate number."
        ),
        "classify": {
            "field": "flow_regime",
            "buckets": [
                {
                    "range": [-5000, -500],
                    "label": "heavy_outflows",
                    "narrative": (
                        ">$500M/day outflows — sustained institutional "
                        "distribution. Strong risk-off signal."
                    ),
                },
                {
                    "range": [-500, -100],
                    "label": "moderate_outflows",
                    "narrative": (
                        "$100-500M/day outflows — mild distribution pressure. "
                        "Bearish but not extreme."
                    ),
                },
                {
                    "range": [-100, 100],
                    "label": "flat",
                    "narrative": (
                        "Flows near zero — no directional institutional signal. "
                        "ETF capital is sidelined."
                    ),
                },
                {
                    "range": [100, 500],
                    "label": "moderate_inflows",
                    "narrative": (
                        "$100-500M/day inflows — steady accumulation. "
                        "Bullish institutional tailwind."
                    ),
                },
                {
                    "range": [500, 5000],
                    "label": "heavy_inflows",
                    "narrative": (
                        ">$500M/day inflows — aggressive institutional buying. "
                        "Strong risk-on signal."
                    ),
                },
            ],
        },
        "ttl_hint": (
            "Check daily after US market close (21:00Z+). "
            "ETF flow data publishes T+1 — Monday's flow is available "
            "Tuesday morning. Weekend flows are zero (markets closed)."
        ),
        "output_schema": {
            "value": "float (net flow in USD millions, negative = outflow)",
            "flow_regime": (
                "string (heavy_outflows|moderate_outflows|flat|"
                "moderate_inflows|heavy_inflows)"
            ),
            "date": "string (date of the flow data, e.g. '2026-06-15')",
            "consecutive_days": (
                "integer — same-direction days in current streak. "
                "3+ days above $300M = directional regime signal"
            ),
            "source": "string (URL used)",
        },
    }
