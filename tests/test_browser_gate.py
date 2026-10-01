"""The browser release gate is wired, not decorative.

The real checks run under `scripts/check_browser.py` (Playwright, outside
pytest because they need a browser and a server).  This pins the WIRING and the
coverage surface so a refactor cannot silently drop a check class: the runner
imports both suites, the suites register the expected names, and AGENTS.md
documents the command.  Behavioural assertions live in tests/browser/*.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

WEB_CHECKS = [
    "web-load-smoke",
    "web-cache-tokens",
    "web-horizon-actions-not-full-width",
    "web-pano-paint-save-persist",
    "web-pano-undo-redo-clear",
    "web-pano-storage-quota",
    "web-pano-canvas-dpr-resize",
    "web-horizon-stage-preserves-photo-aspect",
    "web-horizon-profile-region",
    "web-sky-band-no-diagonal-chord",
    "web-sun-moon-above-horizon",
    "web-scrub-changes-time",
    "web-mag-slider-changes-sky",
    "web-pano-crop-recalibrate",
    "web-catalog-direction-b-density",
    "web-workbench-only-render",
    "web-accessibility-essentials",
]
DESKTOP_CHECKS = [
    "desktop-boot",
    "desktop-worker-date-change",
    "desktop-worker-kill-sync-fallback",
    "desktop-pref-rescore",
    "desktop-advanced-mode",
    "desktop-catalog-direction-b-density",
    "desktop-scrub-window",
    "desktop-pano-crop-fov",
    "desktop-workbench-only-render",
    "desktop-accessibility-essentials",
]


def test_browser_runner_imports_both_suites():
    src = (ROOT / "scripts" / "check_browser.py").read_text(encoding="utf-8")
    assert "import web_suite" in src and "import desktop_suite" in src
    assert "--web" in src and "--desktop" in src and "--prod-url" in src


def test_browser_suites_register_the_review_classes():
    web = (ROOT / "tests" / "browser" / "web_suite.py").read_text(encoding="utf-8")
    desk = (ROOT / "tests" / "browser" / "desktop_suite.py").read_text(encoding="utf-8")
    for name in WEB_CHECKS:
        assert f'"{name}"' in web, f"web check missing: {name}"
    for name in DESKTOP_CHECKS:
        assert f'"{name}"' in desk, f"desktop check missing: {name}"


def test_browser_gate_is_browser_required_not_a_skip():
    harness = (ROOT / "tests" / "browser" / "harness.py").read_text(encoding="utf-8")
    # a launch failure must be a hard failure (exit 2), never a skip
    assert "check_browser FATAL: browser" in harness
    assert "return 2" in harness
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "scripts/check_browser.py --all" in agents
    assert "HARD failure" in agents


def test_browser_gate_driver_start_failure_is_exit_2(monkeypatch, capsys):
    """TO-02: entering the Playwright DRIVER is infrastructure too.

    `sync_playwright()` is entered outside the handler that converts start-up
    failures into exit 2, so a driver that cannot spawn (restricted token, broken
    install, missing driver bundle) escaped as a traceback and the process reported
    exit 1 - which the documented contract defines as "failed check(s)", the signal
    a release wrapper keys on. The conversion lives in the owner of that contract,
    `check_browser.run_gate()`."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "gate_runner", ROOT / "scripts" / "check_browser.py")
    gate = importlib.util.module_from_spec(spec)
    sys.modules["gate_runner"] = gate          # annotations resolve through this
    spec.loader.exec_module(gate)              # imports the suites (self-register)

    def boom(**kwargs):
        raise PermissionError("[WinError 5] Zugriff verweigert")

    monkeypatch.setattr(gate.harness, "run", boom)
    rc = gate.run_gate(targets=("web",), verbose=False)
    out = capsys.readouterr().out
    assert rc == 2, "a driver that cannot start is infrastructure (exit 2)"
    assert "check_browser FATAL" in out and "infrastructure" in out


def test_browser_harness_starts_the_driver_exactly_once():
    """The TO-02 conversion must NOT be implemented by probing the driver.

    A throw-away `sync_playwright().start()` leaves the first instance's asyncio
    task pending, and the real gate then exited 1 after passing every check
    (observed: 12/12 passed, exit 1). One start, in the `with` that uses it."""
    src = (ROOT / "tests" / "browser" / "harness.py").read_text(encoding="utf-8")
    assert src.count("with sync_playwright() as p:") == 1, (
        "harness.py must start the Playwright driver exactly once")
    assert "_driver = sync_playwright().start()" not in src, (
        "the throw-away driver probe is back - it left the gate exiting 1 "
        "after every check passed")


def _desktop_suite():
    """tests/browser/desktop_suite.py (its `from harness import check` needs the
    directory on sys.path, exactly as scripts/check_browser.py arranges)."""
    import sys
    browser_dir = str(ROOT / "tests" / "browser")
    sys.path.insert(0, browser_dir)
    try:
        import desktop_suite
        return desktop_suite
    finally:
        sys.path.remove(browser_dir)


class _FakePage:
    """A page that times out the first `fails` upload waits, then succeeds."""

    def __init__(self, fails: int):
        self.fails = fails
        self.uploads = 0
        self.waits = 0

    def set_input_files(self, selector, path):
        self.uploads += 1

    def wait_for_function(self, script, arg=None, timeout=None):
        self.waits += 1
        if self.waits <= self.fails:
            raise TimeoutError("cold start")

    def evaluate(self, script):
        return "pano-1"


class _FakeCtx:
    def __init__(self, fails: int):
        self.page = _FakePage(fails)


def test_pano_crop_upload_wait_survives_a_cold_first_round_trip():
    """TR-12 (0.6.32 review): the crop check's upload wait timed out on a COLD
    first launch (measured 91.9 s against a 90 s budget, then 3.0 s on an
    immediate re-run), so the release gate could go red for a machine rather than
    for a regression. The wait must stay above the measured cold start AND retry
    a timed-out attempt; the retry is safe because the wait asks for a DIFFERENT
    panorama id, so a second upload is simply another id."""
    suite = _desktop_suite()
    timeout, attempts = suite.upload_pano.__defaults__
    assert timeout >= 150000, (
        f"the upload wait is back under the measured cold start: {timeout} ms")
    assert attempts >= 2, "a timed-out upload wait must be retried once"

    c = _FakeCtx(fails=1)                       # one cold timeout, then it lands
    assert suite.upload_pano(c, "x.png", None) == "pano-1"
    assert c.page.uploads == 2, "the timed-out upload must be retried"

    # a wait that keeps timing out must FAIL (never pass quietly)
    c = _FakeCtx(fails=2)
    try:
        suite.upload_pano(c, "x.png", None)
    except AssertionError as exc:
        assert "did not land" in str(exc)
    else:
        pytest.fail("a persistently timing-out upload must raise")
