"""Tests for agent/escalation.py — V2 escalation flag + self-scheduled wake.

Key contract: NEVER notifies the operator. The wake mechanism is a self-
scheduled cron that fires kimi-k2.6 in fresh-session mode, NOT a Telegram
message or anything operator-visible.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import escalation


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert escalation._hermes_home() == tmp_path
    yield tmp_path


# =========================================================================
# Flag read/write/clear
# =========================================================================

class TestFlagReadWrite:
    def test_read_missing_returns_none(self, tmp_home):
        assert escalation.read_escalation_flag() is None

    def test_write_then_read_round_trip(self, tmp_home):
        escalation.write_escalation_flag(
            reason="near_liquidation",
            details_md="BTC long #7 within 1.5× ATR of liquidation",
            set_by_tier="ops",
            set_by_session_id="cron_plutus-ops_2026-05-20T14:30:00",
            trigger_observation_id=1234,
        )
        flag = escalation.read_escalation_flag()
        assert flag is not None
        assert flag["reason"] == "near_liquidation"
        assert "BTC long #7" in flag["details_md"]
        assert flag["set_by_tier"] == "ops"
        assert flag["set_by_session_id"] == "cron_plutus-ops_2026-05-20T14:30:00"
        assert flag["trigger_observation_id"] == 1234
        assert flag["set_at"] > 0

    def test_write_overwrites_existing(self, tmp_home):
        escalation.write_escalation_flag(
            reason="near_liquidation", details_md="first",
            set_by_tier="ops",
        )
        escalation.write_escalation_flag(
            reason="equity_drop_10pct", details_md="second",
            set_by_tier="ops",
        )
        flag = escalation.read_escalation_flag()
        assert flag["reason"] == "equity_drop_10pct"
        assert flag["details_md"] == "second"

    def test_clear_returns_true_when_present(self, tmp_home):
        escalation.write_escalation_flag(
            reason="total_drift", details_md="d",
            set_by_tier="ops",
        )
        assert escalation.clear_escalation_flag() is True
        assert escalation.read_escalation_flag() is None
        assert not escalation._flag_path().exists()

    def test_clear_returns_false_when_absent(self, tmp_home):
        assert escalation.clear_escalation_flag() is False

    def test_corrupt_file_returns_none(self, tmp_home):
        escalation._flag_path().write_text("{not json", encoding="utf-8")
        assert escalation.read_escalation_flag() is None


# =========================================================================
# Approved reason enforcement
# =========================================================================

class TestApprovedReasons:
    def test_known_reasons_pass(self, tmp_home):
        for reason in escalation.APPROVED_REASONS:
            escalation.write_escalation_flag(
                reason=reason, details_md="x", set_by_tier="ops",
            )
            assert escalation.read_escalation_flag()["reason"] == reason

    def test_unknown_reason_raises(self, tmp_home):
        with pytest.raises(ValueError, match="approved list"):
            escalation.write_escalation_flag(
                reason="plutus_made_a_silly_trade",
                details_md="not a real escalation",
                set_by_tier="ops",
            )

    def test_approved_reasons_contains_expected(self):
        """Sanity-check the approved list matches architecture-v2 §10."""
        expected = {
            "near_liquidation",
            "equity_drop_10pct",
            "sl_approaching_low_conv",
            "total_drift",
            "watcher_catastrophic",
        }
        assert escalation.APPROVED_REASONS == expected


# =========================================================================
# schedule_escalation_wake — model + provider override
# =========================================================================

class TestScheduleWake:
    def test_creates_legacy_path_one_shot_cron(self, tmp_home):
        """schedule_escalation_wake calls cron.jobs.create_job with model
        override → routes through legacy fresh-session per A.2."""
        captured = {}

        def fake_create_job(**kwargs):
            captured.update(kwargs)
            return {"id": "mock-job-id", "name": kwargs.get("name", "")}

        with patch("cron.jobs.create_job", side_effect=fake_create_job):
            job_id = escalation.schedule_escalation_wake()

        assert job_id == "mock-job-id"
        # Model override is the key V2 invariant — must be present so the
        # cron routes through legacy fresh-session (NOT unified-session).
        assert captured["model"] == "kimi-k2.6"
        assert captured["provider"] == "opencode-go"
        # One-shot.
        assert captured["repeat"] == 1
        # Wake prompt instructs the agent to read the flag.
        assert "escalation.flag" in captured["prompt"]
        assert "kind=\"escalation_response\"" in captured["prompt"] \
            or "escalation_response" in captured["prompt"]
        # No origin → legacy path (unified-injection-eligible needs origin).
        assert captured["origin"] is None

    def test_custom_delay_and_model(self, tmp_home):
        captured = {}

        def fake_create_job(**kwargs):
            captured.update(kwargs)
            return {"id": "j", "name": ""}

        with patch("cron.jobs.create_job", side_effect=fake_create_job):
            escalation.schedule_escalation_wake(
                delay="30s", model="custom-model", provider="custom-provider",
            )

        assert captured["schedule"] == "30s"
        assert captured["model"] == "custom-model"
        assert captured["provider"] == "custom-provider"

    def test_create_job_failure_returns_none_does_not_raise(self, tmp_home):
        """Cron scheduling failure must not crash the ops tick — the flag
        is still set on disk and the next regular beat will see it."""
        def fake_create_job(**kwargs):
            raise RuntimeError("cron system down")

        with patch("cron.jobs.create_job", side_effect=fake_create_job):
            result = escalation.schedule_escalation_wake()

        assert result is None


# =========================================================================
# Atomic write
# =========================================================================

class TestAtomicWrite:
    def test_no_tmp_file_left_on_success(self, tmp_home):
        escalation.write_escalation_flag(
            reason="total_drift", details_md="d", set_by_tier="ops",
        )
        tmp_files = [p for p in tmp_home.iterdir() if p.name.endswith(".tmp")]
        assert tmp_files == []
