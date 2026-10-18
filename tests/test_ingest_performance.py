"""Regression checks for bounded preparation and actual-byte dedup safety."""
import hashlib
import os
import threading
from contextlib import contextmanager

import pytest

from fastapi.testclient import TestClient

from app import compression, file_version, ingest, server
from test_compression import exposure, library, no_stages
from test_ingest_compression import upload
from scripts import stress_dedup as stress


def durable_versions(directory):
    """Whether this volume can reuse actual reads across handle lifetimes."""
    probe = directory / 'durable-probe.bin'
    probe.write_bytes(b'probe')
    try:
        with file_version.pinned_version(probe) as version:
            return version is not None and version.durable() is not None
    finally:
        probe.unlink()


def test_exact_duplicate_reads_only_its_original_in_populated_archive(tmp_path, monkeypatch):
    db = stress.Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    records = stress.seed(db, archive, 128, 32)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    original, reads = ingest._fingerprint_file, []
    def measured(path, *args):
        reads.append(path)
        assert not db.lock._is_owned()
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    index = ingest.DedupBatch.prepare(db, archive, {records[-1]['file_size']})
    try:
        assert not reads and not index.entries and not index.leased
        with db.archive_lock.for_batch(index):
            found = ingest._find_duplicate(db, archive, records[-1]['content_sha256'],
                records[-1]['file_size'], dedup_batch=index)
        assert found['id'] == records[-1]['id']
        assert reads == [archive / records[-1]['file']]
        assert not index.entries and not index.leased
    finally:
        index.close()


def test_stale_exact_hint_cannot_hide_restored_mtime_duplicate(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=2)
    records = db.images()
    incoming = exposure(20)
    digest = hashlib.sha256(incoming).hexdigest()
    # The earlier row claims the incoming hash but contains other bytes. The
    # later row was externally changed while retaining its recorded timestamp.
    db.set_image_fingerprint(records[0]['id'], digest, len(incoming), 1)
    target = archive / records[1]['file']
    before = target.stat()
    target.write_bytes(incoming)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    index = ingest.DedupBatch.prepare(db, archive, {len(incoming)})
    try:
        with db.archive_lock.for_batch(index):
            found = ingest._find_duplicate(db, archive, digest, len(incoming), dedup_batch=index)
        assert found['id'] == records[1]['id']
        assert found['imagetyp'] == records[1]['imagetyp']
        assert found['file'] == records[1]['file']
    finally:
        index.close()


def test_admission_hashes_under_one_pin_and_rechecks_new_request_bytes(tmp_path, monkeypatch):
    db = stress.Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    record, = stress.seed(db, archive, 1, 1)
    path = archive / record['file']
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    version, fingerprint = ingest.pinned_version, ingest._fingerprint_file
    opens, reads = [], []
    @contextmanager
    def observed_version(candidate):
        opens.append(candidate)
        with version(candidate) as current:
            yield current
    def observed_hash(candidate, *args):
        reads.append(candidate)
        return fingerprint(candidate, *args)
    monkeypatch.setattr(ingest, 'pinned_version', observed_version)
    monkeypatch.setattr(ingest, '_fingerprint_file', observed_hash)
    index = ingest.DedupBatch.prepare(db, archive, {record['file_size']})
    try:
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'absent', record['file_size'], dedup_batch=index) is None
            assert opens == reads == [path]
            # Only the actual read under this pin may seed the process cache.
            cached = getattr(db, '_ingest_fingerprints', {})
            assert list(cached) == ([path] if durable_versions(tmp_path) else [])
            assert ingest._find_duplicate(db, archive, 'another-negative', record['file_size'], dedup_batch=index) is None
            assert opens == reads == [path]  # The continuously held proof is reused.
    finally:
        index.close()
    before = path.stat()
    changed = bytearray(path.read_bytes())
    changed[2888] ^= 1
    path.write_bytes(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    digest = hashlib.sha256(changed).hexdigest()
    index = ingest.DedupBatch.prepare(db, archive, {len(changed)})
    try:
        with db.archive_lock.for_batch(index):
            found = ingest._find_duplicate(db, archive, digest, len(changed), dedup_batch=index)
            assert found['id'] == record['id'] and opens == reads == [path, path]
            again = ingest._find_duplicate(db, archive, digest, len(changed), dedup_batch=index)
            assert again['id'] == record['id'] and reads == [path, path, path]
    finally:
        index.close()


def test_negative_candidates_use_safe_versions_and_edits_invalidate(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    original = ingest._fingerprint_file
    reads = []
    def measured(path, *args):
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    results = [upload(db, archive, exposure(index), name=f'{index}.fit') for index in range(12)]
    assert all(result['ok'] for result in results)
    # Without a durable version (no change journal), Windows rehashes between
    # unpinned requests; timestamps alone can collide.
    assert len(reads) == (11 if durable_versions(tmp_path) or os.name != 'nt' else 66)
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
        if executable.stem == 'fpack':
            assert not db.archive_lock._is_owned()
            with guard:
                calls.append(executable.stem)
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


def test_bounded_negative_cache_and_conservative_reads_above_capacity(tmp_path, monkeypatch):
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
    # Without a durable version (e.g. no change journal on a CI volume), handle-
    # lifetime versions cannot be reused, so nothing is cached at all.
    durable = durable_versions(tmp_path)
    assert len(db._ingest_fingerprints) == (3 if durable else 0)
    assert len(reads) == (13 if durable or os.name != 'nt' else 28)


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
    fingerprint = ingest._fingerprint_file
    checks = []
    def measured(path, cancel=None):
        if path in previous:
            checks.append(path)
        return fingerprint(path, cancel)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    db._ingest_fingerprints.clear()
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', files=[
            ('files', (f'new-{index}.fit', exposure(index + 100))) for index in range(24)]).json()['results']
        assert all(item['ok'] for item in result)
        assert len(checks) == 64  # not 64 * 24 per-file metadata inspections
        # A later request reuses those actual reads while change-journal
        # versions are unchanged, instead of rereading the same-size archive.
        result = client.post('/api/images/ingest', files=[
            ('files', (f'later-{index}.fit', exposure(index + 200))) for index in range(4)]).json()['results']
        assert all(item['ok'] for item in result)
        assert len(checks) == (64 if durable_versions(tmp_path) else 128)
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
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'absent', path.stat().st_size,
                                         dedup_batch=index) is None
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


@pytest.mark.parametrize('bundle,suffix', [('windows-x64', '.exe'), ('linux-x64', '')])
def test_native_tool_cache_revalidates_changed_executable_with_restored_mtime(tmp_path, monkeypatch, bundle, suffix):
    root = tmp_path / 'tools'
    destination = root / 'vendor/fits-tools' / bundle
    destination.mkdir(parents=True)
    import json
    provenance = {'binaries': {}}
    for name in (f'fpack{suffix}', f'funpack{suffix}'):
        data = name.encode('ascii')
        (destination / name).write_bytes(data)
        provenance['binaries'][name] = dict(sha256=hashlib.sha256(data).hexdigest())
    (destination / 'provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
    monkeypatch.setattr(compression, 'resource_dir', lambda: root)
    monkeypatch.setattr(compression, 'tool_bundle', lambda: (bundle, suffix))
    assert compression.tools()['fpack'] == destination / f'fpack{suffix}'
    executable = destination / f'fpack{suffix}'
    before = executable.stat()
    executable.write_bytes(b'X' * before.st_size)
    os.utime(executable, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match='integrity check'):
        compression.tools()
