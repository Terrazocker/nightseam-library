"""Opt-in native Rice ingest preserves original identity and publishes atomically."""
import asyncio
import gzip
import hashlib
import io
import sqlite3
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app import compression, ingest
from app.archive_health import scan_archive
from app.server import create_app
from test_compression import exposure, library, no_stages, run
from test_archive_health import scan, acknowledge


@pytest.mark.parametrize('action', ['forget_missing', 'accept_contents'])
def test_health_acknowledgement_invalidates_original_compression_alias(tmp_path, action):
    db, archive, _ = library(tmp_path, count=0)
    result = upload(db, archive, exposure(), compress=True)
    path = archive / result['stored']
    if action == 'forget_missing':
        path.unlink()
    else:
        path.write_bytes(path.read_bytes()[:-1] + b'X')
    with TestClient(create_app(db)) as client:
        report = scan(client)
        assert acknowledge(client, report, result['id'], action).status_code == 200
    assert db.conn.execute('SELECT COUNT(*) FROM image_compression').fetchone()[0] == 0
    if action == 'accept_contents':
        assert db.image(result['id'])['storage_compressed'] is None


def upload(db, archive, data, name='auto.fit', **kwargs):
    return asyncio.run(ingest.ingest_one(db, [], archive, UploadFile(io.BytesIO(data), filename=name), **kwargs))


def empty_archive(archive):
    assert not [p for p in archive.rglob('*') if p.is_file()]


def test_opt_in_stores_only_verified_rice_source_untouched_and_restores(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    source = tmp_path / 'source.fit'
    original = exposure()
    source.write_bytes(original)
    with source.open('rb') as stream:
        result = asyncio.run(ingest.ingest_one(db, [], archive, UploadFile(stream, filename=source.name), compress=True))
    assert result['ok'] and result['compression'] == 'rice'
    row = db.images()[0]
    assert row['storage_compressed'] == 1
    assert row['file'].endswith('.fit.fz') and row['orig_name'] == source.name
    assert source.read_bytes() == original
    assert len([p for p in archive.rglob('*') if p.is_file()]) == 1
    mapping = dict(db.conn.execute('SELECT * FROM image_compression').fetchone())
    assert mapping['original_sha256'] == hashlib.sha256(original).hexdigest()
    assert mapping['current_sha256'] == hashlib.sha256((archive / row['file']).read_bytes()).hexdigest()
    assert scan_archive(db, 'deep', threading.Event(), lambda _: None)['verified'] == 1
    duplicate = upload(db, archive, original, compress=True)
    assert duplicate['skipped'] and duplicate['id'] == row['id']
    run(db, 'uncompress')
    assert (archive / db.images()[0]['file']).read_bytes() == original
    assert not list(archive.glob('.nightseam-import-*'))
    no_stages(db, archive)


@pytest.mark.parametrize('kind', ['float', 'gzip', 'rice', 'directive'])
def test_unsupported_and_already_compressed_import_unchanged(tmp_path, kind):
    db, archive, _ = library(tmp_path, count=0)
    raw = exposure(dtype=np.float32) if kind == 'float' else exposure(FZI2F=True) if kind == 'directive' else exposure()
    name = 'image.fit'
    if kind == 'gzip':
        raw = gzip.compress(raw)
        name += '.gz'
    elif kind == 'rice':
        first = upload(db, archive, raw, compress=True)
        raw = (archive / first['stored']).read_bytes()
        # A distinct catalog tests importing an existing compressed representation.
        (tmp_path / 'other').mkdir()
        db, archive, _ = library(tmp_path / 'other', count=0)
        name += '.fz'
    result = upload(db, archive, raw, name, compress=True)
    assert result['ok'] and result['compression'] == 'unchanged' and result['compression_reason']
    assert (archive / result['stored']).read_bytes() == raw
    assert db.conn.execute('SELECT COUNT(*) FROM image_compression').fetchone()[0] == 0
    assert not list(archive.glob('.nightseam-import-*'))


@pytest.mark.parametrize('mode,failure', [('safe', 'tool'), ('safe', 'hash'), ('safe', 'database'),
    ('safe', 'publication'), ('fast', 'tool'), ('fast', 'database'), ('fast', 'publication')])
def test_compression_failure_publishes_neither_representation(tmp_path, monkeypatch, mode, failure):
    db, archive, _ = library(tmp_path, count=0)
    native = compression.native

    def failing_native(executable, flags, source, destination, checkpoint):
        if failure == 'tool':
            raise ValueError('synthetic native failure')
        native(executable, flags, source, destination, checkpoint)
        if failure == 'hash' and executable.name == 'funpack.exe':
            destination.write_bytes(destination.read_bytes()[:-1] + b'X')
    monkeypatch.setattr(compression, 'native', failing_native)
    promote = ingest._promote_stage

    def corrupt_publication(source, target):
        promote(source, target)
        if failure == 'publication':
            target.write_bytes(target.read_bytes()[:-1] + b'X')
    monkeypatch.setattr(ingest, '_promote_stage', corrupt_publication)
    if failure == 'database':
        db.conn.execute("CREATE TRIGGER fail_mapping BEFORE INSERT ON image_compression BEGIN SELECT RAISE(ABORT,'synthetic mapping failure'); END")
        db.conn.commit()
        with pytest.raises(sqlite3.Error):
            upload(db, archive, exposure(), compress=True, compression_mode=mode)
    else:
        result = upload(db, archive, exposure(), compress=True, compression_mode=mode)
        assert not result['ok'] and 'Lossless Rice compression failed' in result['error']
    assert not db.images()
    assert db.conn.execute('SELECT COUNT(*) FROM image_integrity').fetchone()[0] == 0
    assert db.conn.execute('SELECT COUNT(*) FROM image_compression').fetchone()[0] == 0
    empty_archive(archive)


def test_missing_compressed_original_restores_same_record_even_with_option_off(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    raw = exposure()
    first = upload(db, archive, raw, compress=True)
    before = db.images()[0]
    (archive / first['stored']).unlink()
    restored = upload(db, archive, raw, name='renamed.fit', compress=False)
    assert restored['ok'] and restored['restored'] and restored['id'] == first['id']
    assert len(db.images()) == 1 and db.images()[0]['file'] == before['file']
    assert db.images()[0]['orig_name'] == before['orig_name']
    assert scan_archive(db, 'deep', threading.Event(), lambda _: None)['verified'] == 1
    run(db, 'uncompress')
    assert (archive / db.images()[0]['file']).read_bytes() == raw


def test_missing_uncompressed_record_restores_from_prior_compressed_bytes(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    raw = exposure()
    first = upload(db, archive, raw, compress=True)
    packed = (archive / first['stored']).read_bytes()
    run(db, 'uncompress')
    before = db.images()[0]
    (archive / before['file']).unlink()
    result = upload(db, archive, packed, name='previous.fit.fz', compress=True)
    assert result['ok'] and result['restored'] and result['id'] == first['id']
    assert db.images()[0]['file'] == before['file'] and len(db.images()) == 1
    assert (archive / before['file']).read_bytes() == raw
    assert scan_archive(db, 'deep', threading.Event(), lambda _: None)['verified'] == 1


def test_compressed_collision_names_restore_without_overwriting(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    first = upload(db, archive, exposure(), compress=True)
    second = upload(db, archive, exposure(1), compress=True)
    assert first['stored'] != second['stored']
    before = db.images()
    run(db, 'uncompress')
    for index, row in enumerate(db.images()):
        assert row['file'] == before[index]['file'][:-3]
        assert (archive / row['file']).read_bytes() == exposure(index)


def test_cancellation_stops_verification_and_cleans_staging(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    entered = threading.Event()

    def slow_native(executable, flags, source, destination, checkpoint):
        entered.set()
        while True:
            checkpoint()
            time.sleep(.01)
    monkeypatch.setattr(compression, 'native', slow_native)

    async def cancel_import():
        task = asyncio.create_task(ingest.ingest_one(db, [], archive,
            UploadFile(io.BytesIO(exposure()), filename='cancel.fit'), compress=True))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    asyncio.run(cancel_import())
    assert not db.images()
    empty_archive(archive)


def test_multipart_option_default_and_opt_in_are_independent_per_file(tmp_path):
    db, archive, _ = library(tmp_path, count=0)
    with TestClient(create_app(db)) as client:
        plain = client.post('/api/images/ingest', files={'files': ('plain.fit', exposure())}).json()['results'][0]
        assert plain['ok'] and not plain['stored'].endswith('.fz') and 'compression' not in plain
        packed = client.post('/api/images/ingest', data={'compress': 'true'}, files=[
            ('files', ('packed.fit', exposure(1))), ('files', ('float.fit', exposure(2, dtype=np.float32)))]).json()['results']
        assert packed[0]['compression'] == 'rice' and packed[1]['compression'] == 'unchanged'
        assert packed[0]['compression_mode'] == 'safe'
        assert all(r['ok'] for r in packed)
        assert client.post('/api/images/ingest', data={'compress': 'invalid'}, files={'files': ('bad.fit', exposure(3))}).status_code == 422
        assert client.post('/api/images/ingest', data={'compress': 'true', 'compression_mode': 'invalid'}, files={'files': ('bad.fit', exposure(3))}).status_code == 422
        fast = client.post('/api/images/ingest', data={'compress': 'true', 'compression_mode': 'fast'}, files={'files': ('fast.fit', exposure(3))}).json()['results'][0]
        assert fast['ok'] and fast['compression_mode'] == 'fast'


@pytest.mark.parametrize('mode,expected_tools,baseline_source', [
    ('fast', ['fpack.exe'], 'rice_ingest_fast'),
    ('safe', ['fpack.exe', 'funpack.exe'], 'verified_rice_ingest')])
def test_fast_skips_decoder_safe_verifies_and_both_restore(tmp_path, monkeypatch, mode, expected_tools, baseline_source):
    db, archive, _ = library(tmp_path, count=0)
    original = exposure()
    native = compression.native
    calls = []

    def record_native(executable, *args):
        calls.append(executable.name)
        return native(executable, *args)
    monkeypatch.setattr(compression, 'native', record_native)
    result = upload(db, archive, original, compress=True, compression_mode=mode)
    assert result['ok'] and result['compression_mode'] == mode
    assert calls == expected_tools
    assert db.conn.execute('SELECT source FROM image_integrity').fetchone()[0] == baseline_source
    assert scan_archive(db, 'deep', threading.Event(), lambda _: None)['verified'] == 1
    run(db, 'uncompress')
    assert (archive / db.images()[0]['file']).read_bytes() == original


def test_missing_recovery_still_requires_safe_verification_when_fast_requested(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    original = exposure()
    first = upload(db, archive, original, compress=True, compression_mode='fast')
    (archive / first['stored']).unlink()
    native = compression.native
    decoded = []

    def corrupt_decoder(executable, flags, source, destination, checkpoint):
        native(executable, flags, source, destination, checkpoint)
        if executable.name == 'funpack.exe':
            decoded.append(True)
            destination.write_bytes(destination.read_bytes()[:-1] + b'X')
    monkeypatch.setattr(compression, 'native', corrupt_decoder)
    result = upload(db, archive, original, compress=True, compression_mode='fast')
    assert decoded and not result['ok']
    assert len(db.images()) == 1 and not (archive / first['stored']).exists()
    assert not list(archive.glob('.nightseam-import-*'))
