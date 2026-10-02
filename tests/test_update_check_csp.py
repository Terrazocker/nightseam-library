"""Independent versions must never consult the combined website updater.

Same-origin script restrictions and inline-handler bans remain in both shells.
"""
from pathlib import Path
import re
from unittest.mock import Mock
import urllib.request

import pytest
from fastapi.testclient import TestClient
from app.database import Database
from app.server import create_app, CSP_HEADER

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("catalog_only", [True, False])
def test_no_shell_can_query_combined_release_metadata(tmp_path, monkeypatch, catalog_only):
    network = Mock(side_effect=AssertionError("unexpected update network request"))
    monkeypatch.setattr(urllib.request, "urlopen", network)
    database = Database(tmp_path / "library.db")
    try:
        with TestClient(create_app(database, prefetch=False, catalog_only=catalog_only)) as client:
            response = client.post("/api/update-check")
            assert response.status_code == 404
            assert "catalog release source" in response.json()["detail"]
            page = client.get("/")
            assert page.status_code == 200
            assert "script-src 'self'" in page.headers["content-security-policy"]
        network.assert_not_called()
    finally:
        database.conn.close()

def test_startup_does_not_initialize_an_updater():
    main = (ROOT / "static/js/main.js").read_text(encoding="utf-8")
    catalog = (ROOT / "static/js/catalog_main.js").read_text(encoding="utf-8")
    for subject in (main, catalog):
        assert "./updater.js" not in subject
        assert "checkForUpdates(" not in subject
    body = (ROOT / "static/js/updater.js").read_text(encoding="utf-8")
    assert "document.createElement" not in body
    assert "window.open" not in body
    assert "api(" not in body and "fetch(" not in body

@pytest.mark.parametrize("shell", ["index.html", "catalog.html"])
def test_shells_allow_only_same_origin_scripts(shell):
    script_src = CSP_HEADER.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src
    assert "script-src 'self'" in CSP_HEADER
    html = (ROOT / "static" / shell).read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S | re.I) == []
    assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html)

def test_rendered_planner_rows_have_no_inline_handlers():
    for name in ("targets.js", "tonight.js"):
        src = (ROOT / "static/js" / name).read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", src)
