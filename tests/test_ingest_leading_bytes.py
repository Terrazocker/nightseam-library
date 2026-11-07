"""Same-size duplicate checks compare leading bytes before hashing.

Different leading bytes prove different files from current bytes alone, so
new exposures of one camera (all one size) are ruled out without hashing the
archive. Matching leading bytes, true duplicates and unreadable files still
take the full current-byte path.
"""
import io
import os

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

from app import ingest, server
from test_compression import library
from test_ingest_compression import upload


@pytest.fixture(autouse=True)
def no_version_cache(monkeypatch):
    # Count actual reads, not reuse of earlier ones.
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)


def frame(index=0, tail=0):
    """A 256x256 exposure (~131 KB): its leading 64 KiB hold the header and
    the first rows; `tail` changes only the last row."""
    pixels = (np.arange(256 * 256, dtype=np.uint64) * 2654435761 % 60000).astype(np.uint16)
    pixels = (pixels + index).reshape(256, 256)
    if tail:
        pixels[-1, :] += tail
    header = fits.Header({'OBJECT': 'Lead test', 'DATE-OBS': f'2026-08-22T22:{index // 60:02d}:{index % 60:02d}',
                          'EXPTIME': 10, 'SITELAT': 50.0, 'SITELONG': 7.0, 'IMAGETYP': 'LIGHT'})
    output = io.BytesIO()
    fits.PrimaryHDU(pixels, header=header).writeto(output)
    data = output.getvalue()
    assert len(data) > ingest._LEAD_BYTES + 512
    return data


def counting(monkeypatch, archive):
    """Record archive (not stage) reads by the full hash and the lead read."""
    hashed, leads = [], []
    fingerprint, leading = ingest._fingerprint_file, ingest._leading_bytes

    def measured(path, *args):
        if not path.name.endswith('.part'):
            hashed.append(path)
        return fingerprint(path, *args)

    def lead(path):
        if not path.name.endswith('.part'):
            leads.append(path)
        return leading(path)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    monkeypatch.setattr(ingest, '_leading_bytes', lead)
    return hashed, leads


def post(db, files):
    with TestClient(server.create_app(db)) as client:
        return client.post('/api/images/ingest', files=files).json()['results']


def test_new_same_size_exposures_are_never_hashed_and_each_lead_is_read_once(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    hashed, leads = counting(monkeypatch, archive)
    count = 24                                     # the HTTP endpoint's request limit
    results = post(db, [('files', (f'new-{i}.fit', frame(i))) for i in range(count)])
    assert all(item['ok'] and not item.get('skipped') for item in results)
    assert hashed == []                            # previously one full read per earlier file
    assert len(leads) == len(set(leads)) == count - 1
    for i, item in enumerate(results):
        assert (archive / item['stored']).read_bytes() == frame(i)


def test_a_true_duplicate_is_still_found_from_current_bytes(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    first = post(db, [('files', ('a.fit', frame(0))), ('files', ('b.fit', frame(1)))])
    hashed, _ = counting(monkeypatch, archive)
    (again,) = post(db, [('files', ('again.fit', frame(0)))])
    assert again['skipped'] and again['id'] == first[0]['id']
    assert archive / first[0]['stored'] in hashed  # a positive is always rehashed
    assert archive / first[1]['stored'] not in hashed


def test_same_leading_bytes_with_another_tail_are_hashed_then_imported(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    (stored,) = post(db, [('files', ('a.fit', frame(0)))])
    hashed, _ = counting(monkeypatch, archive)
    edited = frame(0, tail=7)
    assert edited[:ingest._LEAD_BYTES] == frame(0)[:ingest._LEAD_BYTES] and edited != frame(0)
    (result,) = post(db, [('files', ('a-edited.fit', edited))])
    assert result['ok'] and not result.get('skipped')
    assert hashed.count(archive / stored['stored']) == 1


def test_an_archived_file_rewritten_with_restored_mtime_is_judged_by_its_current_bytes(tmp_path, monkeypatch):
    """No request-scoped or stored hash may hide an external rewrite."""
    db, archive, _ = library(tmp_path, count=0)
    original = upload(db, archive, frame(0), name='a.fit')
    target = archive / original['stored']
    info = target.stat()
    target.write_bytes(frame(1))                   # another exposure, same size
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    # The rewritten bytes are now a duplicate of that row, whatever its stored hash says.
    rewritten = upload(db, archive, frame(1), name='b.fit')
    assert rewritten['skipped'] and rewritten['id'] == original['id']
    # The bytes the row was imported with are no longer in the archive.
    restored = upload(db, archive, frame(0), name='c.fit')
    assert restored['ok'] and not restored.get('skipped')


def test_unreadable_leading_bytes_fall_back_to_a_full_hash(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    (stored,) = post(db, [('files', ('a.fit', frame(0)))])
    hashed, _ = counting(monkeypatch, archive)
    leading = ingest._leading_bytes
    monkeypatch.setattr(ingest, '_leading_bytes',
                        lambda path: leading(path) if path.name.endswith('.part') else None)
    (result,) = post(db, [('files', ('b.fit', frame(1)))])
    assert result['ok'] and not result.get('skipped')
    assert archive / stored['stored'] in hashed


def test_turning_the_filter_off_restores_full_hashing(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    monkeypatch.setattr(ingest, '_LEAD_BYTES', 0)
    hashed, leads = counting(monkeypatch, archive)
    results = post(db, [('files', (f'n-{i}.fit', frame(i))) for i in range(4)])
    assert all(item['ok'] for item in results)
    assert leads == [] and len(hashed) == 3
