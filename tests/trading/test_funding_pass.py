"""The funding pass — mechanical funding as an actual mechanism. Guards,
the fill wake main narrates from, skip observations, and the memory that
stops a dead candidate being retried every minute."""

from __future__ import annotations

import json

import pytest

from harness import wake_queue
from trading.lifecycle import funding
from trading.lifecycle.db import get_db


@pytest.fixture(autouse=True)
def fresh_memory():
    funding._dead_candidates.clear()
    yield
    funding._dead_candidates.clear()


CAND = {"prediction_id": 41, "strategy_name": "flow-a", "symbol": "BTC",
        "lane": "pilot", "conviction": 0.71, "conviction_calibrated": 0.62,
        "ev_pct": 0.4, "p_win": 0.6, "stop_pct": 1.2, "reward_pct": 2.5,
        "target": "far"}


def _patch_selection(monkeypatch, cand):
    import trading.lifecycle.queries as queries
    monkeypatch.setattr(queries, "best_actionable_prediction",
                        lambda conn, **kw: cand)


def _patch_open(monkeypatch, result):
    calls = []

    def fake_open(args):
        calls.append(args)
        return json.dumps(result)

    import trading.dispatchers.desk_execution as de
    monkeypatch.setattr(de, "_desk_open", fake_open)
    return calls


def test_halt_stops_everything(monkeypatch):
    from harness.constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "HALT").touch()
    calls = _patch_open(monkeypatch, {"ok": True})
    res = funding.fund_pass(get_db())
    assert res == {"acted": False, "why": "HALT"}
    assert calls == []


def test_no_candidate_is_a_cheap_pass(monkeypatch):
    calls = _patch_open(monkeypatch, {"ok": True})
    res = funding.fund_pass(get_db())
    assert res["why"] == "no fundable candidate"
    assert calls == []


def test_fill_funds_with_honest_attribution_and_wakes_the_voice(monkeypatch):
    _patch_selection(monkeypatch, dict(CAND))
    calls = _patch_open(monkeypatch, {"ok": True, "position_id": 19})
    res = funding.fund_pass(get_db())
    assert res["filled"] is True and res["position_id"] == 19
    assert calls[0]["agent"] == funding.SOURCE
    thesis = calls[0]["thesis_md"]
    assert "flow-a" in thesis and "0.62" in thesis and "#41" in thesis
    wakes = wake_queue.drain()
    assert any("forum" in w["detail"] and '"position_id": 19' in w["detail"]
               for w in wakes)


def test_candidate_refusal_is_remembered_and_recorded(monkeypatch):
    _patch_selection(monkeypatch, dict(CAND))
    _patch_open(monkeypatch, {"ok": False,
                              "refused": "RR at live price below threshold"})
    conn = get_db()
    res = funding.fund_pass(conn)
    assert res["filled"] is False and res["transient"] is False
    row = conn.execute(
        "SELECT text_md, agent FROM observations "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and "skipped prediction #41" in row["text_md"]
    assert row["agent"] == funding.SOURCE
    # Second pass: same candidate, no retry.
    res2 = funding.fund_pass(conn)
    assert res2 == {"acted": False, "why": "candidate #41 already refused"}


def test_transient_refusal_is_not_remembered(monkeypatch):
    _patch_selection(monkeypatch, dict(CAND))
    _patch_open(monkeypatch, {"ok": False,
                              "refused": "trade path is not READY"})
    conn = get_db()
    res = funding.fund_pass(conn)
    assert res["transient"] is True
    assert 41 not in funding._dead_candidates
    # No skip observation for a transient condition.
    n = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert n == 0


class TestCalendar:
    def test_printed_since_finds_the_print(self):
        from trading.integrations.macro import calendar as cal
        ts, label = cal.EVENTS[5]  # FOMC 2026-09-16
        ev = cal.printed_since(ts - 3600, now=ts + 600)
        assert ev is not None and ev["label"] == label

    def test_nothing_printed_is_none(self):
        from trading.integrations.macro import calendar as cal
        ts, _ = cal.EVENTS[5]
        assert cal.printed_since(ts + 60, now=ts + 3600) is None

    def test_predict_due_wakes_on_a_print(self, monkeypatch):
        import time as _time

        from harness import desk_events
        from trading.lifecycle import write

        conn = get_db()
        ts, _ = __import__(
            "trading.integrations.macro.calendar",
            fromlist=["EVENTS"]).EVENTS[5]
        write.record_action_run(conn, action_type="predict", agent="t",
                                ok=True, ts=ts - 60)
        due, reason = desk_events.predict_due(conn, now=ts + 300)
        assert due and "FOMC" in reason

    def test_labels_match_their_epochs(self):
        from datetime import datetime, timezone

        from trading.integrations.macro import calendar as cal
        for ts, label in cal.EVENTS:
            stamp = datetime.fromtimestamp(ts, timezone.utc)
            assert stamp.strftime("%Y-%m-%d %H:00Z") in label