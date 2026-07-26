"""Cron one-shot timestamps accept the ' UTC' suffix the desk actually writes."""

from datetime import timezone

import pytest

from harness.cron.jobs import parse_schedule


class TestUtcSuffix:
    @pytest.mark.parametrize("raw", [
        "2026-07-26 12:30 UTC",
        "2026-07-26 12:30 utc",
        "2026-07-26T12:30:00 UTC",
    ])
    def test_trailing_utc_accepted(self, raw):
        """Regression: main failed to schedule its own wake on 2026-07-26 with
        'Invalid timestamp' — and ' UTC' is the very format this function
        emits in its own `display` field."""
        out = parse_schedule(raw)
        assert out["kind"] == "once"
        assert out["run_at"].startswith("2026-07-26T12:30")
        assert out["run_at"].endswith("+00:00")

    def test_existing_forms_still_parse(self):
        assert parse_schedule("2026-07-26T12:30:00Z")["kind"] == "once"
        assert parse_schedule("2026-07-26T12:30:00+00:00")["kind"] == "once"
        assert parse_schedule("2026-07-26 12:30")["kind"] == "once"

    def test_still_refuses_genuine_rubbish(self):
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_schedule("2026-07-26 not-a-time UTC")
