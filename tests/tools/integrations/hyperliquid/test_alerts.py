"""Hyperliquid alerts — state-diff detection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading.integrations.hyperliquid import _client, alerts


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _client.reset_singletons_for_tests()
    monkeypatch.setenv("ACP_AGENT_WALLET", "0x000000000000000000000000000000000000dead")
    yield
    _client.reset_singletons_for_tests()


def test_position_status_change_open(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.01", "entryPx": "80000"}}
        ]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    fired, new_state = alerts.poll_hl_position_status_change(state={"positions": {}})
    assert len(fired) == 1
    assert fired[0]["kind"] == "opened"
    assert fired[0]["coin"] == "BTC"
    assert "BTC" in new_state["positions"]


def test_position_status_change_close(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {"assetPositions": []}
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    prev = {"positions": {"BTC": {"szi": 0.01, "entry_px": 80000}}}
    fired, new_state = alerts.poll_hl_position_status_change(state=prev)
    assert len(fired) == 1
    assert fired[0]["kind"] == "closed"
    assert fired[0]["coin"] == "BTC"
    assert new_state["positions"] == {}


def test_position_status_change_size_change(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.02", "entryPx": "80000"}}
        ]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    prev = {"positions": {"BTC": {"szi": 0.01, "entry_px": 80000}}}
    fired, _ = alerts.poll_hl_position_status_change(state=prev)
    assert len(fired) == 1
    assert fired[0]["kind"] == "size_changed"
    assert fired[0]["previous_szi"] == 0.01
    assert fired[0]["current_szi"] == 0.02


def test_position_status_change_no_diff(monkeypatch):
    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.01", "entryPx": "80000"}}
        ]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(alerts, "get_info", lambda: info_mock)

    prev = {"positions": {"BTC": {"szi": 0.01, "entry_px": "80000"}}}
    fired, _ = alerts.poll_hl_position_status_change(state=prev)
    assert fired == []


def _equity_info_mock(monkeypatch, spot_usdc: str, perp_account_value: str):
    """Mock the SDK for the balance alert's TOTAL-equity measure."""
    from trading.integrations.hyperliquid import data_points

    info_mock = MagicMock()
    info_mock.user_state.return_value = {
        "marginSummary": {"accountValue": perp_account_value},
        "withdrawable": "0",
    }
    info_mock.spot_user_state.return_value = {
        "balances": [{"coin": "USDC", "total": spot_usdc}]
    }
    monkeypatch.setattr(_client, "get_info", lambda: info_mock)
    monkeypatch.setattr(alerts, "get_info", lambda: info_mock)
    monkeypatch.setattr(data_points, "get_info", lambda: info_mock)
    return info_mock


def test_account_balance_change_above_threshold(monkeypatch):
    # Deposit landed in SPOT (unified mode) — perp side still 0.
    _equity_info_mock(monkeypatch, spot_usdc="100.0", perp_account_value="0")

    fired, new_state = alerts.poll_hl_account_balance_change(
        state={"equity_usd": 50.0},
    )
    assert len(fired) == 1
    assert fired[0]["delta"] == pytest.approx(50.0)
    assert new_state["equity_usd"] == 100.0


def test_account_balance_change_below_threshold(monkeypatch):
    _equity_info_mock(monkeypatch, spot_usdc="100.10", perp_account_value="0")

    # delta=0.10, threshold=max(0.50, 1.00) = 1.00 → below
    fired, _ = alerts.poll_hl_account_balance_change(state={"equity_usd": 100.0})
    assert fired == []


def test_account_balance_change_ignores_margin_display_shift(monkeypatch):
    # Opening a position moves margin INSIDE the unified balance:
    # spot 100→60, perp 0→40. Total equity unchanged — must NOT fire.
    _equity_info_mock(monkeypatch, spot_usdc="60.0", perp_account_value="40.0")

    fired, new_state = alerts.poll_hl_account_balance_change(
        state={"equity_usd": 100.0},
    )
    assert fired == []
    assert new_state["equity_usd"] == 100.0


def test_alerts_no_address_returns_empty(monkeypatch):
    monkeypatch.delenv("ACP_AGENT_WALLET", raising=False)
    fired, new_state = alerts.poll_hl_position_status_change(state={"positions": {}})
    assert fired == []
    assert new_state == {"positions": {}}


# ── prediction-resolution alert (event-driven resolver) ──────────────────────

def _zone_draft(**over):
    import time
    from trading.lifecycle import write
    base = dict(
        claim_md="zone", horizon_ts=time.time() + 3600, entry_ref_price=100_000.0,
        near_edge_pct=5.0, far_edge_pct=10.0, conviction=0.7,
        agent="plutus-predict", symbol="BTC", strategy_name=None, kind="adhoc",
    )
    base.update(over)
    return write.PredictionDraft(**base)


def test_prediction_resolution_paper_is_silent(monkeypatch):
    from trading.lifecycle.db import get_db
    from trading.lifecycle import write, queries
    from trading.integrations.hyperliquid import outcomes

    conn = get_db()
    pid = write.record_prediction(conn, _zone_draft())  # near +5%, far +10% (110k)
    info = MagicMock()
    info.all_mids.return_value = {"BTC": "111000"}  # above the far edge → target
    monkeypatch.setattr(alerts, "get_info", lambda: info)
    monkeypatch.setattr(outcomes, "path_stats",
                        lambda *a, **k: {"mfe_pct": 11.0, "mae_pct": -1.0})

    fired, _ = alerts.poll_hl_prediction_resolution(state={})
    assert fired == []  # routine paper resolution does not wake main
    assert queries.prediction(conn, pid)["outcome"] == "correct"


def test_prediction_resolution_funded_wakes(monkeypatch):
    from trading.lifecycle.db import get_db
    from trading.lifecycle import write
    from trading.integrations.hyperliquid import outcomes

    conn = get_db()
    pid = write.record_prediction(conn, _zone_draft())
    tid = write.record_thesis(conn, prediction_id=pid, symbol="BTC",
                              text_md="t", agent="plutus-main")
    did = write.record_decision(conn, thesis_id=tid, action="open_long",
                                agent="plutus-main")
    trid = write.record_trade(conn, decision_id=did, venue="hyperliquid",
                              symbol="BTC", side="long", size=0.001, fill_price=100_000.0)
    write.open_position(conn, venue="hyperliquid", symbol="BTC", side="long",
                        size=0.001, opening_trade_id=trid)

    info = MagicMock()
    info.all_mids.return_value = {"BTC": "111000"}  # above the far edge → target
    monkeypatch.setattr(alerts, "get_info", lambda: info)
    monkeypatch.setattr(outcomes, "path_stats", lambda *a, **k: {"mfe_pct": 11.0})

    fired, _ = alerts.poll_hl_prediction_resolution(state={})
    assert len(fired) == 1
    assert fired[0]["funded"] and fired[0]["prediction_id"] == pid
    assert fired[0]["kind"] == "correct" and fired[0]["mode"] == "target"


def test_prediction_resolution_no_open_skips_network(monkeypatch):
    from trading.lifecycle.db import get_db
    get_db()  # fresh, no predictions
    info = MagicMock()
    monkeypatch.setattr(alerts, "get_info", lambda: info)
    fired, _ = alerts.poll_hl_prediction_resolution(state={})
    assert fired == []
    info.all_mids.assert_not_called()  # no open predictions → no price fetch


# ── position alert (the 4-target judgment triggers) ──────────────────────────

def _open_with_alerts(side, near_px, adverse_px, symbol="BTC"):
    from trading.lifecycle.db import get_db
    from trading.lifecycle import write
    conn = get_db()
    pid = write.record_prediction(conn, _zone_draft(symbol=symbol))
    tid = write.record_thesis(conn, prediction_id=pid, symbol=symbol,
                              text_md="t", agent="plutus-main")
    did = write.record_decision(
        conn, thesis_id=tid,
        action="open_long" if side == "long" else "open_short",
        agent="plutus-main", conviction=0.7,
        params={"alert_near_px": near_px, "alert_adverse_px": adverse_px})
    trid = write.record_trade(conn, decision_id=did, venue="hyperliquid",
                              symbol=symbol, side=side, size=0.01, fill_price=100_000.0)
    posid = write.open_position(conn, venue="hyperliquid", symbol=symbol, side=side,
                                size=0.01, opening_trade_id=trid)
    conn.commit()
    return posid


def _mids(monkeypatch, price):
    info = MagicMock()
    info.all_mids.return_value = {"BTC": str(price)}
    monkeypatch.setattr(alerts, "get_info", lambda: info)


def test_position_alert_near_long(monkeypatch):
    posid = _open_with_alerts("long", near_px=101_000.0, adverse_px=99_000.0)
    _mids(monkeypatch, 101_500)                 # up through the near edge
    fired, state = alerts.poll_hl_position_alert(state={})
    assert {f["kind"] for f in fired} == {"near"}
    assert fired[0]["position_id"] == posid
    assert state["position_id"] == posid and "near" in state["fired"]


def test_position_alert_adverse_long(monkeypatch):
    _open_with_alerts("long", near_px=101_000.0, adverse_px=99_000.0)
    _mids(monkeypatch, 98_500)                  # down through the adverse level
    fired, _ = alerts.poll_hl_position_alert(state={})
    assert {f["kind"] for f in fired} == {"adverse"}


def test_position_alert_near_short(monkeypatch):
    # short: near (favorable) is BELOW entry, adverse ABOVE
    _open_with_alerts("short", near_px=99_000.0, adverse_px=101_000.0)
    _mids(monkeypatch, 98_500)
    fired, _ = alerts.poll_hl_position_alert(state={})
    assert {f["kind"] for f in fired} == {"near"}


def test_position_alert_dedups_per_level(monkeypatch):
    _open_with_alerts("long", near_px=101_000.0, adverse_px=99_000.0)
    _mids(monkeypatch, 101_500)
    fired1, state1 = alerts.poll_hl_position_alert(state={})
    assert fired1                                # fires once
    fired2, _ = alerts.poll_hl_position_alert(state=state1)
    assert fired2 == []                          # already fired this level


def test_position_alert_silent_when_flat(monkeypatch):
    from trading.lifecycle.db import get_db
    get_db()
    _mids(monkeypatch, 100_000)
    fired, state = alerts.poll_hl_position_alert(state={})
    assert fired == [] and state == {}


# ── Regression: the 2026-08-16 descriptor leak ──────────────────────────
#
# These two pollers leaked one lifecycle.db descriptor per tick inside the
# watcher daemon. Full account in ``integrity._check_watcher_fds``, the
# invariant that now guards the class. Asserted here: the pollers CLOSE what
# they open, on every exit path.


class _ConnTracker:
    """Hands out real connections and remembers whether each was closed."""

    def __init__(self, real):
        self._real = real          # captured BEFORE patching, or this recurses
        self.conns = []

    def __call__(self, *a, **k):
        conn = self._real(*a, **k)
        self.conns.append(conn)
        return conn

    @property
    def leaked(self):
        out = []
        for c in self.conns:
            try:
                c.execute("SELECT 1")
                out.append(c)        # still usable → never closed
            except Exception:
                pass                 # closed, as it should be
        return out


@pytest.fixture
def track_db(monkeypatch):
    import trading.lifecycle.db as dbmod
    tracker = _ConnTracker(dbmod.get_db)
    monkeypatch.setattr(dbmod, "get_db", tracker)
    return tracker


def test_prediction_resolution_closes_its_connection(monkeypatch, track_db):
    _mids(monkeypatch, 111_000)

    alerts.poll_hl_prediction_resolution(state={})

    assert track_db.conns, "poller never opened a connection — test is not exercising the path"
    assert not track_db.leaked, (
        f"{len(track_db.leaked)} lifecycle.db connection(s) left open by "
        f"poll_hl_prediction_resolution — this is the daemon fd leak"
    )


def test_position_alert_closes_its_connection(monkeypatch, track_db):
    _mids(monkeypatch, 100_000)

    alerts.poll_hl_position_alert(state={})

    assert track_db.conns, "poller never opened a connection — test is not exercising the path"
    assert not track_db.leaked, (
        f"{len(track_db.leaked)} lifecycle.db connection(s) left open by "
        f"poll_hl_position_alert — this is the daemon fd leak"
    )


def test_position_alert_closes_on_the_flat_early_return(monkeypatch, track_db):
    """The early return that fires most often — flat desk, no open position."""
    fired, state = alerts.poll_hl_position_alert(state={})

    assert fired == [] and state == {}
    assert track_db.conns, "poller never opened a connection — test is not exercising the path"
    assert not track_db.leaked, (
        "connection leaked on the flat early-return path — the one the "
        "daemon takes on almost every 5-second tick while the desk is flat"
    )
