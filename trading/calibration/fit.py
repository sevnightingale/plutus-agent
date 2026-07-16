"""Purged walk-forward evaluation + the versioned conviction artifact.

The evaluation answers ONE question honestly: out of sample, does a model
over the registration-time features predict resolution better than (a) the
strategy base rate and (b) the stored conviction after a fair 1-D isotonic
recalibration? Splits are chronological with label purging — a training row
is admitted only if it RESOLVED before the test window opened, so no fold
ever trains on a label that wasn't knowable yet.

The linear artifact is plain JSON — feature names, imputation constants,
standardization, coefficients — auditable by reflect and scoreable with
numpy alone (``predict_from_artifact``), no pickle, no sklearn at runtime.
sklearn is an optional dependency (``pip install 'plutus-agent[ml]'``);
every entry point raises a loud, instructive error without it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from harness.constants import get_hermes_home

from . import features as F

ARTIFACT_VERSION = "conviction-lr-v1"
_EPS = 1e-6


def _require_sklearn():
    try:
        import sklearn  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is not installed — conviction calibration needs the "
            "optional ml extra: pip install 'plutus-agent[ml]'") from exc


def models_dir() -> Path:
    """Resolved at call time (the 45a6cc9 lesson)."""
    return get_hermes_home() / "models" / "conviction"


# ── metrics ──────────────────────────────────────────────────────────────────

def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _logloss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, _EPS, 1 - _EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return {"n": int(len(y)), "brier": round(_brier(y, p), 5),
            "logloss": round(_logloss(y, p), 5)}


def _bootstrap_brier_delta(y, p_a, p_b, n_boot: int = 1000, seed: int = 7) -> dict:
    """CI on brier(a) − brier(b); negative favors a. Paired resampling."""
    rng = np.random.default_rng(seed)
    n = len(y)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        deltas[i] = _brier(y[idx], p_a[idx]) - _brier(y[idx], p_b[idx])
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return {"delta": round(_brier(y, p_a) - _brier(y, p_b), 5),
            "ci95": [round(float(lo), 5), round(float(hi), 5)],
            "significant": bool(hi < 0 or lo > 0)}


def _calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum() == 0:
            continue
        out.append({"bucket": f"{edges[i]:.1f}–{edges[i + 1]:.1f}",
                    "n": int(m.sum()),
                    "p_mean": round(float(p[m].mean()), 3),
                    "hit_rate": round(float(y[m].mean()), 3)})
    return out


# ── estimators ───────────────────────────────────────────────────────────────

def _fit_linear(X: pd.DataFrame, y: np.ndarray) -> dict:
    """Elastic-net logistic on imputed+standardized features.

    Returns the fitted spec as PLAIN VALUES (the artifact body) — the model
    is fully determined by (impute, mean, scale, coef, intercept).
    """
    from sklearn.linear_model import LogisticRegression

    impute = X.median(numeric_only=True).fillna(0.5)
    dp_cols = [c for c in X.columns if c.startswith("dp_") or c.startswith("score_")]
    impute[dp_cols] = 0.5  # absent support reads neutral; the has_* flag carries absence
    Xi = X.fillna(impute)
    mean = Xi.mean()
    scale = Xi.std(ddof=0).replace(0.0, 1.0)
    Xs = (Xi - mean) / scale
    lr = LogisticRegression(solver="saga", l1_ratio=0.5, C=1.0, max_iter=8000)
    lr.fit(Xs.to_numpy(), y)
    return {
        "features": list(X.columns),
        "impute": {c: round(float(impute[c]), 6) for c in X.columns},
        "mean": {c: round(float(mean[c]), 6) for c in X.columns},
        "scale": {c: round(float(scale[c]), 6) for c in X.columns},
        "coef": {c: round(float(w), 6)
                 for c, w in zip(X.columns, lr.coef_[0])},
        "intercept": round(float(lr.intercept_[0]), 6),
    }


def predict_from_artifact(spec: dict, X: pd.DataFrame) -> np.ndarray:
    """numpy-only scoring of a linear artifact — the future runtime path."""
    cols = spec["features"]
    Xi = X.reindex(columns=cols)
    for c in cols:
        Xi[c] = Xi[c].fillna(spec["impute"][c])
        Xi[c] = (Xi[c] - spec["mean"][c]) / spec["scale"][c]
    z = Xi.to_numpy() @ np.array([spec["coef"][c] for c in cols]) + spec["intercept"]
    return 1.0 / (1.0 + np.exp(-z))


def _fit_gbm(X: pd.DataFrame, y: np.ndarray):
    """Small HistGBM (native NaN) — comparison only; not serialized in v1."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    gbm = HistGradientBoostingClassifier(
        max_iter=200, max_depth=3, learning_rate=0.08,
        early_stopping=True, validation_fraction=0.2, random_state=7)
    gbm.fit(X.to_numpy(), y)
    return gbm


def _fit_isotonic_baseline(conv_train: np.ndarray, y_train: np.ndarray):
    """The fairest 'current engine' baseline: stored conviction, recalibrated 1-D."""
    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(y_min=_EPS, y_max=1 - _EPS, out_of_bounds="clip")
    iso.fit(conv_train, y_train)
    return iso


# ── the walk-forward report ──────────────────────────────────────────────────

MIN_TRAIN = 60
MIN_TEST = 8


def walk_forward_report(frame: pd.DataFrame, folds: int = 5) -> dict:
    """Chronological folds; train = rows RESOLVED before the fold opens."""
    _require_sklearn()
    n = len(frame)
    if n < MIN_TRAIN + MIN_TEST:
        return {"error": f"only {n} resolved predictions — need ≥ {MIN_TRAIN + MIN_TEST}"}

    cols = F.feature_columns(frame)
    ts = frame["ts"].to_numpy()
    res = frame["resolved_at"].to_numpy()
    y_all = frame["y"].to_numpy()
    conv_all = frame["conviction_stored"].to_numpy()

    # fold boundaries over the tail 60% of rows, by registration order
    start = int(n * 0.4)
    bounds = np.linspace(start, n, folds + 1, dtype=int)

    pooled: dict = {k: [] for k in
                    ("y", "conv", "p_lr", "p_gbm", "p_iso", "p_base")}
    fold_rows = []
    for i in range(folds):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo < MIN_TEST:
            continue
        t_open = ts[lo]
        train = res <= t_open           # label knowable before the fold opened
        train[lo:] = False              # and registered before it, regardless
        if train.sum() < MIN_TRAIN or len(set(y_all[train])) < 2:
            continue
        Xtr, ytr = frame.loc[train, cols], y_all[train]
        Xte, yte = frame.iloc[lo:hi][cols], y_all[lo:hi]

        spec = _fit_linear(Xtr, ytr)
        p_lr = predict_from_artifact(spec, Xte)
        p_gbm = _fit_gbm(Xtr, ytr).predict_proba(Xte.to_numpy())[:, 1]
        iso = _fit_isotonic_baseline(conv_all[train], ytr)
        p_iso = iso.predict(conv_all[lo:hi])
        p_base = np.full(hi - lo, ytr.mean())

        pooled["y"].append(yte)
        pooled["conv"].append(conv_all[lo:hi])
        pooled["p_lr"].append(p_lr)
        pooled["p_gbm"].append(p_gbm)
        pooled["p_iso"].append(p_iso)
        pooled["p_base"].append(p_base)
        fold_rows.append({"fold": i + 1, "train_n": int(train.sum()),
                          "test_n": int(hi - lo),
                          "lr_brier": round(_brier(yte, p_lr), 5),
                          "iso_brier": round(_brier(yte, p_iso), 5)})

    if not fold_rows:
        return {"error": "no viable folds (books too thin for purged splits)"}

    y = np.concatenate(pooled["y"])
    conv = np.concatenate(pooled["conv"])
    p_lr = np.concatenate(pooled["p_lr"])
    p_gbm = np.concatenate(pooled["p_gbm"])
    p_iso = np.concatenate(pooled["p_iso"])
    p_base = np.concatenate(pooled["p_base"])

    report = {
        "dataset": F.dataset_summary(frame),
        "folds": fold_rows,
        "oos": {
            "model_lr": _metrics(y, p_lr),
            "model_gbm": _metrics(y, p_gbm),
            "baseline_conviction_raw": _metrics(y, conv),
            "baseline_conviction_isotonic": _metrics(y, p_iso),
            "baseline_base_rate": _metrics(y, p_base),
        },
        "deltas": {
            "lr_vs_isotonic": _bootstrap_brier_delta(y, p_lr, p_iso),
            "lr_vs_base_rate": _bootstrap_brier_delta(y, p_lr, p_base),
            "gbm_vs_lr": _bootstrap_brier_delta(y, p_gbm, p_lr),
        },
        "calibration_table_lr": _calibration_table(y, p_lr),
        "calibration_table_stored_conviction": _calibration_table(y, conv),
    }
    report["verdict"] = {
        "lr_beats_isotonic_conviction":
            bool(report["deltas"]["lr_vs_isotonic"]["delta"] < 0),
        "lr_beats_isotonic_significant":
            bool(report["deltas"]["lr_vs_isotonic"]["significant"]
                 and report["deltas"]["lr_vs_isotonic"]["delta"] < 0),
        "stored_conviction_worse_than_base_rate":
            bool(_brier(y, conv) > _brier(y, p_base)),
    }
    return report


def fit_and_save(frame: pd.DataFrame, report: dict) -> dict:
    """Fit the linear spec on the FULL frame and write the versioned artifact."""
    _require_sklearn()
    cols = F.feature_columns(frame)
    spec = _fit_linear(frame[cols], frame["y"].to_numpy())
    trained_at = time.time()
    artifact = {
        "version": ARTIFACT_VERSION,
        "trained_at": trained_at,
        "n_train": int(len(frame)),
        "oos": report.get("oos"),
        "verdict": report.get("verdict"),
        "spec": spec,
    }
    out_dir = models_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(trained_at))
    path = out_dir / f"{ARTIFACT_VERSION}-{stamp}.json"
    path.write_text(json.dumps(artifact, indent=1), encoding="utf-8")
    return {"artifact_path": str(path), "version": ARTIFACT_VERSION,
            "n_train": int(len(frame))}


def load_artifact(path: Optional[Path] = None) -> dict:
    """Load an artifact (default: newest in the models dir)."""
    if path is None:
        cands = sorted(models_dir().glob(f"{ARTIFACT_VERSION}-*.json"))
        if not cands:
            raise FileNotFoundError(f"no conviction artifacts in {models_dir()}")
        path = cands[-1]
    return json.loads(Path(path).read_text(encoding="utf-8"))
