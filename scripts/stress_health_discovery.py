"""Measure discovery memory with lazy synthetic fanout in isolated processes.

Exercises the real scanner/spool with a tiny report limit. --indexed uses a real
populated SQLite catalog and its consistent snapshot reader; the default has
no indexed rows and tests file/directory fanout.
Filesystem enumeration/stat of synthetic children are test doubles; real path,
link, mutation and permission behavior is covered by archive-health tests.
RSS includes tracing/observer overhead. This is a resource gate, not scan IO
throughput measurement. Retained findings and baseline-source output still cost
memory in proportion to their payload, independently of indexed row count.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import archive_health, discovery_spool
from app.database import Database
from scripts.stress_dedup import resources
from scripts.clean_workspace import remove_tree


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def measure(count, reference, output, indexed=False):
    scanner = archive_health
    if reference:
        source = subprocess.check_output(['git', 'show', '--end-of-options', reference + ':app/archive_health.py'],
            cwd=ROOT, text=True)
        scanner = types.ModuleType('app._health_discovery_baseline')
        scanner.__package__ = 'app'
        exec(compile(source, '<Git archive-health baseline>', 'exec'), scanner.__dict__)
    (ROOT / 'logs').mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='health-memory-', dir=ROOT / 'logs'))
    (scratch / '.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
    report = dict(status='running', count=count, baseline_ref=reference, source_commit=os.environ.get('GITHUB_SHA'))
    tracing = False
    db = None
    snapshot_closed = not indexed
    snapshot_seconds = None
    try:
        report['runtime_source_sha256'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(archive_health.__file__), Path(discovery_spool.__file__), Path(sys.modules[Database.__module__].__file__))}
        report['scanner_source_sha256'] = hashlib.sha256(source.encode('utf-8')).hexdigest() if reference else report['runtime_source_sha256']['app/archive_health.py']
        root = scratch / 'archive'
        root.mkdir()
        spools = scratch / 'spools'
        spools.mkdir()
        discovery_spool.logs_dir = lambda: spools
        scanner.MAX_FINDINGS = 16
        def filename(i):
            return f'file-{i:09d}-' + 'x' * 160 + '.fits'
        def entries(root, folder, identity, checkpoint):
            require(snapshot_closed, 'Source snapshot remains open during filesystem enumeration')
            if not folder:
                for i in range(count):
                    checkpoint()
                    yield filename(i), False, False
                    if not indexed:
                        yield f'dir-{i:09d}-' + 'x' * 160, False, True
                if indexed:
                    yield 'untracked.fits', False, False
        scanner._directory_entries = entries
        target = types.SimpleNamespace(stat=lambda: None)
        scanner._contained = lambda *args: target
        if indexed:
            db = Database(scratch / 'catalog.sqlite')
            db.set_setting('image_dir', str(root))
            db.conn.executemany('INSERT INTO images(file,orig_name,object_name,ingested_at) VALUES(?,?,?,?)',
                ((filename(i), filename(i), 'M31', 'today') for i in range(count)))
            db.conn.execute("INSERT INTO image_integrity SELECT id,?,1,1,CASE WHEN id%2=0 THEN 'import' ELSE 'reindex' END,'today' FROM images", ('a'*64,))
            db.conn.commit()
            scanner._inspect = lambda *args: (types.SimpleNamespace(st_size=1, st_mtime_ns=1), None)
            if reference:
                snapshot = db.integrity_snapshot
                def capture():
                    nonlocal snapshot_closed, snapshot_seconds
                    started = time.perf_counter()
                    result = snapshot()
                    snapshot_seconds = time.perf_counter() - started
                    snapshot_closed = True
                    return result
                db.integrity_snapshot = capture
            else:
                snapshot = db.integrity_scan_rows
                @contextmanager
                def capture(checkpoint):
                    nonlocal snapshot_closed, snapshot_seconds
                    started = time.perf_counter()
                    last_sample = started
                    def observed_checkpoint():
                        nonlocal peak_rss, last_sample
                        checkpoint()
                        if time.perf_counter() - last_sample >= .2:
                            peak_rss = max(peak_rss, resources()['rss_bytes'])
                            last_sample = time.perf_counter()
                    try:
                        with snapshot(observed_checkpoint) as rows:
                            yield rows
                    finally:
                        snapshot_seconds = time.perf_counter() - started
                        snapshot_closed = True
                db.integrity_scan_rows = capture
        else:
            @contextmanager
            def scan_rows(checkpoint):
                checkpoint()
                yield str(root), iter(())
            db = types.SimpleNamespace(archive_lock=threading.Lock(), integrity_snapshot=lambda: (str(root), []),
                integrity_scan_rows=scan_rows)
        resources()  # Warm observer classes/import caches before measurement.
        gc.collect()
        before = resources()
        peak_rss = before['rss_bytes']
        spool_bytes = 0
        def observe(update):
            nonlocal peak_rss, spool_bytes
            peak_rss = max(peak_rss, resources()['rss_bytes'])
            spool_bytes = max(spool_bytes, sum(p.stat().st_size for p in spools.glob('*/discovery.sqlite')))
        tracemalloc.start()
        tracing = True
        started = time.perf_counter()
        result = scanner.scan_archive(db, 'quick', threading.Event(), observe)
        elapsed = time.perf_counter() - started
        _, peak_heap = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        tracing = False
        require(result['status'] == 'completed', 'Synthetic discovery did not finish')
        total = count + 1 if indexed else count
        require(result['total'] == result['processed'] == result['unverified'] == total, 'Discovery counts differ')
        require(result['finding_counts'] == {'untracked': 1 if indexed else count}, 'Finding counts differ')
        require(result['omitted_findings'] == (0 if indexed else count - 16), 'Report limit lost omitted counts')
        expected = ['untracked.fits'] if indexed else [filename(i) for i in range(16)]
        if indexed:
            require(result['metadata_unchanged'] == count, 'Indexed metadata count differs')
            require(result['baseline_sources'] == {'import': count//2, 'reindex': count-count//2}, 'Baseline counts differ')
        require([item['path'] for item in result['findings']] == expected, 'Finding order differs')
        require(not list(spools.iterdir()), 'Owned spool remains after scan')
        require(db.archive_lock.acquire(blocking=False), 'Archive lock remains held')
        db.archive_lock.release()
        after = resources()
        require(after['handles'] <= before['handles'], 'Discovery leaked descriptors/handles')
        report.update(status='passed', before=before, after=after, peak_rss_bytes=peak_rss,
            observed_rss_growth_bytes=max(0, peak_rss - before['rss_bytes']), traced_peak_bytes=peak_heap,
            spool_bytes=spool_bytes, elapsed_seconds=elapsed, processed=result['processed'],
            omitted_findings=result['omitted_findings'], retained_findings=len(expected), finding_order_preserved=True,
            indexed_rows=count if indexed else 0, source_snapshot_closed_before_filesystem=snapshot_closed,
            snapshot_capture_seconds=snapshot_seconds)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if tracing:
            tracemalloc.stop()
        try:
            if indexed and db is not None:
                db.conn.close()
            remove_tree(scratch)
            report['owned_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def run(args):
    report = dict(status='running', os=platform.platform(), indexed_mode=args.indexed, report_limit=16,
        source_commit=os.environ.get('GITHUB_SHA'), synthetic_filesystem=True,
        rss_includes_tracing=True, discovery_cache_kib=discovery_spool.CACHE_KIB, cases=[])
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        for reference in ([args.baseline_ref, None] if args.baseline_ref else [None]):
            for count in args.counts:
                child = args.output.with_name(args.output.stem + f'-{"baseline" if reference else "candidate"}-{count}.json')
                command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--count', str(count), '--output', str(child)]
                if reference:
                    command += ['--baseline-ref', reference]
                if args.indexed:
                    command += ['--indexed']
                subprocess.run(command, cwd=ROOT, check=True, timeout=300)
                case = json.loads(child.read_text(encoding='utf-8'))
                report['cases'].append(case)
                if not reference:
                    require(case['traced_peak_bytes'] < 2 << 20, 'Discovery Python peak exceeds 2 MiB')
                    require(case['observed_rss_growth_bytes'] < 12 << 20, 'Instrumented discovery RSS growth exceeds 12 MiB')
        candidate = [case for case in report['cases'] if not case['baseline_ref']]
        require(max(c['observed_rss_growth_bytes'] for c in candidate) -
                min(c['observed_rss_growth_bytes'] for c in candidate) < 8 << 20,
                'Discovery RSS grows with fanout instead of its fixed cache')
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    label = 'indexed snapshot' if args.indexed else 'discovery'
    print(f'Archive-health {label}: complete counts/order, bounded heap/RSS, no leaked handles or scratch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', type=int, nargs='+', default=[5000, 100000])
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/resources-health-discovery.json')
    parser.add_argument('--baseline-ref')
    parser.add_argument('--indexed', action='store_true', help='Use a real populated SQLite catalog and known-path lookups')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--count', type=int)
    args = parser.parse_args()
    if (args.worker and (args.count is None or args.count < 16)) or any(count < 16 for count in args.counts):
        parser.error('Use at least 16 synthetic files/directories')
    args.output = args.output.resolve()
    if args.worker:
        measure(args.count, args.baseline_ref, args.output, args.indexed)
    else:
        run(args)


if __name__ == '__main__':
    main()
