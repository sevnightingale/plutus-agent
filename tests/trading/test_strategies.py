"""Strategy file format, validation, loader, mirror sync."""

import pytest

from trading.lifecycle.db import get_db
from trading.strategies import loader
from trading.strategies.files import Strategy, parse_strategy, render_strategy, validate_strategy

BODY = """
# Hypothesis
After a deeply negative funding flush while price holds support, BTC mean-reverts
upward within 24h.

# Mechanism
Over-levered shorts pay to hold; forced covering supplies the bid. The other side
is late momentum shorts chasing the flush.

# Trigger
hl_funding < -0.01%/h AND price within 1 ATR of support.

# Invalidation template
Support breaks on rising CVD sell pressure — the flush was distribution, not capitulation.
"""


def _strategy(tmp_path, **over):
    fields = dict(
        name="funding-flush-reversal",
        status="test",
        timescale="intraday",
        mechanism_family="flow",
        file_path=tmp_path / "funding-flush-reversal.md",
        regime_applicability={"direction": ["ranging"], "volatility": ["elevated"]},
        data_points=[
            {"name": "hl_funding", "params": {"symbol": "BTC"}, "weight": 0.4},
            {"name": "hl_cvd", "params": {"symbol": "BTC"}, "weight": 0.3},
            {"name": "ta_atr", "params": {"symbol": "BTC"}, "weight": 0.3},
        ],
        created="2026-06-15",
        body_md=BODY,
    )
    fields.update(over)
    return Strategy(**fields)


@pytest.fixture()
def conn(tmp_path):
    c = get_db(tmp_path / "lifecycle.db")
    yield c
    c.close()


class TestFileFormat:
    def test_render_parse_round_trip(self, tmp_path):
        s = _strategy(tmp_path)
        s.file_path.write_text(render_strategy(s), encoding="utf-8")
        back = parse_strategy(s.file_path)
        assert back.name == s.name
        assert back.status == "test"
        assert back.weights == {
            "hl_funding(symbol=BTC)": 0.4,
            "hl_cvd(symbol=BTC)": 0.3,
            "ta_atr(symbol=BTC)": 0.3,
        }
        assert "forced covering" in back.body_section("Mechanism")

    def test_validation_catches_problems(self, tmp_path):
        bad = _strategy(
            tmp_path, status="paused", timescale="longer",
            data_points=[{"name": "hl_funding", "weight": 1.4}],
            body_md="# Hypothesis\nx\n",
        )
        problems = validate_strategy(bad)
        joined = "\n".join(problems)
        assert "status" in joined
        assert "timescale" in joined
        assert "1.4" in joined
        assert "Mechanism" in joined

    def test_variant_requires_stated_tweak(self, tmp_path):
        v = _strategy(tmp_path, parent_strategy="funding-flush-reversal")
        assert any("tweak" in p for p in validate_strategy(v))

    def test_unregistered_dp_needs_self_extension_hook(self, tmp_path):
        s = _strategy(tmp_path)
        problems = validate_strategy(s, known_data_points={"hl_funding", "hl_cvd"})
        assert any("ta_atr" in p for p in problems)
        s.missing_data_points = ["ta_atr"]
        assert validate_strategy(
            s, known_data_points={"hl_funding", "hl_cvd"}) == []


class TestLoader:
    def test_status_gates_context(self, tmp_path, conn):
        for name, status in [
            ("alpha-live", "active"), ("beta-test", "test"),
            ("gamma-dorm", "dormant"), ("delta-dead", "retired"),
        ]:
            s = _strategy(tmp_path, name=name, status=status,
                          file_path=tmp_path / f"{name}.md")
            loader.write_strategy(s, conn)

        live = loader.load_strategies(base_dir=tmp_path)
        assert sorted(s.name for s in live) == ["alpha-live", "beta-test"]

        block = loader.strategy_context_block(base_dir=tmp_path)
        assert "alpha-live" in block and "beta-test" in block
        assert "gamma-dorm" not in block and "delta-dead" not in block

    def test_empty_book_says_no_trades(self, tmp_path):
        block = loader.strategy_context_block(base_dir=tmp_path)
        assert "NO" in block and "trades" in block

    def test_write_syncs_mirror(self, tmp_path, conn):
        loader.write_strategy(_strategy(tmp_path), conn)
        row = conn.execute(
            "SELECT status, timescale, mechanism_family, hypothesis_md "
            "FROM strategies WHERE name='funding-flush-reversal'").fetchone()
        assert row["status"] == "test"
        assert row["timescale"] == "intraday"
        assert "mean-reverts" in row["hypothesis_md"]

    def test_set_status_updates_file_and_mirror(self, tmp_path, conn):
        loader.write_strategy(_strategy(tmp_path), conn)
        loader.set_status("funding-flush-reversal", "retired", conn,
                          reason="failed checkpoint 4/10", base_dir=tmp_path)
        back = parse_strategy(tmp_path / "funding-flush-reversal.md")
        assert back.status == "retired"
        assert back.retirement_reason == "failed checkpoint 4/10"
        row = conn.execute(
            "SELECT status, retirement_reason FROM strategies "
            "WHERE name='funding-flush-reversal'").fetchone()
        assert row["status"] == "retired"
        assert "checkpoint" in row["retirement_reason"]

    def test_invalid_write_refused_and_nothing_lands(self, tmp_path, conn):
        bad = _strategy(tmp_path, mechanism_family="vibes")
        with pytest.raises(ValueError, match="refused"):
            loader.write_strategy(bad, conn)
        assert not bad.file_path.exists()
        assert conn.execute("SELECT COUNT(*) c FROM strategies").fetchone()["c"] == 0
