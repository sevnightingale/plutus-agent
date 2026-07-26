"""Keyed wakes back off instead of firing every tick.

Regression for 2026-07-26: ops re-enqueued the same perception-staleness wake
every 30 minutes for eleven hours. Each one cost plutus-main a full turn on an
~80k context, and main had to keep its own tally in prose ("11th identical").
"""

import pytest

from harness import wake_queue


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wake_queue, "get_hermes_home", lambda: tmp_path)
    return tmp_path


class TestKeyedBackoff:
    def test_unkeyed_wakes_are_unchanged(self, home):
        for i in range(5):
            wake_queue.enqueue("staleness", f"overdue {i}", source="plutus-ops")
        assert wake_queue.peek() == 5

    def test_repeat_within_backoff_is_suppressed(self, home):
        first = wake_queue.enqueue("staleness", "perception overdue",
                                   key="staleness:perception")
        second = wake_queue.enqueue("staleness", "perception overdue 30m later",
                                    key="staleness:perception")
        assert "ts" in first
        assert second == {"ok": True, "suppressed": True,
                          "key": "staleness:perception", "held": 1}
        assert wake_queue.peek() == 1

    def test_distinct_keys_do_not_shadow_each_other(self, home):
        wake_queue.enqueue("staleness", "perception", key="staleness:perception")
        wake_queue.enqueue("staleness", "predict", key="staleness:predict")
        assert wake_queue.peek() == 2

    def test_fires_again_past_backoff_carrying_the_count(self, home, monkeypatch):
        clock = [1_000_000.0]
        monkeypatch.setattr(wake_queue.time, "time", lambda: clock[0])

        wake_queue.enqueue("staleness", "perception overdue",
                           key="staleness:perception")
        # Two ops ticks inside the first 30-minute window: both held.
        for _ in range(2):
            clock[0] += 600
            wake_queue.enqueue("staleness", "still overdue",
                               key="staleness:perception")
        assert wake_queue.peek() == 1

        clock[0] += 1900          # past the 1800s first backoff
        wake_queue.enqueue("staleness", "still overdue",
                           key="staleness:perception")
        details = [w["detail"] for w in wake_queue.drain()]
        assert len(details) == 2
        assert "2nd consecutive" in details[1]
        assert "2 suppressed since the last" in details[1]

    def test_backoff_doubles_and_caps(self):
        assert wake_queue._backoff_for(1) == 1800
        assert wake_queue._backoff_for(2) == 3600
        assert wake_queue._backoff_for(3) == 7200
        assert wake_queue._backoff_for(20) == wake_queue._BACKOFF_MAX_S

    def test_condition_clearing_makes_it_loud_again(self, home, monkeypatch):
        clock = [1_000_000.0]
        monkeypatch.setattr(wake_queue.time, "time", lambda: clock[0])
        wake_queue.enqueue("escalation", "ACP auth dead", key="integration:acp")
        wake_queue.drain()

        clock[0] += wake_queue._BACKOFF_MAX_S * 2 + 60
        wake_queue.enqueue("escalation", "ACP auth dead again", key="integration:acp")
        detail = wake_queue.drain()[0]["detail"]
        assert "consecutive" not in detail, "a returning condition should read as fresh"

    def test_corrupt_state_file_is_survivable(self, home):
        wake_queue._suppression_path().write_text("{not json")
        rec = wake_queue.enqueue("staleness", "perception", key="staleness:perception")
        assert "ts" in rec
