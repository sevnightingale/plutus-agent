"""Capital reconciliation — the desk's record of money in and money out.

``capital_movements`` has existed in the schema since the beginning and had
NO writer: ``grep -rn capital_movements`` over the tree returned exactly one
hit, its own CREATE TABLE. The same defect class as ``reflections`` before
2026-07-21 — a table nothing could reach, whose emptiness read as "nothing
happened" rather than "nothing can be written here". The live runtime held 0
rows while equity had moved from $23.99 to $75.12.

The consequence is not bookkeeping neatness. Without capital movements the
desk cannot state its own P&L at all: equity rises for two unrelated reasons,
trading well and being topped up, and nothing in lifecycle.db distinguished
them. Every performance figure was gross of unknown deposits.

The fix is a RECONCILER, not a hand-recording tool. Hyperliquid publishes the
account's own ledger, so movements are fetched and upserted on the venue's tx
hash rather than narrated by an agent — the desk's standing design law that
records belong to code, applied to the one table that never got it. Running
it on every ops tick is safe and cheap: after the first pass it inserts
nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def reconcile_capital_movements(conn, account_name: str = "hl_trading",
                                session_name: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the venue ledger and upsert anything not already recorded.

    Returns ``{ok, inserted, seen, gross_deposits_usd, gross_withdrawals_usd,
    net_deposits_usd, unparsed}``. A venue read failure surfaces as
    ``ok=False`` with the error — never as a silent zero, which would read
    downstream as "no deposits ever" and make the P&L wrong in the flattering
    direction.
    """
    from trading.lifecycle import write

    try:
        from trading.integrations.hyperliquid.venue import hl_capital_ledger
        movements = hl_capital_ledger(account_name)
    except Exception as exc:
        logger.warning("capital reconcile: venue read failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "inserted": 0, "seen": 0}

    inserted = 0
    unparsed = 0
    for m in movements:
        if m.get("amount_token") is None:
            unparsed += 1
            continue
        row_id = write.record_capital_movement(
            conn,
            ts=m["ts"],
            token=m["token"],
            amount_token=m["amount_token"],
            amount_usd_at_time=m.get("amount_usd_at_time"),
            movement_type=m["movement_type"],
            from_account=m.get("from_account"),
            to_account=m.get("to_account"),
            tx_hash=m.get("tx_hash"),
            note=m.get("note"),
            session_name=session_name,
        )
        if row_id is not None:
            inserted += 1

    totals = net_deposits(conn)
    return {
        "ok": True,
        "inserted": inserted,
        "seen": len(movements),
        "unparsed": unparsed,
        **totals,
    }


def net_deposits(conn) -> Dict[str, Any]:
    """Capital in, capital out, and the net the desk was handed.

    ``net_deposits_usd`` is the denominator every honest performance figure
    needs: equity above it is profit, equity below it is loss, and neither is
    knowable from equity alone.
    """
    row = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN COALESCE(amount_usd_at_time, amount_token) > 0
                          THEN COALESCE(amount_usd_at_time, amount_token) END), 0.0),
             COALESCE(SUM(CASE WHEN COALESCE(amount_usd_at_time, amount_token) < 0
                          THEN COALESCE(amount_usd_at_time, amount_token) END), 0.0),
             COUNT(*)
           FROM capital_movements"""
    ).fetchone()
    gross_in, gross_out, n = float(row[0]), float(row[1]), int(row[2])
    return {
        "gross_deposits_usd": round(gross_in, 6),
        "gross_withdrawals_usd": round(gross_out, 6),
        "net_deposits_usd": round(gross_in + gross_out, 6),
        "movement_count": n,
    }


def lifetime_pnl(conn, equity_usd: Optional[float] = None,
                 account_name: str = "hl_trading") -> Dict[str, Any]:
    """Equity measured against the capital that was put in.

    Kept separate from ``net_deposits`` because it needs a live equity read
    and can therefore fail; callers that only want the denominator should not
    have to touch the venue.
    """
    totals = net_deposits(conn)
    if equity_usd is None:
        try:
            from trading.integrations.hyperliquid.venue import hl_account_state
            equity_usd = float(hl_account_state(account_name)["equity_usd"])
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", **totals}

    net_in = totals["net_deposits_usd"]
    pnl = equity_usd - net_in
    return {
        "ok": True,
        "equity_usd": round(equity_usd, 6),
        "pnl_usd": round(pnl, 6),
        "pnl_pct_of_capital": round(100.0 * pnl / net_in, 4) if net_in else None,
        **totals,
    }
