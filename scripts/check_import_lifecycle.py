"""Check completed-body disconnects and native close during real packaged IO.

Only the owned executable, private database and verified private archive are used.
An external SQLite writer briefly blocks publication, without application hooks.
Ingest drains a fully received upload after disconnect; this is not a claim of
transport-driven cooperative cancellation. Native close must finish gracefully
before the harness is permitted to dispose of its process job.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import shutil
import socket
import sqlite3
import struct
import sys
import time
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/browser'))
import harness
from fits_fixtures import compressed_gate_image
from scripts.check_native_window import NativeWindows, wait_for


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def image(number):
    raw = bytearray(compressed_gate_image(False))
    raw[2880:2888] = struct.pack('>Q', number)
    return bytes(raw)


def send(server, path, content_type, body):
    connection = socket.create_connection(('127.0.0.1', server.port), timeout=20)
    try:
        headers = (f'POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n'
                   f'Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n'
                   'Connection: close\r\n\r\n').encode('ascii')
        connection.sendall(headers + body)
        return connection
    except BaseException:
        connection.close()
        raise


def send_upload(server, name, raw, run):
    boundary = 'nightseam-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="run_id"\r\n\r\n'
            f'{run}\r\n--{boundary}\r\nContent-Disposition: form-data; name="files"; '
            f'filename="{name}"\r\nContent-Type: application/fits\r\n\r\n').encode('ascii')
    body += raw + f'\r\n--{boundary}--\r\n'.encode('ascii')
    return send(server, '/api/images/ingest', 'multipart/form-data; boundary=' + boundary, body)


def response(connection):
    with http.client.HTTPResponse(connection) as result:
        result.begin()
        data = result.read()
        require(result.status == 200, f'HTTP {result.status}: {data[:500]!r}')
        return json.loads(data)


def imported(connection):
    result, = response(connection)['results']
    require(result.get('ok') and not any(result.get(key) for key in
        ('skipped', 'unconfirmed', 'unattempted')), f'Import failed: {result}')
    return result


def writer_allowed(path):
    try:
        with path.open('r+b'):
            return True
    except PermissionError:
        return False


def active_import(archive, pinned, name):
    # Publication promotes a stage before waiting for BEGIN IMMEDIATE. Either
    # an owned stage or the uncommitted destination establishes active file IO.
    work = any(archive.glob('.nightseam-import-*.part')) or any(archive.rglob(name))
    return work and not writer_allowed(pinned)


def audit(database, archive, expected):
    with closing(sqlite3.connect(database)) as db:
        rows = db.execute('SELECT id,orig_name,file,content_sha256 FROM images ORDER BY id').fetchall()
        history = db.execute('SELECT run_id,SUM(n_files) FROM import_batches GROUP BY run_id').fetchall()
        integrity = db.execute('SELECT COUNT(*) FROM image_integrity').fetchone()[0]
        pending = db.execute('SELECT COUNT(*) FROM compression_journal').fetchone()[0]
    require(len(rows) == integrity == len(expected), 'Incomplete or duplicated catalog/integrity publication')
    require(dict(history) == {run: 1 for run, _ in expected.values()}, 'Image/history units differ')
    require(pending == 0, 'Compression journal remains')
    for _, name, relative, digest in rows:
        require(name in expected, f'Unexpected catalog image: {name}')
        path = (archive / relative).resolve()
        require(path.is_relative_to(archive.resolve()), 'Catalog path escaped the private archive')
        raw = expected[name][1]
        require(path.read_bytes() == raw and digest == hashlib.sha256(raw).hexdigest(),
                f'Committed bytes/fingerprint differ: {name}')
        require(writer_allowed(path), f'Request still owns a writer-excluding pin: {name}')
    require(not any(path.name.startswith('.nightseam-') or path.suffix == '.part'
                    for path in archive.rglob('*')), 'Staging leftovers remain')
    return rows


def run(executable, output):
    require(os.name == 'nt', 'Windows is required; no packaged lifecycle checks executed')
    server = harness.DesktopServer(executable, seed=False, native=True, restricted_runtime=True)
    with executable.open('rb') as stream:
        executable_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = dict(status='running', source_commit=os.environ.get('GITHUB_SHA'),
        executable_sha256=executable_digest,
        completed_body_disconnect_cancels_ingest=False, checks=[])
    windows = None
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.start(timeout=60)
        windows = NativeWindows(server.tree)
        hwnd = wait_for(windows.main_window, 'owned native window', timeout=60)
        archive, database = server.tmp / 'archive', server.tmp / 'data/nightseam.db'
        expected = {'seed.fits': (uuid.uuid4().hex, image(0))}
        with closing(send_upload(server, 'seed.fits', expected['seed.fits'][1], expected['seed.fits'][0])) as seed:
            seed_result = imported(seed)
        pinned = (archive / seed_result['stored']).resolve()
        require(pinned.is_relative_to(archive.resolve()), 'Seed escaped the private archive')
        before = audit(database, archive, expected)

        with closing(sqlite3.connect(database, timeout=1)) as blocker, ExitStack() as connections:
            try:
                blocker.execute('BEGIN IMMEDIATE')
                started = time.monotonic()
                name = 'disconnected.fits'
                expected[name] = uuid.uuid4().hex, image(1)
                first = connections.enter_context(closing(send_upload(server, name, expected[name][1], expected[name][0])))
                wait_for(lambda: active_import(archive, pinned, name), 'active pinned import', timeout=2.5)
                require(blocker.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 1,
                        'Blocked import published before disconnect')
                first.close()  # The entire multipart body was transmitted.
                name = 'queued.fits'
                expected[name] = uuid.uuid4().hex, image(2)
                queued = connections.enter_context(closing(send_upload(server, name, expected[name][1], expected[name][0])))
                mutation = connections.enter_context(closing(send(server, '/api/settings', 'application/json',
                                json.dumps({'image_dir': str(archive)}).encode('utf-8'))))
                require(not select.select([queued, mutation], [], [], .1)[0],
                        'An archive operation completed while the request held its lease')
                with urllib.request.urlopen(server.base + 'catalog.html', timeout=1) as page:
                    require(page.status == 200, 'Static UI did not answer during blocked import')
                require(time.monotonic() - started < 4, 'External writer hold approached SQLite timeout')
            finally:
                blocker.rollback()
            imported(queued)
            require(response(mutation).get('ok'), 'Queued settings update failed')
            wait_for(lambda: writer_allowed(pinned) and not any(archive.glob('*.part')),
                     'disconnected request to release its pins and stages')
            after = audit(database, archive, expected)
            require(after[0] == before[0], 'Earlier committed seed changed')
            # A subsequent request must still find a positive duplicate.
            with closing(send_upload(server, 'repeated.fits', image(0), uuid.uuid4().hex)) as repeated:
                result, = response(repeated)['results']
                require(result.get('ok') and result.get('skipped') and result['id'] == seed_result['id'],
                        'Follow-up duplicate lost the original identity')
            audit(database, archive, expected)
            report['checks'].append('disconnect-contention-drain')
            print('[PASS] disconnect-contention-drain', flush=True)

            name = 'closing.fits'
            expected[name] = uuid.uuid4().hex, image(3)
            try:
                blocker.execute('BEGIN IMMEDIATE')
                started = time.monotonic()
                closing_request = connections.enter_context(closing(send_upload(server, name, expected[name][1], expected[name][0])))
                wait_for(lambda: active_import(archive, pinned, name), 'pinned import before native close', timeout=2.5)
                require(blocker.execute('SELECT COUNT(*) FROM images').fetchone()[0] == 3,
                        'Closing import published before native close')
                windows.post(hwnd, 0x0010)  # WM_CLOSE, only the owned native HWND.
                wait_for(lambda: not windows.user.IsWindow(hwnd), 'native window to close during pinned IO', timeout=.8)
                require(not writer_allowed(pinned), 'Import finished before the native window closed')
                require(time.monotonic() - started < 4, 'Shutdown writer hold approached SQLite timeout')
            finally:
                blocker.rollback()
            imported(closing_request)
            require(server.proc.wait(timeout=30) == 0, 'Native close failed to finish gracefully')
            wait_for(windows.processes_exited, 'all owned native descendants to exit', timeout=20)
            harness._wait_listener_closed(server.port)
            audit(database, archive, expected)
            # Native close releases directory pins before forced harness cleanup.
            moved = pinned.parent.with_name(pinned.parent.name + '-released')
            pinned.parent.rename(moved)
            moved.rename(pinned.parent)
            report['checks'].append('native-close-during-import')
            print('[PASS] native-close-during-import', flush=True)
        report.update(status='passed', committed_images=len(expected), history_units=len(expected),
                      staging_leftovers=0, graceful_process_exit=True, listener_closed=True)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        try:
            if server.tmp is not None:
                for log in (server.tmp / 'logs').glob('nightseam.log*'):
                    shutil.copyfile(log, output.parent / f'lifecycle-{log.name}')
            if windows is not None:
                windows.restore_thread()
        except BaseException as exc:
            report.update(status='failed', diagnostic_cleanup_error=str(exc))
            raise
        finally:
            try:
                server.stop()
                report['owned_scratch_cleanup'] = True
            except BaseException as exc:
                report.update(status='failed', cleanup_error=str(exc))
                raise
            finally:
                output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'check_import_lifecycle: {len(report["checks"])}/2 passed', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist/NightSeam.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/windows-import-lifecycle.json')
    args = parser.parse_args()
    run(args.exe.resolve(strict=True), args.output)


if __name__ == '__main__':
    main()
