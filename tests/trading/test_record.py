"""record() fan-out: lifecycle.db + ledger journal (+ forum error surfacing)."""

import json
import time

import pytest

import trading.dispatchers.record  # noqa: F401 — registers on import
from harness.constants import get_hermes_home
from harness.tools.registry import registry as tool_registry
from trading.lifecycle import write
from trading.lifecycle.db import get_db


def _call(args):
    return json.loads(tool_registry.get_entry("record").handler(args))


class TestRecord:
    def test_observation_fans_out_to_db_and_journal(self):
        result = _call({"kind": "observation", "text": "BTC funding flipped negative",
                        "kind_tag": "noticed", "symbol": "BTC"})
        assert result["ok"], result
        row = get_db().execute(
            "SELECT kind, text_md, agent FROM observations WHERE id=?",
            (result["observation_id"],)).fetchone()
        assert row["kind"] == "noticed"
        assert row["agent"] == "plutus-main"
        journal = get_hermes_home() / "ledger" / f"{time.strftime('%Y-%-m-%-d')}.md"
        assert "funding flipped negative" in journal.read_text()

    def test_decision_requires_thesis_and_action(self):
        assert "error" in _call({"kind": "decision", "text": "opening"})

    def test_decision_writes_chain(self):
        conn = get_db()
        # seed prediction + thesis for the FK chain
        conn.execute(
            "INSERT INTO predictions(ts, horizon_ts, timescale, claim_md, "
            "success_criteria_json, conviction) VALUES (?, ?, 'intraday', 'x', '{}', 0.7)",
            (time.time(), time.time() + 3600))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        tid = write.record_thesis(conn, prediction_id=pid, symbol="BTC",
                                  text_md="t", agent="plutus-main")
        result = _call({"kind": "decision", "text": "funding the flush setup",
                        "thesis_id": tid, "action": "open_long", "conviction": 0.7})
        assert result["ok"], result
        row = get_db().execute(
            "SELECT action, conviction FROM decisions WHERE id=?",
            (result["decision_id"],)).fetchone()
        assert row["action"] == "open_long"
        assert row["conviction"] == pytest.approx(0.7)

    def test_forum_post_missing_ids_is_loud(self):
        result = _call({"kind": "forum_post", "text": "rationale", "title": "Opened BTC"})
        assert not result["ok"]
        assert any("forum" in e for e in result["errors"])
        # but the journal + observation still landed (partial fan-out reported)
        assert "observation_id" in result

    def test_eod_appends_close(self):
        result = _call({"kind": "eod", "text": "Quiet day; two predictions registered."})
        assert result["ok"]
        journal = get_hermes_home() / "ledger" / f"{time.strftime('%Y-%-m-%-d')}.md"
        assert "EOD" in journal.read_text()

    def test_bad_kind_refused(self):
        assert "error" in _call({"kind": "vibes", "text": "x"})
