"""Exercise the real request-pin boundary with small, private FITS files.

This is admission/handle stress, not exposure-throughput measurement. Windows
is required for authoritative writer exclusion; tests can exercise the algorithm
on POSIX without enabling it in the application.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys
import tempfile
import threading
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import bench_windows_import as bench
from scripts.clean_workspace import remove_tree
from fits_fixtures import compressed_gate_image
from app import ingest
from app.database import Database


def resources():
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle, count = kernel.GetCurrentProcess(), wintypes.DWORD()
        if not kernel.GetProcessHandleCount(handle, ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        class Memory(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak_rss', 'rss', 'peak_pool',
                    'pool', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        memory = Memory()
        memory.cb = ctypes.sizeof(memory)
        api = ctypes.WinDLL('psapi', use_last_error=True)
        api.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
        if not api.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'handles': count.value, 'rss_bytes': memory.rss}
    return {'handles': len(list(Path('/proc/self/fd').iterdir())),
        'rss_bytes': int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')}


def available(db):
    acquired = db.archive_lock.acquire(blocking=False)
    if acquired:
        db.archive_lock.release()
    return acquired


def worker_available(db):
    # Shutdown joins threads, but an executor still owns Python locks. Those
    # locks own Windows semaphore handles until this scope releases the pool.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(available, db).result(timeout=5)


def seed(db, archive, count, files_per_directory):
    archive.mkdir()
    records = []
    template = compressed_gate_image(False)
    with db.import_unit():
        for number in range(count):
            raw = bytearray(template)
            raw[2880:2888] = struct.pack('>Q', number)
            relative = f'stress/session-{number // files_per_directory:05d}/light/{number:05d}.fits'
            path = archive / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            record = dict(file=relative, orig_name=path.name, object_name='Synthetic stress',
                imagetyp='light', ingested_at='2026-10-17T00:00:00+00:00',
                content_sha256=hashlib.sha256(raw).hexdigest(), file_size=len(raw),
                file_mtime_ns=path.stat().st_mtime_ns)
            record['id'] = db.add_image(record)
            records.append(record)
    return records


async def cancelled_preparation(db, archive, size, checkpoint):
    original = ingest._fingerprint_file
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    reads = 0
    def pause(path, *args):
        nonlocal reads
        reads += 1
        if reads == checkpoint:
            entered.set()
            if not release.wait(15):
                raise RuntimeError('Stress cancellation worker was not released')
        return original(path, *args)
    ingest._fingerprint_file = pause
    task = asyncio.create_task(ingest._archive_io(ingest.DedupBatch.prepare,
        db, archive, {size}, cancel, on_cancel=lambda index: index.close(), cancel_event=cancel))
    try:
        bench.require(await asyncio.to_thread(entered.wait, 15), 'Cancellation checkpoint was not reached')
        task.cancel()
        await asyncio.sleep(0)
        bench.require(not task.done(), 'Cancellation failed to drain its active worker')
        task.cancel()
    finally:
        release.set()
        try:
            index = await task
            index.close()
        except asyncio.CancelledError:
            pass
        finally:
            ingest._fingerprint_file = original
    bench.require(cancel.is_set() and reads == checkpoint, 'Cancelled preparation continued hashing')
    bench.require(available(db), 'Cancelled preparation leaked its archive lease')
    return {'hash_checkpoint': reads, 'worker_drained': True, 'lease_released': True}


def exercise(root, count, cycles=3, files_per_directory=32):
    """Root is newly allocated private scratch; never accept an existing archive."""
    bench.require(not root.exists(), 'Stress scratch must be a new directory')
    root.mkdir()
    db = Database(root / 'catalog.db')
    archive = root / 'archive'
    original = ingest._fingerprint_file
    tracing = False
    try:
        records = seed(db, archive, count, files_per_directory)
        size, cap = records[0]['file_size'], ingest._BATCH_PIN_LIMIT
        # Initialise native APIs and the discardable cache before the baseline.
        ingest._dedup_fingerprint(db, archive / records[0]['file'], '')
        resources()
        # The observer itself uses threads and asyncio for lease/cancel probes.
        # Initialise their process/thread runtime state before measuring pins.
        bench.require(worker_available(db), 'Warm archive is locked')
        asyncio.run(asyncio.to_thread(lambda: None))
        gc.collect()
        case_baseline = resources()
        tracemalloc.start()
        tracing = True
        results = []
        for cycle in range(cycles):
            gc.collect()
            before = resources()
            heap_before = tracemalloc.get_traced_memory()[0]
            reads = 0
            def measured(path, *args):
                nonlocal reads
                reads += 1
                return original(path, *args)
            ingest._fingerprint_file = measured
            started = time.perf_counter()
            index = ingest.DedupBatch.prepare(db, archive, {size})
            try:
                held = resources()
                pinned = min(count, cap)
                bench.require(len(index.entries) == pinned and reads == pinned,
                    'Preparation did not hash and pin exactly the bounded candidates')
                bench.require(held['handles'] - before['handles'] >= pinned,
                    'Expected native file pins were not observed')
                if os.name == 'nt':
                    try:
                        with (archive / records[0]['file']).open('r+b'):
                            pass
                    except PermissionError:
                        pass
                    else:
                        raise RuntimeError('The pinned archive still permits a writer')
                bench.require(not worker_available(db),
                    'Pinned batch did not exclude an unrelated archive operation')
                after_observer = resources()
                with db.archive_lock.for_batch(index):
                    for _ in range(2):
                        bench.require(ingest._find_duplicate(db, archive, 'absent', size,
                            dedup_batch=index) is None, 'Distinct bytes matched a negative probe')
                    bench.require(reads == pinned + 2 * max(0, count - cap),
                        'Above-cap candidates did not retain fresh fallback hashes')
                    last = records[-1]
                    duplicate = ingest._find_duplicate(db, archive, last['content_sha256'], size,
                        dedup_batch=index)
                    bench.require(duplicate and duplicate['id'] == last['id'], 'Positive duplicate lost identity')
                    bench.require(reads == pinned + 2 * max(0, count - cap) + 1,
                        'Positive duplicate was not freshly hashed')
                live_heap, peak_heap = tracemalloc.get_traced_memory()
                folders = len(index.folders)
            finally:
                index.close()
                ingest._fingerprint_file = original
            gc.collect()
            after = resources()
            bench.require(after['handles'] <= before['handles'] + 2,
                f'A request leaked native handles: before={before}, held={held}, '
                f'after_observer={after_observer}, after={after}')
            bench.require(available(db) and not index.entries and not index.folders,
                'Request cleanup retained pins or an archive lease')
            results.append(dict(cycle=cycle + 1, pinned_files=pinned, pinned_parents=folders,
                archive_hashes=reads, before=before, held=held, after_observer=after_observer, after=after,
                held_python_heap_growth_bytes=live_heap - heap_before,
                after_python_heap_growth_bytes=tracemalloc.get_traced_memory()[0] - heap_before,
                traced_peak_bytes=peak_heap, elapsed_seconds=time.perf_counter() - started))
            print(f'{count} files, cycle {cycle + 1}: {pinned} pinned; {reads} hashes; handles {before["handles"]}/{held["handles"]}/{after["handles"]}', flush=True)
        cancellation_before = resources()
        cancelled = asyncio.run(cancelled_preparation(db, archive, size, min(32, count, cap)))
        gc.collect()
        bench.require(resources()['handles'] <= cancellation_before['handles'] + 2,
            'Cancelled preparation leaked native handles')
        final_resources = resources()
        bench.require(final_resources['handles'] <= case_baseline['handles'] + 2,
            f'Repeated requests accumulated native handles: before={case_baseline}, after={final_resources}')
        for record in records:
            bench.require(bench.digest(archive / record['file']) == record['content_sha256'], 'Stress changed source bytes')
        bench.require(db.conn.execute('SELECT COUNT(*) FROM images').fetchone()[0] == count,
            'Stress changed library rows')
        bench.require(not any(p.name.startswith('.nightseam-') or p.suffix == '.part'
            for p in archive.rglob('*')), 'Stress left staging files')
        return dict(files=count, file_bytes=size, seeded_bytes=count * size, cycles=results,
            cancellation=cancelled, case_baseline=case_baseline, final_resources=final_resources,
            all_archive_hashes_verified=count, pending_or_staging=0)
    finally:
        ingest._fingerprint_file = original
        if tracing:
            tracemalloc.stop()
        db.conn.close()


def run(args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scratch_parent = ROOT / 'logs/dedup-stress'
    scratch_parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='owned-', dir=scratch_parent))
    (scratch / '.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
    report = dict(status='running', os=platform.platform(), cpu=platform.processor(),
        logical_cpus=os.cpu_count(), pin_limit=ingest._BATCH_PIN_LIMIT,
        windows_writer_exclusion_tested=os.name == 'nt',
        workload='small FITS; admission/handle stress, not realistic exposure throughput', cases=[])
    try:
        for count in args.counts:
            report['cases'].append(exercise(scratch / str(count), count,
                args.cycles, args.files_per_directory))
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        try:
            remove_tree(scratch)
            report['owned_scratch_cleanup'] = True
        except BaseException as exc:
            report.update(status='failed', cleanup_error=str(exc))
            raise
        finally:
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', type=int, nargs='+', default=[4095, 4096, 4097])
    parser.add_argument('--cycles', type=int, default=3)
    parser.add_argument('--files-per-directory', type=int, default=32)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/windows-dedup-stress.json')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('authoritative request-pin stress requires Windows NTFS/ReFS')
    if min(args.counts) < 1 or args.cycles < 1 or args.files_per_directory < 1:
        parser.error('counts, cycles and files per directory must be positive')
    run(args)


if __name__ == '__main__':
    main()
