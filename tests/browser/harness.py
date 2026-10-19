"""Browser gate support for the independent packaged Image Catalog.

The executable is copied into private temporary data, never the user install.
The catalog-only gate registers local checks and treats browser launch failures
as hard errors.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from process_tree import ProcessTree

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXE = ROOT / "dist" / "NightSeam.exe"
ARTIFACTS = ROOT / "tests" / "browser" / "artifacts"
SCRATCH = ROOT / "logs" / "browser"

# Pinned wall clock: a real, non-degenerate late-summer night.  Passed as an
# ISO string on purpose - Playwright's numeric `clock.install(time=...)` is
# interpreted as SECONDS in the Python binding (a 1000x skew), the string is not.
PINNED_ISO = "2026-08-12T22:00:00Z"
TZ = "UTC"
VIEWPORT = (1366, 900)

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- registry

_TESTS: list[tuple[str, str, object]] = []


def check(name: str, target: str):
    """Register a browser check under `name` for the packaged catalog target."""
    if target != "catalog":
        raise ValueError(target)

    def deco(fn):
        _TESTS.append((name, target, fn))
        return fn

    return deco


def all_checks():
    return list(_TESTS)


# ---------------------------------------------------------------- helpers


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listener_closed(port: int, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket() as listener:
            listener.settimeout(0.2)
            if listener.connect_ex(("127.0.0.1", port)) != 0:
                return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"gate listener on port {port} survived teardown")
        time.sleep(0.05)


def _http(url: str, method: str = "GET", body: dict | None = None,
          timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


class DesktopServer:
    """The packed exe, isolated in a temp dir (never the user's data)."""

    def __init__(self, exe: Path = DEFAULT_EXE, native: bool = False,
                 debug_port: int | None = None, restricted_runtime: bool = False):
        self.exe = Path(exe)
        self.native = native
        self.debug_port = debug_port
        self.restricted_runtime = restricted_runtime
        self.tmp: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.tree: ProcessTree | None = None
        self.port = 0
        self.base = ""

    def start(self, timeout: float = 45.0) -> str:
        if not self.exe.is_file():
            raise RuntimeError(f"packed exe not found: {self.exe}")
        if self.tmp is not None:
            raise RuntimeError("gate server is already started")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="ns-browser-", dir=SCRATCH))
        try:
            (self.tmp / ".owner-pid").write_text(str(os.getpid()), encoding="utf-8")
            return self._start(timeout)
        except BaseException:
            self.stop()
            raise

    def _start(self, timeout: float) -> str:
        exe = self.tmp / "NightSeam.exe"
        shutil.copy2(self.exe, exe)
        self.port = free_port()
        args = [str(exe), *(["--require-native-window"] if self.native else ["--no-browser"]), "--port", str(self.port),
                "--idle-exit-hours", "0"]
        # PyInstaller's onefile bootloader extracts into TEMP. Keep extraction
        # inside owned scratch too, so killing the job cannot strand _MEI files
        # in the user's system temp directory.
        environment = dict(os.environ)
        for name in ("TEMP", "TMP", "TMPDIR"):
            environment[name] = str(self.tmp)
        # Native window placement and WebView2 profiles must also stay private.
        environment['LOCALAPPDATA'] = str(self.tmp / 'local-app-data')
        environment['WEBVIEW2_USER_DATA_FOLDER'] = str(self.tmp / 'webview2')
        if self.restricted_runtime:
            # Only the child app loses developer paths; the controller keeps
            # its own Python/Playwright. Installed OS runtimes remain present.
            empty_path = self.tmp / 'empty-path'
            empty_path.mkdir()
            environment['PATH'] = str(empty_path)
            for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE',
                         'VIRTUAL_ENV', 'CONDA_PREFIX', 'NODE_PATH'):
                environment.pop(name, None)
        if self.native and self.debug_port is not None:
            environment['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = (
                f'--remote-debugging-port={self.debug_port} --remote-debugging-address=127.0.0.1')
        self.tree = ProcessTree(
            args,
            cwd=str(self.tmp), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW,
            env=environment)
        self.proc = self.tree.proc
        self.base = f"http://127.0.0.1:{self.port}/"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"packed exe exited early (code {self.proc.returncode})")
            try:
                _http(self.base + "api/state", timeout=2.0)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("packed exe never answered /api/state")
        # A private executable/database does not isolate the default Documents
        # archive. Set its destination explicitly before any check can upload.
        private_archive = self.tmp / "archive"
        _http(self.base + "api/settings", "POST", {"image_dir": str(private_archive)})
        state = _http(self.base + "api/state")
        if Path(state["image_dir"]).resolve() != private_archive.resolve():
            raise RuntimeError("gate archive configuration did not select private scratch")
        return self.base

    def api(self, path: str, method: str = "GET", body: dict | None = None):
        return _http(self.base + path.lstrip("/"), method, body)

    def stop(self):
        if self.tree is not None:
            self.tree.stop()
            self.tree = None
            self.proc = None
        if self.port:
            _wait_listener_closed(self.port)
            self.port = 0
        if self.tmp is not None:
            # Windows may briefly hold executable files after process exit.
            # A persistent refusal is an infrastructure failure, never hidden.
            for attempt in range(4):
                try:
                    shutil.rmtree(self.tmp)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    if attempt == 3:
                        raise
                    time.sleep(0.2 * (attempt + 1))
            self.tmp = None


# ---------------------------------------------------------------- context

class Ctx:
    """Per-check browser context + the assertions a check may make."""

    def __init__(self, page, base: str, target: str, artifacts: Path):
        self.page = page
        self.base = base
        self.target = target
        self.artifacts = artifacts
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.bad_responses: list[str] = []
        self._allow_console = False
        self._allow_responses = False

    # same-origin URL used to decide which responses belong to the app under
    # test (external CDNs/images are not the gate's business).
    @property
    def origin(self) -> str:
        return "/".join(self.base.split("/")[:3])

    def goto(self, path: str = "/"):
        self.page.goto(self.base.rstrip("/") + path,
                       wait_until="domcontentloaded")

    def url(self, path: str = "/") -> str:
        return self.base.rstrip("/") + path

    def expect(self, cond, msg: str):
        if not cond:
            raise AssertionError(msg)

    def expect_eq(self, got, want, msg: str = ""):
        if got != want:
            raise AssertionError(f"{msg}: got {got!r}, want {want!r}")

    def expect_in(self, needle, haystack, msg: str = ""):
        if needle not in (haystack or ""):
            raise AssertionError(
                f"{msg}: {needle!r} not in {(haystack or '')[:300]!r}")

    def allow_console_errors(self, reason: str):
        self._allow_console = True

    def allow_failed_responses(self, reason: str):
        self._allow_responses = True

    def assert_clean(self):
        """Every check that does not deliberately break something ends here:
        no console errors, no uncaught exceptions, no same-origin 4xx/5xx - and
        no request to a third party (the external-host tripwire)."""
        attempts = getattr(self.page, "_ns_external_attempts", None) or []
        if attempts:
            raise AssertionError(
                "NightSeam must not contact a third party at "
                "boot; the popularity snapshot ships in the catalog. Attempted: "
                + "; ".join(attempts[:3]))
        if self.page_errors and not self._allow_console:
            raise AssertionError("uncaught page errors: " + "; ".join(self.page_errors[:5]))
        if self.console_errors and not self._allow_console:
            raise AssertionError("console errors: " + "; ".join(self.console_errors[:5]))
        if self.bad_responses and not self._allow_responses:
            raise AssertionError("failed same-origin responses: "
                                 + "; ".join(self.bad_responses[:5]))

    # -- waiting helpers

    def wait_hidden(self, selector: str, timeout: float = 45000.0):
        self.page.wait_for_function(
            """(sel) => { const e = document.querySelector(sel);
                 return !e || e.classList.contains('hidden')
                        || getComputedStyle(e).display === 'none'
                        || getComputedStyle(e).visibility === 'hidden'; }""",
            arg=selector, timeout=timeout)

    def wait_visible(self, selector: str, timeout: float = 45000.0):
        self.page.wait_for_selector(selector, state="visible", timeout=timeout)


# ---------------------------------------------------------------- runner

@dataclass
class Result:
    name: str
    target: str
    ok: bool
    error: str = ""
    duration: float = 0.0


LOCAL_HOSTS = {None, "127.0.0.1", "localhost", "::1"}


def _install_stubs(page):
    """The gate is offline + deterministic, and NightSeam must not contact a
    third party at boot: the popularity snapshot ships in the catalog data.
    Every external request is therefore a TRIPWIRE, not a stub - it is aborted
    and recorded, and `Ctx.assert_clean()` fails the check that made it.
    `tests/test_no_popularity_fetch.py` pins the same rule statically."""
    attempts: list[str] = []
    page._ns_external_attempts = attempts

    def handler(route):
        try:
            attempts.append(route.request.url)
        except Exception:
            pass
        try:
            route.abort()
        except Exception:
            pass

    page.route(lambda url: urlsplit(url).hostname not in LOCAL_HOSTS, handler)


def _new_context(browser, dpr: int = 1):
    ctx = browser.new_context(
        timezone_id=TZ, locale="en-US",
        viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]},
        color_scheme="dark", device_scale_factor=dpr,
        reduced_motion="reduce",
    )
    page = ctx.new_page()
    try:
        page.clock.install(time=PINNED_ISO)
        page.clock.resume()
    except Exception:
        pass  # clock API unavailable: tests set dates explicitly anyway
    _install_stubs(page)
    return ctx, page


def _attach(ctx: Ctx):
    def on_console(msg):
        if msg.type == "error":
            ctx.console_errors.append(msg.text[:300])
    def on_pageerror(err):
        # Include a short stack so a page error reports its actual caller
        # instead of only the often-ambiguous property access message.
        ctx.page_errors.append(str(getattr(err, "stack", None) or err)[:900])
    def on_response(resp):
        try:
            if resp.status >= 400 and resp.url.startswith(ctx.origin):
                if resp.url.endswith("favicon.ico"):
                    return
                ctx.bad_responses.append(f"{resp.status} {resp.url}")
        except Exception:
            pass
    ctx.page.on("console", on_console)
    ctx.page.on("pageerror", on_pageerror)
    ctx.page.on("response", on_response)


def run(channel: str = "chrome", targets=("catalog",),
        exe: Path = DEFAULT_EXE, only: str | None = None,
        headed: bool = False, artifacts: Path = ARTIFACTS, verbose: bool = True) -> int:
    """Run the registered checks. Returns 0 (pass), 1 (failures) or 2 (the
    infrastructure could not start - browser, server or packed exe)."""
    from playwright.sync_api import sync_playwright

    selected = [(n, t, f) for (n, t, f) in all_checks()
                if t in targets and (not only or only in n)]
    if not selected:
        print("check_browser: no checks selected")
        return 0

    artifacts.mkdir(parents=True, exist_ok=True)
    catalog_srv = None
    bases: dict[str, str] = {}
    try:
        if "catalog" in targets:
            catalog_srv = DesktopServer(exe)
            bases["catalog"] = catalog_srv.start()
    except Exception as exc:
        print(f"check_browser FATAL: could not start a target: {exc}")
        if catalog_srv:
            catalog_srv.stop()
        return 2

    results: list[Result] = []
    # TO-02: entering the Playwright DRIVER (the `with` below) can fail before any
    # browser exists. That is infrastructure, and `check_browser.run_gate()` maps it
    # to the documented exit 2. It must NOT be probed here with a throw-away
    # `sync_playwright().start()`: the first instance's teardown leaves a pending
    # asyncio task, and the gate then exited 1 even when every check passed
    # (observed on the real gate: 12/12 passed, exit 1).
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel=channel, headless=not headed)
            except Exception as exc:
                print(f"check_browser FATAL: browser '{channel}' could not "
                      f"launch ({exc}). Install it or pass --channel.")
                return 2
            for name, target, fn in selected:
                ctx_b, page = _new_context(browser)
                c = Ctx(page, bases[target], target, artifacts)
                _attach(c)
                t0 = time.time()
                ok, err = True, ""
                try:
                    fn(c)
                except Exception as exc:
                    ok, err = False, f"{type(exc).__name__}: {exc}"
                try:
                    c.assert_clean()
                except Exception as exc:
                    ok = False
                    err = (err + " | " if err else "") + str(exc)
                if not ok:
                    _dump_failure(c, name, err)
                try:
                    ctx_b.close()
                except Exception:
                    pass
                dt = time.time() - t0
                results.append(Result(name, target, ok, err, dt))
                if verbose:
                    mark = "PASS" if ok else "FAIL"
                    print(f"  [{mark}] {target:7s} {name} ({dt:.1f}s)")
                    if not ok:
                        print(f"         {err[:400]}")
            browser.close()
    finally:
        if catalog_srv:
            catalog_srv.stop()

    failed = [r for r in results if not r.ok]
    print(f"check_browser: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def _dump_failure(c: Ctx, name: str, err: str):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    try:
        c.page.screenshot(path=str(c.artifacts / f"{safe}.png"), full_page=True)
    except Exception:
        pass
    try:
        (c.artifacts / f"{safe}.json").write_text(json.dumps({
            "name": name, "target": c.target, "url": c.page.url,
            "error": err, "console_errors": c.console_errors,
            "page_errors": c.page_errors, "bad_responses": c.bad_responses,
            "dom": (c.page.content() or "")[:4000],
        }, indent=1), encoding="utf-8")
    except Exception:
        pass
