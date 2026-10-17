"""Storage exhaustion and real native-child failure must leave retryable imports."""
import asyncio
import errno
import io
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app import compression, ingest
from app.server import create_app
from test_compression import exposure, library
from test_ingest import _Upload


def state(db):
    return (db.images(), db.integrity_snapshot(), db.import_batches(),
            [tuple(r) for r in db.conn.execute('SELECT * FROM image_compression')])


def pristine(db, archive, before, files):
    assert state(db) == before
    assert {p.relative_to(archive).as_posix(): p.read_bytes()
            for p in archive.rglob('*') if p.is_file()} == files
    assert db.conn.execute('SELECT COUNT(*) FROM compression_journal').fetchone()[0] == 0


@pytest.mark.parametrize('adapter', ['async', 'spooled'])
def test_stage_disk_full_preserves_originals_and_allows_retry(tmp_path, monkeypatch, adapter):
    db, archive, _ = library(tmp_path, count=1)
    before = state(db)
    files = {r['file']: (archive / r['file']).read_bytes() for r in db.images()}
    original = Path.open

    class FullDisk:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            self.stream.close()
        def write(self, chunk):
            self.stream.write(chunk[:97])
            raise OSError(errno.ENOSPC, 'injected full disk')

    def opened(path, mode='r', *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        return FullDisk(stream) if mode == 'wb' and path.name.endswith('.part') else stream

    def upload():
        raw = exposure(3)
        source = (_Upload(raw) if adapter == 'async' else
                  UploadFile(io.BytesIO(raw), filename='retry.fit'))
        return asyncio.run(ingest.ingest_one(db, [], archive, source))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'open', opened)
            failed = upload()
        assert not failed['ok'] and 'injected full disk' in failed['error']
        pristine(db, archive, before, files)
        assert upload()['ok']
    finally:
        db.conn.close()


@pytest.mark.parametrize('point', ['fallback-copy', 'rice-encode-flush', 'rice-publication-flush'])
def test_storage_failure_rolls_back_image_history_and_mapping(tmp_path, monkeypatch, point):
    db, archive, _ = library(tmp_path, count=1)
    before = state(db)
    files = {r['file']: (archive / r['file']).read_bytes() for r in db.images()}
    promoted = []
    promote, fsync = ingest._promote_stage, ingest.os.fsync
    def observed(*args):
        result = promote(*args)
        promoted.append(True)
        return result
    def full_copy(source, destination, *args):
        destination.write(source.read(97))
        raise OSError(errno.ENOSPC, 'injected full disk')
    def unsupported(*args):
        raise OSError(errno.EXDEV, 'injected unsupported hard link')
    def full_flush(fd):
        if point == 'rice-encode-flush' or promoted:
            raise OSError(errno.ENOSPC, 'injected full disk')
        return fsync(fd)
    try:
        with TestClient(create_app(db)) as client:
            def upload():
                response = client.post('/api/images/ingest', files={
                    'files': ('retry.fit', exposure(3), 'application/fits')},
                    data={'compress': str(point != 'fallback-copy').lower()})
                assert response.status_code == 200, response.text
                return response.json()['results'][0]
            with monkeypatch.context() as patch:
                if point == 'fallback-copy':
                    patch.setattr(ingest.os, 'link', unsupported)
                    patch.setattr(ingest.shutil, 'copyfileobj', full_copy)
                else:
                    patch.setattr(ingest, '_promote_stage', observed)
                    patch.setattr(ingest.os, 'fsync', full_flush)
                failed = upload()
            assert not failed['ok'] and 'injected full disk' in failed['error']
            if point == 'rice-publication-flush':
                assert promoted
            pristine(db, archive, before, files)
            assert upload()['ok']
            assert len(db.images()) == 2
            assert sum(row['n_files'] for row in db.import_batches()) == 1
            assert not list(archive.glob('*.part'))
            assert not list(archive.glob('.nightseam-rice-*'))
    finally:
        db.conn.close()


@pytest.mark.parametrize('reason', ['cancel', 'deadline'])
def test_native_failure_terminates_and_reaps_real_child(tmp_path, monkeypatch, reason):
    children = []
    launch = compression.subprocess.Popen
    def tracked(*args, **kwargs):
        child = launch(*args, **kwargs)
        children.append(child)
        return child
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if reason == 'cancel' and calls == 2:
            raise compression.Cancelled
    try:
        with monkeypatch.context() as patch:
            patch.setattr(compression.subprocess, 'Popen', tracked)
            if reason == 'deadline':
                ticks = iter([0, 0, 601])
                patch.setattr(compression.time, 'monotonic', lambda: next(ticks))
            expected = compression.Cancelled if reason == 'cancel' else ValueError
            with pytest.raises(expected):
                compression.native(Path(sys.executable), ['-c', 'import time; time.sleep(30)'],
                    tmp_path / 'source', tmp_path / 'output', checkpoint)
        assert calls == 2 and len(children) == 1
        assert children[0].poll() is not None and children[0].returncode != 0
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
