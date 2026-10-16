"""Actual native Rice round trips and failure-safe catalog publication."""
import asyncio
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import threading
import time

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app import compression, ingest
from app.archive_health import scan_archive
from app.database import Database
from app.server import create_app


def exposure(index=0, dtype=np.uint16, **extra):
    header = fits.Header({'OBJECT': 'Rice test', 'DATE-OBS': f'2026-08-22T22:00:{index:02d}',
        'EXPTIME': 10, 'SITELAT': 50.0, 'SITELONG': 7.0, 'IMAGETYP': 'LIGHT', **extra})
    output = io.BytesIO()
    fits.PrimaryHDU(np.arange(2048, dtype=dtype).reshape(32, 64) + index, header=header).writeto(output)
    return output.getvalue()


def library(tmp_path, count=2, **extra):
    db = Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    archive.mkdir()
    db.update_settings({'image_dir': str(archive)})
    originals = {}
    for i in range(count):
        data = exposure(i, **extra)
        result = asyncio.run(ingest.ingest_one(db, db.all_objects(advanced=True), archive, UploadFile(io.BytesIO(data), filename=f'image-{i}.fit')))
        assert result['ok'], result
        originals[result['id']] = data
    return db, archive, originals


def run(db, mode, **kwargs):
    rows = db.images()
    return compression.convert(db, mode, rows[0]['id'], [r['id'] for r in rows],
        kwargs.pop('cancel', threading.Event()), kwargs.pop('publish', lambda _: None), **kwargs)


def no_stages(db, archive):
    assert not list(archive.glob('.nightseam-rice-*'))
    assert db.conn.execute('SELECT COUNT(*) FROM compression_journal').fetchone()[0] == 0


@pytest.mark.parametrize('mode', ['fast', 'safe'])
def test_session_modes_parallel_workers_and_real_publication_progress(tmp_path, monkeypatch, mode):
    db, archive, originals = library(tmp_path, count=4)
    native = compression.native
    barrier = threading.Barrier(2, timeout=10)
    calls, guard = [], threading.Lock()
    def observed(executable, *args):
        with guard:
            calls.append(executable.name)
            packs = calls.count('fpack.exe')
        if executable.name == 'fpack.exe' and packs <= 2:
            barrier.wait()
        return native(executable, *args)
    monkeypatch.setattr(compression, 'native', observed)
    reports = []
    result = run(db, 'compress', compression_mode=mode, publish=lambda report: reports.append(dict(report)))
    assert calls.count('fpack.exe') == 4
    assert calls.count('funpack.exe') == (4 if mode == 'safe' else 0)
    assert any(report['processed'] == 4 and report['published'] == 0 for report in reports)
    assert [report['published'] for report in reports] == sorted(report['published'] for report in reports)
    assert result['published'] == result['publication_total'] == 4
    assert {'checking_originals', 'publishing', 'committing', 'cleaning'} <= {report['phase'] for report in reports}
    assert all(report.get('status') != 'completed' for report in reports)
    expected = 'rice_conversion_fast' if mode == 'fast' else 'verified_rice_conversion'
    assert {record['baseline_source'] for record in db.integrity_snapshot()[1]} == {expected}
    run(db, 'uncompress')
    assert all((archive / record['file']).read_bytes() == originals[record['id']] for record in db.images())
    no_stages(db, archive)


def test_compression_api_mode_validation_and_default(tmp_path):
    db, archive, _ = library(tmp_path, count=1)
    record = db.images()[0]
    with TestClient(create_app(db)) as client:
        body = dict(mode='compress', anchor_id=record['id'], image_ids=[record['id']])
        assert client.post('/api/library/compression', json={**body, 'compression_mode': 'invalid'}).status_code == 422
        response = client.post('/api/library/compression', json=body)
        assert response.status_code == 202 and response.json()['compression_mode'] == 'safe'


def test_progress_callback_failure_drains_workers_before_recovery(tmp_path, monkeypatch):
    db, archive, originals = library(tmp_path, count=2)
    native, recovery = compression.native, compression.recover
    waiting, stopped = threading.Event(), threading.Event()
    def conversion(executable, flags, source, destination, checkpoint):
        if executable.name == 'fpack.exe' and source.name == 'image-1.fit':
            waiting.set()
            try:
                while True:
                    checkpoint()
                    time.sleep(.005)
            finally:
                stopped.set()
        return native(executable, flags, source, destination, checkpoint)
    def progress(report):
        if report['processed']:
            assert waiting.wait(3)
            raise RuntimeError('synthetic progress callback failure')
    def recover(db):
        if waiting.is_set():
            assert stopped.is_set(), 'recovery cannot race live conversion workers'
        return recovery(db)
    monkeypatch.setattr(compression, 'native', conversion)
    monkeypatch.setattr(compression, 'recover', recover)
    with pytest.raises(RuntimeError, match='progress callback failure'):
        run(db, 'compress', publish=progress)
    assert stopped.is_set()
    assert all((archive / record['file']).read_bytes() == originals[record['id']] for record in db.images())
    no_stages(db, archive)


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows pinned directory cache')
def test_pinned_directory_cache_rejects_replaced_leaf_link(tmp_path):
    from app.archive_trash import PinnedParents
    db, archive, _ = library(tmp_path, count=1)
    record = db.images()[0]
    path = archive / record['file']
    original = path.read_bytes()
    outside = tmp_path / 'private-outside.fit'
    outside.write_bytes(b'private outside data')
    info = archive.stat()
    identity = info.st_dev, info.st_ino
    pins = PinnedParents([(archive, record['file'], identity)])
    alias = path.with_name('previously-unseen.fit')
    try:
        with pytest.raises(OSError):
            path.parent.rename(path.parent.with_name('blocked-parent-move'))
        alias.symlink_to(path)
        with pins.use(), pytest.raises(ValueError, match='Linked'):
            compression.fingerprint(archive, alias.relative_to(archive).as_posix(), identity, lambda: None)
        alias.unlink()
        path.unlink()
        path.symlink_to(outside)
        with pins.use(), pytest.raises(ValueError, match='Linked'):
            compression.fingerprint(archive, record['file'], identity, lambda: None)
    finally:
        alias.unlink(missing_ok=True)
        pins.close()
        moved = path.parent.with_name('blocked-parent-move')
        if moved.exists() and not path.parent.exists():
            moved.rename(path.parent)
        path.unlink(missing_ok=True)
        path.write_bytes(original)


def test_legacy_compression_status_migration_uses_current_bytes_not_mapping_existence(tmp_path):
    db, archive, _ = library(tmp_path)
    db.conn.execute('UPDATE images SET storage_compressed=NULL')
    db.conn.commit()
    reopened = Database(db.path)
    assert all(r['storage_compressed'] is None for r in reopened.images())
    reopened.conn.close()
    run(db, 'compress')
    for mode, expected in [('compress', 1), ('uncompress', 0)]:
        if mode == 'uncompress':
            run(db, mode)
        db.conn.execute('UPDATE images SET storage_compressed=NULL')
        db.conn.commit()
        reopened = Database(db.path)
        assert all(r['storage_compressed'] == expected for r in reopened.images())
        reopened.conn.close()


def test_native_session_roundtrip_dedup_integrity_and_membership(tmp_path):
    db, archive, originals = library(tmp_path)
    before = db.images()
    result = run(db, 'compress')
    assert result['converted'] == 2 and result['status'] == 'completed'
    for old, new in zip(before, db.images()):
        assert new['file'] == old['file'] + '.fz'
        assert not (archive / old['file']).exists()
        assert compression.kind(archive / new['file'], lambda: None) == 'rice'
        assert new['storage_compressed'] == 1
        assert all(new[k] == old[k] for k in old if k not in ('file', 'content_sha256', 'file_size', 'file_mtime_ns', 'storage_compressed'))
    duplicate = asyncio.run(ingest.ingest_one(db, db.all_objects(advanced=True), archive, UploadFile(io.BytesIO(originals[before[0]['id']]), filename='again.fit')))
    assert duplicate['skipped'] and duplicate['id'] == before[0]['id']
    health = scan_archive(db, 'deep', threading.Event(), lambda _: None)
    assert health['verified'] == 2 and not health['findings']
    run(db, 'uncompress')
    for old, new in zip(before, db.images()):
        assert old['file'] == new['file']
        assert (archive / new['file']).read_bytes() == originals[new['id']]
        assert new['content_sha256'] == old['content_sha256']
        assert new['storage_compressed'] == 0
    assert scan_archive(db, 'deep', threading.Event(), lambda _: None)['verified'] == 2
    no_stages(db, archive)


@pytest.mark.parametrize('extra', [{'dtype': np.float32}, {'FZI2F': True}, {'FZALGOR': 'GZIP_1'}])
def test_unsupported_refuses_without_changing_anything(tmp_path, extra):
    db, archive, originals = library(tmp_path, **extra)
    before = db.images()
    with pytest.raises(ValueError, match='Cannot safely Rice'):
        run(db, 'compress')
    assert db.images() == before
    assert all((archive / r['file']).read_bytes() == originals[r['id']] for r in before)
    no_stages(db, archive)


@pytest.mark.parametrize('failure', ['second_file', 'cancel', 'commit', 'publish', 'hash_mismatch'])
def test_failures_roll_back_session(tmp_path, monkeypatch, failure):
    db, archive, originals = library(tmp_path)
    before = db.images()
    cancel = threading.Event()
    native = compression.native
    calls = 0

    def failing_native(*args):
        nonlocal calls
        calls += 1
        if failure == 'second_file' and args[0].name == 'fpack.exe' and args[2].name == 'image-1.fit':
            raise ValueError('synthetic tool failure')
        native(*args)
        if failure == 'hash_mismatch' and args[0].name == 'funpack.exe':
            output = args[3]
            output.write_bytes(output.read_bytes()[:-1] + b'X')
        if failure == 'cancel' and args[0].name == 'funpack.exe':
            cancel.set()
    monkeypatch.setattr(compression, 'native', failing_native)
    transaction = compression._transaction

    @contextmanager
    def failing_commit(db):
        with transaction(db) as conn:
            yield conn
            state = conn.execute('SELECT state FROM compression_journal').fetchone()
            if failure == 'commit' and state and state[0] == 'committed':
                raise sqlite3.OperationalError('synthetic commit failure')
    monkeypatch.setattr(compression, '_transaction', failing_commit)
    move = compression._move
    moves = 0

    def failing_move(*args):
        nonlocal moves
        moves += 1
        if failure == 'publish' and moves == 3:
            raise OSError('synthetic publication failure')
        return move(*args)
    monkeypatch.setattr(compression, '_move', failing_move)
    with pytest.raises((ValueError, OSError, sqlite3.OperationalError, compression.Cancelled)):
        run(db, 'compress', cancel=cancel)
    assert db.images() == before
    assert all((archive / r['file']).read_bytes() == originals[r['id']] for r in before)
    no_stages(db, archive)


@pytest.mark.parametrize('dtype', [np.uint8, np.int32])
def test_other_supported_integer_types_restore_exact_bytes(tmp_path, dtype):
    db, archive, originals = library(tmp_path, count=1, dtype=dtype)
    run(db, 'compress')
    run(db, 'uncompress')
    row = db.images()[0]
    assert (archive / row['file']).read_bytes() == originals[row['id']]


@pytest.mark.parametrize('later_float', [True, False])
def test_every_image_hdu_is_checked(tmp_path, later_float):
    path = tmp_path / 'multi.fit'
    later = fits.ImageHDU(np.ones((4, 4), dtype=np.float32 if later_float else np.int16))
    if not later_float:
        later.header['FZI2F'] = True
    fits.HDUList([fits.PrimaryHDU(np.ones((4, 4), dtype=np.int16)), later]).writeto(path)
    assert compression.kind(path, lambda: None) == 'unsupported'


def test_destination_collision_preserves_unrecorded_file(tmp_path):
    db, archive, _ = library(tmp_path)
    before = db.images()
    occupied = archive / (before[1]['file'] + '.fz')
    occupied.write_bytes(b'unrecorded file')
    with pytest.raises(ValueError, match='already exists'):
        run(db, 'compress')
    assert db.images() == before and occupied.read_bytes() == b'unrecorded file'
    no_stages(db, archive)


def test_stale_membership_and_external_change_refused(tmp_path):
    db, archive, _ = library(tmp_path)
    before = db.images()
    with pytest.raises(ValueError, match='membership changed'):
        compression.convert(db, 'compress', before[0]['id'], [before[0]['id']], threading.Event(), lambda _: None)
    path = archive / before[0]['file']
    path.write_bytes(path.read_bytes()[:-1] + b'X')
    with pytest.raises(ValueError, match='Integrity check failed'):
        run(db, 'compress')
    assert db.images() == before
    no_stages(db, archive)


def test_calibration_anchor_and_other_object_are_preserved(tmp_path):
    db, archive, originals = library(tmp_path, count=1)
    anchor = db.images()[0]
    target = ingest.calibration_import_target(db, archive, anchor['id'], 'dark')
    dark = exposure(3, IMAGETYP='DARK', **{'DATE-OBS': '2030-01-01T00:00:00'})
    attached = asyncio.run(ingest.ingest_one(db, [], archive,
        UploadFile(io.BytesIO(dark), filename='dark.fit'), frame_override='dark', session_target=target))
    assert attached['ok'], attached
    other = exposure(2, OBJECT='Different Rice target')
    result = asyncio.run(ingest.ingest_one(db, [], archive, UploadFile(io.BytesIO(other), filename='other.fit')))
    assert result['ok'], result
    before = db.images()
    ids = [anchor['id'], attached['id']]
    compression.convert(db, 'compress', anchor['id'], ids, threading.Event(), lambda _: None)
    unchanged = db.image(result['id'])
    assert unchanged == next(r for r in before if r['id'] == result['id'])
    assert (archive / unchanged['file']).read_bytes() == other
    assert db.image(attached['id'])['date_obs'] == '2030-01-01T00:00:00'
    compression.convert(db, 'uncompress', anchor['id'], ids, threading.Event(), lambda _: None)
    assert (archive / db.image(attached['id'])['file']).read_bytes() == dark
    no_stages(db, archive)


def test_incomplete_recovery_blocks_archive_mutations(tmp_path):
    db, archive, _ = library(tmp_path)
    with TestClient(create_app(db)) as client:
        db.conn.execute('INSERT INTO compression_journal VALUES(?,?,?,?,?,?)',
            ('blocked', str(archive), str(archive.stat().st_dev), str(archive.stat().st_ino), 'prepared', '[]'))
        db.conn.commit()
        for path in ('/api/library/reindex', '/api/library/edit', '/api/images/ingest'):
            assert client.post(path, json={}).status_code == 409
        assert client.post('/api/settings', json={'image_dir': str(tmp_path)}).status_code == 409
        assert client.get('/api/library').status_code == 200
        compression.recover(db)
    no_stages(db, archive)


def test_manual_library_copy_has_individual_usable_files_and_hashes(tmp_path):
    db, archive, originals = library(tmp_path)
    before = db.images()
    disk = tmp_path / 'backup-disk'
    disk.mkdir()
    result = run(db, 'copy', destination=str(disk))
    saved = Path(result['destination'])
    manifest = json.loads((saved / 'manifest.json').read_text())
    assert len(manifest['files']) == 2 and (saved / 'nightseam.db').is_file()
    for item in manifest['files']:
        path = saved / item['stored_file']
        assert compression.kind(path, lambda: None) == 'rice'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['stored_sha256']
        restored = tmp_path / 'restored.fit'
        compression.native(compression.tools()['funpack'], ['-C'], path, restored, lambda: None)
        assert restored.read_bytes() == originals[item['record']['id']]
        restored.unlink()
    assert db.images() == before
    copied = Database(saved / 'nightseam.db')
    assert all(r['storage_compressed'] == 1 for r in copied.images())
    assert copied.image_dir() == str(saved / 'images')
    assert scan_archive(copied, 'deep', threading.Event(), lambda _: None)['verified'] == 2
    run(copied, 'uncompress')
    assert all((Path(copied.image_dir()) / r['file']).read_bytes() == originals[r['id']] for r in copied.images())
    no_stages(db, archive)


def test_copy_preserves_unsupported_files_and_cancellation_cleans_both_disks(tmp_path):
    db, archive, originals = library(tmp_path, dtype=np.float32)
    disk = tmp_path / 'disk'
    disk.mkdir()
    result = run(db, 'copy', destination=str(disk))
    assert result['converted'] == 0 and result['skipped'] == 2
    copied = Path(result['destination'])
    manifest = json.loads((copied / 'manifest.json').read_text())
    assert all((copied / r['stored_file']).read_bytes() == originals[r['record']['id']] for r in manifest['files'])
    cancel = threading.Event()

    def stop(report):
        if report['processed']:
            cancel.set()
    with pytest.raises(compression.Cancelled):
        run(db, 'copy', destination=str(disk), cancel=cancel, publish=stop)
    assert list(disk.iterdir()) == [copied]
    no_stages(db, archive)


@pytest.mark.parametrize('committed', [False, True])
def test_startup_recovers_interrupted_publication(tmp_path, committed):
    db, archive, originals = library(tmp_path, count=1)
    row = db.images()[0]
    token = 'recovery-test'
    stage = compression._stage_name(token)
    work = archive / stage
    work.mkdir()
    original = archive / row['file']
    original.rename(work / f"{row['id']}.original")
    target_rel = row['file'] + '.fz'
    target = archive / target_rel
    target.write_bytes(b'published representation')
    plan = [dict(id=row['id'], old=row['file'], new=target_rel, sha256=hashlib.sha256(target.read_bytes()).hexdigest(), size=target.stat().st_size)]
    with db.lock:
        db.conn.execute('INSERT INTO compression_journal VALUES(?,?,?,?,?,?)',
            (token, str(archive), str(archive.stat().st_dev), str(archive.stat().st_ino), 'committed' if committed else 'prepared', json.dumps(plan)))
        db.conn.commit()
    compression.recover(db)
    if committed:
        assert target.read_bytes() == b'published representation' and not original.exists()
    else:
        assert original.read_bytes() == originals[row['id']] and not target.exists()
    no_stages(db, archive)


def test_background_api_is_responsive_and_cancellable(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path)
    entered, proceed = threading.Event(), threading.Event()
    native = compression.native

    def slow_native(executable, flags, source, destination, checkpoint):
        entered.set()
        while not proceed.wait(.01):
            checkpoint()
        return native(executable, flags, source, destination, checkpoint)
    monkeypatch.setattr(compression, 'native', slow_native)
    app = create_app(db)
    with TestClient(app) as client:
        assert client.post('/api/library/compression', json={'mode': 'copy'}).status_code == 422
        rows = db.images()
        response = client.post('/api/library/compression', json={'mode': 'compress',
            'anchor_id': rows[0]['id'], 'image_ids': [r['id'] for r in rows]})
        assert response.status_code == 202
        job = response.json()['id']
        assert entered.wait(5)
        assert client.get('/api/library').status_code == 200
        assert client.post('/api/library/compression', json={'mode': 'copy', 'destination': str(tmp_path)}).status_code == 409
        assert client.post(f'/api/library/compression/{job}/cancel').status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(f'/api/library/compression/{job}').json()
            if state['status'] == 'cancelled':
                break
            time.sleep(.01)
        assert state['status'] == 'cancelled', state
    no_stages(db, archive)
