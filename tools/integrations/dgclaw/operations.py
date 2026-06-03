"""dgclaw operations — forum posts + dgclaw-routed trades.

Verified against `bash dgclaw.sh --help` (v4.0) and
`npx tsx scripts/trade.ts --help`:

- `dgclaw.sh create-post <agentId> <threadId> <title> <content>` —
  creates a post in a thread. There is no separate "reply" subcommand;
  replying to a thread IS creating a post in that thread.
- `dgclaw.sh setup-cron <agentId>` / `remove-cron <agentId>` — auto-reply
  cron management; not wrapped here (Plutus uses Hermes's own cron).
- `scripts/trade.ts open --pair <symbol> --side <long|short> --size <usd>
  --leverage <n> --type <market|limit> --limit-price <px> --sl <px> --tp <px>`
- `scripts/trade.ts close --pair <symbol>`
- `scripts/trade.ts modify --pair <symbol> [--leverage --sl --tp]`
- `scripts/trade.ts positions` / `balance` / `tickers` — no args

Note: `--size` for `trade.ts open` is in **USD notional**, not native units.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from tools.registry import registry, tool_error, tool_result

from . import _cli

logger = logging.getLogger(__name__)


# ─── forum ────────────────────────────────────────────────────────────────


_FORUM_CREATE_SCHEMA = {
    "name": "dgclaw_forum_create_post",
    "description": (
        "Create a post in an agent's forum thread (this is also how you "
        "reply — there's no separate 'reply' subcommand in dgclaw.sh). "
        "agent_id and thread_id are positional ids from the leaderboard / "
        "forum data points."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_id":  {"type": "string"},
            "thread_id": {"type": "string"},
            "title":     {"type": "string"},
            "content":   {"type": "string"},
        },
        "required": ["agent_id", "thread_id", "title", "content"],
    },
}


def _dgclaw_forum_create_post(args: Dict[str, Any]) -> str:
    agent_id = args.get("agent_id")
    thread_id = args.get("thread_id")
    title = args.get("title")
    content = args.get("content")
    if not all([agent_id, thread_id, title, content]):
        return tool_error("agent_id, thread_id, title, content required")
    try:
        result = _cli.dgclaw(
            "create-post",
            str(agent_id), str(thread_id),
            str(title), str(content),
        )
    except Exception as exc:
        return tool_error(f"dgclaw create-post failed: {exc}")
    return tool_result(result)


registry.register(
    name="dgclaw_forum_create_post",
    toolset="execution",
    schema=_FORUM_CREATE_SCHEMA,
    handler=lambda args, **kw: _dgclaw_forum_create_post(args),
    description="Create a dgclaw forum post (also how you reply to threads).",
    emoji="📝",
)


# ─── trade routing (alternative to direct HL place_order) ─────────────────


_TRADE_OPEN_SCHEMA = {
    "name": "dgclaw_trade_open",
    "description": (
        "Open a position via dgclaw's trade routing (alternative to direct "
        "HL place_order). Use when the leaderboard explicitly requires "
        "dgclaw-routed trades; otherwise prefer place_order(venue='hyperliquid'). "
        "Note: --size is USD NOTIONAL, not native units. "
        "BRACKET CAVEAT: this tool passes sl/tp through to dgclaw-skill's "
        "scripts/trade.ts. Whether trade.ts actually places on-chain HL "
        "trigger orders (vs. just recording intent client-side) is upstream "
        "behavior we don't control. The HL-native path "
        "(place_order(venue='hyperliquid', sl=..., tp=...)) places atomic "
        "bracket trigger orders via bulk_orders(grouping='normalTpsl') and "
        "is verified end-to-end. If you must use this path AND want "
        "guaranteed protection, place reduce-only trigger orders separately "
        "via place_order(reduce_only=True, ...) after the entry fills."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pair":        {"type": "string", "description": "BTC, ETH, SOL, xyz:TSLA, etc."},
            "side":        {"type": "string", "enum": ["long", "short"]},
            "size":        {"type": "number", "description": "USD notional size."},
            "leverage":    {"type": "number", "default": 1},
            "order_type":  {"type": "string", "enum": ["market", "limit"]},
            "limit_price": {"type": "number"},
            "sl":          {"type": "number"},
            "tp":          {"type": "number"},
        },
        "required": ["pair", "side", "size"],
    },
}


def _dgclaw_trade_open(args: Dict[str, Any]) -> str:
    pair = args.get("pair")
    side = args.get("side")
    size = args.get("size")
    if not all([pair, side, size is not None]):
        return tool_error("pair, side, size required")

    cmd_args = ["open",
                "--pair", str(pair),
                "--side", str(side),
                "--size", str(size)]
    if args.get("leverage") is not None:
        cmd_args.extend(["--leverage", str(args["leverage"])])
    if args.get("order_type"):
        cmd_args.extend(["--type", str(args["order_type"])])
    if args.get("limit_price") is not None:
        cmd_args.extend(["--limit-price", str(args["limit_price"])])
    if args.get("sl") is not None:
        cmd_args.extend(["--sl", str(args["sl"])])
    if args.get("tp") is not None:
        cmd_args.extend(["--tp", str(args["tp"])])

    try:
        result = _cli.dgclaw_trade(*cmd_args)
    except Exception as exc:
        return tool_error(f"dgclaw_trade_open failed: {exc}")
    return tool_result(result)


registry.register(
    name="dgclaw_trade_open",
    toolset="execution",
    schema=_TRADE_OPEN_SCHEMA,
    handler=lambda args, **kw: _dgclaw_trade_open(args),
    description="Open a perp position via dgclaw's trade routing.",
    emoji="🚀",
)


_TRADE_CLOSE_SCHEMA = {
    "name": "dgclaw_trade_close",
    "description": "Close an open position via dgclaw's trade routing.",
    "parameters": {
        "type": "object",
        "properties": {"pair": {"type": "string"}},
        "required": ["pair"],
    },
}


def _dgclaw_trade_close(args: Dict[str, Any]) -> str:
    pair = args.get("pair")
    if not pair:
        return tool_error("pair required")
    try:
        result = _cli.dgclaw_trade("close", "--pair", str(pair))
    except Exception as exc:
        return tool_error(f"dgclaw_trade_close failed: {exc}")
    return tool_result(result)


registry.register(
    name="dgclaw_trade_close",
    toolset="execution",
    schema=_TRADE_CLOSE_SCHEMA,
    handler=lambda args, **kw: _dgclaw_trade_close(args),
    description="Close a position via dgclaw's trade routing.",
    emoji="🛑",
)


_TRADE_POSITIONS_SCHEMA = {
    "name": "dgclaw_trade_positions",
    "description": "List open positions per dgclaw's trade view.",
    "parameters": {"type": "object", "properties": {}},
}


def _dgclaw_trade_positions(args: Dict[str, Any]) -> str:
    try:
        return tool_result(_cli.dgclaw_trade("positions"))
    except Exception as exc:
        return tool_error(f"dgclaw_trade_positions failed: {exc}")


registry.register(
    name="dgclaw_trade_positions",
    toolset="perception",
    schema=_TRADE_POSITIONS_SCHEMA,
    handler=lambda args, **kw: _dgclaw_trade_positions(args),
    description="List open positions per dgclaw.",
    emoji="📊",
)


_TRADE_BALANCE_SCHEMA = {
    "name": "dgclaw_trade_balance",
    "description": "HL spot + perp account balance via dgclaw (unified mode).",
    "parameters": {"type": "object", "properties": {}},
}


def _dgclaw_trade_balance(args: Dict[str, Any]) -> str:
    try:
        return tool_result(_cli.dgclaw_trade("balance"))
    except Exception as exc:
        return tool_error(f"dgclaw_trade_balance failed: {exc}")


registry.register(
    name="dgclaw_trade_balance",
    toolset="perception",
    schema=_TRADE_BALANCE_SCHEMA,
    handler=lambda args, **kw: _dgclaw_trade_balance(args),
    description="HL account balance via dgclaw.",
    emoji="💰",
)


_TRADE_TICKERS_SCHEMA = {
    "name": "dgclaw_trade_tickers",
    "description": "List available trading pairs on Hyperliquid (BTC, ETH, SOL, xyz:* HIP-3 perps, etc.).",
    "parameters": {"type": "object", "properties": {}},
}


def _dgclaw_trade_tickers(args: Dict[str, Any]) -> str:
    try:
        return tool_result(_cli.dgclaw_trade("tickers"))
    except Exception as exc:
        return tool_error(f"dgclaw_trade_tickers failed: {exc}")


registry.register(
    name="dgclaw_trade_tickers",
    toolset="perception",
    schema=_TRADE_TICKERS_SCHEMA,
    handler=lambda args, **kw: _dgclaw_trade_tickers(args),
    description="List available HL trading pairs via dgclaw.",
    emoji="📋",
)
