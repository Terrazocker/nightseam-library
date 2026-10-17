"""Compare two packaged builds sequentially on one Windows machine.

Alternating AB/BA pairs use fresh private stores and the existing byte/dedup/edit
audits. Generation/audits are outside timing. Caches are not flushed; timings
are observations, never correctness thresholds or cold-cache guarantees.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import bench_windows_import as bench


def order(pairs):
    return [variant for pair in range(pairs) for variant in (
        ('baseline', 'candidate') if pair % 2 == 0 else ('candidate', 'baseline'))]


def summarize(runs):
    summary = {}
    for variant in ('baseline', 'candidate'):
        selected = [run['report']['import'] for run in runs if run['variant'] == variant]
        times = [item['elapsed_seconds'] for item in selected]
        rates = [item['mib_per_second'] for item in selected]
        summary[variant] = dict(samples=len(times), seconds=times,
            median_seconds=statistics.median(times), min_seconds=min(times), max_seconds=max(times),
            median_mib_per_second=statistics.median(rates))
    summary['median_elapsed_reduction_percent'] = 100 * (
        1 - summary['candidate']['median_seconds'] / summary['baseline']['median_seconds'])
    return summary


def validate(report, count, batch_size, expected_hash):
    bench.require(report.get('status') == 'passed' and report.get('owned_process_and_scratch_cleanup'),
        'Comparison child failed or did not confirm owned cleanup')
    bench.require(report.get('executable_sha256') == expected_hash, 'Comparison ran the wrong executable')
    imported = report['import']
    bench.require(imported['files'] == imported['source_and_archive_hashes_verified'] == count
        and imported['batch_size'] == batch_size and imported['bytes'] == count * bench.FILE_BYTES,
        'Comparison child measured a different workload')
    bench.require(imported['duplicate_class_session_location_preserved']
        and imported['same_size_restored_mtime_edit_detected'] and imported['pending_or_staging'] == 0
        and imported['elapsed_seconds'] > 0, 'Comparison child failed its byte/identity/edit audits')
    bench.require(all(report['restricted_runtime_rice'].get(key) is True for key in (
        'safe_rice_ingest', 'native_uncompress', 'exact_original_bytes')), 'Comparison child failed Rice restoration')


def run(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    directory = args.output.parent / (args.output.stem + '-runs')
    # Never overwrite partial evidence or mix reports from separate comparisons.
    directory.mkdir()
    executables = {'baseline': args.baseline_exe.resolve(), 'candidate': args.candidate_exe.resolve()}
    hashes = {name: bench.digest(path) for name, path in executables.items()}
    report = dict(status='running', os=platform.platform(), cpu=platform.processor(),
        logical_cpus=os.cpu_count(), baseline_commit=args.baseline_commit,
        candidate_commit=args.candidate_commit, executable_sha256=hashes,
        order=order(args.pairs), same_controller_and_machine=True, fresh_stores_each_run=True,
        cold_cache_controlled=False, timing_threshold_enforced=False, runs=[])
    try:
        for position, variant in enumerate(report['order'], 1):
            output = directory / f'{position:02d}-{variant}.json'
            log = output.with_suffix('.log')
            environment = dict(os.environ, GITHUB_SHA=getattr(args, variant + '_commit'))
            tree = None
            try:
                with log.open('w', encoding='utf-8') as stream:
                    tree = bench.harness.ProcessTree([sys.executable, str(ROOT / 'scripts/bench_windows_import.py'),
                        '--exe', str(executables[variant]), '--count', str(args.count),
                        '--batch-size', str(args.batch_size), '--output', str(output)],
                        cwd=ROOT, env=environment, stdout=stream, stderr=bench.harness.subprocess.STDOUT)
                    code = tree.proc.wait(timeout=900)
                bench.require(code == 0, f'Comparison {position} ({variant}) failed; see {log}')
            finally:
                if tree is not None:
                    tree.stop()
            current = json.loads(output.read_text(encoding='utf-8'))
            validate(current, args.count, args.batch_size, hashes[variant])
            report['runs'].append(dict(position=position, variant=variant, report=current))
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(f'{position}: {variant} {current["import"]["elapsed_seconds"]:.3f}s; byte/edit/cleanup audits passed', flush=True)
        report.update(status='passed', summary=summarize(report['runs']), owned_process_cleanup=True)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-exe', type=Path, required=True)
    parser.add_argument('--candidate-exe', type=Path, required=True)
    parser.add_argument('--baseline-commit', required=True)
    parser.add_argument('--candidate-commit', required=True)
    parser.add_argument('--pairs', type=int, default=2)
    parser.add_argument('--count', type=int, default=512)
    parser.add_argument('--batch-size', type=int, default=24)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/windows-import-comparison.json')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('packaged comparison requires Windows')
    if args.pairs < 2 or args.count < 3 or not 1 <= args.batch_size <= 24:
        parser.error('use at least two pairs, at least three files and batch size 1..24')
    run(args)


if __name__ == '__main__':
    main()
