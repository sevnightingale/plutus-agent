"""account_state — read venue account state and surface diffs (perception).

Dispatches to the venue's ``account_state_fn``. When ``venue`` is omitted,
aggregates across every registered venue. Does NOT auto-write capital
movements or holdings to lifecycle.db — surfacing state is the perception
step; recording capital movements is an explicit ``record_event`` call by
the agent (atomic with the operation that moved them).
"""

from __future__ import annotations

from typing import Any, Dict

from trading.perception.core import venue_registry
from harness.tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "account_state",
    "description": (
        "Read the current account state at a trading venue (positions, "
        "open orders, balances). Pass venue='hyperliquid' for a specific "
        "venue, or omit to aggregate across all registered venues. "
        "Returns a snapshot dict; does not auto-record any lifecycle events. "
        "How to read the numbers (unified account, TRADING.md money "
        "glossary): equity_usd = spot_usdc + perp_account_value and is THE "
        "account-worth number — sizing, snapshots, and drawdown all use it. "
        "perp_account_value ≈ 0 when flat is NORMAL (display, not missing "
        "funds; never transfer spot→perp). withdrawable_usd is what could "
        "leave the venue right now. A healthy equity does NOT prove trading "
        "works — only hl_trade_readiness does."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "venue": {
                "type": "string",
                "description": "Venue name (e.g., 'hyperliquid'). Omit for all venues.",
            },
        },
    },
}


def _account_state(args: Dict[str, Any]) -> str:
    venue_name = args.get("venue")
    venues = venue_registry.list_all()

    if venue_name:
        try:
            entry = venue_registry.lookup(venue_name)
        except KeyError as exc:
            return tool_error(str(exc))
        if not entry.account_state_fn:
            return tool_error(
                f"venue '{venue_name}' has no account_state_fn registered"
            )
        try:
            return tool_result({"venue": venue_name, "state": entry.account_state_fn()})
        except Exception as exc:
            return tool_error(f"account_state failed for {venue_name}: {exc}")

    # No venue specified — aggregate.
    if not venues:
        return tool_error(
            "no venues registered; account_state has nothing to read"
        )

    out: Dict[str, Any] = {}
    for v in venues:
        if not v.account_state_fn:
            continue
        try:
            out[v.name] = v.account_state_fn()
        except Exception as exc:
            out[v.name] = {"error": str(exc)}
    return tool_result({"venues": list(out.keys()), "states": out})


registry.register(
    name="account_state",
    toolset="perception",
    schema=SCHEMA,
    handler=lambda args, **kw: _account_state(args),
    description="Read venue account state (positions/orders/balances).",
    emoji="🏦",
)
