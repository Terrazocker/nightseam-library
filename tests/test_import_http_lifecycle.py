"""Real sockets exercise complete-body disconnects and server shutdown drain."""
from contextlib import ExitStack, closing
import socket
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import uvicorn

from app import ingest, server
from scripts import check_import_lifecycle as gate
from test_compression import library


def test_real_http_disconnect_queue_and_shutdown_preserve_units(tmp_path, monkeypatch):
    db, archive, _ = library(tmp_path, count=0)
    monkeypatch.setattr(ingest, '_WINDOWS_BATCH_PINS', True)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    target = SimpleNamespace(port=port)
    service = uvicorn.Server(uvicorn.Config(server.create_app(db), host='127.0.0.1',
        port=port, log_level='error', access_log=False, loop='asyncio'))
    worker = threading.Thread(target=lambda: service.run(sockets=[listener]), daemon=True)
    connections = ExitStack()
    expected = {}
    worker.start()
    try:
        gate.wait_for(lambda: service.started, 'private HTTP server startup', timeout=10)
        def start(name, number):
            expected[name] = uuid.uuid4().hex, gate.image(number)
            run, raw = expected[name]
            return connections.enter_context(closing(gate.send_upload(target, name, raw, run)))
        with closing(start('seed.fits', 0)) as seed:
            seed_result = gate.imported(seed)
        before = gate.audit(tmp_path / 'catalog.db', archive, expected)
        with closing(sqlite3.connect(tmp_path / 'catalog.db')) as blocker:
            first = queued = None
            try:
                blocker.execute('BEGIN IMMEDIATE')
                first = start('orphan.fits', 1)
                # The uncommitted destination proves the endpoint is processing
                # this complete body and waiting on the actual SQLite writer.
                gate.wait_for(lambda: any(archive.rglob('orphan.fits')),
                              'active uncommitted orphan import', timeout=2.5)
                assert blocker.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 1
                first.close()
                queued = start('queued.fits', 2)
            finally:
                blocker.rollback()
                if first is not None:
                    first.close()
            try:
                gate.imported(queued)
            finally:
                if queued is not None:
                    queued.close()
            assert gate.audit(tmp_path / 'catalog.db', archive, expected)[0] == before[0]
            with closing(gate.send_upload(target, 'duplicate.fits', gate.image(0), uuid.uuid4().hex)) as repeated:
                result, = gate.response(repeated)['results']
                assert result['skipped'] and result['id'] == seed_result['id']

            closing_request = None
            try:
                blocker.execute('BEGIN IMMEDIATE')
                closing_request = start('closing.fits', 3)
                gate.wait_for(lambda: any(archive.rglob('closing.fits')),
                              'active import before server shutdown', timeout=2.5)
                assert blocker.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 3
                service.should_exit = True
            finally:
                blocker.rollback()
            try:
                gate.imported(closing_request)
                worker.join(timeout=15)
                assert not worker.is_alive(), 'Graceful HTTP shutdown retained workers'
                gate.audit(tmp_path / 'catalog.db', archive, expected)
                assert db.archive_lock.acquire(blocking=False)
                db.archive_lock.release()
            finally:
                if closing_request is not None:
                    closing_request.close()
    finally:
        try:
            connections.close()
        finally:
            service.should_exit = True
            worker.join(timeout=15)
            listener.close()
            assert not worker.is_alive(), 'Private HTTP test server failed to stop'
            db.conn.close()
