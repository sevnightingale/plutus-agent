"""Hyperliquid outcomes — math correctness across known scenarios.

Seeds a temp lifecycle.db with positions / trades / position_evaluations,
stubs ``Info.candles_snapshot`` to feed deterministic candle data, then
asserts the computed outcome columns are sound.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from harness.agent.lifecycle_db import get_lifecycle_db, reset_lifecycle_db_singleton
from harness.tools.integrations.hyperliquid import _client, outcomes


@pytest.fixture(autouse=True)
def _reset_singletons():
    _client.reset_singletons_for_tests()
    reset_lifecycle_db_singleton()
    yield
    _client.reset_singletons_for_tests()
    reset_lifecycle_db_singleton()


def _seed(symbol: str, side: str, entry: float, exit_: float, size: float,
          opened_at: float, closed_at: float,
          sl: float = None,
          conviction_traj: list = None,
          invalidation_at: float = None) -> int:
    """Create thesis + decisions + trades + position + close in a temp db."""
    db = get_lifecycle_db()
    conn = db.conn()

    conn.execute(
        "INSERT INTO theses(ts, symbol, text_md, invalidation_criteria_json) "
        "VALUES (?, ?, ?, ?)",
        (opened_at, symbol, "test thesis", '{"min_price": 1.0}'),
    )
    thesis_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    import json as _json
    decision_params = {"sl": sl} if sl is not None else {}
    conn.execute(
        "INSERT INTO decisions(thesis_id, ts, action, params_json, conviction) "
        "VALUES (?, ?, ?, ?, ?)",
        (thesis_id, opened_at, "open_long" if side == "long" else "open_short",
         _json.dumps(decision_params), 0.7),
    )
    decision_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price, slippage_bp) "
        "VALUES (?, ?, 'hyperliquid', ?, ?, ?, ?, 5)",
        (decision_id, opened_at, symbol, side, size, entry),
    )
    open_trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO positions(venue, symbol, side, size, opening_trade_id, status, opened_at) "
        "VALUES ('hyperliquid', ?, ?, ?, ?, 'open', ?)",
        (symbol, side, size, open_trade_id, opened_at),
    )
    position_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO decisions(thesis_id, ts, action, params_json, conviction) "
        "VALUES (?, ?, 'close', '{}', 0.5)",
        (thesis_id, closed_at),
    )
    close_dec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO trades(decision_id, ts, venue, symbol, side, size, fill_price, slippage_bp) "
        "VALUES (?, ?, 'hyperliquid', ?, 'close', ?, ?, 5)",
        (close_dec_id, closed_at, symbol, size, exit_),
    )
    close_trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "UPDATE positions SET closing_trade_id = ?, status = 'closed', "
        "closed_at = ? WHERE id = ?",
        (close_trade_id, closed_at, position_id),
    )

    if conviction_traj:
        for ts, conv, status in conviction_traj:
            conn.execute(
                "INSERT INTO position_evaluations(position_id, ts, conviction, "
                "thesis_status, recommended_action) VALUES (?, ?, ?, ?, 'hold')",
                (position_id, ts, conv, status),
            )

    db._conn.commit()
    return position_id


def _stub_candles(monkeypatch, candles):
    """Make Info.candles_snapshot return the provided candle list."""
    info_mock = MagicMock()
    info_mock.candles_snapshot.return_value = candles
    monkeypatch.setattr(_client, "get_info", lambda: info_mock); monkeypatch.setattr(outcomes, "get_info", lambda: info_mock)


def test_long_winner_basic_pnl(monkeypatch):
    t0 = time.time() - 3600
    t1 = time.time()
    pid = _seed("BTC", "long", entry=80000.0, exit_=81000.0, size=0.01,
                opened_at=t0, closed_at=t1, sl=79000.0)
    _stub_candles(monkeypatch, [
        {"t": int(t0 * 1000), "o": "80000", "h": "81200", "l": "79800", "c": "80500", "v": "100"},
        {"t": int((t0 + 1800) * 1000), "o": "80500", "h": "81200", "l": "80200", "c": "81000", "v": "100"},
    ])

    out = outcomes.compute_outcome(pid)
    assert out["realized_pnl_usd"] == pytest.approx(10.0, rel=1e-6)
    assert out["realized_pnl_pct"] == pytest.approx(1.25, rel=1e-3)
    # r_multiple: pnl per unit risk. risk=1000. exit-entry=1000 → r=1.0
    assert out["r_multiple"] == pytest.approx(1.0, rel=1e-3)
    assert out["holding_minutes"] == pytest.approx(60.0, rel=1e-2)
    # MFE 81200 vs entry 80000 → +1.5%
    assert out["mfe_pct"] == pytest.approx(1.5, rel=1e-3)
    # MAE 79800 vs entry 80000 → -0.25%
    assert out["mae_pct"] == pytest.approx(-0.25, rel=1e-3)
    assert out["slippage_total_bp"] == pytest.approx(10.0, rel=1e-3)


def test_short_loser(monkeypatch):
    t0 = time.time() - 1800
    t1 = time.time()
    pid = _seed("ETH", "short", entry=2400.0, exit_=2440.0, size=1.0,
                opened_at=t0, closed_at=t1, sl=2440.0)
    _stub_candles(monkeypatch, [
        {"t": int(t0 * 1000), "o": "2400", "h": "2450", "l": "2390", "c": "2430", "v": "10"},
    ])
    out = outcomes.compute_outcome(pid)
    # Short: PnL = -1 * (exit - entry) * size = -40 USD
    assert out["realized_pnl_usd"] == pytest.approx(-40.0, rel=1e-6)
    # r_multiple: short risk = 40 USD per unit; pnl/risk = -1.0
    assert out["r_multiple"] == pytest.approx(-1.0, rel=1e-3)


def test_no_sl_yields_none_r_multiple(monkeypatch):
    t0 = time.time() - 60
    t1 = time.time()
    pid = _seed("BTC", "long", entry=80000.0, exit_=80100.0, size=0.001,
                opened_at=t0, closed_at=t1, sl=None)
    _stub_candles(monkeypatch, [])
    out = outcomes.compute_outcome(pid)
    assert out["r_multiple"] is None
    assert out["realized_pnl_pct"] == pytest.approx(0.125, rel=1e-3)


def test_invalidation_timing(monkeypatch):
    t0 = time.time() - 7200
    t_inv = t0 + 1800
    t1 = t0 + 3600
    pid = _seed("BTC", "long", entry=80000.0, exit_=79500.0, size=0.01,
                opened_at=t0, closed_at=t1, sl=79000.0,
                conviction_traj=[
                    (t0 + 600, 0.7, "intact"),
                    (t_inv,  0.4, "invalidated"),
                    (t_inv + 300, 0.3, "invalidated"),
                ])
    _stub_candles(monkeypatch, [])
    out = outcomes.compute_outcome(pid)
    assert out["invalidation_triggered_at"] == pytest.approx(t_inv, rel=1e-6)
    # invalidation 30 min before exit
    assert out["invalidation_to_exit_minutes"] == pytest.approx(30.0, rel=1e-3)


def test_compute_outcome_rejects_unclosed_position(monkeypatch):
    t0 = time.time() - 60
    pid = _seed("BTC", "long", entry=80000.0, exit_=80000.0, size=0.001,
                opened_at=t0, closed_at=t0 + 30)
    # mark it open again
    db = get_lifecycle_db()
    db.conn().execute("UPDATE positions SET closed_at = NULL, status = 'open' WHERE id = ?", (pid,))
    db._conn.commit()
    with pytest.raises(ValueError, match="not closed"):
        outcomes.compute_outcome(pid)
