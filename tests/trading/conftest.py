"""trading-suite fixtures."""

import pytest


@pytest.fixture(autouse=True)
def ready_trade_path(monkeypatch):
    """Trade-path readiness is a live on-chain check `desk_open_position` now
    enforces — default every test to READY; readiness-gate tests override."""
    import trading.dispatchers.desk_execution as mod
    monkeypatch.setattr(mod, "_trade_readiness",
                        lambda: {"ready": True, "reason": "test"})


def arm_pilot():
    """Arm the operator pilot sentinel in the test-isolated HERMES_HOME."""
    from harness.constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "PILOT").touch()
