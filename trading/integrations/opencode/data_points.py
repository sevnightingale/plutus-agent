"""OpenCode Zen (Go) data points — subscription-quota watchdog.

One data point, `opencode_go_usage`, fetched by plutus-ops every tick beside
`deepseek_balance`. The opencode-go route is a SUBSCRIPTION with quota
windows, not a prepaid balance — its exhaustion is what benched the desk for
two weeks in early August 2026 (the staleness ceiling respawning into a dead
provider every five minutes), so the desk watches the meter instead of
discovering it empty.

The endpoint is undocumented but sits on the inference base URL itself
(discovered 2026-08-24): ``GET {base}/usage`` with the same bearer key
returns three windows (rolling / weekly / monthly), each a percent-used and
a reset time. Monthly is the one that took the desk down.
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

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
USAGE_TIMEOUT_S = 15.0

WARN_PERCENT = 80
CRITICAL_PERCENT = 95

_WINDOWS = ("rolling", "weekly", "monthly")


def _configured_provider() -> str:
    try:
        from harness.cli.config import load_config
        return str((load_config().get("model") or {}).get("provider") or "")
    except Exception:
        return ""


@register_data_point(
    name="opencode_go_usage",
    category="account",
    source="opencode-go",
    description=(
        "Subscription-quota usage on the OpenCode Zen (Go) plan — the model "
        "provider's analogue of hl_trade_readiness for the opencode-go "
        "route. The plan is quota-windowed (rolling/weekly/monthly percent "
        "used), and an exhausted window fails every desk spawn in the shape "
        f"of the 2026-08-03→05 outage. warn (≥{WARN_PERCENT}%) and critical "
        f"(≥{CRITICAL_PERCENT}%) on ANY window are encoded in the verdict; "
        "the reset times say how long the pain lasts. Computed per call, "
        "never persisted."
    ),
    params_schema={},
    returns_schema={
        "fetch_failed": "bool — the usage could not be determined",
        "rolling_percent": "float|null — rolling-window percent used",
        "weekly_percent": "float|null — weekly-window percent used",
        "monthly_percent": "float|null — monthly-window percent used",
        "max_window": "string|null — the window nearest exhaustion",
        "max_percent": "float|null — that window's percent used",
        "resets": "object — {window: ISO reset time}",
        "low": f"bool — any window ≥ {WARN_PERCENT}%",
        "critical": f"bool — any window ≥ {CRITICAL_PERCENT}%",
        "is_current_provider": "bool — model.provider is opencode-go right now",
        "reason": "human-readable verdict",
    },
    tags=["account", "opencode", "provider", "quota", "watchdog"],
)
def opencode_go_usage() -> Dict[str, Any]:
    """Return the quota verdict dict. Never raises; failures are encoded."""
    result: Dict[str, Any] = {
        "fetch_failed": True,
        "rolling_percent": None,
        "weekly_percent": None,
        "monthly_percent": None,
        "max_window": None,
        "max_percent": None,
        "resets": {},
        "low": False,
        "critical": False,
        "is_current_provider": _configured_provider() == "opencode-go",
        "reason": "",
    }

    api_key = os.getenv("OPENCODE_GO_API_KEY", "").strip()
    if not api_key:
        result["reason"] = (
            "OPENCODE_GO_API_KEY is not set in the runtime env — the quota "
            "cannot be read. If opencode-go is the configured provider this "
            "is itself a defect.")
        return result

    # The UA matters: Cloudflare fronts opencode.ai and 403s the default
    # Python-urllib agent while accepting anything browser-shaped
    # (measured 2026-08-24 — curl 200, bare urllib 403, this UA 200).
    req = urllib.request.Request(
        USAGE_URL, headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) plutus-agent/1.0",
            "Accept": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=USAGE_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        result["reason"] = f"usage endpoint returned HTTP {exc.code}"
        return result
    except Exception as exc:
        result["reason"] = f"usage fetch failed: {type(exc).__name__}: {exc}"
        return result

    windows = (payload.get("usage") or {}) if isinstance(payload, dict) else {}
    percents: Dict[str, float] = {}
    for w in _WINDOWS:
        blk = windows.get(w) or {}
        pct = blk.get("percent")
        if isinstance(pct, (int, float)):
            percents[w] = float(pct)
            result[f"{w}_percent"] = float(pct)
        if blk.get("resetsAt"):
            result["resets"][w] = blk["resetsAt"]

    if not percents:
        result["reason"] = (
            f"no usage windows in response (keys: {sorted(windows)})")
        return result

    max_window = max(percents, key=percents.get)
    max_percent = percents[max_window]
    result["fetch_failed"] = False
    result["max_window"] = max_window
    result["max_percent"] = max_percent
    result["low"] = max_percent >= WARN_PERCENT
    result["critical"] = max_percent >= CRITICAL_PERCENT

    summary = ", ".join(
        f"{w} {percents[w]:g}%" for w in _WINDOWS if w in percents)
    reset = result["resets"].get(max_window, "unknown")
    if result["critical"]:
        result["reason"] = (
            f"CRITICAL — {max_window} window at {max_percent:g}% "
            f"(≥{CRITICAL_PERCENT}%); the desk goes dark at 100%. Resets "
            f"{reset}. Operator decides: wait it out or switch provider.")
    elif result["low"]:
        result["reason"] = (
            f"WARN — {max_window} window at {max_percent:g}% "
            f"(≥{WARN_PERCENT}%); resets {reset}. Pace or plan the "
            "provider fallback before it becomes an outage.")
    else:
        result["reason"] = f"OK — {summary}."
    return result
