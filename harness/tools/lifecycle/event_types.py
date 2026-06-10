"""Standard lifecycle event types — registered at module load.

The agent dispatches via ``record_event(type, params)``; the dispatcher
looks up the handler in ``event_registry``. This module contains the
production handlers for every event type Plutus uses across the trade
lifecycle: thesis, decision (skip/hold/modify), position_evaluation,
reflection, capital_movement, strategy_open, strategy_status_change.

Trade and position rows are NOT registered as event types — they're
written by the ``place_order``/``close_position`` dispatchers via their
own atomic transactions, not via ``record_event``. ``outcome`` is
similarly written by the ``close_position`` dispatcher (shell + venue
enrichment).

Embedding-aware events (``thesis``, ``reflection``) atomically write
both the row and its vec0 entry — see PLUTUS principle 4 (atomicity
required for searchable rows).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from harness.agent.lifecycle_db import get_lifecycle_db
from harness.tools.core.event_registry import register_event

logger = logging.getLogger(__name__)


def _json_dump(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _embed(text: str) -> tuple[Optional[bytes], Optional[str], Optional[List[float]]]:
    """Return (embedding_blob, embedding_model_name, embedding_vector).

    Failures are logged but non-fatal — the row writes without the
    embedding so the textual record is preserved. The vec0 sibling row
    is only written when the vector succeeded.
    """
    try:
        from harness.tools.core.embedder import get_embedder
        embedder = get_embedder()
        vec = embedder.embed_documents([text])[0]
        import struct
        blob = struct.pack(f"{len(vec)}f", *vec)
        return blob, embedder.model_name, vec
    except Exception as exc:
        logger.warning("embed failed (text len=%d): %s", len(text), exc)
        return None, None, None


def _vec_insert(conn, table: str, fk_col: str, fk_id: int, vec: List[float]) -> None:
    try:
        import sqlite_vec
        conn.execute(
            f"INSERT INTO {table}({fk_col}, embedding) VALUES (?, ?)",
            (fk_id, sqlite_vec.serialize_float32(vec)),
        )
    except Exception as exc:
        logger.warning("vec insert into %s failed for id=%s: %s", table, fk_id, exc)


# ─── thesis ───────────────────────────────────────────────────────────────


@register_event(
    name="thesis",
    description=(
        "Record a market thesis — a falsifiable claim Plutus is willing to act on. "
        "Required: symbol, text_md, invalidation_criteria (place_order refuses "
        "without invalidation criteria — the thesis must be testable). Strongly "
        "recommended: strategy_name (file under ~/.plutus-agent/strategies/active/ "
        "or trial/ that produced this thesis), regime_tag (perceived regime), "
        "prediction_horizon_hours (when thesis must resolve — open-ended theses "
        "drift). Optional: structured_tags, snapshot_ids. Embeds text_md via "
        "voyage-finance-2 and writes the vec0 row atomically."
    ),
    fields_schema={
        "symbol":                    {"type": "string", "required": True},
        "text_md":                   {"type": "string", "required": True},
        "invalidation_criteria":     {"type": "object"},
        "strategy_name":             {"type": "string"},
        "regime_tag":                {"type": "string"},
        "prediction_horizon_hours":  {"type": "number"},
        "strategy_id":               {"type": "integer"},  # legacy
        "structured_tags":           {"type": "object"},
        "snapshot_ids":              {"type": "array"},
    },
)
def _record_thesis(*, symbol: str, text_md: str,
                   invalidation_criteria: Any = None,
                   strategy_name: Optional[str] = None,
                   regime_tag: Optional[str] = None,
                   prediction_horizon_hours: Optional[float] = None,
                   strategy_id: Optional[int] = None,
                   structured_tags: Any = None,
                   snapshot_ids: Any = None) -> Dict[str, Any]:
    blob, model, vec = _embed(text_md)

    db = get_lifecycle_db()

    def _w(conn):
        thesis_id = conn.execute(
            "INSERT INTO theses(ts, symbol, text_md, strategy_id, strategy_name, "
            "regime_tag, prediction_horizon_hours, structured_tags_json, "
            "snapshot_ids_json, invalidation_criteria_json, embedding, embedding_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), symbol, text_md, strategy_id, strategy_name,
             regime_tag, prediction_horizon_hours,
             _json_dump(structured_tags), _json_dump(snapshot_ids),
             _json_dump(invalidation_criteria), blob, model),
        ).lastrowid
        if vec is not None:
            _vec_insert(conn, "theses_vec", "thesis_id", thesis_id, vec)
        return thesis_id

    thesis_id = db._execute_write(_w)
    return {"id": thesis_id, "thesis_id": thesis_id, "embedding_model": model}


# ─── decision (skip / hold / modify — non-trade decisions) ────────────────


@register_event(
    name="decision",
    description=(
        "Record a decision against a thesis WITHOUT a venue trade — "
        "typically action='skip' (decided not to open) or action='hold' "
        "(no change). For action='open_long'/'open_short'/'close', use "
        "place_order/close_position dispatchers (those write the decision "
        "row atomically with the trade)."
    ),
    fields_schema={
        "thesis_id": {"type": "integer", "required": True},
        "action":    {"type": "string", "required": True,
                      "description": "skip | hold | modify_sl | ..."},
        "conviction": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "params":    {"type": "object"},
    },
)
def _record_decision(*, thesis_id: int, action: str,
                     conviction: float = 0.5,
                     params: Any = None) -> Dict[str, Any]:
    if not (0.0 <= float(conviction) <= 1.0):
        raise ValueError("conviction must be in [0.0, 1.0]")

    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO decisions(thesis_id, ts, action, params_json, conviction) "
            "VALUES (?, ?, ?, ?, ?)",
            (thesis_id, time.time(), action, _json_dump(params), float(conviction)),
        ).lastrowid

    decision_id = db._execute_write(_w)
    return {"id": decision_id, "decision_id": decision_id}


# ─── position_evaluation ──────────────────────────────────────────────────


@register_event(
    name="position_evaluation",
    description=(
        "Re-evaluate an open position. position-monitor skill MUST call "
        "this every cycle for each open position, even when "
        "recommended_action='hold'. The trajectory IS the analytical "
        "substrate — query_conviction_trajectory and "
        "query_conviction_outcomes depend on it.\n\n"
        "V2: `conviction` here is the **composite** conviction — sqrt(strategy_conviction × "
        "thesis_conviction) — not raw thesis-level conviction. This makes the trajectory "
        "comparable across positions opened under different strategies, and aligns the "
        "field's meaning with place_order's `multiplier = 20**composite` sizing math. "
        "If the skill only has thesis conviction handy, it should look up the strategy's "
        "frontmatter strategy_conviction (via agent.strategy_loader.get_strategy_conviction) "
        "and compute the geometric mean before passing it here."
    ),
    fields_schema={
        "position_id":         {"type": "integer", "required": True},
        "conviction":          {"type": "number", "minimum": 0.0, "maximum": 1.0,
                                "required": True,
                                "description": "V2: composite conviction (geometric mean of strategy × thesis). Not raw thesis-level."},
        "thesis_status":       {"type": "string",
                                "description": "intact | strengthened | weakening | invalidated"},
        "active_thesis_id":    {"type": "integer"},
        "rationale_md":        {"type": "string"},
        "snapshot_ids":        {"type": "array"},
        "recommended_action":  {"type": "string",
                                "description": "hold | exit_now | tighten_sl | scale_in | scale_out"},
    },
)
def _record_position_evaluation(*, position_id: int, conviction: float,
                                thesis_status: Optional[str] = None,
                                active_thesis_id: Optional[int] = None,
                                rationale_md: Optional[str] = None,
                                snapshot_ids: Any = None,
                                recommended_action: Optional[str] = None) -> Dict[str, Any]:
    if not (0.0 <= float(conviction) <= 1.0):
        raise ValueError("conviction must be in [0.0, 1.0]")

    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO position_evaluations(ts, position_id, conviction, "
            "thesis_status, active_thesis_id, rationale_md, "
            "snapshot_ids_json, recommended_action) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), position_id, float(conviction), thesis_status,
             active_thesis_id, rationale_md, _json_dump(snapshot_ids),
             recommended_action),
        ).lastrowid

    eval_id = db._execute_write(_w)
    return {"id": eval_id, "position_evaluation_id": eval_id}


# ─── reflection ───────────────────────────────────────────────────────────


@register_event(
    name="reflection",
    description=(
        "Record a reflection. reflection_kind: 'post_trade' (opportunistic), "
        "'loss_postmortem' (mandatory on r_multiple < -0.5), 'weekly_review' "
        "(Sunday cron), 'calibration_review' (calibration-review skill), "
        "'strategy_review' (strategy-curator skill), 'setup_complete', "
        "'ad_hoc' (everything else). On losses, set error_class to one of "
        "'forecast' (thesis was wrong), 'execution' (thesis right, entry/exit "
        "off), 'sizing' (right but oversized), 'regime' (wrong regime "
        "applied), 'variance' (right and properly sized, market noise), "
        "'process_violation' (skipped a required step). Optionally tag "
        "strategy_name (the strategy this reflection is about). Embeds via "
        "voyage-finance-2 and writes the vec0 row atomically."
    ),
    fields_schema={
        "text_md":                    {"type": "string", "required": True},
        "reflection_kind":            {"type": "string"},
        "position_ids":               {"type": "array"},
        "related_thesis_ids":         {"type": "array"},
        "related_prediction_ids":     {"type": "array"},
        "error_class":                {"type": "string"},
        "strategy_name":              {"type": "string"},
    },
)
def _record_reflection(*, text_md: str,
                       reflection_kind: Optional[str] = None,
                       position_ids: Any = None,
                       related_thesis_ids: Any = None,
                       related_prediction_ids: Any = None,
                       error_class: Optional[str] = None,
                       strategy_name: Optional[str] = None) -> Dict[str, Any]:
    valid_error_classes = {None, "forecast", "execution", "sizing", "regime",
                           "variance", "process_violation"}
    if error_class not in valid_error_classes:
        raise ValueError(
            f"error_class must be one of {sorted(c for c in valid_error_classes if c)} "
            f"or omitted; got {error_class!r}"
        )
    blob, model, vec = _embed(text_md)

    db = get_lifecycle_db()

    def _w(conn):
        reflection_id = conn.execute(
            "INSERT INTO reflections(ts, text_md, position_ids_json, "
            "related_thesis_ids_json, related_prediction_ids_json, "
            "reflection_kind, error_class, strategy_name, embedding, embedding_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), text_md, _json_dump(position_ids),
             _json_dump(related_thesis_ids), _json_dump(related_prediction_ids),
             reflection_kind, error_class, strategy_name, blob, model),
        ).lastrowid
        if vec is not None:
            _vec_insert(conn, "reflections_vec", "reflection_id", reflection_id, vec)
        return reflection_id

    reflection_id = db._execute_write(_w)
    return {"id": reflection_id, "reflection_id": reflection_id, "embedding_model": model}


# ─── capital_movement ─────────────────────────────────────────────────────


@register_event(
    name="capital_movement",
    description=(
        "Manually record a capital movement (deposit, withdrawal, internal "
        "transfer). Auto-recorded by acp_wallet_send and venue execution; "
        "use this event for capital movements that happen out-of-band "
        "(operator funded externally, bridge transfer, etc.)."
    ),
    fields_schema={
        "from_account":       {"type": "string"},
        "to_account":         {"type": "string"},
        "token":              {"type": "string", "required": True},
        "amount_token":       {"type": "number", "required": True},
        "amount_usd_at_time": {"type": "number"},
        "movement_type":      {"type": "string", "required": True,
                               "description": "deposit | withdrawal | internal_transfer | venue_transfer"},
        "tx_hash":            {"type": "string"},
        "note":               {"type": "string"},
    },
)
def _record_capital_movement(*, token: str, amount_token: float,
                             movement_type: str,
                             from_account: Optional[str] = None,
                             to_account: Optional[str] = None,
                             amount_usd_at_time: Optional[float] = None,
                             tx_hash: Optional[str] = None,
                             note: Optional[str] = None) -> Dict[str, Any]:
    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO capital_movements(ts, from_account, to_account, "
            "token, amount_token, amount_usd_at_time, movement_type, "
            "tx_hash, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), from_account, to_account, token,
             float(amount_token),
             float(amount_usd_at_time) if amount_usd_at_time is not None else None,
             movement_type, tx_hash, note),
        ).lastrowid

    cm_id = db._execute_write(_w)
    return {"id": cm_id, "capital_movement_id": cm_id}


# ─── strategy_open ────────────────────────────────────────────────────────


@register_event(
    name="strategy_open",
    description=(
        "Open a new strategy in the strategy book. After this, theses can "
        "FK-link via strategy_id and query_strategy_book reports per-strategy "
        "performance. Use when 3+ similar trades surface a recognisable "
        "pattern worth tracking."
    ),
    fields_schema={
        "name":                 {"type": "string", "required": True},
        "description_md":       {"type": "string"},
        "hypothesis_md":        {"type": "string"},
        "regime_conditions":    {"type": "object"},
    },
)
def _record_strategy_open(*, name: str,
                          description_md: Optional[str] = None,
                          hypothesis_md: Optional[str] = None,
                          regime_conditions: Any = None) -> Dict[str, Any]:
    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO strategies(name, description_md, hypothesis_md, "
            "regime_conditions_json, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (name, description_md, hypothesis_md,
             _json_dump(regime_conditions), time.time()),
        ).lastrowid

    strategy_id = db._execute_write(_w)
    return {"id": strategy_id, "strategy_id": strategy_id}


# ─── strategy_status_change ───────────────────────────────────────────────


@register_event(
    name="strategy_status_change",
    description=(
        "Change a strategy's status (active → paused / paused → active / "
        "active|paused → retired). Reason required for paused/retired so "
        "weekly-review reads are coherent."
    ),
    fields_schema={
        "strategy_id":  {"type": "integer", "required": True},
        "new_status":   {"type": "string", "required": True,
                         "description": "active | paused | retired"},
        "reason_md":    {"type": "string"},
    },
)
def _record_strategy_status_change(*, strategy_id: int, new_status: str,
                                   reason_md: Optional[str] = None) -> Dict[str, Any]:
    if new_status not in ("active", "paused", "retired"):
        raise ValueError(
            f"new_status must be active|paused|retired, got {new_status!r}"
        )
    db = get_lifecycle_db()

    def _w(conn):
        if new_status == "retired":
            conn.execute(
                "UPDATE strategies SET status = ?, retired_at = ?, "
                "retirement_reason = ? WHERE id = ?",
                (new_status, time.time(), reason_md, strategy_id),
            )
        else:
            conn.execute(
                "UPDATE strategies SET status = ? WHERE id = ?",
                (new_status, strategy_id),
            )
        return strategy_id

    db._execute_write(_w)
    return {"strategy_id": strategy_id, "new_status": new_status}


# ─── compaction (V2 visibility) ───────────────────────────────────────────


@register_event(
    name="compaction",
    description=(
        "Record a context-window compaction. V2: kimi-k2.6's 256K window means "
        "compactions are less frequent but more impactful — visibility into when "
        "they happened (and how much was compressed) is load-bearing for "
        "plutus-main's self-awareness and for retroactive debugging when 'I "
        "thought I knew X' turns out to be 'X was in the pre-compaction half'. "
        "Recorded as an observation row with structured_tags marking the event, "
        "so query_compaction_history surfaces them via observations FTS."
    ),
    fields_schema={
        "layer": {"type": "string", "required": True,
                  "description": "gateway_pre_compress | agent_mid_conversation"},
        "pre_token_count": {"type": "integer", "required": True},
        "post_token_count": {"type": "integer", "required": True},
        "pre_message_count": {"type": "integer"},
        "post_message_count": {"type": "integer"},
        "session_id_before": {"type": "string",
                              "description": "Session id that was compacted (may differ from current if rotation occurred)."},
        "session_id_after": {"type": "string"},
        "model": {"type": "string"},
        "focus_topic": {"type": "string",
                        "description": "Optional focus passed to /compress; populated for explicit user-driven compactions."},
        "summary_md": {"type": "string"},
    },
)
def _record_compaction(
    *,
    layer: str,
    pre_token_count: int,
    post_token_count: int,
    pre_message_count: Optional[int] = None,
    post_message_count: Optional[int] = None,
    session_id_before: Optional[str] = None,
    session_id_after: Optional[str] = None,
    model: Optional[str] = None,
    focus_topic: Optional[str] = None,
    summary_md: Optional[str] = None,
) -> Dict[str, Any]:
    if layer not in ("gateway_pre_compress", "agent_mid_conversation"):
        raise ValueError(
            f"layer must be gateway_pre_compress|agent_mid_conversation, got {layer!r}"
        )
    pre = int(pre_token_count)
    post = int(post_token_count)
    ratio = (post / pre) if pre > 0 else 0.0

    text_md = (
        summary_md
        or f"Compaction ({layer}): {pre:,} → {post:,} tokens "
           f"(ratio {ratio:.2f})"
        + (f" — focus: {focus_topic}" if focus_topic else "")
    )
    structured_tags = {
        "event_type": "compaction",
        "layer": layer,
        "pre_token_count": pre,
        "post_token_count": post,
        "pre_message_count": pre_message_count,
        "post_message_count": post_message_count,
        "compression_ratio": ratio,
        "session_id_before": session_id_before,
        "session_id_after": session_id_after,
        "model": model,
        "focus_topic": focus_topic,
    }

    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO observations(session_id, ts, kind, text_md, structured_tags_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session_id_after or session_id_before,
                time.time(),
                "noticed",
                text_md,
                _json_dump(structured_tags),
            ),
        ).lastrowid

    obs_id = db._execute_write(_w)
    return {"observation_id": obs_id, "compression_ratio": ratio}


# ─── perception_digest (V2.1 — main-spawned plutus-perception sub-agent) ──


@register_event(
    name="perception_digest",
    description=(
        "Record a perception digest from the plutus-perception sub-agent. V2.1: "
        "plutus-main spawns plutus-perception at beat start; perception executes "
        "the wide fetch sweep (declared strategy DPs × watchlist + cross-asset + "
        "macro blueprint resolution); writes ONE perception_digest observation "
        "summarizing findings. plutus-main reads the digest in Phase 3 instead "
        "of doing perception itself. This event is the sync contract between "
        "the two tiers — main blocks until a digest matching this beat appears. "
        "Recorded as an observation row with structured_tags marking the event, "
        "so query_latest_perception_digest can find it without scanning text."
    ),
    fields_schema={
        "for_main_beat_at_unix": {"type": "number", "required": True,
                                  "description": "Unix ts of the main beat this digest serves."},
        "scope": {"type": "string", "required": True,
                  "description": "standard | weekly. standard = declared strategy DPs × watchlist + cross-asset + macro. weekly = standard + dgclaw_leaderboard/forums + extra TA periods."},
        "text_md": {"type": "string", "required": True,
                    "description": "The structured digest body — per-asset findings, cross-asset, macro, anomalies, broken-list retest results."},
        "watchlist_covered": {"type": "array",
                              "description": "Symbols this digest covers (mirror of WORLDVIEW.watchlist at perception start)."},
        "strategies_perceived": {"type": "array",
                                 "description": "Strategy names whose declared DPs were fetched."},
        "fresh_count": {"type": "integer",
                        "description": "How many DPs were fetched fresh (cache bypass)."},
        "failed_dps": {"type": "array",
                       "description": "DP names that errored during fetch."},
        "broken_list_retest_results": {"type": "object",
                                       "description": "Results of re-testing WORLDVIEW.broken list (key: dp_name, value: 'now_working' | 'still_broken' | error)."},
        "snapshot_ids_by_dp": {"type": "object",
                               "description": "Map of dp_name → snapshot_id so main can drill in if needed."},
        "duration_s": {"type": "number"},
        "session_id_perception": {"type": "string",
                                  "description": "session_id of the spawned plutus-perception sub-agent (for traceability)."},
        "structured_tags_extra": {"type": "object",
                                  "description": "Extra tags to merge (rare; reserved)."},
    },
)
def _record_perception_digest(
    *,
    for_main_beat_at_unix: float,
    scope: str,
    text_md: str,
    watchlist_covered: Optional[List[str]] = None,
    strategies_perceived: Optional[List[str]] = None,
    fresh_count: Optional[int] = None,
    failed_dps: Optional[List[str]] = None,
    broken_list_retest_results: Optional[Dict[str, Any]] = None,
    snapshot_ids_by_dp: Optional[Dict[str, int]] = None,
    duration_s: Optional[float] = None,
    session_id_perception: Optional[str] = None,
    structured_tags_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if scope not in ("standard", "weekly"):
        raise ValueError(f"scope must be standard|weekly, got {scope!r}")

    # Default session_id_perception from execution context. The spawn helper
    # sets HERMES_SESSION_KEY to the sub-agent's session id before running the
    # AIAgent (see agent.subagent_spawn._run_with_sub_session), so dispatchers
    # see the correct session even though the sub-agent itself never has to
    # know its own id. If the caller passes session_id_perception explicitly
    # (e.g., for back-fill from a script), respect that.
    if not session_id_perception:
        from harness.tools.dispatchers._helpers import session_id_from_context
        session_id_perception = session_id_from_context()

    structured_tags: Dict[str, Any] = {
        "event_type": "perception_digest",
        "source_tier": "perception",
        "scope": scope,
        "for_main_beat_at_unix": float(for_main_beat_at_unix),
        "watchlist_covered": list(watchlist_covered or []),
        "strategies_perceived": list(strategies_perceived or []),
        "fresh_count": int(fresh_count) if fresh_count is not None else None,
        "failed_dps": list(failed_dps or []),
        "broken_list_retest_results": dict(broken_list_retest_results or {}),
        "snapshot_ids_by_dp": dict(snapshot_ids_by_dp or {}),
        "duration_s": float(duration_s) if duration_s is not None else None,
        "session_id_perception": session_id_perception,
    }
    if structured_tags_extra:
        structured_tags.update(structured_tags_extra)

    db = get_lifecycle_db()

    def _w(conn):
        return conn.execute(
            "INSERT INTO observations(session_id, ts, kind, text_md, structured_tags_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session_id_perception,
                time.time(),
                "noticed",
                text_md,
                _json_dump(structured_tags),
            ),
        ).lastrowid

    obs_id = db._execute_write(_w)
    return {"observation_id": obs_id}
