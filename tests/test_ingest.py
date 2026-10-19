"""Direct unit tests for the ingest helpers (app/ingest.py)."""

import asyncio
import hashlib
import io
import sqlite3
import time
import threading
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import ingest
from app.database import Database, SCHEMA
from app.fits import date_epoch
from app.ingest import (_session_dir, classify_frame, obj_slug,
                        session_folder_name)
from _util import make_fits


@pytest.fixture
def dedup_store(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    db = Database(tmp_path / "dedup.sqlite3")
    try:
        yield db, archive
    finally:
        db.conn.close()


class _Upload:
    def __init__(self, data, name="frame.fit", yield_reads=False):
        self.data = data
        self.filename = name
        self.size = len(data)
        self.yield_reads = yield_reads

    async def read(self, size):
        if self.yield_reads:
            await asyncio.sleep(0)
        result, self.data = self.data[:size], self.data[size:]
        return result


def _capture(pixels=b"original pixels"):
    return make_fits([
        ("SIMPLE", "T"), ("OBJECT", "'Unlisted nebula'"),
        ("IMAGETYP", "'LIGHT'"), ("EXPTIME", "60"),
        ("DATE-OBS", "'2026-08-22T20:00:00'"),
    ]) + pixels


def _import(db, archive, data, name="frame.fit", **options):
    return asyncio.run(ingest.ingest_one(db, [], archive,
                                        _Upload(data, name), **options))


def test_spooled_upload_keeps_full_bytes_and_renamed_dedup(dedup_store):
    from starlette.datastructures import UploadFile
    db, archive = dedup_store
    content = _capture(bytes(range(256)) * 16384)
    for name in ("original.fit", "renamed.fit"):
        upload = UploadFile(file=io.BytesIO(content), filename=name, size=len(content))
        result = asyncio.run(ingest.ingest_one(db, [], archive, upload))
        assert result["ok"]
        if name == "renamed.fit":
            assert result["skipped"]
    rows = db.images()
    assert len(rows) == 1
    assert (archive / rows[0]["file"]).read_bytes() == content
    assert rows[0]["content_sha256"] == hashlib.sha256(content).hexdigest()


def test_spooled_upload_enforces_actual_size_and_cleans_stage(dedup_store, monkeypatch):
    from starlette.datastructures import UploadFile
    db, archive = dedup_store
    content = _capture(b"pixels" * 2000)
    monkeypatch.setattr(ingest, "MAX_INGEST_BYTES", len(content) - 1)
    upload = UploadFile(file=io.BytesIO(content), filename="frame.fit", size=None)
    result = asyncio.run(ingest.ingest_one(db, [], archive, upload))
    assert not result["ok"] and "too large" in result["error"]
    assert not db.images() and not list(archive.glob("*.part"))


def test_cancelled_spooled_copy_finishes_before_stage_cleanup(dedup_store, monkeypatch):
    from starlette.datastructures import UploadFile
    db, archive = dedup_store
    entered, release = threading.Event(), threading.Event()
    original = ingest._copy_spooled_upload
    def paused(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(ingest, "_copy_spooled_upload", paused)
    async def run():
        upload = UploadFile(file=io.BytesIO(_capture()), filename="frame.fit")
        task = asyncio.create_task(ingest.ingest_one(db, [], archive, upload))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and list(archive.glob("*.part"))
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert not db.images() and not list(archive.glob("*.part"))


def test_reindex_reuses_catalog_matcher_for_all_new_files(dedup_store, monkeypatch):
    db, archive = dedup_store
    folder = archive / "Unlisted nebula" / "session" / "lights"
    folder.mkdir(parents=True)
    for i in range(8):
        (folder / f"frame-{i}.fit").write_bytes(_capture(bytes([i])))
    loads = []
    original = db.all_objects
    def catalog(**kwargs):
        loads.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(db, "all_objects", catalog)
    assert ingest.reindex_library(db, archive) == {"found": 8, "added": 8, "skipped": 0, "failed": 0, "repaired": 0, "conflicts": 0}
    assert len(loads) == 1
    assert {row["object_name"] for row in db.images()} == {"Unlisted nebula"}
    loads.clear()
    assert ingest.reindex_library(db, archive)["skipped"] == 8
    assert loads == []


# ---------------------------------------------------------------- obj_slug


@pytest.mark.parametrize("change", ["delete", "rename", "session"])
def test_selected_session_changed_during_upload_refuses_and_cleans_stage(
        dedup_store, monkeypatch, change):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    original = archive / first["stored"]
    target = ingest.calibration_import_target(db, archive, first["id"], "dark")
    stream = ingest._stream_to

    async def changed(*args):
        result = await stream(*args)
        with db.lock:
            if change == "delete":
                db.conn.execute("DELETE FROM images WHERE id=?", (first["id"],))
            elif change == "rename":
                db.conn.execute("UPDATE images SET object_name=? WHERE id=?",
                                ("Renamed object", first["id"]))
            else:
                db.conn.execute("UPDATE images SET session_date_obs=? WHERE id=?",
                                ("2026-08-19T20:00:00Z", first["id"]))
            db.conn.commit()
        return result

    monkeypatch.setattr(ingest, "_stream_to", changed)
    result = _import(db, archive, _capture(b"new calibration"), "cal.fit",
                     frame_override="dark", session_target=target)
    assert not result["ok"] and "session changed" in result["error"]
    assert original.read_bytes() == _capture()
    assert len(db.images()) == (0 if change == "delete" else 1)
    assert len(list(archive.rglob("*.fit"))) == 1 and not list(archive.rglob("*.part"))


@pytest.mark.parametrize("stamp,expected", [
    (None, None),
    ("", ""),
    ("2026-08-22T20:00:00Z", "2026-08-22T20:00:00Z"),
    ("2026-08-22T22:00:00+02:00", "2026-08-22T20:00:00Z"),
    ("2026-08-22T22:00:00.123456+02:00", "2026-08-22T20:00:00.123456Z"),
])
def test_session_anchor_normalizer_normalizes_to_utc_z(stamp, expected):
    """Calibration anchors carry a logical session date; the storage boundary
    normalizes every accepted spelling to UTC ...Z while keeping the distinct
    ordinary-NULL vs explicitly-undated ("") forms."""
    from app.database import normalize_session_date_obs
    assert normalize_session_date_obs(stamp) == expected


@pytest.mark.parametrize("local_hours", [-5, 9])
def test_naive_session_anchor_normalization_is_utc_independent_of_local_zone(
        monkeypatch, local_hours):
    from app import database

    class ForeignLocalDateTime(datetime):
        def astimezone(self, tz=None):
            if self.tzinfo is None:
                return self.replace(tzinfo=timezone(timedelta(hours=local_hours))).astimezone(tz)
            return super().astimezone(tz)

    monkeypatch.setattr(database, "datetime", ForeignLocalDateTime)
    assert database.normalize_session_date_obs("2026-08-22T20:00:00.123456") == \
        "2026-08-22T20:00:00.123456Z"


def test_fractional_session_attachment_keeps_day_night_membership(dedup_store):
    from app import imglib
    db, archive = dedup_store
    earlier = db.add_image({"file": "Target/first/lights/a.fit", "orig_name": "a.fit",
                            "object_name": "Target", "imagetyp": "light",
                            "date_obs": "2026-08-22T08:00:00.900000", "site_lat":52.52, "site_lon":13.405, "ingested_at": ""})
    anchor = db.add_image({"file": "Target/second/lights/b.fit", "orig_name": "b.fit",
                           "object_name": "Target", "imagetyp": "light",
                           "date_obs": "2026-08-22T20:00:00.900000", "site_lat":52.52, "site_lon":13.405, "ingested_at": ""})
    target = ingest.calibration_import_target(db, archive, anchor, "dark")
    assert target["session_date_obs"] == "2026-08-22T20:00:00.900000Z"
    result = _import(db, archive, _capture(), "calibration.fit", frame_override="dark",
                     session_target=target)
    assert result["ok"]
    sessions = imglib.compute_sessions(db.images())
    assert len(sessions) == 2
    assert sessions[0]["image_ids"] == [earlier]
    assert set(sessions[1]["image_ids"]) == {anchor, result["id"]}


@pytest.mark.parametrize("stamp", [False, 42, [], {}, "2026-02-30T20:00:00Z",
                                   "2026-08-22", '<img src="x">'])
def test_session_anchor_normalizer_rejects_invalid_values(stamp):
    from app.database import normalize_session_date_obs
    with pytest.raises(ValueError, match="session_date_obs"):
        normalize_session_date_obs(stamp)


def test_old_image_schema_migrates_nullable_session_anchor(tmp_path):
    old_path = tmp_path / "old-library.sqlite3"
    connection = sqlite3.connect(old_path)
    connection.executescript(SCHEMA.replace("    session_date_obs TEXT,\n", ""))
    connection.execute("INSERT INTO images(file,orig_name,object_name,date_obs,ingested_at) "
                       "VALUES(?,?,?,?,?)", ("M31/lights/a.fit", "a.fit", "M31",
                                           "2026-08-22T20:00:00", ""))
    connection.commit()
    connection.close()
    migrated = Database(old_path)
    try:
        record = migrated.images()[0]
        assert record["session_date_obs"] is None
        assert record["date_obs"] == "2026-08-22T20:00:00"
    finally:
        migrated.conn.close()

def test_obj_slug_keeps_safe_chars():
    assert obj_slug("M 31") == "M 31"
    assert obj_slug("NGC 7000/North America") == "NGC 7000North America"
    assert obj_slug("M42!?") == "M42"


def test_obj_slug_avoids_windows_device_names():
    assert obj_slug("NUL") == "NUL_"
    assert obj_slug("Com1!") == "Com1_"
    assert obj_slug("Aux.") == "Aux_"
    assert obj_slug("CONE Nebula") == "CONE Nebula"


def test_obj_slug_empty_falls_back():
    assert obj_slug("") == "misc"
    assert obj_slug("///") == "misc"
    assert obj_slug("   ") == "misc"


# ---------------------------------------------------------------- classify_frame

def test_classify_frame_single_words():
    assert classify_frame("Light") == "light"
    assert classify_frame("dark") == "dark"
    assert classify_frame("FLAT") == "flat"
    assert classify_frame("bias") == "bias"


def test_classify_frame_multi_word_spellings():
    # MaxIm DL / CCDSoft / SBIG write these; they used to land in lights/
    assert classify_frame("Dark Frame") == "dark"
    assert classify_frame("Flat Field") == "flat"
    assert classify_frame("Bias Frame") == "bias"
    assert classify_frame("Light Frame") == "light"
    assert classify_frame("Sky Flat") == "flat"


def test_classify_frame_unknown_defaults_to_light():
    assert classify_frame(None) == "light"
    assert classify_frame("") == "light"
    assert classify_frame("calibration?") == "light"


def test_classify_frame_master_qualifier():
    """PY-09 (review 0.6.32): the letters are reduced and then matched with
    `startswith`, so `"Master Dark"` became `masterdark`, matched neither `dark`
    nor `flat` and fell through to `light` - stacked master calibration frames
    landed in `<object>/<session>/lights/` and were counted as light frames. The
    docstring claims the multi-word spellings are handled, and `fits._STOPWORDS`
    already knows "master"."""
    assert classify_frame("Master Dark") == "dark"
    assert classify_frame("MasterDark") == "dark"
    assert classify_frame("MASTER DARK FRAME") == "dark"
    assert classify_frame("Master Flat") == "flat"
    assert classify_frame("MasterFlat") == "flat"
    assert classify_frame("Master Bias") == "bias"
    # a master LIGHT is still a light, and the plain spellings are unchanged
    assert classify_frame("Master Light") == "light"
    assert classify_frame("Dark") == "dark"
    assert classify_frame("SkyFlat") == "flat"


# ---------------------------------------------------------------- classify_frame

def test_classify_known_types():
    assert classify_frame("light") == "light"
    assert classify_frame("LIGHT") == "light"
    assert classify_frame("L") == "light"
    assert classify_frame("science") == "light"
    assert classify_frame("bias") == "bias"
    assert classify_frame("ZERO") == "bias"
    assert classify_frame("dark") == "dark"
    assert classify_frame("flat") == "flat"
    assert classify_frame("flatfield") == "flat"
    assert classify_frame("skyflat") == "flat"


def test_classify_unknown_defaults_to_light():
    assert classify_frame("object") == "light"
    assert classify_frame("") == "light"
    assert classify_frame(None) == "light"
    assert classify_frame("calibration?") == "light"


# ---------------------------------------------------------------- session_folder_name

def test_session_folder_uses_fixed_utc_interval_and_site_identity():
    from app.solar import session_metadata
    metadata = session_metadata("2026-08-22T23:14:00Z", 52.52, 13.405)
    name = session_folder_name(metadata)
    assert name.startswith("night_2026-08-22_")
    assert name == session_folder_name(session_metadata("2026-08-23T01:00:00Z", 52.52, 13.405))
    assert name != session_folder_name(session_metadata("2026-08-22T23:14:00Z", 40, -74))


def test_session_folder_undated():
    from app.solar import session_metadata
    assert session_folder_name(session_metadata(None, None, None)) == "undated"


def test_session_folder_without_site_is_visibly_unassigned_utc_date():
    from app.solar import session_metadata
    assert session_folder_name(session_metadata("2026-08-22T23:00:00Z", None, None)) == "unassigned_2026-08-22"


def test_images_for_object_returns_only_matching_rows_in_id_order(tmp_path):
    db = Database(tmp_path / "session-queries.sqlite3")
    try:
        rows = [
            ("a.fit", "Known", "known"),
            ("b.fit", "Mystery", "other"),
            ("c.fit", "Mystery", None),
            ("d.fit", "Known", "known"),
        ]
        for filename, name, oid in rows:
            db.conn.execute(
                "INSERT INTO images(file,orig_name,object_id,object_name,"
                "ingested_at) VALUES(?,?,?,?,?)",
                (filename, filename, oid, name, "2026-08-22T00:00:00"))
        db.conn.commit()

        assert [r["file"] for r in db.images_for_object("known", "Known")] == [
            "a.fit", "d.fit"]
        # Unresolved names share the Library normalization, and resolved rows
        # never leak into an unresolved group's session selection.
        assert [r["file"] for r in db.images_for_object(None, "Mystery")] == ["c.fit"]
        assert [r["file"] for r in db.images_for_object(None, "my stery")] == ["c.fit"]

        id_plan = " ".join(str(r[3]) for r in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM images "
            "WHERE object_id=? ORDER BY id", ("known",)))
        name_plan = " ".join(str(r[3]) for r in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM images "
            "WHERE object_name=? ORDER BY id", ("Mystery",)))
        assert "idx_images_object_id_id" in id_plan
        assert "idx_images_object_name_id" in name_plan
    finally:
        db.conn.close()


def test_unresolved_session_lookup_excludes_existing_resolved_id(tmp_path):
    db = Database(tmp_path / "session-name.sqlite3")
    archive = tmp_path / "archive"
    archive.mkdir()
    old_date = "2026-08-22T23:00:00"
    try:
        db.add_image({
            "file": "Mystery/old.fit", "orig_name": "old.fit",
            "object_id": "a-different-resolved-id", "object_name": "Mystery",
            "date_obs": old_date, "ingested_at": "2026-08-22T23:00:00",
        })
        selected = _session_dir(
            db, archive, "Mystery", None, "Mystery", "2026-08-23T01:00:00")
        assert selected.name == "unassigned_2026-08-23"
    finally:
        db.conn.close()


def test_session_override_skips_database_lookup(tmp_path):
    class NoLookup:
        def images_for_object(self, object_id, object_name):
            raise AssertionError("explicit sessions must bypass auto-lookup")

    archive = tmp_path / "archive"
    assert _session_dir(NoLookup(), archive, "M31", "ngc224", "M31", None,
                        "2026-08-22_2000") == archive / "M31" / "2026-08-22_2000"


def test_stream_to_uses_one_mib_reads_and_preserves_bytes(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    target = archive / "frame.fit"
    first = b"FITS header bytes"
    payload = bytes(i % 251 for i in range((2 << 20) + 17))

    class Upload:
        def __init__(self, data):
            self.data = data
            self.read_sizes = []

        async def read(self, size):
            self.read_sizes.append(size)
            result, self.data = self.data[:size], self.data[size:]
            return result

    upload = Upload(payload)
    stored = asyncio.run(ingest._stream_to(archive, target, upload, first))
    assert stored == "frame.fit"
    assert upload.read_sizes == [1 << 20] * 4
    assert target.read_bytes() == first + payload


def test_ingest_read_failure_after_chunk_removes_partial_file(tmp_path):
    db = Database(tmp_path / "partial-ingest.sqlite3")
    archive = tmp_path / "archive"
    archive.mkdir()
    header = make_fits([("SIMPLE", "T"), ("OBJECT", "'M31'")])

    class FailingUpload:
        filename = "partial.fit"
        size = None

        def __init__(self):
            self.read_sizes = []
            self.body_reads = 0

        async def read(self, size):
            self.read_sizes.append(size)
            if len(self.read_sizes) == 1:
                return header
            self.body_reads += 1
            if self.body_reads == 1:
                return bytes(1 << 20)
            raise RuntimeError("injected upload read failure")

    upload = FailingUpload()
    try:
        with pytest.raises(RuntimeError, match="injected upload read failure"):
            asyncio.run(ingest.ingest_one(db, [], archive, upload))
        assert upload.read_sizes == [ingest.HEADER_READ, 1 << 20, 1 << 20]
        assert list(archive.rglob("partial.fit")) == []
        assert not list(archive.rglob("*.part"))
        assert db.images() == []
    finally:
        db.conn.close()


@pytest.mark.parametrize("name", ["frame.fit", "renamed.fits"])
def test_ingest_identical_bytes_skip_without_copy_or_reclassification(dedup_store, name):
    db, archive = dedup_store
    data = _capture()
    first = _import(db, archive, data)
    second = _import(db, archive, data, name, frame_override="dark",
                     session_override="2026-09-01_0000")
    assert first["ok"] and not first.get("skipped")
    assert second["ok"] and second["skipped"]
    assert second["reason"] == "already_imported"
    assert second["id"] == first["id"] and second["stored"] == first["stored"]
    assert second["imagetyp"] == "light"
    assert len(db.images()) == 1
    assert len(list(archive.rglob("*.fit"))) == 1
    assert not list(archive.rglob("*.part"))
    rec = db.images()[0]
    assert rec["content_sha256"] == hashlib.sha256(data).hexdigest()
    assert rec["file_size"] == len(data)


def test_ingest_same_name_and_headers_different_pixels_are_both_preserved(dedup_store):
    db, archive = dedup_store
    a, b = _capture(b"pixels A"), _capture(b"pixels B")
    first = _import(db, archive, a)
    second = _import(db, archive, b)
    assert first["ok"] and second["ok"] and not second.get("skipped")
    assert first["stored"] != second["stored"]
    assert len(db.images()) == 2
    assert (archive / first["stored"]).read_bytes() == a
    assert (archive / second["stored"]).read_bytes() == b


def test_relative_archive_root_can_import_and_skip_renamed_duplicate(dedup_store, monkeypatch):
    db, archive = dedup_store
    monkeypatch.chdir(archive.parent)
    relative = Path(archive.name)
    first = _import(db, relative, _capture())
    second = _import(db, relative, _capture(), "renamed.fits")
    assert first["ok"] and second["skipped"]
    assert first["stored"] == second["stored"]
    assert len(db.images()) == 1
    assert (archive / first["stored"]).read_bytes() == _capture()
    assert not list(archive.rglob("*.part"))


def test_ingest_concurrent_uploads_finalize_only_one_capture(dedup_store):
    db, archive = dedup_store

    async def run():
        return await asyncio.gather(*[
            ingest.ingest_one(db, [], archive, _Upload(_capture(), name, True))
            for name in ("first.fit", "second.fit")])

    results = asyncio.run(run())
    assert sum(bool(r.get("skipped")) for r in results) == 1
    assert all(r["ok"] for r in results)
    assert len(db.images()) == 1
    assert len(list(archive.rglob("*.fit"))) == 1
    assert not list(archive.rglob("*.part"))


def test_legacy_row_is_fingerprinted_and_skipped_after_restart(dedup_store):
    db, archive = dedup_store
    data = _capture()
    (archive / "old.fit").write_bytes(data)
    old_id = db.add_image({"file": "old.fit", "orig_name": "old.fit",
                           "object_name": "Unlisted nebula", "imagetyp": "light",
                           "ingested_at": "2026-08-22T20:00:00"})
    assert db.image(old_id)["content_sha256"] is None
    reopened = Database(db.path)
    try:
        result = _import(reopened, archive, data, "new_name.fit")
        assert result["skipped"] and result["id"] == old_id
        assert len(reopened.images()) == 1
        assert reopened.image(old_id)["content_sha256"] == hashlib.sha256(data).hexdigest()
    finally:
        reopened.conn.close()


def test_missing_fingerprinted_original_is_restored_without_another_row(dedup_store):
    db, archive = dedup_store
    data = _capture()
    first = _import(db, archive, data)
    (archive / first["stored"]).unlink()
    result = _import(db, archive, data, "replacement.fit", frame_override="dark")
    assert result["ok"] and result["restored"] and not result["skipped"]
    assert result["id"] == first["id"] and result["stored"] == first["stored"]
    assert result["imagetyp"] == "light"
    assert len(db.images()) == 1
    assert (archive / first["stored"]).read_bytes() == data


def test_cached_digest_is_verified_against_current_original_bytes(dedup_store):
    db, archive = dedup_store
    data = _capture(b"pixels A")
    first = _import(db, archive, data)
    original = archive / first["stored"]
    old_stat = original.stat()
    original.write_bytes(_capture(b"pixels B"))
    # Even matching size and forged/reused mtime cannot suppress the upload
    # when a matching cached digest disagrees with the actual archived bytes.
    import os
    os.utime(original, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    second = _import(db, archive, data)
    assert second["ok"] and not second.get("skipped")
    assert len(db.images()) == 2
    assert original.read_bytes() == _capture(b"pixels B")


def test_reindex_skips_duplicate_content_and_preserves_manual_files(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    manual = archive / "Manual" / "lights" / "manual-copy.fit"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(_capture())
    result = ingest.reindex_library(db, archive)
    assert result == {"found": 2, "added": 0, "skipped": 2, "failed": 0, "repaired": 0, "conflicts": 0}
    assert manual.read_bytes() == _capture()
    assert len(db.images()) == 1
    assert db.image(first["id"])["content_sha256"]


def test_reindexed_file_is_recognized_on_later_upload(dedup_store):
    db, archive = dedup_store
    manual = archive / "Manual" / "undated" / "lights" / "manual.fit"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(_capture())
    assert ingest.reindex_library(db, archive)["added"] == 1
    result = _import(db, archive, _capture(), "new-name.fit")
    assert result["skipped"] and result["stored"] == "Manual/undated/lights/manual.fit"
    assert len(db.images()) == 1


def test_oversized_upload_removes_staging_file(dedup_store, monkeypatch):
    db, archive = dedup_store
    monkeypatch.setattr(ingest, "MAX_INGEST_BYTES", len(_capture()) - 1)
    upload = _Upload(_capture())
    upload.size = None  # exercise the stream cap, not the advertised size check
    result = asyncio.run(ingest.ingest_one(db, [], archive, upload))
    assert not result["ok"] and "too large" in result["error"]
    assert not db.images() and not list(archive.rglob("*.part"))
    assert not list(archive.rglob("*.fit"))


def test_database_failure_removes_promoted_original_and_staging(dedup_store, monkeypatch):
    db, archive = dedup_store

    def fail(_rec):
        raise sqlite3.OperationalError("injected insert failure")

    monkeypatch.setattr(db, "add_image", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected insert failure"):
        _import(db, archive, _capture())
    assert not db.images() and not list(archive.rglob("*.part"))
    assert not list(archive.rglob("*.fit"))


def test_commit_failure_rolls_back_image_before_later_writes(dedup_store):
    db, archive = dedup_store
    connection = db.conn

    class FailCommitOnce:
        def __init__(self):
            self.fail = True

        def __getattr__(self, name):
            return getattr(connection, name)

        def commit(self):
            if self.fail:
                self.fail = False
                raise sqlite3.OperationalError("injected commit failure")
            return connection.commit()

    db.conn = FailCommitOnce()
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        _import(db, archive, _capture())
    assert not connection.in_transaction and not db.images()
    assert not list(archive.rglob("*.fit")) and not list(archive.rglob("*.part"))
    # A later unrelated fingerprint write must not commit an orphaned image.
    db.set_image_fingerprint(9999, "a" * 64, 1, 1)
    assert not db.images()


def test_cancelled_upload_removes_staging(dedup_store):
    db, archive = dedup_store

    class Cancelled(_Upload):
        async def read(self, size):
            if not self.data:
                raise asyncio.CancelledError()
            return await super().read(size)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingest.ingest_one(db, [], archive, Cancelled(_capture())))
    assert not db.images() and not list(archive.rglob("*.part"))


def test_archive_publication_never_overwrites_existing_file(tmp_path):
    stage, target = tmp_path / "upload.part", tmp_path / "original.fit"
    stage.write_bytes(b"new bytes")
    target.write_bytes(b"existing bytes")
    with pytest.raises(FileExistsError):
        ingest._promote_stage(stage, target)
    assert target.read_bytes() == b"existing bytes"


def test_stale_fingerprint_hint_is_remeasured_and_still_dedups(dedup_store):
    """A cached fingerprint is a hint, never identity evidence: when the stored
    size/mtime do not match the file on disk, the candidate is re-measured from
    the actual bytes (refreshing the hint) instead of trusting stale metadata."""
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    original = db.image(first["id"])
    real_digest, real_size, real_mtime = (original["content_sha256"],
                                          original["file_size"],
                                          original["file_mtime_ns"])
    db.set_image_fingerprint(first["id"], real_digest, 1, 1)   # stale size/mtime
    result = _import(db, archive, _capture())
    assert result["skipped"] and len(db.images()) == 1
    restored = db.image(first["id"])
    assert restored["content_sha256"] == real_digest
    assert restored["file_size"] == real_size
    assert restored["file_mtime_ns"] == real_mtime


def test_additive_fingerprint_migration_preserves_old_image_rows(tmp_path):
    path = tmp_path / "old.sqlite3"
    image_schema = SCHEMA.split("CREATE TABLE IF NOT EXISTS images (", 1)[1].split(
        ");", 1)[0]
    old_columns = "\n".join(line for line in image_schema.splitlines()
                            if not any(name in line for name in
                                       ("content_sha256", "file_size", "file_mtime_ns")))
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE images (" + old_columns + ")")
    conn.execute("INSERT INTO images(file,orig_name,object_name,ingested_at) "
                 "VALUES('old.fit','old.fit','M31','2026-08-22')")
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        rows = db.images()
        assert len(rows) == 1 and rows[0]["file"] == "old.fit"
        assert rows[0]["content_sha256"] is None
        assert {"content_sha256", "file_size", "file_mtime_ns"} <= rows[0].keys()
    finally:
        db.conn.close()


def test_reindex_during_streamed_upload_produces_one_row(dedup_store):
    db, archive = dedup_store
    data = _capture()
    manual = archive / "Manual" / "lights" / "manual.fit"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(data)

    class ReindexDuringUpload(_Upload):
        async def read(self, size):
            if not self.data:
                result = await asyncio.to_thread(ingest.reindex_library, db, archive)
                assert result["added"] == 1
            return await super().read(size)

    result = asyncio.run(ingest.ingest_one(db, [], archive,
                                          ReindexDuringUpload(data)))
    assert result["skipped"] and result["stored"] == "Manual/lights/manual.fit"
    assert len(db.images()) == 1
    assert len(list(archive.rglob("*.fit"))) == 1
    assert not list(archive.rglob("*.part"))


def test_reindex_unchanged_known_paths_uses_cached_fingerprints(dedup_store, monkeypatch):
    db, archive = dedup_store
    _import(db, archive, _capture())

    def fail(_path):
        raise AssertionError("unchanged known files should not be re-read")

    monkeypatch.setattr(ingest, "_fingerprint_file", fail)
    assert ingest.reindex_library(db, archive)["skipped"] == 1


def test_archive_publication_falls_back_to_exclusive_copy(tmp_path, monkeypatch):
    stage, target = tmp_path / "upload.part", tmp_path / "original.fit"
    stage.write_bytes(b"new bytes")

    def unsupported(_source, _target):
        raise OSError("drive does not support hard links")

    monkeypatch.setattr(ingest.os, "link", unsupported)
    ingest._promote_stage(stage, target)
    assert target.read_bytes() == b"new bytes"
    with pytest.raises(FileExistsError):
        ingest._promote_stage(stage, target)
    assert target.read_bytes() == b"new bytes"


def test_duplicate_hash_keeps_event_loop_and_database_reads_available(dedup_store, monkeypatch):
    db, archive = dedup_store
    _import(db, archive, _capture())
    entered, release = threading.Event(), threading.Event()
    original = ingest._fingerprint_file

    def paused(path, cancel=None):
        assert not db.lock._is_owned()
        assert cancel is not None
        entered.set()
        assert release.wait(3), "event loop did not release the worker"
        return original(path, cancel)

    monkeypatch.setattr(ingest, "_fingerprint_file", paused)

    async def run():
        task = asyncio.create_task(ingest.ingest_one(db, [], archive, _Upload(_capture())))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            rows = await asyncio.wait_for(asyncio.to_thread(db.images), 0.5)
            assert len(rows) == 1 and not task.done()
        finally:
            release.set()
        assert (await task)["skipped"]

    asyncio.run(run())


def test_reindex_hash_keeps_database_reads_available(dedup_store, monkeypatch):
    db, archive = dedup_store
    manual = archive / "Manual" / "lights" / "manual.fit"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(_capture())
    entered, release = threading.Event(), threading.Event()
    original = ingest._fingerprint_file

    def paused(path):
        assert not db.lock._is_owned()
        entered.set()
        assert release.wait(3)
        return original(path)

    monkeypatch.setattr(ingest, "_fingerprint_file", paused)

    async def run():
        task = asyncio.create_task(asyncio.to_thread(ingest.reindex_library, db, archive))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert await asyncio.wait_for(asyncio.to_thread(db.images), 0.5) == []
        finally:
            release.set()
        assert (await task)["added"] == 1

    asyncio.run(run())


def test_upload_uses_indexed_candidates_without_materializing_library(dedup_store, monkeypatch):
    db, archive = dedup_store
    first = _import(db, archive, _capture())

    def forbidden():
        raise AssertionError("upload must not read the complete images table")

    monkeypatch.setattr(db, "images", forbidden)
    duplicate = _import(db, archive, _capture(), "renamed.fit")
    assert duplicate["skipped"] and duplicate["id"] == first["id"]
    distinct = _import(db, archive, _capture(b"different pixels"))
    assert distinct["ok"] and distinct["id"] != first["id"]


def test_cancelled_hash_waits_for_worker_before_removing_stage(dedup_store, monkeypatch):
    db, archive = dedup_store
    _import(db, archive, _capture())
    entered, release = threading.Event(), threading.Event()
    original = ingest._fingerprint_file

    def paused(path, cancel=None):
        assert cancel is not None
        entered.set()
        assert release.wait(3)
        return original(path, cancel)

    monkeypatch.setattr(ingest, "_fingerprint_file", paused)

    async def run():
        task = asyncio.create_task(ingest.ingest_one(db, [], archive, _Upload(_capture())))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and list(archive.glob("*.part"))
        task.cancel()  # repeated shutdown cancellation must also await the worker
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(db.images()) == 1 and not list(archive.glob("*.part"))
    assert not any(path.parent == archive for path in ingest._ACTIVE_STAGES)


def test_cancelled_stage_creation_cleans_unreturned_stage(dedup_store, monkeypatch):
    db, archive = dedup_store
    entered, release = threading.Event(), threading.Event()
    original = ingest._prepare_stage

    def paused(root):
        stage = original(root)
        entered.set()
        assert release.wait(3)
        return stage

    monkeypatch.setattr(ingest, "_prepare_stage", paused)

    async def run():
        task = asyncio.create_task(ingest.ingest_one(db, [], archive, _Upload(_capture())))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not db.images() and not list(archive.glob("*.part"))


def test_stale_stage_sweep_preserves_active_recent_live_and_unknown_files(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    names = [".nightseam-import-111-dead.part", ".nightseam-import-222-live.part",
             ".nightseam-import-111-active.part", ".nightseam-import-111-recent.part",
             ".nightseam-import-legacy.part", "unrelated.part"]
    for name in names:
        path = archive / name
        path.write_bytes(b"preserve unless abandoned")
        if "recent" not in name:
            os.utime(path, (time.time() - 7200, time.time() - 7200))
    active = archive / names[2]
    ingest._ACTIVE_STAGES.add(active)
    monkeypatch.setattr(ingest, "_process_alive", lambda pid: pid == 222)
    stage = None
    try:
        stage = ingest._prepare_stage(archive)
        assert not (archive / names[0]).exists()
        assert all((archive / name).exists() for name in names[1:])
    finally:
        ingest._ACTIVE_STAGES.discard(active)
        if stage is not None:
            ingest._remove_stage(stage)


def test_rename_merge_failure_undoes_completed_moves(dedup_store, monkeypatch):
    """F-22: a mid-merge failure must UNDO the moves it already completed -
    files stay where the (unchanged) database rows point."""
    db, archive = dedup_store
    old_dir = archive / "Object A" / "2026-08-22_2200" / "lights"
    new_dir = archive / "Object B" / "2026-08-22_2200" / "lights"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "a.fit").write_bytes(b"AAA")
    (old_dir / "b.fit").write_bytes(b"BBB")
    (new_dir / "a.fit").write_bytes(b"original in B")

    calls = {"n": 0}
    real_move = ingest._move_one

    def second_move_fails(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk locked mid-rename")
        return real_move(src, dst)

    monkeypatch.setattr(ingest, "_move_one", second_move_fails)

    result = ingest.rename_archive_folder(archive, "Object A", "Object B")
    assert result is None
    assert (old_dir / "a.fit").read_bytes() == b"AAA"
    assert (old_dir / "b.fit").read_bytes() == b"BBB"
    assert sorted(p.name for p in new_dir.iterdir()) == ["a.fit"]
    assert (new_dir / "a.fit").read_bytes() == b"original in B"


def test_undo_archive_rename_restores_completed_moves(dedup_store):
    """The journal undo reverses a completed move map, newest first."""
    db, archive = dedup_store
    old_dir = archive / "Object A" / "lights"
    old_dir.mkdir(parents=True)
    (old_dir / "a.fit").write_bytes(b"AAA")

    moved = ingest.rename_archive_folder(archive, "Object A", "Object B")
    assert moved
    assert (archive / "Object B" / "lights" / "a.fit").read_bytes() == b"AAA"

    failures = ingest.undo_archive_rename(archive, moved)
    assert failures == []
    assert (old_dir / "a.fit").read_bytes() == b"AAA"
    assert not (archive / "Object B" / "lights" / "a.fit").exists()


def test_rename_merge_refused_destination_undoes_prior_moves(
        dedup_store, monkeypatch):
    _, archive = dedup_store
    first = archive / "Object A/first/lights/a.fit"
    second = archive / "Object A/second/lights/b.fit"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first bytes")
    second.write_bytes(b"second bytes")
    (archive / "Object B").mkdir()
    real_safe_join = ingest.paths.safe_join

    def refuse_second_destination(root, relative):
        if relative == "Object B/second/lights/b.fit":
            return None
        return real_safe_join(root, relative)

    monkeypatch.setattr(ingest.paths, "safe_join", refuse_second_destination)
    assert ingest.rename_archive_folder(archive, "Object A", "Object B") is None
    assert first.read_bytes() == b"first bytes"
    assert second.read_bytes() == b"second bytes"
    assert not (archive / "Object B/first/lights/a.fit").exists()
    assert not (archive / "Object B/second").exists()


def test_whole_folder_rename_enumeration_failure_leaves_originals(
        dedup_store, monkeypatch):
    _, archive = dedup_store
    original = archive / "Object A" / "lights" / "a.fit"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original bytes")
    real_rglob = Path.rglob

    def failed_enumeration(path, *args, **kwargs):
        if path == original.parents[1]:
            yield original
            raise OSError("cannot enumerate the remaining original files")
        yield from real_rglob(path, *args, **kwargs)

    undo_maps = []
    real_undo = ingest.undo_archive_rename

    def record_undo(root, moved):
        undo_maps.append(dict(moved))
        return real_undo(root, moved)

    monkeypatch.setattr(Path, "rglob", failed_enumeration)
    monkeypatch.setattr(ingest, "undo_archive_rename", record_undo)
    assert ingest.rename_archive_folder(archive, "Object A", "Object B") is None
    assert original.read_bytes() == b"original bytes"
    assert not (archive / "Object B").exists()
    assert all(not moved for moved in undo_maps)


def test_whole_folder_undo_restores_empty_and_unmapped_children(dedup_store):
    _, archive = dedup_store
    old = archive / "Object A"
    (old / "empty" / "child").mkdir(parents=True)
    (old / "a.fit").write_bytes(b"original")
    moved = ingest.rename_archive_folder(archive, "Object A", "Object B")
    # A later unknown child must be restored with the directory too; the file
    # map is not the complete rollback record for a whole-directory move.
    (archive / "Object B" / "unknown.bin").write_bytes(b"keep unknown bytes")
    assert ingest.undo_archive_rename(archive, moved) == []
    assert (old / "empty" / "child").is_dir()
    assert (old / "a.fit").read_bytes() == b"original"
    assert (old / "unknown.bin").read_bytes() == b"keep unknown bytes"
    assert not (archive / "Object B").exists()


def test_whole_folder_undo_refuses_occupied_origin(dedup_store):
    _, archive = dedup_store
    old = archive / "Object A"
    old.mkdir()
    (old / "a.fit").write_bytes(b"original")
    moved = ingest.rename_archive_folder(archive, "Object A", "Object B")
    old.mkdir()
    (old / "untouched.bin").write_bytes(b"new occupant")
    failures = ingest.undo_archive_rename(archive, moved)
    assert failures == [("Object B", "original directory is occupied; refusing to overwrite it")]
    assert (old / "untouched.bin").read_bytes() == b"new occupant"
    assert (archive / "Object B" / "a.fit").read_bytes() == b"original"


def test_whole_folder_rename_does_not_enumerate_after_publication(
        dedup_store, monkeypatch):
    _, archive = dedup_store
    original = archive / "Object A" / "lights" / "a.fit"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original bytes")
    published = False
    real_rename = Path.rename
    real_rglob = Path.rglob

    def publish(src, dst):
        nonlocal published
        result = real_rename(src, dst)
        published = True
        return result

    def enumerate_before_publication(path, *args, **kwargs):
        assert not published, "filesystem enumeration occurred after publication"
        yield from real_rglob(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", publish)
    monkeypatch.setattr(Path, "rglob", enumerate_before_publication)
    assert ingest.rename_archive_folder(archive, "Object A", "Object B") == {
        "Object A/lights/a.fit": "Object B/lights/a.fit"}
    assert published
    assert (archive / "Object B/lights/a.fit").read_bytes() == b"original bytes"


def test_failed_whole_folder_publication_never_undoes_planned_paths(
        dedup_store, monkeypatch):
    _, archive = dedup_store
    original = archive / "Object A" / "lights" / "a.fit"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original bytes")
    undo_maps = []

    def failed_rename(src, dst):
        raise OSError("original folder is locked")

    def record_undo(root, moved):
        undo_maps.append(dict(moved))
        return []

    monkeypatch.setattr(Path, "rename", failed_rename)
    monkeypatch.setattr(ingest, "undo_archive_rename", record_undo)
    assert ingest.rename_archive_folder(archive, "Object A", "Object B") is None
    assert original.read_bytes() == b"original bytes"
    assert not (archive / "Object B").exists()
    assert all(not moved for moved in undo_maps)


def test_external_publication_collision_retries_without_replacing_bytes(dedup_store, monkeypatch):
    db, archive = dedup_store
    original = ingest._promote_stage
    collided = []

    def collide_once(stage, target):
        if not collided:
            target.write_bytes(b"external capture")
            collided.append(target)
        return original(stage, target)

    monkeypatch.setattr(ingest, "_promote_stage", collide_once)
    result = _import(db, archive, _capture())
    assert result["ok"]
    assert collided[0].read_bytes() == b"external capture"
    assert (archive / result["stored"]).read_bytes() == _capture()
    assert len(db.images()) == 1


def test_reindex_refreshes_externally_changed_original_size_before_dedup(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    original = archive / first["stored"]
    changed = _capture(b"externally replaced pixels with a different byte length")
    original.write_bytes(changed)
    indexed = ingest.reindex_library(db, archive)
    assert indexed["skipped"] == 1 and indexed["added"] == 0
    rec = db.image(first["id"])
    assert rec["file_size"] == len(changed)
    assert rec["content_sha256"] == hashlib.sha256(changed).hexdigest()
    repeated = _import(db, archive, changed, "changed-copy.fit")
    assert repeated["skipped"] and repeated["id"] == first["id"]
    assert len(db.images()) == 1


@pytest.mark.parametrize("matching", [True, False])
def test_missing_original_collision_rechecks_original_identity(dedup_store, monkeypatch, matching):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    original_path = archive / first["stored"]
    original_path.unlink()
    original = ingest._promote_stage
    competing = _capture() if matching else b"different external bytes"

    def collide(stage, target):
        target.write_bytes(competing)
        return original(stage, target)

    monkeypatch.setattr(ingest, "_promote_stage", collide)
    result = _import(db, archive, _capture())
    assert result["ok"] is matching
    if matching:
        assert result["skipped"] and result["id"] == first["id"]
    assert original_path.read_bytes() == competing
    assert len(db.images()) == 1 and not list(archive.glob("*.part"))


def test_ingest_endpoint_builds_catalog_off_the_event_loop(tmp_path, monkeypatch):
    """F-06: /api/images/ingest reads the catalog and builds the resolver on
    the threadpool, never on the event loop.

    The verification measured ~60-67 ms for the catalog read and ~291-307 ms
    for resolver construction on the full 12,176-row catalog; synchronously on
    the loop they starved every other request. This test exercises the REAL
    registered endpoint function with an empty upload (no exposure files), so
    only catalog/resolver preparation runs, and records the thread of every
    step. No elapsed-time thresholds: the thread identities alone prove the
    offload, and run_in_threadpool itself stays unpatched."""
    from app import server as server_module
    from app.server import create_app

    db = Database(tmp_path / "ingest-loop.sqlite3")
    try:
        app = create_app(db)
        route = next(route for route in app.routes
                     if getattr(route, "path", None) == "/api/images/ingest")
        endpoint = route.endpoint

        observed = {}
        real_all_objects = db.all_objects

        def spy_all_objects(*args, **kwargs):
            observed["thread"] = threading.get_ident()
            observed["args"] = args
            observed["kwargs"] = kwargs
            observed["calls"] = observed.get("calls", 0) + 1
            observed["catalog"] = real_all_objects(*args, **kwargs)
            return observed["catalog"]

        monkeypatch.setattr(db, "all_objects", spy_all_objects)

        class RecordingResolver:
            def __init__(self, catalog):
                self.thread = threading.get_ident()
                self.catalog_arg = catalog

        resolver_seen = []
        real_resolver = server_module.CatalogResolver

        def recording_resolver(catalog):
            instance = RecordingResolver(catalog)
            resolver_seen.append(instance)
            return real_resolver(catalog)

        monkeypatch.setattr(server_module, "CatalogResolver", recording_resolver)

        loop = {}

        # One unsupported file part: preparation runs, nothing is staged.
        body = (b'--b\r\nContent-Disposition: form-data; name="files"; filename="notes.txt"\r\n'
                b'Content-Type: text/plain\r\n\r\nnot an exposure\r\n--b--\r\n')

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def invoke():
            from starlette.requests import Request
            loop["thread"] = threading.get_ident()
            request = Request({"type": "http", "method": "POST", "path": "/api/images/ingest",
                               "headers": [(b"content-type", b"multipart/form-data; boundary=b")]}, receive)
            return await endpoint(request)

        result = asyncio.run(invoke())

        # the endpoint finished successfully, refusing the unsupported file
        assert [item["ok"] for item in result["results"]] == [False]
        # all_objects received advanced=True, exactly once
        assert observed["calls"] == 1
        assert observed["kwargs"] == {"advanced": True}
        assert observed["args"] == ()
        # resolver construction received the exact list all_objects returned,
        # exactly once
        assert len(resolver_seen) == 1
        assert resolver_seen[0].catalog_arg is observed["catalog"]
        # both operations ran OUTSIDE the event-loop thread...
        assert observed["thread"] != loop["thread"]
        assert resolver_seen[0].thread != loop["thread"]
        # ...and both inside the SAME worker thread (one helper invocation)
        assert resolver_seen[0].thread == observed["thread"]
    finally:
        db.conn.close()



def test_normalized_unresolved_imports_share_session_label(dedup_store):
    db, archive = dedup_store
    def exposure(name, stamp, pixels):
        return make_fits([("SIMPLE", "T"), ("OBJECT", repr(name)),
                          ("DATE-OBS", repr(stamp)), ("IMAGETYP", "'LIGHT'")]) + pixels
    first = _import(db, archive, exposure("Zzq Blob 7", "2026-01-01T20:00:00Z", b"A"), "a.fit")
    second = _import(db, archive, exposure("zzqblob7", "2026-01-01T21:00:00Z", b"B"), "b.fit")
    assert first["ok"] and second["ok"]
    assert Path(first["stored"]).parts[1] == Path(second["stored"]).parts[1]
    groups = ingest.imglib.group_library(db.images(), [])
    assert len(groups) == 1 and len(groups[0]["sessions"]) == 1
    assert groups[0]["sessions"][0]["n_images"] == 2


def test_reindex_repairs_unique_missing_identity_preserving_metadata(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    db.conn.execute("UPDATE images SET object_name=?,otype=?,session_date_obs=? WHERE id=?",
                    ("Manual identity", "nebula", "2026-08-21T20:00:00Z", first["id"]))
    db.conn.commit()
    before = db.image(first["id"])
    new_path = archive / "Actual/session/darks/restored.fit"
    new_path.parent.mkdir(parents=True)
    (archive / before["file"]).rename(new_path)
    outcome = ingest.reindex_library(db, archive)
    assert outcome == {"found": 1, "added": 0, "skipped": 0, "failed": 0,
                       "repaired": 1, "conflicts": 0}
    after = db.image(first["id"])
    assert after["file"] == "Actual/session/darks/restored.fit"
    for key in before.keys() - {"file", "content_sha256", "file_size", "file_mtime_ns"}:
        assert after[key] == before[key]
    assert after["imagetyp"] == "light"  # stored classification wins over folder
    assert len(db.images()) == 1 and new_path.read_bytes() == _capture()


@pytest.mark.parametrize("ambiguous", ["records", "destinations"])
def test_reindex_refuses_ambiguous_missing_identity(dedup_store, ambiguous):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    rec = db.image(first["id"])
    (archive / rec["file"]).unlink()
    if ambiguous == "records":
        extra = dict(rec, file="Other/undated/lights/missing.fit")
        db.add_image(extra)
    before = db.images()
    count = 2 if ambiguous == "destinations" else 1
    for index in range(count):
        target = archive / f"Actual/undated/lights/{index}.fit"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_capture())
    result = ingest.reindex_library(db, archive)
    assert result["conflicts"] == count and result["repaired"] == result["added"] == 0
    assert db.images() == before
    assert all((archive / f"Actual/undated/lights/{index}.fit").read_bytes() == _capture()
               for index in range(count))


def test_reindex_does_not_guess_without_trusted_missing_fingerprint(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    (archive / first["stored"]).unlink()
    db.conn.execute("UPDATE images SET content_sha256=NULL,file_size=NULL WHERE id=?", (first["id"],))
    db.conn.commit()
    before = db.image(first["id"])
    manual = archive / "Manual/lights/manual.fit"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(_capture())
    outcome = ingest.reindex_library(db, archive)
    assert outcome["repaired"] == 0 and outcome["added"] == 1
    assert db.image(first["id"]) == before


def test_reindex_commit_failure_rolls_back_repairs_fingerprints_and_additions(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    second = _import(db, archive, _capture(b"second"), "second.fit")
    old = archive / first["stored"]
    relocated = archive / "Manual/lights/relocated.fit"
    relocated.parent.mkdir(parents=True)
    old.rename(relocated)
    (archive / second["stored"]).write_bytes(_capture(b"changed second"))
    for index in (1, 2):
        (relocated.parent / f"new-{index}.fit").write_bytes(_capture(f"new-{index}".encode()))
    before = db.images()
    db.conn.execute("CREATE TRIGGER refuse_second_reindex BEFORE INSERT ON images "
                    "WHEN NEW.orig_name='new-2.fit' BEGIN SELECT RAISE(ABORT,'refused insert'); END")
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="refused insert"):
        ingest.reindex_library(db, archive)
    assert db.images() == before
    assert relocated.read_bytes() == _capture()
    assert not list(archive.rglob("*.part"))


def test_reindex_cancel_after_hash_leaves_entire_database_unchanged(dedup_store, monkeypatch):
    db, archive = dedup_store
    first = _import(db, archive, _capture())
    (archive / first["stored"]).write_bytes(_capture(b"changed"))
    before = db.images()
    cancelled = threading.Event()
    original = ingest._fingerprint_file
    def stop_after_read(path, event=None):
        result = original(path, event)
        cancelled.set()
        return result
    monkeypatch.setattr(ingest, "_fingerprint_file", stop_after_read)
    with pytest.raises(ingest.ReindexCancelled):
        ingest.reindex_library(db, archive, cancelled)
    assert db.images() == before


def test_reindex_hash_checks_cancellation_between_chunks(dedup_store, monkeypatch):
    db, archive = dedup_store
    target = archive / "Manual/lights/large.fit"
    target.parent.mkdir(parents=True)
    target.write_bytes(_capture(b"pixel" * (1 << 19)))
    before = db.images()
    cancelled = threading.Event()
    original = ingest.hashlib.sha256
    class StopDigest:
        def __init__(self):
            self.digest = original()
        def update(self, data):
            self.digest.update(data)
            cancelled.set()
        def hexdigest(self):
            raise AssertionError("cancellation must stop before completing digest")
    monkeypatch.setattr(ingest.hashlib, "sha256", StopDigest)
    with pytest.raises(ingest.ReindexCancelled):
        ingest.reindex_library(db, archive, cancelled)
    assert db.images() == before


def test_reindex_refuses_outside_archive_candidate_before_read(dedup_store, monkeypatch):
    db, archive = dedup_store
    target = archive / "Manual/lights/refused.fit"
    target.parent.mkdir(parents=True)
    target.write_bytes(_capture())
    before = db.images()
    original = ingest.paths.safe_join
    monkeypatch.setattr(ingest.paths, "safe_join", lambda root, rel:
                        None if rel == "Manual/lights/refused.fit" else original(root, rel))
    with pytest.raises(ValueError, match="Unsafe archive file"):
        ingest.reindex_library(db, archive)
    assert db.images() == before and target.read_bytes() == _capture()



def test_reindex_cancel_while_waiting_for_publication_lock(dedup_store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    db, archive = dedup_store
    target = archive / "Manual/lights/new.fit"
    target.parent.mkdir(parents=True)
    target.write_bytes(_capture())
    before = db.images()
    cancelled, entered, publish = threading.Event(), threading.Event(), threading.Event()
    original = db.apply_reindex_plan
    def hold_publication(*args, **kwargs):
        entered.set()
        assert publish.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(db, "apply_reindex_plan", hold_publication)
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(ingest.reindex_library, db, archive, cancelled)
        assert entered.wait(3)
        with db.lock:
            publish.set()
            with pytest.raises(TimeoutError):
                future.result(timeout=0.05)
            cancelled.set()
        with pytest.raises(ingest.ReindexCancelled):
            future.result(timeout=3)
    assert db.images() == before and target.read_bytes() == _capture()



@pytest.mark.parametrize("include_cached_digest_copy", [False, True])
def test_reindex_updated_live_digest_remains_available_to_later_candidates(
        dedup_store, include_cached_digest_copy):
    db, archive = dedup_store
    old_bytes, actual_bytes = _capture(b"pixels-A"), _capture(b"pixels-B")
    first = _import(db, archive, old_bytes)
    original = archive / first["stored"]
    original.write_bytes(actual_bytes)
    stat = original.stat()
    # A restoration can preserve size and modification time while changing
    # bytes. The cached digest is only a hint until a positive match is read.
    db.conn.execute("UPDATE images SET file_size=?,file_mtime_ns=? WHERE id=?",
                    (stat.st_size, stat.st_mtime_ns, first["id"]))
    db.conn.commit()
    manual = archive / "Manual/lights"
    manual.mkdir(parents=True)
    if include_cached_digest_copy:
        (manual / "a-old.fit").write_bytes(old_bytes)
    (manual / "b-actual.fit").write_bytes(actual_bytes)
    outcome = ingest.reindex_library(db, archive)
    extra = int(include_cached_digest_copy)
    assert outcome == {"found": 2 + extra, "added": extra, "skipped": 2, "failed": 0,
                       "repaired": 0, "conflicts": 0}
    assert len(db.images()) == 1 + extra
    assert db.image(first["id"])["file"] == first["stored"]
    assert db.image(first["id"])["content_sha256"] == hashlib.sha256(actual_bytes).hexdigest()
    assert sum(row["content_sha256"] == hashlib.sha256(actual_bytes).hexdigest()
               for row in db.images()) == 1



def test_upload_checks_current_bytes_despite_different_cached_digest(dedup_store):
    db, archive = dedup_store
    old_bytes, actual_bytes = _capture(b"pixels-A"), _capture(b"pixels-B")
    first = _import(db, archive, old_bytes)
    original = archive / first["stored"]
    before = original.stat()
    original.write_bytes(actual_bytes)
    os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))
    record = db.image(first["id"])
    assert record["content_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert (original.stat().st_size, original.stat().st_mtime_ns) == (
        record["file_size"], record["file_mtime_ns"])
    repeated = _import(db, archive, actual_bytes, "restored-copy.fit")
    assert repeated["skipped"] and repeated["id"] == first["id"]
    assert repeated["stored"] == first["stored"] and len(db.images()) == 1
    assert db.image(first["id"])["content_sha256"] == hashlib.sha256(actual_bytes).hexdigest()
    assert original.read_bytes() == actual_bytes
    assert len(list(archive.rglob("*.fit"))) == 1



def _site_capture(stamp, pixels=b"private pixels", location=(52.52, 13.405), extra=()):
    cards = [("SIMPLE", "T"), ("OBJECT", "'Private target'"),
             ("DATE-OBS", repr(stamp)), ("IMAGETYP", "'LIGHT'")]
    if location is not None:
        cards += [("SITELAT", str(location[0])), ("SITELONG", str(location[1]))]
    return make_fits(cards + list(extra)) + pixels


def test_import_solar_sessions_are_stable_across_order_and_winter_gaps(dedup_store):
    db, archive = dedup_store
    late = _import(db, archive, _site_capture("2026-12-22T06:00:00Z", b"late"), "late.fit")
    early = _import(db, archive, _site_capture("2026-12-21T16:00:00Z", b"early"), "early.fit")
    day = _import(db, archive, _site_capture("2026-12-22T12:00:00Z", b"day"), "day.fit")
    next_night = _import(db, archive, _site_capture("2026-12-22T16:00:00Z", b"next"), "next.fit")
    assert all(result["ok"] for result in (late, early, day, next_night))
    assert late["session_key"] == early["session_key"]
    assert Path(late["stored"]).parts[1] == Path(early["stored"]).parts[1]
    assert early["session_kind"] == late["session_kind"] == "night"
    assert day["session_kind"] == "day" and next_night["session_kind"] == "night"
    assert next_night["session_key"] != late["session_key"]
    sessions = ingest.imglib.compute_sessions(db.images())
    assert [session["session_kind"] for session in sessions] == ["night", "day", "night"]
    assert set(sessions[0]["image_ids"]) == {early["id"], late["id"]}
    assert sessions[0]["start"] == "2026-12-21T16:00:00"
    assert sessions[0]["end"] == "2026-12-22T06:00:00"
    assert sessions[0]["session_start"] < sessions[0]["start"]
    assert sessions[0]["session_end"] > sessions[0]["end"]


def test_explicit_calibration_copies_session_but_preserves_own_site_and_capture(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _site_capture("2026-08-22T23:00:00Z"))
    target = ingest.calibration_import_target(db, archive, first["id"], "dark")
    calibration = _import(db, archive,
                          _site_capture("2026-09-15T12:00:00Z", b"calibration", (40, -74)),
                          "calibration.fit", frame_override="dark", session_target=target)
    assert calibration["ok"]
    rec = db.image(calibration["id"])
    assert (rec["site_lat"], rec["site_lon"]) == (40, -74)
    assert rec["date_obs"] == "2026-09-15T12:00:00"
    assert all(rec[key] == db.image(first["id"])[key] for key in ingest.imglib.SESSION_FIELDS)
    chosen = ingest.imglib.session_for_anchor(db.images(), first["id"])
    assert set(chosen["image_ids"]) == {first["id"], calibration["id"]}
    before = dict(rec)
    ingest.reindex_library(db, archive)
    assert db.image(calibration["id"]) == before


@pytest.mark.parametrize("header_location", [None, ("nan", 13), (52, "infinity")])
def test_default_site_only_applies_when_header_location_is_absent(dedup_store, header_location):
    db, archive = dedup_store
    db.set_setting("observing_site", {"latitude":52.52, "longitude":13.405})
    result = _import(db, archive, _site_capture("2026-08-22T23:00:00Z", location=header_location))
    assert result["ok"]
    if header_location is None:
        assert (result["site_lat"], result["site_lon"]) == (52.52, 13.405)
        assert result["session_kind"] == "night"
    else:
        assert result["site_lat"] is result["site_lon"] is None
        assert result["session_kind"] == "unassigned"
        assert "unassigned_" in result["stored"]


def test_reindex_backfills_header_site_and_solar_metadata_without_moving_files(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _site_capture("2026-08-22T23:00:00Z"))
    original = db.image(first["id"])
    db.conn.execute("UPDATE images SET site_lat=NULL,site_lon=NULL,session_key=NULL,session_kind=NULL,"
                    "session_start=NULL,session_end=NULL,date_obs=? WHERE id=?",
                    ("2026-08-22T20:00:00", first["id"]))
    db.conn.commit()
    before_bytes = (archive / original["file"]).read_bytes()
    result = ingest.reindex_library(db, archive)
    assert result["added"] == result["repaired"] == 0 and result["skipped"] == 1
    updated = db.image(first["id"])
    assert updated["date_obs"] == "2026-08-22T23:00:00"
    assert all(updated[key] == original[key] for key in ("site_lat", "site_lon", *ingest.imglib.SESSION_FIELDS))
    assert updated["file"] == original["file"] and (archive / original["file"]).read_bytes() == before_bytes


def test_duplicate_preserves_original_session_when_default_site_changes(dedup_store):
    db, archive = dedup_store
    content = _site_capture("2026-08-22T23:00:00Z", location=None)
    first = _import(db, archive, content)
    before = db.image(first["id"])
    db.set_setting("observing_site", {"latitude":52.52, "longitude":13.405})
    duplicate = _import(db, archive, content, "copy.fit", frame_override="dark")
    assert duplicate["skipped"] and duplicate["id"] == first["id"]
    assert db.image(first["id"]) == before and duplicate["session_kind"] == "unassigned"


def test_reindex_session_backfill_and_fingerprints_roll_back_on_addition_error(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _site_capture("2026-08-22T23:00:00Z"))
    db.conn.execute("UPDATE images SET site_lat=NULL,site_lon=NULL,session_key=NULL,session_kind=NULL,"
                    "session_start=NULL,session_end=NULL,content_sha256=NULL WHERE id=?", (first["id"],))
    db.conn.execute("CREATE TRIGGER refuse_reindex_insert BEFORE INSERT ON images "
                    "BEGIN SELECT RAISE(ABORT,'refuse addition'); END")
    db.conn.commit()
    target = archive / "Manual/lights/new.fit"
    target.parent.mkdir(parents=True)
    target.write_bytes(_site_capture("2026-08-22T23:00:00Z", b"new"))
    before = db.images()
    with pytest.raises(sqlite3.IntegrityError, match="refuse addition"):
        ingest.reindex_library(db, archive)
    assert db.images() == before


def test_reindex_older_attachment_ignores_invalid_unrelated_anchor_date(dedup_store):
    db, archive = dedup_store
    first = _import(db, archive, _site_capture("2026-08-22T23:00:00Z"))
    target = ingest.calibration_import_target(db, archive, first["id"], "flat")
    attached = _import(db, archive, _site_capture("2026-09-15T12:00:00Z", b"flat", (40, -74)),
                       "flat.fit", frame_override="flat", session_target=target)
    other = _import(db, archive, _site_capture("2026-09-16T12:00:00Z", b"other"), "other.fit")
    # Older parsers stored impossible ISO dates; attachment backfill must still
    # find its valid anchor when another ordinary record has one of those dates.
    db.conn.execute("UPDATE images SET date_obs='2026-13-31T12:00:00' WHERE id=?", (other["id"],))
    db.conn.execute("UPDATE images SET session_key=NULL,session_kind=NULL,session_start=NULL,"
                    "session_end=NULL WHERE id=?", (attached["id"],))
    db.conn.commit()
    ingest.reindex_library(db, archive)
    assert db.image(attached["id"])["session_key"] == db.image(first["id"])["session_key"]
    assert db.image(other["id"])["date_obs"] == "2026-09-16T12:00:00"


@pytest.mark.parametrize("legacy_anchor", [False, True])
def test_reindex_location_backfill_keeps_explicit_calibration_with_target(dedup_store, legacy_anchor):
    db, archive = dedup_store
    first = _import(db, archive, _site_capture("2026-08-22T23:00:00Z", location=None))
    if legacy_anchor:
        db.conn.execute("UPDATE images SET session_key=NULL,session_kind=NULL,session_start=NULL,"
                        "session_end=NULL WHERE id=?", (first["id"],))
        db.conn.commit()
    target = ingest.calibration_import_target(db, archive, first["id"], "flat")
    flat = _import(db, archive, _site_capture("2026-09-15T12:00:00Z", b"flat", (40, -74)),
                   "flat.fit", frame_override="flat", session_target=target)
    db.set_setting("observing_site", {"latitude": 52.52, "longitude": 13.405})
    ingest.reindex_library(db, archive)
    anchor, attached = db.image(first["id"]), db.image(flat["id"])
    assert anchor["session_kind"] == "night"
    assert attached["session_key"] == anchor["session_key"]
    assert attached["date_obs"] == "2026-09-15T12:00:00"
    assert (attached["site_lat"], attached["site_lon"]) == (40, -74)
    ingest.reindex_library(db, archive)
    assert db.image(flat["id"]) == attached
