"""Real FITS decoding, bounded reads, containment and read-only previews."""
import hashlib
import io
import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient
from PIL import Image

from app import fits_preview
from app.database import Database
from app.server import create_app


def fits_bytes(data=None, header=None, extensions=()):
    stream = io.BytesIO()
    primary = fits.PrimaryHDU(data)
    if header is not None: primary.header.update(header)
    fits.HDUList([primary, *extensions]).writeto(stream)
    return stream.getvalue()


def decode(png):
    with Image.open(io.BytesIO(png)) as image:
        assert image.mode == "L"
        return np.array(image)


def render(data, size=512):
    source = io.BytesIO(data)
    result = fits_preview.render_preview(source, size)
    assert not source.closed
    return result, decode(result.png)


@pytest.fixture
def library(tmp_path):
    db = Database(tmp_path / "preview.db")
    root = tmp_path / "archive"
    db.set_setting("image_dir", str(root))
    return db, root


def stored_image(db, root, data, relative="M31/session/lights/a.fit"):
    target = root / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    info = target.stat()
    image_id = db.add_image(dict(file=relative, orig_name=target.name, object_name="M31", ingested_at="today",
        content_sha256=hashlib.sha256(data).hexdigest(), file_size=info.st_size, file_mtime_ns=info.st_mtime_ns))
    return image_id, target


def snapshot(db):
    with db.lock: return tuple(db.conn.iterdump())


@pytest.mark.parametrize("dtype", [np.uint8, np.int16, np.uint16, np.int32, np.int64, np.float32, np.float64])
def test_pixel_types_grayscale_and_lower_origin(dtype):
    result, gray = render(fits_bytes(np.arange(16, dtype=dtype).reshape(4, 4)))
    assert (result.width, result.height, result.hdu, result.first_plane) == (4, 4, 0, False)
    assert gray.shape == (4, 4) and gray[3, 0] == 0 and gray[0, 3] == 255
    assert np.all(np.diff(gray[3].astype(int)) >= 0)


def test_scaling_blank_and_nonfinite_pixels():
    header = fits.Header({"BSCALE": -2.0, "BZERO": 1000.0, "BLANK": -32768})
    _, gray = render(fits_bytes(np.array([[-32768, 100], [200, 300]], dtype=np.int16), header))
    assert gray[1, 0] == 0 and gray[1, 1] == 255 and gray[0, 1] == 0
    _, gray = render(fits_bytes(np.array([[np.nan, np.inf], [-np.inf, 4], [5, 6]], dtype=np.float64)))
    assert gray[2, 0] == gray[2, 1] == gray[1, 0] == 0 and gray[0, 1] == 255


def test_extension_selection_and_first_cube_plane():
    table = fits.BinTableHDU.from_columns([fits.Column(name="value", format="J", array=[1, 2])])
    cube = np.stack((np.arange(12).reshape(3, 4), np.full((3, 4), -1000))).astype(np.int16)
    result, gray = render(fits_bytes(extensions=[table, fits.ImageHDU(cube)]))
    assert result.hdu == 2 and result.first_plane and gray.shape == (3, 4)
    assert gray[2, 0] == 0 and gray[0, 3] == 255


def test_rice_compressed_image():
    data = np.arange(300, dtype=np.int16).reshape(15, 20)
    result, gray = render(fits_bytes(extensions=[fits.CompImageHDU(data, compression_type="RICE_1")]), 10)
    assert result.hdu == 1 and gray.shape == (8, 10) and gray[0, -1] == 255 and gray[-1, 0] == 0


def test_compressed_cube_and_aggregate_tile_band_limit(monkeypatch):
    cube = np.stack((np.arange(300).reshape(15, 20), np.full((15, 20), -1000))).astype(np.int16)
    data = fits_bytes(extensions=[fits.CompImageHDU(cube, compression_type="GZIP_1", tile_shape=(2, 5, 1))])
    result, gray = render(data, 10)
    assert result.first_plane and gray.max() == 255
    monkeypatch.setattr(fits_preview, "MAX_TILE_PIXELS", 100)
    with pytest.raises(fits_preview.PreviewUnavailable, match="tiles exceed"): render(data, 10)


def test_large_image_never_loads_full_data(library, monkeypatch):
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes(np.arange(600 * 900, dtype=np.int32).reshape(600, 900)))
    original = fits.PrimaryHDU.data
    def refused(hdu): raise AssertionError("whole image array was accessed")
    monkeypatch.setattr(fits.PrimaryHDU, "data", property(refused, original.__set__))
    before = target.read_bytes()
    result = fits_preview.preview_image(db, image_id, 128)
    assert max(decode(result.png).shape) <= 128 and target.read_bytes() == before


def test_constant_sparse_and_extreme_finite_ranges():
    _, gray = render(fits_bytes(np.zeros((4, 4), dtype=np.int16)))
    assert np.all(gray == 128)
    sparse = np.zeros((20, 20), dtype=np.float32); sparse[10, 10] = 100
    _, gray = render(fits_bytes(sparse)); assert np.count_nonzero(gray) == 1 and gray.max() == 255
    _, gray = render(fits_bytes(np.array([[-1e308, -1e307], [1e307, 1e308]], dtype=np.float64)))
    assert gray[1, 0] == 0 and gray[0, 1] == 255 and 0 < gray[1, 1] < gray[0, 0] < 255


@pytest.mark.parametrize("data", [b"not FITS", fits_bytes(), fits_bytes(np.full((3, 3), np.nan)), fits_bytes(np.arange(4))])
def test_unusable_pixels_are_explained_errors(data):
    with pytest.raises(fits_preview.PreviewUnavailable): render(data)


def test_truncated_pixels_are_refused():
    data = fits_bytes(np.arange(100, dtype=np.int16).reshape(10, 10))
    with pytest.raises(fits_preview.PreviewUnavailable, match="truncated"): render(data[:2880 + 190])


def test_real_disk_reads_cannot_bypass_budget(library, monkeypatch):
    from astropy.io.fits.util import _array_from_file, isfile
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes(np.arange(10_000, dtype=np.int16).reshape(100, 100)))
    with target.open("rb") as source:
        wrapper = fits_preview._BorrowedFile(source)
        assert not isfile(wrapper)
        before = wrapper.remaining
        _array_from_file(wrapper, np.dtype("uint8"), 64)
        assert wrapper.remaining == before - 64
    monkeypatch.setattr(fits_preview, "MAX_READ_BYTES", 4000)
    with pytest.raises(fits_preview.PreviewUnavailable): fits_preview.preview_image(db, image_id)


def test_png_api_metadata_and_no_mutations(library):
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes(np.arange(1200, dtype=np.uint16).reshape(30, 40)))
    before = snapshot(db), target.read_bytes(), target.stat().st_mtime_ns
    with TestClient(create_app(db)) as client:
        response = client.get(f"/api/images/{image_id}/preview")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff" and response.headers["cache-control"] == "no-store"
        assert response.headers["x-fits-size"] == "40x30" and response.headers["x-fits-hdu"] == "0"
        assert response.headers["x-fits-plane"] == "none" and decode(response.content).shape == (30, 40)
        assert client.get(f"/api/images/{image_id}/preview?size=2048").status_code == 422
        assert client.get(f"/api/images/{image_id}/preview", headers={"Origin": "https://example.com"}).status_code == 403
    assert before == (snapshot(db), target.read_bytes(), target.stat().st_mtime_ns)


def test_missing_and_nonimage_statuses(library):
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes())
    with TestClient(create_app(db)) as client:
        assert client.get("/api/images/999/preview").status_code == 404
        assert client.get(f"/api/images/{image_id}/preview").status_code == 422
        target.unlink(); assert client.get(f"/api/images/{image_id}/preview").status_code == 404


@pytest.mark.parametrize("image_id", [0, -1, 1 << 63])
def test_invalid_id_never_reaches_sqlite(library, monkeypatch, image_id):
    db, root = library
    def refused(*args): raise AssertionError("invalid ID reached SQLite")
    monkeypatch.setattr(db, "image", refused)
    with TestClient(create_app(db)) as client:
        assert client.get(f"/api/images/{image_id}/preview").status_code == 422


@pytest.mark.parametrize("unsafe", ["../outside.fit", "linked.fit", "parent/a.fit"])
def test_unsafe_paths_and_links_never_reach_reader(library, tmp_path, monkeypatch, unsafe):
    db, root = library; root.mkdir()
    outside = tmp_path / "outside.fit"; outside.write_bytes(fits_bytes(np.zeros((2, 2))))
    (root / "linked.fit").symlink_to(outside); (root / "parent").symlink_to(tmp_path, target_is_directory=True)
    image_id = db.add_image(dict(file=unsafe, orig_name="unsafe.fit", object_name="M31", ingested_at="today"))
    def refused(*args, **kwargs): raise AssertionError("unsafe file reached the reader")
    monkeypatch.setattr(fits_preview, "render_preview", refused)
    with TestClient(create_app(db)) as client:
        assert client.get(f"/api/images/{image_id}/preview").status_code == 404


def test_changed_file_is_refused_and_archive_conflicts_are_responsive(library, monkeypatch):
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes(np.arange(9).reshape(3, 3)))
    original = fits_preview.render_preview
    def changing(source, size, cancelled=None):
        result = original(source, size, cancelled); info = target.stat()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000)); return result
    before = snapshot(db)
    with TestClient(create_app(db)) as client:
        with db.archive_lock: assert client.get(f"/api/images/{image_id}/preview").status_code == 409
        monkeypatch.setattr(fits_preview, "render_preview", changing)
        assert client.get(f"/api/images/{image_id}/preview").status_code == 409
    assert snapshot(db) == before


def test_permission_refusal_and_parent_swap_are_not_read(library, tmp_path, monkeypatch):
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes(np.arange(4).reshape(2, 2)))
    original = os.open
    def denied(path, *args, **kwargs):
        if Path(path) == target: raise PermissionError("refused")
        return original(path, *args, **kwargs)
    with TestClient(create_app(db)) as client:
        monkeypatch.setattr(fits_preview.os, "open", denied)
        assert client.get(f"/api/images/{image_id}/preview").status_code == 403
        outside = tmp_path / "outside"; outside.mkdir(); (outside / target.name).write_bytes(b"private")
        def swapped(path, *args, **kwargs):
            if Path(path) == target:
                target.parent.rename(target.parent.parent / "old-lights"); target.parent.symlink_to(outside, target_is_directory=True)
            return original(path, *args, **kwargs)
        def refused(*args): raise AssertionError("outside file reached the reader")
        monkeypatch.setattr(fits_preview.os, "open", swapped)
        monkeypatch.setattr(fits_preview, "render_preview", refused)
        assert client.get(f"/api/images/{image_id}/preview").status_code in (404, 409)


def test_shutdown_interrupts_preview_before_encoding(library, monkeypatch):
    db, root = library
    image_id, target = stored_image(db, root, fits_bytes(np.arange(100).reshape(10, 10)))
    app = create_app(db); original = fits_preview._BorrowedFile.read
    interrupted = False
    def stopping(reader, size=-1):
        nonlocal interrupted
        position = reader.tell(); result = original(reader, size)
        if position >= 2880 and result and not interrupted:
            interrupted = True; app.state.archive_health.request_shutdown()
        return result
    def refused(*args): raise AssertionError("cancelled preview reached PNG encoding")
    monkeypatch.setattr(fits_preview._BorrowedFile, "read", stopping)
    monkeypatch.setattr(fits_preview, "_grayscale", refused)
    before = snapshot(db), target.read_bytes()
    with TestClient(app) as client:
        response = client.get(f"/api/images/{image_id}/preview")
        assert response.status_code == 409 and "shutdown" in response.json()["detail"]
    assert interrupted and before == (snapshot(db), target.read_bytes())
