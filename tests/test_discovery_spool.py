"""Discovery preserves paths/order/counts while bounding queues and cleanup."""
from contextlib import closing
import sqlite3
import threading
import tracemalloc

import pytest

from app import archive_health, discovery_spool
from app.database import Database


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'spools'
    root.mkdir()
    monkeypatch.setattr(discovery_spool, 'logs_dir', lambda: root)
    return root


def unlocked(db):
    assert db.archive_lock.acquire(blocking=False), 'Discovery retained the archive lock'
    db.archive_lock.release()


def test_spool_preserves_raw_unicode_duplicates_and_lifo(workspace):
    paths = ['z.fit', 'a.fit', 'é.fit', '\ud800.fit', '\ue000.fit', '😀.fit',
             'a.fit', '../unsafe.fit', 'a\\b.fit', ' padded.fit ', 'bad\x00.fit']
    with discovery_spool.DiscoverySpool() as spool:
        for path in paths:
            spool.add_untracked(path)
            spool.push_directory(path)
        assert spool.untracked_count == spool.pending_count == len(paths)
        assert [spool.pop_directory() for _ in paths] == list(reversed(paths))
        assert spool.pending_count == 0
        with closing(spool.untracked()) as ordered:
            assert list(ordered) == sorted(paths)
        plan = spool.execute('EXPLAIN QUERY PLAN SELECT path FROM untracked ORDER BY path').fetchall()
        assert not any('TEMP B-TREE' in row[-1] for row in plan)
        assert any('untracked_order' in row[-1] for row in plan)
    assert not list(workspace.iterdir())


def test_spool_python_memory_does_not_retain_wide_directory_or_file_lists(workspace):
    with discovery_spool.DiscoverySpool() as spool:
        tracemalloc.start()
        try:
            for i in range(20000):
                path = f'{i:08d}-' + 'x' * 180 + '.fits'
                spool.push_directory(path)
                spool.add_untracked(path)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert spool.pending_count == spool.untracked_count == 20000
        assert peak < 2 * 1024 * 1024, f'Discovery retained pathname collections ({peak} bytes)'
    assert not list(workspace.iterdir())


def test_scan_counts_all_files_and_retains_sorted_prefix(workspace, tmp_path, monkeypatch):
    root = tmp_path / 'archive'
    root.mkdir()
    names = ['z.fit', 'é.fit', 'a.fit', 'middle.fz', '😀.fits']
    for name in names:
        (root / name).write_bytes(b'unchanged')
    db = Database(tmp_path / 'health.db')
    db.set_setting('image_dir', str(root))
    before = tuple(db.conn.iterdump())
    monkeypatch.setattr(archive_health, 'MAX_FINDINGS', 2)
    report = archive_health.scan_archive(db, 'quick', threading.Event(), lambda _: None)
    assert report['status'] == 'completed'
    assert report['total'] == report['processed'] == report['unverified'] == len(names)
    assert report['finding_counts'] == {'untracked': len(names)}
    assert report['omitted_findings'] == len(names) - 2
    assert [item['path'] for item in report['findings']] == sorted(names)[:2]
    assert tuple(db.conn.iterdump()) == before
    assert all((root / name).read_bytes() == b'unchanged' for name in names)
    assert not list(workspace.iterdir())
    unlocked(db)


@pytest.mark.parametrize('phase', ['discovering', 'checking'])
def test_cancellation_closes_live_ordered_cursor_and_spool(workspace, tmp_path, monkeypatch, phase):
    root = tmp_path / 'archive'
    root.mkdir()
    for i in range(100):
        (root / f'{i}.fit').write_bytes(b'x')
        (root / f'dir-{i}').mkdir()
    db = Database(tmp_path / 'health.db')
    db.set_setting('image_dir', str(root))
    cancel = threading.Event()
    if phase == 'discovering':
        original = discovery_spool.DiscoverySpool.add_untracked
        def observed(self, path):
            original(self, path)
            if self.untracked_count == 50:
                cancel.set()
        monkeypatch.setattr(discovery_spool.DiscoverySpool, 'add_untracked', observed)
    else:
        original = discovery_spool.DiscoverySpool.untracked
        def observed(self):
            with closing(original(self)) as paths:
                for path in paths:
                    cancel.set()
                    yield path
        monkeypatch.setattr(discovery_spool.DiscoverySpool, 'untracked', observed)
    report = archive_health.scan_archive(db, 'quick', cancel, lambda _: None)
    assert report['status'] == 'cancelled'
    assert not list(workspace.iterdir())
    unlocked(db)


@pytest.mark.parametrize('failure', ['connect', 'schema', 'insert', 'iterate', 'close', 'cleanup'])
def test_storage_failures_fail_job_and_release_every_owned_resource(workspace, tmp_path, monkeypatch, failure):
    root = tmp_path / 'archive'
    root.mkdir()
    exposure = root / 'untracked.fit'
    exposure.write_bytes(b'preserve this exposure')
    db = Database(tmp_path / 'health.db')
    db.set_setting('image_dir', str(root))
    before = tuple(db.conn.iterdump())
    connect = sqlite3.connect
    class Connection:
        def __init__(self, *args, **kwargs):
            self.actual = connect(*args, **kwargs)
        def execute(self, sql, parameters=()):
            if (failure == 'schema' and sql.startswith('CREATE')) or (
                    failure == 'insert' and sql.startswith('INSERT INTO untracked')):
                raise sqlite3.OperationalError('injected database or disk is full')
            result = self.actual.execute(sql, parameters)
            if failure == 'iterate' and sql == 'SELECT path FROM untracked ORDER BY path':
                class Cursor:
                    def __iter__(self): return self
                    def __next__(self): raise sqlite3.OperationalError('injected scratch read failure')
                    def close(self): result.close()
                return Cursor()
            return result
        def close(self):
            self.actual.close()
            if failure == 'close':
                raise sqlite3.OperationalError('injected close refusal')
    if failure == 'connect':
        def refused(*args, **kwargs):
            raise sqlite3.OperationalError('injected scratch connect refusal')
        monkeypatch.setattr(discovery_spool.sqlite3, 'connect', refused)
    else:
        monkeypatch.setattr(discovery_spool.sqlite3, 'connect', Connection)
    if failure == 'cleanup':
        cleanup = discovery_spool.tempfile.TemporaryDirectory.cleanup
        def refused(self):
            cleanup(self)
            raise OSError('injected scratch cleanup refusal')
        monkeypatch.setattr(discovery_spool.tempfile.TemporaryDirectory, 'cleanup', refused)
    manager = archive_health.ArchiveHealthManager(db)
    manager.start('quick')
    manager.thread.join(timeout=10)
    assert not manager.thread.is_alive()
    assert manager.snapshot()['status'] == 'failed'
    assert not list(workspace.iterdir())
    assert exposure.read_bytes() == b'preserve this exposure'
    assert tuple(db.conn.iterdump()) == before
    unlocked(db)
