"""Mixed representations, overflow lookup cost and cancellable positive reads."""
import asyncio
import hashlib
import io
import threading

import pytest
from starlette.datastructures import UploadFile

from app import compression, ingest
from scripts import stress_dedup as stress
from test_compression import exposure, library, no_stages
from test_ingest_compression import upload


@pytest.mark.parametrize('mode', ['preflight_alias', 'preflight_exact', 'final_alias', 'final_plain'])
def test_duplicate_hash_cancellation_reaches_worker_before_stage_cleanup(tmp_path, monkeypatch, mode):
    db, archive, _ = library(tmp_path, count=0)
    raw = exposure()
    first = upload(db, archive, raw, compress='alias' in mode)
    assert first['ok']
    target = archive / first['stored']
    saved = target.read_bytes()
    before = db.images()
    mappings = db.conn.execute('SELECT * FROM image_compression').fetchall()
    index = ingest.DedupBatch.prepare(db, archive, {len(raw)})
    entered, release = threading.Event(), threading.Event()
    cooperative, encoded = [], []
    original = ingest._fingerprint_file
    def paused(path, cancel=None):
        assert not db.lock._is_owned()
        if path != target:
            return original(path, cancel)
        entered.set()
        if not release.wait(5):
            raise RuntimeError('Cancellation test did not release its worker')
        cooperative.append(cancel is not None and cancel.is_set())
        ingest._check_reindex_cancelled(cancel)
        return original(path, cancel)
    def forbidden_encode(*args, **kwargs):
        encoded.append(True)
        raise AssertionError('A cancelled duplicate must not reach an encoder')
    monkeypatch.setattr(ingest, '_fingerprint_file', paused)
    monkeypatch.setattr(compression, 'native', forbidden_encode)
    async def scenario():
        task = asyncio.create_task(ingest.ingest_one(db, [], archive,
            UploadFile(io.BytesIO(raw), filename='cancel.fit'), compress=mode.startswith('preflight'),
            dedup_batch=index))
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
            if not task.cancelling():
                task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        asyncio.run(scenario())
        assert cooperative == [True] and not encoded
        assert db.images() == before
        assert db.conn.execute('SELECT * FROM image_compression').fetchall() == mappings
        assert target.read_bytes() == saved and not list(archive.glob('*.part'))
        no_stages(db, archive)
    finally:
        index.close()
        assert stress.available(db)
        db.conn.close()
    target.write_bytes(saved)  # Native writer pin has been released.


def test_late_positive_above_capacity_hydrates_only_verified_record(tmp_path, monkeypatch):
    db = stress.Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    rows = stress.seed(db, archive, 41, 7)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)
    monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 2)
    monkeypatch.setattr(ingest, '_CANDIDATE_PAGE_SIZE', 3)
    index = ingest.DedupBatch.prepare(db, archive, {rows[0]['file_size']})
    full_rows, original = [], db.image
    def hydrated(number):
        full_rows.append(number)
        return original(number)
    monkeypatch.setattr(db, 'image', hydrated)
    try:
        with db.archive_lock.for_batch(index):
            found = ingest._find_duplicate(db, archive, rows[-1]['content_sha256'],
                rows[-1]['file_size'], dedup_batch=index)
        assert found['id'] == rows[-1]['id'] and found['object_name'] == rows[-1]['object_name']
        assert full_rows == [rows[-1]['id']] and not index.entries
    finally:
        index.close()
        db.conn.close()


def test_successful_alias_does_not_query_exact_positive_tier(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    raw = exposure()
    first = upload(db, archive, raw, compress=True)
    def unnecessary_exact(*args, **kwargs):
        raise AssertionError('Successful alias should not fetch exact candidates')
    monkeypatch.setattr(ingest, '_candidate_rows', unnecessary_exact)
    try:
        result = ingest._known_duplicate(db, archive, hashlib.sha256(raw).hexdigest(),
                                        len(raw), False, None, 'light')
        assert result['id'] == first['id']
    finally:
        db.conn.close()


def test_failed_alias_can_recheck_same_record_as_exact_positive(tmp_path, monkeypatch):
    db, archive, originals = library(tmp_path, count=1)
    number, raw = next(iter(originals.items()))
    digest = hashlib.sha256(raw).hexdigest()
    with db.lock:
        db.conn.execute('INSERT INTO image_compression VALUES(?,?,?,?,?,?,?)',
                        (number, digest, len(raw), 'legacy.fit', 'f' * 64, len(raw), 'a' * 64))
        db.conn.commit()
    original, reads = ingest._fingerprint_file, []
    def measured(path, *args):
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    try:
        found = ingest._known_duplicate(db, archive, digest, len(raw), False, None, 'dark')
        assert found['id'] == number and found['imagetyp'] == 'light'
        assert len(reads) == 2  # Alias/current identity and exact identity differ.
    finally:
        db.conn.close()


@pytest.mark.parametrize('preflight', [False, True])
def test_hydration_never_returns_a_different_unverified_path(tmp_path, monkeypatch, preflight):
    db, archive, originals = library(tmp_path, count=1)
    number, raw = next(iter(originals.items()))
    original = db.image
    monkeypatch.setattr(db, 'image', lambda key: dict(original(key), file='unverified-other.fits'))
    digest = hashlib.sha256(raw).hexdigest()
    try:
        if preflight:
            found = ingest._known_duplicate(db, archive, digest, len(raw), False, None, 'light')
        else:
            with db.archive_lock:
                found = ingest._find_duplicate(db, archive, digest, len(raw), dedup_batch=ingest.DedupBatch())
        assert found is None
    finally:
        db.conn.close()
