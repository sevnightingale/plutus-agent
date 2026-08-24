"""opencode_go_usage — subscription-quota watchdog verdict contract.

Network is mocked throughout; these cover the threshold flags, the
max-window selection, and the fail-loud-in-band encodings (missing key,
HTTP error, malformed payload). The DP never raises — failures are data.
"""

import io
import json
import urllib.error

import pytest

from trading.integrations.opencode import data_points as dp


def _payload(rolling=1, weekly=17, monthly=33):
    return {
        "usage": {
            "rolling": {"status": "ok", "percent": rolling,
                        "resetsAt": "2026-08-24T17:01:19.840Z"},
            "weekly": {"status": "ok", "percent": weekly,
                       "resetsAt": "2026-08-31T00:00:00.840Z"},
            "monthly": {"status": "ok", "percent": monthly,
                        "resetsAt": "2026-09-19T05:07:49.840Z"},
        }
    }


class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")


def _mock_fetch(monkeypatch, payload):
    monkeypatch.setattr(dp.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(payload))


class TestVerdicts:
    def test_ok_all_windows_reported(self, monkeypatch):
        _mock_fetch(monkeypatch, _payload())
        out = dp.opencode_go_usage()
        assert out["fetch_failed"] is False
        assert (out["rolling_percent"], out["weekly_percent"],
                out["monthly_percent"]) == (1.0, 17.0, 33.0)
        assert out["max_window"] == "monthly" and out["max_percent"] == 33.0
        assert out["low"] is False and out["critical"] is False
        assert out["reason"].startswith("OK")
        assert out["resets"]["monthly"] == "2026-09-19T05:07:49.840Z"

    def test_warn_at_80_on_any_window(self, monkeypatch):
        _mock_fetch(monkeypatch, _payload(weekly=80))
        out = dp.opencode_go_usage()
        assert out["low"] is True and out["critical"] is False
        assert out["max_window"] == "weekly"
        assert "WARN" in out["reason"] and "2026-08-31" in out["reason"]

    def test_critical_at_95(self, monkeypatch):
        _mock_fetch(monkeypatch, _payload(monthly=97))
        out = dp.opencode_go_usage()
        assert out["critical"] is True and out["low"] is True
        assert "CRITICAL" in out["reason"]

    def test_partial_windows_still_verdict(self, monkeypatch):
        _mock_fetch(monkeypatch, {"usage": {"monthly": {"percent": 40}}})
        out = dp.opencode_go_usage()
        assert out["fetch_failed"] is False
        assert out["monthly_percent"] == 40.0
        assert out["rolling_percent"] is None
        assert out["max_window"] == "monthly"


class TestFailuresAreData:
    def test_missing_key_encoded(self, monkeypatch):
        monkeypatch.delenv("OPENCODE_GO_API_KEY")
        out = dp.opencode_go_usage()
        assert out["fetch_failed"] is True
        assert "OPENCODE_GO_API_KEY" in out["reason"]

    def test_http_error_encoded(self, monkeypatch):
        def _raise(req, timeout):
            raise urllib.error.HTTPError(
                dp.USAGE_URL, 503, "unavailable", {}, io.BytesIO(b""))
        monkeypatch.setattr(dp.urllib.request, "urlopen", _raise)
        out = dp.opencode_go_usage()
        assert out["fetch_failed"] is True and "503" in out["reason"]
        assert out["low"] is False and out["critical"] is False

    def test_malformed_payload_encoded(self, monkeypatch):
        _mock_fetch(monkeypatch, {"usage": {"rolling": {"status": "ok"}}})
        out = dp.opencode_go_usage()
        assert out["fetch_failed"] is True
        assert "no usage windows" in out["reason"]
