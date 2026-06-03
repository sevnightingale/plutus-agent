"""HALT kill-switch + Telegram trade notifications.

Two hooks:

- ``pre_tool_call``: If ``~/.plutus-agent/HALT`` exists, return
  ``{action: "block", message: ...}`` for any tool that would emit a
  trade or move funds. The dispatcher receives the block and surfaces
  the reason to Plutus instead of executing.

- ``post_tool_call``: For successful trade-emitting tool calls, send a
  one-line Telegram message to the operator's chat. Configured via
  ``notifications.trade_chat_id`` in ``~/.plutus-agent/config.yaml``.

The list of trade-emitting tools is intentionally explicit (not regex)
so additions stay deliberate. Currently:
  place_order, close_position, modify_order, cancel_order,
  acp_wallet_send, dgclaw_trade_open, dgclaw_trade_close.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from plutus_constants import get_hermes_home

logger = logging.getLogger(__name__)


TRADE_TOOLS = frozenset({
    "place_order",
    "close_position",
    "modify_order",
    "cancel_order",
    "acp_wallet_send",
    "dgclaw_trade_open",
    "dgclaw_trade_close",
})


def halt_path() -> Path:
    return get_hermes_home() / "HALT"


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> Optional[Dict[str, Any]]:
    if tool_name not in TRADE_TOOLS:
        return None
    if not halt_path().exists():
        return None

    reason = halt_path().read_text(encoding="utf-8").strip() if halt_path().is_file() else ""
    msg = (
        f"HALT file present at {halt_path()} — operator has paused trading. "
        f"Tool '{tool_name}' refused. Remove the HALT file (or `/resume` "
        f"in Telegram) to release."
    )
    if reason:
        msg += f" Operator's note: {reason}"

    return {"action": "block", "message": msg}


def _format_trade_summary(tool_name: str, args: Dict[str, Any], result: Any) -> str:
    """Produce a one-line operator-facing summary for a successful trade tool."""
    args = args or {}
    base = f"[trade] {tool_name}"
    parts = []
    if "venue" in args:
        parts.append(args["venue"])
    if "symbol" in args:
        parts.append(args["symbol"])
    if "side" in args:
        parts.append(args["side"])
    if "size" in args:
        parts.append(f"size={args['size']}")
    if "conviction" in args:
        parts.append(f"conv={args['conviction']}")
    if "exit_reason" in args:
        parts.append(f"exit={args['exit_reason']}")

    # Try to surface fill_price / pnl from result
    try:
        result_obj = json.loads(result) if isinstance(result, str) else result
        if isinstance(result_obj, dict):
            if result_obj.get("fill_price") is not None:
                parts.append(f"px={result_obj['fill_price']}")
            if result_obj.get("error"):
                parts.append(f"ERROR: {result_obj['error']}")
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    return " ".join([base, "·", *parts]) if parts else base


def _send_telegram(chat_id: str, text: str) -> None:
    """Best-effort Telegram message — uses gateway's bot token + chat id."""
    try:
        import httpx
    except ImportError:
        try:
            import requests as httpx_compat  # type: ignore
            httpx = None
        except ImportError:
            logger.debug("httpx not available; skipping trade notification")
            return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.debug("TELEGRAM_BOT_TOKEN not set; skipping trade notification")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}

    try:
        if httpx is not None:
            resp = httpx.post(url, json=payload, timeout=5.0)
            resp.raise_for_status()
        else:
            httpx_compat.post(url, json=payload, timeout=5.0).raise_for_status()
    except Exception as exc:
        logger.warning("trade-notify Telegram post failed: %s", exc)


def _resolve_trade_chat_id() -> Optional[str]:
    """Read ``notifications.trade_chat_id`` from config.yaml or env."""
    chat_id = os.getenv("PLUTUS_TRADE_CHAT_ID")
    if chat_id:
        return chat_id
    try:
        from plutus_cli.config import load_config
        cfg = load_config()
        return (cfg.get("notifications") or {}).get("trade_chat_id") or None
    except Exception:
        return None


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    **_: Any,
) -> None:
    if tool_name not in TRADE_TOOLS:
        return
    chat_id = _resolve_trade_chat_id()
    if not chat_id:
        return
    summary = _format_trade_summary(tool_name, args or {}, result)
    _send_telegram(chat_id, summary)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
