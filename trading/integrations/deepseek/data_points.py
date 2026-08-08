"""DeepSeek platform data points — prepaid-balance watchdog.

One data point, `deepseek_balance`, fetched by plutus-ops every tick beside
`hl_trade_readiness` and `acp_auth_readiness`. The DeepSeek API is prepaid;
an exhausted balance reproduces the 2026-08-03→05 provider outage (the desk
respawning into a dead provider every staleness tick), so the desk watches
the meter instead of discovering it empty.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict

from trading.perception.core.data_point_registry import register_data_point

logger = logging.getLogger(__name__)

# The platform account endpoint sits on the API host but OUTSIDE /v1.
BALANCE_URL = "https://api.deepseek.com/user/balance"
BALANCE_TIMEOUT_S = 15.0

LOW_BALANCE_USD = 5.0
CRITICAL_BALANCE_USD = 2.0


def _configured_provider() -> str:
    try:
        from harness.cli.config import load_config
        return str((load_config().get("model") or {}).get("provider") or "")
    except Exception:
        return ""


@register_data_point(
    name="deepseek_balance",
    category="account",
    source="deepseek",
    description=(
        "Prepaid balance on the DeepSeek platform account — the model "
        "provider's analogue of hl_trade_readiness. DeepSeek is prepaid, so "
        "an exhausted balance fails every desk spawn in the same shape as a "
        "quota outage. low (<$"
        f"{LOW_BALANCE_USD:g}) and critical (<${CRITICAL_BALANCE_USD:g}) "
        "thresholds are encoded in the verdict; topping up is the "
        "operator's, never the desk's. Computed per call, never persisted."
    ),
    params_schema={},
    returns_schema={
        "fetch_failed": "bool — the balance could not be determined",
        "balance_usd": "float|null — total available balance in USD",
        "is_available": "bool|null — DeepSeek's own serviceability flag",
        "low": f"bool — balance < ${LOW_BALANCE_USD:g}",
        "critical": f"bool — balance < ${CRITICAL_BALANCE_USD:g}",
        "is_current_provider": "bool — model.provider is deepseek right now",
        "reason": "human-readable verdict",
    },
    tags=["account", "deepseek", "provider", "balance", "watchdog"],
)
def deepseek_balance() -> Dict[str, Any]:
    """Return the balance verdict dict. Never raises; failures are encoded."""
    result: Dict[str, Any] = {
        "fetch_failed": True,
        "balance_usd": None,
        "is_available": None,
        "low": False,
        "critical": False,
        "is_current_provider": _configured_provider() == "deepseek",
        "reason": "",
    }

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        result["reason"] = (
            "DEEPSEEK_API_KEY is not set in the runtime env — the balance "
            "cannot be read. If deepseek is the configured provider this is "
            "itself a defect.")
        return result

    req = urllib.request.Request(
        BALANCE_URL, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=BALANCE_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        result["reason"] = f"balance endpoint returned HTTP {exc.code}"
        return result
    except Exception as exc:
        result["reason"] = f"balance fetch failed: {type(exc).__name__}: {exc}"
        return result

    infos = payload.get("balance_infos") or []
    usd = next((i for i in infos if i.get("currency") == "USD"), None)
    if usd is None:
        result["reason"] = (
            f"no USD balance in response (currencies: "
            f"{[i.get('currency') for i in infos]})")
        return result

    try:
        balance = float(usd.get("total_balance"))
    except (TypeError, ValueError):
        result["reason"] = (
            f"unparseable total_balance {usd.get('total_balance')!r}")
        return result

    result["fetch_failed"] = False
    result["balance_usd"] = balance
    result["is_available"] = bool(payload.get("is_available"))
    result["low"] = balance < LOW_BALANCE_USD
    result["critical"] = balance < CRITICAL_BALANCE_USD

    if result["critical"]:
        result["reason"] = (
            f"CRITICAL — ${balance:.2f} left (<${CRITICAL_BALANCE_USD:g}); "
            "the desk goes dark when this hits zero. Operator must top up.")
    elif result["low"]:
        result["reason"] = (
            f"LOW — ${balance:.2f} left (<${LOW_BALANCE_USD:g}); "
            "top up before it becomes an outage.")
    else:
        result["reason"] = f"OK — ${balance:.2f} available."
    return result
