"""desk_open_position / desk_close_position / rescore_position — the money path,
venue mocked. Execution is one deterministic derive path: prediction_id + thesis
→ gated, sized, placed, verified, naked-aborted."""

import json
import time

import pytest

import trading.dispatchers.desk_execution  # noqa: F401 — registers on import
from harness.tools.registry import registry as tool_registry
from trading.lifecycle import queries, write
from trading.lifecycle.db import get_db


def _call(name, args):
    return json.loads(tool_registry.get_entry(name).handler(args))


def _seed_strategy(name, status, book, symbol="BTC"):
    """Insert a strategy with a resolved book + return a fresh OPEN prediction.

    book: list of (far, outcome, mae, reached_far[, reached_near]).
    reached_far = tagged TP (win on the far sim); reached_near = tagged the
    near edge (win on the near sim).
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO strategies (name,file_path,status,timescale,"
        "mechanism_family,created_at,updated_at) VALUES "
        "(?,?,?,'intraday','flow',0,0)", (name, f"{name}.md", status))
    for far, outcome, mae, reached, *rest in book:
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="z", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
            near_edge_pct=far / 2.0, far_edge_pct=far, conviction=0.72,
            agent="plutus-predict", symbol=symbol, strategy_name=name, kind="strategy"))
        write.resolve_prediction(conn, pid, outcome, resolved_by="r",
                                 realized_value={"mae_pct": mae})
        if reached:
            conn.execute("UPDATE predictions SET reached_far_at=? WHERE id=?",
                         (time.time(), pid))
        if rest and rest[0]:
            conn.execute("UPDATE predictions SET reached_near_at=? WHERE id=?",
                         (time.time(), pid))
    open_pid = write.record_prediction(conn, write.PredictionDraft(
        claim_md="live", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
        near_edge_pct=1.5, far_edge_pct=3.0, conviction=0.72,
        agent="plutus-predict", symbol=symbol, strategy_name=name, kind="strategy"))
    conn.commit()
    return open_pid


_TRADEABLE_BOOK = [(3.0, "correct", -0.3, True)] * 12 + [(3.0, "wrong", -2.0, False)] * 4
_MIRAGE_BOOK = [(1.0, "correct", -3.0, False)] * 16   # floor-correct, never tags far
# Tradeable overall, but scratch-heavy: 6 wins / 4 losses / 6 scratches. The
# scratch-free win_rate (0.6) would pass the setup gate; p = wins/n (0.375)
# must refuse it. Losses lead so the trailing hazard window stays positive.
_SCRATCHY_BOOK = ([(3.0, "wrong", -2.0, False)] * 4
                  + [(3.0, "correct", -0.3, False)] * 3
                  + [(3.0, "correct", -0.3, True)] * 3
                  + [(3.0, "correct", -0.3, False)] * 3
                  + [(3.0, "correct", -0.3, True)] * 3)


@pytest.fixture()
def mock_venue(monkeypatch):
    """Mocks the venue + live price so the derive path runs end to end."""
    calls = {}

    def fake_place(**kw):
        calls["place"] = kw
        return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                "fill_id": "f1", "slippage_bp": 4.0,
                "sl_order_id": "sl9", "tp_order_id": "tp9", "bracket_warnings": []}

    def fake_close(**kw):
        calls["close"] = kw
        return {"fill_price": 101_000.0, "size": 0.035, "order_id": "o2",
                "slippage_bp": 3.0, "cancel_warnings": []}

    import trading.dispatchers.desk_execution as mod
    import trading.integrations.hyperliquid.venue as venue
    monkeypatch.setattr(venue, "hl_place_order", fake_place)
    monkeypatch.setattr(venue, "hl_close_position", fake_close)
    # open_orders carries the SL oid so the on-venue verification confirms.
    monkeypatch.setattr(venue, "hl_account_state",
                        lambda **k: {"equity_usd": 1000.0,
                                     "open_perp_positions": [],
                                     "open_orders": [{"coin": "BTC", "oid": "sl9"}]})
    monkeypatch.setattr(mod, "_fresh_price", lambda symbol: 100_000.0)
    return calls


class TestOpen:
    def test_full_chain_derives_and_places(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "flow long"})
        assert r["ok"], r
        assert r["sizing"]["mode"] == "notional_based"
        assert r["sl"]["price"] < 100_000.0 < r["tp"]["price"]   # long: SL below, TP (far) above
        assert r["sl"]["on_venue"] is True
        assert r["sizing"]["notional_multiple"] == 1.0           # conviction 0.72 band
        assert r["sizing"]["notional_usd"] == 1000.0             # 1× the $1000 equity
        assert r["pilot"] is False
        assert mock_venue["place"]["slippage"] == 0.003          # ±0.3% cap
        assert r["sizing"]["leverage"] is not None
        pos = queries.open_position(get_db())
        assert pos["id"] == r["position_id"]
        assert pos["thesis"]["prediction_id"] == pid

    def test_one_position_law(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        assert _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})["ok"]
        second = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "one at a time" in second["error"]

    def test_requires_live_prediction(self, mock_venue):
        r = _call("desk_open_position", {"prediction_id": 9999, "thesis_md": "t"})
        assert "does not exist" in r["error"]

    def test_resolved_prediction_refused(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        write.resolve_prediction(get_db(), pid, "correct", resolved_by="plutus-ops")
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "already resolved" in r["error"]

    def test_stale_prediction_refused(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        conn = get_db()
        conn.execute("UPDATE predictions SET ts=? WHERE id=?", (time.time() - 3600, pid))
        conn.commit()
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and "stale" in r["refused"]

    def test_equity_failure_refuses(self, mock_venue, monkeypatch):
        # Risk-based sizing NEEDS equity — a failed read blocks the open (honest absence).
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.integrations.hyperliquid.venue as venue

        def boom(**kw):
            raise RuntimeError("hl down")

        monkeypatch.setattr(venue, "hl_account_state", boom)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "cannot size" in r["error"] and "hl down" in r["error"]


class TestExpectancyGate:
    def test_refuses_non_tradeable_strategy(self, mock_venue):
        pid = _seed_strategy("mirage", "active", _MIRAGE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "x"})
        assert r["ok"] is False and r["refused"] == "strategy not tradeable"
        assert queries.open_position(get_db()) is None

    def test_p_includes_scratches(self, mock_venue):
        # 6/16 wins: the scratch-free win_rate (0.6) clears the setup gate,
        # p = wins/n (0.375) must refuse — scratches are not free.
        pid = _seed_strategy("scratchy", "active", _SCRATCHY_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "x"})
        assert r["ok"] is False, r
        assert r["refused"] == "setup below expectancy gate"
        assert r["p_win"] == pytest.approx(0.375)


class TestPilotLane:
    """The operator pilot mandate (2026-08-22): armed via the PILOT sentinel,
    a TEST book above the conviction threshold may fund; graduation keeps
    gating the evidence-backed lane. Retired books never fund."""

    def _arm(self):
        from tests.trading.conftest import arm_pilot
        arm_pilot()

    def test_test_book_refused_when_not_armed(self, mock_venue):
        pid = _seed_strategy("tb", "test", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and "pilot not armed" in r["refused"]

    def test_pilot_funds_test_book_and_tags_it(self, mock_venue):
        self._arm()
        pid = _seed_strategy("tb", "test", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["pilot"] is True
        dec = get_db().execute(
            "SELECT params_json FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert json.loads(dec[0])["pilot"] is True

    def test_pilot_neutral_prior_on_empty_book(self, mock_venue, monkeypatch):
        self._arm()
        import trading.dispatchers.desk_execution as mod
        # an evidence-empty book has no MAE envelope; the live path falls back
        # to ATR — stubbed here so the test exercises the p=0.5 prior, not TA
        monkeypatch.setattr(mod, "_derive_stop_pct",
                            lambda *a: (2.0, "stubbed stop"))
        pid = _seed_strategy("empty", "test", [])
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r          # 3% far edge vs 2% stop clears the p=0.5 gate
        assert r["pilot"] is True

    def test_pilot_never_funds_retired(self, mock_venue):
        self._arm()
        pid = _seed_strategy("dead", "retired", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and "pilot lane takes test books only" in r["refused"]

    def test_min_notional_floor(self, mock_venue, monkeypatch):
        self._arm()
        import trading.integrations.hyperliquid.venue as venue
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 8.0,
                                         "open_perp_positions": [],
                                         "open_orders": [{"coin": "BTC", "oid": "sl9"}]})
        pid = _seed_strategy("tiny", "test", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["sizing"]["min_notional_floored"] is True
        assert r["sizing"]["notional_usd"] == 10.0   # conviction 0.72 → 1× $8, floored


class TestMechanicalGuards:
    """The guards the recipes describe live IN the tool — HALT, ACTIVE
    status, trade-path readiness (review items A + B)."""

    def _halt(self, note=""):
        from harness.constants import get_hermes_home
        path = get_hermes_home() / "HALT"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note, encoding="utf-8")

    def test_halt_refuses_open(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        self._halt("stand down")
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and "HALT" in r["refused"]
        assert r["halt_note"] == "stand down"
        assert queries.open_position(get_db()) is None

    def test_halt_refuses_close_but_not_naked_abort(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        opened = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert opened["ok"], opened
        self._halt()
        refused = _call("desk_close_position",
                        {"position_id": opened["position_id"], "exit_reason": "tp"})
        assert refused["ok"] is False and "HALT" in refused["refused"]
        # the naked-position abort must ALWAYS be able to close
        aborted = _call("desk_close_position",
                        {"position_id": opened["position_id"],
                         "exit_reason": "naked_position_abort"})
        assert aborted["ok"] is True
        assert queries.open_position(get_db()) is None

    def test_non_active_strategy_refused_even_if_tradeable(self, mock_venue):
        # A tradeable book left in status=test must NOT fund (the status gate
        # is in-tool, not just in best_actionable's SQL).
        pid = _seed_strategy("stillTest", "test", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False
        assert r["refused"].startswith("strategy not ACTIVE")
        assert r["status"] == "test"

    def test_not_ready_refuses(self, mock_venue, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        monkeypatch.setattr(mod, "_trade_readiness",
                            lambda: {"ready": False, "reason": "agent wallet expired"})
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and r["refused"] == "trade path not READY"
        assert "expired" in r["reason"]

    def test_unverifiable_readiness_refuses(self, mock_venue, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod

        def boom():
            raise RuntimeError("rpc down")

        monkeypatch.setattr(mod, "_trade_readiness", boom)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False and r["refused"] == "trade readiness unverifiable"
        assert "rpc down" in r["error"]


class TestGeometryInvariant:
    """The mechanical TP is the edge the strategy graduated on (C1)."""

    # Near-edge book: near tagged 12/16 with small MAE, far never tagged.
    # best_target = near; the placed TP must sit at the NEAR edge.
    _NEAR_BOOK = ([(3.0, "correct", -0.3, False, True)] * 12
                  + [(3.0, "wrong", -2.0, False)] * 4)

    def test_near_target_book_places_near_tp(self, mock_venue):
        pid = _seed_strategy("nearS", "active", self._NEAR_BOOK)
        exp = queries.strategy_expectancy(get_db(), "nearS")
        assert exp["best_target"] == "near" and exp["tradeable"], exp
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["tp"]["target"] == "near"
        # open prediction: entry_ref 100k, near 1.5% → TP at 101_500 (not 103_000)
        assert r["tp"]["price"] == pytest.approx(101_500.0)

    def test_far_target_book_places_far_tp(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["tp"]["target"] == "far"
        assert r["tp"]["price"] == pytest.approx(103_000.0)


class TestNakedAbort:
    def test_naked_position_auto_closes(self, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue
        closed = {}

        def fake_place(**kw):                 # bracket leg rejected: SL warning
            return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                    "fill_id": "f1", "slippage_bp": 4.0, "sl_order_id": None,
                    "tp_order_id": None,
                    "bracket_warnings": ["SL: Order could not immediately match"]}

        def fake_close(**kw):
            closed.update(kw)
            return {"fill_price": 100_400.0, "size": 0.035, "cancel_warnings": []}

        monkeypatch.setattr(venue, "hl_place_order", fake_place)
        monkeypatch.setattr(venue, "hl_close_position", fake_close)
        monkeypatch.setattr(venue, "hl_account_state", lambda **k: {"equity_usd": 1000.0})
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)

        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False
        assert "naked_position" in r["aborted_reason"]
        assert closed                                    # auto-close fired
        assert queries.open_position(get_db()) is None   # flat after abort


class TestClose:
    def test_close_computes_outcome(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        opened = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        pos_id = opened["position_id"]
        write.record_evaluation(get_db(), position_id=pos_id, conviction=0.7,
                                agent="plutus-main")
        closed = _call("desk_close_position", {"position_id": pos_id, "exit_reason": "tp"})
        assert closed["ok"], closed
        assert closed["outcome"]["realized_pnl_usd"] is not None
        assert queries.open_position(get_db()) is None
        bands = queries.sizing_performance(get_db())
        assert bands[0]["conviction_band"] == pytest.approx(0.7)   # opened at conviction 0.72
        assert bands[0]["n"] == 1

    def test_close_wrong_position_refused(self, mock_venue):
        result = _call("desk_close_position", {"position_id": 777, "exit_reason": "tp"})
        assert "not the open position" in result["error"]


class TestRescore:
    def _open(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)   # entry conviction 0.72
        return _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})["position_id"]

    def test_exit_on_conviction_decay(self, mock_venue, monkeypatch):
        pos_id = self._open(mock_venue)
        import trading.dispatchers.predict_tools as pt
        monkeypatch.setattr(pt, "score_strategy",
                            lambda name, regime=None: {"conviction": 0.30, "support_scores": []})
        r = _call("rescore_position", {"position_id": pos_id})
        assert r["ok"] and r["recommended_action"] == "exit_now"

    def test_hold_when_conviction_holds(self, mock_venue, monkeypatch):
        pos_id = self._open(mock_venue)
        import trading.dispatchers.predict_tools as pt
        monkeypatch.setattr(pt, "score_strategy",
                            lambda name, regime=None: {"conviction": 0.75, "support_scores": []})
        r = _call("rescore_position", {"position_id": pos_id})
        assert r["ok"] and r["recommended_action"] == "hold"

    def test_missing_conviction_exits_not_holds(self, mock_venue, monkeypatch):
        # Honest absence WHILE RISK IS OPEN: an unverifiable premise is
        # treated as gone (the review's rescore finding) — never held.
        pos_id = self._open(mock_venue)
        import trading.dispatchers.predict_tools as pt
        monkeypatch.setattr(pt, "score_strategy",
                            lambda name, regime=None: {"conviction": None, "support_scores": []})
        r = _call("rescore_position", {"position_id": pos_id, "alert": "adverse"})
        assert r["ok"] and r["recommended_action"] == "exit_now"

    def test_missing_conviction_on_near_alert_takes_profit(self, mock_venue, monkeypatch):
        pos_id = self._open(mock_venue)
        import trading.dispatchers.predict_tools as pt
        monkeypatch.setattr(pt, "score_strategy",
                            lambda name, regime=None: {"conviction": None, "support_scores": []})
        r = _call("rescore_position", {"position_id": pos_id, "alert": "near"})
        assert r["ok"] and r["recommended_action"] == "take_profit"


class TestExtractBracketStatus:
    """Bracket verification is what stands between a position and nakedness."""

    def test_resting_limit_order(self):
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status([{"resting": {"oid": 42}}], 0, "TP")
        assert oid == "42" and warn is None

    def test_waiting_for_trigger_dict_shape(self):
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status(
            [{"waitingForTrigger": {"oid": 7}}], 0, "SL")
        assert oid == "7" and warn is None

    def test_waiting_for_trigger_bare_string(self):
        # The REAL shape observed live 2026-07-03: HL reports untriggered
        # stops as the bare string "waitingForTrigger" with NO oid. This is
        # acceptance (no warning) — unrecognized, it aborted five
        # well-bracketed fills in a row.
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status(["waitingForTrigger"], 0, "SL")
        assert oid is None and warn is None

    def test_unknown_bare_string_fails_loudly(self):
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status(["somethingNew"], 0, "SL")
        assert oid is None and "unrecognised" in warn

    def test_unknown_status_fails_loudly(self):
        # Never guess an oid out of an unknown blob: a fabricated id reads as
        # "SL rests on-venue" and defeats the naked-position guard.
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status([{"rejected": {"oid": 99}}], 0, "SL")
        assert oid is None
        assert "unrecognised" in warn

    def test_error_status_surfaces_message(self):
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status([{"error": "px too far"}], 0, "SL")
        assert oid is None and "px too far" in warn

    def test_truncated_response(self):
        from trading.integrations.hyperliquid.venue import _extract_bracket_status
        oid, warn = _extract_bracket_status([], 0, "SL")
        assert oid is None and "truncated" in warn


class TestSlRestsOnVenue:
    """The on-venue stop verifier — oid match or price-matched trigger."""

    def _sl(self, *a, **k):
        from trading.dispatchers.desk_execution import _sl_rests_on_venue
        return _sl_rests_on_venue(*a, **k)

    def test_oid_match(self):
        st = {"open_orders": [{"coin": "BTC", "oid": 42}]}
        assert self._sl(st, "BTC", "42", sl_price=95_000.0)

    def test_price_matched_trigger_without_oid(self):
        # The waitingForTrigger reality: no oid from the response, but the
        # stop rests on-venue — matched by triggerPx.
        st = {"open_orders": [{"coin": "BTC", "isTrigger": True,
                               "triggerPx": "95001.0", "oid": 7}]}
        assert self._sl(st, "BTC", None, sl_price=95_000.0)

    def test_tp_trigger_does_not_masquerade_as_sl(self):
        # A resting TP is also a trigger order — it must NOT satisfy the SL
        # check (price mismatch).
        st = {"open_orders": [{"coin": "BTC", "isTrigger": True,
                               "triggerPx": "103000.0", "oid": 8}]}
        assert not self._sl(st, "BTC", None, sl_price=95_000.0)

    def test_empty_orders_is_naked(self):
        assert not self._sl({"open_orders": []}, "BTC", None, sl_price=95_000.0)


class TestBracketVerification:
    """End-to-end guard behavior against the real venue response shapes."""

    def test_waiting_for_trigger_no_oid_opens_clean(self, monkeypatch):
        # The 2026-07-03 five-abort scenario: brackets accepted, response
        # carries no oids (bare-string statuses), on-venue state unreadable
        # for orders. Must NOT abort.
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue

        def fake_place(**kw):
            return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                    "fill_id": "f1", "slippage_bp": 4.0, "sl_order_id": None,
                    "tp_order_id": None, "bracket_warnings": []}

        monkeypatch.setattr(venue, "hl_place_order", fake_place)
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 1000.0})
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)

        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is True, r
        assert queries.open_position(get_db()) is not None

    def test_readable_empty_order_book_aborts(self, monkeypatch):
        # Response claims acceptance but on-venue truth shows NO orders at
        # all — genuinely naked; venue truth must win over the response.
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue
        closed = {}

        def fake_place(**kw):
            return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                    "fill_id": "f1", "slippage_bp": 4.0, "sl_order_id": None,
                    "tp_order_id": None, "bracket_warnings": []}

        def fake_close(**kw):
            closed.update(kw)
            return {"fill_price": 100_400.0, "size": 0.035, "cancel_warnings": []}

        monkeypatch.setattr(venue, "hl_place_order", fake_place)
        monkeypatch.setattr(venue, "hl_close_position", fake_close)
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 1000.0, "open_orders": [],
                                         "open_perp_positions": []})
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)

        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False
        assert "naked_position" in r["aborted_reason"]
        assert closed
        assert queries.open_position(get_db()) is None


class TestDexAwareNakedGuard:
    """Position #15 (xyz:GOLD, 2026-08-25) — the guard shot a bracketed trade.

    The stop DID rest: Hyperliquid's own historicalOrders showed the Stop
    Market at 4654.1 open at 01:13:29. The guard read the main dex, saw an
    empty book, and force-closed three seconds after the fill. The account
    read now spans every dex; these tests hold that line, and hold the retry
    honest so it can never paper over a genuinely absent stop.
    """

    @staticmethod
    def _placer(seen):
        """Stand in for the venue: record the stop it was asked to rest."""
        def place(**kw):
            seen["sl"] = kw.get("sl")
            seen["symbol"] = kw.get("symbol")
            return {"fill_price": 4678.6, "size": kw["size"], "order_id": "o1",
                    "fill_id": "f1", "slippage_bp": 4.0, "sl_order_id": None,
                    "tp_order_id": None, "bracket_warnings": []}
        return place

    @staticmethod
    def _resting(seen):
        """The trigger order as the venue reports it — dex-qualified coin."""
        return {"coin": seen["symbol"], "isTrigger": True,
                "triggerPx": str(seen["sl"]), "oid": 9}

    def test_stop_resting_on_builder_dex_opens_clean(self, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK,
                             symbol="xyz:GOLD")
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue

        seen = {}

        def state(**k):
            # What the MERGED read returns: the trigger order carries its
            # dex-qualified coin name, exactly as the venue reports it.
            if "sl" not in seen:
                return {"equity_usd": 1000.0}
            return {"equity_usd": 1000.0, "open_perp_positions": [],
                    "open_orders": [self._resting(seen)]}

        monkeypatch.setattr(venue, "hl_place_order", self._placer(seen))
        monkeypatch.setattr(venue, "hl_account_state", state)
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is True, r
        # The guard matched a dex-qualified coin name, not a bare one.
        assert seen["symbol"] == "xyz:GOLD"
        assert queries.open_position(get_db()) is not None

    def test_retry_clears_a_stop_the_venue_had_not_listed_yet(self, monkeypatch):
        # A venue that has accepted an order need not list it in the same
        # second. One empty read is not an absence.
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue
        reads = {"n": 0}
        seen = {}

        def state(**k):
            if "sl" not in seen:
                return {"equity_usd": 1000.0}
            reads["n"] += 1
            orders = [] if reads["n"] == 1 else [self._resting(seen)]
            return {"equity_usd": 1000.0, "open_perp_positions": [],
                    "open_orders": orders}

        monkeypatch.setattr(mod, "_NAKED_VERIFY_BACKOFF_S", 0)
        monkeypatch.setattr(venue, "hl_place_order", self._placer(seen))
        monkeypatch.setattr(venue, "hl_account_state", state)
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is True, r
        assert reads["n"] == 2                      # retried, then cleared
        assert queries.open_position(get_db()) is not None

    def test_retry_does_not_paper_over_a_real_absence(self, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue
        closed = {}
        reads = {"n": 0}
        seen = {}

        def state(**k):
            if "sl" not in seen:
                return {"equity_usd": 1000.0}
            reads["n"] += 1
            return {"equity_usd": 1000.0, "open_perp_positions": [],
                    "open_orders": []}

        def fake_close(**kw):
            closed.update(kw)
            return {"fill_price": 4670.0, "size": 0.0167, "cancel_warnings": []}

        monkeypatch.setattr(mod, "_NAKED_VERIFY_BACKOFF_S", 0)
        monkeypatch.setattr(venue, "hl_place_order", self._placer(seen))
        monkeypatch.setattr(venue, "hl_close_position", fake_close)
        monkeypatch.setattr(venue, "hl_account_state", state)
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False
        assert "naked_position" in r["aborted_reason"]
        assert reads["n"] == mod._NAKED_VERIFY_ATTEMPTS   # every attempt spent
        assert closed
        assert queries.open_position(get_db()) is None


class TestResponseEnvelope:
    """_response_statuses — the shared exchange-response unwrapper."""

    def _unwrap(self, resp):
        from trading.integrations.hyperliquid.venue import _response_statuses
        return _response_statuses(resp)

    def test_ok_envelope(self):
        resp = {"status": "ok",
                "response": {"data": {"statuses": [{"filled": {"oid": 1}}]}}}
        assert self._unwrap(resp) == [{"filled": {"oid": 1}}]

    def test_action_rejection_surfaces_venue_text(self):
        # HL returns HTTP 200 with response as a STRING on action-level
        # rejections (expired agent wallet, bad nonce...).
        resp = {"status": "err",
                "response": "User or API Wallet does not exist."}
        with pytest.raises(RuntimeError, match="does not exist"):
            self._unwrap(resp)

    def test_none_response_fails_loudly(self):
        with pytest.raises(RuntimeError, match="no statuses"):
            self._unwrap(None)


class TestAlreadyFlatClose:
    def test_bracket_fired_close_settles_books(self, mock_venue, monkeypatch):
        # An on-venue SL/TP fired: market_close finds nothing, the fill is
        # recovered from venue history. The DB position MUST close (this
        # used to deadlock the desk permanently on the one-position law).
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        import trading.integrations.hyperliquid.venue as venue
        monkeypatch.setattr(venue, "hl_close_position",
                            lambda **kw: {"already_flat": True,
                                          "fill_price": 101_200.0, "size": 0.035,
                                          "order_id": "486", "fill_id": "t1",
                                          "slippage_bp": None})
        c = _call("desk_close_position",
                  {"position_id": r["position_id"], "exit_reason": "sl_fired"})
        assert c["ok"] is True
        assert c["venue_already_flat"] is True
        assert queries.open_position(get_db()) is None   # books settled


class TestAbortCloseEscalation:
    def test_failed_abort_close_reported_loudly(self, monkeypatch):
        # Naked position AND the abort-close fails: the result must scream
        # "still open and unprotected", never report a clean abort.
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue

        def fake_place(**kw):
            return {"fill_price": 100_500.0, "size": kw["size"], "order_id": "o1",
                    "fill_id": "f1", "slippage_bp": 4.0, "sl_order_id": None,
                    "tp_order_id": None,
                    "bracket_warnings": ["SL: Order could not immediately match"]}

        def close_boom(**kw):
            raise RuntimeError("venue timeout")

        monkeypatch.setattr(venue, "hl_place_order", fake_place)
        monkeypatch.setattr(venue, "hl_close_position", close_boom)
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 1000.0})
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)

        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"] is False
        assert r["abort_close_failed"] is True
        assert "UNPROTECTED" in r["aborted_reason"]
        assert "venue timeout" in r["abort_close_error"]


class TestVenuePreflight:
    def test_untracked_on_venue_position_refuses_open(self, monkeypatch):
        # lifecycle.db says flat but the venue shows a live position —
        # opening would silently double exposure. Refuse.
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.dispatchers.desk_execution as mod
        import trading.integrations.hyperliquid.venue as venue
        placed = {}

        def fake_place(**kw):
            placed["yes"] = True
            return {}

        monkeypatch.setattr(venue, "hl_place_order", fake_place)
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 1000.0,
                                         "open_perp_positions": [
                                             {"coin": "BTC", "szi": "0.001"}]})
        monkeypatch.setattr(mod, "_fresh_price", lambda s: 100_000.0)

        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert "refusing to stack exposure" in r["error"]
        assert not placed                                # never reached the venue
        assert queries.open_position(get_db()) is None


class TestAdopt:
    """desk_adopt_position — books catch up to venue truth (the 2026-07-03
    orphan fills left a live position lifecycle.db knew nothing about)."""

    _VENUE_POS = {"coin": "BTC", "szi": "0.00592", "entryPx": "62561.8",
                  "unrealizedPnl": "1.32"}

    def test_adopt_then_manage_full_cycle(self, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.integrations.hyperliquid.venue as venue
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 150.0,
                                         "open_perp_positions": [self._VENUE_POS],
                                         "open_orders": []})
        r = _call("desk_adopt_position", {
            "prediction_id": pid,
            "thesis_md": "orphan fills adopted; brackets rest on venue",
            "sl_price": 60644.0, "tp_price": 63909.0,
            "sl_order_id": "486977722841", "tp_order_id": "486977722840"})
        assert r["ok"], r
        assert r["adopted"]["side"] == "long"
        assert r["adopted"]["size"] == pytest.approx(0.00592)
        assert r["adopted"]["entry_px"] == pytest.approx(62561.8)
        pos = queries.open_position(get_db())
        assert pos["id"] == r["position_id"]
        assert pos["thesis"]["prediction_id"] == pid

        # the adopted position is now manageable: close it normally
        monkeypatch.setattr(venue, "hl_close_position",
                            lambda **kw: {"fill_price": 63000.0, "size": 0.00592,
                                          "cancel_warnings": []})
        c = _call("desk_close_position",
                  {"position_id": r["position_id"], "exit_reason": "main_decision"})
        assert c["ok"], c
        assert queries.open_position(get_db()) is None

    def test_adopt_refuses_when_venue_flat(self, monkeypatch):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        import trading.integrations.hyperliquid.venue as venue
        monkeypatch.setattr(venue, "hl_account_state",
                            lambda **k: {"equity_usd": 150.0,
                                         "open_perp_positions": [],
                                         "open_orders": []})
        r = _call("desk_adopt_position",
                  {"prediction_id": pid, "thesis_md": "t"})
        assert "nothing to adopt" in r["error"]

    def test_adopt_refuses_when_db_already_open(self, mock_venue):
        pid = _seed_strategy("flowS", "active", _TRADEABLE_BOOK)
        assert _call("desk_open_position",
                     {"prediction_id": pid, "thesis_md": "t"})["ok"]
        r = _call("desk_adopt_position",
                  {"prediction_id": pid, "thesis_md": "t"})
        assert "already has an open position" in r["error"]


class TestCalibratedSizing:
    """Calibrated conviction drives the bands and the pilot prior
    (wired in 2026-08-24); absence falls back to raw, on the record."""

    def _arm(self):
        from tests.trading.conftest import arm_pilot
        arm_pilot()

    def _stub_cal(self, monkeypatch, p):
        import trading.calibration.live as live
        monkeypatch.setattr(
            live, "calibrated_conviction",
            lambda conn, pid: ({"p": p, "version": "test-v", "trained_at": 0.0}
                               if p is not None else None))

    def test_calibrated_conviction_picks_the_band(self, mock_venue, monkeypatch):
        self._arm()
        import trading.dispatchers.desk_execution as mod
        monkeypatch.setattr(mod, "_derive_stop_pct", lambda *a: (1.0, "s"))
        self._stub_cal(monkeypatch, 0.55)          # raw 0.72 would take 1×
        pid = _seed_strategy("cal1", "test", [])
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["sizing"]["notional_multiple"] == 0.5   # 0.55 band, not raw's 1×
        assert r["sizing"]["conviction_calibrated"] == 0.55
        assert r["sizing"]["conviction_raw"] == 0.72
        assert r["sizing"]["calibration_used"] is True
        dec = get_db().execute(
            "SELECT conviction FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert dec[0] == pytest.approx(0.55)       # the number that chose the band

    def test_calibrated_below_threshold_refuses(self, mock_venue, monkeypatch):
        self._arm()
        import trading.dispatchers.desk_execution as mod
        # stop 1.0 vs far 3.0: rr=3.0 clears even the p=0.42-tightened RR
        # threshold, so the refusal under test is the BAND one specifically.
        monkeypatch.setattr(mod, "_derive_stop_pct", lambda *a: (1.0, "s"))
        self._stub_cal(monkeypatch, 0.42)
        pid = _seed_strategy("cal2", "test", [])
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r.get("ok") is False, r
        assert r["refused"] == "conviction below global threshold"
        assert r["calibration_used"] is True and r["conviction_calibrated"] == 0.42

    def test_absent_calibration_falls_back_to_raw(self, mock_venue, monkeypatch):
        self._arm()
        import trading.dispatchers.desk_execution as mod
        monkeypatch.setattr(mod, "_derive_stop_pct", lambda *a: (2.0, "s"))
        self._stub_cal(monkeypatch, None)
        pid = _seed_strategy("cal3", "test", [])
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["sizing"]["notional_multiple"] == 1.0   # raw 0.72 band
        assert r["sizing"]["calibration_used"] is False

    def test_calibrated_prior_feeds_pilot_rr_gate(self, mock_venue, monkeypatch):
        self._arm()
        import trading.dispatchers.desk_execution as mod
        monkeypatch.setattr(mod, "_derive_stop_pct", lambda *a: (2.6, "s"))
        # far edge 3.0 vs stop 2.6: rr ~1.15 fails at p=0.5 (threshold >1)
        # but clears at a calibrated p=0.62 — the measured prior opens the
        # gate the fabricated neutral one kept shut.
        self._stub_cal(monkeypatch, 0.62)
        pid = _seed_strategy("cal4", "test", [])
        r = _call("desk_open_position", {"prediction_id": pid, "thesis_md": "t"})
        assert r["ok"], r
        assert r["sizing"]["rr"] < 1.2
