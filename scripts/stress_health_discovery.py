"""Measure discovery memory with lazy synthetic fanout in isolated processes.

Exercises the real scanner/spool with no indexed rows and a tiny report limit.
Filesystem enumeration/stat of synthetic children are test doubles; real path,
link, mutation and permission behavior is covered by archive-health tests.
RSS includes tracing/observer overhead. This is a resource gate, not scan IO
throughput or a bound on the existing indexed-record snapshot/known-path set.
"""
from __future__ import annotations

import argparse
import gc
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
from scripts.stress_dedup import resources
from scripts.clean_workspace import remove_tree


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def measure(count, reference, output):
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
    try:
        root = scratch / 'archive'
        root.mkdir()
        spools = scratch / 'spools'
        spools.mkdir()
        discovery_spool.logs_dir = lambda: spools
        scanner.MAX_FINDINGS = 16
        def entries(root, folder, identity, checkpoint):
            if not folder:
                for i in range(count):
                    checkpoint()
                    yield f'file-{i:09d}-' + 'x' * 160 + '.fits', False, False
                    yield f'dir-{i:09d}-' + 'x' * 160, False, True
        scanner._directory_entries = entries
        target = types.SimpleNamespace(stat=lambda: None)
        scanner._contained = lambda *args: target
        db = types.SimpleNamespace(archive_lock=threading.Lock(), integrity_snapshot=lambda: (str(root), []))
        resources()  # Warm observer classes/import caches before measurement.
        gc.collect()
        before = resources()
        peak_rss = before['rss_bytes']
        spool_bytes = 0
        def observe(update):
            nonlocal peak_rss, spool_bytes
            peak_rss = max(peak_rss, resources()['rss_bytes'])
            if update['phase'] == 'checking':
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
        require(result['total'] == result['processed'] == result['unverified'] == count, 'Discovery counts differ')
        require(result['finding_counts'] == {'untracked': count}, 'Finding counts differ')
        require(result['omitted_findings'] == count - 16, 'Report limit lost omitted counts')
        expected = [f'file-{i:09d}-' + 'x' * 160 + '.fits' for i in range(16)]
        require([item['path'] for item in result['findings']] == expected, 'Finding order differs')
        require(not list(spools.iterdir()), 'Owned spool remains after scan')
        require(db.archive_lock.acquire(blocking=False), 'Archive lock remains held')
        db.archive_lock.release()
        after = resources()
        require(after['handles'] <= before['handles'], 'Discovery leaked descriptors/handles')
        report.update(status='passed', before=before, after=after, peak_rss_bytes=peak_rss,
            observed_rss_growth_bytes=max(0, peak_rss - before['rss_bytes']), traced_peak_bytes=peak_heap,
            spool_bytes=spool_bytes, elapsed_seconds=elapsed, processed=result['processed'],
            omitted_findings=result['omitted_findings'], retained_findings=16, finding_order_preserved=True)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if tracing:
            tracemalloc.stop()
        try:
            remove_tree(scratch)
            report['owned_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def run(args):
    report = dict(status='running', os=platform.platform(), indexed_rows=0, report_limit=16,
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
    print('Archive-health discovery: complete counts/order, bounded heap/RSS, no leaked handles or scratch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', type=int, nargs='+', default=[5000, 100000])
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/resources-health-discovery.json')
    parser.add_argument('--baseline-ref')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--count', type=int)
    args = parser.parse_args()
    if (args.worker and (args.count is None or args.count < 16)) or any(count < 16 for count in args.counts):
        parser.error('Use at least 16 synthetic files/directories')
    args.output = args.output.resolve()
    if args.worker:
        measure(args.count, args.baseline_ref, args.output)
    else:
        run(args)


if __name__ == '__main__':
    main()
