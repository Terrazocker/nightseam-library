"""HTTP-level API tests via fastapi TestClient (httpx).

These exercise the full route layer of create_app(): validation, error
codes, ingest, and the export/import round trip. The app is built with
prefetch=False so no image-cache thread hits the network.
"""

import io
import json
import math
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import server as server_mod
from app.database import Database
from app.server import create_app
from _util import make_fits, m31_fits, unresolved_fits

from app.catalog_data import STANDARD_TOP_N

_DATE = "2026-08-24"
_TZ = 120


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "nightseam.db")


@pytest.fixture()
def client(db, tmp_path, monkeypatch):
    # pano uploads/replaces write to data_dir()/"panos" - point that at the tmp
    # dir so a test run can never drop files into the repo's real data/ folder.
    # The real data_dir() creates the panos sub-folder, so mirror that here.
    data = tmp_path / "data"
    (data / "panos").mkdir(parents=True)
    monkeypatch.setattr("app.server.data_dir", lambda: data)
    with TestClient(create_app(db, prefetch=False)) as c:
        c.post("/api/settings", json={"image_dir": str(tmp_path / "archive")})
        yield c


def add_site(c, name="Backyard", lat=50.0, lon=7.8):
    r = c.post("/api/sites", json={"name": name, "lat": lat, "lon": lon, "elev": 150})
    assert r.status_code == 200
    return r.json()["id"]


# ------------------------------------------------------------------ state / settings

def test_state_defaults(client):
    st = client.get("/api/state").json()
    assert st["version"]
    assert st["sites"] == []
    assert st["adv_mode"] is False
    assert st["min_useful_alt"] == 25.0
    assert st["profile_len"] == 720
    assert st["image_dir"]  # defaults to a real path


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={
        "min_useful_alt": 30.0, "moon_aware": True,
        "favorites": ["m31", "m42"],
        "filters": {"broadband": False, "narrowband_ha": True,
                    "narrowband_oiii": False}})
    assert r.status_code == 200
    st = client.get("/api/state").json()
    assert st["min_useful_alt"] == 30.0
    assert st["moon_aware"] is True
    assert st["favorites"] == ["m31", "m42"]
    assert st["filters"] == {"broadband": False, "narrowband_ha": True,
                             "narrowband_oiii": False}


def test_timezone_setting_roundtrip(client):
    r = client.post("/api/settings", json={"timezone": "America/Santiago"})
    assert r.status_code == 200
    assert client.get("/api/state").json()["timezone"] == "America/Santiago"
    r = client.post("/api/settings", json={"timezone": ""})
    assert r.status_code == 200
    assert client.get("/api/state").json()["timezone"] == ""


def test_min_vis_setting_roundtrips_and_clamps(client):
    """`min_vis` is the target list's Min-visibility gate (0.6.30): a percentage
    persisted like every other display setting. /api/state must carry it (the
    desktop's filter bar is seeded from it) with a default of 1 and a 0..100
    clamp, so a hand-edited settings row cannot wedge the slider."""
    assert client.get("/api/state").json()["min_vis"] == 1        # default
    assert client.post("/api/settings", json={"min_vis": 42}).status_code == 200
    assert client.get("/api/state").json()["min_vis"] == 42
    assert client.post("/api/settings", json={"min_vis": 0}).status_code == 200
    assert client.get("/api/state").json()["min_vis"] == 0        # 0 = gate off
    assert client.post("/api/settings", json={"min_vis": 150}).status_code == 200
    assert client.get("/api/state").json()["min_vis"] == 100
    assert client.post("/api/settings", json={"min_vis": -5}).status_code == 200
    assert client.get("/api/state").json()["min_vis"] == 0
    # it survives a reopen (the desktop reads it back at boot)
    st = client.get("/api/state").json()
    assert isinstance(st["min_vis"], int)


def test_settings_rejects_bad_size_unit(client):
    r = client.post("/api/settings", json={"size_unit": "parsecs"})
    assert r.status_code == 400


def test_retired_sky_brightness_setting_is_ignored(client):
    """Old clients may still submit the removed preference; it must not be
    exposed or stored again."""
    assert "sky_brightness_provider" not in client.get("/api/state").json()
    assert client.post("/api/settings", json={
        "sky_brightness_provider": "unknown"}).status_code == 200
    assert "sky_brightness_provider" not in client.get("/api/state").json()


# ------------------------------------------------------------------ sites / rigs

def test_sites_crud_and_first_is_active(client):
    sid = add_site(client)
    assert client.get("/api/state").json()["active_site_id"] == sid
    r = client.put(f"/api/sites/{sid}",
                   json={"name": "Renamed", "lat": 1.0, "lon": 2.0, "elev": 0})
    assert r.status_code == 200
    assert client.get("/api/sites").json()[0]["name"] == "Renamed"
    assert client.delete(f"/api/sites/{sid}").status_code == 200
    assert client.get("/api/sites").json() == []


def test_rigs_crud_and_fov(client):
    r = client.post("/api/rigs", json={"name": "Seestar",
                                       "focal_mm": 150.0,
                                       "sensor_w": 6.0, "sensor_h": 4.0})
    assert r.status_code == 200
    body = r.json()
    assert body["fov_w"] > body["fov_h"] > 0
    assert client.delete(f"/api/rigs/{body['id']}").status_code == 200
    r = client.post("/api/rigs", json={"name": "Bad",
                                       "focal_mm": -5.0,
                                       "sensor_w": 6.0, "sensor_h": 4.0})
    assert r.status_code == 400


# ---------------------------------------------------- planning lives in core/
# Phase C removed the server planning routes (/api/night, /api/object/{oid}):
# the desktop and the web both compute from core/ client-side. The payload
# contracts are pinned by tests/js/night_race.mjs, tests/js/core_fixtures.mjs
# and the derive engines - plus the frozen fixtures under tests/fixtures/.


def test_removed_planning_routes_answer_404(client):
    """A stale client must fail loudly, not reach a half-alive compute route."""
    add_site(client)
    assert client.get("/api/night", params={
        "date": _DATE, "tz_offset_min": _TZ}).status_code == 404
    assert client.get("/api/object/m31", params={
        "date": _DATE, "tz_offset_min": _TZ}).status_code == 404
    # the image IO route is NOT planning and stays (for an existing object)
    assert client.get("/api/object/ngc224/image", params={
        "thumb": 1}).status_code == 200


# ------------------------------------------------------------------ catalog / customs

def test_catalog_counts(client, db):
    plain = client.get("/api/catalog").json()
    assert len(plain) == STANDARD_TOP_N
    adv = client.get("/api/catalog", params={"adv": "1"}).json()
    assert len(adv) == STANDARD_TOP_N + db.advanced_count()
    assert all("_adv" not in o for o in adv)


def test_custom_target_crud(client):
    r = client.post("/api/catalog/custom", json={
        "name": "My Nebula", "ra": "5h 34.5m", "dec": "+22 01"})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert cid.startswith("u_")
    catalog = client.get("/api/catalog").json()
    mine = next(o for o in catalog if o["id"] == cid)
    assert mine["primary_name"] == "My Nebula"
    assert 5.5 < mine["ra_hours"] < 5.6
    # builtin objects cannot be deleted
    assert client.delete("/api/catalog/custom/m31").status_code == 400
    assert client.delete(f"/api/catalog/custom/{cid}").status_code == 200
    assert not any(o["id"] == cid
                   for o in client.get("/api/catalog").json())


def test_custom_target_rejects_bad_ra(client):
    r = client.post("/api/catalog/custom", json={
        "name": "Bad", "ra": "99h", "dec": "+22"})
    assert r.status_code == 400


def test_custom_target_comma_decimals(client):
    """RA/Dec with comma decimals must parse (German locale)."""
    r = client.post("/api/catalog/custom", json={
        "name": "Comma Neb", "ra": "5,5", "dec": "+22,5", "vmag": 9.5})
    assert r.status_code == 200
    cid = r.json()["id"]
    mine = next(o for o in client.get("/api/catalog").json()
                if o["id"] == cid)
    assert 5.49 < mine["ra_hours"] < 5.51
    assert 22.49 < mine["dec_deg"] < 22.51
    assert mine["vmag"] == 9.5
    assert client.delete(f"/api/catalog/custom/{cid}").status_code == 200


def test_custom_target_rejects_non_finite_vmag(client, db):
    """T6-F1 (review of t6, high): the LIVE create route was unguarded.

    POST /api/catalog/custom with `vmag: 1e400` (a valid strict-JSON number
    literal that Python parses to inf) - or with the bare `Infinity` token -
    returned 200 and stored custom_objects.vmag = inf. GET /api/catalog then
    answered 500 on EVERY later request (Starlette serializes with
    allow_nan=False) and the desktop shell fetches that route at boot. This is
    the certified PY-04 failure mode reached with no FITS file and no bundle.
    """
    shapes = {
        "1e400 literal": '{"name":"Zzz","ra":"1.0","dec":"1.0","vmag":1e400}',
        "bare Infinity token":
            '{"name":"Zzz","ra":"1.0","dec":"1.0","vmag":Infinity}',
        "bare -Infinity token":
            '{"name":"Zzz","ra":"1.0","dec":"1.0","vmag":-Infinity}',
        "bare NaN token": '{"name":"Zzz","ra":"1.0","dec":"1.0","vmag":NaN}',
    }
    for label, body in shapes.items():
        r = client.post("/api/catalog/custom", content=body,
                        headers={"Content-Type": "application/json"})
        assert 400 <= r.status_code < 500, (label, r.status_code, r.text)
        # nothing was stored ...
        assert not any(c["primary_name"] == "Zzz" for c in db.customs()), label
        for c in db.customs():
            for key in ("vmag", "ra_hours", "dec_deg"):
                assert c[key] is None or math.isfinite(float(c[key])), (label, c)
        # ... and the route the desktop awaits at boot still answers
        cat = client.get("/api/catalog")
        assert cat.status_code == 200, (label, cat.status_code)
        text = cat.content.decode("utf-8")
        assert "Infinity" not in text and "NaN" not in text, label


def test_database_add_custom_refuses_non_finite_numbers(db):
    """The storage boundary holds for ANY caller, not just the route: a
    non-finite vmag/ra/dec is refused instead of persisted. A refusal (not a
    silent NULL) is the right shape here - silently nulling would change what
    the caller asked to store."""
    for key, bad in (("vmag", float("inf")), ("vmag", float("nan")),
                     ("ra_hours", float("inf")), ("dec_deg", float("-inf"))):
        with pytest.raises(ValueError, match="finite"):
            db.add_custom({"id": "u_bad", "primary_name": "Bad target",
                           "otype": "galaxy", "ra_hours": 1.0, "dec_deg": 1.0,
                           "vmag": 9.5, key: bad})
    assert not any(c["primary_name"] == "Bad target" for c in db.customs())


def test_custom_target_accepts_large_finite_vmag(client, db):
    """The fix must not be a blanket refusal: legitimate large-but-finite
    magnitudes still create the target."""
    for vmag in (30.0, 99.9):
        r = client.post("/api/catalog/custom", json={
            "name": f"Faint {vmag}", "ra": "1.0", "dec": "1.0", "vmag": vmag})
        assert r.status_code == 200, (vmag, r.status_code, r.text)
        stored = next(c for c in db.customs()
                      if c["primary_name"] == f"Faint {vmag}")
        assert stored["vmag"] == vmag
        assert math.isfinite(float(stored["vmag"]))
    assert client.get("/api/catalog").status_code == 200


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


def test_restore_payload_commit_serializes_duplicate_finalization(client, db, monkeypatch):
    from app import backup
    payload = unresolved_fits()
    first = client.post("/api/images/ingest", files=[
        ("files", ("source.fit", payload, "application/octet-stream")),
    ]).json()["results"][0]
    blob = client.get("/api/export").content
    changed = payload + b"different restored pixels"
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for member in source.namelist():
            contents = changed if member.startswith("library/") else source.read(member)
            target.writestr(member, contents)
    lock = _ObservedArchiveLock(db.archive_lock)
    monkeypatch.setattr(db, "archive_lock", lock)
    entered, release = threading.Event(), threading.Event()
    original = backup._PayloadStage.commit

    def paused(stage):
        assert not db.lock._is_owned()
        entered.set()
        assert release.wait(8)
        return original(stage)

    monkeypatch.setattr(backup._PayloadStage, "commit", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(client.post, "/api/import", files={
            "file": ("restore.zip", rebuilt.getvalue(), "application/zip")})
        try:
            assert entered.wait(3)
            duplicate = pool.submit(client.post, "/api/images/ingest", files=[
                ("files", ("renamed.fit", changed, "application/octet-stream")),
            ])
            assert lock.contended.wait(3)
            assert not duplicate.done()
            assert client.get("/api/library").json()["total_images"] == 1
            assert (tmp_archive(client) / first["stored"]).read_bytes() == payload
        finally:
            release.set()
        assert restore.result(timeout=5).status_code == 200
        assert duplicate.result(timeout=5).json()["results"][0]["skipped"]
    assert len(db.images()) == 1
    assert (tmp_archive(client) / first["stored"]).read_bytes() == changed
    assert client.get("/api/library/imports").json() == []
    assert not list(tmp_archive(client).rglob("*.part"))
    assert not list(tmp_archive(client).rglob("payload.journal"))


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
    # preview of the light frame renders a PNG
    img_id = g["images"][0]["id"]
    assert client.get(f"/api/images/{img_id}/preview").content.startswith(b"\x89PNG")


def m31_at(date_obs: str) -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'M 31                     ' / Andromeda"),
        ("EXPTIME", "300.0"),
        ("DATE-OBS", f"'{date_obs}'"),
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
    assert batches[1]["session"] == "2026-08-22_2000"
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
    assert parts[1] == ("custom-session" if layout == "modern" else "2026-08-22_2314")
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


def test_ingest_session_folders(client):
    """Frames from different nights land in different session sub-folders;
    frames within 12 h share one session folder."""
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
    assert s1 == s3 and s1 != s2 and s2.endswith("_1300")
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


def test_unresolved_ingest_joins_exact_name_session_even_if_old_row_has_id(
        client, db):
    from app.fits import date_epoch
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
        session_folder_name(date_epoch(old_date))


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
    assert r.json() == {"found": 0, "added": 0, "skipped": 0, "failed": 0}


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
    assert response.json() == {"found": 0, "added": 0, "skipped": 0, "failed": 0}
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


def test_preview_endpoint(client):
    client.post("/api/images/ingest",
                files=[("files", ("m31.fit", m31_fits(),
                                  "application/octet-stream"))])
    lib = client.get("/api/library").json()
    img_id = lib["objects"][0]["images"][0]["id"]
    r = client.get(f"/api/images/{img_id}/preview")
    assert r.status_code == 404            # m31_fits has no image data
    client.post("/api/images/ingest",
                files=[("files", ("m42.fit", m42_light_fits(),
                                  "application/octet-stream"))])
    lib = client.get("/api/library").json()
    m42 = next(o for o in lib["objects"] if o["object_id"] == "ngc1976")
    img_id = m42["images"][0]["id"]
    r = client.get(f"/api/images/{img_id}/preview")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    r = client.get("/api/images/999999/preview")
    assert r.status_code == 404


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


@pytest.mark.parametrize("evil", [
    "../secret.jpg",
    "..\\secret.jpg",                 # Windows separator
    "/etc/passwd",
    "C:/Windows/win.ini",
])
def test_pano_image_never_leaves_the_pano_folder(client, db, tmp_path, evil):
    """Regression: a stored `file` value was joined onto data/panos raw, so a
    crafted import turned this route into an arbitrary-file-read primitive."""
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"TOP-SECRET-OUTSIDE-PANOS")
    _crafted_pano(db, "evilpano", evil)

    r = client.get("/api/panorama/evilpano/image")
    assert r.status_code == 404
    assert b"TOP-SECRET" not in r.content


def test_pano_image_still_serves_a_legitimate_file(client, db, tmp_path):
    payload = b"a-real-panorama"
    (tmp_path / "data" / "panos" / "goodpano.jpg").write_bytes(payload)
    db.add_panorama("goodpano", "mine", "goodpano.jpg")
    db.update_panorama("goodpano", active=False)

    r = client.get("/api/panorama/goodpano/image")
    assert r.status_code == 200
    assert r.content == payload
    # the self-heal fallback (db names a variant that is gone) still works
    db.update_panorama("goodpano", file="goodpano.png")
    r = client.get("/api/panorama/goodpano/image")
    assert r.status_code == 200 and r.content == payload


def test_pano_delete_cannot_remove_a_file_outside_the_pano_folder(client, db, tmp_path):
    victim = tmp_path / "victim.jpg"
    victim.write_bytes(b"keep me")
    _crafted_pano(db, "evilpano", "../victim.jpg")

    assert client.delete("/api/panorama/evilpano").status_code == 200
    assert victim.exists(), "a stored path escaped the panorama folder on delete"
    assert db.get_panorama("evilpano") is None


def test_pano_replace_cannot_unlink_a_file_outside_the_pano_folder(client, db, tmp_path):
    victim = tmp_path / "victim.jpg"
    victim.write_bytes(b"keep me")
    _crafted_pano(db, "evilpano", "../victim.jpg")

    r = client.post("/api/panorama/evilpano/image",
                    files={"file": ("new.jpg", b"new pano", "image/jpeg")})
    assert r.status_code == 200
    assert victim.exists(), "the replace path unlinked a file it does not own"


def test_image_preview_never_leaves_the_archive(client, db, tmp_path):
    secret = tmp_path / "secret.fit"
    secret.write_bytes(m42_light_fits())
    img_id = _crafted_image(db, "../secret.fit")

    assert client.get(f"/api/images/{img_id}/preview").status_code == 404


@pytest.mark.parametrize("evil_id,url_pid,refused", [
    ("..\\evil", "..%5Cevil", {404}),      # decoded backslash; route matches
    (".\\evil", ".%5Cevil", {404}),        # '.' + '\' -> temp name escapes
    ("C:evil", "C%3Aevil", {404}),
    ("evil\x00", "evil%00", {404}),
    # the router does not turn %2F into a separator, so these never reach a
    # handler at all (405 for POST, 404 for the GET route) - equally safe
    ("../evil", "..%2Fevil", {404, 405}),
    ("sub/../../evil", "sub%2F..%2F..%2Fevil", {404, 405}),
])
def test_pano_routes_refuse_ids_that_could_act_as_paths(client, db, tmp_path,
                                                        evil_id, url_pid,
                                                        refused):
    """Regression (found in post-remediation review): the crop/replace route
    builds `f"{pid}.{ext}"` and writes it under data/panos, so an id planted by
    a crafted setup ZIP reached the filesystem - `..\\evil` made the server try
    to open data/panos/...\\evil.<hex>.upload.tmp (HTTP 500) and `.\\evil` wrote
    a temp file into data/ itself. Ids that are not plain tokens must be
    refused on every pid route, before anything path-like is built."""
    db.conn.execute("INSERT INTO panoramas(id,name,file,active) VALUES(?,?,?,?)",
                    (evil_id, "crafted", "legit.jpg", 0))
    db.conn.commit()
    (tmp_path / "data" / "panos" / "legit.jpg").write_bytes(b"ORIGINAL")

    r = client.post(f"/api/panorama/{url_pid}/image",
                    files={"file": ("new.jpg", b"ATTACKER-BYTES", "image/jpeg")})
    assert r.status_code in refused, (evil_id, r.status_code, r.text[:200])
    assert client.get(f"/api/panorama/{url_pid}/image").status_code in refused
    assert client.put(f"/api/panorama/{url_pid}",
                      json={"paint_mode": "sky"}).status_code in refused
    # deleting stays idempotent (200) when the route matches, and never touches
    # anything outside panos either way
    assert client.delete(f"/api/panorama/{url_pid}").status_code in (200, 405)

    # nothing was created next to the panorama folder, and the original file
    # (and the legitimate picture) is untouched
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["panos"]
    assert (tmp_path / "data" / "panos" / "legit.jpg").read_bytes() == b"ORIGINAL"
    assert not list((tmp_path / "data" / "panos").glob("*.tmp"))


def test_pano_replace_still_works_for_a_normal_id(client, db, tmp_path):
    """The guard must not break the crop tool's normal flow."""
    pid = client.post("/api/panorama",
                      files={"file": ("p.jpg", b"first", "image/jpeg")}).json()["id"]
    r = client.post(f"/api/panorama/{pid}/image",
                    files={"file": ("p2.jpg", b"SECOND", "image/jpeg")})
    assert r.status_code == 200
    assert (tmp_path / "data" / "panos" / f"{pid}.jpg").read_bytes() == b"SECOND"
    assert client.get(f"/api/panorama/{pid}/image").content == b"SECOND"


@pytest.mark.parametrize("fov,azimuth", [(360, 0), (288, 36), (72, 108)])
def test_pano_crop_preserves_multipart_calibration_after_saved_metadata(client, tmp_path, fov, azimuth):
    """The editor saves old metadata before posting its cropped PNG/calibration."""
    import struct
    import zlib

    def png(width, height, color):
        def chunk(tag, body):
            return (struct.pack(">I", len(body)) + tag + body
                    + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))
        pixels = (b"\x00" + bytes(color) * width) * height
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))

    source = png(1000, 100, (40, 60, 90))
    pid = client.post("/api/panorama", files={
        "file": ("wide.png", source, "image/png")}).json()["id"]
    saved = client.put(f"/api/panorama/{pid}", json={
        "fov": 360, "az_offset": 0, "paint_mode": "sky", "block_above": True,
        "strokes": [{"m": "b", "x": 0.5, "y": 0.3, "r": 0.02}],
        "profile": [10] * 720, "profile_top": [30] * 720})
    assert saved.status_code == 200
    width = round(1000 * fov / 360)
    cropped = png(width, 100, (50, 70, 100))
    response = client.post(f"/api/panorama/{pid}/image", files={
        "file": ("crop.png", cropped, "image/png")},
        data={"fov": str(fov), "az_offset": str(azimuth)})
    assert response.status_code == 200
    row = next(item for item in client.get("/api/panoramas").json() if item["id"] == pid)
    assert row["fov"] == fov and row["az_offset"] == azimuth
    assert row["strokes"] == [] and row["profile"] == [] and row["profile_top"] == []
    assert row["paint_mode"] == "obstacle" and not row["block_above"]
    downloaded = client.get(f"/api/panorama/{pid}/image").content
    assert downloaded == cropped
    assert struct.unpack(">II", downloaded[16:24]) == (width, 100)
    assert not list((tmp_path / "data" / "panos").glob("*.tmp"))


def test_image_preview_still_serves_an_archived_frame(client, db, tmp_path):
    """The safe path is unchanged: a normal ingest + preview still works."""
    client.post("/api/images/ingest",
                files=[("files", ("m42.fit", m42_light_fits(),
                                  "application/octet-stream"))])
    lib = client.get("/api/library").json()
    m42 = next(o for o in lib["objects"] if o["object_id"] == "ngc1976")
    img_id = m42["images"][0]["id"]
    r = client.get(f"/api/images/{img_id}/preview")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")


# ------------------------------------------------------------------ bortle

def test_bortle_setting_and_min_alt_default(client):
    client.post("/api/settings", json={"bortle": 8})
    st = client.get("/api/state").json()
    assert st["bortle"] == 8
    assert st["min_useful_alt"] == 17 + 16     # default follows bortle
    client.post("/api/settings", json={"bortle": 1})
    assert client.get("/api/state").json()["min_useful_alt"] == 19.0
    # an explicit min alt wins over the bortle default
    client.post("/api/settings", json={"min_useful_alt": 30.0})
    assert client.get("/api/state").json()["min_useful_alt"] == 30.0
    # out-of-range values are clamped
    client.post("/api/settings", json={"bortle": 42})
    assert client.get("/api/state").json()["bortle"] == 9


def test_state_min_alt_explicit_flag(client):
    """E5: /api/state must tell the desktop client whether the min altitude is
    an EXPLICIT value or the Bortle-derived default - the client re-derives the
    default live on Bortle changes and must not treat the resolved number as an
    explicit choice (the flag used to not exist)."""
    assert client.get("/api/state").json()["min_useful_alt_explicit"] is False
    client.post("/api/settings", json={"min_useful_alt": 30.0})
    st = client.get("/api/state").json()
    assert st["min_useful_alt_explicit"] is True
    assert st["min_useful_alt"] == 30.0
    client.post("/api/settings", json={"bortle": 1})
    st = client.get("/api/state").json()
    assert st["min_useful_alt_explicit"] is True     # explicit still wins
    assert st["min_useful_alt"] == 30.0


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

def test_paint_mode_roundtrip(client, tmp_path):
    # upload a fake pano image (server only checks the extension)
    r = client.post("/api/panorama",
                    files={"file": ("pano.jpg", b"fake jpeg", "image/jpeg")})
    assert r.status_code == 200
    pid = r.json()["id"]
    try:
        assert client.get("/api/panoramas").json()[0]["paint_mode"] == "obstacle"
        # invalid mode rejected
        assert client.put(f"/api/panorama/{pid}",
                          json={"paint_mode": "diagonal"}).status_code == 400
        r = client.put(f"/api/panorama/{pid}", json={
            "paint_mode": "sky",
            "strokes": [{"m": "b", "x": 0.5, "y": 0.3, "r": 0.02}],
            "profile": [90.0] * 720,
            "profile_top": [90.0] * 720})
        assert r.status_code == 200
        row = client.get("/api/panoramas").json()[0]
        assert row["paint_mode"] == "sky"
        assert len(row["profile_top"]) == 720
        assert client.get("/api/state").json()["panoramas"][0]["paint_mode"] == "sky"

        # export -> import preserves the mode and both profiles
        data = client.get("/api/export").content
        db2 = Database(tmp_path / "other.db")
        # give the second database its own archive: without this the import
        # restored into the DEFAULT folder (~/Documents/NightSeam Images)
        db2.set_setting("image_dir", str(tmp_path / "archive2"))
        with TestClient(create_app(db2, prefetch=False)) as c2:
            c2.post("/api/import", files={
                "file": ("b.zip", data, "application/zip")})
            row = c2.get("/api/panoramas").json()[0]
            assert row["paint_mode"] == "sky"
            assert len(row["profile_top"]) == 720
    finally:
        client.delete(f"/api/panorama/{pid}")


def test_panorama_activate_deactivate(client):
    """active:true makes a pano the active one; active:false clears it."""
    r = client.post("/api/panorama",
                    files={"file": ("pano.jpg", b"fake jpeg", "image/jpeg")})
    assert r.status_code == 200
    pid = r.json()["id"]
    try:
        # activate via the PUT endpoint
        assert client.put(f"/api/panorama/{pid}",
                          json={"active": True}).status_code == 200
        assert client.get("/api/state").json()["active_pano_id"] == pid
        # explicit False deactivates (clears the global pointer)
        assert client.put(f"/api/panorama/{pid}",
                          json={"active": False}).status_code == 200
        assert client.get("/api/state").json()["active_pano_id"] is None
        # an update that doesn't mention active leaves the pointer alone
        other = client.post("/api/panorama",
                            files={"file": ("p2.jpg", b"x", "image/jpeg")}).json()["id"]
        try:
            assert client.put(f"/api/panorama/{other}",
                              json={"active": True}).status_code == 200
            assert client.put(f"/api/panorama/{other}",
                              json={"az_offset": 5.0}).status_code == 200
            assert client.get("/api/state").json()["active_pano_id"] == other
        finally:
            client.delete(f"/api/panorama/{other}")
    finally:
        client.delete(f"/api/panorama/{pid}")


def test_deleting_the_active_panorama_clears_the_pointer(client):
    """BUG-006: `DELETE /api/panorama/{pid}` removed the row and its image but
    left `settings.active_pano_id` naming it, so `/api/state` advertised an
    "active panorama" that planning could not resolve - the desktop planned
    against empty sky while the UI claimed a horizon. Deleting an INACTIVE pano
    must leave the user's active choice alone."""
    ids = [client.post("/api/panorama",
                       files={"file": (f"{n}.jpg", b"x", "image/jpeg")}).json()["id"]
           for n in ("pdel-active", "pdel-other", "pdel-third")]
    active, other, third = ids
    # (a) deleting the ACTIVE record clears the pointer
    client.put(f"/api/panorama/{active}", json={"active": True})
    assert client.get("/api/state").json()["active_pano_id"] == active
    assert client.delete(f"/api/panorama/{active}").status_code == 200
    state = client.get("/api/state").json()
    assert state["active_pano_id"] is None, state["active_pano_id"]
    assert all(p["id"] != active for p in state["panoramas"])
    # (b) deleting an INACTIVE record keeps the pointer where it was ...
    client.put(f"/api/panorama/{other}", json={"active": True})
    assert client.delete(f"/api/panorama/{third}").status_code == 200
    assert client.get("/api/state").json()["active_pano_id"] == other
    # ... and the surviving row is still the active one
    rows = client.get("/api/panoramas").json()
    assert [r["id"] for r in rows if r["active"]] == [other]
    # (c) the pointer is idempotent to a second delete of the same id
    assert client.delete(f"/api/panorama/{other}").status_code == 200
    assert client.delete(f"/api/panorama/{other}").status_code == 200
    assert client.get("/api/state").json()["active_pano_id"] is None


def test_activating_an_unknown_panorama_id_is_refused(client):
    """BUG-006: `POST /api/settings {active_pano_id: <well-formed but unknown>}`
    stored the pointer anyway (the token rule passed), so `/api/state` advertised
    a panorama that does not exist. A well-formed id must still NAME a row."""
    pid = client.post("/api/panorama",
                      files={"file": ("pknown.jpg", b"x", "image/jpeg")}).json()["id"]
    try:
        # unknown but token-shaped -> 400, and nothing is stored
        r = client.post("/api/settings", json={"active_pano_id": "deadbeef00"})
        assert r.status_code == 400, r.text
        assert "deadbeef00" in r.json()["detail"]
        assert client.get("/api/state").json()["active_pano_id"] is None
        # a real id still works, and clearing still works
        assert client.post("/api/settings",
                           json={"active_pano_id": pid}).status_code == 200
        assert client.get("/api/state").json()["active_pano_id"] == pid
        assert client.post("/api/settings",
                           json={"active_pano_id": ""}).status_code == 200
        assert client.get("/api/state").json()["active_pano_id"] in (None, "")
    finally:
        client.delete(f"/api/panorama/{pid}")


def test_the_database_refuses_a_dangling_active_pointer(client, db):
    """The rule lives in the DATABASE transaction, not only in the route: the
    import path writes settings directly, so a pointer that names no row must be
    refused there too (and the activate/clear pair must stay atomic)."""
    import pytest as _pytest

    pid = client.post("/api/panorama",
                      files={"file": ("pdbrule.jpg", b"x", "image/jpeg")}).json()["id"]
    with _pytest.raises(ValueError):
        db.set_active_panorama("nosuchpano")
    assert db.setting("active_pano_id") is None
    # a known id still activates, and deleting it through the DB clears it
    db.set_active_panorama(pid)
    assert db.setting("active_pano_id") == pid
    db.delete_panorama(pid)
    assert db.setting("active_pano_id") is None
    # deleting an unknown id is a no-op, not an error, and never clears a
    # pointer that names a DIFFERENT row
    other = client.post("/api/panorama",
                        files={"file": ("pdbrule2.jpg", b"x", "image/jpeg")}).json()["id"]
    try:
        db.set_active_panorama(other)
        db.delete_panorama("nosuchpano")
        assert db.setting("active_pano_id") == other
    finally:
        client.delete(f"/api/panorama/{other}")


def test_activating_a_panorama_deactivates_the_previous(client):
    """Exactly one panorama may be active at a time.

    `PUT /api/panorama/{pid}` with active=true used to leave earlier rows
    active; `active_panorama()` returns the newest of them, so `/api/state` and
    the planning client could disagree about which horizon governs planning."""
    ids = [client.post("/api/panorama",
                       files={"file": (f"{n}.jpg", b"x", "image/jpeg")}).json()["id"]
           for n in ("pa", "pb")]
    try:
        client.put(f"/api/panorama/{ids[0]}", json={"active": True})
        client.put(f"/api/panorama/{ids[1]}", json={"active": True})
        rows = client.get("/api/panoramas").json()
        active = [r["id"] for r in rows if r["active"]]
        assert active == [ids[1]], active
        assert client.get("/api/state").json()["active_pano_id"] == ids[1]
        # and the settings route equally keeps a single active row
        client.post("/api/settings", json={"active_pano_id": ids[0]})
        rows = client.get("/api/panoramas").json()
        assert [r["id"] for r in rows if r["active"]] == [ids[0]]
    finally:
        for i in ids:
            client.delete(f"/api/panorama/{i}")


# ------------------------------------------------------------------ export / import

def test_export_import_roundtrip(client, tmp_path):
    add_site(client, "Export Site")
    client.post("/api/rigs", json={"name": "RigA", "focal_mm": 150.0,
                                   "sensor_w": 6.0, "sensor_h": 4.0})
    cust = client.post("/api/catalog/custom", json={
        "name": "Export Neb", "ra": "6.0", "dec": "-10"}).json()["id"]
    client.post("/api/images/ingest",
                files=[("files", ("m31.fit", m31_fits(),
                                  "application/octet-stream"))])
    client.post("/api/settings", json={"min_useful_alt": 33.0})

    z = client.get("/api/export")
    assert z.status_code == 200
    data = z.content
    assert data[:2] == b"PK"
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert any(n.startswith("library/") for n in names)

    # fresh database + app -> import
    db2 = Database(tmp_path / "other.db")
    # own archive: the default would be the developer's real Documents folder
    db2.set_setting("image_dir", str(tmp_path / "archive2"))
    with TestClient(create_app(db2, prefetch=False)) as c2:
        r = c2.post("/api/import", files={
            "file": ("backup.zip", data, "application/zip")})
        assert r.status_code == 200
        assert r.json()["sites"] == 1
        assert r.json()["customs"] == 1
        assert r.json()["images"] == 1
        st = c2.get("/api/state").json()
        assert st["min_useful_alt"] == 33.0
        assert st["sites"][0]["name"] == "Export Site"
        lib = c2.get("/api/library").json()
        assert lib["total_images"] == 1
        assert lib["objects"][0]["name"]
        catalog = c2.get("/api/catalog").json()
        assert any(o["id"] == cust for o in catalog)


def test_import_rejects_garbage(client):
    r = client.post("/api/import", files={
        "file": ("junk.zip", b"not a zip at all", "application/zip")})
    assert r.status_code == 400


def test_numeric_fields_reject_nan_and_keep_state_alive(client):
    """PY3 (review): NaN/Infinity used to be stored by settings/rigs/panoramas
    and then 500 /api/state forever (the desktop awaits it at boot)."""
    for path, body in (("/api/settings", {"min_useful_alt": float("nan")}),
                       ("/api/rigs", {"name": "x", "focal_mm": float("nan"),
                                      "sensor_w": 6.0, "sensor_h": 4.0})):
        r = client.post(path, content=json.dumps(body),
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400, (path, r.status_code, r.text)
    pid = client.post("/api/panorama",
                      files={"file": ("p.jpg", b"x", "image/jpeg")}
                      ).json()["id"]
    r = client.put(f"/api/panorama/{pid}",
                   content=json.dumps({"az_offset": float("inf")}),
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert client.get("/api/state").status_code == 200


def test_origin_null_is_rejected(client):
    """PY6 (review): `Origin: null` skips the host comparison - it never
    belongs to a legitimate local app. No-Origin clients stay allowed."""
    assert client.get("/api/state",
                      headers={"Origin": "null"}).status_code == 403
    assert client.get("/api/state").status_code == 200


def test_shutdown_is_post_only(client, monkeypatch):
    """SP-03 (review 0.6.32): the route was BOTH `@app.get` and `@app.post`, so
    any page the user visits could kill the app with
    `<img src="http://127.0.0.1:8407/api/shutdown">` - an image request carries
    neither `Sec-Fetch-Site` nor `Origin`, which is exactly the case the
    anti-cross-site guard must fail open on (non-browser clients send neither).
    A state-changing route must not be reachable by GET."""
    # the POST arms a 1.5 s thread that calls os._exit(0): never let it run here
    class _NoTimer:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server_mod.threading, "Timer", _NoTimer)
    assert client.get("/api/shutdown").status_code == 405
    assert client.post("/api/shutdown").status_code == 200
    assert server_mod._state["shutdown"] is True


def test_int_path_params_beyond_64_bits_are_404_not_500(client):
    """PY-05 (review 0.6.32): pydantic accepts arbitrary-precision ints, so a
    hand-crafted URL reached sqlite3 and raised `OverflowError: Python int too
    large to convert to SQLite INTEGER` - a 500 with a traceback instead of a
    clean not-found. 2**63-1 is the largest id SQLite can hold and must still
    answer normally."""
    too_big = 999999999999999999999
    assert client.get(f"/api/images/{too_big}/preview").status_code == 404
    assert client.delete(f"/api/sites/{too_big}").status_code == 404
    assert client.delete(f"/api/rigs/{too_big}").status_code == 404
    assert client.put(
        f"/api/sites/{too_big}",
        json={"name": "x", "lat": 1.0, "lon": 2.0, "elev": 0}
    ).status_code == 404
    assert client.get(
        f"/api/images/{2 ** 63 - 1}/preview").status_code == 404   # clean miss


def test_import_malformed_bundles_are_clean_400s(client):
    """FIX-4 follow-up (verification): a VALID zip with a wrong/missing
    setup.json used to leave the ZipFile open, so the route's spool unlink
    failed on Windows and a clean 400 became a 500 (plus a leaked spool)."""
    for setup in ({"app": "Other"}, None):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            if setup is not None:
                z.writestr("setup.json", json.dumps(setup))
            else:
                z.writestr("other.txt", "x")
        r = client.post("/api/import", files={
            "file": ("backup.zip", out.getvalue(), "application/zip")})
        assert r.status_code == 400, (setup, r.status_code, r.text)


def test_import_rejects_hostile_settings_and_keeps_state_alive(client):
    """FIX-4 (review PY2): a crafted bundle with bortle=null used to be stored
    verbatim and then 500'd /api/state forever - and the desktop awaits that
    route at boot, so the UI never started. The import must be a clean 400 and
    the state route must stay alive afterwards."""
    z = client.get("/api/export")
    assert z.status_code == 200
    setup = json.loads(zipfile.ZipFile(io.BytesIO(z.content))
                       .read("setup.json"))
    setup["settings"]["bortle"] = None
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("setup.json", json.dumps(setup))
    r = client.post("/api/import", files={
        "file": ("backup.zip", out.getvalue(), "application/zip")})
    assert r.status_code == 400, r.text
    assert "bortle" in r.json()["detail"]
    st = client.get("/api/state")
    assert st.status_code == 200
    assert st.json()["bortle"] == 4


def test_import_rejects_a_hostile_min_vis_and_keeps_state_alive(client):
    """PY-02 (review 0.6.32): `min_vis` was the one int-coerced setting with no
    import check, so `"abc"`/`null` was stored verbatim and every `/api/state`
    then answered 500 (`int(db.setting("min_vis", 1))`) - static/js/main.js
    awaits that route before rendering anything, so one crafted bundle bricked
    the desktop at every boot."""
    z = client.get("/api/export")
    assert z.status_code == 200
    base = json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read("setup.json"))
    for bad in ("abc", None):
        setup = json.loads(json.dumps(base))
        setup["settings"]["min_vis"] = bad
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            dst.writestr("setup.json", json.dumps(setup))
        r = client.post("/api/import", files={
            "file": ("backup.zip", out.getvalue(), "application/zip")})
        assert r.status_code == 400, (bad, r.status_code, r.text)
        assert "min_vis" in r.json()["detail"]
    st = client.get("/api/state")
    assert st.status_code == 200
    assert st.json()["min_vis"] == 1
    # a finite float is a legitimate (if odd) value: coerced and clamped
    setup = json.loads(json.dumps(base))
    setup["settings"]["min_vis"] = 42.9
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("setup.json", json.dumps(setup))
    r = client.post("/api/import", files={
        "file": ("backup.zip", out.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    assert client.get("/api/state").json()["min_vis"] == 42


def test_state_survives_a_min_vis_row_that_is_not_a_number(db, tmp_path,
                                                           monkeypatch):
    """PY-02, second half: even a row that arrived some other way must not be
    able to 500 the boot route. `/api/state` falls back to the default."""
    db.set_setting("min_vis", "abc")
    data = tmp_path / "data"
    (data / "panos").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.server.data_dir", lambda: data)
    with TestClient(create_app(db, prefetch=False)) as c:
        r = c.get("/api/state")
    assert r.status_code == 200
    assert r.json()["min_vis"] == 1


def test_import_never_writes_a_payload_for_a_record_it_dropped(client, tmp_path):
    """SP-05/PY-06 (review 0.6.32): the restore write loop iterated the RAW
    bundle list, so a panorama whose id failed `safe_token` never became a row
    but its payload was still written into `data/panos` - an orphan file, never
    cleaned, while the summary reported `panoramas: 0, pano_images: 1`."""
    pano_root = server_mod.data_dir() / "panos"
    setup = {"app": "NightSeam", "version": "test", "settings": {},
             "sites": [], "rigs": [], "customs": [],
             "panoramas": [{"id": "../evil", "name": "evil", "file": "ok.jpg"}],
             "images": []}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("setup.json", json.dumps(setup))
        z.writestr("panos/ok.jpg", b"JPEGDATA")
    r = client.post("/api/import", files={
        "file": ("backup.zip", out.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["panoramas"] == 0
    assert r.json()["pano_images"] == 0, "a dropped record's payload was written"
    assert not (pano_root / "ok.jpg").exists()


def test_import_rejects_a_control_character_in_a_stored_path(client):
    """SP-05 (review 0.6.32): `safe_rel` rejected NUL, `..`, absolutes and
    device names but let `\\x01`/`\\n`/`\\t`/`\\x1b` through, and Windows refuses
    those at write time (`OSError: [Errno 22] Invalid argument`) - so the record
    was stored and then aborted the restore AFTER the database had been
    replaced. It must be refused at the boundary, so nothing is stored."""
    setup = {"app": "NightSeam", "version": "test", "settings": {},
             "sites": [], "rigs": [], "customs": [], "images": [],
             "panoramas": [{"id": "p1", "name": "ctl", "file": "a\x01b.jpg"}]}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("setup.json", json.dumps(setup))
    r = client.post("/api/import", files={
        "file": ("backup.zip", out.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["panoramas"] == 0
    assert client.get("/api/state").json()["panoramas"] == []


def test_import_rejects_nonfinite_image_numbers_and_keeps_library_alive(client):
    """PY04-F2 (review round 2): a crafted bundle whose images[0] carries the
    bare JSON token Infinity/NaN was stored verbatim, so /api/library then
    answered 500 on every request (Starlette serializes with allow_nan=False).
    The import must be a defined 400-class error with nothing persisted, and
    the library route must stay alive."""
    for bad in ({"exposure": float("inf")},
                {"ra_hours": float("nan")},
                {"dec_deg": float("-inf")}):
        image = {"file": "M 31/2026-08-22_2314/lights/m31.fit",
                 "orig_name": "m31.fit", "object_id": None,
                 "object_name": "M 31", "ra_hours": 0.712, "dec_deg": 41.269,
                 "exposure": 300.0, "date_obs": "2026-08-22T23:14:05",
                 "filter": "L", "telescope": "Seestar S50",
                 "imagetyp": "light", "ingested_at": "2026-08-22T23:20:00"}
        image.update(bad)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            dst.writestr("setup.json", json.dumps(
                {"app": "NightSeam", "version": "test", "settings": {},
                 "sites": [], "rigs": [], "panoramas": [], "customs": [],
                 "images": [image]}))
        r = client.post("/api/import", files={
            "file": ("backup.zip", out.getvalue(), "application/zip")})
        assert r.status_code == 400, (bad, r.status_code, r.text)
        assert "finite number" in r.json()["detail"]
        lib = client.get("/api/library")
        assert lib.status_code == 200, (bad, lib.status_code)
        assert lib.json()["total_images"] == 0      # nothing was persisted
        assert client.get("/api/library/imports").status_code == 200


# -------------------------------------------------- request guard (middleware)
# The guard is what stops any website the user visits from reaching this local
# API (a no-cors request to 127.0.0.1 is not blocked by CORS). It covers EVERY
# route, so it is exercised here on a harmless GET and on a state-changing POST.

def test_cross_site_requests_are_rejected(client):
    assert client.get("/api/state").status_code == 200

    r = client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and "cross-site" in r.json()["detail"]

    r = client.post("/api/settings", json={"min_useful_alt": 42.0},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    # ... and the rejected request really did not change anything
    assert client.get("/api/state").json()["min_useful_alt"] != 42.0

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

def test_oversized_request_body_is_rejected_before_parsing(client, monkeypatch):
    """A 413 + "too large" alone does not say WHERE the request died: the
    route's own per-file cap (60 MB, inside the handler) produces a similar
    body. This proves the rejection happened BEFORE the handler: the streaming
    drain the route calls is replaced by a recorder, the declared request cap is
    set to 3 MB, and the reply must be the middleware's REQUEST-cap wording
    ("... per request", which the per-file cap never emits) with no recorded
    call and no panorama row."""
    cap = 3 * 1024 * 1024
    calls = []

    async def _record(*args, **kwargs):
        calls.append((args, kwargs))

    with monkeypatch.context() as m:
        m.setitem(server_mod._MAX_REQUEST_BYTES, "/api/panorama", cap)
        m.setattr(server_mod, "_drain_upload", _record)
        r = client.post("/api/panorama",
                        files={"file": ("big.jpg", b"x" * (cap + 1),
                                        "image/jpeg")})
    assert r.status_code == 413, r.text
    # distinct from the per-file message: f"Image too large (max 60 MB)"
    assert r.json()["detail"] == "upload too large (max 3 MB per request)"
    assert calls == [], "the handler ran despite the oversized request"
    assert client.get("/api/panoramas").json() == [], "a panorama was stored"

    # a request under the cap still goes through the real handler
    r = client.post("/api/panorama",
                    files={"file": ("ok.jpg", b"x" * 64, "image/jpeg")})
    assert r.status_code == 200
    client.delete(f"/api/panorama/{r.json()['id']}")


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

def test_panorama_profile_length_and_types_are_validated(client):
    pid = client.post("/api/panorama",
                      files={"file": ("p.jpg", b"fake jpeg", "image/jpeg")}
                      ).json()["id"]
    try:
        # a short profile used to be stored and then failed OPEN at read time
        assert client.put(f"/api/panorama/{pid}",
                          json={"profile": [10.0] * 719}).status_code == 400
        assert client.put(f"/api/panorama/{pid}",
                          json={"profile_top": [10.0] * 721}).status_code == 400
        # non-numeric values used to make the read-time lookup fail open
        assert client.put(f"/api/panorama/{pid}",
                          json={"profile": ["x"] * 720}).status_code == 400
        assert client.put(f"/api/panorama/{pid}",
                          json={"profile": [True] * 720}).status_code == 400
        # empty (clear) and exactly 720 finite numbers are fine
        assert client.put(f"/api/panorama/{pid}",
                          json={"profile": [], "profile_top": []}).status_code == 200
        assert client.put(f"/api/panorama/{pid}",
                          json={"profile": [12.5] * 720}).status_code == 200
        assert client.post("/api/settings",
                           json={"active_pano_id": pid}).status_code == 200
    finally:
        client.delete(f"/api/panorama/{pid}")


def test_panorama_strokes_shape_is_validated(client):
    """PY-03 (write half): both profile arrays were validated but `strokes` - the
    THIRD stored array, and the one both editors iterate - accepted any list, so a
    list of non-objects was persisted and the desktop editor then threw while
    painting/saving that record."""
    pid = client.post("/api/panorama",
                      files={"file": ("p.jpg", b"fake jpeg", "image/jpeg")}
                      ).json()["id"]
    try:
        # pydantic rejects a non-list body with 422 before the route runs; the
        # route's own content rule (a list of stroke OBJECTS) is the 400 half.
        for bad in ({}, "abc", 7):
            assert client.put(f"/api/panorama/{pid}",
                              json={"strokes": bad}).status_code in (400, 422), bad
        for bad in (["not-a-stroke"], [1, 2], [None]):
            assert client.put(f"/api/panorama/{pid}",
                              json={"strokes": bad}).status_code == 400, bad
        # a real stroke list, and the empty list, are fine
        assert client.put(f"/api/panorama/{pid}", json={
            "strokes": [{"m": "b", "x": 0.1, "y": 0.2, "r": 0.01}]}
        ).status_code == 200
        assert client.put(f"/api/panorama/{pid}",
                          json={"strokes": []}).status_code == 200
    finally:
        client.delete(f"/api/panorama/{pid}")


def test_panorama_metadata_rejects_unhashable_enum_values(client):
    pid = client.post("/api/panorama",
                      files={"file": ("p.jpg", b"fake jpeg", "image/jpeg")}
                      ).json()["id"]
    try:
        landmark = {"name": "oak", "azimuth": 45.0, "altitude": 20.0,
                    "kind": []}
        r = client.put(f"/api/panorama/{pid}", json={"landmarks": [landmark]})
        assert r.status_code == 400, r.text

        r = client.put(f"/api/panorama/{pid}",
                       json={"provenance": {"source": {}}})
        assert r.status_code == 400, r.text
    finally:
        client.delete(f"/api/panorama/{pid}")


def test_malformed_stored_strokes_do_not_500_the_listing(client, db):
    """PY-03 (read half): a row whose `strokes` column is not valid JSON (hand
    edit, interrupted write, an older build) made `panoramas()` raise, so
    /api/state and /api/panoramas answered 500 - and the desktop's FIRST boot
    request is /api/state, i.e. the app was unbootable until the DB was repaired.
    The column now falls back to its canonical empty value."""
    db.conn.execute(
        "INSERT INTO panoramas(id,name,file,az_offset,alt_bottom,alt_top,strokes,"
        "profile,profile_top,active,fov,paint_mode,block_above) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("pbad", "bad", "bad.jpg", 0, 0, 45, "{not json", "[]", "[]", 1, 360,
         "obstacle", 0))
    db.conn.commit()
    try:
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/panoramas").status_code == 200
        row = [p for p in db.panoramas() if p["id"] == "pbad"][0]
        assert row["strokes"] == []            # canonical empty, not a crash
        # a non-JSON profile falls back the same way rather than 500ing
        db.conn.execute("UPDATE panoramas SET profile=? WHERE id=?", ("{oops", "pbad"))
        db.conn.commit()
        assert client.get("/api/panoramas").status_code == 200
    finally:
        client.delete("/api/panorama/pbad")


def test_settings_rejects_a_non_token_active_pano_id(client):
    """PY-05: this boundary accepted ANY string as `active_pano_id` while every
    id-consuming route rejects a non-token id (`_pano_or_404`) - so /api/state
    advertised an "active panorama" that no route could ever resolve."""
    assert client.post("/api/settings",
                       json={"active_pano_id": "../../evil"}).status_code == 400
    # "" and null legitimately mean "no active panorama"
    assert client.post("/api/settings",
                       json={"active_pano_id": ""}).status_code == 200
    assert client.get("/api/state").json()["active_pano_id"] in (None, "")


# ------------------------------------------------------------- site validation

def test_site_coordinates_are_validated(client):
    for body in ({"name": "x", "lat": 999, "lon": 0},
                 {"name": "x", "lat": 0, "lon": 200},
                 {"name": "x", "lat": 0, "lon": 0, "elev": 99999}):
        r = client.post("/api/sites", json=body)
        assert r.status_code == 400, body
    assert client.get("/api/sites").json() == []
    sid = add_site(client)
    assert client.put(f"/api/sites/{sid}",
                      json={"name": "x", "lat": 91, "lon": 0}).status_code == 400


def test_deleting_the_active_site_reassigns_the_pointer(client):
    """Regression: the active pointer dangled, so planning had no site with no
    way back through the UI. The server keeps the pointer coherent; the client
    core resolves the same way."""
    first = add_site(client, "First")
    second = add_site(client, "Second")
    assert client.get("/api/state").json()["active_site_id"] == first

    client.post("/api/settings", json={"active_site_id": second})
    assert client.delete(f"/api/sites/{second}").status_code == 200
    st = client.get("/api/state").json()
    assert st["active_site_id"] == first

    # deleting every site clears the pointer
    assert client.delete(f"/api/sites/{first}").status_code == 200
    assert client.get("/api/state").json()["active_site_id"] is None
    # ... and adding a new site repairs it without touching the files
    again = add_site(client, "Again")
    assert client.get("/api/state").json()["active_site_id"] == again


# ------------------------------------------------------------ UI cache policy

def test_ui_assets_are_never_cached(client):
    """A stale themes.css/style.css next to a fresh index.html breaks the UI -
    themes.css was missing from the no-store list."""
    for path in ("/", "/index.html", "/style.css", "/themes.css",
                 "/favicon.ico", "/js/util.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-store", path
