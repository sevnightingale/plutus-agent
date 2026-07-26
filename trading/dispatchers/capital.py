"""capital_reconcile (toolset: resolution) — ops's capital bookkeeping.

Deliberately NOT a "record a deposit" tool. Agents narrating financial events
is how ``capital_movements`` stayed empty while equity tripled: the table had
no writer, and even with one, an agent that must remember to write a row will
eventually not. The venue already knows; the desk just has to ask.

Lives in the ``resolution`` toolset because that is ops's — the tick that
already resolves predictions and syncs live state, the cheapest mind on the
desk, and one that never interprets. Reconciliation is exactly that shape:
fetch, compare, write, report. There is no judgement anywhere in it.
"""

from __future__ import annotations

from typing import Any, Dict

from harness.tools.registry import registry, tool_error, tool_result

CAPITAL_RECONCILE_SCHEMA = {
    "name": "capital_reconcile",
    "description": (
        "Reconcile lifecycle.db's capital_movements against the venue's own "
        "deposit/withdrawal ledger, and report the desk's lifetime P&L "
        "against the capital it was handed. Idempotent — safe to call every "
        "tick; after the first pass it inserts nothing. Call it on every tick "
        "and report `inserted` in your ops_report when nonzero: a new "
        "movement means the operator funded or drew down the account, which "
        "changes what every performance number means."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "account_name": {
                "type": "string",
                "description": "Venue account to reconcile (default hl_trading).",
            },
        },
    },
}


def _capital_reconcile(args: Dict[str, Any]) -> str:
    from trading.dispatchers._helpers import session_id_from_context
    from trading.lifecycle import capital
    from trading.lifecycle.db import get_db

    account_name = args.get("account_name") or "hl_trading"
    conn = get_db()
    try:
        result = capital.reconcile_capital_movements(
            conn, account_name=account_name,
            session_name=session_id_from_context())
    except Exception as exc:
        return tool_error(f"capital_reconcile failed: {type(exc).__name__}: {exc}")

    if not result.get("ok"):
        # An unreachable venue is reported as a failure, not as zero deposits.
        return tool_error(
            f"capital_reconcile could not read the venue ledger: "
            f"{result.get('error')}")

    pnl = capital.lifetime_pnl(conn, account_name=account_name)
    return tool_result({**result, "lifetime": pnl})


registry.register(
    name="capital_reconcile",
    toolset="resolution",
    schema=CAPITAL_RECONCILE_SCHEMA,
    handler=lambda args, **kw: _capital_reconcile(args),
    description="Reconcile capital_movements against the venue ledger; report lifetime P&L.",
    emoji="🏦",
)
