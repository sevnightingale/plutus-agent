"""Conviction calibration harness — feature builder, purged splits, artifact."""

import json
import time

import numpy as np
import pytest

pytest.importorskip("sklearn")

from trading.calibration import features as F
from trading.calibration import fit as FIT
from trading.lifecycle import write
from trading.lifecycle.db import get_db

NOW = time.time()


def _seed(conn, n=140, seed=3):
    """Synthetic resolved book: dp_alpha is predictive, dp_noise is not.

    Registration times stride hourly; every prediction resolves 30 min
    after registration so purged walk-forward folds keep training data.
    """
    rng = np.random.default_rng(seed)
    conn.execute(
        "INSERT INTO strategies (name, file_path, status, timescale, "
        "mechanism_family, data_points_json, created_at, updated_at) VALUES "
        "('cal-s','cal-s.md','test','intraday','flow', ?, 0, 0)",
        (json.dumps([{"name": "dp_alpha", "weight": 0.5},
                     {"name": "dp_noise", "weight": 0.5}]),))
    t0 = NOW - n * 3600.0
    for i in range(n):
        ts = t0 + i * 3600.0
        alpha = float(rng.uniform())
        noise = float(rng.uniform())
        p_correct = 0.15 + 0.7 * alpha          # alpha drives the outcome
        outcome = "correct" if rng.uniform() < p_correct else "wrong"
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="c", ts=ts, horizon_ts=ts + 6 * 3600.0,
            entry_ref_price=100_000.0, near_edge_pct=1.0, far_edge_pct=2.0,
            conviction=round(1.0 - alpha, 4),   # stored conviction ANTI-calibrated
            agent="plutus-predict", symbol="BTC", strategy_name="cal-s",
            kind="strategy", regime_tag="intraday/ranging/normal",
            support_scores=[
                write.SupportScore(data_point="dp_alpha", score=round(alpha, 4),
                                   kind="numerical", weight=0.5),
                write.SupportScore(data_point="dp_noise", score=round(noise, 4),
                                   kind="numerical", weight=0.5),
            ]))
        write.resolve_prediction(conn, pid, outcome, resolved_by="t",
                                 ts=ts + 1800.0)
    conn.commit()


@pytest.fixture()
def frame(tmp_path):
    conn = get_db(tmp_path / "lifecycle.db")
    _seed(conn)
    fr = F.build_frame(conn)
    conn.close()
    return fr


class TestFeatures:
    def test_shape_and_labels(self, frame):
        assert len(frame) == 140
        assert set(frame["y"].unique()) <= {0.0, 1.0}
        assert "dp_dp_alpha" in frame.columns and "has_dp_alpha" in frame.columns
        assert frame["ts"].is_monotonic_increasing

    def test_book_stats_are_prior_only(self, frame):
        # first prediction of the strategy has an empty prior book → Laplace 0.5
        first = frame.iloc[0]
        assert first["book_n_prior"] == 0
        assert first["book_hit_prior"] == pytest.approx(0.5)
        # later rows accumulate priors monotonically in n
        assert frame["book_n_prior"].iloc[-1] > 100


class TestWalkForward:
    def test_model_beats_anticalibrated_conviction(self, frame):
        report = FIT.walk_forward_report(frame, folds=4)
        assert "error" not in report, report
        oos = report["oos"]
        # the synthetic edge is real: LR must beat raw stored conviction and
        # the base rate on Brier
        assert oos["model_lr"]["brier"] < oos["baseline_conviction_raw"]["brier"]
        assert oos["model_lr"]["brier"] < oos["baseline_base_rate"]["brier"]
        assert report["verdict"]["stored_conviction_worse_than_base_rate"] is True

    def test_too_thin_book_reports_error(self, frame):
        report = FIT.walk_forward_report(frame.head(30), folds=4)
        assert "error" in report


class TestArtifact:
    def test_roundtrip_and_numpy_parity(self, frame, tmp_path, monkeypatch):
        monkeypatch.setattr(FIT, "models_dir", lambda: tmp_path / "models")
        report = FIT.walk_forward_report(frame, folds=4)
        saved = FIT.fit_and_save(frame, report)
        art = FIT.load_artifact()
        assert art["version"] == FIT.ARTIFACT_VERSION
        assert art["n_train"] == 140
        p = FIT.predict_from_artifact(art["spec"], frame[F.feature_columns(frame)])
        assert p.shape == (140,)
        assert np.all((p > 0) & (p < 1))
        # artifact is pure JSON (no pickle) and self-describing
        assert set(art["spec"]) == {"features", "impute", "mean", "scale",
                                    "coef", "intercept"}


class TestDispatcher:
    def test_tool_runs_end_to_end(self, tmp_path, monkeypatch):
        import trading.dispatchers.conviction_fit  # noqa: F401 — registers
        from harness.tools.registry import registry as tool_registry

        conn = get_db()  # per-test HERMES_HOME
        _seed(conn)
        monkeypatch.setattr(FIT, "models_dir", lambda: tmp_path / "models")
        entry = tool_registry.get_entry("conviction_fit")
        res = json.loads(entry.handler({"folds": 4}))
        assert "error" not in res, res
        assert res["dataset"]["n"] == 140
        assert res["artifact"]["version"] == FIT.ARTIFACT_VERSION
        assert "verdict" in res and "calibration_table_lr" in res
        run = conn.execute(
            "SELECT notes_md FROM action_runs WHERE action_type='conviction_fit'"
        ).fetchone()
        assert run is not None and "lr_brier" in run["notes_md"]
        # first fit ever → previous is honest-absent
        assert res["previous"] is None

    def test_second_run_reports_trend(self, tmp_path, monkeypatch):
        import trading.dispatchers.conviction_fit  # noqa: F401 — registers
        from harness.tools.registry import registry as tool_registry

        conn = get_db()
        _seed(conn)
        monkeypatch.setattr(FIT, "models_dir", lambda: tmp_path / "models")
        entry = tool_registry.get_entry("conviction_fit")
        first = json.loads(entry.handler({"folds": 4}))
        assert first["previous"] is None
        second = json.loads(entry.handler({"folds": 4}))
        assert second["previous"] is not None
        assert second["previous"]["n_train"] == 140
        assert second["trend"]["n_delta"] == 0
        assert second["trend"]["brier_delta"] == pytest.approx(0.0, abs=1e-9)
        assert second["trend"]["significance_flipped_true"] in (True, False)


class TestLiveScoring:
    """The wired-in path: one prediction, scored from the newest artifact."""

    def _fit_artifact(self, conn):
        # fit_and_save needs only the frame — skip the walk-forward (it is
        # exercised by TestWalkForward/TestDispatcher and too slow per-test).
        frame = F.build_frame(conn)
        return frame, FIT.fit_and_save(frame, {})

    def test_scores_unresolved_prediction(self, tmp_path):
        from trading.calibration import live
        conn = get_db(tmp_path / "lifecycle.db")
        _seed(conn)
        self._fit_artifact(conn)
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="live", ts=NOW, horizon_ts=NOW + 6 * 3600.0,
            entry_ref_price=100_000.0, near_edge_pct=1.0, far_edge_pct=2.0,
            conviction=0.9, agent="plutus-predict", symbol="BTC",
            strategy_name="cal-s", kind="strategy",
            regime_tag="intraday/ranging/normal",
            support_scores=[
                write.SupportScore(data_point="dp_alpha", score=0.95,
                                   kind="numerical", weight=0.5),
                write.SupportScore(data_point="dp_noise", score=0.5,
                                   kind="numerical", weight=0.5)]))
        out = live.calibrated_conviction(conn, pid)
        assert out is not None
        assert 0.0 < out["p"] < 1.0
        assert out["version"] == FIT.ARTIFACT_VERSION
        # dp_alpha ~0.95 on a model that learned alpha drives outcomes:
        # the calibrated number should read favorably despite nothing else.
        assert out["p"] > 0.5
        conn.close()

    def test_parity_with_training_frame(self, tmp_path):
        """The live row and the batch frame must score IDENTICALLY for the
        same resolved prediction — the one-builder guarantee, asserted."""
        import pandas as pd
        from trading.calibration import live
        conn = get_db(tmp_path / "lifecycle.db")
        _seed(conn)
        frame, _ = self._fit_artifact(conn)
        spec = FIT.load_artifact()["spec"]
        i = len(frame) // 2
        pid = int(frame.iloc[i]["meta_id"])
        p_batch = float(FIT.predict_from_artifact(
            spec, frame.iloc[[i]][F.feature_columns(frame)])[0])
        p_live = live.calibrated_conviction(conn, pid)["p"]
        assert p_live == pytest.approx(p_batch, abs=1e-4)
        conn.close()

    def test_absent_artifact_reads_none(self, tmp_path):
        from trading.calibration import live
        conn = get_db(tmp_path / "lifecycle.db")
        _seed(conn, n=2)
        pid = conn.execute("SELECT id FROM predictions LIMIT 1").fetchone()[0]
        assert live.calibrated_conviction(conn, pid) is None
        conn.close()

    def test_missing_prediction_reads_none(self, tmp_path):
        from trading.calibration import live
        conn = get_db(tmp_path / "lifecycle.db")
        _seed(conn)
        self._fit_artifact(conn)
        assert live.calibrated_conviction(conn, 10**9) is None
        conn.close()
