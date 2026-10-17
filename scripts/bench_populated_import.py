"""Compare packaged large-exposure imports in private populated libraries.

Run sequential ABBA samples on one Windows runner. Caches are not flushed and
synthetic sensor data is not an observing-night or cold-disk throughput promise.
The old empty-library benchmark remains independent.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import ctypes
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import bench_windows_import as bench
from scripts.compare_import import order
from scripts.profile_populated_import import image as small_image
from app.database import Database
from app import ingest
from process_tree import _Accounting, _ExtendedLimits

ROWS = (2048, 4096, 8192)  # 2048 columns × uint16: 8, 16 and 32 MiB + FITS padding.
PLAIN_OBJECT, RICE_OBJECT = 'NSBENCHPLAIN', 'NSBENCHRICE'


@lru_cache(maxsize=3)
def template(rows):
    import numpy as np
    from astropy.io import fits
    pixels = np.random.default_rng(20261017 + rows).integers(3500, 4500, (rows, 2048), dtype=np.uint16)
    header = fits.Header({'OBJECT': PLAIN_OBJECT, 'DATE-OBS': '2026-08-12T22:00:00',
        'EXPTIME': 60, 'SITELAT': 52.52, 'SITELONG': 13.405,
        'IMAGETYP': 'LIGHT', 'FILTER': 'L', 'INSTRUME': 'Synthetic sensor noise'})
    stream = io.BytesIO()
    fits.PrimaryHDU(pixels, header=header).writeto(stream)
    return stream.getvalue()


def exposure(number, rows, *, rice=False):
    from astropy.io import fits
    raw = bytearray(template(rows))
    raw[2880:2888] = struct.pack('>Q', number)
    if rice:
        offset = next(i for i in range(0, 2880, 80) if raw[i:i + 8] == b'OBJECT  ')
        raw[offset:offset + 80] = fits.Card('OBJECT', RICE_OBJECT).image.encode('ascii')
    return raw


def write_input(folder, number, rows, *, rice=False):
    raw = exposure(number, rows, rice=rice)
    path = folder / f'large-{number:07d}.fits'
    path.write_bytes(raw)
    return dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))


def seed(root, small_count, large_count, rows=ROWS):
    """Only run before the private executable starts, with no live DB writers."""
    (root / 'data').mkdir()
    archive = root / 'archive'
    archive.mkdir()
    manifest = []
    db = Database(root / 'data/nightseam.db')
    try:
        db.update_settings({'image_dir': str(archive), 'wizard_done': True})
        metadata = ingest._header_session(db, {'SITELAT': 52.52, 'SITELONG': 13.405}, '2026-08-12T22:00:00')
        sessions = {}
        with db.import_unit():
            for number in range(small_count + large_count):
                large = number >= small_count
                raw = exposure(number, rows[(number - small_count) % len(rows)]) if large else small_image(number)[0]
                # Small seeds retain their own FITS header/session metadata.
                if large:
                    object_name, date, frame, session = PLAIN_OBJECT, '2026-08-12T22:00:00', 'light', metadata
                else:
                    _, header = small_image(number)
                    object_name, date, frame = header['OBJECT'], header['DATE-OBS'], header['IMAGETYP'].lower()
                    if date not in sessions:
                        sessions[date] = ingest._header_session(db, header, date)
                    session = sessions[date]
                relative = f'{ingest.obj_slug(object_name)}/{ingest.session_folder_name(session)}/{ingest.FRAME_TYPES[frame]}/seed-{number:05d}.fits'
                path = archive / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                record = dict(file=relative, orig_name=path.name, object_name=object_name,
                    date_obs=date, exposure=60 if large else 30, filter='L' if large else 'Ha',
                    imagetyp=frame, storage_compressed=0, ingested_at='2026-10-17T00:00:00',
                    content_sha256=digest, file_size=len(raw), file_mtime_ns=path.stat().st_mtime_ns, **session)
                number_id = db.add_image(record)
                manifest.append(dict(id=number_id, file=relative, sha256=digest, size=len(raw), large=large))
    finally:
        db.conn.close()
    return manifest


def workload_digest(manifest, sources):
    # Exclude private roots and filesystem timestamps; include every expected byte identity.
    content = [[r['id'], r['file'], r['sha256'], r['size']] for r in manifest]
    content += [[r['path'].name, r['sha256'], r['size']] for r in sources]
    return hashlib.sha256(json.dumps(content, separators=(',', ':')).encode()).hexdigest()


class PopulatedServer(bench.harness.DesktopServer):
    def __init__(self, exe, small_count, large_count):
        super().__init__(exe, seed=False, native=True, debug_port=bench.harness.free_port(), restricted_runtime=True)
        self.small_count, self.large_count = small_count, large_count

    def _start(self, timeout):
        # Budget actual seed data, input/archive copies, Rice stages and extraction.
        required = self.large_count * (32 * 2**20 + 5760) + 3 * 2**30
        bench.require(shutil.disk_usage(self.tmp).free >= required, f'Need at least {required} free bytes')
        self.manifest = seed(self.tmp, self.small_count, self.large_count)
        return super()._start(timeout)


class JobResources:
    """Owned job totals include bootloader, app, WebView2 and native utilities."""
    def __init__(self, tree):
        self.tree = tree

    def snapshot(self):
        tree = self.tree
        cpu, memory = _Accounting(), _ExtendedLimits()
        for kind, value in ((1, cpu), (9, memory)):
            tree._check(tree.api.QueryInformationJobObject(tree.job, kind,
                ctypes.byref(value), ctypes.sizeof(value), None))
        return dict(cpu_seconds=(cpu.TotalUserTime + cpu.TotalKernelTime) / 10**7,
            peak_job_committed_bytes=memory.PeakJobMemoryUsed, active_processes=cpu.ActiveProcesses)


def latency(samples):
    bench.require(bool(samples), 'No responsiveness samples were collected during the workload')
    values = sorted(samples)
    return dict(samples=len(values), median_seconds=statistics.median(values),
        p95_seconds=values[min(len(values) - 1, int(.95 * len(values)))], max_seconds=values[-1])


def measure(function, client, page, resources):
    probes, javascript = [], []
    before = resources.snapshot()
    def timed():
        started = time.perf_counter()
        result = function()
        return result, time.perf_counter() - started
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(timed)
        while not future.done():
            tick = time.perf_counter()
            client.get('/catalog.html').raise_for_status()
            probes.append(time.perf_counter() - tick)
            tick = time.perf_counter()
            bench.require(page.evaluate('Boolean(document.querySelector("#view"))'), 'Native catalog DOM disappeared')
            javascript.append(time.perf_counter() - tick)
            time.sleep(.05)
        result, elapsed = future.result()
    after = resources.snapshot()
    return result, dict(seconds=elapsed,
        owned_job_cpu_seconds=after['cpu_seconds'] - before['cpu_seconds'],
        job_peak_committed_bytes_since_launch=after['peak_job_committed_bytes'],
        static_http=latency(probes), native_webview_js_roundtrip=latency(javascript))


def job_done(client, job, timeout=180):
    deadline = time.monotonic() + timeout
    while job['status'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
        time.sleep(.02)
        job = bench.payload(client.get('/api/library/compression/' + job['id']))
    bench.require(job['status'] in ('completed', 'failed', 'cancelled'), 'Native job did not reach terminal status')
    return job


def cancellation(client, archive, database):
    before = bench.snapshot(database)
    ids = [row[0] for row in before[0] if row[3] == PLAIN_OBJECT]
    job = bench.payload(client.post('/api/library/compression', json={
        'mode': 'compress', 'anchor_id': ids[0], 'image_ids': ids, 'compression_mode': 'safe'}))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        outputs = [p for stage in archive.glob('.nightseam-rice-*') for p in stage.glob('*.output')]
        if job['status'] == 'running' and any(p.is_file() and p.stat().st_size > 0 for p in outputs):
            break
        bench.require(job['status'] not in ('completed', 'failed', 'cancelled'),
            f'Conversion finished before active native output could be observed: {job}')
        time.sleep(.01)
        job = bench.payload(client.get('/api/library/compression/' + job['id']))
    else:
        raise RuntimeError('No active native conversion output observed before cancellation')
    started = time.perf_counter()
    accepted = bench.payload(client.post('/api/library/compression/' + job['id'] + '/cancel'))
    job = job_done(client, accepted)
    elapsed = time.perf_counter() - started
    bench.require(job['status'] == 'cancelled', f'Native cancellation raced with publication or failed: {job}')
    bench.require(bench.snapshot(database) == before, 'Cancelled conversion changed records or integrity baselines')
    bench.require(not list(archive.glob('.nightseam-rice-*')), 'Cancelled conversion retained staging')
    return dict(seconds_to_terminal_and_cleanup=elapsed, observed_nonempty_native_output=True,
        status=job['status'], selected_images=len(ids), original_records_preserved=True)


def exercise(server, client, page, resources, count, batch_size, report=None):
    archive, database = server.tmp / 'archive', server.tmp / 'data/nightseam.db'
    manifest = server.manifest
    before, integrity, pending = bench.snapshot(database)
    bench.require(len(before) == integrity == len(manifest) and pending == 0, 'Seed publication is incomplete')
    inputs = server.tmp / 'inputs'
    inputs.mkdir()
    large = [row for row in manifest if row['large']]
    selected = [large[i] for i in sorted({0, 1, 2, len(large) // 2, len(large) - 1})]
    duplicates = []
    for row in selected:
        path = inputs / f'duplicate-{row["id"]}.fits'
        shutil.copyfile(archive / row['file'], path)
        duplicates.append(dict(path=path, sha256=row['sha256'], size=row['size']))
    plain = [write_input(inputs, 1_000_000 + i, ROWS[i % 3]) for i in range(count)]
    rice = [write_input(inputs, 2_000_000 + i, rows, rice=True) for i, rows in enumerate(ROWS)]
    sources = duplicates + plain + rice
    report = {} if report is None else report
    report.update(seed_images=len(manifest), seed_large_images=len(large), new_plain_images=count,
        large_file_bytes=sorted({row['size'] for row in sources}), workload_sha256=workload_digest(manifest, sources), waves={})
    # Warm resolver/tool/UI caches outside measured waves. No cold-cache claim.
    warm = bench.upload(client, duplicates[:1], compress='false')
    bench.require(warm[0].get('skipped') and warm[0]['id'] == selected[0]['id'], 'Warmup changed identity')
    def wave(name, items, compress=False):
        def upload():
            results = []
            for offset in range(0, len(items), batch_size):
                results += bench.upload(client, items[offset:offset + batch_size],
                    run_id=uuid.uuid4().hex, compress=str(compress).lower(), frame='light')
            return results
        results, measured = measure(upload, client, page, resources)
        bench.require(len(results) == len(items) and all(row.get('ok') for row in results), f'{name} failed: {results}')
        measured.update(files=len(items), bytes=sum(row['size'] for row in items),
            mib_per_second=sum(row['size'] for row in items) / measured['seconds'] / 2**20)
        report['waves'][name] = measured
        print(f'{name}: {measured["seconds"]:.3f}s, {measured["mib_per_second"]:.2f} MiB/s', flush=True)
        return results
    repeated = wave('existing-duplicates', duplicates)
    bench.require(all(r.get('skipped') and r['id'] == old['id'] for r, old in zip(repeated, selected)), 'Seed duplicate lost identity')
    bench.require(bench.snapshot(database)[0] == before, 'Duplicate changed seed metadata')
    new_plain = wave('new-plain', plain)
    new_rice = wave('new-safe-rice', rice, compress=True)
    bench.require(all(not row.get('skipped') for row in new_plain + new_rice), 'New exposure was unexpectedly deduplicated')
    bench.require(all(row.get('compression') == 'rice' for row in new_rice), 'Native Rice ingest was bypassed')
    mixed = wave('mixed-repeats', [duplicates[0], plain[0], rice[0]], compress=True)
    bench.require(all(row.get('skipped') for row in mixed) and
        [row['id'] for row in mixed] == [selected[0]['id'], new_plain[0]['id'], new_rice[0]['id']], 'Mixed repeat changed identity')
    report['cancellation'], report['cancellation_observation'] = measure(
        lambda: cancellation(client, archive, database), client, page, resources)
    # Restore the complete separate three-image Rice control session.
    job = bench.payload(client.post('/api/library/compression', json={
        'mode': 'uncompress', 'anchor_id': new_rice[0]['id'], 'image_ids': [r['id'] for r in new_rice]}))
    job = job_done(client, job)
    bench.require(job['status'] == 'completed', f'Native restoration failed: {job}')
    after, integrity, pending = bench.snapshot(database)
    bench.require(after[:len(before)] == before, 'Existing seed metadata changed')
    bench.require(len(after) == integrity == len(before) + count + 3 and pending == 0, 'Wrong image/integrity count')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        history = db.execute('SELECT COALESCE(SUM(n_files),0) FROM import_batches').fetchone()[0]
    bench.require(history == count + 3, 'Image/history publication differs')
    expected = {r['id']: r['sha256'] for r in manifest}
    expected.update({r['id']: s['sha256'] for r, s in zip(new_plain + new_rice, plain + rice)})
    for row in after:
        bench.require(row[8] == expected[row[0]] == bench.digest(archive / row[1]), 'Archive/catalog/expected SHA differs')
    for source in sources:
        bench.require(bench.digest(source['path']) == source['sha256'], 'Input bytes changed')
    bench.require(not any(p.name.startswith('.nightseam-') or p.suffix == '.part' for p in archive.rglob('*')), 'Staging leftovers remain')
    report.update(archive_hashes_verified=len(after), source_hashes_verified=len(sources), history_units=history,
        seed_metadata_preserved=True, exact_rice_restoration=True, staging_leftovers=0)
    return report


def sample(exe, args, position, variant, output):
    import httpx
    from playwright.sync_api import sync_playwright
    from scripts.check_native_window import NativeWindows, wait_for, host_capabilities
    server = PopulatedServer(exe, args.small_count, args.large_count)
    report = dict(status='running', variant=variant, position=position, executable_sha256=bench.digest(exe))
    windows = None
    try:
        server.start(timeout=120)
        windows = NativeWindows(server.tree)
        wait_for(windows.main_window, 'owned native benchmark window', timeout=60)
        debug_url = f'http://127.0.0.1:{server.debug_port}'
        def debug_ready():
            try:
                return bench.harness._http(debug_url + '/json/version', timeout=1)
            except OSError:
                return None
        wait_for(debug_ready, 'private WebView2 debugging endpoint')
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(debug_url, timeout=30000)
            discovery = browser.new_browser_cdp_session()
            def catalog():
                discovery.send('Target.getTargets')
                return next((p for c in browser.contexts for p in c.pages if p.url.startswith(server.base)), None)
            page = wait_for(catalog, 'native benchmark catalog')
            discovery.detach()
            page.locator('body[data-window-chrome="native"]').wait_for(state='attached')
            bench.require(host_capabilities(page)['window_chrome'] == 'native', 'Native host bridge unavailable')
            page.locator('#view').wait_for(state='attached')
            with httpx.Client(base_url=server.base, timeout=300, trust_env=False) as client:
                report['workload'] = {}
                exercise(server, client, page, JobResources(server.tree), args.count, args.batch_size, report['workload'])
            windows.post(windows.main_window(), 0x0010)
            bench.require(server.proc.wait(timeout=30) == 0, 'Native benchmark process exited uncleanly')
            wait_for(windows.processes_exited, 'owned benchmark descendants to exit', timeout=20)
            bench.harness._wait_listener_closed(server.port)
            report['graceful_process_exit_and_listener_closed'] = True
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if server.tmp is not None:
            for log in (server.tmp / 'logs').glob('nightseam.log*'):
                shutil.copyfile(log, output.parent / f'{position:02d}-{variant}-{log.name}')
        raise
    finally:
        try:
            try:
                if windows is not None:
                    windows.restore_thread()
            finally:
                server.stop()
            report['owned_process_and_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def summarize(runs):
    result = {}
    for name in runs[0]['workload']['waves']:
        result[name] = {}
        for variant in ('baseline', 'candidate'):
            samples = [r['workload']['waves'][name] for r in runs if r['variant'] == variant]
            result[name][variant] = dict(samples=len(samples), seconds=[s['seconds'] for s in samples],
                median_seconds=statistics.median(s['seconds'] for s in samples),
                median_mib_per_second=statistics.median(s['mib_per_second'] for s in samples),
                median_job_cpu_seconds=statistics.median(s['owned_job_cpu_seconds'] for s in samples),
                max_job_committed_bytes=max(s['job_peak_committed_bytes_since_launch'] for s in samples),
                max_native_webview_js_roundtrip_seconds=max(s['native_webview_js_roundtrip']['max_seconds'] for s in samples))
    return result


def run(args):
    directory = args.output.parent / (args.output.stem + '-runs')
    directory.mkdir(parents=True)  # Refuse to mix or overwrite partial evidence.
    report = dict(status='running', os=platform.platform(), cpu=platform.processor(), logical_cpus=os.cpu_count(),
        baseline_commit=args.baseline_commit, candidate_commit=args.candidate_commit,
        same_controller_and_machine=True, sequential=True, cold_cache_controlled=False,
        timing_threshold_enforced=False, memory_metric='owned Windows job peak committed bytes since launch; not RSS',
        cpu_scope='owned job incl. bootloader, app, WebView2 and native tools; excludes controller',
        responsiveness_scope='static HTTP and actual native WebView2 JavaScript roundtrip during uploads; no physical input latency claim',
        generation_startup_audits_outside_timing=True, order=order(args.pairs), runs=[])
    try:
        expected = None
        for position, variant in enumerate(report['order'], 1):
            exe = getattr(args, variant + '_exe').resolve()
            current = sample(exe, args, position, variant, directory / f'{position:02d}-{variant}.json')
            fingerprint = current['workload']['workload_sha256']
            if expected is None:
                expected = fingerprint
            bench.require(fingerprint == expected, 'Comparison ran different input/archive bytes')
            report['runs'].append(current)
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        report.update(status='passed', summary=summarize(report['runs']), owned_process_and_scratch_cleanup=True)
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for variant in ('baseline', 'candidate'):
        parser.add_argument('--' + variant + '-exe', type=Path, required=True)
        parser.add_argument('--' + variant + '-commit', required=True)
    parser.add_argument('--small-count', type=int, default=6000)
    parser.add_argument('--large-count', type=int, default=96)
    parser.add_argument('--count', type=int, default=24)
    parser.add_argument('--batch-size', type=int, default=12)
    parser.add_argument('--pairs', type=int, default=2)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/large-import-comparison.json')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('packaged native comparison requires Windows')
    if args.small_count < 12 or args.large_count < 24 or args.count < 3 or args.pairs < 2 or not 1 <= args.batch_size <= 24:
        parser.error('use >=12 small seeds, >=24 large seeds, >=3 new plain exposures, >=2 pairs and batch size 1..24')
    run(args)


if __name__ == '__main__':
    main()
