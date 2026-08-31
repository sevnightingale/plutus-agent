"""Calendar-gated data points — window arithmetic, honest absence, the
disputed-date union. (The dispatcher-side enforcement lives in
test_event_gate.py.)"""

from datetime import date

import pytest

from trading.integrations.calendars import data_points as cal


def _freeze(monkeypatch, iso):
    monkeypatch.setattr(cal, "_iso_today", lambda: date.fromisoformat(iso))


class TestWindowArithmetic:
    def test_outside_window(self, monkeypatch):
        _freeze(monkeypatch, "2026-08-31")
        out = cal.earnings_calendar("NVDA")
        assert out["has_data"] and not out["in_window"]
        assert out["next_event"] == "2026-11-17"

    def test_window_opens_at_margin(self, monkeypatch):
        _freeze(monkeypatch, "2026-11-10")  # 7d before the 11-17 candidate
        assert cal.earnings_calendar("NVDA")["in_window"]

    def test_day_after_still_in_window(self, monkeypatch):
        _freeze(monkeypatch, "2026-11-18")  # 11-17 is -1d: held through day after
        out = cal.earnings_calendar("NVDA")
        assert out["next_event"] == "2026-11-17" and out["in_window"]

    def test_disputed_dates_cover_the_union(self, monkeypatch):
        """Sources disagree (11-17 vs 11-25): once the first candidate ages
        out, the second's window has already begun — no gap a real print
        could fall through."""
        _freeze(monkeypatch, "2026-11-19")  # 11-17 aged out; 11-25 is 6d off
        out = cal.earnings_calendar("NVDA")
        assert out["next_event"] == "2026-11-25" and out["in_window"]

    def test_window_closes_after_last_candidate(self, monkeypatch):
        _freeze(monkeypatch, "2026-11-27")
        out = cal.earnings_calendar("NVDA")
        assert not out["in_window"] and out["next_event"] is None

    def test_lockup_margin_is_14d(self, monkeypatch):
        _freeze(monkeypatch, "2026-08-27")  # 13d before the 09-09 unlock
        assert cal.ipo_lockup_calendar("SPCX")["in_window"]
        _freeze(monkeypatch, "2026-08-25")  # 15d before
        assert not cal.ipo_lockup_calendar("SPCX")["in_window"]


class TestHonestAbsence:
    def test_unknown_symbol_has_no_data(self):
        out = cal.earnings_calendar("BTC")
        assert not out["has_data"]
        assert out["in_window"] is False and out["days_to_next"] is None

    def test_verified_never_fabricated(self, monkeypatch):
        """A table without a verified stamp says None — no default may
        invent a review date nobody performed."""
        monkeypatch.setitem(cal._EARNINGS, "FAKE",
                            {"next": "2027-01-01", "note": ""})
        assert cal.earnings_calendar("FAKE")["verified"] is None
        assert cal.earnings_calendar("NVDA")["verified"] == "2026-08-31"

    def test_case_insensitive_symbol(self):
        assert cal.ipo_lockup_calendar("spcx")["has_data"]
