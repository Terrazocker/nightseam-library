"""Large-catalog searches stay bounded without truncating actual-byte lookup."""
import hashlib
import os
import threading

import pytest

from app import ingest
from test_compression import exposure, library


def test_preparation_limits_rows_in_sql_before_failed_admissions(tmp_path, monkeypatch):
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
        assert visited == [row['id'] for row in db.images()[:2]]
        reads = [sql for sql in statements if sql.startswith('SELECT id,file')]
        assert len(reads) == 1 and reads[0].endswith('LIMIT 2')
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
        assert len(pages) == 4 and all(sql.endswith('LIMIT 2') for sql in pages)
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
