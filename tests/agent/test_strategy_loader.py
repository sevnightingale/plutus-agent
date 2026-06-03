"""Tests for agent/strategy_loader.py — strategy_conviction frontmatter (V2)."""

import textwrap
from pathlib import Path

import pytest

from agent import strategy_loader


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Ensure dir tree exists.
    strategy_loader.ensure_strategies_dir()
    yield tmp_path


def _write_strategy(tmp_home: Path, stage: str, name: str, frontmatter: str, body: str = "body"):
    path = tmp_home / "strategies" / stage / f"{name}.md"
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return path


class TestGetStrategyConviction:
    def test_returns_declared_value(self, tmp_home):
        _write_strategy(tmp_home, "trial", "support-hold", textwrap.dedent("""\
            name: support-hold
            stage: trial
            strategy_conviction: 0.7
            description: test
        """))
        assert strategy_loader.get_strategy_conviction("support-hold") == 0.7

    def test_default_when_field_missing(self, tmp_home):
        _write_strategy(tmp_home, "trial", "no-conv", textwrap.dedent("""\
            name: no-conv
            stage: trial
            description: missing field
        """))
        # DEFAULT_STRATEGY_CONVICTION = 0.5
        assert strategy_loader.get_strategy_conviction("no-conv") == 0.5

    def test_unknown_strategy_returns_none(self, tmp_home):
        assert strategy_loader.get_strategy_conviction("does-not-exist") is None

    def test_clamps_above_one(self, tmp_home):
        _write_strategy(tmp_home, "active", "over", textwrap.dedent("""\
            name: over
            stage: active
            strategy_conviction: 1.5
        """))
        assert strategy_loader.get_strategy_conviction("over") == 1.0

    def test_clamps_below_zero(self, tmp_home):
        _write_strategy(tmp_home, "active", "under", textwrap.dedent("""\
            name: under
            stage: active
            strategy_conviction: -0.3
        """))
        assert strategy_loader.get_strategy_conviction("under") == 0.0

    def test_non_numeric_falls_back_to_default(self, tmp_home):
        _write_strategy(tmp_home, "active", "garbage", textwrap.dedent("""\
            name: garbage
            stage: active
            strategy_conviction: not a number
        """))
        assert strategy_loader.get_strategy_conviction("garbage") == 0.5

    def test_int_value_accepted(self, tmp_home):
        _write_strategy(tmp_home, "active", "intval", textwrap.dedent("""\
            name: intval
            stage: active
            strategy_conviction: 1
        """))
        assert strategy_loader.get_strategy_conviction("intval") == 1.0


class TestPromptBlockIncludesConviction:
    def test_strategy_conviction_appears_in_prompt_block(self, tmp_home):
        _write_strategy(tmp_home, "trial", "support-hold", textwrap.dedent("""\
            name: support-hold
            stage: trial
            strategy_conviction: 0.6
            description: hold support
            regime_applicability: [range_bound]
        """))
        block = strategy_loader.build_strategy_prompt_block()
        assert block is not None
        assert "support-hold" in block
        assert "strategy_conviction: 0.60" in block

    def test_strategy_without_conviction_field_omits_line(self, tmp_home):
        _write_strategy(tmp_home, "trial", "no-conv", textwrap.dedent("""\
            name: no-conv
            stage: trial
            description: no conv field
        """))
        block = strategy_loader.build_strategy_prompt_block()
        # The line shouldn't appear (only printed when frontmatter declares it).
        assert "strategy_conviction:" not in block
