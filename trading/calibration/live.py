"""Live calibrated conviction — score ONE (possibly unresolved) prediction.

The wire-in of the conviction calibration model (operator-approved
2026-08-24 after four consecutive significant walk-forward runs): the
newest artifact reflect saves is consumed at funding time. Selection ranks
pilot candidates by this probability and sizing feeds it to the notional
bands — raw conviction remains stored on the prediction untouched, so the
training loop keeps learning from the uncalibrated signal and can never
train on its own output.

Feature parity is structural, not aspirational: the row is built by the
same ``features.feature_row`` the training frame uses (the 2026-08-22
one-builder lesson). The per-DP column set comes from the artifact's own
spec, so a model trained under one coverage cut scores under the same one.

Failure posture: ``calibrated_conviction`` returns None when it cannot
produce an honest number (no artifact, prediction missing its geometry) —
callers fall back to raw conviction AND record that they did. It logs and
never raises into the funding path.
"""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "artifact": None}


def _newest_artifact() -> Optional[dict]:
    """Newest saved artifact, cached on (path, mtime)."""
    from trading.calibration import fit
    try:
        cands = sorted(fit.models_dir().glob(f"{fit.ARTIFACT_VERSION}-*.json"))
    except OSError:
        return None
    if not cands:
        return None
    path = cands[-1]
    mtime = path.stat().st_mtime
    if _CACHE["path"] != str(path) or _CACHE["mtime"] != mtime:
        _CACHE.update(path=str(path), mtime=mtime,
                      artifact=fit.load_artifact(path))
    return _CACHE["artifact"]


def _dp_names(spec: dict) -> list:
    return sorted(c[len("dp_"):] for c in spec["features"] if c.startswith("dp_"))


def calibrated_conviction(
    conn: sqlite3.Connection, prediction_id: int,
) -> Optional[Dict[str, Any]]:
    """{"p": float, "version": str, "trained_at": float} — or None, honestly.

    Never raises: any failure is logged and reported as absence so the
    funding path degrades to raw conviction with the fallback on record.
    """
    try:
        artifact = _newest_artifact()
        if artifact is None:
            return None
        spec = artifact["spec"]

        import pandas as pd

        from trading.calibration import features as F
        from trading.calibration.fit import predict_from_artifact

        pred = conn.execute(
            """SELECT p.id, p.ts, p.timescale, p.regime_tag, p.near_edge_pct,
                      p.far_edge_pct, p.horizon_ts, p.strategy_name,
                      s.mechanism_family
                 FROM predictions p
                 LEFT JOIN strategies s ON s.name = p.strategy_name
                WHERE p.id = ?""", (prediction_id,)).fetchone()
        if pred is None or pred["near_edge_pct"] is None:
            return None

        scores = pd.read_sql_query(
            "SELECT score, weight, data_point FROM support_scores "
            "WHERE prediction_id = ?", conn, params=(prediction_id,))
        scores["base"] = scores["data_point"].map(F._base_name)

        p = SimpleNamespace(**dict(pred))
        row = F.feature_row(p, scores if len(scores) else None, _dp_names(spec))

        # Leak-free book prior, same definition as the training frame:
        # only predictions RESOLVED before this one's registration count.
        n, hits = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(outcome = 'correct'), 0)
                 FROM predictions
                WHERE strategy_name = ? AND kind = 'strategy'
                  AND outcome IN ('correct', 'wrong') AND resolved_at <= ?""",
            (pred["strategy_name"], pred["ts"])).fetchone()
        row["book_n_prior"] = float(n)
        row["book_hit_prior"] = (float(hits) + 1.0) / (float(n) + 2.0)

        prob = float(predict_from_artifact(spec, pd.DataFrame([row]))[0])
        return {"p": round(prob, 4), "version": artifact["version"],
                "trained_at": artifact["trained_at"]}
    except Exception:
        logger.warning("calibrated_conviction failed for prediction %s",
                       prediction_id, exc_info=True)
        return None
