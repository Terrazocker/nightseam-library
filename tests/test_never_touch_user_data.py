"""The autouse profile guard must keep payload writes out of the repo's `data/`.

`app/backup.py` binds `data_dir` with `from .paths import data_dir`, so patching
`app.server.data_dir` (which `test_api.py`'s client fixture does) is NOT enough:
a bundle import still wrote its panorama payload into the developer's real
`data/panos/`. It was found twice as a stray `ok.jpg` - 1 byte during the 0.6.32
    prior full review (the removed report remains in Git history) and 8 bytes
(`b"JPEGDATA"`) during the fix session - and the timing was the nasty part: the
assertion that guards it reads that same real directory, so the pollution
happened exactly when the test was FAILING.

`tests/conftest.py` now redirects `data_dir` for every test. This module pins
that the redirect really INTERCEPTS a write, by forcing one: a bundle whose
panorama record is valid (a plain token id), so the payload is written by design.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.server import create_app

ROOT = Path(__file__).resolve().parents[1]
REAL_PANOS = ROOT / "data" / "panos"
PROBE = "never_touch_user_data_probe.jpg"


@pytest.fixture()
def client(tmp_path):
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db, prefetch=False)) as c:
        c.post("/api/settings", json={"image_dir": str(tmp_path / "archive")})
        yield c


def _bundle() -> bytes:
    setup = {"app": "NightSeam", "settings": {}, "sites": [], "rigs": [],
             "panoramas": [{"id": "p1", "name": "P", "file": PROBE,
                            "strokes": [], "profile": [], "profile_top": []}],
             "customs": [], "images": []}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("setup.json", json.dumps(setup))
        z.writestr(f"panos/{PROBE}", b"PROBE-PAYLOAD")
    return out.getvalue()


def test_a_bundle_import_keeps_the_pano_payload_out_of_the_real_data_dir(client):
    import app.backup
    real = REAL_PANOS / PROBE
    before = real.read_bytes() if real.exists() else None
    sand = app.backup.data_dir() / "panos" / PROBE
    r = client.post("/api/import", files={
        "file": ("bundle.zip", _bundle(), "application/zip")})
    assert r.status_code == 200, r.text
    assert (real.read_bytes() if real.exists() else None) == before
    assert sand.read_bytes() == b"PROBE-PAYLOAD"


def test_without_the_guard_the_payload_bypasses_the_sandbox(client, monkeypatch, tmp_path):
    """Mutation proof that the guard above is load-bearing.

    Undo just the `app/backup.py` half of the conftest redirect and the identical
    import drops the payload into a second private destination. Even this
    mutation proof must never write or delete actual source data.
    """
    import app.backup

    sand = app.backup.data_dir() / "panos" / PROBE
    alternate = tmp_path / "guard-disabled"
    (alternate / "panos").mkdir(parents=True)
    monkeypatch.setattr(app.backup, "data_dir", lambda: alternate)
    r = client.post("/api/import", files={
        "file": ("bundle.zip", _bundle(), "application/zip")})
    assert r.status_code == 200, r.text
    assert (alternate / "panos" / PROBE).read_bytes() == b"PROBE-PAYLOAD"
    assert not sand.exists()
