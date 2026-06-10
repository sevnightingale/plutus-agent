"""Surviving read-side dispatchers against lifecycle.db v2.

The v1 write dispatchers (record_event & co.) and execution wrappers died in
the R1 clean-cut; fetch_data_point / list_* / account_state survive and now
snapshot into the v2 schema.
"""

import json

import pytest

import trading.dispatchers.account_state  # noqa: F401 — registers on import
import trading.dispatchers.fetch_data_point  # noqa: F401
import trading.dispatchers.list_data_points  # noqa: F401
from harness.tools.registry import registry as tool_registry
from trading.lifecycle.db import get_db
from trading.perception.core import data_point_registry


@pytest.fixture()
def fake_dp(monkeypatch):
    entry = data_point_registry.DataPointEntry(
        name="test_price",
        fn=lambda symbol="BTC": {"price": 101.5, "symbol": symbol},
        source="test",
        category="market",
        returns_schema={},
        description="test data point",
        params_schema={},
        tags=["test"],
    )
    monkeypatch.setitem(data_point_registry._REGISTRY, "test_price", entry)
    return entry


def _call(name, args):
    tool = tool_registry.get_entry(name)
    return json.loads(tool.handler(args))


class TestFetchDataPoint:
    def test_fetch_snapshots_to_v2(self, fake_dp):
        result = _call("fetch_data_point", {"name": "test_price",
                                            "params": {"symbol": "BTC"},
                                            "force_fresh": True})
        assert result["value"]["price"] == 101.5
        snap = get_db().execute(
            "SELECT name, value_json, source FROM data_point_snapshots WHERE id=?",
            (result["snapshot_id"],),
        ).fetchone()
        assert snap["name"] == "test_price"
        assert json.loads(snap["value_json"])["price"] == 101.5

    def test_unknown_data_point_errors(self):
        result = _call("fetch_data_point", {"name": "nope_never"})
        assert "error" in result


class TestListDataPoints:
    def test_lists_registered(self, fake_dp):
        result = _call("list_data_points", {})
        names = [d["name"] for d in result["entries"]]
        assert "test_price" in names
