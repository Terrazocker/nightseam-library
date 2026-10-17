"""Measure real packaged HTTP import; audit private bytes and dedup afterwards.

The child app has an empty PATH and no Python/Node environment overrides. This
is a restricted-runtime check, not a clean Windows VM or a network-denial test.
Generation/audits are outside timing; caches are not flushed. No timing threshold
or speedup against the earlier, different machine is implied.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import struct
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests/browser'))
import harness

FILE_BYTES = 4_150_080


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@lru_cache(maxsize=1)
def template():
    import numpy as np
    from astropy.io import fits
    pixels = np.random.default_rng(20261015).normal(4096, 60, (1080, 1920))
    pixels = np.clip(pixels, 0, 65535).astype(np.uint16)
    header = fits.Header({'OBJECT': 'M31', 'DATE-OBS': '2026-08-12T22:00:00',
        'EXPTIME': 60, 'SITELAT': 52.52, 'SITELONG': 13.405,
        'IMAGETYP': 'LIGHT', 'FILTER': 'L', 'INSTRUME': 'Synthetic benchmark'})
    output = io.BytesIO()
    fits.PrimaryHDU(pixels, header=header).writeto(output)
    raw = output.getvalue()
    require(len(raw) == FILE_BYTES and raw[2880 - 80:2880].strip() == b'',
            'Benchmark template must have one header block and the historical file size')
    return raw


def exposure(index):
    raw = bytearray(template())
    raw[2880:2888] = struct.pack('>Q', index)
    return raw


def fixtures(root, count):
    root.mkdir()
    records = []
    for index in range(count):
        path = root / f'exposure-{index:04d}.fits'
        raw = exposure(index)
        path.write_bytes(raw)
        records.append({'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'size': len(raw)})
    return records


def payload(response):
    response.raise_for_status()
    return response.json()


def upload(client, sources, **fields):
    with ExitStack() as stack:
        files = [('files', (item['path'].name,
                  stack.enter_context(item['path'].open('rb')), 'application/fits'))
                 for item in sources]
        return payload(client.post('/api/images/ingest', files=files,
                                   data=fields))['results']


def snapshot(database):
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        rows = connection.execute('SELECT id,file,object_id,object_name,imagetyp,'
            'date_obs,session_key,storage_compressed,content_sha256,site_lat,site_lon,'
            'session_date_obs,session_kind,session_start,session_end FROM images ORDER BY id').fetchall()
        integrity = connection.execute('SELECT COUNT(*) FROM image_integrity').fetchone()[0]
        pending = connection.execute('SELECT COUNT(*) FROM compression_journal').fetchone()[0]
    return rows, integrity, pending


def stored_path(archive, result):
    path = (archive / result['stored']).resolve()
    require(path.is_relative_to(archive.resolve()), 'Result escaped the private archive')
    return path


def benchmark(client, archive, database, sources, batch_size):
    require(payload(client.get('/api/library'))['total_images'] == 0, 'Benchmark needs an empty library')
    batches, results = [], []
    run_id = uuid.uuid4().hex
    started = time.perf_counter()
    for offset in range(0, len(sources), batch_size):
        batch = sources[offset:offset + batch_size]
        tick = time.perf_counter()
        current = upload(client, batch, run_id=run_id, compress='false', frame='light')
        seconds = time.perf_counter() - tick
        require(len(current) == len(batch) and all(item.get('ok') and not any(
            item.get(flag) for flag in ('skipped', 'unconfirmed', 'unattempted')) for item in current),
            f'Import failed in batch {len(batches) + 1}: {current}')
        results.extend(current)
        batches.append({'count': len(batch), 'bytes': sum(item['size'] for item in batch),
                        'seconds': seconds})
        print(f'batch {len(batches):02d}: {len(batch)} files in {seconds:.3f}s', flush=True)
    elapsed = time.perf_counter() - started
    require(len({item['id'] for item in results}) == len(sources), 'Import reused an ID unexpectedly')
    require(payload(client.get('/api/library'))['total_images'] == len(sources), 'Wrong catalog count')
    for source, result in zip(sources, results):
        require(digest(source['path']) == source['sha256'], 'Source bytes changed')
        require(digest(stored_path(archive, result)) == source['sha256'], 'Archived bytes differ')
    before, integrity, pending = snapshot(database)
    require(len(before) == integrity == len(sources) and pending == 0, 'Incomplete database publication')
    require([row[8] for row in before] == [item['sha256'] for item in sources], 'Wrong catalog hashes')
    indexes = sorted({0, len(sources) // 2, len(sources) - 1})
    repeated = upload(client, [sources[index] for index in indexes], compress='false', frame='dark')
    require(len(repeated) == len(indexes) and all(item.get('ok') and item.get('skipped')
        and item['id'] == results[index]['id'] for index, item in zip(indexes, repeated)),
        'Duplicate import changed identity')
    require(snapshot(database)[0] == before, 'Duplicate changed class, session, location or identity')

    # Deliberately edit only the owned archive, after the original-byte audit.
    # The next request must observe it even when size and mtime still match.
    path = stored_path(archive, results[0])
    info = path.stat()
    changed = bytearray(path.read_bytes())
    changed[2888] ^= 1
    path.write_bytes(changed)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    require(path.stat().st_mtime_ns == info.st_mtime_ns, 'Could not restore test mtime')
    edited = sources[0]['path'].parent / 'edited-original.fits'
    edited.write_bytes(changed)
    changed_result, = upload(client, [{'path': edited}], compress='false')
    require(changed_result.get('ok') and changed_result.get('skipped') and
            changed_result['id'] == results[0]['id'], 'Next request missed edited archive bytes')
    rows, integrity, pending = snapshot(database)
    require(rows[0][8] == hashlib.sha256(changed).hexdigest(), 'Changed fingerprint was not refreshed')
    require(len(rows) == integrity == len(sources) and pending == 0, 'Dedup probe left pending data')
    require(not any(item.name.startswith('.nightseam-') or item.suffix == '.part'
                    for item in archive.rglob('*')), 'Staging leftovers remain')
    total_bytes = sum(item['size'] for item in sources)
    full_batches = [item for item in batches if item['count'] == batch_size]
    return {'files': len(sources), 'bytes': total_bytes, 'elapsed_seconds': elapsed,
        'mib_per_second': total_bytes / elapsed / 2**20, 'batch_size': batch_size,
        'batches': batches, 'first_full_batch_seconds': full_batches[0]['seconds'] if full_batches else None,
        'last_full_batch_seconds': full_batches[-1]['seconds'] if full_batches else None,
        'source_and_archive_hashes_verified': len(sources), 'duplicate_probes': len(indexes),
        'duplicate_class_session_location_preserved': True,
        'same_size_restored_mtime_edit_detected': True, 'pending_or_staging': 0,
        'timing_includes': 'sequential real HTTP multipart import and publication',
        'timing_excludes': 'fixture generation, startup, audits and duplicate/edit probes',
        'cold_cache_controlled': False, 'archive_hash_read_counts_measured': False}


def rice_smoke(client, archive):
    from fits_fixtures import compressed_gate_image
    original = compressed_gate_image(False)
    result, = payload(client.post('/api/images/ingest', data={
        'compress': 'true', 'compression_mode': 'safe'},
        files={'files': ('runtime-smoke.fits', original, 'application/fits')}))['results']
    require(result.get('ok') and not result.get('skipped') and result['stored'].endswith('.fz'),
            f'Safe Rice ingest failed: {result}')
    job = payload(client.post('/api/library/compression', json={
        'mode': 'uncompress', 'anchor_id': result['id'], 'image_ids': [result['id']]}))
    deadline = time.monotonic() + 60
    while job['status'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
        time.sleep(.05)
        job = payload(client.get('/api/library/compression/' + job['id']))
    require(job['status'] == 'completed', f'Native restoration failed: {job}')
    library = payload(client.get('/api/library'))
    require(library['total_images'] == 1, 'Rice roundtrip changed library count')
    record, = [image for obj in library['objects'] for image in obj['images']]
    require(record['id'] == result['id'] and stored_path(archive, {'stored': record['file']}).read_bytes() == original,
            'Rice restoration changed identity or original bytes')
    return {'safe_rice_ingest': True, 'native_uncompress': True, 'exact_original_bytes': True}


def run(args):
    import httpx
    report = {'status': 'running', 'source_commit': os.environ.get('GITHUB_SHA'),
        'executable_sha256': digest(args.exe), 'os': platform.platform(),
        'cpu': platform.processor(), 'logical_cpus': os.cpu_count(),
        'controller_python': platform.python_version(), 'child_developer_path_removed': True,
        'clean_windows_vm': False, 'network_denial_tested': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server = harness.DesktopServer(args.exe, seed=False, restricted_runtime=True)
    try:
        server.start(timeout=60)
        required = int(args.count * FILE_BYTES * 2.5)
        report['free_disk_bytes_before'] = shutil.disk_usage(server.tmp).free
        require(report['free_disk_bytes_before'] >= required, f'Need {required} free bytes for private fixtures/archive')
        sources = fixtures(server.tmp / 'inputs', args.count)
        with httpx.Client(base_url=server.base, trust_env=False, timeout=300) as client:
            report['import'] = benchmark(client, server.tmp / 'archive',
                server.tmp / 'data/nightseam.db', sources, args.batch_size)
        server.stop()
        server = harness.DesktopServer(args.exe, seed=False, restricted_runtime=True)
        server.start(timeout=60)
        with httpx.Client(base_url=server.base, trust_env=False, timeout=120) as client:
            report['restricted_runtime_rice'] = rice_smoke(client, server.tmp / 'archive')
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if server.tmp is not None:
            for log in (server.tmp / 'logs').glob('nightseam.log*'):
                shutil.copyfile(log, args.output.parent / f'benchmark-{log.name}')
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
    parser.add_argument('--exe', type=Path, default=harness.DEFAULT_EXE)
    parser.add_argument('--count', type=int, default=512)
    parser.add_argument('--batch-size', type=int, default=24)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/windows-import-benchmark.json')
    args = parser.parse_args()
    if args.count < 3 or not 1 <= args.batch_size <= 24:
        parser.error('count must be at least 3 and batch size must be 1..24')
    if os.name != 'nt':
        parser.error('the packaged benchmark requires Windows')
    run(args)


if __name__ == '__main__':
    main()
