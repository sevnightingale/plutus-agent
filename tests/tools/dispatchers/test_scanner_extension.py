"""AST scanner enumerates both discovery roots.

The restructure split discovery into two roots: flat harness tools
(``_enumerate_tool_modules``) and the trading domain
(``_enumerate_trading_modules`` — ``dispatchers/``, ``lifecycle/queries/``,
``integrations/<source>/``). These tests verify the enumeration helpers
directly so the contract is exercised without needing real tool modules
at every nesting level.
"""

from pathlib import Path

import pytest

from harness.tools.registry import (
    _enumerate_tool_modules,
    _enumerate_trading_modules,
)


@pytest.fixture()
def fake_tools_dir(tmp_path: Path) -> Path:
    """Fake flat `tools/` root (the harness side of discovery)."""
    root = tmp_path / "tools"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "registry.py").write_text("# real registry, must be skipped\n")
    (root / "mcp_tool.py").write_text("# lazy-load module, must be skipped\n")
    (root / "flat_tool.py").write_text(
        "from harness.tools.registry import registry\n"
        "registry.register(name='flat', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )
    return root


@pytest.fixture()
def fake_trading_dir(tmp_path: Path) -> Path:
    """Fake `trading/` root with one file at every supported nesting."""
    root = tmp_path / "trading"
    root.mkdir()
    (root / "__init__.py").write_text("")

    disp = root / "dispatchers"
    disp.mkdir()
    (disp / "__init__.py").write_text("")
    (disp / "x.py").write_text(
        "from harness.tools.registry import registry\n"
        "registry.register(name='x', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    queries = root / "lifecycle" / "queries"
    queries.mkdir(parents=True)
    (root / "lifecycle" / "__init__.py").write_text("")
    (queries / "__init__.py").write_text("")
    (queries / "y.py").write_text(
        "from harness.tools.registry import registry\n"
        "registry.register(name='y', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    integ = root / "integrations"
    integ.mkdir()
    (integ / "__init__.py").write_text("")
    src = integ / "hyperliquid"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "data_points.py").write_text(
        "from harness.tools.registry import registry\n"
        "registry.register(name='hl_dp', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    return root


def test_enumerate_includes_all_supported_layers(fake_tools_dir, fake_trading_dir):
    tool_names = [name for name, _ in _enumerate_tool_modules(fake_tools_dir)]
    trading_names = [name for name, _ in _enumerate_trading_modules(fake_trading_dir)]

    assert "tools.flat_tool" in tool_names
    assert "trading.dispatchers.x" in trading_names
    assert "trading.lifecycle.queries.y" in trading_names
    assert "trading.integrations.hyperliquid.data_points" in trading_names


def test_enumerate_skips_internals(fake_tools_dir, fake_trading_dir):
    names = [name for name, _ in _enumerate_tool_modules(fake_tools_dir)]
    names += [name for name, _ in _enumerate_trading_modules(fake_trading_dir)]

    # registry.py / mcp_tool.py are deliberately excluded from the flat scan
    assert not any(n.endswith(".registry") for n in names)
    assert not any(n.endswith(".mcp_tool") for n in names)
    # __init__.py is never returned for any subdir
    assert not any(n.endswith(".__init__") for n in names)


def test_enumerate_handles_missing_subdirs(tmp_path: Path):
    """A flat-only tools root — older Hermes-style — still works."""
    root = tmp_path / "tools"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "z.py").write_text(
        "from harness.tools.registry import registry\n"
        "registry.register(name='z', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )
    names = [name for name, _ in _enumerate_tool_modules(root)]
    assert names == ["tools.z"]


def test_enumerate_trading_handles_missing_subdirs(tmp_path: Path):
    """A trading root missing some shapes yields only what exists."""
    root = tmp_path / "trading"
    root.mkdir()
    (root / "__init__.py").write_text("")
    disp = root / "dispatchers"
    disp.mkdir()
    (disp / "__init__.py").write_text("")
    (disp / "only.py").write_text(
        "from harness.tools.registry import registry\n"
        "registry.register(name='only', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )
    names = [name for name, _ in _enumerate_trading_modules(root)]
    assert names == ["trading.dispatchers.only"]
