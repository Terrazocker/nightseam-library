"""Per-object stacks folders: detection, import routing, reindex and Library
grouping (<archive>/<object>/stacks/<file>)."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import imglib, ingest
from app.database import Database
from app.ingest import is_stack
from app.server import create_app
from _util import make_fits


@pytest.fixture
def store(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    db = Database(tmp_path / "stacks.sqlite3")
    try:
        yield db, archive
    finally:
        db.conn.close()


class _Upload:
    def __init__(self, data, name):
        self.data, self.filename, self.size = data, name, len(data)

    async def read(self, size):
        result, self.data = self.data[:size], self.data[size:]
        return result


def _fits(*cards, pixels=b"pixels"):
    return make_fits([("SIMPLE", "T"), ("OBJECT", "'Unlisted nebula'"),
                      ("EXPTIME", "60"), ("DATE-OBS", "'2026-08-22T20:00:00'"),
                      *cards]) + pixels


def _import(db, archive, data, name, **options):
    return asyncio.run(ingest.ingest_one(db, [], archive, _Upload(data, name), **options))


# ---------------------------------------------------------------- detection

@pytest.mark.parametrize("header, name", [
    ({"IMAGETYP": "Light", "STACKCNT": 30}, "frame.fit"),          # Siril, Seestar
    ({"IMAGETYP": "Light", "NCOMBINE": 12}, "frame.fit"),
    ({"IMAGETYP": "Light", "NCOMBINE": "12"}, "frame.fit"),
    ({"IMAGETYP": "Master Light"}, "frame.fit"),                    # PixInsight WBPP
    ({"IMAGETYP": "Stacked"}, "frame.fit"),
    ({"IMAGETYP": "Light"}, "Stacked_214_M 31_10.0s_IRCUT_20260822-201500.fit"),
    ({}, "DSO_Stacked_12_M 81_10.0s.fits"),
    ({"IMAGETYP": "Light"}, "r_pp_light_stacked.fit"),             # Siril script output
])
def test_stack_markers_are_recognized(header, name):
    assert is_stack(header, name)


@pytest.mark.parametrize("header, name", [
    ({"IMAGETYP": "Light"}, "Light_M 31_10.0s_IRCUT_20260822-201500.fit"),
    ({"IMAGETYP": "Light", "STACKCNT": 1}, "frame.fit"),
    ({"IMAGETYP": "Light", "NCOMBINE": 1}, "frame.fit"),
    ({"IMAGETYP": "Light", "NCOMBINE": True}, "frame.fit"),
    ({"IMAGETYP": "Light", "NCOMBINE": "many"}, "frame.fit"),
    ({"IMAGETYP": "Light"}, "unstacked.fit"),
    ({"IMAGETYP": "Light"}, "stackedframe.fit"),
    # master calibration keeps its calibration folder (PY-09)
    ({"IMAGETYP": "Master Dark", "NCOMBINE": 40}, "master_dark_stacked.fit"),
    ({"IMAGETYP": "Master Flat", "NCOMBINE": 40}, "frame.fit"),
    ({"IMAGETYP": "Master Bias", "STACKCNT": 40}, "frame.fit"),
])
def test_single_exposures_and_master_calibration_are_not_stacks(header, name):
    assert not is_stack(header, name)


# ---------------------------------------------------------------- import

def test_import_routes_stack_to_object_stacks_folder(store):
    db, archive = store
    light = _import(db, archive, _fits(("IMAGETYP", "'LIGHT'")), "light_001.fit")
    stack = _import(db, archive, _fits(("IMAGETYP", "'LIGHT'"), ("STACKCNT", "30"),
                                       pixels=b"stacked"), "Stacked_30_nebula.fit")
    assert light["ok"] and stack["ok"]
    assert stack["imagetyp"] == "stack"
    assert stack["stored"] == "Unlisted nebula/stacks/Stacked_30_nebula.fit"
    assert (archive / stack["stored"]).read_bytes().endswith(b"stacked")
    assert light["imagetyp"] == "light" and "/lights/" in light["stored"]
    rows = {row["orig_name"]: row for row in db.images()}
    assert rows["Stacked_30_nebula.fit"]["imagetyp"] == "stack"
    assert rows["Stacked_30_nebula.fit"]["file"] == stack["stored"]


def test_every_import_keeps_an_empty_stacks_folder_beside_sessions(store):
    db, archive = store
    result = _import(db, archive, _fits(("IMAGETYP", "'LIGHT'")), "light_001.fit")
    assert result["ok"]
    stacks = archive / "Unlisted nebula" / "stacks"
    assert stacks.is_dir() and not any(stacks.iterdir())
    # an empty stacks folder is a valid layout for reindex
    assert ingest.reindex_library(db, archive)["failed"] == 0


def test_explicit_frame_choice_overrides_stack_detection(store):
    db, archive = store
    data = _fits(("IMAGETYP", "'LIGHT'"), ("STACKCNT", "30"))
    forced = _import(db, archive, data, "Stacked_30_nebula.fit", frame_override="light")
    assert forced["ok"] and forced["imagetyp"] == "light" and "/lights/" in forced["stored"]
    other = _import(db, archive, _fits(("IMAGETYP", "'LIGHT'"), pixels=b"no markers"),
                    "autosave.fit", frame_override="stack")
    assert other["ok"] and other["stored"] == "Unlisted nebula/stacks/autosave.fit"


def test_duplicate_stack_keeps_its_original_record(store):
    db, archive = store
    data = _fits(("IMAGETYP", "'LIGHT'"), ("NCOMBINE", "8"))
    first = _import(db, archive, data, "a_stacked.fit")
    again = _import(db, archive, data, "renamed.fit")
    assert first["ok"] and again["ok"] and again["skipped"]
    assert len(db.images()) == 1


# ---------------------------------------------------------------- reindex

def test_reindex_records_files_saved_into_stacks_folder(store):
    db, archive = store
    folder = archive / "M 31" / "stacks"
    folder.mkdir(parents=True)
    (folder / "siril_result.fit").write_bytes(
        _fits(("IMAGETYP", "'LIGHT'"), ("OBJECT", "'M 31'"), pixels=b"from siril"))
    result = ingest.reindex_library(db, archive)
    assert result["added"] == 1 and result["failed"] == 0
    (row,) = db.images()
    assert row["imagetyp"] == "stack"
    assert row["file"] == "M 31/stacks/siril_result.fit"
    assert ingest.reindex_library(db, archive)["added"] == 0


def test_reindex_refuses_stacks_inside_a_session(store):
    db, archive = store
    folder = archive / "M 31" / "night_2026-08-22_180000_abcdef0123456789" / "stacks"
    folder.mkdir(parents=True)
    (folder / "x.fit").write_bytes(_fits(("IMAGETYP", "'LIGHT'")))
    with pytest.raises(ValueError, match="stacks"):
        ingest.reindex_library(db, archive)
    assert db.images() == []


def test_reindex_keeps_folder_class_for_stacks_filed_as_lights(store):
    """Stacks imported before this feature stay where they are."""
    db, archive = store
    folder = archive / "M 31" / "lights"
    folder.mkdir(parents=True)
    (folder / "Stacked_30_M 31.fit").write_bytes(_fits(("STACKCNT", "30")))
    ingest.reindex_library(db, archive)
    (row,) = db.images()
    assert row["imagetyp"] == "light"


# ---------------------------------------------------------------- library

def test_library_keeps_stacks_out_of_sessions_and_totals(store):
    db, archive = store
    _import(db, archive, _fits(("IMAGETYP", "'LIGHT'")), "light_001.fit")
    _import(db, archive, _fits(("IMAGETYP", "'LIGHT'"), ("STACKCNT", "30"),
                               ("EXPTIME", "1800"), pixels=b"stacked"), "Stacked_30.fit")
    (obj,) = imglib.group_library(db.images(), [])
    assert obj["n_images"] == 1 and obj["total_exposure"] == 60.0
    assert set(obj["types"]) == {"light"}
    assert obj["n_stacks"] == 1 and obj["stacks"][0]["orig_name"] == "Stacked_30.fit"
    assert "content_sha256" not in obj["stacks"][0]
    (session,) = obj["sessions"]
    assert session["n_images"] == 1 and set(session["types"]) == {"light"}
    stack_id = obj["stacks"][0]["id"]
    assert stack_id not in session["image_ids"]
    assert imglib.session_for_anchor(db.images(), stack_id) is None


def test_library_object_with_only_stacks(store):
    db, archive = store
    _import(db, archive, _fits(("IMAGETYP", "'Master Light'")), "integration.fit")
    (obj,) = imglib.group_library(db.images(), [])
    assert obj["name"] == "Unlisted nebula"
    assert obj["n_images"] == 0 and obj["sessions"] == [] and obj["n_stacks"] == 1


def test_stack_cannot_anchor_calibration(store):
    db, archive = store
    stack = _import(db, archive, _fits(("STACKCNT", "30")), "Stacked_30.fit")
    with pytest.raises(ValueError):
        ingest.calibration_import_target(db, archive, stack["id"], "dark")


# ---------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("app.server.open_folder", lambda folder: opened.append(Path(folder)))
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db), base_url="http://127.0.0.1") as c:
        archive = tmp_path / "archive"
        assert c.post("/api/settings", json={"image_dir": str(archive)}).status_code == 200
        c.opened, c.archive = opened, archive
        yield c
    db.conn.close()


def test_stacks_folder_route_creates_and_opens_only_for_existing_targets(client):
    target = client.archive / "M 42"
    (target / "lights").mkdir(parents=True)
    response = client.post("/api/library/stacks_folder", json={"slug": "M 42"})
    assert response.status_code == 200, response.text
    assert response.json()["rel"] == "M 42/stacks"
    assert (target / "stacks").is_dir()
    assert client.opened == [(target / "stacks").resolve()]
    # opening again reuses the folder
    assert client.post("/api/library/stacks_folder", json={"slug": "M 42"}).status_code == 200
    assert client.post("/api/library/stacks_folder", json={"slug": "M 43"}).status_code == 404
    assert not (client.archive / "M 43").exists()
    for bad in ("", "..", "M 42/lights", "M 42\\lights", "a\x00b", "CON"):
        assert client.post("/api/library/stacks_folder",
                           json={"slug": bad}).status_code == 400, bad
    assert len(client.opened) == 2


def test_stacks_folder_route_refuses_a_file_named_stacks(client):
    target = client.archive / "M 42"
    target.mkdir(parents=True)
    (target / "stacks").write_bytes(b"not a folder")
    assert client.post("/api/library/stacks_folder", json={"slug": "M 42"}).status_code == 409
    assert client.opened == []
