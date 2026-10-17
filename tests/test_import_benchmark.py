"""Exercise benchmark audits against real ingest and actual CFITSIO binaries."""
from datetime import datetime
import hashlib
import io

from astropy.io import fits
from fastapi.testclient import TestClient

from app import compression, server
from scripts import bench_windows_import as bench
from test_compression import library


def test_large_benchmark_fixtures_are_valid_equal_size_and_distinct():
    hashes = set()
    for index in (0, 63, 511):
        raw = bench.exposure(index)
        assert len(raw) == 4_150_080
        with fits.open(io.BytesIO(raw)) as hdus:
            hdus.verify('exception')
            assert hdus[0].data.shape == (1080, 1920)
            assert hdus[0].data.dtype.name == 'uint16'
            assert datetime.fromisoformat(hdus[0].header['DATE-OBS']).year == 2026
        hashes.add(hashlib.sha256(raw).hexdigest())
    assert len(hashes) == 3


def test_benchmark_audits_and_restricted_runtime_rice_workload(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    sources = bench.fixtures(tmp_path / 'inputs', 5)
    with TestClient(server.create_app(db)) as client:
        report = bench.benchmark(client, archive, tmp_path / 'catalog.db', sources, 3)
    assert [batch['count'] for batch in report['batches']] == [3, 2]
    assert report['source_and_archive_hashes_verified'] == 5
    assert report['same_size_restored_mtime_edit_detected']
    assert report['duplicate_class_session_location_preserved']

    rice_root = tmp_path / 'rice'
    rice_root.mkdir()
    rice_db, rice_archive, _ = library(rice_root, count=0)
    native, calls = compression.native, []
    def measured(executable, *args):
        calls.append(executable.stem)
        return native(executable, *args)
    monkeypatch.setattr(compression, 'native', measured)
    with TestClient(server.create_app(rice_db)) as client:
        result = bench.rice_smoke(client, rice_archive)
    assert all(result.values()) and 'fpack' in calls and 'funpack' in calls
