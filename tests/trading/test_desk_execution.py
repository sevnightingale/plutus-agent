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


def _seed_strategy(name, status, book):
    """Insert a strategy with a resolved book + return a fresh OPEN prediction.

    book: list of (far, outcome, mae, reached_far). reached_far = tagged TP (win).
    """
    conn = get_db()
    conn.execute(
        "INSERT INTO strategies (name,file_path,status,timescale,"
        "mechanism_family,created_at,updated_at) VALUES "
        "(?,?,?,'intraday','flow',0,0)", (name, f"{name}.md", status))
    for far, outcome, mae, reached in book:
        pid = write.record_prediction(conn, write.PredictionDraft(
            claim_md="z", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
            near_edge_pct=far / 2.0, far_edge_pct=far, conviction=0.72,
            agent="plutus-predict", symbol="BTC", strategy_name=name, kind="strategy"))
        write.resolve_prediction(conn, pid, outcome, resolved_by="r",
                                 realized_value={"mae_pct": mae})
        if reached:
            conn.execute("UPDATE predictions SET reached_far_at=? WHERE id=?",
                         (time.time(), pid))
    open_pid = write.record_prediction(conn, write.PredictionDraft(
        claim_md="live", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
        near_edge_pct=1.5, far_edge_pct=3.0, conviction=0.72,
        agent="plutus-predict", symbol="BTC", strategy_name=name, kind="strategy"))
    conn.commit()
    return open_pid


_TRADEABLE_BOOK = [(3.0, "correct", -0.3, True)] * 12 + [(3.0, "wrong", -2.0, False)] * 4
_MIRAGE_BOOK = [(1.0, "correct", -3.0, False)] * 16   # floor-correct, never tags far


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
        assert r["sizing"]["mode"] == "risk_based"
        assert r["sl"]["price"] < 100_000.0 < r["tp"]["price"]   # long: SL below, TP (far) above
        assert r["sl"]["on_venue"] is True
        assert r["sizing"]["risk_budget"] == 0.07                # conviction 0.72 band
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
