"""Browser gate support for the independent packaged Image Catalog.

The executable is copied into private temporary data, never the user install.
The catalog-only gate registers local checks and treats browser launch failures
as hard errors. Retained legacy helper APIs are not registered by the runner.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from process_tree import ProcessTree

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
DEFAULT_EXE = ROOT / "dist" / "NightSeam.exe"
ARTIFACTS = ROOT / "tests" / "browser" / "artifacts"
SCRATCH = ROOT / "logs" / "browser"

# Pinned wall clock: a real, non-degenerate late-summer night.  Passed as an
# ISO string on purpose - Playwright's numeric `clock.install(time=...)` is
# interpreted as SECONDS in the Python binding (a 1000x skew), the string is not.
PINNED_ISO = "2026-08-12T22:00:00Z"
PINNED_DATE = "2026-08-12"
TZ = "UTC"
VIEWPORT = (1366, 900)

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- registry

_TESTS: list[tuple[str, str, object]] = []


def check(name: str, target: str):
    """Register a browser check under `name` for `target` ("web"/"desktop")."""
    if target not in ("web", "desktop", "catalog"):
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


class StaticServer:
    """Threading HTTP server over a directory (no cache-busting rewritten)."""

    def __init__(self, directory: Path):
        handler = partial(_QuietHandler, directory=str(directory))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        return f"http://127.0.0.1:{self.port}/"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the gate output readable
        pass


class DesktopServer:
    """The packed exe, isolated in a temp dir (never the user's data)."""

    def __init__(self, exe: Path = DEFAULT_EXE, seed: bool = True,
                 planner: bool = False):
        self.exe = Path(exe)
        self.seed = seed
        self.planner = planner
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
        args = [str(exe), "--no-browser", "--port", str(self.port),
                "--idle-exit-hours", "0"]
        if self.planner:
            args.append("--planner")
        # PyInstaller's onefile bootloader extracts into TEMP. Keep extraction
        # inside owned scratch too, so killing the job cannot strand _MEI files
        # in the user's system temp directory.
        environment = dict(os.environ)
        for name in ("TEMP", "TMP", "TMPDIR"):
            environment[name] = str(self.tmp)
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
        if self.seed:
            _http(self.base + "api/sites", "POST",
                  {"name": "Gate Site", "lat": 52.52, "lon": 13.4, "elev": 34})
            _http(self.base + "api/settings", "POST", {"wizard_done": True})
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
        self.external_images: list[tuple[int, str]] = []
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

    def wait_app(self, ready_selector: str, timeout: float = 60000.0):
        self.page.wait_for_selector("#view", timeout=timeout)
        self.page.wait_for_selector(ready_selector, timeout=timeout)
        try:
            self.wait_hidden("#busy", timeout=20000)
        except Exception:
            pass

    def wait_tonight_render(self, timeout: float = 15000.0) -> dict:
        """Wait for the Tonight view to have PAINTED the current selection.

        BUG-005: the check used to scrub the slider and sleep 120 ms, but the
        application debounces that scrub by 120 ms and then defers the ranking by
        another tick - so the check raced the app's own debounce (it failed 25/26
        full-sequence runs while passing in isolation, with stale/partial rows in
        the artifact). `web/app.js::stampTonightRender` publishes
        `#view[data-ns-gen]`/`[data-ns-sel]` after the DOM swap; this returns them
        once the view carries a painted selection."""
        self.page.wait_for_function(
            """() => { const v = document.querySelector('#view');
                 if (!v || !v.dataset || !v.dataset.nsGen) return false;
                 const sel = Number(v.dataset.nsSel);
                 return Number.isFinite(sel) && sel > 0; }""",
            timeout=timeout)
        return self.page.evaluate(
            """() => { const v = document.querySelector('#view');
                 return { gen: Number(v.dataset.nsGen), sel: Number(v.dataset.nsSel) }; }""")

    def tonight_gen(self) -> int:
        """The current Tonight render generation (0 = never painted)."""
        return int(self.page.evaluate(
            """() => { const v = document.querySelector('#view');
                 return v && v.dataset ? Number(v.dataset.nsGen || 0) : 0; }"""))

    def wait_tonight_after(self, gen: int, sel: int | None = None,
                           timeout: float = 15000.0) -> dict:
        """Wait for a Tonight paint that shows the REQUESTED selection.

        BUG-005's implementation hint is explicit: poll for the requested scrub
        position, not for "a paint happened". A generation bump alone is not
        enough - the boot sequence can leave a stale marker whose value is an
        arbitrary timestamp, and a check that accepts any newer generation
        samples whatever happened to be on screen (that is the race, one level
        up). `sel` is the instant the app itself would select for the scrub
        position (`window.__ns_scrub_at`), so both sides compare the same
        quantity; omit it to accept any newer paint."""
        if sel is None:
            self.page.wait_for_function(
                """(g) => { const v = document.querySelector('#view');
                     if (!v || !v.dataset) return false;
                     const s = Number(v.dataset.nsSel);
                     return Number(v.dataset.nsGen || 0) > g
                            && Number.isFinite(s) && s > 0; }""",
                arg=int(gen), timeout=timeout)
        else:
            self.page.wait_for_function(
                """(a) => { const v = document.querySelector('#view');
                     if (!v || !v.dataset) return false;
                     const s = Number(v.dataset.nsSel);
                     return Number(v.dataset.nsGen || 0) > a.g
                            && Number.isFinite(s) && s === a.sel; }""",
                arg={"g": int(gen), "sel": int(sel)}, timeout=timeout)
        return self.page.evaluate(
            """() => { const v = document.querySelector('#view');
                 return { gen: Number(v.dataset.nsGen), sel: Number(v.dataset.nsSel) }; }""")


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
    third party at boot: the popularity snapshot is BAKED into the
    catalog payload (`web/app.js::popularityOf` reads `o.popularity`), and the
    web app has no live fetch left. Every external request is therefore a
    TRIPWIRE, not a stub - it is aborted and recorded, and `Ctx.assert_clean()`
    fails the check that made it, on both shells. `tests/test_no_popularity_
    fetch.py` pins the same rule statically."""
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
            # the allowlisted third-party image hosts: recorded so the production
            # smoke can prove the shipped CSP does not block them
            if "upload.wikimedia.org" in resp.url or "commons.wikimedia.org" in resp.url:
                ctx.external_images.append((resp.status, resp.url))
        except Exception:
            pass
    ctx.page.on("console", on_console)
    ctx.page.on("pageerror", on_pageerror)
    ctx.page.on("response", on_response)


def run(channel: str = "chrome", targets=("catalog",),
        exe: Path = DEFAULT_EXE, only: str | None = None,
        headed: bool = False, prod_url: str | None = None,
        artifacts: Path = ARTIFACTS, verbose: bool = True) -> int:
    """Run the registered checks. Returns 0 (pass), 1 (failures) or 2 (the
    infrastructure could not start - browser, server or packed exe)."""
    from playwright.sync_api import sync_playwright

    selected = [(n, t, f) for (n, t, f) in all_checks()
                if t in targets and (not only or only in n)]
    if not selected and not prod_url:
        print("check_browser: no checks selected")
        return 0

    artifacts.mkdir(parents=True, exist_ok=True)
    web_srv = desktop_srv = catalog_srv = None
    bases: dict[str, str] = {}
    try:
        if "web" in targets:
            web_srv = StaticServer(WEB_DIR)
            bases["web"] = web_srv.start()
        if "desktop" in targets:
            # The release suite continues to exercise the full desktop source
            # shell. Its standard launch is separately checked under `catalog`.
            desktop_srv = DesktopServer(exe, planner=True)
            bases["desktop"] = desktop_srv.start()
        if "catalog" in targets:
            catalog_srv = DesktopServer(exe, seed=False)
            bases["catalog"] = catalog_srv.start()
    except Exception as exc:
        print(f"check_browser FATAL: could not start a target: {exc}")
        if web_srv:
            web_srv.stop()
        if desktop_srv:
            desktop_srv.stop()
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
            if prod_url:
                results.append(_prod_smoke(browser, prod_url, artifacts, verbose))
            browser.close()
    finally:
        if web_srv:
            web_srv.stop()
        if desktop_srv:
            desktop_srv.stop()
        if catalog_srv:
            catalog_srv.stop()

    failed = [r for r in results if not r.ok]
    print(f"check_browser: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def _inline_script_hashes() -> list[str]:
    """`sha256-...` for every inline `<script>` in web/index.html - the hashes a
    correct CSP must carry."""
    import base64
    import hashlib
    out = []
    try:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    except OSError:
        return out
    for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                           html, re.S | re.I):
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        out.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return out


# Directives a browser IGNORES when the policy arrives in a `<meta>` tag
# (CSP3 §"The meta element": frame-ancestors, report-uri, sandbox are only
# meaningful as response headers). Naming them in a meta policy is a silent
# no-op - exactly the false-assurance shape BUG-003 was - so the check reports
# it instead of counting the directive as present.
_META_IGNORED = ("frame-ancestors", "report-uri", "report-to", "sandbox")


def _csp_problems(policy: str | None, document: bool = False,
                  from_meta: bool = False) -> list[str]:
    """Why a deployed Content-Security-Policy is unusable ([] = fine).

    The web shell is the PUBLIC origin and loads third-party images while
    holding the user's sites/rigs/targets/panoramas in IndexedDB and
    localStorage, so a missing or over-permissive policy is a real finding -
    not a nicety. The check is read-only and only runs for `--prod-url`.

    BUG-003: `document=True` is for the response that carries the PAGE. A CSP
    protects only the response it is attached to, so a policy seen on
    `version.json` says nothing about the HTML a browser runs. The document check
    therefore drops the inline-bootstrap-hash requirement (the static desktop
    entry point has no such script) but keeps every policy rule.

    `from_meta=True` is the BUG-003 WORKAROUND path: the deployed host answers
    `/astro` from nginx, so no header can be applied there and the policy ships
    inside `web/index.html` instead (measured 2026-09-17: `server: nginx`,
    `content-type: text/html`, no policy header)."""
    if not policy:
        return ["no Content-Security-Policy is served (neither a response header "
                "nor a meta policy in the document; web/.htaccess carries the "
                "header form and web/index.html the meta form)"]
    low = policy.lower()
    bad = []
    if from_meta:
        for directive in _META_IGNORED:
            if directive in low:
                bad.append(
                    f"{directive} is IGNORED in a meta policy - it only works as "
                    f"a response header, so the document is not protected by it")
    if "script-src" not in low:
        bad.append("no script-src directive (default-src alone is weaker than "
                   "the desktop shell, which pins script-src)")
    elif "unsafe-inline" in low.split("script-src", 1)[1].split(";", 1)[0]:
        bad.append("script-src allows 'unsafe-inline' (the one-theme shell has "
                   "no inline script)")
    elif "unsafe-eval" in low:
        bad.append("script-src allows 'unsafe-eval'")
    if not document:
        for wanted in _inline_script_hashes():
            if wanted.lower() not in low:
                bad.append(f"the inline theme bootstrap hash {wanted} is missing "
                           f"- the saved theme would not be applied before paint")
    if "connect-src" not in low:
        bad.append("no connect-src (the Wikipedia photo fetch and version.json "
                   "are unrestricted)")
    return bad


def _prod_smoke(browser, prod_url: str, artifacts: Path, verbose: bool) -> Result:
    """Read-only production smoke (/astro): the page loads, shows a version,
    serves a usable CSP, has no console errors - and its own policy does not
    break the third-party photos it allows. Never mutates anything."""
    ctx_b, page = _new_context(browser)
    c = Ctx(page, prod_url, "web-prod", artifacts)
    _attach(c)
    t0 = time.time()
    ok, err = True, ""
    try:
        resp = page.goto(prod_url, wait_until="domcontentloaded")
        c.expect(resp is not None, "the production page did not respond")
        # BUG-003: the policy must cover the DOCUMENT, and nothing else counts.
        # This used to fall back to probing version.json / catalog.manifest.json,
        # which nginx DOES proxy to Apache - so a deployment whose HTML carried no
        # policy at all still "passed" while every browser ran the page
        # unprotected. A CSP protects only the response that carries it.
        #
        # Two legitimate carriers, both checked here:
        #   1. a response header on the page (the stronger form);
        #   2. a `<meta http-equiv="Content-Security-Policy">` in the document -
        #      the BUG-003 WORKAROUND, because the deployed host answers /astro
        #      from nginx and exposes no directive field, so no header can be
        #      applied to the HTML (measured 2026-09-17). The meta form cannot
        #      carry frame-ancestors/report-uri/sandbox, and _csp_problems
        #      reports those as findings rather than counting them.
        policy = resp.headers.get("content-security-policy")
        from_meta = False
        if not policy:
            policy = page.evaluate(
                """() => { const m = document.querySelector(
                       'meta[http-equiv="Content-Security-Policy" i]');
                     return m ? (m.getAttribute('content') || '') : ''; }""") or None
            from_meta = bool(policy)
        problems = _csp_problems(policy, document=True, from_meta=from_meta)
        carrier = "meta policy" if from_meta else "response header"
        c.expect(not problems,
                 f"production DOCUMENT CSP ({carrier}): " + "; ".join(problems))
        page.wait_for_selector("#foot-ver", timeout=45000)
        # First-run welcome (throwaway profile, empty storage): "Skip" keeps the
        # default site so boot proceeds.  This writes only to the ephemeral
        # browser profile - the server and any real user data are untouched.
        try:
            page.wait_for_selector("#w-skip", state="visible", timeout=10000)
            page.click("#w-skip")
        except Exception:
            pass
        page.wait_for_function(
            """() => { const v = document.querySelector('#foot-ver');
                 const t = (v && v.textContent || '').trim();
                 return t && t !== '\\u2026'; }""", timeout=45000)
        shown = (page.evaluate(
            """() => (document.querySelector('#foot-ver')?.textContent || '').trim()""") or "")
        # The policy must not block what the app legitimately loads: the whole
        # point of the img-src allowlist. `web-prod` used to verify the CSP text
        # only, which cannot tell a correct policy from one that breaks photos.
        failed = [f"{st} {url}" for st, url in c.external_images if st >= 400]
        c.expect(not failed,
                 "an allowlisted third-party image failed under the policy: "
                 + "; ".join(failed[:3]))
        deployed = page.evaluate(
            """async () => { const r = await fetch('version.json'); return r.ok
                 ? (await r.json()).version : null; }""")
        c.expect(deployed, "the deployed version.json must be readable")
        c.expect(deployed in shown,
                 f"page shows {shown!r} but version.json says {deployed!r}")
        # A policy can be present while the page stylesheet fails to load. Check
        # the fixed Workbench document identity and a real menu control's layout.
        style_ok = page.evaluate(
            """() => {
                 const rng = document.querySelector('#edgeSpace');
                 return { theme: document.documentElement.dataset.theme,
                          rangeWidth: rng ? getComputedStyle(rng).width : null };
               }""")
        c.expect(style_ok.get("theme") == "workbench" and style_ok.get("rangeWidth"),
                 f"the Workbench stylesheet or menu controls are unavailable "
                 f"under the document policy ({style_ok})")
        c.assert_clean()
    except Exception as exc:
        ok, err = False, f"{type(exc).__name__}: {exc}"
        _dump_failure(c, "web-prod-smoke", err)
    finally:
        try:
            ctx_b.close()
        except Exception:
            pass
    dt = time.time() - t0
    if verbose:
        print(f"  [{'PASS' if ok else 'FAIL'}] web-prod production smoke ({dt:.1f}s)")
        if not ok:
            print(f"         {err[:400]}")
    return Result("production-smoke", "web-prod", ok, err, dt)


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
