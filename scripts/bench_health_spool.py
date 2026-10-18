"""Compare actual historical/candidate spool storage, independent of scanners.

Synthetic raw paths and directory fanout; not filesystem/physical IO throughput.
Each case runs in a fresh process with tracing overhead. Exact ordered multiset,
LIFO traversal, cleanup and source hashes accompany disk/time observations.
"""
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tracemalloc
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import discovery_spool


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def measure(count, reference, output):
    module = discovery_spool
    source = Path(discovery_spool.__file__).read_bytes()
    if reference:
        source = subprocess.check_output(['git', 'show', '--end-of-options', reference+':app/discovery_spool.py'], cwd=ROOT)
        module = types.ModuleType('app._benchmark_spool')
        module.__package__ = 'app'
        exec(compile(source, '<historical discovery spool>', 'exec'), module.__dict__)
    report = {'status': 'running', 'count': count, 'baseline_ref': reference,
              'spool_source_sha256': hashlib.sha256(source).hexdigest()}
    (ROOT/'logs').mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='spool-benchmark-', dir=ROOT/'logs') as scratch:
            root = Path(scratch)
            (root/'.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
            module.logs_dir = lambda: root
            def path(i):
                return f'file-{i:09d}-' + 'x'*160 + '.fits'
            tracemalloc.start()
            try:
                started = time.perf_counter()
                with module.DiscoverySpool() as spool:
                    for i in reversed(range(count)):
                        spool.push_directory(f'dir-{i:09d}-' + 'x'*160)
                        spool.add_untracked(path(i))
                        if i % 17 == 0:
                            spool.add_untracked(path(i))
                    with closing(spool.execute('PRAGMA page_count')) as cursor:
                        pages = cursor.fetchone()[0]
                    with closing(spool.execute('PRAGMA page_size')) as cursor:
                        page_size = cursor.fetchone()[0]
                    report['logical_spool_bytes'] = pages*page_size
                    for i in range(count):
                        require(spool.pop_directory() == f'dir-{i:09d}-' + 'x'*160, 'Directory LIFO differs')
                    digest = hashlib.sha256()
                    observed = duplicates = 0
                    previous = ''
                    with closing(spool.untracked()) as ordered:
                        for value in ordered:
                            require(value >= previous, 'Path ordering differs')
                            duplicates += value == previous
                            previous = value
                            raw = value.encode('utf-8', 'surrogatepass')
                            digest.update(len(raw).to_bytes(4, 'big'))
                            digest.update(raw)
                            observed += 1
                    expected_duplicates = (count-1)//17+1
                    require(observed == spool.untracked_count == count+expected_duplicates, 'Complete counts differ')
                    require(duplicates == expected_duplicates and spool.pending_count == 0, 'Duplicates/directories differ')
                    report.update(ordered_sha256=digest.hexdigest(), observations=observed, duplicates=duplicates,
                                  traced_peak_bytes=tracemalloc.get_traced_memory()[1])
                report['elapsed_seconds'] = time.perf_counter()-started
                require([p.name for p in root.iterdir()] == ['.owner-pid'], 'Owned spool remains')
            finally:
                tracemalloc.stop()
        report.update(status='passed', owned_scratch_cleanup=True)
    except BaseException as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--counts', nargs='+', type=int, default=[5000, 100000])
    parser.add_argument('--output', type=Path, default=ROOT/'logs/health-spool-comparison.json')
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if any(count < 1 for count in args.counts):
        parser.error('Counts must be positive')
    args.output = args.output.resolve()
    if args.worker:
        measure(args.counts[0], args.baseline_ref, args.output)
        return
    report = {'status': 'running', 'cases': [], 'synthetic_paths': True, 'tracing_enabled': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for reference in ([args.baseline_ref, None] if args.baseline_ref else [None]):
            for count in args.counts:
                child = args.output.with_name(args.output.stem+f'-{"baseline" if reference else "candidate"}-{count}.json')
                command = [sys.executable, __file__, '--worker', '--counts', str(count), '--output', str(child)]
                if reference:
                    command += ['--baseline-ref', reference]
                subprocess.run(command, cwd=ROOT, check=True, timeout=300)
                report['cases'].append(json.loads(child.read_text(encoding='utf-8')))
        for count in args.counts:
            cases = [c for c in report['cases'] if c['count']==count]
            require(len({c['ordered_sha256'] for c in cases}) == 1, 'Ordered multiset differs between implementations')
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('Spool comparison passed: identical ordered observations, LIFO traversal and cleanup')


if __name__ == '__main__':
    main()
