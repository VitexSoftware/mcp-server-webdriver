"""
Stub heavy optional imports so unit tests run without the full fastmcp
dependency tree (opentelemetry, importlib_metadata, …) being installed.

When fastmcp IS installed (dev machine, integration CI), the real modules
are used and the stubs are never injected.
"""
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("WEBDRIVER_READONLY", "false")

_FASTMCP_REAL = True


def _inject_fastmcp_stubs() -> None:
    util = ModuleType("fastmcp.utilities")
    types_mod = ModuleType("fastmcp.utilities.types")
    types_mod.Image = MagicMock  # type: ignore[attr-defined]
    sys.modules.setdefault("fastmcp.utilities", util)
    sys.modules.setdefault("fastmcp.utilities.types", types_mod)

    fm = ModuleType("fastmcp")
    fm.FastMCP = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    fm.Context = type("Context", (), {})  # type: ignore[attr-defined]
    fm.utilities = util  # type: ignore[attr-defined]
    sys.modules.setdefault("fastmcp", fm)


try:
    import fastmcp  # noqa: F401
except ImportError:
    _FASTMCP_REAL = False
    _inject_fastmcp_stubs()


def pytest_collection_modifyitems(config, items):
    if _FASTMCP_REAL:
        return
    skip = pytest.mark.skip(reason="requires real fastmcp installed (stubbed in build env)")
    stubbed_items = {
        ("TestToolCount", "test_tool_names"),
        ("TestBrowserOpenHasNoProfileParams", "test_no_profile_params_on_tool"),
    }
    for item in items:
        if item.cls and (item.cls.__name__, item.name) in stubbed_items:
            item.add_marker(skip)
