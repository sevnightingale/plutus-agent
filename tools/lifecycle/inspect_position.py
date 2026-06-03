"""inspect_position — full causal chain for one position."""

from __future__ import annotations

from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.lifecycle._helpers import safe_json_loads
from tools.registry import registry, tool_error, tool_result


SCHEMA = {
    "name": "inspect_position",
    "description": (
        "Return the full causal chain for one position: position row, opening "
        "and closing trades, the decisions that anchored those trades, the "
        "linked theses (with snapshot_ids), the linked strategy, the conviction "
        "trajectory (every position_evaluation), and the outcome row. The full "
        "lineage in one tool call — for post-trade reflection or debugging."
    ),
    "parameters": {
        "type": "object",
        "properties": {"position_id": {"type": "integer"}},
        "required": ["position_id"],
    },
}


def _inspect_position(args: Dict[str, Any]) -> str:
    position_id = args.get("position_id")
    if not position_id:
        return tool_error("inspect_position requires position_id")
    pid = int(position_id)
    db = get_lifecycle_db()
    conn = db.conn()

    pos = conn.execute("SELECT * FROM positions WHERE id = ?", (pid,)).fetchone()
    if pos is None:
        return tool_error(f"position {pid} not found")

    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE id IN (?, ?)",
        (pos["opening_trade_id"], pos["closing_trade_id"] or -1),
    ).fetchall()]

    decision_ids = list({t["decision_id"] for t in trades})
    decisions = [dict(r) for r in conn.execute(
        f"SELECT * FROM decisions WHERE id IN ({','.join('?' * len(decision_ids))})",
        decision_ids,
    ).fetchall()]
    for d in decisions:
        d["params"] = safe_json_loads(d.pop("params_json"))

    thesis_ids = list({d["thesis_id"] for d in decisions})
    theses = [dict(r) for r in conn.execute(
        f"SELECT id, ts, symbol, text_md, strategy_id, structured_tags_json, "
        f"snapshot_ids_json, invalidation_criteria_json, embedding_model "
        f"FROM theses WHERE id IN ({','.join('?' * len(thesis_ids))})",
        thesis_ids,
    ).fetchall()]
    for t in theses:
        t["snapshot_ids"] = safe_json_loads(t.pop("snapshot_ids_json"))
        t["structured_tags"] = safe_json_loads(t.pop("structured_tags_json"))
        t["invalidation_criteria"] = safe_json_loads(t.pop("invalidation_criteria_json"))

    strategy_ids = [t["strategy_id"] for t in theses if t["strategy_id"] is not None]
    strategies = []
    if strategy_ids:
        strategies = [dict(r) for r in conn.execute(
            f"SELECT id, name, description_md, status FROM strategies "
            f"WHERE id IN ({','.join('?' * len(set(strategy_ids)))})",
            list(set(strategy_ids)),
        ).fetchall()]

    trajectory = [dict(r) for r in conn.execute(
        "SELECT id, ts, conviction, thesis_status, active_thesis_id, "
        "rationale_md, snapshot_ids_json, recommended_action, action_taken_decision_id "
        "FROM position_evaluations WHERE position_id = ? ORDER BY ts",
        (pid,),
    ).fetchall()]
    for ev in trajectory:
        ev["snapshot_ids"] = safe_json_loads(ev.pop("snapshot_ids_json"))

    outcome_row = conn.execute(
        "SELECT * FROM outcomes WHERE position_id = ?", (pid,)
    ).fetchone()
    outcome = dict(outcome_row) if outcome_row else None

    return tool_result({
        "position": dict(pos),
        "trades": trades,
        "decisions": decisions,
        "theses": theses,
        "strategies": strategies,
        "conviction_trajectory": trajectory,
        "outcome": outcome,
    })


registry.register(
    name="inspect_position",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _inspect_position(args),
    description="Full causal chain for one position.",
    emoji="🔍",
)
