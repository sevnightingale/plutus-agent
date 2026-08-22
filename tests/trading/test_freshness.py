"""Perception-freshness gate for prediction authoring."""

from __future__ import annotations

import trading.perception.cache as cache
from trading.perception import freshness


def test_effective_max_age_floors_at_30min_keeps_slow_budget():
    # fast DPs get the 30-min authoring floor (not their tight 60s cache budget)
    assert freshness.effective_max_age("hl_price") == 1800.0
    # naturally-slow signals keep their own (longer) budget — not false-blocked
    assert freshness.effective_max_age("macro_vix") == 14400.0


def test_effective_max_age_is_timescale_aware():
    # freshness is an intraday concern: swing rides the 4h floor, position 12h
    assert freshness.effective_max_age("hl_price", "intraday") == 1800.0
    assert freshness.effective_max_age("hl_price", "swing") == 4 * 3600.0
    assert freshness.effective_max_age("hl_price", "position") == 12 * 3600.0
    # a budget longer than the floor still wins (macro_vix at 14400 > 4h floor)
    assert freshness.effective_max_age("macro_vix", "swing") == 14400.0
    # unknown/absent timescale takes the STRICTEST floor — stale-averse
    assert freshness.effective_max_age("hl_price", "nonsense") == 1800.0
    assert freshness.effective_max_age("hl_price", None) == 1800.0


def test_stale_data_points_timescale_floor(monkeypatch):
    now = 1_000_000.0
    ck = cache._canonical_key
    state = {"data_points": {
        ck("hl_cvd", {"symbol": "BTC"}): {"fetched_at": now - 2000},  # 33 min old
    }}
    monkeypatch.setattr(freshness, "read_perception_state", lambda: state,
                        raising=False)
    import trading.perception.cache as cache_mod
    monkeypatch.setattr(cache_mod, "read_perception_state", lambda: state)
    dps = [{"name": "hl_cvd", "params": {"symbol": "BTC"}}]
    # stale for an intraday book (>1800s), fresh for a swing book (<4h)
    assert [e["reason"] for e in
            freshness.stale_data_points(dps, now=now, timescale="intraday")] == ["stale"]
    assert freshness.stale_data_points(dps, now=now, timescale="swing") == []


def test_stale_missing_and_fresh(monkeypatch):
    now = 1_000_000.0
    ck = cache._canonical_key
    state = {"data_points": {
        ck("hl_price", {"symbol": "BTC"}): {"fetched_at": now - 100},    # fresh
        ck("hl_cvd", {"symbol": "BTC"}):   {"fetched_at": now - 2000},   # STALE (>1800)
        ck("macro_vix", None):             {"fetched_at": now - 5000},   # fresh (<14400)
    }}
    monkeypatch.setattr(cache, "read_perception_state", lambda: state)

    dps = [{"name": "hl_price", "params": {"symbol": "BTC"}},
           {"name": "hl_cvd", "params": {"symbol": "BTC"}},
           {"name": "macro_vix"},
           {"name": "ta_rsi", "params": {"symbol": "BTC"}}]  # never fetched → missing
    flagged = freshness.stale_data_points(dps, now=now)
    assert {e["name"]: e["reason"] for e in flagged} == {
        "hl_cvd": "stale", "ta_rsi": "missing"}


def test_empty_when_all_fresh(monkeypatch):
    now = 1_000_000.0
    ck = cache._canonical_key
    monkeypatch.setattr(
        cache, "read_perception_state",
        lambda: {"data_points": {ck("hl_price", None): {"fetched_at": now - 10}}})
    assert freshness.stale_data_points([{"name": "hl_price"}], now=now) == []
