"""Byte identity and population controls for the large packaged experiment."""
import hashlib
import io

from astropy.io import fits
import numpy as np
import pytest

from scripts import bench_populated_import as bench


def test_real_large_fixtures_are_distinct_valid_lossless_integer_fits():
    raw = bench.exposure(123, 2048)
    repeat = bench.exposure(123, 2048)
    changed = bench.exposure(124, 2048)
    assert len(raw) >= 8 * 2**20 and len(raw) % 2880 == 0
    assert raw == repeat and hashlib.sha256(raw).digest() != hashlib.sha256(changed).digest()
    with fits.open(io.BytesIO(raw)) as hdus:
        assert hdus[0].data.shape == (2048, 2048)
        assert hdus[0].data.dtype == np.uint16
        assert hdus[0].header['OBJECT'] == bench.PLAIN_OBJECT
    rice = bench.exposure(123, 2048, rice=True)
    with fits.open(io.BytesIO(rice)) as hdus:
        assert hdus[0].header['OBJECT'] == bench.RICE_OBJECT
        assert np.array_equal(hdus[0].data, fits.getdata(io.BytesIO(raw)))


def test_seed_manifest_is_root_independent_and_audits_real_archive_bytes(tmp_path):
    roots = [tmp_path / str(i) for i in range(2)]
    manifests = []
    for root in roots:
        root.mkdir()
        records = bench.seed(root, 12, 3, rows=(16,))
        manifests.append(records)
        assert len(records) == 15 and sum(r['large'] for r in records) == 3
        for record in records:
            path = root / 'archive' / record['file']
            assert path.stat().st_size == record['size']
            assert bench.bench.digest(path) == record['sha256']
        rows, integrity, pending = bench.bench.snapshot(root / 'data/nightseam.db')
        assert len(rows) == integrity == 15 and pending == 0
        assert len({r[6] for r in rows if r[3] == bench.PLAIN_OBJECT}) == 1
    assert bench.workload_digest(manifests[0], []) == bench.workload_digest(manifests[1], [])
    manifests[1][-1]['sha256'] = 'a' * 64
    assert bench.workload_digest(manifests[0], []) != bench.workload_digest(manifests[1], [])


def test_latency_refuses_missing_observations_and_keeps_slowest_sample():
    with pytest.raises(RuntimeError, match='No responsiveness samples'):
        bench.latency([])
    observed = bench.latency([.1, .03, .005, .2])
    assert observed['samples'] == 4 and observed['max_seconds'] == .2
    assert observed['median_seconds'] == .065
