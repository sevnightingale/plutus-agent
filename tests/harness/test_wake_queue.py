"""Serialized wake queue — enqueue, drain-all, collapse semantics."""

import pytest

from harness import wake_queue


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wake_queue, "get_hermes_home", lambda: tmp_path)
    return tmp_path


class TestWakeQueue:
    def test_enqueue_drain_round_trip(self, home):
        wake_queue.enqueue("watcher", "BTC crossed 104k", source="plutus-watchers")
        wake_queue.enqueue("staleness", "perception overdue", source="plutus-ops")
        assert wake_queue.peek() == 2
        wakes = wake_queue.drain()
        assert [w["reason"] for w in wakes] == ["watcher", "staleness"]
        assert wake_queue.peek() == 0
        assert wake_queue.drain() == []

    def test_invalid_reason_refused(self, home):
        with pytest.raises(ValueError, match="reason"):
            wake_queue.enqueue("vibes", "x")

    def test_drain_collapses_into_one_prompt(self, home):
        wake_queue.enqueue("watcher", "price alert")
        wake_queue.enqueue("escalation", "SL missing on-venue", source="plutus-ops")
        prompt = wake_queue.format_wake_prompt(wake_queue.drain())
        assert "2 pending trigger(s)" in prompt
        assert "price alert" in prompt
        assert "SL missing on-venue" in prompt
        assert "schedule the next wake" in prompt

    def test_prompt_header_carries_absolute_utc_stamp(self, home):
        """The wake header is main's live clock; relative ages are not enough.

        The session anchor is stamped once per session, so in the persistent
        gateway session it drifts. A wake is exactly when a current time
        matters.
        """
        import re

        wake_queue.enqueue("staleness", "perception overdue", source="plutus-ops")
        prompt = wake_queue.format_wake_prompt(wake_queue.drain())
        assert re.match(
            r"\[WAKE @ "
            r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) "
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\] 1 pending trigger",
            prompt,
        ), f"wake header lacks an absolute UTC stamp: {prompt.splitlines()[0]!r}"

    def test_malformed_line_dropped_not_fatal(self, home):
        wake_queue.enqueue("watcher", "good")
        with open(wake_queue._queue_path(), "a") as f:
            f.write("not json\n")
        wake_queue.enqueue("schedule", "also good")
        wakes = wake_queue.drain()
        assert [w["detail"] for w in wakes] == ["good", "also good"]
