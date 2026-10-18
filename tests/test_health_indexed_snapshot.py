"""Bounded health snapshots preserve consistency and complete acknowledgements."""
from contextlib import closing
import json
import os
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit
import sqlite3
import threading

import pytest

from app import archive_health, discovery_spool
from app.database import Database, _readonly_sqlite_uri


@pytest.fixture
def library(tmp_path, monkeypatch):
    spools = tmp_path / 'spools'
    spools.mkdir()
    monkeypatch.setattr(discovery_spool, 'logs_dir', lambda: spools)
    root = tmp_path / 'archive'
    root.mkdir()
    db = Database(tmp_path / 'catalog.sqlite')
    db.set_setting('image_dir', str(root))
    return db, root, spools


def populate(db, count=3):
    db.conn.executemany('INSERT INTO images(file,orig_name,object_name,ingested_at) VALUES(?,?,?,?)',
        ((f'{i}.fit', f'{i}.fit', 'M31', 'today') for i in range(count)))
    db.conn.execute("INSERT INTO image_integrity SELECT id,?,1,1,'import','today' FROM images", ('a'*64,))
    db.conn.commit()


def clean(db, spools):
    assert not list(spools.iterdir())
    assert db.archive_lock.acquire(blocking=False)
    db.archive_lock.release()
    # A leaked read snapshot would prevent this rollback-journal commit.
    with closing(sqlite3.connect(db.path, timeout=.1)) as writer:
        writer.execute("INSERT OR REPLACE INTO settings VALUES('snapshot-probe','true')")
        writer.commit()


def test_native_rows_duplicate_invalid_paths_and_private_ack_survive_spool_removal(library):
    db, root, spools = library
    populate(db)
    db.conn.execute('ALTER TABLE images ADD COLUMN "legacy""blob" BLOB')
    db.conn.execute('UPDATE images SET "legacy""blob"=?', (b'\x00\xfflegacy',))
    db.conn.execute("UPDATE images SET file='0.fit' WHERE id=2")
    db.conn.execute("UPDATE images SET file='../unsafe.fit' WHERE id=3")
    db.conn.commit()
    archive, expected = db.integrity_snapshot()
    with discovery_spool.DiscoverySpool() as spool:
        with db.integrity_scan_rows(lambda: None) as (actual_archive, rows):
            spool.copy_records(rows, lambda: None)
        assert actual_archive == archive
        assert spool.indexed_count == 3 and spool.baseline_sources == {'import': 3}
        with closing(spool.records()) as records:
            assert list(records) == expected
        assert spool.contains('0.fit') and not spool.contains('../unsafe.fit')
        assert not spool.contains('untracked.fit')
    manager = archive_health.ArchiveHealthManager(db)
    manager.start('quick')
    manager.thread.join(timeout=10)
    assert not manager.thread.is_alive()
    report = manager.snapshot(full=True)
    assert report['status'] == 'completed' and report['processed'] == report['total'] == 3
    assert report['finding_counts'] == {'missing': 2, 'unsafe_path': 1}
    assert all('_record' not in item for item in report['findings'])
    assert [item['_record'] for item in manager.job['findings'][:2]] == expected[:2]
    assert not list(spools.iterdir())
    # Extra legacy metadata is part of the stale-record guard, even after cleanup.
    db.conn.execute('UPDATE images SET "legacy""blob"=? WHERE id=1', (b'edited',))
    db.conn.commit()
    with pytest.raises(ValueError, match='record changed'):
        manager.acknowledge(report['id'], 1, 'forget_missing')
    db.conn.execute('UPDATE images SET "legacy""blob"=? WHERE id=1', (b'\x00\xfflegacy',))
    db.conn.commit()
    manager.acknowledge(report['id'], 1, 'forget_missing')
    assert [row['id'] for row in db.images()] == [2, 3]
    clean(db, spools)


def test_source_snapshot_freezes_archive_and_later_rows_across_external_commits(library, monkeypatch):
    db, root, spools = library
    populate(db)
    db.conn.execute('PRAGMA journal_mode=WAL').close()
    archive, expected = db.integrity_snapshot()
    original = discovery_spool.DiscoverySpool.execute
    changed = False
    def execute(self, sql, parameters=()):
        nonlocal changed
        result = original(self, sql, parameters)
        if not changed and sql.startswith('INSERT INTO indexed_records'):
            changed = True
            with closing(sqlite3.connect(db.path, timeout=.1)) as writer:
                writer.execute("UPDATE settings SET value=? WHERE key='image_dir'", (json.dumps(str(root/'elsewhere')),))
                writer.execute("UPDATE images SET file='changed.fit',object_name='M42' WHERE id=3")
                writer.execute("UPDATE image_integrity SET sha256=?,source='external' WHERE image_id=3", ('b'*64,))
                writer.execute("INSERT INTO images(file,orig_name,object_name,ingested_at) VALUES('later.fit','later','M42','later')")
                writer.commit()
        return result
    monkeypatch.setattr(discovery_spool.DiscoverySpool, 'execute', execute)
    report = archive_health.scan_archive(db, 'quick', threading.Event(), lambda _: None)
    assert changed and report['status'] == 'completed'
    assert report['archive'] == archive and report['total'] == report['processed'] == 3
    assert report['baseline_sources'] == {'import': 3}
    assert [item['_record'] for item in report['findings']] == expected
    clean(db, spools)


def test_source_and_shared_database_locks_are_released_before_filesystem_checks(library, monkeypatch):
    db, root, spools = library
    populate(db)
    execute = discovery_spool.DiscoverySpool.execute
    read_ran = threading.Event()
    threads = []
    def observed(self, sql, parameters=()):
        if sql.startswith('INSERT INTO indexed_records') and not threads:
            # Would time out if copying held db.lock, including an RLock owned
            # by this worker. Read in another thread, never recursively.
            thread = threading.Thread(target=lambda: (db.images(), read_ran.set()))
            threads.append(thread)
            thread.start()
            assert read_ran.wait(2)
        return execute(self, sql, parameters)
    monkeypatch.setattr(discovery_spool.DiscoverySpool, 'execute', observed)
    entries = archive_health._directory_entries
    filesystem_seen = False
    def probe(*args):
        nonlocal filesystem_seen
        filesystem_seen = True
        with closing(sqlite3.connect(db.path, timeout=.1)) as writer:
            writer.execute("INSERT OR REPLACE INTO settings VALUES('during-filesystem','true')")
            writer.commit()
        yield from entries(*args)
    monkeypatch.setattr(archive_health, '_directory_entries', probe)
    report = archive_health.scan_archive(db, 'quick', threading.Event(), lambda _: None)
    for thread in threads:
        thread.join(timeout=2)
    assert filesystem_seen and read_ran.is_set() and report['status'] == 'completed'
    clean(db, spools)


def test_cancellation_during_copy_removes_partial_snapshot(library, monkeypatch):
    db, root, spools = library
    populate(db, 30)
    before = tuple(db.conn.iterdump())
    cancel = threading.Event()
    execute = discovery_spool.DiscoverySpool.execute
    inserted = 0
    def observed(self, sql, parameters=()):
        nonlocal inserted
        result = execute(self, sql, parameters)
        if sql.startswith('INSERT INTO indexed_records'):
            inserted += 1
            if inserted == 10:
                cancel.set()
        return result
    monkeypatch.setattr(discovery_spool.DiscoverySpool, 'execute', observed)
    report = archive_health.scan_archive(db, 'quick', cancel, lambda _: None)
    assert report['status'] == 'cancelled' and inserted == 10 and report['processed'] == 0
    assert tuple(db.conn.iterdump()) == before
    clean(db, spools)


def test_membership_normalizes_paths_with_platform_case_rules_and_preserves_records(library):
    db, root, spools = library
    paths = ['Dir\\A.fit', 'Dir/A.fit', '../bad.fit', '/absolute.fit', 'é.fit']
    db.conn.executemany('INSERT INTO images(file,orig_name,object_name,ingested_at) VALUES(?,?,?,?)',
        ((path, 'raw', 'M31', 'today') for path in paths))
    db.conn.commit()
    with discovery_spool.DiscoverySpool() as spool:
        with db.integrity_scan_rows(lambda: None) as (_, rows):
            spool.copy_records(rows, lambda: None)
        assert spool.contains('Dir/A.fit') and spool.contains('é.fit')
        assert spool.contains('dir/a.fit') == (os.name == 'nt')
        assert not spool.contains('../bad.fit') and not spool.contains('/absolute.fit')
        with closing(spool.records()) as records:
            assert [row['file'] for row in records] == paths
        assert spool.baseline_sources == {'none': len(paths)}
    clean(db, spools)


def test_relative_database_path_keeps_opened_location_after_cwd_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = Database(Path('relative.sqlite'))
    populate(db)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with db.integrity_scan_rows(lambda: None) as (archive, rows):
        assert archive == db.image_dir()
        assert [row['id'] for row in rows] == [1, 2, 3]
    assert not (elsewhere / 'relative.sqlite').exists()


@pytest.mark.skipif(os.name == 'nt', reason='Windows refuses renaming an open SQLite catalog')
@pytest.mark.parametrize('during_copy', [False, True])
def test_replacing_open_catalog_is_refused_before_archive_inspection(library, monkeypatch, during_copy):
    db, root, spools = library
    populate(db)
    expected = db.integrity_snapshot()
    replaced = False
    def replace():
        nonlocal replaced
        db.path.rename(db.path.with_suffix('.opened.sqlite'))
        replacement = Database(db.path)
        replacement.set_setting('image_dir', str(root / 'replacement'))
        populate(replacement, 1)
        replacement.conn.close()
        replaced = True
    if during_copy:
        execute = discovery_spool.DiscoverySpool.execute
        def observed(self, sql, parameters=()):
            result = execute(self, sql, parameters)
            if not replaced and sql.startswith('INSERT INTO indexed_records'):
                replace()
            return result
        monkeypatch.setattr(discovery_spool.DiscoverySpool, 'execute', observed)
    else:
        replace()
    def unexpected(*args):
        pytest.fail('Wrong-catalog snapshot reached archive inspection')
    monkeypatch.setattr(archive_health, '_directory_entries', unexpected)
    with pytest.raises(RuntimeError, match='catalog file changed'):
        archive_health.scan_archive(db, 'quick', threading.Event(), lambda _: None)
    assert replaced and db.integrity_snapshot() == expected
    assert not list(spools.iterdir())
    assert db.archive_lock.acquire(blocking=False)
    db.archive_lock.release()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlink database alias')
def test_changed_symlink_alias_keeps_original_opened_database(tmp_path):
    original = Database(tmp_path / 'original.sqlite')
    populate(original)
    replacement = Database(tmp_path / 'replacement.sqlite')
    populate(replacement, 1)
    alias = tmp_path / 'alias.sqlite'
    alias.symlink_to(original.path)
    opened = Database(alias)
    expected = opened.integrity_snapshot()
    alias.unlink()
    alias.symlink_to(replacement.path)
    with opened.integrity_scan_rows(lambda: None) as (archive, rows):
        assert (archive, [dict(row) for row in rows]) == expected


def test_in_memory_list_api_remains_available_but_health_snapshot_refuses(tmp_path):
    db = Database(Path(':memory:'))
    populate(db)
    assert len(db.integrity_snapshot()[1]) == 3
    with pytest.raises(RuntimeError, match='file-backed'):
        with db.integrity_scan_rows(lambda: None):
            pytest.fail('An in-memory database has no independent file snapshot')


@pytest.mark.parametrize('path', [r'C:\catalog\plain.sqlite', r'\\server\share\catalog.sqlite',
    r'\\?\C:\long\catalog.sqlite', r'\\?\UNC\server\share\catalog.sqlite',
    'C:\\catalog\\% ? # é.sqlite'])
def test_native_windows_filenames_roundtrip_without_sqlite_uri_authorities(path):
    path = PureWindowsPath(path)
    parts = urlsplit(_readonly_sqlite_uri(path))
    assert parts.scheme == 'file' and not parts.netloc and parts.query == 'mode=ro'
    assert unquote(parts.path) == str(path)


def test_native_snapshot_opens_literal_uri_characters_and_unicode(tmp_path):
    db = Database(tmp_path / 'literal % # ? é.sqlite' if os.name != 'nt' else tmp_path / 'literal % # é.sqlite')
    populate(db)
    with db.integrity_scan_rows(lambda: None) as (_, rows):
        assert [row['id'] for row in rows] == [1, 2, 3]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows extended long-path database')
def test_native_windows_extended_database_path(tmp_path):
    parent = tmp_path
    for i in range(5):
        parent /= f'long-{i}-' + 'x'*45
    path = Path('\\\\?\\' + str(parent / 'catalog.sqlite'))
    path.parent.mkdir(parents=True)
    db = Database(path)
    populate(db)
    with db.integrity_scan_rows(lambda: None) as (_, rows):
        assert [row['id'] for row in rows] == [1, 2, 3]


@pytest.mark.parametrize('value', [None, 'broken JSON', 'false', '[]', '123', '"relative/archive"'])
def test_snapshot_archive_setting_matches_existing_fallback_rules(library, value):
    db, root, spools = library
    if value is None:
        db.conn.execute("DELETE FROM settings WHERE key='image_dir'")
    else:
        db.conn.execute("UPDATE settings SET value=? WHERE key='image_dir'", (value,))
    db.conn.commit()
    with db.integrity_scan_rows(lambda: None) as (archive, rows):
        assert archive == db.image_dir()
        assert list(rows) == []


def test_sqlite_vm_interruption_preserves_cancellation_and_readonly_source(library):
    db, root, spools = library
    populate(db, 500)
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls > 12:
            raise archive_health.ScanCancelled
    with pytest.raises(archive_health.ScanCancelled):
        with db.integrity_scan_rows(checkpoint) as (_, rows):
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                rows.connection.execute("DELETE FROM images")
            # No Python checkpoint in this consumption loop: interruption must
            # originate in the SQLite progress handler, then regain its type.
            for row in rows:
                pass
    clean(db, spools)


def test_wait_for_external_exclusive_writer_is_cancellable(library, monkeypatch):
    db, root, spools = library
    populate(db)
    attempted = threading.Event()
    connect = sqlite3.connect
    def observed(*args, **kwargs):
        if kwargs.get('uri'):
            attempted.set()
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', observed)
    with closing(connect(db.path, timeout=.1)) as writer:
        writer.execute('BEGIN EXCLUSIVE')
        manager = archive_health.ArchiveHealthManager(db)
        manager.start('quick')
        assert attempted.wait(2)
        manager.cancel_event.set()
        manager.thread.join(timeout=2)
        writer.rollback()
    assert not manager.thread.is_alive() and manager.snapshot()['status'] == 'cancelled'
    clean(db, spools)


@pytest.mark.parametrize('stage', ['records', 'known', 'iteration'])
def test_snapshot_storage_failures_are_visible_and_leave_source_unchanged(library, monkeypatch, stage):
    db, root, spools = library
    populate(db)
    before = tuple(db.conn.iterdump())
    execute = discovery_spool.DiscoverySpool.execute
    triggered = False
    def observed(self, sql, parameters=()):
        nonlocal triggered
        match = {'records': 'INSERT INTO indexed_records', 'known': 'INSERT OR IGNORE INTO known',
                 'iteration': 'SELECT * FROM indexed_records'}[stage]
        if sql.startswith(match):
            triggered = True
            raise discovery_spool.DiscoveryStorageError('injected snapshot storage failure')
        return execute(self, sql, parameters)
    monkeypatch.setattr(discovery_spool.DiscoverySpool, 'execute', observed)
    manager = archive_health.ArchiveHealthManager(db)
    manager.start('quick')
    manager.thread.join(timeout=5)
    assert triggered and not manager.thread.is_alive() and manager.snapshot()['status'] == 'failed'
    assert tuple(db.conn.iterdump()) == before
    clean(db, spools)
