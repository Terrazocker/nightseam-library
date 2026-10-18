"""Compare library grouping against a Git revision using synthetic metadata.

This measures grouping, not HTTP throughput or disk IO. Each implementation sees
identical 12k-row catalogs and must return exactly the same JSON. Timings are
local observations with uncontrolled CPU/cache conditions, not CI thresholds.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import imglib


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def run(reference, output, repeats):
    baseline_source = git('show', '--end-of-options', reference + ':app/imglib.py')
    baseline = types.ModuleType('app._library_grouping_baseline')
    baseline.__package__ = 'app'
    exec(compile(baseline_source, '<Git library grouping baseline>', 'exec'), baseline.__dict__)
    catalog = [dict(id=f'ngc{10000 + i}', primary_name=f'Target {i}',
        ngcic=f'NGC {10000 + i}', ra_hours=1., dec_deg=2., otype='galaxy', size_am='1x2')
        for i in range(12000)]
    def image(number, oid, name):
        return dict(id=number, file=f'private/light/{number}.fits', orig_name=f'{number}.fits',
            object_id=oid, object_name=name, exposure=30., imagetyp='light', filter='L',
            date_obs=None, ra_hours=None, dec_deg=None, ingested_at='2026-10-18')
    workloads = {
        'few_current_objects': [image(i, catalog[i]['id'], catalog[i]['ngcic']) for i in range(4)],
        'many_current_objects': [image(i, catalog[i * 12]['id'], catalog[i * 12]['ngcic']) for i in range(1000)],
        'obsolete_ids': [image(i, f'old-{i}', catalog[i * 180]['ngcic']) for i in range(64)],
        'intentionally_unresolved': [image(i, None, f'Private target {i}') for i in range(1000)],
        'empty_library': [],
    }
    report = dict(baseline_commit=git('rev-parse', reference + '^{commit}'),
        candidate_commit=git('rev-parse', 'HEAD'), candidate_dirty=bool(git('status', '--porcelain')),
        candidate_imglib_sha256=hashlib.sha256((ROOT / 'app/imglib.py').read_bytes()).hexdigest(),
        python=platform.python_version(), platform=platform.platform(), catalog_rows=len(catalog),
        repeats=repeats, cpu_and_cache_controlled=False, scope='group_library only; synthetic metadata, no archives',
        cases={})
    for name, images in workloads.items():
        expected = baseline.group_library(images, catalog)
        assert imglib.group_library(images, catalog) == expected, name + ': grouping changed'
        expected_json = json.dumps(expected, sort_keys=True).encode('utf-8')
        timings = dict(baseline=[], candidate=[])
        implementations = dict(baseline=baseline.group_library, candidate=imglib.group_library)
        for iteration in range(repeats):
            order = ['baseline', 'candidate'] if iteration % 2 == 0 else ['candidate', 'baseline']
            for label in order:
                gc.collect()  # Outside timing; do not retain prior resolver snapshots.
                started = time.perf_counter()
                result = implementations[label](images, catalog)
                timings[label].append(time.perf_counter() - started)
                assert result == expected, name + ': output changed between runs'
        medians = {key: statistics.median(values) for key, values in timings.items()}
        report['cases'][name] = dict(images=len(images), output_sha256=hashlib.sha256(expected_json).hexdigest(),
            output_equal=True, seconds=timings, median_seconds=medians,
            observed_speedup=medians['baseline'] / medians['candidate'])
        print(f'{name}: baseline {medians["baseline"]:.6f}s, candidate {medians["candidate"]:.6f}s; identical output')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default='78e6a559302c37ab53b49abbb1e9a98341bee245')
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/library-grouping-benchmark.json')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    run(args.baseline_ref, args.output.resolve(), args.repeats)


if __name__ == '__main__':
    main()
