"""Regression checks for bounded preparation and actual-byte dedup safety."""
import hashlib
import os
import threading
from contextlib import contextmanager

import pytest

from fastapi.testclient import TestClient

from app import compression, ingest, server
from test_compression import exposure, library, no_stages
from test_ingest_compression import upload


def test_unchanged_negative_candidates_are_hashed_once_and_edits_invalidate(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    original = ingest._fingerprint_file
    reads = []
    def measured(path, *args):
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    results = [upload(db, archive, exposure(index), name=f'{index}.fit') for index in range(12)]
    assert all(result['ok'] for result in results)
    assert len(reads) == 11  # one actual read per earlier file, rather than 66
    path = archive / results[0]['stored']
    info = path.stat()
    actual = exposure(40)
    path.write_bytes(actual)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    repeated = upload(db, archive, actual, name='external-edit.fit')
    assert repeated['skipped'] and repeated['id'] == results[0]['id']
    assert db.image(results[0]['id'])['content_sha256'] == hashlib.sha256(actual).hexdigest()
    count = len(reads)
    assert upload(db, archive, actual)['skipped']
    assert len(reads) > count  # positive matches still check current bytes


def test_compression_prepares_concurrently_without_archive_lock_and_publishes_in_order(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    native = compression.native
    barrier = threading.Barrier(2, timeout=10)
    calls, guard = [], threading.Lock()
    def parallel(executable, *args):
        if executable.name == 'fpack.exe':
            assert not db.archive_lock._is_owned()
            with guard:
                calls.append(executable.name)
                number = len(calls)
            if number <= 2:
                barrier.wait()
        return native(executable, *args)
    monkeypatch.setattr(compression, 'native', parallel)
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', data=dict(compress='true', compression_mode='fast'),
            files=[('files', (f'{index}.fit', exposure(index))) for index in range(6)]).json()['results']
    assert all(item['ok'] for item in result)
    assert [item['id'] for item in result] == sorted(item['id'] for item in result)
    assert [record['orig_name'] for record in db.images()] == [f'{index}.fit' for index in range(6)]
    no_stages(db, archive)


def test_resolver_reused_across_chunks_and_invalidated_by_catalog_edit(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    builds = []
    original = server.CatalogResolver
    def counted(catalog):
        builds.append(1)
        return original(catalog)
    monkeypatch.setattr(server, 'CatalogResolver', counted)
    with TestClient(server.create_app(db)) as client:
        for index in range(2):
            assert client.post('/api/images/ingest', files={'files': (f'{index}.fit', exposure(index))}).json()['results'][0]['ok']
        assert len(builds) == 1
        with db.lock:
            db.conn.execute("UPDATE objects SET primary_name=primary_name WHERE id=(SELECT id FROM objects LIMIT 1)")
            db.conn.commit()
        assert client.post('/api/images/ingest', files={'files': ('new.fit', exposure(2))}).json()['results'][0]['ok']
        assert len(builds) == 2


def test_unavailable_file_versions_fall_back_to_current_byte_reads(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    @contextmanager
    def unsupported(path):
        yield None
    monkeypatch.setattr(ingest, 'pinned_version', unsupported)
    original = ingest._fingerprint_file
    reads = []
    def measured(path, *args):
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    for index in range(4):
        assert upload(db, archive, exposure(index))['ok']
    assert len(reads) == 6


def test_bounded_negative_cache_remains_useful_above_capacity(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 3)
    original = ingest._fingerprint_file
    reads = []
    def measured(path, *args):
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    for index in range(8):
        assert upload(db, archive, exposure(index))['ok']
    assert len(db._ingest_fingerprints) == 3
    assert len(reads) == 13  # a thrashing 3-entry LRU would read all 28 candidates


def test_known_compressed_duplicate_does_not_need_encoder(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    data = exposure()
    first = upload(db, archive, data, compress=True)
    def unavailable():
        raise ValueError('synthetic unavailable encoder')
    monkeypatch.setattr(compression, 'tools', unavailable)
    repeated = upload(db, archive, data, compress=True)
    assert repeated['skipped'] and repeated['id'] == first['id']
    assert len(db.images()) == 1
    assert not list(archive.glob('.nightseam-import-*'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows pinned negative batch index')
def test_http_batch_checks_earlier_files_once_and_detects_next_request_edits(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=64)
    previous = {archive / record['file'] for record in db.images()}
    fingerprint = ingest._dedup_fingerprint
    checks = []
    def measured(db, path, digest, cancel=None):
        if path in previous:
            checks.append(path)
        return fingerprint(db, path, digest, cancel)
    monkeypatch.setattr(ingest, '_dedup_fingerprint', measured)
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', files=[
            ('files', (f'new-{index}.fit', exposure(index + 100))) for index in range(24)]).json()['results']
        assert all(item['ok'] for item in result)
        assert len(checks) == 64  # not 64 * 24 per-file metadata inspections
        record = db.images()[0]
        path = archive / record['file']
        before = path.stat()
        changed = exposure(300)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        repeated = client.post('/api/images/ingest', files={'files': ('edited.fit', changed)}).json()['results'][0]
        assert repeated['skipped'] and repeated['id'] == record['id']


def test_http_import_commits_image_and_history_once(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    statements = []
    db.conn.set_trace_callback(statements.append)
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', files={'files': ('atomic.fit', exposure())}).json()['results'][0]
    db.conn.set_trace_callback(None)
    assert result['ok'] and len(db.images()) == len(db.import_batches()) == 1
    assert sum(sql.strip().upper() == 'COMMIT' for sql in statements) == 1


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing and directory pins')
def test_batch_pins_refuse_writes_and_renames_until_closed(tmp_path):
    db, archive, _ = library(tmp_path, count=1)
    record = db.images()[0]
    path = archive / record['file']
    index = ingest.DedupBatch.prepare(db, archive, {path.stat().st_size})
    try:
        assert index.entries
        with pytest.raises(PermissionError):
            path.write_bytes(b'blocked external writer')
        with pytest.raises(OSError):
            path.rename(path.with_suffix('.moved'))
        with pytest.raises(OSError):
            path.parent.rename(path.parent.with_name('moved-parent'))
    finally:
        index.close()
    path.write_bytes(b'writer allowed after request cleanup')
    assert path.read_bytes() == b'writer allowed after request cleanup'


def test_parallel_compression_database_failure_stops_later_publication(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    db.conn.execute("CREATE TRIGGER reject_second BEFORE INSERT ON images WHEN NEW.orig_name='1.fit' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    db.conn.commit()
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', data=dict(compress='true', compression_mode='fast'),
            files=[('files', (f'{index}.fit', exposure(index))) for index in range(8)]).json()['results']
    assert result[0]['ok'] and result[1]['unconfirmed']
    assert all(item['unattempted'] for item in result[2:])
    assert len(db.images()) == len(list(archive.rglob('*.fz'))) == 1
    assert not list(archive.glob('.nightseam-import-*'))
    no_stages(db, archive)


def test_native_tool_cache_revalidates_changed_executable_with_restored_mtime(tmp_path, monkeypatch):
    root = tmp_path / 'tools'
    destination = root / 'vendor/fits-tools/windows-x64'
    destination.mkdir(parents=True)
    import json
    provenance = {'binaries': {}}
    for name in ('fpack.exe', 'funpack.exe'):
        data = name.encode('ascii')
        (destination / name).write_bytes(data)
        provenance['binaries'][name] = dict(sha256=hashlib.sha256(data).hexdigest())
    (destination / 'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
    monkeypatch.setattr(compression, 'resource_dir', lambda: root)
    assert compression.tools()['fpack'] == destination / 'fpack.exe'
    executable = destination / 'fpack.exe'
    before = executable.stat()
    executable.write_bytes(b'X' * before.st_size)
    os.utime(executable, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match='integrity check'):
        compression.tools()
