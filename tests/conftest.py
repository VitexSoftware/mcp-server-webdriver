"""
Stub heavy optional imports so unit tests run without the full fastmcp
dependency tree (opentelemetry, importlib_metadata, …) being installed.

When fastmcp IS installed (dev machine, integration CI), the real modules
are used and the stubs are never injected.
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _inject_fastmcp_stubs() -> None:
    # fastmcp.utilities.types needs Image before server.py can be imported
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
    _inject_fastmcp_stubs()
