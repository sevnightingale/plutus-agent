"""Cheap-LLM predict/conviction tools — with call_llm mocked."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading.dispatchers import predict_tools
from trading.strategies.files import Strategy


def _resp_tool_call(payload: dict):
    """Fake call_llm response that forced the 'emit' tool."""
    tc = SimpleNamespace(function=SimpleNamespace(arguments=json.dumps(payload)))
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(tool_calls=[tc], content=None))])


def _resp_content(text: str):
    """Fake call_llm response with no tool call — content only (Codex path)."""
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(tool_calls=None, content=text))])


def _patch_call_llm(monkeypatch, resp):
    import harness.agent.auxiliary_client as aux
    monkeypatch.setattr(aux, "call_llm", lambda **kw: resp)


def _strategy():
    return Strategy(
        name="funding-flush", status="test", timescale="intraday",
        mechanism_family="flow", file_path=Path("funding-flush.md"),
        data_points=[
            {"name": "ta_rsi", "params": {"symbol": "BTC"}, "weight": 0.5},
            {"name": "hl_funding", "weight": 0.5},
        ],
        body_md="# Hypothesis\nMean reversion after a flush.\n# Mechanism\nForced sellers.\n",
    )


# ── structured-output plumbing ───────────────────────────────────────────────

class TestStructuredCall:
    def test_parse_json_loose_strips_fences(self):
        assert predict_tools._parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
        assert predict_tools._parse_json_loose('here it is: {"a": 2} ok') == {"a": 2}

    def test_forced_tool_call(self, monkeypatch):
        _patch_call_llm(monkeypatch, _resp_tool_call({"near_pct": 3.0}))
        out = predict_tools._structured_call(
            task="t", system="s", user="u", schema={"type": "object"})
        assert out == {"near_pct": 3.0}

    def test_content_fallback(self, monkeypatch):
        _patch_call_llm(monkeypatch, _resp_content('{"near_pct": 4.0}'))
        out = predict_tools._structured_call(
            task="t", system="s", user="u", schema={"type": "object"})
        assert out == {"near_pct": 4.0}

    def test_never_sends_tool_choice(self, monkeypatch):
        # DeepSeek thinking mode 400s on tool_choice — structured output must
        # use strict-JSON-in-prompt + content parsing, never forced tool calls.
        captured = {}
        import harness.agent.auxiliary_client as aux

        def fake(**kw):
            captured.update(kw)
            return _resp_content('{"x": 1}')

        monkeypatch.setattr(aux, "call_llm", fake)
        predict_tools._structured_call(
            task="t", system="s", user="u", schema={"type": "object"})
        assert "tool_choice" not in captured
        assert not captured.get("tools")


# ── predict_draft ────────────────────────────────────────────────────────────

class TestPredictDraft:
    def test_returns_zone(self, monkeypatch):
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: _strategy())
        _patch_call_llm(monkeypatch, _resp_tool_call(
            {"near_pct": 2.0, "far_pct": 5.0, "horizon_hours": 12, "rationale": "flush"}))
        res = json.loads(predict_tools._predict_draft(
            {"strategy_name": "funding-flush", "symbol": "BTC"}))
        assert res["near_pct"] == 2.0 and res["far_pct"] == 5.0
        assert res["symbol"] == "BTC"

    def test_unknown_strategy(self, monkeypatch):
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: None)
        res = json.loads(predict_tools._predict_draft(
            {"strategy_name": "nope", "symbol": "BTC"}))
        assert "error" in res


# ── conviction_score ─────────────────────────────────────────────────────────

class TestConvictionScore:
    def test_aggregates_with_declared_weights(self, monkeypatch):
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: _strategy())
        monkeypatch.setattr(predict_tools, "_fetch_reading", lambda dp: (50.0, "reading", None))
        _patch_call_llm(monkeypatch, _resp_tool_call({"scores": [
            {"dp_key": "ta_rsi(symbol=BTC)", "score": 0.8, "kind": "narrative",
             "reasoning": "oversold"},
            {"dp_key": "hl_funding", "score": 0.6, "kind": "narrative",
             "reasoning": "negative funding"},
        ]}))
        res = json.loads(predict_tools._conviction_score({"strategy_name": "funding-flush"}))
        # (0.5*0.8 + 0.5*0.6) / 1.0 = 0.7
        assert res["conviction"] == pytest.approx(0.7)
        assert len(res["support_scores"]) == 2
        assert res["missing"] == []

    def test_missing_score_excluded(self, monkeypatch):
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: _strategy())
        monkeypatch.setattr(predict_tools, "_fetch_reading", lambda dp: (50.0, "reading", None))
        # only one DP scored; the other is missing → conviction from the one present
        _patch_call_llm(monkeypatch, _resp_tool_call({"scores": [
            {"dp_key": "ta_rsi(symbol=BTC)", "score": 0.9, "kind": "narrative",
             "reasoning": "deeply oversold"},
        ]}))
        res = json.loads(predict_tools._conviction_score({"strategy_name": "funding-flush"}))
        assert res["conviction"] == pytest.approx(0.9)  # only the scored DP counts
        assert "hl_funding" in res["missing"]

    def test_unreasoned_narrative_score_dropped(self, monkeypatch):
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: _strategy())
        monkeypatch.setattr(predict_tools, "_fetch_reading", lambda dp: (50.0, "reading", None))
        _patch_call_llm(monkeypatch, _resp_tool_call({"scores": [
            {"dp_key": "ta_rsi(symbol=BTC)", "score": 0.9, "kind": "narrative", "reasoning": ""},
            {"dp_key": "hl_funding", "score": 0.6, "kind": "narrative", "reasoning": "ok"},
        ]}))
        res = json.loads(predict_tools._conviction_score({"strategy_name": "funding-flush"}))
        assert res["conviction"] == pytest.approx(0.6)  # the unreasoned one is dropped
        assert "ta_rsi(symbol=BTC)" in res["missing"]

    def test_unusable_reading_forced_missing(self, monkeypatch):
        """Issue 4: a TRUNCATED / fetch-failed reading is excluded
        deterministically even when the scoring LLM (wrongly) returns a number
        for it — honest absence, never a guessed 0.5 neutral."""
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: _strategy())

        def _fake_fetch(dp):
            if dp["name"] == "hl_funding":
                return (None, "<TRUNCATED dp=hl_funding kept=0B/9000B — NO RENDERER>",
                        "no-renderer-truncated")
            return (50.0, "reading", None)
        monkeypatch.setattr(predict_tools, "_fetch_reading", _fake_fetch)

        # the LLM ignores the instruction and scores the truncated DP anyway
        _patch_call_llm(monkeypatch, _resp_tool_call({"scores": [
            {"dp_key": "ta_rsi(symbol=BTC)", "score": 0.8, "kind": "narrative",
             "reasoning": "oversold"},
            {"dp_key": "hl_funding", "score": 0.5, "kind": "narrative",
             "reasoning": "neutral guess"},
        ]}))
        res = json.loads(predict_tools._conviction_score({"strategy_name": "funding-flush"}))
        assert res["conviction"] == pytest.approx(0.8)  # only the usable DP counts
        assert "hl_funding" in res["missing"]  # truncated → missing, not 0.5


# ── ops rescore loop (conviction trajectory) ─────────────────────────────────

def _zone_draft(strategy_name):
    from trading.lifecycle import write
    return write.PredictionDraft(
        claim_md="zone", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
        near_edge_pct=5.0, far_edge_pct=10.0, conviction=0.7,
        agent="plutus-predict", symbol="BTC", strategy_name=strategy_name, kind="adhoc")


class TestRescoreLoop:
    def test_rescore_writes_one_trajectory_row_per_open_prediction(self, monkeypatch):
        from trading.dispatchers import resolution
        from trading.lifecycle import write
        from trading.lifecycle.db import get_db

        conn = get_db()
        p1 = write.record_prediction(conn, _zone_draft("s1"))
        p2 = write.record_prediction(conn, _zone_draft("s1"))  # same strategy
        # one cheap scoring pass per strategy, applied to both open predictions
        monkeypatch.setattr(
            predict_tools, "score_strategy",
            lambda name, regime=None: {"strategy_name": name, "conviction": 0.66,
                                       "support_scores": [], "missing": []})

        res = json.loads(resolution._rescore_open({}))
        assert res["rescored"][0]["n_predictions"] == 2
        rows = conn.execute(
            "SELECT prediction_id, conviction FROM prediction_evaluations "
            "ORDER BY prediction_id").fetchall()
        assert [r["prediction_id"] for r in rows] == [p1, p2]
        assert [r["conviction"] for r in rows] == [0.66, 0.66]

    def test_rescore_records_strategy_failures(self, monkeypatch):
        from trading.dispatchers import resolution
        from trading.lifecycle import write
        from trading.lifecycle.db import get_db

        conn = get_db()
        write.record_prediction(conn, _zone_draft("s1"))

        def boom(name, regime=None):
            raise RuntimeError("scoring exploded")

        monkeypatch.setattr(predict_tools, "score_strategy", boom)
        res = json.loads(resolution._rescore_open({}))
        assert res["rescored"] == []
        assert res["failures"][0]["strategy_name"] == "s1"
        assert conn.execute(
            "SELECT COUNT(*) FROM prediction_evaluations").fetchone()[0] == 0


# ── declared normalizers: deterministic scoring path ─────────────────────────

def _norm_strategy():
    return Strategy(
        name="norm-mix", status="test", timescale="intraday",
        mechanism_family="mean_reversion", file_path=Path("norm-mix.md"),
        data_points=[
            # mean-reversion RSI: oversold reads as support (lo > hi inverts)
            {"name": "ta_rsi", "params": {"symbol": "BTC"}, "weight": 0.5,
             "normalizer": {"name": "linear_band", "params": {"lo": 70, "hi": 20}}},
            {"name": "hl_orderbook", "weight": 0.5},  # contextual → LLM
        ],
        body_md="# Hypothesis\nh\n# Mechanism\nm\n",
    )


class TestDeclaredNormalizers:
    def test_normalized_dp_scored_deterministically(self, monkeypatch):
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: _norm_strategy())
        monkeypatch.setattr(predict_tools, "_fetch_reading",
                            lambda dp: (45.0, "reading", None))
        _patch_call_llm(monkeypatch, _resp_tool_call({"scores": [
            {"dp_key": "hl_orderbook", "score": 0.8, "kind": "narrative",
             "reasoning": "bid-heavy"}]}))
        res = json.loads(predict_tools._conviction_score({"strategy_name": "norm-mix"}))
        by_dp = {s["data_point"]: s for s in res["support_scores"]}
        rsi = by_dp["ta_rsi(symbol=BTC)"]
        # linear_band(45, lo=70, hi=20) = (45-70)/(20-70) = 0.5
        assert rsi["score"] == pytest.approx(0.5)
        assert rsi["kind"] == "numerical"
        assert rsi["normalizer"] == "linear_band(hi=20,lo=70)"
        assert rsi["reasoning_md"] is None
        assert by_dp["hl_orderbook"]["score"] == pytest.approx(0.8)
        assert res["conviction"] == pytest.approx(0.65)  # (0.5*0.5 + 0.5*0.8)

    def test_all_normalized_skips_llm_entirely(self, monkeypatch):
        s = _norm_strategy()
        s.data_points = [s.data_points[0]]
        s.data_points[0]["weight"] = 1.0
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: s)
        monkeypatch.setattr(predict_tools, "_fetch_reading",
                            lambda dp: (20.0, "reading", None))
        import harness.agent.auxiliary_client as aux
        def boom(**kw):
            raise AssertionError("LLM must not be called when all DPs are normalized")
        monkeypatch.setattr(aux, "call_llm", boom)
        res = json.loads(predict_tools._conviction_score({"strategy_name": "norm-mix"}))
        assert res["conviction"] == pytest.approx(1.0)  # RSI 20 = fully oversold
        assert res["support_scores"][0]["normalizer"] == "linear_band(hi=20,lo=70)"

    def test_normalizer_without_numeric_scores_missing(self, monkeypatch):
        s = _norm_strategy()
        monkeypatch.setattr(predict_tools, "_load_strategy", lambda n: s)
        monkeypatch.setattr(
            predict_tools, "_fetch_reading",
            lambda dp: (None if dp["name"] == "ta_rsi" else 1.0, "reading", None))
        _patch_call_llm(monkeypatch, _resp_tool_call({"scores": [
            {"dp_key": "hl_orderbook", "score": 0.6, "kind": "narrative",
             "reasoning": "thin book"}]}))
        res = json.loads(predict_tools._conviction_score({"strategy_name": "norm-mix"}))
        # RSI is missing (declared normalizer, no numeric) — never LLM-scored
        assert "ta_rsi(symbol=BTC)" in res["missing"]
        assert res["conviction"] == pytest.approx(0.6)
