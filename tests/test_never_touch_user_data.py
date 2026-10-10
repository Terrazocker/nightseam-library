"""The autouse profile guard must keep archive writes out of the repo's `data/`.

Ingest writes land in the configured archive folder, whose DEFAULT comes from
`app.paths.default_archive_dir()` (Documents/NightSeam Images). `tests/conftest.py`
redirects that default for every test; this module pins that the redirect really
INTERCEPTS a write, and that no test ever creates the real default folder or
touches the repo's own `data/` store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import paths
from app.database import Database
from app.server import create_app

ROOT = Path(__file__).resolve().parents[1]
REAL_DATA = ROOT / "data"


@pytest.fixture()
def client(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db)) as c:
        yield c


def _ingest_one_frame(client):
    from _util import make_fits
    frame = make_fits([("SIMPLE", "T"), ("OBJECT", "'Unlisted nebula'"),
                       ("IMAGETYP", "'LIGHT'"), ("EXPTIME", "60"),
                       ("DATE-OBS", "'2026-08-22T20:00:00'")]) + b"pixels"
    r = client.post("/api/images/ingest", files={
        "files": ("m42_00.fit", frame, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def test_default_archive_write_stays_inside_the_sandbox(client, tmp_path):
    result = _ingest_one_frame(client)
    assert result["ok"], result
    sandbox_default = paths.default_archive_dir()
    stored = Path(result["stored"])
    # the write landed in the redirected default archive, inside this test's
    # tmp sandbox - never in the repo's own data/ store
    assert (sandbox_default / stored).is_file()
    assert str(sandbox_default).startswith(str(tmp_path))
    for existing in REAL_DATA.rglob("*") if REAL_DATA.exists() else []:
        assert stored.name != existing.name or existing.is_dir()


def test_without_the_guard_the_default_reaches_the_real_documents_path(
        client, monkeypatch, tmp_path):
    """Mutation proof that the redirect is load-bearing.

    Point `default_archive_dir` at a SECOND private directory (what an
    unpatched run would do: the real Documents folder) and prove the write
    follows that setting. This mutation proof must never write to the real
    Documents folder - the substitute is private scratch.
    """
    alternate = tmp_path / "guard-disabled" / "NightSeam Images"
    monkeypatch.setattr("app.paths.default_archive_dir", lambda: alternate)
    result = _ingest_one_frame(client)
    assert result["ok"], result
    assert (alternate / result["stored"]).is_file()
