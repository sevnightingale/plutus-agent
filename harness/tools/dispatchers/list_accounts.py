"""list_accounts — discovery tool for the account registry."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from harness.tools.core import account_registry
from harness.tools.registry import registry, tool_result


SCHEMA = {
    "name": "list_accounts",
    "description": (
        "List registered accounts (HL trading account, ACP wallets, cold "
        "storage, etc.). Filter by purpose (trading_capital | treasury | "
        "spot_holding | staking | lp | cold_storage), venue, or chain. "
        "Holdings/balances are fetched separately as data points; this tool "
        "is for the catalog of WHAT accounts exist and their roles."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": {"type": "string"},
            "venue":   {"type": "string"},
            "chain":   {"type": "string"},
        },
    },
}


def _list_accounts(args: Dict[str, Any]) -> str:
    entries = account_registry.list_all(
        purpose=args.get("purpose"),
        venue=args.get("venue"),
        chain=args.get("chain"),
    )
    return tool_result({
        "count": len(entries),
        "entries": [asdict(e) for e in entries],
    })


registry.register(
    name="list_accounts",
    toolset="identity",
    schema=SCHEMA,
    handler=lambda args, **kw: _list_accounts(args),
    description="Enumerate registered accounts.",
    emoji="🪪",
)
