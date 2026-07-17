"""Polymarket integration — parsing, ladder math, event selection.

Hermetic: fixtures stand in for Gamma API payloads; no network. The
client's fail-loud contract is the point — an unparseable payload or a
series with no live event must raise, never degrade to a guessed value.
"""

import json

import pytest

from trading.integrations.polymarket import _client as pc
from trading.integrations.polymarket import data_points as pdp


def _market(strike_title, p_yes, slug="m"):
    return {
        "slug": slug,
        "groupItemTitle": strike_title,
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([str(p_yes), str(1 - p_yes)]),
    }


def _ladder_event(end="2099-01-01T16:00:00Z"):
    return {
        "slug": "bitcoin-above-on-jan-1",
        "title": "Bitcoin above ___ on Jan 1?",
        "endDate": end,
        "markets": [
            _market("62,000", 0.80),
            _market("58,000", 0.95),
            _market("66,000", 0.20),
            _market("64,000", 0.50),
            _market("60,000", 0.90),
        ],
    }


class TestParsing:
    def test_parse_strike_forms(self):
        assert pdp._parse_strike("54,000") == 54000.0
        assert pdp._parse_strike("$58,500") == 58500.0
        assert pdp._parse_strike("70K") == 70000.0
        assert pdp._parse_strike("No change") is None
        assert pdp._parse_strike("") is None
        assert pdp._parse_strike(None) is None

    def test_outcome_prices_decodes_json_strings(self):
        pairs = pc.outcome_prices(_market("60,000", 0.75))
        assert pairs == [("Yes", 0.75), ("No", 0.25)]

    def test_outcome_prices_tolerates_real_lists(self):
        m = {"outcomes": ["Up", "Down"], "outcomePrices": ["0.6", "0.4"]}
        assert pc.outcome_prices(m) == [("Up", 0.6), ("Down", 0.4)]

    def test_outcome_prices_mismatch_raises(self):
        m = {"outcomes": json.dumps(["Yes"]), "outcomePrices": json.dumps(["0.5", "0.5"])}
        with pytest.raises(RuntimeError, match="unrecognized outcome shape"):
            pc.outcome_prices(m)

    def test_yes_price_requires_yes(self):
        m = {"outcomes": json.dumps(["Up", "Down"]),
             "outcomePrices": json.dumps(["0.6", "0.4"])}
        with pytest.raises(RuntimeError, match="no 'Yes' outcome"):
            pc.yes_price(m)


class TestLadderMath:
    def test_build_ladder_sorts_and_skips_unparseable(self):
        markets = _ladder_event()["markets"] + [_market("N/A", 0.5)]
        ladder = pdp._build_ladder(markets)
        assert [r["strike"] for r in ladder] == [58000, 60000, 62000, 64000, 66000]
        assert [r["p_above"] for r in ladder] == [0.95, 0.90, 0.80, 0.50, 0.20]

    def test_build_ladder_needs_two_strikes(self):
        with pytest.raises(RuntimeError, match="not a ladder"):
            pdp._build_ladder([_market("60,000", 0.5), _market("junk", 0.5)])

    def test_interpolation_midpoint(self):
        ladder = pdp._build_ladder(_ladder_event()["markets"])
        p, extrapolated = pdp._interp_p_above(ladder, 63000.0)
        assert not extrapolated
        assert p == pytest.approx(0.65)  # halfway 62k(0.80) → 64k(0.50)

    def test_interpolation_clamps_and_flags_outside(self):
        ladder = pdp._build_ladder(_ladder_event()["markets"])
        p_lo, ex_lo = pdp._interp_p_above(ladder, 50000.0)
        p_hi, ex_hi = pdp._interp_p_above(ladder, 80000.0)
        assert (p_lo, ex_lo) == (0.95, True)
        assert (p_hi, ex_hi) == (0.20, True)

    def test_implied_median_crossing(self):
        ladder = pdp._build_ladder(_ladder_event()["markets"])
        # 62k(0.80) → 64k(0.50): p hits 0.5 exactly at 64k
        assert pdp._implied_median(ladder) == pytest.approx(64000.0)

    def test_implied_median_none_without_crossing(self):
        ladder = [{"strike": 1.0, "p_above": 0.9}, {"strike": 2.0, "p_above": 0.8}]
        assert pdp._implied_median(ladder) is None


class TestCurrentEvent:
    def test_picks_soonest_future_event(self, monkeypatch):
        events = [
            {"slug": "later", "endDate": "2099-06-01T00:00:00Z"},
            {"slug": "past", "endDate": "2001-01-01T00:00:00Z"},
            {"slug": "soonest", "endDate": "2099-01-01T00:00:00Z"},
        ]
        monkeypatch.setattr(pc, "_get", lambda path, params: events)
        assert pc.current_event("any")["slug"] == "soonest"

    def test_all_past_raises(self, monkeypatch):
        monkeypatch.setattr(pc, "_get", lambda path, params: [
            {"slug": "past", "endDate": "2001-01-01T00:00:00Z"}])
        with pytest.raises(RuntimeError, match="past its endDate"):
            pc.current_event("any")

    def test_empty_series_raises(self, monkeypatch):
        monkeypatch.setattr(pc, "_get", lambda path, params: [])
        with pytest.raises(RuntimeError, match="no active Polymarket events"):
            pc.current_event("bad-slug")


class TestDataPoints:
    def test_price_ladder(self, monkeypatch):
        monkeypatch.setattr(pdp, "current_event", lambda series: _ladder_event())
        monkeypatch.setattr(pdp, "_hl_price", lambda sym: {"price": 63000.0})
        out = pdp.poly_price_ladder("BTC")
        assert out["p_above_spot"] == pytest.approx(0.65)
        assert not out["extrapolated"]
        assert out["implied_median"] == pytest.approx(64000.0)
        assert len(out["ladder"]) == 5
        assert out["hours_to_close"] > 0

    def test_price_ladder_unknown_symbol(self):
        with pytest.raises(ValueError, match="no Polymarket strike-ladder series"):
            pdp.poly_price_ladder("DOGE")

    def test_event_odds_multi_market(self, monkeypatch):
        event = {
            "slug": "fed-decision", "title": "Fed Decision?",
            "endDate": "2099-01-01T00:00:00Z",
            "markets": [
                {"groupItemTitle": "25 bps increase",
                 "outcomes": json.dumps(["Yes", "No"]),
                 "outcomePrices": json.dumps(["0.04", "0.96"])},
                {"groupItemTitle": "No change",
                 "outcomes": json.dumps(["Yes", "No"]),
                 "outcomePrices": json.dumps(["0.95", "0.05"])},
            ],
        }
        monkeypatch.setattr(pdp, "current_event", lambda slug: event)
        out = pdp.poly_event_odds("fomc")
        assert out["top_outcome"] == "No change"
        assert out["p_top"] == pytest.approx(0.95)
        assert [r["outcome"] for r in out["outcomes"]] == ["No change", "25 bps increase"]

    def test_event_odds_single_market_uses_own_outcomes(self, monkeypatch):
        event = {
            "slug": "btc-updown", "title": "BTC Up or Down?",
            "endDate": "2099-01-01T00:00:00Z",
            "markets": [{"outcomes": json.dumps(["Up", "Down"]),
                         "outcomePrices": json.dumps(["0.62", "0.38"])}],
        }
        monkeypatch.setattr(pdp, "current_event", lambda slug: event)
        out = pdp.poly_event_odds("btc-up-or-down-daily")
        assert out["top_outcome"] == "Up"
        assert out["p_top"] == pytest.approx(0.62)

    def test_event_odds_no_markets_raises(self, monkeypatch):
        monkeypatch.setattr(pdp, "current_event",
                            lambda slug: {"slug": "x", "markets": []})
        with pytest.raises(RuntimeError, match="no markets"):
            pdp.poly_event_odds("x")
