"""AST scanner picks up dispatcher / lifecycle / integration modules.

Phase 4a extends ``tools.registry.discover_builtin_tools`` to recurse into
``tools/dispatchers/``, ``tools/lifecycle/``, and ``tools/integrations/<source>/``
in addition to the flat ``tools/*.py`` layout. These tests verify the
enumeration helper directly so the contract is exercised without needing
real tool modules at every nesting level.
"""

import shutil
from pathlib import Path

import pytest

from harness.tools.registry import _enumerate_tool_modules, discover_builtin_tools


@pytest.fixture()
def fake_tools_dir(tmp_path: Path) -> Path:
    """Build a fake `tools/` layout with one file at every supported nesting."""
    root = tmp_path / "tools"
    root.mkdir()
    (root / "__init__.py").write_text("")

    # Flat tools/*.py
    (root / "registry.py").write_text("# real registry, must be skipped\n")
    (root / "mcp_tool.py").write_text("# lazy-load module, must be skipped\n")
    (root / "flat_tool.py").write_text(
        "from tools.registry import registry\n"
        "registry.register(name='flat', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    # tools/dispatchers/*.py
    disp = root / "dispatchers"
    disp.mkdir()
    (disp / "__init__.py").write_text("")
    (disp / "x.py").write_text(
        "from tools.registry import registry\n"
        "registry.register(name='x', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    # tools/lifecycle/*.py
    lc = root / "lifecycle"
    lc.mkdir()
    (lc / "__init__.py").write_text("")
    (lc / "y.py").write_text(
        "from tools.registry import registry\n"
        "registry.register(name='y', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    # tools/integrations/<source>/*.py
    integ = root / "integrations"
    integ.mkdir()
    (integ / "__init__.py").write_text("")
    src = integ / "hyperliquid"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "data_points.py").write_text(
        "from tools.registry import registry\n"
        "registry.register(name='hl_dp', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )

    return root


def test_enumerate_includes_all_supported_layers(fake_tools_dir):
    candidates = _enumerate_tool_modules(fake_tools_dir)
    names = [name for name, _ in candidates]

    assert "tools.flat_tool" in names
    assert "harness.tools.dispatchers.x" in names
    assert "harness.tools.lifecycle.y" in names
    assert "harness.tools.integrations.hyperliquid.data_points" in names


def test_enumerate_skips_internals(fake_tools_dir):
    candidates = _enumerate_tool_modules(fake_tools_dir)
    names = [name for name, _ in candidates]

    # registry.py / mcp_tool.py are deliberately excluded from the flat scan
    assert "harness.tools.registry" not in names
    assert "harness.tools.mcp_tool" not in names
    # __init__.py is never returned for any subdir
    assert not any(n.endswith(".__init__") for n in names)


def test_enumerate_handles_missing_subdirs(tmp_path: Path):
    """Layout with only flat tools/*.py — older Hermes-style — still works."""
    root = tmp_path / "tools_minimal"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "z.py").write_text(
        "from tools.registry import registry\n"
        "registry.register(name='z', toolset='t', schema={}, handler=lambda a, **k: '')\n"
    )
    candidates = _enumerate_tool_modules(root)
    names = [name for name, _ in candidates]
    assert names == ["tools.z"]
