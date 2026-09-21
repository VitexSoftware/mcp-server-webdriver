#!/usr/bin/env python3
"""Live capability scenario for the WebDriver MCP server.

If geckodriver + Firefox are available under ``/usr/bin``, opens ``about:blank``,
exercises read tools (status / get_title / get_url / get_source), then closes.
Mutating tools are probed under ``WEBDRIVER_READONLY=true`` and must refuse.

Skips the browser session gracefully when Firefox/geckodriver are unavailable
(exit 0 with SKIP markers for browser tools; catalog/guard checks still run).

Usage:
  python tests/live_capability_scenario.py
  WEBDRIVER_READONLY=true python tests/live_capability_scenario.py

Exit code is 0 only when every non-skipped check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Fail-closed for this scenario unless --allow-writes
os.environ.setdefault("WEBDRIVER_READONLY", "true")


@dataclass
class CheckResult:
    name: str
    kind: str  # tool | meta | guard
    ok: bool
    detail: str = ""
    sample: Any = None
    skipped: bool = False


@dataclass
class ScenarioReport:
    browser_available: bool
    read_only: bool
    results: List[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok and not r.skipped)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok and not r.skipped)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.skipped)


def _browser_bins_available() -> tuple[bool, str]:
    gd = Path("/usr/bin/geckodriver")
    ff = None
    for candidate in ("/usr/bin/firefox", "/usr/bin/firefox-esr"):
        if Path(candidate).is_file():
            ff = candidate
            break
    if not gd.is_file():
        return False, "geckodriver missing at /usr/bin/geckodriver"
    if not ff:
        return False, "firefox/firefox-esr missing under /usr/bin"
    # Also require selenium import works
    try:
        import selenium  # noqa: F401
    except ImportError:
        return False, "python3-selenium not importable"
    return True, f"geckodriver={gd} firefox={ff}"


def _tool_is_readonly(tool: Any) -> bool:
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return True
    if isinstance(ann, dict):
        return bool(ann.get("read_only_hint", ann.get("readOnlyHint", True)))
    hint = getattr(ann, "read_only_hint", None)
    if hint is None:
        hint = getattr(ann, "readOnlyHint", None)
    return True if hint is None else bool(hint)


def _run_check(
    report: ScenarioReport,
    name: str,
    kind: str,
    fn: Callable[[], Any],
    *,
    expect_error_substr: Optional[str] = None,
    skip_reason: Optional[str] = None,
) -> Optional[Any]:
    if skip_reason:
        report.add(
            CheckResult(name=name, kind=kind, ok=True, detail=skip_reason, skipped=True)
        )
        return None
    try:
        raw = fn()
        if expect_error_substr:
            report.add(
                CheckResult(
                    name=name,
                    kind=kind,
                    ok=False,
                    detail=f"expected error containing {expect_error_substr!r}",
                    sample=str(raw)[:300],
                )
            )
            return raw
        report.add(
            CheckResult(
                name=name,
                kind=kind,
                ok=True,
                detail="ok",
                sample=str(raw)[:400],
            )
        )
        return raw
    except Exception as exc:  # noqa: BLE001
        if expect_error_substr:
            ok = expect_error_substr.lower() in str(exc).lower()
            report.add(
                CheckResult(name=name, kind=kind, ok=ok, detail=str(exc))
            )
            return None
        report.add(
            CheckResult(
                name=name,
                kind=kind,
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                sample=traceback.format_exc()[-600:],
            )
        )
        return None


def _await(coro: Any) -> Any:
    return asyncio.run(coro)


def run_scenario(*, read_only: bool = True) -> ScenarioReport:
    os.environ["WEBDRIVER_READONLY"] = "true" if read_only else "false"

    import importlib

    import server as srv

    srv = importlib.reload(srv)

    available, avail_detail = _browser_bins_available()
    report = ScenarioReport(browser_available=available, read_only=read_only)

    tools = _await(srv.mcp.list_tools())
    report.add(
        CheckResult(
            name="tool_catalog",
            kind="meta",
            ok=len(tools) >= 37,
            detail=f"{len(tools)} tools registered ({avail_detail})",
        )
    )

    missing_ann = []
    for t in tools:
        ann = getattr(t, "annotations", None)
        has = False
        if isinstance(ann, dict):
            has = "read_only_hint" in ann or "readOnlyHint" in ann
        elif ann is not None:
            has = getattr(ann, "read_only_hint", None) is not None
        if not has:
            missing_ann.append(t.name)
    report.add(
        CheckResult(
            name="annotations:readOnlyHint",
            kind="meta",
            ok=not missing_ann,
            detail="all tools annotated" if not missing_ann else f"missing: {missing_ann}",
        )
    )

    # Mutating tools that must call _require_writable (page interaction)
    guarded = [
        "browser_click",
        "browser_fill",
        "browser_upload_file",
        "browser_select",
        "browser_execute_js",
        "browser_press_key",
        "browser_accept_dialog",
        "browser_dismiss_dialog",
        "browser_set_cookie",
        "browser_set_storage",
        "browser_clear_storage",
    ]

    if read_only:
        # Guards do not need a live browser — they raise before touching WebDriver
        state = srv.BrowserState()
        ctx = SimpleNamespace(lifespan_context={"browser": state})
        for name in guarded:
            fn = getattr(srv, name)
            # Build minimal kwargs per tool
            kwargs: dict[str, Any] = {"ctx": ctx}
            if name == "browser_click":
                kwargs["selector"] = "body"
            elif name == "browser_fill":
                kwargs["selector"] = "input"
                kwargs["value"] = "x"
            elif name == "browser_upload_file":
                kwargs["selector"] = "input"
                kwargs["path"] = "/tmp/x"
            elif name == "browser_select":
                kwargs["selector"] = "select"
                kwargs["value"] = "1"
            elif name == "browser_execute_js":
                kwargs["script"] = "return 1"
            elif name == "browser_press_key":
                kwargs["key"] = "enter"
            elif name == "browser_set_cookie":
                kwargs["name"] = "n"
                kwargs["value"] = "v"
            elif name == "browser_set_storage":
                kwargs["key"] = "k"
                kwargs["value"] = "v"
            elif name in ("browser_clear_storage", "browser_accept_dialog", "browser_dismiss_dialog"):
                pass

            _run_check(
                report,
                f"readonly_guard:{name}",
                "guard",
                lambda f=fn, kw=kwargs: _await(f(**kw)),
                expect_error_substr="read-only",
            )

        for t in tools:
            if _tool_is_readonly(t):
                continue
            if t.name in guarded:
                continue
            # Session/nav tools are intentionally allowed in RO mode
            report.add(
                CheckResult(
                    name=f"catalog_rw_session:{t.name}",
                    kind="meta",
                    ok=True,
                    detail="session/nav tool (allowed under WEBDRIVER_READONLY)",
                )
            )

    skip = None if available else avail_detail
    if not available:
        for name in (
            "browser_open",
            "browser_status",
            "browser_get_title",
            "browser_get_url",
            "browser_get_source",
            "browser_close",
        ):
            report.add(
                CheckResult(
                    name=name,
                    kind="tool",
                    ok=True,
                    detail=skip or "browser unavailable",
                    skipped=True,
                )
            )
        return report

    state = srv.BrowserState()
    ctx = SimpleNamespace(lifespan_context={"browser": state})
    try:
        _run_check(
            report,
            "browser_open",
            "tool",
            lambda: _await(
                srv.browser_open(
                    url="about:blank",
                    headless=True,
                    enable_bidi=False,
                    ctx=ctx,
                )
            ),
        )
        status = _run_check(
            report,
            "browser_status",
            "tool",
            lambda: _await(srv.browser_status(ctx=ctx)),
        )
        if isinstance(status, dict):
            report.add(
                CheckResult(
                    name="browser_status:session_active",
                    kind="meta",
                    ok=bool(status.get("session_active")),
                    detail=f"session_active={status.get('session_active')}",
                )
            )

        title = _run_check(
            report,
            "browser_get_title",
            "tool",
            lambda: _await(srv.browser_get_title(ctx=ctx)),
        )
        url = _run_check(
            report,
            "browser_get_url",
            "tool",
            lambda: _await(srv.browser_get_url(ctx=ctx)),
        )
        if isinstance(url, str):
            report.add(
                CheckResult(
                    name="browser_get_url:about_blank",
                    kind="meta",
                    ok="blank" in url.lower() or url == "about:blank",
                    detail=f"url={url!r}",
                )
            )
        _ = title
        _run_check(
            report,
            "browser_get_source",
            "tool",
            lambda: _await(srv.browser_get_source(ctx=ctx)),
        )
    finally:
        _run_check(
            report,
            "browser_close",
            "tool",
            lambda: _await(srv.browser_close(ctx=ctx)),
        )
        # Ensure driver really gone even if close check failed
        try:
            state.stop()
        except Exception:  # noqa: BLE001
            pass

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Set WEBDRIVER_READONLY=false (skips readonly guards)",
    )
    parser.add_argument("--json-out", help="Write full report JSON here")
    args = parser.parse_args()

    report = run_scenario(read_only=not args.allow_writes)

    print("WebDriver MCP live scenario")
    print(f"WEBDRIVER_READONLY={'true' if report.read_only else 'false'}")
    print(f"browser_available={report.browser_available}")
    print(f"passed={report.passed} failed={report.failed} skipped={report.skipped}")
    print()
    for r in report.results:
        if r.skipped:
            flag = "SKIP"
        elif r.ok:
            flag = "PASS"
        else:
            flag = "FAIL"
        print(f"  {flag:4} [{r.kind}] {r.name}: {r.detail}")

    if args.json_out:
        out = {
            "browser_available": report.browser_available,
            "read_only": report.read_only,
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "results": [r.__dict__ for r in report.results],
        }
        Path(args.json_out).write_text(
            json.dumps(out, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json_out}")

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
