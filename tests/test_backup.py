"""Tests for setup export / import (app/backup.py).

All file I/O is isolated: `app.backup.data_dir` is monkeypatched onto a
tmp dir and each Database lives in its own tmp dir, so nothing touches the
repo's real `data/`.
"""

import io
import json
import math
import zipfile

import pytest

from app.backup import _safe_join, apply_import_zip, build_export_zip
from app.database import Database


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = Database(tmp_path / "nightseam.db")
    d.set_setting("image_dir", str(tmp_path / "archive"))
    monkeypatch.setattr("app.backup.data_dir", lambda: tmp_path / "data")
    return d


def _seed(db, tmp_path):
    """A site, a rig, a custom target, a pano and one library image (with
    matching files on disk)."""
    db.add_site("Home", 50.0, 7.8, 150)
    db.add_rig("RigA", 150.0, 6.0, 4.0, 2.3, 1.5)
    cust = db.add_custom({
        "id": "u_seed1", "primary_name": "Export Neb", "otype": "galaxy",
        "ra_hours": 6.0, "dec_deg": -10.0, "vmag": 9.5,
        "size_am": "12x8", "constellation": "Monoceros"})
    pdir = tmp_path / "data" / "panos"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "pano1.jpg").write_bytes(b"JPEGDATA")
    db.add_panorama("p1", "Backyard pano", "pano1.jpg")
    arch = tmp_path / "archive"
    f = arch / "M 31" / "2026-08-22_2314" / "lights" / "m31.fit"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"FITSDATA")
    db.add_image({
        "file": str(f.relative_to(arch)), "orig_name": "m31.fit",
        "object_id": None, "object_name": "M 31", "ra_hours": 0.712,
        "dec_deg": 41.269, "exposure": 300.0, "date_obs": "2026-08-22T23:14:05",
        "filter": "L", "telescope": "Seestar S50", "imagetyp": "light",
        "ingested_at": "2026-08-22T23:20:00"})
    return cust


def test_safe_join_refuses_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert _safe_join(root, "ok/sub/file.jpg") is not None
    assert _safe_join(root, "../evil.jpg") is None
    assert _safe_join(root, "a/../../evil.jpg") is None


def test_export_import_roundtrip(db, tmp_path):
    _seed(db, tmp_path)
    image = db.images()[0]
    db.set_image_fingerprint(image["id"], "ab" * 32, 8, 12345)
    blob = build_export_zip(db)
    assert blob[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
        exported = json.loads(bundle.read("setup.json"))["images"][0]
        assert not {"content_sha256", "file_size", "file_mtime_ns"}.intersection(exported)
        assert bundle.read("library/" + exported["file"].replace("\\", "/")) == b"FITSDATA"
    assert db.image(image["id"])["content_sha256"] == "ab" * 32

    db2 = Database(tmp_path / "other.db")
    db2.set_setting("image_dir", str(tmp_path / "archive"))
    summary = apply_import_zip(db2, blob)
    assert summary["sites"] == 1
    assert summary["rigs"] == 1
    assert summary["customs"] == 1
    assert summary["panoramas"] == 1
    assert summary["images"] == 1
    assert summary["auto_backup"]  # safety copy written

    sites = db2.sites()
    assert [s["name"] for s in sites] == ["Home"]
    assert [r["name"] for r in db2.rigs()] == ["RigA"]
    custs = db2.customs()
    assert custs[0]["primary_name"] == "Export Neb"
    assert db2.panoramas()[0]["file"] == "pano1.jpg"
    imgs = db2.images()
    assert imgs[0]["object_name"] == "M 31"
    assert imgs[0]["content_sha256"] is None
    assert imgs[0]["file_size"] is None and imgs[0]["file_mtime_ns"] is None

    # files restored into the pano dir and the (imported) image dir
    new_pano = tmp_path / "data" / "panos" / "pano1.jpg"
    assert new_pano.read_bytes() == b"JPEGDATA"
    new_img = tmp_path / "archive" / "M 31" / "2026-08-22_2314" / "lights" / "m31.fit"
    assert new_img.read_bytes() == b"FITSDATA"


def test_view_full_day_preference_roundtrips(db, tmp_path):
    """ITEM 1 (0.6.13): view_full_day is one shared preference - the desktop
    stores it in the settings table, so it must survive an export/import like
    every other pref (the web twin rides in backup.prefs)."""
    db.set_setting("view_full_day", True)
    blob = build_export_zip(db)
    db2 = Database(tmp_path / "other.db")
    db2.set_setting("image_dir", str(tmp_path / "archive"))
    apply_import_zip(db2, blob)
    assert db2.setting("view_full_day", False) is True


def test_import_of_the_retired_planning_flag_stays_safe(db, tmp_path):
    """Phase C retired `planning_client_compute`, but backups written by
    0.6.18/0.6.19 carry it in their settings map. Import passes settings
    through opaquely, so such a bundle must restore without error and the dead
    key must simply sit inert in the table (it is never read again)."""
    db.set_setting("planning_client_compute", False)
    blob = build_export_zip(db)
    db2 = Database(tmp_path / "other.db")
    db2.set_setting("image_dir", str(tmp_path / "archive"))
    summary = apply_import_zip(db2, blob)          # must not raise
    assert summary["auto_backup"]
    assert db2.setting("planning_client_compute", "unset") is False


def test_import_clears_stale_import_history(db, tmp_path):
    """The 'Recent imports' history describes the library the restore replaces,
    so a restore must clear it. Rows surviving a restore would point at
    pre-restore object/session folders and show a misleading history."""
    def batch(object_name):
        return {"created_at": "2026-08-22T23:20:00", "object_id": None,
                "object_name": object_name, "slug": object_name.lower(),
                "session": "2026-08-22_2314", "frame": "light", "n_files": 4,
                "total_exposure": 1200.0, "first_obs": "2026-08-22T23:14:05",
                "image_dir": "/x"}

    blob = build_export_zip(db)
    db2 = Database(tmp_path / "other.db")
    db2.set_setting("image_dir", str(tmp_path / "archive"))
    db2.add_import_batch(batch("Old Target"))
    assert len(db2.import_batches()) == 1
    apply_import_zip(db2, blob)
    assert db2.import_batches() == []


def test_import_from_path_file(db, tmp_path):
    """The server spools uploads to a temp FILE and imports from the path -
    the Path branch of apply_import_zip must behave like the bytes branch."""
    _seed(db, tmp_path)
    blob = build_export_zip(db)
    spool = tmp_path / ".import-test.zip"
    spool.write_bytes(blob)

    db2 = Database(tmp_path / "other.db")
    db2.set_setting("image_dir", str(tmp_path / "archive"))
    summary = apply_import_zip(db2, spool)
    assert summary["sites"] == 1 and summary["images"] == 1
    assert db2.sites()[0]["name"] == "Home"
    assert summary["auto_backup"]


def test_import_preserves_current_state_in_safety_backup(db, tmp_path):
    db.add_site("Old Site", 48.0, 11.0, 500)

    other = Database(tmp_path / "other.db")
    other.add_site("New Site", 51.0, 6.0, 100)
    blob = build_export_zip(other)

    summary = apply_import_zip(db, blob)
    assert summary["sites"] == 1
    assert db.sites()[0]["name"] == "New Site"

    # the pre-import state ("Old Site") is preserved in the safety zip
    safety = db.path.with_suffix(".backup-auto.zip")
    assert safety.is_file()
    with zipfile.ZipFile(safety) as z:
        setup = json.loads(z.read("setup.json").decode("utf-8"))
    assert [s["name"] for s in setup["sites"]] == ["Old Site"]


def test_import_rejects_garbage(db):
    with pytest.raises(ValueError):
        apply_import_zip(db, b"not a zip")
    with pytest.raises(ValueError):
        apply_import_zip(db, _zip_with("other-app", {"sites": []}))
    # nothing was touched by a rejected import
    assert db.sites() == []


def test_import_traversal_paths_are_dropped(db, tmp_path):
    """A crafted zip with ../ file entries must not escape the pano dir."""
    pdir = tmp_path / "data" / "panos"
    pdir.mkdir(parents=True)
    setup = {"app": "NightSeam", "sites": [], "rigs": [], "panoramas": [
        {"id": "pX", "name": "evil", "file": "../escape.jpg"}],
        "customs": [], "images": []}
    blob = _zip_with("NightSeam", setup, files={"panos/../escape.jpg": b"x"})
    summary = apply_import_zip(db, blob)
    assert summary["pano_images"] == 0
    assert not (tmp_path / "escape.jpg").exists()
    assert not (pdir / ".." / "escape.jpg").exists()


@pytest.mark.parametrize("evil", [
    "../escape.jpg",
    "..\\escape.jpg",
    "sub/../../escape.jpg",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "C:escape.jpg",
    "\\\\server\\share\\evil.jpg",
])
def test_import_drops_escaping_records_entirely(db, tmp_path, evil):
    """The DB must never even *store* an escaping path.

    Regression: the import refused to write the file but still inserted the
    row, so `/api/panorama/{id}/image` served the target file long after the
    import - a crafted setup ZIP was an arbitrary-file-read primitive.
    """
    (tmp_path / "data" / "panos").mkdir(parents=True)
    setup = {"app": "NightSeam", "sites": [], "rigs": [], "panoramas": [
        {"id": "pX", "name": "evil", "file": evil}],
        "customs": [], "images": [
            {"id": 1, "file": evil, "object_name": "evil"}]}
    summary = apply_import_zip(db, _zip_with("NightSeam", setup))

    assert summary["panoramas"] == 0        # not restored ...
    assert summary["images"] == 0
    assert db.panoramas() == []             # ... and not stored either
    assert db.images() == []


def test_import_normalizes_legitimate_subfolder_paths(db, tmp_path):
    """Safe paths keep working - and are canonicalized (separators, ./ )."""
    (tmp_path / "data" / "panos").mkdir(parents=True)
    setup = {"app": "NightSeam", "sites": [], "rigs": [], "panoramas": [
        {"id": "ok", "name": "fine", "file": ".\\sub//pano.jpg"}],
        "customs": [], "images": [
            {"id": 1, "file": "M 31/lights\\m31.fit", "object_name": "M 31"}]}
    summary = apply_import_zip(db, _zip_with("NightSeam", setup,
                                            files={"panos/sub/pano.jpg": b"jpeg"}))
    assert summary["panoramas"] == 1 and summary["images"] == 1
    assert db.panoramas()[0]["file"] == "sub/pano.jpg"
    assert db.images()[0]["file"] == "M 31/lights/m31.fit"
    assert (tmp_path / "data" / "panos" / "sub" / "pano.jpg").read_bytes() == b"jpeg"


@pytest.mark.parametrize("evil_id", [
    "..\\evil", "../evil", "sub/../../evil", ".\\evil", "C:evil",
    "evil\x00", "", "x" * 80, "-leading-dash",
])
def test_import_drops_panorama_records_with_ids_that_could_act_as_paths(
        db, tmp_path, evil_id):
    """Regression (post-remediation review): `POST /api/panorama/{pid}/image`
    interpolates the id into a FILENAME, so an id is a path input just like
    `file` is - a crafted bundle must not be able to plant one."""
    (tmp_path / "data" / "panos").mkdir(parents=True)
    setup = {"app": "NightSeam", "sites": [], "rigs": [], "panoramas": [
        {"id": evil_id, "name": "evil", "file": "ok.jpg"}],
        "customs": [], "images": []}
    summary = apply_import_zip(db, _zip_with("NightSeam", setup,
                                            files={"panos/ok.jpg": b"jpeg"}))
    assert summary["panoramas"] == 0
    assert db.panoramas() == []


def test_import_cannot_redirect_the_archive_root(db, tmp_path):
    """Regression (post-remediation review): `settings.image_dir` came from the
    ZIP and was stored verbatim, so the restore then wrote `library/*` into an
    attacker-chosen directory (arbitrary name, extension and content) and
    `/api/export` read that directory back out - a crafted "setup export" was
    an arbitrary-write/arbitrary-read primitive. The documented behaviour is
    that the library is restored into the CURRENTLY CONFIGURED archive, so the
    imported value is ignored and reported."""
    attacker = tmp_path / "attacker_dir"
    setup = {
        "app": "NightSeam",
        "settings": {"image_dir": str(attacker), "bortle": 7},
        "sites": [], "rigs": [], "panoramas": [], "customs": [],
        "images": [{"id": 1, "file": "sub/pwn.bat", "object_name": "x"}],
    }
    summary = apply_import_zip(
        db, _zip_with("NightSeam", setup, files={"library/sub/pwn.bat": b"@echo PWNED"}))

    assert not attacker.exists(), "the imported image_dir was used as a root"
    assert db.setting("image_dir") == str(tmp_path / "archive")
    assert summary["image_dir_ignored"] == str(attacker)
    assert summary["library_files"] == 1
    # the file went into the CONFIGURED archive instead
    restored = tmp_path / "archive" / "sub" / "pwn.bat"
    assert restored.read_bytes() == b"@echo PWNED"
    assert db.images()[0]["file"] == "sub/pwn.bat"
    # other settings from the bundle still apply
    assert db.setting("bortle") == 7


def test_import_without_image_dir_keeps_the_configured_archive(db, tmp_path):
    """A bundle that omits image_dir must not reset the user's archive root."""
    db.set_setting("image_dir", str(tmp_path / "mine"))
    setup = {"app": "NightSeam", "settings": {}, "sites": [], "rigs": [],
             "panoramas": [], "customs": [],
             "images": [{"id": 1, "file": "x.fit", "object_name": "x"}]}
    apply_import_zip(db, _zip_with("NightSeam", setup,
                                  files={"library/x.fit": b"FITS"}))
    assert db.setting("image_dir") == str(tmp_path / "mine")
    assert (tmp_path / "mine" / "x.fit").read_bytes() == b"FITS"


def test_failed_import_rolls_back_everything(db):
    """A bundle that blows up mid-restore must leave the existing data intact.

    Regression: the deletes stayed live on the shared connection and were
    committed by the NEXT unrelated write, silently wiping the user's data."""
    db.add_site("Home", 50.0, 7.8, 150)
    db.set_setting("bortle", 4)
    # sites[0] has no name -> NOT NULL constraint failure half way through
    with pytest.raises(Exception):
        db.import_bundle(settings={"theme": "dome"}, sites=[{}], rigs=[],
                         panoramas=[], customs=[], images=[])
    assert len(db.sites()) == 1                      # rolled back, not wiped
    db.set_setting("edge_space", 180)                # a later, unrelated write
    assert len(db.sites()) == 1                      # ... must not persist a wipe
    assert db.setting("bortle") == 4


def test_import_never_touches_observations(db, tmp_path):
    """The legacy journal table is neither exported nor imported - and an
    import must not delete rows it never carried (AGENTS.md: leave it alone)."""
    db.add_site("Home", 50.0, 7.8, 150)
    db.add_observation("m31", "2026-08-22", "old journal row")
    assert len(db.observations()) == 1
    blob = build_export_zip(db)
    assert "observations" not in json.loads(
        zipfile.ZipFile(io.BytesIO(blob)).read("setup.json"))
    apply_import_zip(db, blob)
    assert len(db.observations()) == 1               # survived the restore


def test_import_rejects_malformed_bundle_shape(db):
    """Non-dict settings / non-list collections are a 400-class error, not a
    half-applied restore."""
    db.add_site("Home", 50.0, 7.8, 150)
    with pytest.raises(ValueError):
        db.import_bundle(settings=[], sites=[], rigs=[], panoramas=[],
                         customs=[], images=[])
    assert len(db.sites()) == 1


def _mutate_bundle(blob, mutate):
    """Copy a bundle with setup.json mutated (test helper)."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    setup = json.loads(src.read("setup.json"))
    mutate(setup)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("setup.json", json.dumps(setup))
        for name in src.namelist():
            if name != "setup.json":
                dst.writestr(name, src.read(name))
    return out.getvalue()


def test_import_rejects_malformed_profiles(db, tmp_path):
    """FIX-4 (review PY1): a panorama profile that is not [] or exactly 720
    finite numbers used to be stored verbatim and then failed OPEN at planning
    time (a blocked target ranked observable). Fail closed, leave data intact."""
    db.add_site("Home", 50.0, 7.8, 150)
    baseline = build_export_zip(db)
    for bad in ([10.0] * 719, [10.0] * 721, ["x"] * 720, [True] * 720,
                [float("nan")] * 720):
        blob = _mutate_bundle(
            baseline,
            lambda s, bad=bad: s["panoramas"].append(
                {"id": "pbad", "name": "bad", "file": "bad.jpg",
                 "profile": bad, "profile_top": []}))
        with pytest.raises(ValueError, match="profile"):
            apply_import_zip(db, blob)
    assert len(db.sites()) == 1          # nothing was replaced
    assert db.setting("bortle", 4) == 4


def test_import_rejects_unhashable_horizon_enum_values(db):
    baseline = build_export_zip(db)
    cases = (
        ("landmarks", [{"name": "oak", "azimuth": 45.0, "altitude": 20.0,
                        "kind": []}], "kind"),
        ("provenance", {"source": {}}, "source"),
    )
    for key, value, message in cases:
        blob = _mutate_bundle(
            baseline,
            lambda setup, key=key, value=value: setup["panoramas"].append({
                "id": "pbad", "name": "bad", "file": "bad.jpg",
                key: value,
            }))
        with pytest.raises(ValueError, match=message):
            apply_import_zip(db, blob)


def test_import_rejects_hostile_settings(db):
    """FIX-4 (review PY2): an opaque imported setting could brick /api/state
    forever (bortle null -> float(None) at every boot). Fail closed instead."""
    db.add_site("Home", 50.0, 7.8, 150)
    for bad in ({"bortle": None}, {"bortle": "x"}, {"min_useful_alt": "x"},
                {"adv_mode": 1}, {"filters": []}):
        blob = _mutate_bundle(build_export_zip(db),
                              lambda s, bad=bad: s["settings"].update(bad))
        with pytest.raises(ValueError):
            apply_import_zip(db, blob)
    assert len(db.sites()) == 1
    db.set_setting("edge_space", 100)    # a later write must not persist a wipe
    assert len(db.sites()) == 1


def test_legacy_brightness_setting_is_dropped_from_restore(db):
    """A legacy provider key is inert compatibility data, not a bad setting
    that should reject an otherwise valid backup."""
    blob = _mutate_bundle(build_export_zip(db), lambda s: s["settings"].update(
        {"sky_brightness_provider": {"unexpected": "value"}}))
    apply_import_zip(db, blob)
    assert db.setting("sky_brightness_provider") is None
    exported = build_export_zip(db)
    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        setup = json.loads(zf.read("setup.json"))
    assert "sky_brightness_provider" not in setup["settings"]


def test_legacy_theme_and_brightness_rows_migrate_on_database_open(db):
    """Opening an existing profile converges every retired theme to the sole
    Workbench value and deletes the removed estimated-brightness preference."""
    db.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                    ("theme", json.dumps("dome")))
    db.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                    ("sky_brightness_provider", json.dumps("nasa")))
    db.conn.commit()

    reopened = Database(db.path)
    try:
        assert reopened.setting("theme") == "workbench"
        assert reopened.setting("sky_brightness_provider") is None
        reopened.import_bundle(settings={"theme": "classic"}, sites=[], rigs=[],
                              panoramas=[], customs=[], images=[])
        assert reopened.setting("theme") == "workbench"
        assert reopened.all_settings()["theme"] == "workbench"
    finally:
        reopened.conn.close()


def test_import_rejects_a_hostile_min_vis(db):
    """PY-02 (review 0.6.32): `min_vis` is the FOURTH int-coerced setting and had
    no import check, so `"abc"`/`null` was stored verbatim and `/api/state` then
    raised `int("abc")` on EVERY boot (`static/js/main.js` awaits that route
    before rendering anything). Same rule as bortle/edge_space - and a finite
    float stays a legitimate value, coerced and clamped."""
    db.add_site("Home", 50.0, 7.8, 150)
    for bad in ("abc", None, float("nan"), True):
        blob = _mutate_bundle(build_export_zip(db),
                              lambda s, bad=bad: s["settings"].update(
                                  {"min_vis": bad}))
        with pytest.raises(ValueError, match="min_vis"):
            apply_import_zip(db, blob)
    assert len(db.sites()) == 1                  # nothing was replaced
    # a finite out-of-range number is clamped, not refused
    blob = _mutate_bundle(build_export_zip(db),
                          lambda s: s["settings"].update({"min_vis": 150}))
    apply_import_zip(db, blob)
    assert db.setting("min_vis") == 100


def test_import_rejects_nonfinite_site_and_rig_numbers(db):
    """PY3 follow-up (adversarial verification): imported site/rig/pano
    numbers reach /api/state, so a crafted zip with Infinity used to 500 the
    route forever. Must fail closed before any write."""
    db.add_site("Home", 50.0, 7.8, 150)
    for bad in ({"lat": float("inf")},
                {"lat": 1.0, "lon": float("nan")}):
        blob = _mutate_bundle(build_export_zip(db),
                              lambda s, bad=bad: s["sites"][0].update(bad))
        with pytest.raises(ValueError, match="finite"):
            apply_import_zip(db, blob)
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["rigs"].append({"id": 1, "name": "r",
                                    "focal_mm": float("inf"),
                                    "sensor_w": 6.0, "sensor_h": 4.0}))
    with pytest.raises(ValueError, match="finite"):
        apply_import_zip(db, blob)
    assert len(db.sites()) == 1                    # nothing was replaced
    assert db.sites()[0]["lat"] == 50.0


def test_import_rejects_custom_ids_without_the_u_prefix(db):
    """PY4 (review): a bundle could insert a custom target with a BUILTIN id
    (e.g. "m31"); /api/catalog then shipped duplicate ids that the API could
    not delete. Fail closed."""
    db.add_site("Home", 50.0, 7.8, 150)
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["customs"].append(
            {"id": "m31", "primary_name": "Evil", "otype": "galaxy",
             "ra_hours": 0.7, "dec_deg": 41.3, "vmag": 4, "size_am": "",
             "constellation": "", "blurb": ""}))
    with pytest.raises(ValueError, match="u_"):
        apply_import_zip(db, blob)
    assert len(db.sites()) == 1


def _zip_with(app_name: str, setup: dict, files: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("setup.json", json.dumps(setup))
        for name, data in (files or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


# ------------------------------------------------- PY04-F2: the storage boundary

def _image_row(**overrides) -> dict:
    row = {"file": "M 31/2026-08-22_2314/lights/m31.fit",
           "orig_name": "m31.fit", "object_id": None, "object_name": "M 31",
           "ra_hours": 0.712, "dec_deg": 41.269, "exposure": 300.0,
           "date_obs": "2026-08-22T23:14:05", "filter": "L",
           "telescope": "Seestar S50", "imagetyp": "light",
           "ingested_at": "2026-08-22T23:20:00"}
    row.update(overrides)
    return row


def test_import_rejects_nonfinite_image_numbers(db, tmp_path):
    """PY04-F2 (review round 2): the image rows were the ONE record type the
    import boundary did not check, and `json.loads` accepts the bare tokens
    Infinity/NaN - so a crafted bundle stored `inf`, and /api/library then
    answered 500 on EVERY later request. Fail closed before any write."""
    _seed(db, tmp_path)
    for bad in ({"exposure": float("inf")}, {"ra_hours": float("nan")},
                {"dec_deg": float("-inf")}):
        blob = _mutate_bundle(build_export_zip(db),
                              lambda s, bad=bad: s["images"][0].update(bad))
        with pytest.raises(ValueError, match="image .* finite number"):
            apply_import_zip(db, blob)
    assert db.images()[0]["exposure"] == 300.0      # nothing was replaced
    assert db.images()[0]["ra_hours"] == 0.712
    # a custom target's numbers reach /api/catalog, so they fail closed too
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["customs"][0].update({"ra_hours": float("inf")}))
    with pytest.raises(ValueError, match="custom .* finite number"):
        apply_import_zip(db, blob)
    assert len(db.customs()) == 1


def test_add_image_cannot_store_a_nonfinite_number(db):
    """PY04-F2: the guard sits at the single storage boundary, so NO caller can
    put a non-finite ra/dec/exposure into the images table - a stored `inf` is
    what makes /api/library (allow_nan=False) 500 forever."""
    img_id = db.add_image(_image_row(
        ra_hours=float("inf"), dec_deg=float("nan"), exposure=float("-inf")))
    row = db.image(img_id)
    assert row["ra_hours"] is None and row["dec_deg"] is None
    assert row["exposure"] is None
    # finite values (int or float) and non-numeric values are untouched
    ok_id = db.add_image(_image_row(ra_hours=5, dec_deg=-27.958, exposure=60.0))
    ok = db.image(ok_id)
    assert ok["ra_hours"] == 5 and ok["dec_deg"] == -27.958
    assert ok["exposure"] == 60.0


def test_export_of_a_corrupt_library_is_importable_and_stays_finite(db, tmp_path):
    """PY04-F2 recovery: a database that ALREADY carries a non-finite row (from
    an older build) used to export a bare `Infinity` token - un-parseable by a
    strict JSON reader and, re-imported, the corruption came back. The export
    now writes NULL for an unusable number, so the user's own backup restores
    cleanly and the stored values are finite."""
    _seed(db, tmp_path)
    # simulate the pre-existing corruption (bypasses add_image's guard)
    db.conn.execute("UPDATE images SET ra_hours=?, exposure=?",
                    (float("inf"), float("nan")))
    db.conn.commit()

    blob = build_export_zip(db)
    raw = zipfile.ZipFile(io.BytesIO(blob)).read("setup.json").decode("utf-8")
    assert "Infinity" not in raw and "NaN" not in raw

    def _no_constant(token):
        raise AssertionError(f"non-standard JSON token {token!r}")

    setup = json.loads(raw, parse_constant=_no_constant)   # strict parse
    assert setup["images"][0]["exposure"] is None
    assert setup["images"][0]["ra_hours"] is None

    db2 = Database(tmp_path / "other.db")
    db2.set_setting("image_dir", str(tmp_path / "archive"))
    apply_import_zip(db2, blob)                          # must not raise
    row = db2.images()[0]
    for key in ("ra_hours", "dec_deg", "exposure"):
        assert row[key] is None or math.isfinite(float(row[key])), (key, row)


# ------------------------------------------------- PY-01 / PY-03: import boundary

def test_import_rejects_non_list_strokes(db, tmp_path):
    """PY-03: `strokes` is the THIRD stored panorama array and had no shape check,
    so `{}` / `"abc"` / 7 were persisted and BOTH editors iterate it - the desktop
    threw `TypeError: ... is not iterable` for that record. Fail closed like the
    two profile arrays, leaving the existing data untouched."""
    _seed(db, tmp_path)
    for bad in ({}, "abc", 7, ["not-a-stroke"], [1, 2]):
        blob = _mutate_bundle(
            build_export_zip(db),
            lambda s, bad=bad: s["panoramas"].append(
                {"id": "pstrokes", "name": "bad", "file": "bad.jpg",
                 "strokes": bad, "profile": [], "profile_top": []}))
        with pytest.raises(ValueError, match="strokes"):
            apply_import_zip(db, blob)
    assert all(isinstance(p["strokes"], list) for p in db.panoramas())
    assert [i["file"] for i in db.images()]        # the library was not replaced


def test_import_rejects_two_records_for_one_normalized_path(db, tmp_path):
    """PY-01: `safe_rel` canonicalizes `a/./b`, `a//b` and `a/x/../b` to ONE path,
    so a bundle could carry several records for one target - the restore loop wrote
    every member there (ZIP order, last writer wins) while `import_bundle` had
    already inserted all the rows and the summary counted all of them. That is a
    silent frame loss plus phantom library rows, so the bundle is refused."""
    _seed(db, tmp_path)
    before = [i["file"] for i in db.images()]
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["images"].extend([
            _image_row(file="M 31/dup.fit"),
            _image_row(file="M 31/./dup.fit"),
            _image_row(file="M 31/x/../dup.fit")]))
    with pytest.raises(ValueError, match="same stored path"):
        apply_import_zip(db, blob)
    assert [i["file"] for i in db.images()] == before      # nothing was replaced
    # and two DISTINCT normalized paths are still accepted
    ok = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["images"].extend([
            _image_row(file="M 31/a.fit"),
            _image_row(file="M 31/b.fit")]))
    apply_import_zip(db, ok)
    assert {"M 31/a.fit", "M 31/b.fit"} <= {i["file"] for i in db.images()}


def test_import_rejects_a_non_token_active_pano_id(db, tmp_path):
    """PY-05: `active_pano_id` is a record id interpolated into URLs by the
    panorama routes, so the import boundary applies the same token rule - a
    bundle could otherwise install an "active panorama" that /api/state
    advertises and no id-taking route ever resolves."""
    _seed(db, tmp_path)
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["settings"].update({"active_pano_id": "../../evil"}))
    with pytest.raises(ValueError, match="plain panorama id"):
        apply_import_zip(db, blob)


def test_a_failed_safety_copy_is_reported_not_swallowed(db, tmp_path, monkeypatch):
    """PY-02: the safety copy is best-effort by design, but its failure used to be
    a bare `None` - indistinguishable in the summary from "not attempted" - so
    the documented promise ("before touching anything, the CURRENT setup is
    written to <db>.backup-auto.zip") could be silently absent."""
    import app.backup as backup

    _seed(db, tmp_path)
    blob = build_export_zip(db)          # build BEFORE patching the writer

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "_export_zip_to", boom)
    summary = apply_import_zip(db, blob)      # the restore itself still succeeds
    assert summary["ok"] is True
    assert summary["auto_backup"] is None
    assert summary.get("auto_backup_error"), "the degradation must be visible"
    assert "disk full" in summary.get("auto_backup_error", "")


def test_rolling_backup_is_a_consistent_sqlite_snapshot(db, tmp_path):
    """PY-07: `make_backup` used `shutil.copyfile` on the LIVE database with no
    lock, so a write landing mid-copy could leave a torn page set in the only
    recovery artifact. It now takes a SQLite backup snapshot, which must open
    cleanly and contain the data."""
    import sqlite3
    import inspect
    from pathlib import Path as _P

    # the MECHANISM is the point: a torn copy is not reliably reproducible, so the
    # pin also asserts the snapshot API is what writes the artifact
    assert "self.conn.backup(" in inspect.getsource(Database.make_backup), (
        "PY-07: make_backup must use SQLite's backup API, not a byte copy")
    # the docstring explains the old bug, so assert on the CALL, not the word
    assert "shutil.copyfile(self.path" not in inspect.getsource(Database.make_backup)

    db.add_site("Home", 50.0, 7.8, 150)
    db.make_backup()
    bak = _P(str(db.path) + ".backup")
    assert bak.is_file() and bak.stat().st_size > 0
    conn = sqlite3.connect(str(bak))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 1
    finally:
        conn.close()


# ------------------------------------------------------- review 0.6.32 findings

def test_restore_is_atomic_when_a_payload_cannot_be_written(db, tmp_path,
                                                            monkeypatch):
    """PY-03 (review 0.6.32): the restore committed the database FIRST and wrote
    the payloads after, so a write failure answered 400/500 while the library had
    already been replaced and one row pointed at a file that would never exist -
    "the import failed" and "nothing was replaced" were different statements.
    The payloads are staged first now (BUG-001 moved the swap after the database
    transaction), so a disk-full failure leaves both the database and the
    existing payload files exactly as they were."""
    import app.backup as backup

    _seed(db, tmp_path)
    before = [s["name"] for s in db.sites()]
    # the bundle carries a brand-new panorama record: pre-fix, `import_bundle`
    # committed its ROW before the payload phase ran, so the failing write still
    # left the row behind (and `pano1.jpg` truncated by the failed write)
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["panoramas"].append(
            {"id": "newpano", "name": "new", "file": "new.jpg",
             "profile": [], "profile_top": []}))

    # the FIRST stage succeeds (its target file is new), the SECOND one hits a
    # full disk - the case where a naive swap would already have left a file
    real_stage = backup._PayloadStage.stage
    calls = {"n": 0}

    def flaky(self, zf, member, target, budget):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError(28, "No space left on device")
        return real_stage(self, zf, member, target, budget)

    monkeypatch.setattr(backup._PayloadStage, "stage", flaky)
    with pytest.raises(OSError):
        apply_import_zip(db, blob)

    assert [s["name"] for s in db.sites()] == before, "the library was replaced"
    assert [p["id"] for p in db.panoramas()] == ["p1"], "a row survived the replace"
    assert not (tmp_path / "data" / "panos" / "new.jpg").exists()
    # the pre-import payload is not truncated by the failed restore either
    assert (tmp_path / "data" / "panos" / "pano1.jpg").read_bytes() == b"JPEGDATA"
    assert not (tmp_path / "data" / "panos" / "payload.journal").exists()


def test_a_successful_restore_never_carries_the_incomplete_marker(db, tmp_path):
    """PY-03: `restore_incomplete` is only valid on the ERROR path, so the
    success path must not carry it (it would make every restore look partial)."""
    _seed(db, tmp_path)
    summary = apply_import_zip(db, build_export_zip(db))
    assert summary["ok"] is True
    assert "restore_incomplete" not in summary


def test_a_failure_after_the_replace_is_named_in_the_error():
    """PY-03, the honest-status half: an import that fails AFTER the database was
    replaced must not answer a bare `400 Failed to write restored files`, which
    reads as "bundle rejected - nothing happened". The route names the partial
    state, and only when the marker is set."""
    from app.server import _restore_error

    plain = _restore_error("zip member too large: panos/x.jpg", {})
    assert plain == "zip member too large: panos/x.jpg"
    partial = _restore_error("zip member too large: panos/x.jpg",
                             {"restore_incomplete": True})
    assert "already replaced" in partial
    assert "backup-auto.zip" in partial


# ------------------------------------------------------- SP-01 aggregate cap

def _oversized_bundle_member(declared: int) -> bytes:
    """A tiny zip whose member DECLARES `declared` uncompressed bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr("setup.json", json.dumps(
            {"app": "NightSeam", "version": "test", "settings": {},
             "sites": [], "rigs": [], "customs": [], "images": [],
             "panoramas": [{"id": "p1", "name": "big", "file": "big.jpg"}]}))
        with z.open("panos/big.jpg", "w") as dst:
            dst.write(b"\0" * declared)
    return out.getvalue()


def test_import_refuses_a_bundle_that_expands_past_the_restore_budget(
        db, tmp_path, monkeypatch):
    """SP-01 (review 0.6.32): `MAX_MEMBER_BYTES` bounded ONE member, but nothing
    bounded the sum, so a 42 KB ZIP could write 40 MiB (measured 996:1) and a
    2 GiB upload could fill the disk. The whole restore is refused now, before
    the safety copy and before anything is written."""
    import app.backup as backup

    db.add_site("Home", 50.0, 7.8, 150)
    assert backup.MAX_RESTORE_BYTES >= 1 << 30       # the shipped budget
    blob = _oversized_bundle_member((1 << 20) + 1)
    # a 1 MiB budget keeps the test from materialising the shipped 4 GiB
    monkeypatch.setattr(backup, "MAX_RESTORE_BYTES", 1 << 20)
    with pytest.raises(ValueError, match="expands to more than"):
        apply_import_zip(db, blob)
    assert not (tmp_path / "data" / "panos" / "big.jpg").exists()
    assert len(db.sites()) == 1                      # nothing was replaced


def test_import_never_writes_a_payload_for_a_record_it_dropped(db, tmp_path):
    """PY-06/SP-05 (review 0.6.32): the write loop iterated the RAW bundle list,
    so a panorama whose id failed `safe_token` never became a row but its payload
    was still written into `data/panos` - an orphan file, never cleaned, while
    the summary reported the pair `panoramas: 0, pano_images: 1`."""
    pdir = tmp_path / "data" / "panos"
    pdir.mkdir(parents=True)
    setup = {"app": "NightSeam", "version": "test", "settings": {},
             "sites": [], "rigs": [], "customs": [], "images": [],
             "panoramas": [{"id": "../evil", "name": "evil", "file": "ok.jpg"}]}
    summary = apply_import_zip(db, _zip_with("NightSeam", setup,
                                            files={"panos/ok.jpg": b"JPEG"}))
    assert summary["panoramas"] == 0
    assert summary["pano_images"] == 0, "a dropped record's payload was written"
    assert not (pdir / "ok.jpg").exists()


def test_import_rejects_two_custom_records_with_one_id(db):
    """PY-07 (review 0.6.32): `INSERT OR REPLACE INTO custom_objects` silently
    collapsed two bundle targets sharing an id into ONE row while the summary
    reported both - the silent-loss shape PY-01 was fixed for, in its record-id
    form."""
    db.add_site("Home", 50.0, 7.8, 150)
    dup = {"id": "u_dup", "primary_name": "Dup", "otype": "galaxy",
           "ra_hours": 1.0, "dec_deg": 2.0}
    blob = _mutate_bundle(build_export_zip(db),
                          lambda s: s["customs"].extend([dict(dup), dict(dup)]))
    with pytest.raises(ValueError, match="same id"):
        apply_import_zip(db, blob)
    assert db.customs() == []            # nothing was replaced
    assert len(db.sites()) == 1


def test_import_of_a_structurally_broken_bundle_is_a_400_not_a_500(db):
    """PY-04 (review 0.6.32): a bundle that got past the initial read and then
    blew up inside the transaction (`sqlite3.IntegrityError`, or an
    `AttributeError` from a non-dict record) surfaced as an opaque 500 while the
    API documents ValueError -> 400. The transaction rolled back either way, so
    only the error contract was wrong - and a 400 must not be reported for a
    shape `import_bundle` can simply refuse."""
    db.add_site("Home", 50.0, 7.8, 150)
    # (a) NULL name -> NOT NULL constraint inside the transaction
    blob = _mutate_bundle(build_export_zip(db),
                          lambda s: s["sites"].append(
                              {"id": 2, "name": None, "lat": 1.0, "lon": 2.0,
                               "elev": 0}))
    with pytest.raises(ValueError, match="malformed setup bundle"):
        apply_import_zip(db, blob)
    # (b) a site entry that is not an object at all
    blob = _mutate_bundle(build_export_zip(db),
                          lambda s: s["sites"].append("not-a-site"))
    with pytest.raises(ValueError, match="malformed setup bundle"):
        apply_import_zip(db, blob)
    # (c) a duplicate panorama id -> UNIQUE constraint inside the transaction
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["panoramas"].extend([
            {"id": "pdup", "name": "a", "file": "a.jpg"},
            {"id": "pdup", "name": "b", "file": "b.jpg"}]))
    with pytest.raises(ValueError, match="malformed setup bundle"):
        apply_import_zip(db, blob)
    assert len(db.sites()) == 1          # nothing was replaced
    assert db.panoramas() == []


# --------------------------------------------- BUGS.md BUG-001/002/004 (2026-09-17)

def _replace_setup_json(blob: bytes, raw_json: str) -> bytes:
    """A copy of `blob` whose setup.json is the RAW text (a scalar, a list)."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("setup.json", raw_json)
        for name in src.namelist():
            if name != "setup.json":
                dst.writestr(name, src.read(name))
    return out.getvalue()


def _add_member(blob: bytes, name: str, data: bytes) -> bytes:
    """A copy of `blob` with one extra (or replaced) member."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for entry in src.namelist():
            dst.writestr(entry, data if entry == name else src.read(entry))
        if name not in src.namelist():
            dst.writestr(name, data)
    return out.getvalue()


def test_import_of_a_scalar_or_non_object_collection_is_a_400(db):
    """BUG-004: the decoded top level and every record were used through `.get()`
    BEFORE either was proven to be an object, so `panoramas: ["not-an-object"]`
    raised AttributeError (HTTP 500) and a scalar top level escaped even earlier
    - after the safety copy had already been written. One structural preflight
    now runs immediately after `json.loads`."""
    db.add_site("Home", 50.0, 7.8, 150)
    # (a) a scalar / list / null top level
    for top in ('"just-a-string"', "[]", "null", "5"):
        blob = _replace_setup_json(_zip_with("NightSeam", {}), top)
        with pytest.raises(ValueError):
            apply_import_zip(db, blob)
    # (b) a non-object entry in EVERY collection (panoramas/images reached
    #     `_accepted_records` before the shape check, so they were the live 500s)
    for key in ("sites", "rigs", "panoramas", "customs", "images"):
        blob = _mutate_bundle(build_export_zip(db),
                              lambda s, key=key: s[key].append("not-an-object"))
        with pytest.raises(ValueError, match="malformed setup bundle"):
            apply_import_zip(db, blob)
    # (c) a collection that is not a list, and a scalar settings object
    for key in ("sites", "rigs", "panoramas", "customs", "images"):
        blob = _mutate_bundle(build_export_zip(db),
                              lambda s, key=key: s.update({key: {"not": "a list"}}))
        with pytest.raises(ValueError, match="malformed setup bundle"):
            apply_import_zip(db, blob)
    blob = _mutate_bundle(build_export_zip(db),
                          lambda s: s.update({"settings": []}))
    with pytest.raises(ValueError, match="malformed setup bundle"):
        apply_import_zip(db, blob)
    assert len(db.sites()) == 1          # nothing was replaced, ever


def test_a_rejected_restore_leaves_the_payload_bytes_untouched(db, tmp_path):
    """BUG-001: `apply_import_zip` called `_write_payloads` BEFORE
    `Database.import_bundle` validated the bundle, so a bundle carrying a valid
    colliding payload plus one invalid record replaced the user's file
    (`ORIGINAL` -> `REPLACED`) and then reported "the import failed" - read as
    "nothing happened". The payload is now staged, not swapped in, until the
    database transaction has committed."""
    _seed(db, tmp_path)
    pano = tmp_path / "data" / "panos" / "pano1.jpg"
    pano.write_bytes(b"ORIGINAL")
    # a VALID panorama payload that collides with the existing file + an INVALID
    # site id (a non-integer primary key): the pair that reproduced the finding,
    # because `import_bundle` only fails on it inside the transaction
    blob = _mutate_bundle(build_export_zip(db),
                          lambda s: s["sites"].append(
                              {"id": "not-an-int", "name": "bad", "lat": 1.0,
                               "lon": 1.0, "elev": 0}))
    blob = _add_member(blob, "panos/pano1.jpg", b"REPLACED")
    with pytest.raises(ValueError):
        apply_import_zip(db, blob)
    assert pano.read_bytes() == b"ORIGINAL", \
        "a rejected restore overwrote an existing payload"
    assert len(db.sites()) == 1 and [p["id"] for p in db.panoramas()] == ["p1"]
    # an invalid CUSTOM id must be refused at the same boundary (BUG-002): with
    # the whole bundle rejected, the colliding payload is still untouched
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["customs"].append(
            {"id": 'u_x" onmouseover="globalThis.__pwned=1',
             "primary_name": "evil"}))
    blob = _add_member(blob, "panos/pano1.jpg", b"REPLACED-2")
    with pytest.raises(ValueError):
        apply_import_zip(db, blob)
    assert pano.read_bytes() == b"ORIGINAL"
    # the seeded target is the ONLY one: the hostile record never became a row
    assert [c["id"] for c in db.customs()] == ["u_seed1"]


def test_a_rejected_restore_leaves_no_staging_residue(db, tmp_path):
    """BUG-001: the journal may never survive a restore - not as `.part` files,
    not as a `payload.journal` directory beside the user's photos."""
    _seed(db, tmp_path)
    pano_root = tmp_path / "data" / "panos"
    for blob in (
            # rejected in the structural preflight (before the stage exists)
            _mutate_bundle(build_export_zip(db),
                           lambda s: s["images"].append("nope")),
            # rejected by field validation (the stage is not created either)
            _mutate_bundle(build_export_zip(db),
                           lambda s: s["rigs"].__setitem__(
                               0, {"id": 1, "name": "r", "focal_mm": "wide"}))):
        with pytest.raises(ValueError):
            apply_import_zip(db, blob)
    leftover = [p.name for p in pano_root.iterdir()
                if p.name.endswith(".part") or p.name == "payload.journal"]
    assert leftover == []
    assert (pano_root / "pano1.jpg").read_bytes() == b"JPEGDATA"


def test_a_successful_restore_swaps_the_payload_in(db, tmp_path):
    """The other half of BUG-001: staging must not lose a legitimate payload.
    The rewrite lands, the summary counts it, and the journal is gone."""
    _seed(db, tmp_path)
    blob = _add_member(build_export_zip(db), "panos/pano1.jpg", b"NEWER")
    summary = apply_import_zip(db, blob)
    assert summary["ok"] is True and summary["pano_images"] == 1
    assert (tmp_path / "data" / "panos" / "pano1.jpg").read_bytes() == b"NEWER"
    assert not (tmp_path / "data" / "panos" / "payload.journal").exists()
    assert db.panoramas()[0]["id"] == "p1"


def test_import_normalizes_a_hostile_imagetyp(db, tmp_path):
    """BUG-002: `imagetyp` is stored verbatim and `static/js/archive.js` puts the
    per-session type rollup into a `title` ATTRIBUTE, so an imported value with a
    quote became an executable handler in the desktop shell (which then allowed
    inline script). The import boundary normalizes it through the SAME
    `ingest.classify_frame` the FITS path uses."""
    _seed(db, tmp_path)
    blob = _mutate_bundle(
        build_export_zip(db),
        lambda s: s["images"][0].update(
            {"imagetyp": 'light" onmouseover="globalThis.__pwned=1'}))
    apply_import_zip(db, blob)
    stored = db.images()[0]["imagetyp"]
    assert stored == "light", stored
    assert '"' not in stored and "<" not in stored


def test_import_rejects_a_custom_id_that_is_not_a_plain_token(db):
    """BUG-002: the boundary required only the `u_` prefix, so an imported id
    carrying a quote reached `data-delcust="${o.id}"` in static/js/setup.js and
    rendered as an event handler. The id rule is now `u_` AND `safe_token`."""
    db.add_site("Home", 50.0, 7.8, 150)
    db.add_custom({"id": "u_keep", "primary_name": "Keep", "otype": "galaxy",
                   "ra_hours": 1.0, "dec_deg": 2.0})
    for evil in ('u_x" onmouseover="globalThis.__pwned=1',
                 "u_x' onmouseover='x", "u_x><script>x</script>",
                 "u_../evil", "u_/abs", "m31", "u_x y", "u_x&amp;y"):
        blob = _mutate_bundle(
            build_export_zip(db),
            lambda s, evil=evil: s["customs"].append(
                {"id": evil, "primary_name": "evil", "otype": "galaxy",
                 "ra_hours": 1.0, "dec_deg": 2.0}))
        with pytest.raises(ValueError, match="custom target id"):
            apply_import_zip(db, blob)
    # the seeded target is still the only one: nothing was replaced by a refusal
    assert [c["id"] for c in db.customs()] == ["u_keep"]


def test_the_custom_id_rule_is_u_prefix_and_token():
    """The rule itself, pinned directly: a user row needs BOTH the `u_` marker
    (the custom routes key off it) and the plain-token shape (it is interpolated
    into attributes). A bare `u_` is a token, so it stays legal - over-tightening
    the rule would reject an id an older NightSeam could legitimately have
    stored."""
    from app.database import custom_id_problem

    assert custom_id_problem("u_keep") is None
    assert custom_id_problem("u_") is None
    assert custom_id_problem("u_seed1-2") is None
    for bad in ("m31", "keep", "u_x y", 'u_x"', "u_../evil", "", None, 7):
        assert custom_id_problem(bad) is not None, bad
