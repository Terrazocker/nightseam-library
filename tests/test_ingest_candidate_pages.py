"""Large-catalog searches stay bounded without truncating actual-byte lookup."""
import hashlib
import os
import threading

import pytest

from app import ingest
from test_compression import exposure, library
from test_ingest_compression import upload


def test_preparation_does_not_query_or_hash_same_size_candidates(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=7)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 2)
    visited, statements = [], []
    monkeypatch.setattr(ingest.DedupBatch, 'fingerprint',
                        lambda self, db, row, *args, **kwargs: visited.append(row['id']))
    db.conn.set_trace_callback(statements.append)
    index = None
    try:
        index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
        assert not visited and not index.entries and not index.leased
        reads = [sql for sql in statements if sql.startswith('SELECT id,file')]
        assert not reads
    finally:
        db.conn.set_trace_callback(None)
        if index is not None:
            index.close()
        db.conn.close()


def test_paged_lookup_finds_late_restored_mtime_duplicate_above_pin_cap(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=7)
    monkeypatch.setattr(ingest, '_CANDIDATE_PAGE_SIZE', 2)
    monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 1)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)
    rows = db.images()
    incoming = exposure(10)
    target = archive / rows[-1]['file']
    before = target.stat()
    target.write_bytes(incoming)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    # Earlier excluded/missing candidates must not break page advancement.
    (archive / rows[1]['file']).unlink()
    statements, reads = [], []
    original = ingest._fingerprint_file
    def measured(path, *args):
        assert not db.lock._is_owned()
        reads.append(path)
        return original(path, *args)
    monkeypatch.setattr(ingest, '_fingerprint_file', measured)
    index = ingest.DedupBatch.prepare(db, archive, {len(incoming)})
    db.conn.set_trace_callback(statements.append)
    try:
        with db.archive_lock.for_batch(index):
            result = ingest._find_duplicate(db, archive, hashlib.sha256(incoming).hexdigest(),
                len(incoming), exclude_id=rows[0]['id'], dedup_batch=index)
        assert result['id'] == rows[-1]['id']
        assert result['imagetyp'] == rows[-1]['imagetyp']
        assert target in reads and len(index.entries) == 1
        pages = [sql for sql in statements if sql.startswith('SELECT id,file')]
        assert len(pages) == 5 and all(sql.endswith('LIMIT 2') for sql in pages)
    finally:
        db.conn.set_trace_callback(None)
        index.close()
        db.conn.close()


def test_pages_release_db_lock_and_exclude_insertions_after_search_start(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=4)
    monkeypatch.setattr(ingest, '_CANDIDATE_PAGE_SIZE', 2)
    expected = [row['id'] for row in db.images()]
    try:
        candidates = ingest._batch_candidates(db, 'absent', len(exposure()))
        first = next(candidates)
        assert not db.lock._is_owned()
        with db.lock:
            db.conn.execute("INSERT INTO images(file,orig_name,object_name,file_size,ingested_at) "
                            "VALUES ('late.fit','late.fit','Late object',?,'2026-10-17')", (len(exposure()),))
            db.conn.commit()
        assert [first['id'], *[row['id'] for row in candidates]] == expected
    finally:
        db.conn.close()


def test_cancelled_candidate_pages_stop_before_next_query(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=4)
    monkeypatch.setattr(ingest, '_CANDIDATE_PAGE_SIZE', 2)
    cancel, statements = threading.Event(), []
    candidates = ingest._batch_candidates(db, 'absent', len(exposure()), cancel)
    db.conn.set_trace_callback(statements.append)
    try:
        next(candidates)
        next(candidates)
        cancel.set()
        with pytest.raises(ingest.ReindexCancelled):
            next(candidates)
        assert len([sql for sql in statements if sql.startswith('SELECT id,file')]) == 1
        assert not db.lock._is_owned()
    finally:
        db.conn.set_trace_callback(None)
        db.conn.close()


def counted_candidates(index):
    streamed, original = [], index.candidates
    def candidates(*args):
        for record in original(*args):
            streamed.append(record['id'])
            yield record
    index.candidates = candidates
    return streamed


def test_later_lookups_read_only_rows_added_since_the_last(tmp_path, monkeypatch):
    # Camera frames share one size: rereading every earlier frame for each new
    # one made a long import slower with every file.
    db, archive, _ = library(tmp_path, count=6)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 2)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)
    reads, original = [], ingest._fingerprint_file
    monkeypatch.setattr(ingest, '_fingerprint_file', lambda path, *args: reads.append(path) or original(path, *args))
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    streamed = counted_candidates(index)
    try:
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'absent', len(exposure()), dedup_batch=index) is None
        assert len(streamed) == 6 and len(index.entries) == 2 and len(index.remembered) == 4
        checked = len(reads)
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'other', len(exposure()), dedup_batch=index) is None
        assert len(streamed) == 6 and len(reads) == checked
        added = upload(db, archive, exposure(20), dedup_batch=index)
        assert added['ok'] and len(streamed) == 6 and len(reads) == checked
        streamed.clear()
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'third', len(exposure()), dedup_batch=index) is None
        assert streamed == [added['id']]
    finally:
        index.close()
        db.conn.close()


def test_settled_rows_still_find_remembered_and_unresolved_duplicates(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=5)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_BATCH_PIN_LIMIT', 1)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)
    rows, size = db.images(), len(exposure())
    missing = archive / rows[1]['file']
    missing.unlink()
    index = ingest.DedupBatch.prepare(db, archive, {size})
    streamed = counted_candidates(index)
    try:
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'absent', size, dedup_batch=index) is None
        assert index.unresolved[size] == {rows[1]['id']}
        # A file that could not be checked is looked at again, and found once it returns.
        streamed.clear()
        missing.write_bytes(exposure(10))
        with db.archive_lock.for_batch(index):
            found = ingest._find_duplicate(db, archive, hashlib.sha256(exposure(10)).hexdigest(), size,
                                           dedup_batch=index)
        assert found['id'] == rows[1]['id'] and streamed == [rows[1]['id']]
        # A remembered file whose checked bytes match is rehashed and found,
        # even when its stored hash is stale.
        last = rows[-1]
        assert last['id'] in index.remembered
        with db.lock:
            db.conn.execute('UPDATE images SET content_sha256=? WHERE id=?', ('0' * 64, last['id']))
            db.conn.commit()
        streamed.clear()
        with db.archive_lock.for_batch(index):
            found = ingest._find_duplicate(db, archive, index.remembered[last['id']][1], size,
                                           dedup_batch=index)
        assert found['id'] == last['id'] and last['id'] in streamed
    finally:
        index.close()
        db.conn.close()


def test_unpinned_batches_lease_and_remember_from_the_first_lookup(tmp_path, monkeypatch):
    # Linux has no pins; its imports slowed down from the first file.
    db, archive, _ = library(tmp_path, count=4)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', False)
    index = ingest.DedupBatch.prepare(db, archive, {len(exposure())})
    streamed = counted_candidates(index)
    try:
        assert not index.leased and index.root is None
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'absent', len(exposure()), dedup_batch=index) is None
        assert index.leased and not index.entries and len(index.remembered) == 4 and len(streamed) == 4
        with db.archive_lock.for_batch(index):
            assert ingest._find_duplicate(db, archive, 'other', len(exposure()), dedup_batch=index) is None
        assert len(streamed) == 4
    finally:
        index.close()
    assert db.archive_lock.acquire(blocking=False)
    db.archive_lock.release()
    db.conn.close()
