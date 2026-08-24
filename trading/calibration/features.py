"""Feature builder — resolved predictions → a leak-free training frame.

Every feature is knowable AT REGISTRATION TIME (support scores, zone
geometry, regime, timescale, mechanism family, and the strategy's PRIOR
resolved record). Labels are the code-owned resolution outcomes. Nothing is
imputed silently: missing per-DP scores stay NaN in the frame, paired with
an explicit ``has_`` indicator — the model layer decides how each estimator
handles absence (HistGBM natively; the linear pipeline imputes 0.5 *with the
indicator present*, so absence is a feature, never a guessed middle).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

# Per-DP columns are limited to the names covering at least this share of
# rows — n is small (~600), so the tail of rare data points would be pure
# noise dimensions. Everything still contributes through the aggregates.
MIN_DP_COVERAGE = 0.10

_REGIME_DIRECTIONS = ("trending-up", "trending-down", "ranging")
_REGIME_VOLS = ("normal", "compressed", "elevated")
_TIMESCALES = ("intraday", "swing", "position")
_FAMILIES = ("momentum", "mean_reversion", "flow", "event", "narrative")


def _base_name(dp_key: str) -> str:
    """Canonical key → base data-point name (``ta_vortex(interval=4h)`` → ``ta_vortex``)."""
    return dp_key.split("(", 1)[0]


def _regime_parts(tag: Optional[str]) -> tuple:
    """Parse a regime tag's direction and volatility tokens (either may be absent)."""
    tokens = (tag or "").split("/")
    direction = next((t for t in tokens if t in _REGIME_DIRECTIONS), None)
    vol = next((t for t in tokens if t in _REGIME_VOLS), None)
    return direction, vol


def feature_row(p, scores_df, dp_names) -> dict:
    """The ONE feature-row builder — training (build_frame) and live scoring
    (trading.calibration.live) both call this, so the two paths cannot drift
    (the 2026-08-22 panel lesson). ``p`` needs attributes ts / timescale /
    regime_tag / near_edge_pct / far_edge_pct / horizon_ts /
    mechanism_family; ``scores_df`` is that prediction's support_scores rows
    (columns score / weight / base) or None; ``dp_names`` fixes the per-DP
    column set. Book-prior columns are NOT built here — each caller computes
    them leak-free for its own timeframe."""
    row: dict = {
        "abs_near": abs(p.near_edge_pct),
        "abs_far": abs(p.far_edge_pct) if p.far_edge_pct is not None else np.nan,
        "zone_rr": (abs(p.far_edge_pct) / abs(p.near_edge_pct))
        if p.far_edge_pct and p.near_edge_pct else np.nan,
        "horizon_h": (p.horizon_ts - p.ts) / 3600.0,
        "direction_up": 1.0 if p.near_edge_pct > 0 else 0.0,
    }
    for t in _TIMESCALES:
        row[f"tsc_{t}"] = 1.0 if p.timescale == t else 0.0
    rd, rv = _regime_parts(p.regime_tag)
    for d in _REGIME_DIRECTIONS:
        row[f"regime_{d}"] = 1.0 if rd == d else 0.0
    for v in _REGIME_VOLS:
        row[f"vol_{v}"] = 1.0 if rv == v else 0.0
    for f in _FAMILIES:
        row[f"fam_{f}"] = 1.0 if p.mechanism_family == f else 0.0

    if scores_df is not None and len(scores_df):
        sc = scores_df["score"].to_numpy(dtype=float)
        w = scores_df["weight"].fillna(0.0).to_numpy(dtype=float)
        row["n_scored"] = float(len(sc))
        row["score_wmean"] = float(np.average(sc, weights=w)) if w.sum() > 0 \
            else float(sc.mean())
        row["score_min"] = float(sc.min())
        row["score_max"] = float(sc.max())
        row["frac_extreme_hi"] = float((sc >= 0.9).mean())
        row["frac_low"] = float((sc <= 0.2).mean())
        per_base = scores_df.groupby("base")["score"].mean()
    else:
        row.update({"n_scored": 0.0, "score_wmean": np.nan, "score_min": np.nan,
                    "score_max": np.nan, "frac_extreme_hi": np.nan,
                    "frac_low": np.nan})
        per_base = pd.Series(dtype=float)
    for name in dp_names:
        present = name in per_base.index
        row[f"dp_{name}"] = float(per_base[name]) if present else np.nan
        row[f"has_{name}"] = 1.0 if present else 0.0
    return row


def build_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per resolved strategy prediction; ``y`` = 1 iff outcome correct.

    Carries ``ts`` / ``resolved_at`` for purged walk-forward splitting and
    ``conviction_stored`` for the baseline comparisons. Feature columns are
    everything else except the ``meta_*`` identifiers.
    """
    preds = pd.read_sql_query(
        """SELECT p.id, p.ts, p.resolved_at, p.strategy_name, p.timescale,
                  p.regime_tag, p.conviction, p.near_edge_pct, p.far_edge_pct,
                  p.horizon_ts, p.outcome, s.mechanism_family
             FROM predictions p
             LEFT JOIN strategies s ON s.name = p.strategy_name
            WHERE p.kind = 'strategy'
              AND p.outcome IN ('correct', 'wrong')
              AND p.near_edge_pct IS NOT NULL""",
        conn)
    if preds.empty:
        return preds

    scores = pd.read_sql_query(
        "SELECT prediction_id, data_point, score, weight FROM support_scores",
        conn)
    scores["base"] = scores["data_point"].map(_base_name)

    rows = []
    by_pred = dict(tuple(scores.groupby("prediction_id")))
    coverage = scores.groupby("base")["prediction_id"].nunique() / len(preds)
    dp_names = sorted(coverage[coverage >= MIN_DP_COVERAGE].index)

    for p in preds.itertuples():
        row: dict = {
            "meta_id": p.id, "ts": p.ts, "resolved_at": p.resolved_at,
            "meta_strategy": p.strategy_name,
            "y": 1.0 if p.outcome == "correct" else 0.0,
            "conviction_stored": p.conviction,
        }
        row.update(feature_row(p, by_pred.get(p.id), dp_names))
        rows.append(row)

    frame = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)

    # Leak-free prior book stats: only predictions RESOLVED before this one's
    # registration count (Laplace-smoothed so a fresh book reads ~0.5).
    n_prior = np.zeros(len(frame))
    hit_prior = np.zeros(len(frame))
    ts = frame["ts"].to_numpy()
    res = frame["resolved_at"].to_numpy()
    yv = frame["y"].to_numpy()
    strat = frame["meta_strategy"].to_numpy()
    for i in range(len(frame)):
        mask = (strat == strat[i]) & (res <= ts[i])
        n = mask.sum()
        n_prior[i] = n
        hit_prior[i] = (yv[mask].sum() + 1.0) / (n + 2.0)
    frame["book_n_prior"] = n_prior
    frame["book_hit_prior"] = hit_prior
    return frame


META_COLS = ("meta_id", "meta_strategy", "ts", "resolved_at", "y", "conviction_stored")


def feature_columns(frame: pd.DataFrame) -> list:
    return [c for c in frame.columns if c not in META_COLS]


def dataset_summary(frame: pd.DataFrame) -> dict:
    return {
        "n": int(len(frame)),
        "base_rate": round(float(frame["y"].mean()), 4) if len(frame) else None,
        "first_ts": float(frame["ts"].min()) if len(frame) else None,
        "last_ts": float(frame["ts"].max()) if len(frame) else None,
        "n_features": len(feature_columns(frame)),
        "features": feature_columns(frame),
    }
