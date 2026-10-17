"""Lazy request pins measure committed archive bytes and preserve cleanup.

Exercise the admission algorithm on both platforms with real reads. Windows
contracts additionally prove actual write/rename exclusion; POSIX read handles
are not sufficient for enabling this optimisation in production.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import io
import os
import threading

from fastapi.testclient import TestClient
import pytest
from starlette.datastructures import UploadFile

from app import archive_trash, ingest, server
from app.archive_health import ArchiveUnavailable
from test_compression import exposure, library, no_stages
from test_ingest_compression import upload


@pytest.fixture(autouse=True)
def admission_algorithm(monkeypatch):
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    # Windows cross-open versions already prevent this reuse. Disable the POSIX
    # process cache here so read-count tests isolate the request-local index.
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)


def available(db):
    acquired = db.archive_lock.acquire(blocking=False)
    if acquired:
        db.archive_lock.release()
    return acquired


@pytest.mark.parametrize('duplicate', [False, True])
def test_http_new_files_are_hashed_once_and_positives_still_rehash(tmp_path, monkeypatch, duplicate):
    db, archive, _ = library(tmp_path, count=0)
    fingerprint, close = ingest._fingerprint_file, ingest.DedupBatch.close
    reads, closed = [], []
    def measured(path, *args):
        assert not db.lock._is_owned()
        reads.append(path)
        return fingerprint(path, *args)
    def closing(index):
        closed.append((len(index.entries), index.leased))
        close(index)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    monkeypatch.setattr(ingest.DedupBatch, 'close', closing)
    count = 23 if duplicate else 24  # The HTTP endpoint accepts at most 24 uploads.
    files = [('files', (f'new-{i}.fit', exposure(i))) for i in range(count)]
    if duplicate:
        files.append(('files', ('repeated.fit', exposure(0))))
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', files=files).json()['results']
    assert all(item['ok'] for item in result)
    assert len(reads) == count - 1 + int(duplicate)  # Previously quadratic scans.
    assert len(db.images()) == count and closed == [(count - 1, True)]
    if duplicate:
        assert result[-1]['skipped'] and result[-1]['id'] == result[0]['id']
        assert reads.count(archive / result[0]['stored']) == 2
    assert available(db)
    for i, item in enumerate(result[:count]):
        assert (archive / item['stored']).read_bytes() == exposure(i)
    assert not list(archive.glob('*.part'))
    no_stages(db, archive)


def test_empty_batch_first_admission_leases_archive_and_releases_pins(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        assert not index.entries and not index.leased and available(db)
        first = upload(db, archive, exposure(0), dedup_batch=index)
        assert not index.entries and not index.leased  # Never pin an uncommitted output.
        assert upload(db, archive, exposure(1), dedup_batch=index)['ok']
        assert index.leased and len(index.entries) == 1
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert not pool.submit(available, db).result(timeout=3)
            assert pool.submit(lambda: upload(db, archive, exposure(2), dedup_batch=index)).result(timeout=10)['ok']
        path = archive / first['stored']
        if os.name == 'nt':
            with pytest.raises(PermissionError):
                path.write_bytes(b'blocked writer')
            with pytest.raises(OSError):
                path.rename(path.with_suffix('.moved'))
            with pytest.raises(OSError):
                path.parent.rename(path.parent.with_name('moved-parent'))
    finally:
        index.close()
    index.close()  # Idempotent ownership cleanup.
    assert available(db)
    path.write_bytes(b'writer allowed after cleanup')
    moved = path.with_suffix('.moved')
    path.rename(moved)
    path.parent.rename(path.parent.with_name('moved-parent'))


def test_lazy_admission_measures_restored_mtime_edits_and_keeps_duplicate_metadata(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        first = upload(db, archive, exposure(0), dedup_batch=index)
        before = db.image(first['id'])
        path = archive / first['stored']
        stat = path.stat()
        path.write_bytes(exposure(1))  # Edit before the first candidate pin.
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert upload(db, archive, exposure(2), dedup_batch=index)['ok']
        assert index.entries[first['id']][1] == hashlib.sha256(exposure(1)).hexdigest()
        repeated = upload(db, archive, exposure(1), frame_override='dark', dedup_batch=index)
        assert repeated['skipped'] and repeated['id'] == first['id']
        after = db.image(first['id'])
        for key in ('file', 'imagetyp', 'date_obs', 'session_key', 'site_lat', 'site_lon'):
            assert after[key] == before[key]
    finally:
        index.close()
    # A new request must freshly observe another same-size/restored-mtime edit.
    path.write_bytes(exposure(3))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', files={'files': ('edited.fit', exposure(3))}).json()['results'][0]
    assert result['skipped'] and result['id'] == first['id']


@pytest.mark.parametrize('reason', ['capacity', 'unsupported', 'parent_error', 'changed_version', 'failed_read'])
def test_failed_or_bounded_admission_falls_back_without_retaining_handles(tmp_path, monkeypatch, reason):
    db, archive, _ = library(tmp_path, count=0)
    real_parent, real_version = archive_trash._parent, ingest.pinned_version
    active = []
    @contextmanager
    def parent(*args):
        if reason == 'parent_error':
            raise OSError('synthetic directory pin refusal')
        with real_parent(*args):
            token = object()
            active.append(token)
            try:
                yield
            finally:
                active.remove(token)
    monkeypatch.setattr(archive_trash, '_parent', parent)
    if reason == 'capacity':
        monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 1)
    elif reason == 'unsupported':
        @contextmanager
        def unsupported(path):
            yield None
        monkeypatch.setattr(ingest, 'pinned_version', unsupported)
    elif reason == 'changed_version':
        @contextmanager
        def changing(path):
            with real_version(path) as original:
                counter = iter(range(100))
                yield lambda: (original(), next(counter))
        monkeypatch.setattr(ingest, 'pinned_version', changing)
    elif reason == 'failed_read':
        monkeypatch.setattr(ingest, '_fingerprint_file', lambda *args: None)
    fingerprint, reads = ingest._fingerprint_file, []
    def measured(path, *args):
        reads.append(path)
        return fingerprint(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        for i in range(4):
            assert upload(db, archive, exposure(i), dedup_batch=index)['ok']
            expected = int(reason == 'capacity' and i > 0)
            assert len(index.entries) == len(index.folders) == len(active) == expected
        assert index.leased == (reason == 'capacity')
        assert len(reads) == (4 if reason == 'capacity' else 6)
    finally:
        index.close()
    assert not active and available(db)
    assert len(db.images()) == 4 and not list(archive.glob('*.part'))


def test_root_replaced_before_first_admission_is_refused_without_leaking_pins(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        first = upload(db, archive, exposure(0), dedup_batch=index)
        archive.rename(archive.with_name('previous-archive'))
        replacement = archive / first['stored']
        replacement.parent.mkdir(parents=True)
        replacement.write_bytes(exposure(1))
        with pytest.raises(ArchiveUnavailable):
            upload(db, archive, exposure(2), dedup_batch=index)
        assert not index.entries and not index.folders and not index.leased
        assert len(db.images()) == 1 and replacement.read_bytes() == exposure(1)
        assert not list(archive.glob('*.part'))
    finally:
        index.close()
    assert available(db)


@pytest.mark.parametrize('kind', ['leaf', 'parent'])
def test_internal_alias_in_previously_pinned_parent_is_never_admitted(tmp_path, kind):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        first = upload(db, archive, exposure(0), dedup_batch=index)
        second = upload(db, archive, exposure(1), dedup_batch=index)
        original = archive / first['stored']
        other = archive / second['stored']
        if kind == 'leaf':
            alias = original.with_name('alias.fit')
            alias.symlink_to(original)
            relative = alias.relative_to(archive).as_posix()
        else:
            alias = archive / 'alias-directory'
            alias.symlink_to(original.parent, target_is_directory=True)
            relative = f'alias-directory/{original.name}'
            other = archive / 'alternate-parent' / original.name
            other.parent.mkdir()
            other.write_bytes(exposure(1))
        record = {'id': 99999, 'file': relative}
        with db.archive_lock.for_batch(index):
            result = index.fingerprint(db, record, ingest.paths.safe_join(archive, relative), 'different')
        assert result[0] == hashlib.sha256(exposure(0)).hexdigest()
        assert record['id'] not in index.entries
        alias.unlink()
        alias.symlink_to(other if kind == 'leaf' else other.parent, target_is_directory=kind == 'parent')
        digest = hashlib.sha256(exposure(1)).hexdigest()
        assert not index.negative(record, digest)
        with db.archive_lock.for_batch(index):
            result = index.fingerprint(db, record, ingest.paths.safe_join(archive, relative), digest)
        assert result[0] == digest and record['id'] not in index.entries
    finally:
        index.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows open-writer sharing fallback')
def test_lazy_admission_with_open_writer_retains_no_pins(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        first = upload(db, archive, exposure(0), dedup_batch=index)
        with (archive / first['stored']).open('r+b'):
            assert upload(db, archive, exposure(1), dedup_batch=index)['ok']
            assert not index.entries and not index.folders and not index.leased
    finally:
        index.close()
    assert available(db)


@pytest.mark.parametrize('mode', ['new', 'capacity', 'unsupported', 'parent_refusal', 'positive'])
def test_cancelled_lazy_hash_drains_before_stage_cleanup_and_never_admits(tmp_path, monkeypatch, mode):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    first = upload(db, archive, exposure(0), dedup_batch=index)
    if mode == 'capacity':
        monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 0)
    elif mode == 'unsupported':
        @contextmanager
        def unsupported(path):
            yield None
        monkeypatch.setattr(ingest, 'pinned_version', unsupported)
    elif mode == 'parent_refusal':
        @contextmanager
        def refusal(*args):
            raise OSError('synthetic pin refusal')
            yield
        monkeypatch.setattr(archive_trash, '_parent', refusal)
    elif mode == 'positive':
        upload(db, archive, exposure(1), dedup_batch=index)
    previous = dict(index.entries), set(index.folders), index.leased, len(db.images())
    entered, release = threading.Event(), threading.Event()
    original = ingest._fingerprint_file
    def paused(path, *args):
        entered.set()
        assert release.wait(10)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', paused)
    async def run():
        task = asyncio.create_task(ingest.ingest_one(db, [], archive,
            UploadFile(io.BytesIO(exposure(0 if mode == 'positive' else 1)), filename='cancel.fit'), dedup_batch=index))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and list(archive.glob('*.part'))
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        asyncio.run(run())
        assert (index.entries, index.folders, index.leased) == previous[:3]
    finally:
        index.close()
    assert len(db.images()) == previous[3] and available(db)
    assert not list(archive.glob('*.part'))
    (archive / first['stored']).write_bytes(exposure(0))


def test_sql_failure_after_lazy_admission_preserves_earlier_commits_and_cleans_up(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    db.conn.execute("CREATE TRIGGER reject_third BEFORE INSERT ON images WHEN NEW.orig_name='2.fit' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    db.conn.commit()
    with TestClient(server.create_app(db)) as client:
        result = client.post('/api/images/ingest', files=[
            ('files', (f'{i}.fit', exposure(i))) for i in range(4)]).json()['results']
    assert all(item['ok'] for item in result[:2]) and result[2]['unconfirmed']
    assert result[3]['unattempted']
    assert len(db.images()) == len(list(archive.rglob('*.fit'))) == 2
    assert db.import_batches()[0]['n_files'] == 2
    assert not list(archive.glob('*.part')) and available(db)
    for item in result[:2]:
        path = archive / item['stored']
        path.write_bytes(path.read_bytes())  # No leaked writer-excluding pin.
    no_stages(db, archive)


def test_close_failure_is_visible_and_still_releases_lazy_lease(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    try:
        upload(db, archive, exposure(0), dedup_batch=index)
        upload(db, archive, exposure(1), dedup_batch=index)
        def refused():
            raise RuntimeError('synthetic cleanup failure')
        index.stack.callback(refused)
        with pytest.raises(RuntimeError, match='synthetic cleanup failure'):
            index.close()
        assert available(db) and not index.entries and not index.folders
    finally:
        index.close()
