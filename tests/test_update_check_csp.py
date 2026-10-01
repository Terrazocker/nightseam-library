"""SP-02 (0.6.32 review): the desktop update check used to load
`https://nightseam.de/downloads/ns-version.js` as a JSONP `<script>`, so remote
JavaScript executed in the privileged `127.0.0.1:8407` origin where /api/export
and /api/state are unauthenticated same-origin routes. Payload validation cannot
fix that - the script itself runs.

Pins: the version document is fetched and validated in Python (POST
/api/update-check), every failure is a silent "no update", the page loads no
remote code, and the HTML the server serves carries a CSP with `script-src
'self'`. The network is STUBBED for every test here - nightseam.de is never
contacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.server import create_app

ROOT = Path(__file__).resolve().parents[1]

_VERSION_DOC = b'{"version": "9.9.9", "describe": "v9.9.9", "built": "x"}'


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _n: int | None = None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def live_server(monkeypatch):
    """A stubbed updater: any attempt to open a real connection fails loudly."""
    def _boom(*a, **k):
        raise AssertionError("a test must never open a real network connection")

    monkeypatch.setattr("app.server.urllib.request.urlopen", _boom)
    return monkeypatch


def _client(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    return TestClient(create_app(db, prefetch=False))


def test_update_check_reports_a_newer_version(tmp_path, live_server):
    live_server.setattr("app.server.UPDATE_VERSION_URL",
                        "https://updates.invalid/version.json")
    live_server.setattr("app.server.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResponse(_VERSION_DOC))
    with _client(tmp_path) as c:
        r = c.post("/api/update-check")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["update"] is True
    assert body["version"] == "9.9.9"


def test_update_check_is_silent_when_the_host_is_down(tmp_path, live_server):
    def _down(req, timeout=None):
        raise OSError("no route to host")

    live_server.setattr("app.server.urllib.request.urlopen", _down)
    with _client(tmp_path) as c:
        r = c.post("/api/update-check")
    assert r.status_code == 200            # never fatal, never blocking
    body = r.json()
    assert body["update"] is False and body["version"] is None and body["ok"] is False


@pytest.mark.parametrize("doc", [
    b"not json at all",
    b"[1,2,3]",
    b'{"version": "9.9.9; <script>alert(1)</script>"}',
    b'{"version": ""}',
    b'{"describe": null}',
    b'{"version": "' + b"9" * 200 + b'"}',
])
def test_update_check_rejects_a_hostile_or_malformed_document(
        tmp_path, live_server, doc):
    live_server.setattr("app.server.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResponse(doc))
    with _client(tmp_path) as c:
        body = c.post("/api/update-check").json()
    assert body["ok"] is False
    assert body["update"] is False and body["version"] is None
    assert body["current"]


def test_update_check_never_uses_plain_http(tmp_path, live_server):
    live_server.setattr("app.server.UPDATE_VERSION_URL",
                        "http://updates.invalid/version.json")
    with _client(tmp_path) as c:
        body = c.post("/api/update-check").json()
    assert body["version"] is None      # the stub would have asserted otherwise


def test_update_check_rejects_an_oversized_body(tmp_path, live_server):
    live_server.setattr("app.server.urllib.request.urlopen",
                        lambda req, timeout=None:
                        _FakeResponse(b"9" * (70 * 1024)))
    with _client(tmp_path) as c:
        assert c.post("/api/update-check").json()["version"] is None


def test_version_compare_is_numeric():
    from app.server import _version_greater
    assert _version_greater("0.6.32", "0.6.9") is True    # not a string compare
    assert _version_greater("0.6.9", "0.6.32") is False
    assert _version_greater("1.0", "0.6.32") is True
    assert _version_greater("0.6.32", "0.6.32") is False
    assert _version_greater("0.6.32", "0.6.33") is False
    assert _version_greater("0.6.32-3-gabc", "0.6.31") is True


def test_the_privileged_html_carries_a_csp_that_forbids_remote_script(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200
        csp = r.headers.get("content-security-policy")
        assert csp, "the HTML the server serves must carry a CSP (SP-02)"
        assert "script-src 'self'" in csp
        assert "https://nightseam.de" not in csp
        assert "img-src" in csp and "connect-src" in csp
        # the shell is HTML; the JS/CSS assets are not, so no CSP is needed there
        assert "content-security-policy" not in c.get("/js/main.js").headers
        assert "content-security-policy" not in c.get("/style.css").headers


def test_the_desktop_page_loads_no_remote_script(tmp_path):
    """SP-02 at the source level: the JSONP path is gone from updater.js, which
    is the only JS file this change may touch."""
    src = (ROOT / "static" / "js" / "updater.js").read_text(encoding="utf-8")
    # the leading block comment explains what was removed, so check the CODE:
    # a code line is one that is not inside that comment
    body = src.split("*/", 1)[1]
    assert "createElement" not in body
    assert "nightseam.de/downloads" not in body
    assert "ns-version" not in body
    assert "__NS_RELEASE" not in body
    assert "/api/update-check" in body


def test_the_desktop_shell_has_no_inline_scripts():
    """The fixed Workbench page has no inline theme bootstrap and stays CSP
    restricted to same-origin scripts."""
    import re

    from app.server import CSP_HEADER

    script_src = CSP_HEADER.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src, script_src
    assert "unsafe-eval" not in script_src
    assert "script-src 'self'" in CSP_HEADER
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    bodies = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html,
                        re.S | re.I)
    assert bodies == []
    # the inline handlers this exemption used to permit are gone from the shell.
    # The pattern requires the quote, so the explanatory comments that NAME the
    # removed attribute are not findings.
    for name in ("targets.js", "tonight.js"):
        src = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", src), (
            f"{name} carries an inline event handler again - it would need "
            f"'unsafe-inline' in script-src")
