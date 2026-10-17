"""Request-local directory sharing, provisional suffixes and native lifetimes."""
from contextlib import contextmanager
import os
from types import SimpleNamespace

import pytest

from app import archive_trash, ingest
from scripts import stress_dedup as stress


@pytest.fixture
def directory_pins(monkeypatch):
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    monkeypatch.setattr(ingest, '_FINGERPRINT_CACHE_LIMIT', 0)
    original = archive_trash._windows_directory_pin
    if os.name != 'nt':
        # Exercise transaction/ownership logic with actual directory FDs.
        # Only the native Windows cases establish writer/rename exclusion.
        monkeypatch.setattr(ingest, 'os', SimpleNamespace(name='nt'))
        monkeypatch.setattr(archive_trash, 'os', SimpleNamespace(name='nt'))
        @contextmanager
        def original(folder):
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield
            finally:
                os.close(fd)
    active, opened = set(), []
    @contextmanager
    def observed(folder):
        with original(folder):
            assert folder not in active, 'An ancestor was opened more than once'
            opened.append(folder)
            active.add(folder)
            try:
                yield
            finally:
                active.remove(folder)
    monkeypatch.setattr(archive_trash, '_windows_directory_pin', observed)
    return active, opened, observed


def test_request_directory_pins_share_common_ancestors_and_exclude_writers(tmp_path, directory_pins):
    active, opened, _ = directory_pins
    db = stress.Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    records = stress.seed(db, archive, 4, 1, depth=3)
    index = None
    try:
        index = ingest.DedupBatch.prepare(db, archive, {records[0]['file_size']})
        expected = set()
        for record in records:
            parent = (archive / record['file']).parent
            expected.update([*parent.parents, parent])
        assert index.directories == active == expected
        assert len(opened) == len(expected)
        assert len(index.entries) == len(index.folders) == 4
        if os.name == 'nt':
            with pytest.raises(PermissionError):
                (archive / records[0]['file']).write_bytes(b'blocked writer')
            for directory in (archive / 'stress', (archive / records[-1]['file']).parent):
                with pytest.raises(OSError):
                    directory.rename(directory.with_name(directory.name + '-moved'))
        index.close()
        assert not active and not index.directories and stress.available(db)
        for record in records:
            path = archive / record['file']
            path.write_bytes(path.read_bytes())
        (archive / 'stress').rename(archive / 'released')
    finally:
        if index is not None:
            index.close()
        db.conn.close()


@pytest.mark.parametrize('reason', ['suffix', 'unsupported', 'read_failure', 'changed_version', 'cancel'])
def test_failed_new_branch_preserves_earlier_directory_pins(tmp_path, monkeypatch, directory_pins, reason):
    active, _, original_pin = directory_pins
    db = stress.Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    records = stress.seed(db, archive, 1, 1)
    index = ingest.DedupBatch.prepare(db, archive, {records[0]['file_size']})
    previous = set(active), set(index.directories), set(index.folders)
    path = archive / 'new-branch/fail/deep/new.fits'
    path.parent.mkdir(parents=True)
    path.write_bytes(stress.compressed_gate_image(False))
    record = {'id': 9999, 'file': path.relative_to(archive).as_posix()}
    @contextmanager
    def failing_pin(folder):
        if reason == 'suffix' and folder.name == 'fail':
            raise OSError('synthetic suffix refusal')
        with original_pin(folder):
            if reason == 'cancel' and folder.name == 'fail':
                raise ingest.ReindexCancelled('synthetic suffix cancellation')
            yield
    monkeypatch.setattr(archive_trash, '_windows_directory_pin', failing_pin)
    version = ingest.pinned_version
    if reason in ('unsupported', 'changed_version'):
        @contextmanager
        def altered_version(candidate):
            if candidate != path:
                with version(candidate) as value:
                    yield value
            elif reason == 'unsupported':
                yield None
            else:
                counter = iter(range(100))
                yield lambda: next(counter)
        monkeypatch.setattr(ingest, 'pinned_version', altered_version)
    elif reason == 'read_failure':
        monkeypatch.setattr(ingest, '_fingerprint_file', lambda *args: None)
    try:
        with db.archive_lock.for_batch(index):
            if reason == 'cancel':
                with pytest.raises(ingest.ReindexCancelled):
                    index.fingerprint(db, record, path, 'different', admit_only=True)
            else:
                index.fingerprint(db, record, path, 'different', admit_only=True)
        assert (active, index.directories, index.folders) == previous
        assert record['id'] not in index.entries and index.leased
        if os.name == 'nt':
            with pytest.raises(OSError):
                (archive / 'stress').rename(archive / 'moved')
        # The failed suffix is not pinned; only the earlier branch remains.
        (archive / 'new-branch').rename(archive / 'released-branch')
    finally:
        index.close()
        db.conn.close()
    assert not active


@pytest.mark.skipif(os.name != 'nt', reason='Native external directory-pin lifetime')
def test_request_directory_pins_outlive_external_pin_context(tmp_path):
    db = stress.Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    records = stress.seed(db, archive, 1, 1)
    info = archive.stat()
    external = archive_trash.PinnedParents([(archive, records[0]['file'], (info.st_dev, info.st_ino))])
    index = None
    try:
        with external.use():
            index = ingest.DedupBatch.prepare(db, archive, {records[0]['file_size']})
        external.close()
        assert index.directories
        with pytest.raises(OSError):
            (archive / 'stress').rename(archive / 'moved')
        index.close()
        (archive / 'stress').rename(archive / 'released')
    finally:
        if index is not None:
            index.close()
        external.close()
        db.conn.close()
