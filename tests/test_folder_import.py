"""Folder import from the native picker: scan and in-place ingest by token."""

import os

import pytest
from fastapi.testclient import TestClient

from app import local_import
from app.database import Database
from app.server import create_app
from _util import m31_fits, unresolved_fits


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(Database(tmp_path / "nightseam.db"))) as c:
        c.post("/api/settings", json={"image_dir": str(tmp_path / "archive")})
        yield c


@pytest.fixture()
def capture(tmp_path):
    root = tmp_path / "capture"
    (root / "night1" / "lights").mkdir(parents=True)
    (root / "night2").mkdir()
    (root / "night1" / "lights" / "m31_300.fit").write_bytes(m31_fits(300.0))
    (root / "night2" / "M31_120.FITS").write_bytes(m31_fits(120.0))
    (root / "test42.fts").write_bytes(unresolved_fits())
    (root / "night1" / "notes.txt").write_text("not an exposure", encoding="utf-8")
    return root


def scan(client, token):
    return client.post("/api/images/folder/scan", json={"folder": token})


def ingest(client, token, files, **options):
    return client.post("/api/images/folder/ingest", json={"folder": token, "files": files, **options})


def test_scan_lists_fits_exposures_in_subfolders(client, capture):
    token = local_import.register(capture)
    r = scan(client, token)
    assert r.status_code == 200
    body = r.json()
    assert [f["rel"] for f in body["files"]] == [
        "night1/lights/m31_300.fit", "night2/M31_120.FITS", "test42.fts"]
    assert body["files"][0]["size"] == len(m31_fits(300.0))
    assert body["name"] == "capture"
    assert body["archive_skipped"] is False


def test_ingest_reads_sources_in_place(client, capture, tmp_path):
    token = local_import.register(capture)
    rels = [f["rel"] for f in scan(client, token).json()["files"]]
    before = {rel: (capture / rel).read_bytes() for rel in rels}
    r = ingest(client, token, rels, run_id="folder-import-run-1")
    assert r.status_code == 200
    results = r.json()["results"]
    assert [res["file"] for res in results] == ["m31_300.fit", "M31_120.FITS", "test42.fts"]
    assert all(res["ok"] for res in results)
    for res in results:
        assert (tmp_path / "archive" / res["stored"]).is_file()
    # Sources stay exactly where and as they were.
    assert {rel: (capture / rel).read_bytes() for rel in rels} == before
    assert client.get("/api/library").json()["total_images"] == 3
    assert client.get("/api/library/imports").json()

    again = ingest(client, token, rels).json()["results"]
    assert all(res["ok"] and res["skipped"] for res in again)
    assert client.get("/api/library").json()["total_images"] == 3


def test_ingest_honors_frame_override(client, capture):
    token = local_import.register(capture)
    r = ingest(client, token, ["night2/M31_120.FITS"], frame="dark")
    res = r.json()["results"][0]
    assert res["ok"] and res["imagetyp"] == "dark"


def test_missing_source_fails_alone(client, capture):
    token = local_import.register(capture)
    rels = [f["rel"] for f in scan(client, token).json()["files"]]
    (capture / "night2" / "M31_120.FITS").unlink()
    results = ingest(client, token, rels).json()["results"]
    assert [res["ok"] for res in results] == [True, False, True]
    assert "no longer in the selected folder" in results[1]["error"]


def test_unknown_token_is_refused(client, capture):
    assert scan(client, "0" * 32).status_code == 404
    assert ingest(client, "0" * 32, ["test42.fts"]).status_code == 404


@pytest.mark.parametrize("rel", [
    "../outside.fit", "night1/../../outside.fit", "C:/Windows/win.ini", "/etc/passwd",
    "night1//m31.fit", "", "a:b.fit", "x\0.fit", "x" * 1100])
def test_paths_outside_the_folder_are_refused(client, capture, tmp_path, rel):
    (tmp_path / "outside.fit").write_bytes(m31_fits(60.0))
    token = local_import.register(capture)
    r = ingest(client, token, ["test42.fts", rel])
    assert r.status_code == 400
    # A refused request publishes nothing, not even its valid files.
    assert client.get("/api/library").json()["total_images"] == 0


def test_archive_inside_the_folder_is_skipped(client, capture, tmp_path):
    archive = capture / "archive"
    client.post("/api/settings", json={"image_dir": str(archive)})
    token = local_import.register(capture)
    rels = [f["rel"] for f in scan(client, token).json()["files"]]
    assert ingest(client, token, rels).status_code == 200
    body = scan(client, token).json()
    assert body["archive_skipped"] is True
    assert [f["rel"] for f in body["files"]] == rels
    stored = next(p for p in archive.rglob("*.fit"))
    r = ingest(client, token, [stored.relative_to(capture).as_posix()])
    assert r.status_code == 400


def test_folder_inside_the_archive_is_refused(client, tmp_path):
    inner = tmp_path / "archive" / "M31"
    inner.mkdir(parents=True)
    r = scan(client, local_import.register(inner))
    assert r.status_code == 400
    assert "inside your archive" in r.json()["detail"]


def test_linked_subfolders_are_not_followed(client, capture, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "far.fit").write_bytes(m31_fits(30.0))
    try:
        os.symlink(elsewhere, capture / "link", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks need extra privileges here")
    token = local_import.register(capture)
    assert "link/far.fit" not in [f["rel"] for f in scan(client, token).json()["files"]]
    assert ingest(client, token, ["link/far.fit"]).status_code == 400


def test_register_refuses_non_folders(tmp_path):
    (tmp_path / "file.fit").write_bytes(b"SIMPLE")
    assert local_import.register(tmp_path / "file.fit") is None
    assert local_import.register(tmp_path / "missing") is None


def test_request_size_matches_upload_chunks(client, capture):
    token = local_import.register(capture)
    r = ingest(client, token, ["test42.fts"] * 97)
    assert r.status_code == 422
