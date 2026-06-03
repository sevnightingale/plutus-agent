"""WORLDVIEW.md infra — seed + loader + frozen-snapshot semantics."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_seed_creates_file_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Force HERMES_HOME re-resolution
    import plutus_constants
    plutus_constants._HERMES_HOME_OVERRIDE = None  # type: ignore[attr-defined]

    from plutus_cli.config import _ensure_default_worldview_md

    wv_path = tmp_path / "WORLDVIEW.md"
    assert not wv_path.exists()

    _ensure_default_worldview_md(tmp_path)
    assert wv_path.exists()
    body = wv_path.read_text(encoding="utf-8")
    assert "last_updated" in body
    assert "watchlist:" in body
    assert "active_hypotheses:" in body
    assert "# Macro" in body
    assert "# Per-symbol" in body
    assert "# Open hypotheses" in body


def test_seed_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from plutus_cli.config import _ensure_default_worldview_md

    _ensure_default_worldview_md(tmp_path)
    wv_path = tmp_path / "WORLDVIEW.md"
    custom = "---\nlast_updated: 2026-05-04\n---\nplutus's edits"
    wv_path.write_text(custom, encoding="utf-8")

    # Running again must not clobber
    _ensure_default_worldview_md(tmp_path)
    assert wv_path.read_text(encoding="utf-8") == custom


def test_loader_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Don't seed — file shouldn't exist
    from agent.worldview_loader import load_worldview_md
    # Ensure path will be empty so loader sees no file
    wv = tmp_path / "WORLDVIEW.md"
    if wv.exists():
        wv.unlink()
    monkeypatch.setattr(
        "plutus_cli.config.ensure_hermes_home", lambda: None
    )
    assert load_worldview_md() is None


def test_loader_returns_content_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "plutus_cli.config.ensure_hermes_home", lambda: None
    )
    wv = tmp_path / "WORLDVIEW.md"
    wv.write_text("---\nfoo: bar\n---\n# body\nhello", encoding="utf-8")

    from agent.worldview_loader import load_worldview_md
    content = load_worldview_md()
    assert content is not None
    assert "foo: bar" in content
    assert "hello" in content


def test_loader_returns_none_on_empty_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "plutus_cli.config.ensure_hermes_home", lambda: None
    )
    wv = tmp_path / "WORLDVIEW.md"
    wv.write_text("    \n\n  ", encoding="utf-8")  # whitespace only

    from agent.worldview_loader import load_worldview_md
    assert load_worldview_md() is None


def test_default_worldview_md_has_all_required_keys():
    """Sanity-check the seed has every PLUTUS-spec field at the top level."""
    from plutus_cli.default_worldview import DEFAULT_WORLDVIEW_MD
    required = [
        "last_updated:", "last_updated_by:", "horizon:", "watchlist:",
        "risk_posture:", "regime:", "key_levels:", "active_hypotheses:",
        "open_positions_summary:", "portfolio_summary:", "operator_state:",
        "recent_learnings:",
    ]
    for k in required:
        assert k in DEFAULT_WORLDVIEW_MD, f"missing required key: {k}"
