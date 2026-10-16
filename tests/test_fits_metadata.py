"""Compressed/header-only reads preserve input bytes and bound traversal work."""
import asyncio
import gzip
import hashlib
import io
from pathlib import Path
import runpy
import sqlite3
import threading

import numpy as np
import pytest
from astropy.io import fits as astrofits
from starlette.datastructures import UploadFile

from app import archive_health, fits_metadata as metadata, ingest
from app.database import Database
from _util import make_fits


def exposure(*, tiled=True, wrapper=False, primary=None, extra=None, codec="RICE_1"):
    header = astrofits.Header({"OBJECT": "Compression target", "EXPTIME": 45.5,
        "DATE-OBS": "2026-09-15T22:14:05.125", "IMAGETYP": "LIGHT",
        "FILTER": "Ha", "INSTRUME": "Synthetic camera", "SITELAT": 52.52,
        "SITELONG": 13.405, "OBJCTRA": "00:42:44", "OBJCTDEC": "+41:16:09"})
    header.update(extra or {})
    pixels = np.arange(64, dtype=np.uint16).reshape(8, 8)
    if tiled:
        hdus = astrofits.HDUList([astrofits.PrimaryHDU(header=astrofits.Header(primary or {})),
            astrofits.CompImageHDU(pixels, header=header, compression_type=codec)])
    else:
        hdus = astrofits.HDUList([astrofits.PrimaryHDU(pixels, header=header)])
    with io.BytesIO() as stream:
        hdus.writeto(stream)
        raw = stream.getvalue()
    return gzip.compress(raw, mtime=0) if wrapper else raw


def test_packaged_gate_fixtures_decode_independently():
    image = runpy.run_path(str(Path(__file__).parent / "browser" / "fits_fixtures.py"))["compressed_gate_image"]
    for tiled in (False, True):
        with astrofits.open(io.BytesIO(image(tiled))) as hdus:
            selected = hdus[1 if tiled else 0]
            assert np.array_equal(selected.data, np.arange(64).reshape(8, 8))
            assert selected.header["OBJECT"] == "Compressed gate capture"


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "library.db")
    archive = tmp_path / "archive"
    archive.mkdir()
    db.set_setting("image_dir", str(archive))
    yield db, archive
    db.conn.close()


def upload(store, data, name="frame.fits.fz", **kwargs):
    db, archive = store
    file = UploadFile(file=io.BytesIO(data), filename=name, size=len(data))
    return asyncio.run(ingest.ingest_one(db, [], archive, file, **kwargs))


@pytest.mark.parametrize("tiled,wrapper", [(False, False), (False, True), (True, False), (True, True)])
def test_reads_image_metadata_without_materializing_pixels(tmp_path, monkeypatch, tiled, wrapper):
    data = exposure(tiled=tiled, wrapper=wrapper)
    path = tmp_path / "staged.part"  # original filename is not needed to sniff content
    path.write_bytes(data)
    def forbidden(*args, **kwargs):
        pytest.fail("Metadata reader must not load image pixels")
    monkeypatch.setattr(astrofits, "open", forbidden)
    storage_info = {}
    header = metadata.read_metadata(path, storage_info=storage_info)
    assert storage_info['compressed'] == (tiled or wrapper)
    assert header["OBJECT"] == "Compression target" and header["EXPTIME"] == 45.5
    assert header["DATE-OBS"] == "2026-09-15T22:14:05.125"
    assert header["NAXIS"] == 2 and header["NAXIS1"] == header["NAXIS2"] == 8
    assert header["BITPIX"] == 16 and header["SITELAT"] == 52.52
    assert path.read_bytes() == data


@pytest.mark.parametrize("codec", ["RICE_1", "GZIP_1", "GZIP_2", "HCOMPRESS_1"])
def test_tiled_metadata_is_independent_of_pixel_codec(tmp_path, codec):
    path = tmp_path / "image.fz"
    path.write_bytes(exposure(codec=codec))
    assert metadata.read_metadata(path)["FILTER"] == "Ha"


def test_extension_precedence_and_primary_fallback_do_not_mix_aliases_or_sites(tmp_path):
    data = exposure(primary={"OBJECT": "Wrong", "EXPTIME": 999, "TELESCOP": "Wrong",
                             "SITELAT": 10, "SITELONG": 20, "RA": 10, "DEC": 20,
                             "TIMESYS": "UTC", "OBSERVER": "Primary observer"},
                    extra={"SITELAT": "bad"})
    path = tmp_path / "image.fz"
    path.write_bytes(data)
    header = metadata.read_metadata(path)
    assert header["OBJECT"] == "Compression target" and header["EXPTIME"] == 45.5
    assert header["OBSERVER"] == "Primary observer" and header["TIMESYS"] == "UTC"
    assert "TELESCOP" not in header and header["INSTRUME"] == "Synthetic camera"
    assert "RA" not in header and "DEC" not in header
    assert header["SITELAT"] == "bad" and header["SITELONG"] == 13.405


def test_incomplete_extension_site_does_not_borrow_primary_coordinate(tmp_path):
    raw = exposure(primary={"SITELAT": 10, "SITELONG": 20})
    with astrofits.open(io.BytesIO(raw)) as hdus:
        del hdus[1].header["SITELONG"]
        stream = io.BytesIO()
        hdus.writeto(stream)
    path = tmp_path / "image.fz"
    path.write_bytes(stream.getvalue())
    assert "SITELONG" not in metadata.read_metadata(path)


def table_before_image():
    table = astrofits.BinTableHDU.from_columns([
        astrofits.Column(name="VALUE", format="J", array=np.arange(8, dtype=np.int32))])
    image = astrofits.ImageHDU(np.ones((4, 4), dtype=np.int16),
                              header=astrofits.Header({"OBJECT": "First image", "EXPTIME": 10}))
    stream = io.BytesIO()
    astrofits.HDUList([astrofits.PrimaryHDU(), table, image,
        astrofits.ImageHDU(np.ones((2, 2)), header=astrofits.Header({"OBJECT": "Later image"}))]).writeto(stream)
    return stream.getvalue()


@pytest.mark.parametrize("wrapper", [False, True])
def test_skips_table_payload_and_selects_first_nonempty_image(tmp_path, wrapper):
    raw = table_before_image()
    path = tmp_path / "exposure"
    path.write_bytes(gzip.compress(raw) if wrapper else raw)
    assert metadata.read_metadata(path)["OBJECT"] == "First image"


def test_primary_image_remains_authoritative(tmp_path):
    path = tmp_path / "exposure.fits"
    stream = io.BytesIO()
    astrofits.HDUList([
        astrofits.PrimaryHDU(np.zeros((2, 2)), header=astrofits.Header({"OBJECT": "Primary"})),
        astrofits.ImageHDU(np.zeros((2, 2)), header=astrofits.Header({"OBJECT": "Other"}))]).writeto(stream)
    path.write_bytes(stream.getvalue())
    assert metadata.read_metadata(path)["OBJECT"] == "Primary"


def test_compressed_long_string_metadata_keeps_quotes_and_first_occurrence(tmp_path):
    value = "A long target's name " * 5
    path = tmp_path / "long.fz"
    data = exposure(extra={"OBJECT": value})
    with astrofits.open(io.BytesIO(data)) as hdus:
        hdus[1].header.append(("OBJECT", "Wrong duplicate"))
        stream = io.BytesIO()
        hdus.writeto(stream)
    path.write_bytes(stream.getvalue())
    assert metadata.read_metadata(path)["OBJECT"] == value.rstrip()


def test_plain_legacy_header_tolerance_remains(tmp_path):
    path = tmp_path / "legacy.fit"
    path.write_bytes(make_fits([("SIMPLE", "T"), ("OBJECT", "'Legacy'")], end=False))
    assert metadata.read_metadata(path)["OBJECT"] == "Legacy"


def test_large_plain_missing_end_keeps_legacy_bounded_primary_fallback(tmp_path):
    path = tmp_path / "legacy.fit"
    header = make_fits([("SIMPLE", "T"), ("BITPIX", "16"), ("NAXIS", "0"),
                        ("OBJECT", "'Legacy'")], end=False)
    path.write_bytes(header + b"\0" * metadata.MAX_HEADER_BYTES)
    assert metadata.read_metadata(path)["OBJECT"] == "Legacy"


def test_gzip_primary_read_stops_before_pixels(tmp_path, monkeypatch):
    path = tmp_path / "image.gz"
    path.write_bytes(exposure(tiled=False, wrapper=True))
    monkeypatch.setattr(metadata, "MAX_GZIP_SCAN_BYTES", 2880)
    assert metadata.read_metadata(path)["OBJECT"] == "Compression target"


def test_gzip_optional_header_fields_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "image.gz"
    # FNAME without its terminator would otherwise make gzip loop byte by byte.
    path.write_bytes(b"\x1f\x8b\x08\x08" + b"\0" * 6 + b"x" * 1000)
    monkeypatch.setattr(metadata, "MAX_GZIP_INPUT_BYTES", 32)
    with pytest.raises(metadata.FitsMetadataError, match="compressed-byte"):
        metadata.read_metadata(path)


@pytest.mark.parametrize("name", ["frame.fit.gz", "frame.FITS.GZ", "frame.fts.gz", "frame.fz", "frame.fits.fz", "frame.fits", "frame.fts", "frame.fits.fz.gz"])
def test_import_keeps_complete_bytes_hash_and_extension_metadata(store, name):
    data = exposure(wrapper=name.lower().endswith(".gz"))
    result = upload(store, data, name)
    db, archive = store
    assert result["ok"] and result["exposure"] == 45.5 and result["session_kind"] == "night"
    row, = db.images()
    assert row["date_obs"] == "2026-09-15T22:14:05.125000"
    assert row["telescope"] == "Synthetic camera" and row["filter"] == "Ha"
    assert row["content_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["file_size"] == len(data) and (archive / row["file"]).read_bytes() == data
    assert row["orig_name"] == name and not list(archive.glob("*.part"))
    assert row['storage_compressed'] == 1  # Even tiled bytes named .fits or .fts.
    repeated = upload(store, data, "renamed.fz.gz" if name.lower().endswith(".gz") else "renamed.fz", frame_override="dark")
    assert repeated["skipped"] and repeated["id"] == row["id"]
    assert db.images()[0]["imagetyp"] == "light"


def test_plain_and_gzip_representations_keep_distinct_byte_identities(store):
    raw = exposure(tiled=False)
    assert upload(store, raw, "image.fits")["ok"]
    assert upload(store, gzip.compress(raw), "image.fits.gz")["ok"]
    assert len(store[0].images()) == 2
    assert [r['storage_compressed'] for r in store[0].images()] == [0, 1]


def test_compressed_calibration_keeps_capture_date_and_selected_session(store):
    db, archive = store
    anchor = upload(store, exposure(wrapper=True), "light.fits.gz")
    target = ingest.calibration_import_target(db, archive, anchor["id"], "dark")
    result = upload(store, exposure(wrapper=True, extra={"DATE-OBS": "2020-01-01T01:00:00",
        "IMAGETYP": "DARK", "SITELAT": -20, "SITELONG": 100}), "dark.fz.gz",
        frame_override="dark", session_target=target)
    assert result["ok"] and result["imagetyp"] == "dark"
    assert result["date_obs"] == "2020-01-01T01:00:00"
    assert result["session_key"] == anchor["session_key"]


def test_collision_names_preserve_compound_suffix(store):
    one = upload(store, exposure(wrapper=True), "capture.fits.fz.gz")
    two = upload(store, exposure(wrapper=True, extra={"FILTER": "OIII"}), "capture.fits.fz.gz")
    assert one["stored"].endswith("/capture.fits.fz.gz")
    assert two["stored"].endswith("/capture_2.fits.fz.gz")
    assert ingest.reindex_library(*store)["found"] == 2


@pytest.mark.parametrize("data", [b"\x1f\x8bbroken", gzip.compress(b"not FITS"),
    gzip.compress(b"SIMPLE  =                    T"),
    exposure()[:2880] + b"XTENSION" + b" " * 100])
def test_bad_compressed_headers_refuse_without_publishing(store, data):
    result = upload(store, data)
    assert not result["ok"] and "metadata" in result["error"]
    assert not store[0].images() and not list(store[1].rglob("*.part"))
    assert not list(store[1].rglob("*.fz"))


def test_metadata_failure_precedes_database_mutation(store, monkeypatch):
    monkeypatch.setattr(metadata, "MAX_HEADER_BYTES", 0)
    result = upload(store, exposure(wrapper=True))
    assert not result["ok"] and "limit" in result["error"]
    assert not store[0].images() and not list(store[1].rglob("*.part"))


def test_database_failure_rolls_back_compressed_publication(store, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("refuse insert")
    monkeypatch.setattr(store[0], "add_image", fail)
    with pytest.raises(sqlite3.OperationalError):
        upload(store, exposure(wrapper=True), "image.fits.gz")
    assert not list(store[1].rglob("*.gz")) and not list(store[1].rglob("*.part"))


def test_cancelled_metadata_worker_finishes_before_stage_cleanup(store, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = metadata.read_metadata
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(metadata, "read_metadata", paused)
    async def cancelled():
        db, archive = store
        file = UploadFile(file=io.BytesIO(exposure(wrapper=True)), filename="image.fits.gz")
        task = asyncio.create_task(ingest.ingest_one(db, [], archive, file))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and list(archive.glob("*.part"))
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancelled())
    assert not store[0].images() and not list(store[1].rglob("*.part"))


def test_limits_and_cancellation_bound_extension_traversal(tmp_path, monkeypatch):
    path = tmp_path / "image.gz"
    path.write_bytes(gzip.compress(table_before_image()))
    monkeypatch.setattr(metadata, "MAX_GZIP_SCAN_BYTES", 2 * 2880)
    with pytest.raises(metadata.FitsMetadataError, match="expanded-byte"):
        metadata.read_metadata(path)
    class Cancelled(Exception):
        pass
    def cancel():
        raise Cancelled()
    with pytest.raises(Cancelled):
        metadata.read_metadata(path, cancel)


@pytest.mark.parametrize("limit,value,match", [("MAX_HDUS", 1, "HDU-count"),
    ("MAX_HEADER_BYTES", 0, "header"), ("MAX_TOTAL_HEADER_BYTES", 2880, "total header"),
    ("MAX_SCAN_SECONDS", -1, "time limit")])
def test_header_hdu_and_time_limits(tmp_path, monkeypatch, limit, value, match):
    path = tmp_path / "image.fz"
    path.write_bytes(exposure(wrapper=True))
    monkeypatch.setattr(metadata, limit, value)
    with pytest.raises(metadata.FitsMetadataError, match=match):
        metadata.read_metadata(path)


def test_reindex_reads_compressed_metadata_and_keeps_existing_baseline(store):
    db, archive = store
    folder = archive / "Compression target" / "session" / "lights"
    folder.mkdir(parents=True)
    raw = exposure(wrapper=True)
    path = folder / "image.fits.gz"
    path.write_bytes(raw)
    result = ingest.reindex_library(db, archive)
    assert result["added"] == 1 and result["found"] == 1
    row, = db.images()
    assert row["date_obs"] == "2026-09-15T22:14:05.125000" and row["exposure"] == 45.5
    assert row["session_kind"] == "night" and row["content_sha256"] == hashlib.sha256(raw).hexdigest()
    baseline = db.conn.execute("SELECT sha256 FROM image_integrity WHERE image_id=?", (row["id"],)).fetchone()[0]
    assert row['storage_compressed'] == 1
    db.conn.execute('UPDATE images SET storage_compressed=NULL')
    db.conn.commit()
    result = ingest.reindex_library(db, archive)
    assert result["skipped"] == 1
    assert db.images()[0]['storage_compressed'] == 1
    assert db.conn.execute("SELECT sha256 FROM image_integrity WHERE image_id=?", (row["id"],)).fetchone()[0] == baseline
    assert path.read_bytes() == raw


def test_reindex_counts_bad_compressed_file_and_keeps_valid_candidate(store):
    db, archive = store
    folder = archive / "Target" / "lights"
    folder.mkdir(parents=True)
    (folder / "bad.fits.gz").write_bytes(b"\x1f\x8bbad")
    (folder / "valid.fz").write_bytes(exposure())
    result = ingest.reindex_library(db, archive)
    assert result["found"] == 2 and result["failed"] == 1 and result["added"] == 1


def test_reindex_parser_cancellation_leaves_database_unchanged(store, monkeypatch):
    db, archive = store
    folder = archive / "Target" / "lights"
    folder.mkdir(parents=True)
    (folder / "image.fz").write_bytes(exposure())
    cancel = threading.Event()
    original = metadata.read_metadata
    def stop(path, checkpoint, **kwargs):
        cancel.set()
        return original(path, checkpoint, **kwargs)
    monkeypatch.setattr(metadata, "read_metadata", stop)
    with pytest.raises(ingest.ReindexCancelled):
        ingest.reindex_library(db, archive, cancel)
    assert not db.images()


def test_health_discovers_untracked_compressed_exposures(store):
    db, archive = store
    for name in ("image.fz", "image.fits.gz", "image.FITS.FZ.GZ", "ignore.zip"):
        (archive / name).write_bytes(b"untracked bytes")
    report = archive_health.scan_archive(db, "quick", threading.Event(), lambda report: None)
    assert report["finding_counts"] == {"untracked": 3}
