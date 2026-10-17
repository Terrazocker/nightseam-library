"""Developer-only real HTTP import profile in an owned source process.

Normal application code has no profiling endpoints or counters. This bootstrap
patches source paths before database/server imports, and instruments synchronous
work in every worker thread. The controller's fixture/audit CPU is excluded.
Phase timings are instrumented measurements, not a packaged throughput baseline.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import bench_windows_import as bench
harness = bench.harness


class Measurements:
    def __init__(self):
        self.lock = threading.Lock()
        self.local = threading.local()
        self.enabled = False
        self.phases = {}
        self.counters = Counter()

    def start(self):
        with self.lock:
            self.phases.clear()
            self.counters.clear()
            self.wall_start = time.perf_counter_ns()
            self.cpu_start = time.process_time_ns()
            self.enabled = True

    def add(self, **values):
        if self.enabled:
            with self.lock:
                self.counters.update(values)

    @contextmanager
    def phase(self, name):
        if not self.enabled:
            yield
            return
        stack = getattr(self.local, 'stack', None)
        if stack is None:
            stack = self.local.stack = []
        frame = [name, time.perf_counter_ns(), time.thread_time_ns(), 0, 0]
        stack.append(frame)
        failed = 0
        try:
            yield
        except BaseException:
            failed = 1
            raise
        finally:
            wall = time.perf_counter_ns() - frame[1]
            cpu = time.thread_time_ns() - frame[2]
            stack.pop()
            if stack:
                stack[-1][3] += wall
                stack[-1][4] += cpu
            with self.lock:
                entry = self.phases.setdefault(name, Counter())
                entry.update(calls=1, failures=failed, wall_ns=wall, cpu_ns=cpu,
                    exclusive_wall_ns=max(0, wall - frame[3]),
                    exclusive_cpu_ns=max(0, cpu - frame[4]))

    def wrap(self, name, function):
        @wraps(function)
        def measured(*args, **kwargs):
            with self.phase(name):
                return function(*args, **kwargs)
        return measured

    def sql(self, statement):
        if self.enabled:
            kind = statement.lstrip().split(None, 1)[0].upper()
            if kind in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'PRAGMA'):
                self.add(**{f'sql_{kind.lower()}': 1})

    def finish(self):
        self.enabled = False
        elapsed = (time.perf_counter_ns() - self.wall_start) / 1e9
        cpu = (time.process_time_ns() - self.cpu_start) / 1e9
        with self.lock:
            phases = {name: {'calls': value['calls'], 'failures': value['failures'],
                'thread_wall_seconds': value['wall_ns'] / 1e9,
                'thread_cpu_seconds': value['cpu_ns'] / 1e9,
                'exclusive_thread_wall_seconds': value['exclusive_wall_ns'] / 1e9,
                'exclusive_thread_cpu_seconds': value['exclusive_cpu_ns'] / 1e9}
                for name, value in self.phases.items()}
            counters = dict(self.counters)
        exclusive_cpu = sum(value['exclusive_thread_cpu_seconds'] for value in phases.values())
        return {'elapsed_seconds': elapsed, 'process_cpu_seconds': cpu,
            'unattributed_process_cpu_seconds': max(0, cpu - exclusive_cpu),
            'phases': dict(sorted(phases.items(), key=lambda item: -item[1]['exclusive_thread_cpu_seconds'])),
            'counters': counters, 'phase_wall_times_overlap_across_threads': True,
            'exclusive_phase_cpu_is_additive': True,
            'unattributed_includes': 'HTTP parsing, unwrapped server work and instrumentation overhead',
            'instrumentation_overhead_subtracted': False}


class CountingHash:
    def __init__(self, inner, measurements):
        self.inner, self.measurements = inner, measurements
        self.category = None

    def count(self, data):
        # Upload digests are constructed on the event loop before being handed
        # to a copy worker. Classify on first use, where the actual work runs.
        if self.category is None:
            stack = getattr(self.measurements.local, 'stack', [])
            phases = {frame[0] for frame in stack}
            self.category = 'archive' if 'archive_hash' in phases else (
                'incoming' if 'incoming_copy_hash' in phases else 'other')
            self.measurements.add(**{f'{self.category}_sha256_streams': 1})
        self.measurements.add(**{f'{self.category}_sha256_bytes': len(data)})

    def update(self, data):
        self.inner.update(data)
        self.count(data)

    def copy(self):
        result = CountingHash(self.inner.copy(), self.measurements)
        result.category = self.category
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


class HashProxy:
    def __init__(self, measurements):
        self.measurements = measurements

    def sha256(self, *args, **kwargs):
        result = CountingHash(hashlib.sha256(*args, **kwargs), self.measurements)
        data = args[0] if args else kwargs.get('string')
        if data is not None:
            result.count(data)
        return result

    def __getattr__(self, name):
        return getattr(hashlib, name)


def instrument(measurements):
    from app import compression, database, fits_metadata, imglib, ingest
    # Replace module bindings, not the process-global hashlib/sqlite3 modules.
    ingest.hashlib = HashProxy(measurements)
    class Connection(sqlite3.Connection):
        def commit(self):
            with measurements.phase('database_commit'):
                return super().commit()
        def execute(self, *args, **kwargs):
            with measurements.phase('sqlite_execute'):
                return super().execute(*args, **kwargs)
    class SQLiteProxy:
        def connect(self, *args, **kwargs):
            result = sqlite3.connect(*args, factory=Connection, **kwargs)
            result.set_trace_callback(measurements.sql)
            return result
        def __getattr__(self, name):
            return getattr(sqlite3, name)
    database.sqlite3 = SQLiteProxy()
    original_hash = ingest._fingerprint_file
    def archive_hash(*args, **kwargs):
        with measurements.phase('archive_hash'):
            result = original_hash(*args, **kwargs)
            if result is not None:
                measurements.add(archive_hashes_completed=1, completed_archive_bytes=result[1])
            else:
                measurements.add(archive_hashes_incomplete=1)
            return result
    ingest._fingerprint_file = archive_hash
    for attribute in ('_candidate_rows', '_alias_rows'):
        if not hasattr(ingest, attribute):
            continue  # The same controller can measure the earlier baseline.
        original = getattr(ingest, attribute)
        def fetched(*args, _original=original, _name=attribute, **kwargs):
            with measurements.phase(_name.lstrip('_')):
                rows = _original(*args, **kwargs)
                measurements.add(**{_name.lstrip('_') + '_fetched': len(rows)})
                return rows
        setattr(ingest, attribute, fetched)
    for attribute, phase in (
        ('_dedup_fingerprint', 'candidate_validation'), ('_find_duplicate', 'duplicate_lookup'),
        ('_known_duplicate', 'positive_preflight'),
        ('_copy_spooled_upload', 'incoming_copy_hash'), ('_finalize_import', 'publication'),
        ('_prepare_stage', 'stage_create'), ('_remove_stage', 'stage_cleanup'),
        ('_header_session', 'session_assignment'), ('_archive_target', 'archive_destination')):
        setattr(ingest, attribute, measurements.wrap(phase, getattr(ingest, attribute)))
    prepare = ingest.DedupBatch.prepare
    def batch_prepare(cls, *args, **kwargs):
        with measurements.phase('batch_prepare'):
            return prepare(*args, **kwargs)
    ingest.DedupBatch.prepare = classmethod(batch_prepare)
    negative = ingest.DedupBatch.negative
    def batch_negative(self, *args, **kwargs):
        result = negative(self, *args, **kwargs)
        measurements.add(candidate_visits=1, pinned_negative_hits=int(result))
        return result
    ingest.DedupBatch.negative = batch_negative
    fits_metadata.read_metadata = measurements.wrap('fits_metadata', fits_metadata.read_metadata)
    imglib.CatalogResolver.__init__ = measurements.wrap('catalog_index', imglib.CatalogResolver.__init__)
    imglib.CatalogResolver.resolve = measurements.wrap('resolver_cache_miss', imglib.CatalogResolver.resolve)
    for attribute in ('all_objects', 'catalog_revision', 'image', 'images_for_object'):
        setattr(database.Database, attribute,
                measurements.wrap(f'database_{attribute}', getattr(database.Database, attribute)))
    compression.native = measurements.wrap('native_tool', compression.native)
    compression.encode_verified = measurements.wrap('rice_encode_verify', compression.encode_verified)


def child(root, port, seed_count=0):
    import uvicorn
    from app import paths
    root = root.resolve()
    bench.require(root.is_relative_to(harness.SCRATCH.resolve()), 'Source profile must use owned browser scratch')
    paths.app_base_dir = lambda: root
    paths.default_archive_dir = lambda: root / 'archive'
    paths.window_placement_path = lambda: root / 'local-app-data/window-placement.json'
    logging.basicConfig(filename=root / 'child.log', level=logging.WARNING, encoding='utf-8')
    measurements = Measurements()
    instrument(measurements)
    from app.database import Database
    from app.server import create_app
    (root / 'data').mkdir()
    db = Database(root / 'data/nightseam.db')
    db.update_settings({'image_dir': str(root / 'archive')})
    if seed_count:
        from scripts.profile_populated_import import seed
        seed(db, root / 'archive', seed_count, root / 'seed-manifest.json')
    app = create_app(db)
    runtime = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        log_level='warning', access_log=False))
    @app.post('/__import_profile/start')
    async def start():
        measurements.start()
        return {'started': True}
    @app.post('/__import_profile/finish')
    async def finish():
        result = measurements.finish()
        (root / 'profile.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result
    @app.post('/__import_profile/shutdown')
    async def shutdown():
        runtime.should_exit = True
        return {'stopping': True}
    # The normal app's final static mount is a catch-all. Only this private
    # bootstrap adds control routes; place those before that mount.
    static_mount = next(route for route in app.router.routes if getattr(route, 'name', None) == 'static')
    app.router.routes.remove(static_mount)
    app.router.routes.append(static_mount)
    try:
        runtime.run()
    finally:
        db.conn.close()


class SourceServer(harness.DesktopServer):
    def __init__(self, stderr_log=None, seed_count=0):
        super().__init__(ROOT / 'main.py', seed=False)
        self.stderr_log = stderr_log or ROOT / 'logs/import-profile-child-stderr.log'
        self.seed_count = seed_count

    def _start(self, timeout):
        self.port = harness.free_port()
        environment = dict(os.environ)
        for name in ('TEMP', 'TMP', 'TMPDIR'):
            environment[name] = str(self.tmp)
        environment['LOCALAPPDATA'] = str(self.tmp / 'local-app-data')
        environment['WEBVIEW2_USER_DATA_FOLDER'] = str(self.tmp / 'webview2')
        self.stderr_log.parent.mkdir(parents=True, exist_ok=True)
        with self.stderr_log.open('w', encoding='utf-8') as stream:
            self.tree = harness.ProcessTree([sys.executable, str(Path(__file__).resolve()),
                '--child', str(self.tmp), '--port', str(self.port), '--seed-count', str(self.seed_count)],
                cwd=str(ROOT), env=environment,
                stdout=stream, stderr=harness.subprocess.STDOUT)
        self.proc = self.tree.proc
        self.base = f'http://127.0.0.1:{self.port}/'
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f'Source profile exited with {self.proc.returncode}')
            try:
                state = self.api('api/state')
                bench.require(Path(state['image_dir']).resolve() == (self.tmp / 'archive').resolve(),
                              'Source profile did not select its private archive')
                return self.base
            except OSError:
                time.sleep(.1)
        raise RuntimeError('Source profile did not start')


def run(args):
    import httpx
    server = SourceServer(args.output.parent / 'import-profile-child-stderr.log')
    report = {'status': 'running', 'os': platform.platform(), 'cpu': platform.processor(),
        'logical_cpus': os.cpu_count(), 'source_commit': os.environ.get('GITHUB_SHA'),
        'mode': 'instrumented source server; real loopback HTTP; private stores',
        'packaged_baseline_comparable': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.start(timeout=60)
        bench.require(shutil.disk_usage(server.tmp).free >= args.count * bench.FILE_BYTES * 2.5,
                      'Insufficient private disk space for profile fixtures/archive')
        sources = bench.fixtures(server.tmp / 'inputs', args.count)
        with httpx.Client(base_url=server.base, trust_env=False, timeout=300) as client:
            def started():
                bench.payload(client.post('/__import_profile/start'))
            def finished():
                report['profile'] = bench.payload(client.post('/__import_profile/finish'))
            report['import'] = bench.benchmark(client, server.tmp / 'archive',
                server.tmp / 'data/nightseam.db', sources, args.batch_size,
                on_import_started=started, on_import_finished=finished)
            report['import']['archive_hash_read_counts_measured'] = True
            bench.payload(client.post('/__import_profile/shutdown'))
        server.proc.wait(timeout=20)
        bench.require(server.proc.returncode == 0, 'Source profiler did not exit cleanly')
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if server.tmp is not None and (server.tmp / 'child.log').is_file():
            shutil.copyfile(server.tmp / 'child.log', args.output.parent / 'import-profile-child.log')
        raise
    finally:
        try:
            server.stop()
            report['owned_process_and_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--seed-count', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--count', type=int, default=512)
    parser.add_argument('--batch-size', type=int, default=24)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/windows-import-profile.json')
    args = parser.parse_args()
    if args.child is not None:
        if not args.port:
            parser.error('a private child port is required')
        child(args.child, args.port, args.seed_count)
    else:
        if args.count < 3 or not 1 <= args.batch_size <= 24:
            parser.error('count must be at least 3 and batch size must be 1..24')
        run(args)


if __name__ == '__main__':
    main()
