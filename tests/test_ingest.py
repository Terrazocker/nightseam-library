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
    assert ingest.reindex_library(db, archive) == {"found": 8, "added": 8, "skipped": 0, "failed": 0}
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


def test_fractional_session_attachment_cannot_bridge_exact_twelve_hour_boundary(dedup_store):
    from app import imglib
    db, archive = dedup_store
    earlier = db.add_image({"file": "Target/first/lights/a.fit", "orig_name": "a.fit",
                            "object_name": "Target", "imagetyp": "light",
                            "date_obs": "2026-08-22T08:00:00.900000", "ingested_at": ""})
    anchor = db.add_image({"file": "Target/second/lights/b.fit", "orig_name": "b.fit",
                           "object_name": "Target", "imagetyp": "light",
                           "date_obs": "2026-08-22T20:00:00.900000", "ingested_at": ""})
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

def test_session_folder_formats_local_time():
    # 2026-08-22 23:14 UTC depends on the machine zone, but the shape is fixed
    name = session_folder_name(time.mktime((2026, 8, 22, 23, 14, 0, 0, 0, -1)))
    assert len(name) == 15               # YYYY-MM-DD_HHMM
    assert name[4] == "-" and name[7] == "-" and name[10] == "_"
    assert name[:10].count("-") == 2


def test_session_folder_undated():
    assert session_folder_name(None) == "undated"


def test_session_folder_zero_epoch():
    # epoch 0 = 1970-01-01 00:00 UTC -> local may shift the date, shape stays
    assert len(session_folder_name(0.0)) == 15


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
        # The unresolved-name branch retains the original exact-name filter;
        # it includes rows even if they carry an object_id.
        assert [r["file"] for r in db.images_for_object(None, "Mystery")] == [
            "b.fit", "c.fit"]
        assert db.images_for_object(None, "mystery") == []

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


def test_unresolved_session_lookup_joins_exact_name_even_with_existing_id(tmp_path):
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
        assert selected.name == session_folder_name(date_epoch(old_date))
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
    assert result == {"found": 2, "added": 0, "skipped": 2, "failed": 0}
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

    def paused(path):
        assert not db.lock._is_owned()
        entered.set()
        assert release.wait(3), "event loop did not release the worker"
        return original(path)

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

    def paused(path):
        entered.set()
        assert release.wait(3)
        return original(path)

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

        async def invoke():
            loop["thread"] = threading.get_ident()
            return await endpoint(files=[], frame="", session="",
                                  run_id="", session_anchor_id=None)

        result = asyncio.run(invoke())

        # the endpoint finished successfully with nothing to ingest
        assert result == {"results": []}
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
