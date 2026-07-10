"""trading-suite fixtures."""

import pytest


@pytest.fixture(autouse=True)
def ready_trade_path(monkeypatch):
    """Trade-path readiness is a live on-chain check `desk_open_position` now
    enforces — default every test to READY; readiness-gate tests override."""
    import trading.dispatchers.desk_execution as mod
    monkeypatch.setattr(mod, "_trade_readiness",
                        lambda: {"ready": True, "reason": "test"})
