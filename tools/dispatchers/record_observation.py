"""record_observation — the journal stream.

The trader's running journal: dated micro-notes that don't fit anywhere
else. "I noticed X." "I'm watching Y." "Almost took this trade and didn't
because Z." "New mental model: when A and B coincide, expect C." These
compound into expertise over time and are FTS-indexed for retrieval.

DISTINCT FROM theses (a thesis drives a trade): observations may inform
future theses but they're standalone notes.
DISTINCT FROM predictions (a prediction is a public commitment to be
checked): observations don't get resolved or scored. They're ambient
intelligence.
DISTINCT FROM WORLDVIEW.md (synthesis): observations are the raw stream;
WORLDVIEW.md is what Plutus believes after digesting them.

The 'kind' field separates the genres: 'noticed' (observation about the
market), 'watching' (a setup developing), 'almost_traded' (passed on a
setup — counterfactual data), 'mental_model' (new heuristic), etc.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from agent.lifecycle_db import get_lifecycle_db
from tools.dispatchers._helpers import session_id_from_context
from tools.registry import registry, tool_error, tool_result


VALID_KINDS = (
    "noticed",            # ambient market observation worth recording
    "watching",           # a setup is developing — track it
    "almost_traded",      # passed on a trade; record what stopped you (counterfactual)
    "mental_model",       # new heuristic / pattern Plutus has crystallized
    "pattern_candidate",  # candidate strategy idea, not yet authored
    "edge_claim",         # claim of edge in some domain (must be supportable)
    "edge_revoked",       # withdrawing a previously-claimed edge with rationale
    "operator_input",     # operator shared something — record it for context
    "regime_shift",       # explicit note of regime change
)


SCHEMA = {
    "name": "record_observation",
    "description": (
        "Record a journal entry. Cheap to call, FTS-indexed for later "
        "retrieval. Use liberally — this is the raw stream that compounds "
        "into expertise. 'kind' must be one of: noticed (market), watching "
        "(setup developing), almost_traded (counterfactual — what stopped "
        "you?), mental_model (new heuristic), pattern_candidate (strategy "
        "idea), edge_claim / edge_revoked (epistemic accounting), "
        "operator_input (operator shared something), regime_shift."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text_md":                   {"type": "string"},
            "kind":                      {"type": "string", "enum": list(VALID_KINDS)},
            "symbol":                    {"type": "string"},
            "strategy_name":             {"type": "string"},
            "related_thesis_ids":        {"type": "array"},
            "related_prediction_ids":    {"type": "array"},
            "snapshot_ids":              {"type": "array"},
            "structured_tags":           {"type": "object"},
        },
        "required": ["text_md", "kind"],
    },
}


def _record_observation(args: Dict[str, Any]) -> str:
    text = (args.get("text_md") or "").strip()
    if not text:
        return tool_error("record_observation requires non-empty text_md")
    kind = args.get("kind")
    if kind not in VALID_KINDS:
        return tool_error(f"kind must be one of {VALID_KINDS}, got {kind!r}")

    db = get_lifecycle_db()
    sid = session_id_from_context()

    def _w(conn):
        return conn.execute(
            "INSERT INTO observations(session_id, ts, symbol, kind, text_md, strategy_name, "
            "related_thesis_ids_json, related_prediction_ids_json, "
            "snapshot_ids_json, structured_tags_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                time.time(),
                args.get("symbol"),
                kind,
                text,
                args.get("strategy_name"),
                json.dumps(args["related_thesis_ids"]) if args.get("related_thesis_ids") else None,
                json.dumps(args["related_prediction_ids"]) if args.get("related_prediction_ids") else None,
                json.dumps(args["snapshot_ids"]) if args.get("snapshot_ids") else None,
                json.dumps(args["structured_tags"]) if args.get("structured_tags") else None,
            ),
        ).lastrowid

    obs_id = db._execute_write(_w)
    return tool_result({"observation_id": obs_id, "kind": kind})


registry.register(
    name="record_observation",
    toolset="reflection",
    schema=SCHEMA,
    handler=lambda args, **kw: _record_observation(args),
    description="Append a journal entry (noticed/watching/almost_traded/...).",
    emoji="📓",
)
