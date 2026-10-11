"""HTTP-level API tests via fastapi TestClient (httpx).

These exercise the full route layer of create_app(): validation, error
codes, and ingest for the image-catalog API surface.
"""

import io
import json
import math
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import server as server_mod
from app.database import Database
from app.server import create_app
from _util import make_fits, m31_fits, unresolved_fits

from app.catalog_data import STANDARD_TOP_N

@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "nightseam.db")


@pytest.fixture()
def client(db, tmp_path):
    with TestClient(create_app(db)) as c:
        c.post("/api/settings", json={"image_dir": str(tmp_path / "archive")})
        yield c


# ------------------------------------------------------------------ state / settings

def test_state_defaults(client):
    st = client.get("/api/state").json()
    assert st["version"]
    assert st["image_dir"]  # defaults to a real path
    assert st["size_unit"] == "arcmin"


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"size_unit": "deg"})
    assert r.status_code == 200
    assert client.get("/api/state").json()["size_unit"] == "deg"
    r = client.post("/api/settings", json={"size_unit": "arcmin"})
    assert r.status_code == 200
    assert client.get("/api/state").json()["size_unit"] == "arcmin"


def test_settings_rejects_bad_size_unit(client):
    r = client.post("/api/settings", json={"size_unit": "parsecs"})
    assert r.status_code == 400


def test_observing_site_roundtrip_and_clear(client):
    site = {"latitude": 50.0383, "longitude": 7.81667}
    assert client.post("/api/settings", json={"observing_site": site}).status_code == 200
    assert client.get("/api/state").json()["observing_site"] == site
    assert client.post("/api/settings", json={"size_unit": "deg"}).status_code == 200
    assert client.get("/api/state").json()["observing_site"] == site
    assert client.post("/api/settings", json={"observing_site": None}).status_code == 200
    assert client.get("/api/state").json()["observing_site"] is None


@pytest.mark.parametrize("invalid", [{"size_unit": "parsecs"}, {"image_dir": "x" * 1025},
                                    {"observing_site": {"latitude": 91, "longitude": 0}},
                                    {"observing_site": {"latitude": True, "longitude": 0}}])
def test_rejected_settings_leave_all_values_unchanged(client, invalid):
    before = client.get("/api/state").json()
    change = {"size_unit": "deg", "observing_site": {"latitude": 50, "longitude": 8}, **invalid}
    assert client.post("/api/settings", json=change).status_code in (400, 422)
    assert client.get("/api/state").json() == before


def test_retired_sky_brightness_setting_is_ignored(client):
    """Old clients may still submit the removed preference; it must not be
    exposed or stored again."""
    assert "sky_brightness_provider" not in client.get("/api/state").json()
    assert client.post("/api/settings", json={
        "sky_brightness_provider": "unknown"}).status_code == 200
    assert "sky_brightness_provider" not in client.get("/api/state").json()


# ------------------------------------------------------------------ sites / rigs

# ---------------------------------------------------- planning lives in core/
# Phase C removed the server planning routes (/api/night, /api/object/{oid}):
# the desktop and the web both compute from core/ client-side. The payload
# contracts are pinned by tests/js/night_race.mjs, tests/js/core_fixtures.mjs
# and the derive engines - plus the frozen fixtures under tests/fixtures/.


def test_library_edit_database_failure_undoes_the_disk_move(client, db, monkeypatch):
    """F-22: if the database update fails AFTER the folder move completed, the
    move is undone - the files are back where the unchanged rows point."""
    from pathlib import Path

    r = client.post("/api/images/ingest", files=[
        ("files", ("unlisted.fit", unresolved_fits(), "application/octet-stream"))])
    assert r.status_code == 200
    lib_before = client.get("/api/library").json()
    image_dir = Path(client.get("/api/state").json()["image_dir"])

    def boom(*args, **kwargs):
        raise RuntimeError("database locked during rename")

    monkeypatch.setattr(db, "rename_images", boom)
    # TestClient re-raises unhandled server errors; the undo runs inside the
    # route BEFORE the exception escapes, which is what this test pins.
    with pytest.raises(RuntimeError, match="database locked during rename"):
        client.post("/api/library/edit",
                    json={"name": "TestObj42", "new_name": "My Nebula"})

    lib = client.get("/api/library").json()
    assert lib["objects"][0]["name"] == "TestObj42"
    stored = lib["objects"][0]["images"][0]["file"]
    assert (image_dir / stored).is_file()
    assert stored == lib_before["objects"][0]["images"][0]["file"]
    assert not (image_dir / "My Nebula").exists()


def _add_rename_image(db, archive, name, relative, payload, object_id=None):
    path = archive / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    db.add_image({"file": relative, "orig_name": path.name,
                  "object_name": name, "object_id": object_id,
                  "ingested_at": "2026-10-11T00:00:00Z"})
    return path


@pytest.mark.parametrize("other_object_id", [None, "ngc224"])
@pytest.mark.parametrize("other_folder", ["Obj", "oBJ"])
def test_library_rename_refuses_shared_slug_without_changing_rows_or_bytes(
        client, db, tmp_path, other_object_id, other_folder):
    archive = tmp_path / "archive"
    first = _add_rename_image(db, archive, "Obj!", "Obj/session/lights/a.fit", b"A")
    second = _add_rename_image(
        db, archive, "Obj?", f"{other_folder}/session/lights/b.fit", b"B",
        object_id=other_object_id)
    before = db.images()
    response = client.post("/api/library/edit", json={"name": "Obj!", "new_name": "Other"})
    assert response.status_code == 409
    assert "another object group" in response.json()["detail"]
    assert db.images() == before
    assert first.read_bytes() == b"A" and second.read_bytes() == b"B"
    assert not (archive / "Other").exists()


def test_library_edit_same_slug_keeps_other_group_files_in_place(client, db, tmp_path):
    archive = tmp_path / "archive"
    first = _add_rename_image(db, archive, "Obj!", "Obj/session/lights/a.fit", b"A")
    second = _add_rename_image(db, archive, "Obj?", "Obj/session/lights/b.fit", b"B")
    other_before = db.images()[1]
    response = client.post("/api/library/edit", json={"name": "Obj!", "new_name": "Obj#"})
    assert response.status_code == 200
    assert db.images()[0]["object_name"] == "Obj#"
    assert db.images()[1] == other_before
    assert first.read_bytes() == b"A" and second.read_bytes() == b"B"


def test_library_rename_allows_legacy_spellings_in_the_same_logical_group(
        client, db, tmp_path):
    archive = tmp_path / "archive"
    _add_rename_image(db, archive, "Obj!", "Obj/session/lights/a.fit", b"A")
    _add_rename_image(db, archive, " O b j! ", "Obj/session/lights/b.fit", b"B")
    response = client.post("/api/library/edit", json={"name": "Obj!", "new_name": "Other"})
    assert response.status_code == 200
    assert all(row["object_name"] == "Other" for row in db.images())
    assert (archive / "Other/session/lights/a.fit").read_bytes() == b"A"
    assert (archive / "Other/session/lights/b.fit").read_bytes() == b"B"


def test_library_rename_refuses_naturally_imported_multi_root_aliases(client, db):
    imports = []
    for index, name in enumerate(("Zzq Blob 7", "ZzqBlob7")):
        payload = make_fits([("SIMPLE", "T"), ("OBJECT", repr(name)),
                             ("DATE-OBS", f"'2026-01-01T{20 + index}:00:00'")])
        result = client.post("/api/images/ingest", files=[
            ("files", (f"{index}.fit", payload, "application/octet-stream"))]).json()["results"][0]
        assert result["ok"]
        imports.append(result)
    before = db.images()
    original_bytes = [(tmp_archive(client) / item["stored"]).read_bytes() for item in imports]
    response = client.post("/api/library/edit", json={"name": "Zzq Blob 7", "new_name": "Renamed"})
    assert response.status_code == 409
    assert db.images() == before
    assert [(tmp_archive(client) / item["stored"]).read_bytes() for item in imports] == original_bytes
    assert not (tmp_archive(client) / "Renamed").exists()


def test_library_rename_refuses_stored_root_that_differs_from_display_name(client, db, tmp_path):
    original = _add_rename_image(db, tmp_path / "archive", "Display", "Legacy/session/lights/a.fit", b"A")
    before = db.images()
    assert client.post("/api/library/edit", json={"name": "Display", "new_name": "New"}).status_code == 409
    assert db.images() == before and original.read_bytes() == b"A"


def test_library_rename_refuses_missing_record_before_moving_other_files(client, db, tmp_path):
    original = _add_rename_image(db, tmp_path / "archive", "Old", "Old/session/lights/a.fit", b"A")
    _add_rename_image(db, tmp_path / "archive", "Old", "Old/session/lights/b.fit", b"B").unlink()
    before = db.images()
    assert client.post("/api/library/edit", json={"name": "Old", "new_name": "New"}).status_code == 409
    assert db.images() == before and original.read_bytes() == b"A"
    assert not (tmp_path / "archive/New").exists()


@pytest.mark.parametrize("failure", [ValueError, sqlite3.OperationalError])
def test_upload_exception_preserves_prior_results_and_reports_remaining_files(client, db, monkeypatch, failure):
    real_add = db.add_image
    calls = 0

    def rejected_second(rec):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure("injected file failure")
        return real_add(rec)

    monkeypatch.setattr(db, "add_image", rejected_second)
    response = client.post("/api/images/ingest", files=[
        ("files", (f"{index}.fit", m31_fits(index + 1), "application/octet-stream"))
        for index in range(3)])
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 3 and results[0]["ok"] and not results[1]["ok"]
    if failure is ValueError:
        assert results[2]["ok"] and len(db.images()) == 2
    else:
        assert results[1]["unconfirmed"] and results[2]["unattempted"]
        assert len(db.images()) == 1 and calls == 2
    assert (tmp_archive(client) / results[0]["stored"]).is_file()
    assert not list(tmp_archive(client).rglob("*.part"))
    assert len(list(tmp_archive(client).rglob("*.fit"))) == len(db.images())


def test_upload_history_database_failure_reports_committed_file_as_unconfirmed(client, db):
    db.conn.execute("CREATE TRIGGER reject_history BEFORE INSERT ON import_batches "
                    "BEGIN SELECT RAISE(ABORT, 'injected history failure'); END")
    db.conn.commit()
    response = client.post("/api/images/ingest", files=[
        ("files", ("a.fit", m31_fits(), "application/octet-stream")),
        ("files", ("b.fit", m31_fits(1), "application/octet-stream"))])
    results = response.json()["results"]
    assert response.status_code == 200 and results[0]["unconfirmed"] and results[1]["unattempted"]
    assert len(db.images()) == 1
    assert (tmp_archive(client) / db.images()[0]["file"]).is_file()
    assert not db.conn.in_transaction
    assert client.post("/api/library/reindex").status_code == 200


@pytest.mark.parametrize("text", ["=1+1", "+SUM(A1:A2)", "-1+2", "@SUM(A1:A2)", "  =1+1", "\r\n=1+1", "＝1+1"])
def test_csv_neutralizes_formula_text_without_changing_metadata(client, db, text):
    import csv
    record = {"file": "Object/undated/lights/a.fit", "orig_name": text,
              "object_name": text, "filter": text, "telescope": text,
              "ra_hours": 1.25, "dec_deg": -25.0, "ingested_at": "2026-10-11"}
    db.add_image(record)
    response = client.get("/api/library/export?format=csv")
    row = next(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    for field in ("orig_name", "object_name", "filter", "telescope"):
        assert row[field] == "\t" + text
        assert db.images()[0][field] == text
    assert row["dec_deg"] == "-25.0"
    image = client.get("/api/library/export?format=json").json()["objects"][0]["images"][0]
    assert image["filter"] == text and image["telescope"] == text


def test_production_host_policy_and_csp_exclude_retired_allowances(db, monkeypatch):
    monkeypatch.delenv("NIGHTSEAM_ALLOW_HOST")
    with TestClient(create_app(db)) as production:
        assert production.get("/api/state").status_code == 403
    with TestClient(create_app(db), base_url="http://127.0.0.1") as production:
        response = production.get("/api/state")
        assert response.status_code == 200
        csp = production.get("/").headers["content-security-policy"]
        assert "connect-src 'self'" in csp
        assert not any(host in csp for host in ("wikimedia", "alasky", "open-meteo"))


def test_reindex_disconnect_signals_and_drains_worker(db, tmp_path, monkeypatch):
    import asyncio
    stopped = threading.Event()

    def worker(database, archive, cancel):
        assert cancel.wait(2), "disconnect did not reach worker"
        stopped.set()
        raise server_mod.ReindexCancelled()

    class Disconnected:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(server_mod, "reindex_library", worker)
    with pytest.raises(server_mod.ReindexCancelled):
        asyncio.run(server_mod._reindex_with_disconnect(db, Disconnected()))
    assert stopped.is_set()


def test_reindex_task_cancellation_signals_and_drains_worker(db, tmp_path, monkeypatch):
    import asyncio
    started, stopped = threading.Event(), threading.Event()

    def worker(database, archive, cancel):
        started.set()
        assert cancel.wait(2), "request cancellation did not reach worker"
        stopped.set()
        raise server_mod.ReindexCancelled()

    class Connected:
        async def is_disconnected(self):
            return False

    async def scenario():
        task = asyncio.create_task(server_mod._reindex_with_disconnect(db, Connected()))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(server_mod, "reindex_library", worker)
    asyncio.run(scenario())
    assert stopped.is_set()


def test_queued_reindex_uses_archive_setting_under_archive_lock(db, tmp_path, monkeypatch):
    import asyncio
    queued = threading.Event()
    roots = []
    original = tmp_path / "first"
    current = tmp_path / "second"
    db.set_setting("image_dir", str(original))

    def scan(database, archive, cancel):
        roots.append(archive)
        return {"found": 0}

    class Connected:
        async def is_disconnected(self):
            queued.set()
            return False

    async def scenario():
        with db.archive_lock:
            task = asyncio.create_task(server_mod._reindex_with_disconnect(db, Connected()))
            while not queued.is_set():
                await asyncio.sleep(0.01)
            db.set_setting("image_dir", str(current))
        assert await task == {"found": 0}

    monkeypatch.setattr(server_mod, "reindex_library", scan)
    asyncio.run(scenario())
    assert roots == [current]


def test_library_database_failure_reports_failed_undo_with_original_diagnostics(
        client, db, tmp_path, monkeypatch, caplog):
    from pathlib import Path

    archive = tmp_path / "archive"
    old = _add_rename_image(db, archive, "Old", "Old/session/lights/a.fit", b"original")
    before = db.images()
    db.conn.execute("CREATE TRIGGER reject_rename_file BEFORE UPDATE OF file ON images "
                    "BEGIN SELECT RAISE(ABORT, 'injected database update failure'); END")
    db.conn.commit()
    new = archive / "New/session/lights/a.fit"
    real_rename = Path.rename

    def failed_undo(src, dst):
        if src == archive / "New":
            raise OSError("injected locked original destination")
        return real_rename(src, dst)

    monkeypatch.setattr(Path, "rename", failed_undo)
    response = client.post("/api/library/edit", json={"name": "Old", "new_name": "New"})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "recovery is incomplete" in detail and "Reindex folders" in detail
    assert "resolve the filesystem problem" in detail
    assert "unrestored paths: New" in detail
    assert db.images() == before and not db.conn.in_transaction
    assert not old.exists() and new.read_bytes() == b"original"
    assert any(record.exc_info and
               "injected database update failure" in str(record.exc_info[1])
               for record in caplog.records)
    assert "undo failed for New:" in caplog.text
    assert "injected locked original destination" in caplog.text


def test_library_database_failure_reports_occupied_whole_directory_origin(
        client, db, tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    _add_rename_image(db, archive, "Old", "Old/session/lights/a.fit", b"original")
    before = db.images()

    def failed_database_update(*args, **kwargs):
        (archive / "Old").mkdir()
        (archive / "Old/untouched.bin").write_bytes(b"new occupant")
        raise RuntimeError("database update failed")

    monkeypatch.setattr(db, "rename_images", failed_database_update)
    response = client.post("/api/library/edit", json={"name": "Old", "new_name": "New"})
    assert response.status_code == 500
    assert "recovery is incomplete" in response.json()["detail"]
    assert "Reindex folders" in response.json()["detail"]
    assert db.images() == before
    assert (archive / "Old/untouched.bin").read_bytes() == b"new occupant"
    assert (archive / "New/session/lights/a.fit").read_bytes() == b"original"


def test_library_merge_failure_reports_incomplete_file_undo(
        client, db, tmp_path, monkeypatch, caplog):
    from pathlib import Path
    from app import ingest

    archive = tmp_path / "archive"
    first = _add_rename_image(db, archive, "Old", "Old/session/lights/a.fit", b"A")
    second = _add_rename_image(db, archive, "Old", "Old/session/lights/b.fit", b"B")
    (archive / "New").mkdir()
    before = db.images()
    real_move = ingest._move_one
    real_replace = Path.replace
    calls = 0

    def failed_second_move(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected forward move failure")
        return real_move(src, dst)

    def failed_undo(src, dst):
        if src == archive / "New/session/lights/a.fit":
            raise OSError("injected file recovery failure")
        return real_replace(src, dst)

    monkeypatch.setattr(ingest, "_move_one", failed_second_move)
    monkeypatch.setattr(Path, "replace", failed_undo)
    response = client.post("/api/library/edit", json={"name": "Old", "new_name": "New"})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "recovery is incomplete" in detail and "Reindex folders" in detail
    assert "New/session/lights/a.fit" in detail
    assert db.images() == before
    assert not first.exists() and second.read_bytes() == b"B"
    assert (archive / "New/session/lights/a.fit").read_bytes() == b"A"
    assert "injected forward move failure" in caplog.text
    assert "injected file recovery failure" in caplog.text


def test_f15_oversized_strings_are_rejected_not_truncated(client, tmp_path, monkeypatch):
    """F-15: the remaining write fields reject oversized input with a clean 400
    - never a silent truncate (a cut image_dir would name a DIFFERENT folder)
    and never a multi-kilobyte archive folder name."""
    r = client.post("/api/images/ingest", files=[
        ("files", ("unlisted.fit", unresolved_fits(), "application/octet-stream"))])
    assert r.status_code == 200
    r = client.post("/api/library/edit",
                    json={"name": "TestObj42", "new_name": "x" * 121})
    assert r.status_code == 400 and "120" in r.json()["detail"]
    assert client.post("/api/library/edit",
                       json={"name": "TestObj42", "new_name": "x" * 120}
                       ).status_code == 200

    # archive path: reject, never cut
    r = client.post("/api/settings", json={"image_dir": str(tmp_path / ("d" * 1025))})
    assert r.status_code == 400 and "1024" in r.json()["detail"]

    # open_folder rel: oversized input is refused before the filesystem
    monkeypatch.setattr("os.startfile", lambda p: None)
    assert client.post("/api/library/open_folder",
                       json={"rel": "x" * 513}).status_code == 400
    assert client.post("/api/library/open_folder",
                       json={"rel": "x" * 512}).status_code == 404  # in-bounds


def test_retired_planner_and_restore_routes_answer_404(client):
    """A stale client must fail loudly, not reach a half-alive route. The
    planner retirement removed whole route families; every one of them must
    now fall through to the static mount's 404 instead of half-working."""
    for method, path in (
            ("get", "/api/night"), ("get", "/api/sites"),
            ("post", "/api/sites"), ("get", "/api/rigs"),
            ("post", "/api/rigs"), ("get", "/api/panoramas"),
            ("post", "/api/panorama"), ("get", "/api/catalog"),
            ("get", "/api/catalog/provenance"),
            ("post", "/api/catalog/custom"),
            ("get", "/api/object/m31/image"),
            ("post", "/api/images/cache/clear"),
            ("get", "/api/export"), ("post", "/api/import"),
            ("post", "/api/update-check"), ("get", "/api/shutdown"),
            ("post", "/api/shutdown"),
            ("get", "/api/images/1/preview"),
            ("get", "/index.html")):
        r = client.request(method, path)
        # GETs fall through to the static mount's 404; POSTs are refused by
        # the mount itself with 405 (it only serves GET/HEAD) - both prove
        # the route family is gone rather than half-alive.
        assert r.status_code in ({404} if method == "get" else {404, 405}), (
            method, path, r.status_code)


# ------------------------------------------------------------------ catalog / customs

# ------------------------------------------------------------------ ingest / library

def test_ingest_fits(client, tmp_path):
    r = client.post("/api/images/ingest",
                    files=[("files", ("m31.fit", m31_fits(),
                                      "application/octet-stream"))])
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] is True
    assert res["object"]["id"] == "ngc224"
    assert res["exposure"] == 300.0
    assert res["filter"] == "L"

    lib = client.get("/api/library").json()
    assert lib["total_images"] == 1
    assert lib["total_exposure"] == 300.0
    assert lib["objects"][0]["name"] == "Andromeda Galaxy"
    stored = res["stored"]
    assert (tmp_path / "archive" / stored).is_file()


def test_ingest_unresolved_and_library_edit(client, tmp_path):
    client.post("/api/images/ingest",
                files=[("files", ("t42.fit", unresolved_fits(),
                                  "application/octet-stream"))])
    lib = client.get("/api/library").json()
    group = lib["objects"][0]
    assert group["object_id"] is None
    assert group["name"] == "TestObj42"
    assert group["n_sessions"] == 1

    r = client.post("/api/library/edit",
                    json={"name": "TestObj42", "new_name": "Renamed 42",
                          "otype": "nebula"})
    assert r.status_code == 200
    lib = client.get("/api/library").json()
    assert lib["objects"][0]["name"] == "Renamed 42"
    assert lib["objects"][0]["otype"] == "nebula"
    assert any((tmp_path / "archive" / "Renamed 42").rglob("lights/t42.fit"))


class _ObservedArchiveLock:
    """Signal contention before blocking, making publication races deterministic."""
    def __init__(self, lock):
        self.lock = lock
        self.owner = None
        self.depth = 0
        self.contended = threading.Event()

    def __enter__(self):
        identity = threading.get_ident()
        if self.owner is not None and self.owner != identity:
            self.contended.set()
        self.lock.acquire()
        self.owner = identity
        self.depth += 1
        return self

    def __exit__(self, *_args):
        self.depth -= 1
        if not self.depth:
            self.owner = None
        self.lock.release()


def test_library_rename_waits_for_duplicate_verification_without_stale_paths(client, db, monkeypatch):
    from app import ingest
    payload = unresolved_fits()
    first = client.post("/api/images/ingest", files=[
        ("files", ("source.fit", payload, "application/octet-stream")),
    ]).json()["results"][0]
    lock = _ObservedArchiveLock(db.archive_lock)
    monkeypatch.setattr(db, "archive_lock", lock)
    entered, release = threading.Event(), threading.Event()
    original = ingest._fingerprint_file

    def paused(path):
        assert not db.lock._is_owned()
        entered.set()
        assert release.wait(8)
        return original(path)

    monkeypatch.setattr(ingest, "_fingerprint_file", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicate = pool.submit(client.post, "/api/images/ingest", files=[
            ("files", ("renamed.fit", payload, "application/octet-stream")),
        ])
        try:
            assert entered.wait(3)
            rename = pool.submit(client.post, "/api/library/edit", json={
                "name": "TestObj42", "new_name": "Renamed 42"})
            assert lock.contended.wait(3)
            assert not rename.done()
            assert client.get("/api/library").json()["total_images"] == 1
        finally:
            release.set()
        assert duplicate.result(timeout=5).json()["results"][0]["skipped"]
        assert rename.result(timeout=5).status_code == 200
    row = db.image(first["id"])
    assert row["object_name"] == "Renamed 42"
    assert (tmp_archive(client) / row["file"]).read_bytes() == payload
    assert len(list(tmp_archive(client).rglob("*.fit"))) == 1


def test_archive_change_during_streaming_refuses_stale_root_publication(client, db, tmp_path, monkeypatch):
    from app import ingest
    import asyncio
    old_archive = tmp_archive(client)
    old_archive.mkdir(exist_ok=True)
    sentinel = old_archive / "keep-user-file.txt"
    sentinel.write_bytes(b"user bytes")
    entered, release = threading.Event(), threading.Event()
    original = ingest._stream_to

    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        assert await asyncio.to_thread(release.wait, 8)
        return result

    monkeypatch.setattr(ingest, "_stream_to", paused)
    new_archive = tmp_path / "new-archive"
    with ThreadPoolExecutor(max_workers=1) as pool:
        upload = pool.submit(client.post, "/api/images/ingest", files=[
            ("files", ("source.fit", unresolved_fits(), "application/octet-stream")),
        ])
        try:
            assert entered.wait(3)
            assert client.post("/api/settings", json={"image_dir": str(new_archive)}).status_code == 200
        finally:
            release.set()
        result = upload.result(timeout=5).json()["results"][0]
    assert not result["ok"] and "location changed" in result["error"]
    assert not db.images() and client.get("/api/library/imports").json() == []
    assert sentinel.read_bytes() == b"user bytes"
    assert not list(old_archive.rglob("*.fit")) and not list(old_archive.rglob("*.part"))


def test_library_edit_rename_never_overwrites_a_colliding_frame(client, tmp_path):
    """PY-01 (blocker, review 0.6.32): renaming an unresolved group into a name
    whose folder ALREADY holds a same-named frame used `Path.replace(dest)` with
    no uniquify - the colliding frame's pixels were destroyed while both DB rows
    were rewritten to that one path (two rows aliasing one file; nothing else
    held those bytes, `make_backup` copies only the DB).

    Follows the ingest convention (`_archive_target` uniquifies to `stem_2`), so
    the assertion is implementation-neutral on purpose: what must hold is that BOTH
    frames still exist, on DISTINCT stored paths, with their own bytes."""
    def frame(obj: str, exposure: float) -> bytes:
        return make_fits([
            ("SIMPLE", "T"), ("OBJECT", f"'{obj}'"),
            ("EXPTIME", str(exposure)),
        ])

    for obj, exposure in (("ArchiveOne", 60.0), ("ArchiveTwo", 120.0)):
        r = client.post("/api/images/ingest", files=[
            ("files", ("Light_001.fit", frame(obj, exposure),
                       "application/octet-stream"))])
        assert r.status_code == 200 and r.json()["results"][0]["ok"] is True

    archive = tmp_path / "archive"
    lib = client.get("/api/library").json()
    assert {o["name"] for o in lib["objects"]} == {"ArchiveOne", "ArchiveTwo"}

    first = archive / "ArchiveOne/undated/lights/Light_001.fit"
    second = archive / "ArchiveTwo/undated/lights/Light_001.fit"
    assert first.read_bytes() == frame("ArchiveOne", 60.0)
    assert second.read_bytes() == frame("ArchiveTwo", 120.0)

    r = client.post("/api/library/edit",
                    json={"name": "ArchiveTwo", "new_name": "ArchiveOne"})
    assert r.status_code == 200, r.text

    # the two frames are still two distinct files carrying their own bytes
    files = sorted(p for p in archive.rglob("*.fit") if p.is_file())
    assert len(files) == 2, [str(p) for p in files]
    assert {p.read_bytes() for p in files} == {frame("ArchiveOne", 60.0),
                                               frame("ArchiveTwo", 120.0)}

    # and the DB does not alias one file with two rows
    db = Database(tmp_path / "nightseam.db")
    stored = [i["file"] for i in db.images() if i["object_name"] == "ArchiveOne"]
    assert len(stored) == 2, stored
    assert len(set(stored)) == 2, f"two rows alias one file: {stored}"
    for rel in stored:
        assert (archive / rel).is_file(), rel


def test_ingest_rejects_bad_files(client):
    r = client.post("/api/images/ingest", files=[
        ("files", ("notes.txt", b"not a fits file", "text/plain")),
        ("files", ("bad.fit", b"GIF89a garbage", "application/octet-stream")),
    ])
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["error"] == "not a .fit/.fits file"
    assert results[1]["error"] == "Unsupported exposure file (SIMPLE header missing)"
    assert client.get("/api/library").json()["total_images"] == 0


# --------------------------------------------------- PY-04 non-finite headers
#
# Review 0.6.24, PY-04: a FITS header is untrusted input. '1e400' in EXPTIME
# parsed to `inf`, which was streamed to disk AND stored in the images and
# import_batches rows; the ingest response then failed to serialize (`inf` is
# not JSON) and, because the history row is committed before the response is
# rendered, every later /api/library and /api/library/imports request answered
# 500 while /api/library/export?format=json emitted the bare token `Infinity`.
# OBJCTRA/OBJCTDEC = 'Infinity' raised inside math.sin() and 500ed the whole
# multi-file request; 'nan' answered 200 but silently filed the frame under an
# unrelated catalog object.

def nonfinite_fits(**cards) -> bytes:
    """Minimal M 31 frame with overridable header cards (values are raw
    FITS-card text so '1e400'/'nan'/'Infinity' go through the real parser)."""
    base = {
        "OBJECT": "'M 31'",
        "EXPTIME": "300.0",
        "OBJCTRA": "'00 42 44.3'",
        "OBJCTDEC": "'+41 16 09'",
        "NAXIS": "2",
    }
    base.update(cards)
    return make_fits([("SIMPLE", "T")] + list(base.items()))


def test_ingest_extreme_exptime_leaves_a_healthy_library(client, db, tmp_path):
    """PY-04 regression (fails before the fix): EXPTIME='1e400'.

    Before: ingest 500 + frame file + images/import_batches rows with exposure
    `inf`, then /api/library and /api/library/imports 500 forever.
    After: 200, exposure None, no stored infinity anywhere, every library route
    200 and the export is strict JSON."""
    r = client.post("/api/images/ingest",
                    files=[("files", ("huge.fit", nonfinite_fits(EXPTIME="1e400"),
                                      "application/octet-stream"))])
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] is True, res
    assert res["exposure"] is None

    # the row that used to hold `inf` is clean and finite
    rows = db.images()
    assert len(rows) == 1
    exp = rows[0]["exposure"]
    assert exp is None or (isinstance(exp, float) and math.isfinite(exp))
    for b in db.import_batches():
        assert math.isfinite(float(b["total_exposure"])), b

    # ... and every route that used to 500 answers 200
    for route in ("/api/library", "/api/library/imports"):
        assert client.get(route).status_code == 200, route
    lib = client.get("/api/library").json()
    assert lib["total_images"] == 1
    assert math.isfinite(lib["total_exposure"])

    exp_r = client.get("/api/library/export?format=json")
    assert exp_r.status_code == 200
    text = exp_r.content.decode("utf-8")            # strict parser must accept it
    assert "Infinity" not in text and "NaN" not in text
    json.loads(text)


def test_ingest_non_finite_coordinates_do_not_corrupt_the_library(client, db):
    """PY-04 coordinate half: 'Infinity' used to 500 (math.sin(inf)) and 'nan'
    silently mis-filed the frame. Both are now unparseable coordinates (None),
    so the ingest is all-or-nothing and the library stays healthy.

    NOTE: this case resolves the frame by its OBJECT name ('M 31'), so it passes
    on the pre-fix tree as well - it RECORDS the intended behaviour rather than
    pinning the certified 500. The failing-before pin for the certified path is
    the whole-ingest half of test_ingest_extreme_exptime_leaves_a_healthy_library
    and, for the coordinate 500 itself, the parser pins in tests/test_fits.py
    plus the unresolved-object case in
    test_ingest_unresolved_object_with_non_finite_coordinates (below)."""
    r = client.post("/api/images/ingest", files=[
        ("files", ("inf.fit", nonfinite_fits(OBJCTRA="'Infinity'"),
                   "application/octet-stream")),
        ("files", ("nan.fit", nonfinite_fits(OBJCTRA="'nan'", OBJCTDEC="'nan'"),
                   "application/octet-stream")),
        ("files", ("ovf.fit", nonfinite_fits(OBJCTDEC="'1e400'"),
                   "application/octet-stream")),
    ])
    assert r.status_code == 200
    results = r.json()["results"]
    assert all(x["ok"] for x in results), results

    # The non-finite coordinate is unparseable (None), so the header OBJECT
    # resolves the frame by name and the STORED coordinates are the catalog
    # object's own finite values - never an inf/nan handed to math.sin(), and
    # never a frame silently filed under an unrelated object.
    lib = client.get("/api/library").json()
    assert lib["total_images"] == 3
    assert [o["object_id"] for o in lib["objects"]] == ["ngc224"]
    for o in lib["objects"]:
        assert math.isfinite(float(o["total_exposure"]))
    for row in db.images():
        assert row["object_id"] == "ngc224", row
        for value in (row["ra_hours"], row["dec_deg"]):
            assert value is not None and math.isfinite(value), row


def test_ingest_unresolved_object_with_non_finite_coordinates(client, db):
    """PY-04, certified path: an OBJECT name that resolves to NO catalog row,
    with OBJCTRA/DEC = 'Infinity'/'1e400'/'nan'.

    This is the shape the 0.6.24 review probed: with no name match the ingest
    falls back to the coordinate matcher, and app/imglib.py::angular_dist then
    called math.sin(inf) on the pre-fix tree ->
    `ValueError: expected a finite input, got inf` -> HTTP 500 for the whole
    request, with the frame already on disk. (Verified against a `git archive
    HEAD` shadow before the fix.) Post-fix the non-finite cards parse to None,
    so the frame is stored UNRESOLVED with NULL coordinates and every library
    route still answers: 200, frame stored, coordinates NULL or finite."""
    r = client.post("/api/images/ingest", files=[
        ("files", ("c1.fit", nonfinite_fits(OBJECT="'Comet Zzz 9'",
                                            OBJCTRA="'Infinity'"),
                   "application/octet-stream")),
        ("files", ("c2.fit", nonfinite_fits(OBJECT="'Comet Zzz 9'",
                                            OBJCTDEC="'1e400'"),
                   "application/octet-stream")),
        ("files", ("c3.fit", nonfinite_fits(OBJECT="'Comet Zzz 9'",
                                            OBJCTRA="'nan'", OBJCTDEC="'nan'"),
                   "application/octet-stream")),
    ])
    assert r.status_code == 200
    results = r.json()["results"]
    assert all(x["ok"] for x in results), results

    rows = db.images()
    assert len(rows) == 3
    for row in rows:
        assert row["object_id"] is None, row           # unresolved, not mis-filed
        assert row["object_name"] == "Comet Zzz 9", row
        for value in (row["ra_hours"], row["dec_deg"], row["exposure"]):
            assert value is None or math.isfinite(value), row

    lib = client.get("/api/library").json()
    assert lib["total_images"] == 3
    assert [o["name"] for o in lib["objects"]] == ["Comet Zzz 9"]
    assert lib["objects"][0]["object_id"] is None
    text = client.get("/api/library/export?format=json").content.decode("utf-8")
    assert "Infinity" not in text and "NaN" not in text
    json.loads(text)


def test_ingest_finite_coordinates_still_resolve(client, db):
    """Guard against over-rejecting: a normal frame keeps its parsed
    coordinates and still matches the catalog through them."""
    r = client.post("/api/images/ingest",
                    files=[("files", ("good.fit", nonfinite_fits(),
                                      "application/octet-stream"))])
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] and res["object"]["id"] == "ngc224"
    assert res["exposure"] == 300.0
    row = db.images()[0]
    # OBJCTRA '00 42 44.3' -> 0.712 h and OBJCTDEC '+41 16 09' -> 41.269 deg,
    # i.e. the coordinate matcher resolved the catalog object (its stored
    # values), exactly as before the fix.
    assert 0.70 < row["ra_hours"] < 0.72
    assert 41.2 < row["dec_deg"] < 41.4


def test_ingest_size_cap(client, monkeypatch):
    import app.ingest
    monkeypatch.setattr(app.ingest, "MAX_INGEST_BYTES", 64)
    r = client.post("/api/images/ingest",
                    files=[("files", ("big.fit", m31_fits(),
                                      "application/octet-stream"))])
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] is False
    assert "too large" in res["error"]
    assert client.get("/api/library").json()["total_images"] == 0


def dark_fits() -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'M 42'"),
        ("EXPTIME", "0.0"),
        ("IMAGETYP", "'DARK'"),
        ("NAXIS", "2"),
    ])


def m42_light_fits() -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("BITPIX", "16"),
        ("OBJECT", "'M 42'"),
        ("EXPTIME", "300.0"),
        ("IMAGETYP", "'LIGHT'"),
        ("FILTER", "'L'"),
        ("NAXIS", "2"),
        ("NAXIS1", "4"),
        ("NAXIS2", "4"),
    ]) + b"\x00\x10" * 16


def test_ingest_classification_and_rollups(client):
    """IMAGETYP lands in the right subfolder and per-type/filter rollups."""
    r = client.post("/api/images/ingest", files=[
        ("files", ("m42.fit", m42_light_fits(), "application/octet-stream")),
        ("files", ("dark.fit", dark_fits(), "application/octet-stream")),
    ])
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["ok"] and res[0]["imagetyp"] == "light"
    assert "lights" in res[0]["stored"]
    assert res[1]["ok"] and res[1]["imagetyp"] == "dark"
    assert "darks" in res[1]["stored"]
    assert (tmp_archive(client) / res[1]["stored"]).exists()

    lib = client.get("/api/library").json()
    g = lib["objects"][0]
    assert g["types"]["light"]["n"] == 1
    assert g["types"]["dark"]["n"] == 1
    assert g["filters"]["L"]["n"] == 1
    assert g["filters"]["L"]["exposure"] == 300.0


def m31_at(date_obs: str) -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'M 31                     ' / Andromeda"),
        ("EXPTIME", "300.0"),
        ("DATE-OBS", f"'{date_obs}'"),
        ("SITELAT", "50.0383"),
        ("SITELONG", "7.81667"),
        ("FILTER", "'L'"),
        ("NAXIS", "2"),
        ("RA", "0.712"),
        ("DEC", "41.269"),
    ])


def m31_frame(date_obs: str, imagetyp: str, exposure: float = 60.0) -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'M 31                     ' / Andromeda"),
        ("EXPTIME", str(exposure)),
        ("DATE-OBS", f"'{date_obs}'"),
        ("SITELAT", "50.0383"),
        ("SITELONG", "7.81667"),
        ("IMAGETYP", f"'{imagetyp}'"),
        ("NAXIS", "2"),
        ("RA", "0.712"),
        ("DEC", "41.269"),
    ])


def test_import_batches_recorded(client):
    """One history entry per (object, session, frame) group, newest first."""
    files = [
        ("f1.fit", m31_at("2026-08-22T20:00:00"), "application/octet-stream"),
        ("f2.fit", m31_at("2026-08-22T21:00:00"), "application/octet-stream"),
        ("d1.fit", m31_frame("2026-08-23T13:00:00", "DARK"),
         "application/octet-stream"),
    ]
    r = client.post("/api/images/ingest",
                    files=[("files", x) for x in files])
    assert r.status_code == 200
    batches = client.get("/api/library/imports").json()
    assert len(batches) == 2
    assert batches[0]["frame"] == "dark" and batches[1]["frame"] == "light"
    assert batches[1]["n_files"] == 2
    assert batches[1]["total_exposure"] == 600.0
    assert batches[1]["slug"] == "Andromeda Galaxy"
    assert batches[1]["session"].startswith("night_2026-08-22_")
    assert batches[1]["object_id"] == "ngc224"
    assert batches[1]["image_dir"] == str(tmp_archive(client))


@pytest.mark.parametrize("layout", ["modern", "legacy", "flat"])
@pytest.mark.parametrize("frame", ["dark", "flat", "bias"])
def test_selected_session_calibration_keeps_capture_date_and_object(client, db, layout, frame):
    from app import imglib
    from app.database import normalize_session_date_obs
    base = tmp_archive(client)
    relative = {"modern": "M31/custom-session/lights/anchor.fit",
                "legacy": "M31/lights/anchor.fit",
                "flat": "M31/anchor.fit"}[layout]
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(m31_fits())
    anchor_date = "2026-08-22T23:14:05"
    anchor_id = db.add_image({"file": relative, "orig_name": "anchor.fit",
                              "object_id": "ngc224", "object_name": "Andromeda Galaxy",
                              "date_obs": anchor_date, "imagetyp": "light",
                              "ingested_at": anchor_date})
    # A later capture from the same object must remain a separate session,
    # even when the imported calibration's real DATE-OBS matches that night.
    later_id = db.add_image({"file": "M31/later/lights/second.fit",
                             "orig_name": "second.fit", "object_id": "ngc224",
                             "object_name": "Andromeda Galaxy", "imagetyp": "light",
                             "date_obs": "2026-08-31T21:00:00", "ingested_at": anchor_date})
    payload = make_fits([("SIMPLE", "T"), ("OBJECT", "'M42'"),
                         ("IMAGETYP", "'LIGHT'"), ("EXPTIME", "15"),
                         ("DATE-OBS", "'2026-08-31T21:00:00'")]) + frame.encode()
    response = client.post("/api/images/ingest", files=[
        ("files", ("calibration.fit", payload, "application/octet-stream"))],
        data={"frame": frame, "session_anchor_id": str(anchor_id)})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["ok"] and result["object"]["id"] == "ngc224"
    record = db.image(result["id"])
    assert record["object_name"] == "Andromeda Galaxy" and record["imagetyp"] == frame
    assert record["date_obs"] == "2026-08-31T21:00:00"
    assert record["session_date_obs"] == normalize_session_date_obs(anchor_date)
    parts = record["file"].split("/")
    assert len(parts) == 4 and parts[0] == "M31"
    assert parts[1] == ("custom-session" if layout == "modern" else "unassigned_2026-08-22")
    assert parts[-2] == {"dark": "darks", "flat": "flats", "bias": "bias"}[frame]
    assert (base / record["file"]).read_bytes() == payload
    assert path.read_bytes() == m31_fits()
    sessions = imglib.compute_sessions(db.images())
    selected = next(session for session in sessions if anchor_id in session["image_ids"])
    assert set(selected["image_ids"]) == {anchor_id, result["id"]}
    assert len(sessions) == 2 and later_id not in selected["image_ids"]
    assert not list(base.rglob("*.part"))


def test_selected_undated_session_calibration_missing_object(client, db):
    anchor_id = db.add_image({"file": "Unknown/lights/anchor.fit", "orig_name": "anchor.fit",
                              "object_name": "Unknown", "date_obs": None,
                              "imagetyp": "light", "ingested_at": "2026-08-22T21:00:00"})
    payload = make_fits([("SIMPLE", "T"), ("EXPTIME", "1"),
                         ("DATE-OBS", "'2026-09-01T01:00:00'")])
    response = client.post("/api/images/ingest", files=[
        ("files", ("bias.fit", payload, "application/octet-stream"))],
        data={"frame": "bias", "session_anchor_id": str(anchor_id)})
    result = response.json()["results"][0]
    assert result["ok"] and result["object"]["name"] == "Unknown"
    record = db.image(result["id"])
    assert record["session_date_obs"] == "" and record["date_obs"] == "2026-09-01T01:00:00"
    assert record["file"] == "Unknown/undated/bias/bias.fit"
    sessions = client.get("/api/library").json()["objects"][0]["sessions"]
    assert len(sessions) == 1 and sessions[0]["undated"]
    assert set(sessions[0]["image_ids"]) == {anchor_id, result["id"]}


@pytest.mark.parametrize("frame,anchor,session", [
    ("light", "existing", ""), ("", "existing", ""),
    ("dark", "missing", ""), ("bias", "existing", "arbitrary"),
    ("flat", "unsafe", ""),
])
def test_selected_session_target_rejected_before_staging(client, db, monkeypatch,
                                                        frame, anchor, session):
    from app import ingest
    anchor_id = db.add_image({"file": "../outside.fit" if anchor == "unsafe" else
                              "Target/lights/anchor.fit", "orig_name": "anchor.fit",
                              "object_name": "Target", "imagetyp": "light",
                              "date_obs": "2026-08-22T21:00:00", "ingested_at": ""})
    def forbidden(*args):
        raise AssertionError("invalid session target must not create a stage")
    monkeypatch.setattr(ingest, "_prepare_stage", forbidden)
    response = client.post("/api/images/ingest", files=[
        ("files", ("cal.fit", m31_fits(), "application/octet-stream"))],
        data={"frame": frame, "session_anchor_id": str(anchor_id + 999 if anchor == "missing"
                                                     else anchor_id), "session": session})
    assert response.status_code == 400 and len(db.images()) == 1
    assert not list(tmp_archive(client).rglob("*.part"))


@pytest.mark.parametrize("anchor_id", [0, -1, 1 << 63, 10 ** 200])
def test_selected_session_anchor_id_range_rejected_before_lookup_or_staging(
        client, db, monkeypatch, anchor_id):
    from app import ingest

    def forbidden(*args):
        raise AssertionError("invalid anchor id must not query SQLite or create a stage")

    monkeypatch.setattr(db, "image", forbidden)
    monkeypatch.setattr(ingest, "_prepare_stage", forbidden)
    response = client.post("/api/images/ingest", files=[
        ("files", ("cal.fit", m31_fits(), "application/octet-stream"))],
        data={"frame": "dark", "session_anchor_id": str(anchor_id)})
    assert response.status_code == 400 and "anchor" in response.json()["detail"]
    assert not db.images() and not list(tmp_archive(client).rglob("*.part"))


def test_selected_session_calibration_duplicate_keeps_existing_assignment(client, db):
    payload = make_fits([("SIMPLE", "T"), ("OBJECT", "'Other target'"),
                         ("IMAGETYP", "'FLAT'"), ("DATE-OBS", "'2026-09-01T01:00:00'")])
    first = client.post("/api/images/ingest", files=[
        ("files", ("original.fit", payload, "application/octet-stream"))]).json()["results"][0]
    anchor = client.post("/api/images/ingest", files=[
        ("files", ("light.fit", m31_fits(), "application/octet-stream"))]).json()["results"][0]
    original = db.image(first["id"])
    response = client.post("/api/images/ingest", files=[
        ("files", ("renamed.fit", payload, "application/octet-stream"))],
        data={"frame": "dark", "session_anchor_id": str(anchor["id"])})
    duplicate = response.json()["results"][0]
    assert duplicate["skipped"] and duplicate["id"] == first["id"]
    assert db.image(first["id"]) == original and len(db.images()) == 2
    assert db.image(first["id"])["session_date_obs"] is None
    assert len(db.import_batches()) == 2


def test_ingest_session_override(client):
    """session=... forces the archive sub-folder for calibration uploads."""
    r = client.post("/api/images/ingest",
                    files=[("files", ("d1.fit", m31_frame(
                        "2026-08-25T10:00:00", "DARK"),
                        "application/octet-stream"))],
                    data={"frame": "dark", "session": "2026-08-22_2000"})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] and res["imagetyp"] == "dark"
    parts = res["stored"].replace("\\", "/").split("/")
    assert parts[1] == "2026-08-22_2000" and parts[2] == "darks"
    assert (tmp_archive(client) / res["stored"]).is_file()
    b = client.get("/api/library/imports").json()[0]
    assert b["session"] == "2026-08-22_2000"

    # a bogus session name falls back to the automatic session
    r2 = client.post("/api/images/ingest",
                     files=[("files", ("d2.fit", m31_frame(
                         "2026-08-25T10:01:00", "DARK"),
                         "application/octet-stream"))],
                     data={"session": "../escape"})
    res2 = r2.json()["results"][0]
    assert res2["ok"]
    assert ".." not in res2["stored"].replace("\\", "/").split("/")[1]


def test_open_folder(client, monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", lambda p: opened.append(str(p)))
    base = tmp_archive(client)
    (base / "M 42" / "2026-08-22_2000").mkdir(parents=True)
    r = client.post("/api/library/open_folder",
                    json={"rel": "M 42/2026-08-22_2000"})
    assert r.status_code == 200
    assert opened and "M 42" in opened[0]
    assert client.post("/api/library/open_folder",
                       json={"rel": "../escape"}).status_code == 400
    assert client.post("/api/library/open_folder",
                       json={"rel": "M 42/nope"}).status_code == 404


def test_open_folder_rejects_malformed_paths_and_keeps_the_root_open(client, monkeypatch):
    """F-13: the route goes through the shared safe_join rail, so control
    characters and device names are a clean 400 (a raw NUL used to escape as a
    500), and the archive root itself stays openable via "."."""
    from pathlib import Path

    opened = []
    monkeypatch.setattr("os.startfile", lambda p: opened.append(str(p)))
    base = tmp_archive(client)
    base.mkdir(parents=True, exist_ok=True)
    assert client.post("/api/library/open_folder",
                       json={"rel": "a\x00b"}).status_code == 400
    assert client.post("/api/library/open_folder",
                       json={"rel": "CON"}).status_code == 400
    assert client.post("/api/library/open_folder",
                       json={"rel": "."}).status_code == 200
    assert opened and Path(opened[0]).resolve() == base.resolve()
    assert client.post("/api/library/open_folder",
                       json={"rel": ""}).status_code == 400


def test_rejected_requests_do_not_advance_the_idle_timer(client):
    """F-09: the guard runs OUTERMOST, so a rejected (foreign host / cross-site)
    request never reaches the idle-touch - constant probing cannot keep the
    12-hour idle exit from firing. Allowed requests still advance it."""
    import time as time_mod

    from app import server as server_mod

    before = server_mod._state["last_request"]
    time_mod.sleep(0.01)
    r = client.get("/api/state", headers={"Host": "evil.example"})
    assert r.status_code == 403
    assert server_mod._state["last_request"] == before,         "a rejected request must not reset the idle timer"
    r = client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert server_mod._state["last_request"] == before,         "a cross-site rejection must not reset the idle timer either"
    time_mod.sleep(0.01)
    assert client.get("/api/state").status_code == 200
    assert server_mod._state["last_request"] > before


def test_ingest_session_folders(client):
    """Frames from different nights land in different session sub-folders;
    daylight frames belong to their own solar interval."""
    files = [
        ("f1.fit", m31_at("2026-08-22T20:00:00"), "application/octet-stream"),
        ("f2.fit", m31_at("2026-08-23T13:00:00"), "application/octet-stream"),
        ("f3.fit", m31_at("2026-08-22T21:30:00"), "application/octet-stream"),
    ]
    r = client.post("/api/images/ingest",
                    files=[("files", x) for x in files])
    assert r.status_code == 200
    parts = [x["stored"].replace("\\", "/").split("/")
             for x in r.json()["results"]]
    assert all(len(p) == 4 and p[0] == "Andromeda Galaxy" and p[-2] == "lights"
               for p in parts)
    s1, s2, s3 = (p[1] for p in parts)
    assert s1 == s3 and s1 != s2
    assert s1.startswith("night_") and s2.startswith("day_")
    lib = client.get("/api/library").json()
    g = lib["objects"][0]
    assert g["n_sessions"] == 2


def test_ingest_builds_one_catalog_resolver_per_request(client, monkeypatch):
    original = server_mod.CatalogResolver
    builds = []

    def counted_resolver(catalog):
        builds.append(len(catalog))
        return original(catalog)

    monkeypatch.setattr(server_mod, "CatalogResolver", counted_resolver)
    response = client.post("/api/images/ingest", files=[
        ("files", ("m31.fit", m31_fits(), "application/octet-stream")),
        ("files", ("unknown.fit", unresolved_fits(),
                   "application/octet-stream")),
    ])
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2 and all(r["ok"] for r in results)
    assert results[0]["object"]["id"] == "ngc224"
    assert results[1]["object"]["id"] is None
    assert len(builds) == 1


def test_unresolved_ingest_does_not_join_differently_resolved_same_name(
        client, db):
    from app.solar import session_metadata
    from app.ingest import session_folder_name

    old_date = "2026-08-22T23:00:00"
    db.add_image({
        "file": "TestObj42/old.fit", "orig_name": "old.fit",
        "object_id": "a-different-resolved-id", "object_name": "TestObj42",
        "date_obs": old_date, "ingested_at": old_date,
    })
    new_frame = make_fits([
        ("SIMPLE", "T"), ("OBJECT", "'TestObj42'"),
        ("EXPTIME", "60"), ("DATE-OBS", "'2026-08-23T01:00:00'"),
    ])
    response = client.post("/api/images/ingest", files=[
        ("files", ("new.fit", new_frame, "application/octet-stream"))])
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["ok"] and result["object"]["id"] is None
    assert result["stored"].replace("\\", "/").split("/")[1] == \
        session_folder_name(session_metadata("2026-08-23T01:00:00"))


def test_library_session_rollups(client):
    """Sessions carry their own per-type/filter rollups (undated here)."""
    client.post("/api/images/ingest", files=[
        ("files", ("m42.fit", m42_light_fits(), "application/octet-stream")),
        ("files", ("dark.fit", dark_fits(), "application/octet-stream")),
    ])
    lib = client.get("/api/library").json()
    s = lib["objects"][0]["sessions"][0]
    assert s["undated"] is True
    assert s["types"]["light"]["n"] == 1
    assert s["types"]["dark"]["n"] == 1
    assert s["filters"]["L"]["n"] == 1


def test_ingest_creates_sibling_folders(client):
    """The first ingest also creates the darks/flats/bias folders beside lights."""
    r = client.post("/api/images/ingest",
                    files=[("files", ("m42.fit", m42_light_fits(),
                                      "application/octet-stream"))])
    assert r.status_code == 200
    stored = r.json()["results"][0]["stored"]
    base = tmp_archive(client) / "/".join(
        stored.replace("\\", "/").split("/")[:2])
    for t in ("lights", "darks", "flats", "bias"):
        assert (base / t).is_dir(), f"missing {base / t}"


def test_ingest_frame_override(client):
    """frame=dark forces the darks folder; a bogus value falls back to IMAGETYP."""
    r = client.post("/api/images/ingest",
                    files=[("files", ("m42.fit", m42_light_fits(),
                                      "application/octet-stream"))],
                    data={"frame": "dark"})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["ok"] and res["imagetyp"] == "dark"
    assert res["stored"].replace("\\", "/").split("/")[-2] == "darks"
    assert (tmp_archive(client) / res["stored"]).is_file()

    r2 = client.post("/api/images/ingest",
                     files=[("files", ("m42b.fit", m42_light_fits()[:-2] + b"\x00\x11",
                                       "application/octet-stream"))],
                     data={"frame": "bogus"})
    res2 = r2.json()["results"][0]
    assert res2["ok"] and res2["imagetyp"] == "light"
    assert "lights" in res2["stored"]


def test_reindex_adds_disk_files(client):
    """Files placed into the archive on disk (manual copies, restored
    backups) are picked up by the reindex endpoint; the sub-folder forces
    the frame type and a second run skips everything."""
    base = tmp_archive(client)
    (base / "M 42" / "lights").mkdir(parents=True)
    (base / "M 42" / "darks").mkdir(parents=True)
    (base / "M 42" / "lights" / "m42a.fit").write_bytes(m42_light_fits())
    # header says LIGHT, but the darks folder wins for reindex
    (base / "M 42" / "darks" / "d1.fit").write_bytes(
        m42_light_fits()[:-2] + b"\x00\x11")

    r = client.post("/api/library/reindex")
    assert r.status_code == 200
    res = r.json()
    assert res["found"] == 2 and res["added"] == 2
    lib = client.get("/api/library").json()
    assert lib["total_images"] == 2
    imgs = [i for g in lib["objects"] for i in g["images"]]
    by_typ = {i["imagetyp"]: i for i in imgs}
    assert by_typ["light"]["file"].replace("\\", "/") == "M 42/lights/m42a.fit"
    assert by_typ["dark"]["file"].replace("\\", "/") == "M 42/darks/d1.fit"
    assert by_typ["light"]["object_id"] == "ngc1976"

    r2 = client.post("/api/library/reindex").json()
    assert r2["found"] == 2 and r2["added"] == 0 and r2["skipped"] == 2


def test_reindex_empty_archive(client):
    r = client.post("/api/library/reindex")
    assert r.status_code == 200
    assert r.json() == {"found": 0, "added": 0, "skipped": 0, "failed": 0,
                        "repaired": 0, "conflicts": 0}


@pytest.mark.parametrize("relative", [
    "frame.fit", "NGC 6960_sub/frame.fit", "Object/Session/raw/frame.fit",
    "Object/Session/extra/lights/frame.fit",
])
def test_reindex_refuses_unexpected_fits_folder_structure(client, relative):
    frame = tmp_archive(client) / relative
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(m31_fits())
    response = client.post("/api/library/reindex")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert str(frame.parent.resolve()) in detail
    assert "<object>/<session>/<frame-type>/<file.fit>" in detail
    assert "lights, darks, flats or bias" in detail
    assert "Import Exposures" in detail
    assert client.get("/api/library").json()["total_images"] == 0
    assert frame.read_bytes() == m31_fits()


def test_reindex_preflight_refusal_preserves_entire_index_and_files(client, db, monkeypatch):
    original = client.post("/api/images/ingest", files=[
        ("files", ("known.fit", m31_fits(), "application/octet-stream")),
    ]).json()["results"][0]
    # A successful scan would backfill this known row as well as add the
    # lexically early valid frame. Neither may happen before late validation.
    db.conn.execute("UPDATE images SET content_sha256=NULL, file_size=NULL WHERE id=?",
                    (original["id"],))
    db.conn.commit()
    base = tmp_archive(client)
    for relative in ("A valid/Evening session/lights/new.fit", "Z invalid/flat.fit"):
        frame = base / relative
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(m31_fits()[:-2] + b"\x12\x34")
    before_index = list(db.conn.iterdump())
    before_files = {str(path.relative_to(base)): path.read_bytes()
                    for path in base.rglob("*") if path.is_file()}
    before_library = client.get("/api/library").json()

    def unexpected_hash(_path):
        pytest.fail("Structural preflight must finish before any fingerprint IO")

    monkeypatch.setattr("app.ingest._fingerprint_file", unexpected_hash)
    response = client.post("/api/library/reindex")
    assert response.status_code == 400
    assert "Z invalid" in response.json()["detail"]
    assert list(db.conn.iterdump()) == before_index
    assert client.get("/api/library").json() == before_library
    assert {str(path.relative_to(base)): path.read_bytes()
            for path in base.rglob("*") if path.is_file()} == before_files


@pytest.mark.parametrize("frame_dir,expected", [
    ("LIGHTS", "light"), ("darks", "dark"), ("flats", "flat"), ("bias", "bias"),
])
@pytest.mark.parametrize("session", [None, "Evening session 1"])
def test_reindex_accepts_canonical_and_legacy_frame_folders(client, frame_dir, expected, session):
    parent = tmp_archive(client) / "Object"
    if session:
        parent /= session
    frame = parent / frame_dir / "capture.FITS"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(m31_fits())
    response = client.post("/api/library/reindex")
    assert response.status_code == 200
    assert response.json()["added"] == 1
    library = client.get("/api/library").json()
    assert library["objects"][0]["images"][0]["imagetyp"] == expected


def test_reindex_ignores_empty_and_non_fits_folders(client):
    base = tmp_archive(client)
    (base / "flat source" / "unusual").mkdir(parents=True)
    (base / "notes.txt").write_text("Keep this user file.", encoding="utf-8")
    response = client.post("/api/library/reindex")
    assert response.status_code == 200
    assert response.json() == {"found": 0, "added": 0, "skipped": 0, "failed": 0,
                               "repaired": 0, "conflicts": 0}
    assert (base / "notes.txt").read_text(encoding="utf-8") == "Keep this user file."


def test_ingest_run_id_merges_batches(client):
    """Chunked uploads of one import action (same run_id) merge into a
    single history entry; a different run stays separate."""
    f1 = [("files", ("f1.fit", m31_at("2026-08-22T20:00:00"),
                     "application/octet-stream")),
          ("files", ("f2.fit", m31_at("2026-08-22T21:00:00"),
                     "application/octet-stream"))]
    f2 = [("files", ("f3.fit", m31_at("2026-08-22T22:00:00"),
                     "application/octet-stream"))]
    r1 = client.post("/api/images/ingest", files=f1,
                     data={"run_id": "run-aaaa-0001"})
    r2 = client.post("/api/images/ingest", files=f2,
                     data={"run_id": "run-aaaa-0001"})
    assert r1.status_code == 200 and r2.status_code == 200
    batches = client.get("/api/library/imports").json()
    assert len(batches) == 1
    assert batches[0]["n_files"] == 3
    assert batches[0]["total_exposure"] == 900.0

    another_capture = [
        ("files", ("f4.fit", m31_at("2026-08-22T23:00:00"),
                   "application/octet-stream")),
        ("files", ("f5.fit", m31_at("2026-08-23T01:00:00"),
                   "application/octet-stream")),
    ]
    client.post("/api/images/ingest", files=another_capture,
                data={"run_id": "run-bbbb-0002"})
    batches = client.get("/api/library/imports").json()
    assert len(batches) == 2
    assert batches[0]["n_files"] == 2

    # Retrying a completed import must not create another history entry.
    retry = client.post("/api/images/ingest", files=f1,
                        data={"run_id": "run-cccc-0003"})
    assert all(result["skipped"] for result in retry.json()["results"])
    assert client.get("/api/library/imports").json() == batches
    assert client.get("/api/library").json()["total_images"] == 5


def test_ingest_duplicate_bytes_are_skipped_across_names_and_overrides(client):
    payload = m31_at("2026-08-22T20:00:00")
    first = client.post("/api/images/ingest", files=[
        ("files", ("original.fit", payload, "application/octet-stream")),
        ("files", ("renamed.fits", payload, "application/octet-stream")),
    ])
    assert first.status_code == 200
    results = first.json()["results"]
    assert results[0]["ok"] and not results[0].get("skipped")
    assert results[1]["ok"] and results[1]["skipped"]
    assert results[1]["reason"] == "already_imported"
    assert results[0]["stored"] == results[1]["stored"]
    history = client.get("/api/library/imports").json()
    assert len(history) == 1 and history[0]["n_files"] == 1

    repeated = client.post("/api/images/ingest", files=[
        ("files", ("another-name.fit", payload, "application/octet-stream")),
    ], data={"frame": "dark", "session": "different-session"})
    duplicate = repeated.json()["results"][0]
    assert duplicate["ok"] and duplicate["skipped"]
    assert duplicate["imagetyp"] == "light"
    assert duplicate["stored"] == results[0]["stored"]
    assert client.get("/api/library/imports").json() == history
    library = client.get("/api/library").json()
    assert library["total_images"] == 1 and library["total_exposure"] == 300.0
    assert len(list(tmp_archive(client).rglob("*.fit"))) == 1


def test_missing_original_repair_preserves_library_totals_and_history(client):
    payload = m31_fits()
    first = client.post("/api/images/ingest", files=[
        ("files", ("source.fit", payload, "application/octet-stream")),
    ]).json()["results"][0]
    before = client.get("/api/library").json()
    history = client.get("/api/library/imports").json()
    (tmp_archive(client) / first["stored"]).unlink()
    repaired = client.post("/api/images/ingest", files=[
        ("files", ("replacement.fit", payload, "application/octet-stream")),
    ]).json()["results"][0]
    assert repaired["ok"] and repaired["restored"] and not repaired["skipped"]
    assert repaired["id"] == first["id"]
    assert client.get("/api/library").json() == before
    assert client.get("/api/library/imports").json() == history
    assert (tmp_archive(client) / first["stored"]).read_bytes() == payload


def test_library_and_json_export_exclude_local_fingerprint_bookkeeping(client):
    client.post("/api/images/ingest", files=[
        ("files", ("source.fit", m31_fits(), "application/octet-stream")),
    ])
    hidden = {"content_sha256", "file_size", "file_mtime_ns"}
    for route in ("/api/library", "/api/library/export?format=json"):
        payload = client.get(route).json()
        assert payload["total_images"] == 1
        image = payload["objects"][0]["images"][0]
        assert not hidden.intersection(image)
        assert image["orig_name"] == "source.fit"


def test_ingest_same_filename_different_pixels_retains_both_captures(client):
    original = m42_light_fits()
    different = original[:-2] + b"\x00\x11"
    results = client.post("/api/images/ingest", files=[
        ("files", ("capture.fit", original, "application/octet-stream")),
        ("files", ("capture.fit", different, "application/octet-stream")),
    ]).json()["results"]
    assert all(result["ok"] and not result.get("skipped") for result in results)
    assert results[0]["stored"] != results[1]["stored"]
    assert (tmp_archive(client) / results[0]["stored"]).read_bytes() == original
    assert (tmp_archive(client) / results[1]["stored"]).read_bytes() == different
    assert client.get("/api/library").json()["total_images"] == 2


def test_reindex_duplicate_content_keeps_file_without_second_library_row(client):
    initial = client.post("/api/images/ingest", files=[
        ("files", ("source.fit", m31_fits(), "application/octet-stream")),
    ]).json()["results"][0]
    copied = tmp_archive(client) / "Manual" / "lights" / "manual-copy.fits"
    copied.parent.mkdir(parents=True)
    copied.write_bytes((tmp_archive(client) / initial["stored"]).read_bytes())
    result = client.post("/api/library/reindex").json()
    assert result["added"] == 0 and result["skipped"] == 2
    assert copied.is_file()
    assert client.get("/api/library").json()["total_images"] == 1


def tmp_archive(client) -> Path:
    import json as _json
    from pathlib import Path as _Path
    st = client.get("/api/state").json()
    return _Path(st["image_dir"])


# ------------------------------------------------- stored-path trust (C2)

def _crafted_pano(db, pid: str, file_value: str):
    """Simulate a database that holds an unsafe path - exactly what a crafted
    setup ZIP used to plant before import validation existed."""
    db.conn.execute("INSERT INTO panoramas(id,name,file) VALUES(?,?,?)",
                    (pid, "crafted", file_value))
    db.conn.commit()


def _crafted_image(db, file_value: str):
    cur = db.conn.execute(
        "INSERT INTO images(file,orig_name,object_name,ingested_at) "
        "VALUES(?,?,?,?)", (file_value, "x.fit", "X", "2026-08-24T00:00:00"))
    db.conn.commit()
    return cur.lastrowid


def test_image_preview_never_leaves_the_archive(client, db, tmp_path):
    secret = tmp_path / "secret.fit"
    secret.write_bytes(m42_light_fits())
    img_id = _crafted_image(db, "../secret.fit")

    assert client.get(f"/api/images/{img_id}/preview").status_code == 404


# ------------------------------------------------------------------ bortle

# ------------------------------------------------------------------ library export

def test_library_export(client):
    client.post("/api/images/ingest",
                files=[("files", ("m31.fit", m31_fits(),
                                  "application/octet-stream"))])
    r = client.get("/api/library/export?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert b"m31" in r.content.lower() or b"m 31" in r.content
    r = client.get("/api/library/export?format=json")
    assert r.status_code == 200
    j = r.json()
    assert j["total_images"] == 1
    assert j["objects"][0]["images"][0]["imagetyp"] == "light"
    assert client.get("/api/library/export?format=xlsx").status_code == 400


@pytest.mark.parametrize("format", ["csv", "json"])
def test_library_export_object_uses_grouped_name_and_scoped_totals(client, format):
    import csv

    for name, payload, frame in (
        ("andromeda.fit", m31_fits(300), "light"),
        ("calibration.fit", m31_fits(75), "dark"),
        ("orion.fit", m42_light_fits(), "light"),
    ):
        response = client.post("/api/images/ingest", data={"frame": frame},
            files=[("files", (name, payload, "application/octet-stream"))])
        assert response.json()["results"][0]["ok"]
    library = client.get("/api/library").json()
    selected = next(obj for obj in library["objects"] if obj["object_id"] == "ngc224")
    expected_ids = {image["id"] for image in selected["images"]}
    assert selected["name"] != "M 31"
    response = client.get("/api/library/export", params={
        "format": format, "object_name": selected["name"]})
    assert response.status_code == 200
    assert f'nightseam-object.{format}' in response.headers["content-disposition"]
    global_response = client.get("/api/library/export", params={"format": format})
    assert global_response.status_code == 200
    assert f'nightseam-library.{format}' in global_response.headers["content-disposition"]
    if format == "json":
        scoped = response.json()
        assert scoped["total_images"] == 2
        assert scoped["total_exposure"] == 375
        assert len(scoped["objects"]) == 1
        assert scoped["objects"][0]["name"] == selected["name"]
        assert {image["id"] for image in scoped["objects"][0]["images"]} == expected_ids
        for image in scoped["objects"][0]["images"]:
            assert not {"content_sha256", "file_size", "file_mtime_ns"}.intersection(image)
        global_payload = global_response.json()
        assert global_payload == library
        assert global_payload["total_images"] == library["total_images"] == 3
        assert global_payload["total_exposure"] == library["total_exposure"]
        assert len(global_payload["objects"]) == 2
    else:
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert {int(row["id"]) for row in rows} == expected_ids
        assert {row["orig_name"] for row in rows} == {"andromeda.fit", "calibration.fit"}
        assert {row["imagetyp"] for row in rows} == {"light", "dark"}
        assert sum(float(row["exposure"]) for row in rows) == 375
        assert not {"content_sha256", "file_size", "file_mtime_ns"}.intersection(rows[0])
        global_rows = list(csv.DictReader(io.StringIO(global_response.content.decode("utf-8-sig"))))
        assert len(global_rows) == 3
        assert "orion.fit" in {row["orig_name"] for row in global_rows}
    # Neither a FITS alias nor a case-folded/partial match names the active group.
    for nonmatching in ("M 31", selected["name"].swapcase(), "nonexistent target"):
        assert client.get("/api/library/export", params={
            "format": format, "object_name": nonmatching}).status_code == 404


@pytest.mark.parametrize("format", ["csv", "json"])
def test_library_export_object_accepts_encoded_unicode_name(client, db, format):
    import csv
    from urllib.parse import quote

    name = "Cœur + 螺旋 & field #1"
    selected_id = db.add_image({"file": "unicode/undated/lights/capture.fit",
        "orig_name": "capture.fit", "object_id": None, "object_name": name,
        "exposure": 12.5, "imagetyp": "light", "content_sha256": "a" * 64,
        "file_size": 1, "file_mtime_ns": 1, "ingested_at": "2026-10-01T12:00:00"})
    db.add_image({"file": "other/undated/lights/other.fit", "orig_name": "other.fit",
        "object_id": None, "object_name": "Other target", "exposure": 90,
        "ingested_at": "2026-10-01T12:00:00"})
    response = client.get(f"/api/library/export?format={format}&object_name={quote(name, safe='')}")
    assert response.status_code == 200
    if format == "json":
        payload = response.json()
        assert payload["total_images"] == 1 and payload["total_exposure"] == 12.5
        assert payload["objects"][0]["name"] == name
        assert payload["objects"][0]["images"][0]["id"] == selected_id
        assert "content_sha256" not in payload["objects"][0]["images"][0]
    else:
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert len(rows) == 1
        assert rows[0]["object_name"] == name and int(rows[0]["id"]) == selected_id
    assert client.get("/api/library/export", params={"format": format,
        "object_name": name + " "}).status_code == 404


# ------------------------------------------------------------------ panoramas

# ------------------------------------------------------------------ export / import

def test_origin_null_is_rejected(client):
    """PY6 (review): `Origin: null` skips the host comparison - it never
    belongs to a legitimate local app. No-Origin clients stay allowed."""
    assert client.get("/api/state",
                      headers={"Origin": "null"}).status_code == 403
    assert client.get("/api/state").status_code == 200


# -------------------------------------------------- request guard (middleware)
# The guard is what stops any website the user visits from reaching this local
# API (a no-cors request to 127.0.0.1 is not blocked by CORS). It covers EVERY
# route, so it is exercised here on a harmless GET and on a state-changing POST.

def test_cross_site_requests_are_rejected(client):
    assert client.get("/api/state").status_code == 200

    r = client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and "cross-site" in r.json()["detail"]

    r = client.post("/api/settings", json={"size_unit": "deg"},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    # ... and the rejected request really did not change anything
    assert client.get("/api/state").json()["size_unit"] != "deg"

    r = client.get("/api/state", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403

    # same-origin and non-browser callers (no headers) stay allowed
    assert client.get("/api/state",
                      headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.get("/api/state",
                      headers={"Origin": "http://testserver"}).status_code == 200


def test_foreign_host_header_is_rejected(client):
    r = client.get("/api/state", headers={"Host": "evil.example"})
    assert r.status_code == 403 and r.json()["detail"] == "forbidden host"


# ------------------------------------------------------------- upload limits
# The per-file cap inside ingest can only run once Starlette has spooled the
# whole body, so the middleware rejects an oversized request from its declared
# length first. The limits are module constants so the mechanism is testable
# without sending gigabytes.

def test_too_many_files_in_one_ingest_request(client, monkeypatch):
    monkeypatch.setattr("app.server.MAX_FILES_PER_REQUEST", 2)
    files = [("files", (f"m{i}.fit", m31_fits(), "application/octet-stream"))
             for i in range(3)]
    r = client.post("/api/images/ingest", files=files)
    assert r.status_code == 400
    assert "too many files" in r.json()["detail"]
    monkeypatch.setattr("app.server.MAX_FILES_PER_REQUEST", 8)
    r = client.post("/api/images/ingest", files=files[:2])
    assert r.status_code == 200


# --------------------------------------------------- panorama profile shapes

# ------------------------------------------------------------- site validation

# ------------------------------------------------------------ UI cache policy

def test_ui_assets_are_never_cached(client):
    """A stale themes.css/style.css next to a fresh index.html breaks the UI -
    themes.css was missing from the no-store list."""
    for path in ("/", "/catalog.html", "/style.css", "/themes.css",
                 "/favicon.ico", "/js/util.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-store", path
