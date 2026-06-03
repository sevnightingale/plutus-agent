"""place_trigger — execution dispatcher.

Places a standalone reduce-only trigger order (stop-loss or take-profit) on
an existing position. Complements ``place_order``, which only handles
market/limit entries and bracket-mode SL+TP atomically with the entry.

Use this when:
  * The atomic bracket failed for one leg (e.g. validation rejected TP due
    to the inflated entry estimate from ``_slippage_price``) and the
    position now needs that leg added post-hoc on-chain.
  * A position was opened without sl/tp params and now needs protection.
  * Path B in the hl-risk-placement skill needs a real trigger order — the
    skill previously said this was impossible because ``place_order``
    standalone only supports market/limit; this dispatcher fixes that gap.
"""

from __future__ import annotations

from typing import Any, Dict

from tools.core import venue_registry
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "place_trigger",
    "description": (
        "Place a standalone reduce-only trigger order (stop-loss or "
        "take-profit) on an existing position at a venue. The trigger "
        "lands on-chain immediately. Use when the atomic-bracket path in "
        "place_order failed for one leg, or when adding protection to a "
        "position that was opened without sl/tp. For Hyperliquid: trigger "
        "fires at trigger_px; fill happens within slippage of trigger."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "venue": {
                "type": "string",
                "description": "Venue name, e.g. 'hyperliquid'.",
            },
            "symbol": {
                "type": "string",
                "description": "Coin symbol, e.g. 'BTC', 'ETH', 'HYPE'.",
            },
            "position_side": {
                "type": "string",
                "enum": ["long", "short"],
                "description": (
                    "Side of the position being protected. The trigger's "
                    "close direction is the opposite (long position → "
                    "sell-side trigger; short position → buy-side trigger)."
                ),
            },
            "size": {
                "type": "number",
                "description": (
                    "Position size in coin units. Should match the open "
                    "position's |szi| so the trigger fully closes."
                ),
            },
            "trigger_px": {
                "type": "number",
                "description": (
                    "Price at which the trigger fires. For long-SL: "
                    "below market; long-TP: above market; mirror for short."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["sl", "tp"],
                "default": "sl",
                "description": (
                    "'sl' for stop-loss, 'tp' for take-profit. Sets HL's "
                    "tpsl classification on the trigger order."
                ),
            },
            "slippage": {
                "type": "number",
                "description": (
                    "Worst-acceptable-execution padding for the limit_px "
                    "companion field. Default 5% — matches the bracket "
                    "path. Trigger fires at trigger_px; fill happens "
                    "within slippage of that."
                ),
            },
        },
        "required": ["venue", "symbol", "position_side", "size", "trigger_px"],
    },
}


def _place_trigger(args: Dict[str, Any]) -> str:
    venue = args.get("venue")
    if not venue:
        return tool_error("place_trigger requires venue")

    try:
        v = venue_registry.lookup(venue)
    except KeyError as exc:
        return tool_error(str(exc))
    if not v.place_trigger_fn:
        return tool_error(
            f"venue '{venue}' has no place_trigger_fn registered"
        )

    fn_args = {k: v_ for k, v_ in args.items() if k != "venue"}

    try:
        result = v.place_trigger_fn(**fn_args)
    except Exception as exc:
        return tool_error(
            f"venue '{venue}' place_trigger_fn raised: {exc}"
        )

    return tool_result({"venue": venue, "result": result})


registry.register(
    name="place_trigger",
    toolset="execution",
    schema=SCHEMA,
    handler=lambda args, **kw: _place_trigger(args),
    description=(
        "Place a standalone reduce-only trigger (SL/TP) on an existing "
        "position."
    ),
    emoji="🎯",
)
