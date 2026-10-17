"""Real HTTP edge cases and worker profiles against a private populated library.

The library is seeded directly; three Rice originals use the real verified
encoder. Only subsequent HTTP waves are measured. Small synthetic integer FITS,
warm caches and instrumentation are not a real-exposure throughput baseline.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/browser'))
from fits_fixtures import _header, _padded
from scripts import profile_import as profile
bench = profile.bench


def image(number, object_name=None):
    slot = number % 10
    side = 8 if slot < 4 else 32 if slot < 8 else 64 if slot == 8 else 128
    metadata = {'OBJECT': object_name or f'Populated target {number % 3}', 'DATE-OBS': f'2026-09-{15 + number % 4:02d}T22:00:00',
        'EXPTIME': 30, 'SITELAT': 52.52, 'SITELONG': 13.405,
        'IMAGETYP': 'DARK' if number % 7 == 0 else 'LIGHT', 'FILTER': 'Ha'}
    pixels = struct.pack('>Q', number) + bytes((side * side * 2) - 8)
    raw = _header([('SIMPLE', True), ('BITPIX', 16), ('NAXIS', 2),
                   ('NAXIS1', side), ('NAXIS2', side), *metadata.items()]) + _padded(pixels)
    return raw, metadata


def seed(db, archive, count, manifest):
    """Only called by the private source bootstrap before serving HTTP."""
    from starlette.datastructures import UploadFile
    from app import ingest
    archive.mkdir(exist_ok=True)
    bench.require(not db.images() and not any(archive.iterdir()), 'Seed requires an empty private library')
    records, metadata_cache = [], {}
    for number in range(3):
        raw, _ = image(number)
        result = asyncio.run(ingest.ingest_one(db, [], archive,
            UploadFile(io.BytesIO(raw), filename=f'seed-{number}.fits'), compress=True))
        bench.require(result.get('ok') and result.get('compression') == 'rice', f'Seed Rice failed: {result}')
        row = db.image(result['id'])
        records.append(dict(number=number, id=row['id'], file=row['file'], sha256=row['content_sha256'],
                            original_sha256=hashlib.sha256(raw).hexdigest(), file_size=row['file_size'], rice=True))
    with db.import_unit():
        for number in range(3, count):
            raw, header = image(number)
            date = header['DATE-OBS']
            if date not in metadata_cache:
                metadata_cache[date] = ingest._header_session(db, header, date)
            metadata = metadata_cache[date]
            name, frame = f'seed-{number}.fits', header['IMAGETYP'].lower()
            relative = f'{ingest.obj_slug(header["OBJECT"])}/{ingest.session_folder_name(metadata)}/{frame}/{name}'
            path = archive / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            number_id = db.add_image(dict(file=relative, orig_name=name, object_name=header['OBJECT'],
                date_obs=date, exposure=30, filter='Ha', imagetyp=frame,
                ingested_at='2026-10-17T00:00:00+00:00', content_sha256=digest,
                file_size=len(raw), file_mtime_ns=path.stat().st_mtime_ns, storage_compressed=0, **metadata))
            records.append(dict(number=number, id=number_id, file=relative, sha256=digest,
                                original_sha256=digest, file_size=len(raw), rice=False))
    manifest.write_text(json.dumps(records), encoding='utf-8')


def run(args):
    import httpx
    server = profile.SourceServer(args.output.parent / 'populated-child-stderr.log', seed_count=args.count)
    report = dict(status='running', source_commit=os.environ.get('GITHUB_SHA'), os=platform.platform(),
        cpu=platform.processor(), sqlite_version=sqlite3.sqlite_version, logical_cpus=os.cpu_count(),
        workload='directly seeded small integer FITS; measured real HTTP waves; warm instrumented source',
        cold_cache_controlled=False, native_child_cpu_measured=False, packaged_throughput_comparable=False,
        archive_hash_counters_scope='ingest reads; Rice verification is separately timed', waves={})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.start(timeout=120)
        archive, database = server.tmp / 'archive', server.tmp / 'data/nightseam.db'
        seeded = json.loads((server.tmp / 'seed-manifest.json').read_text(encoding='utf-8'))
        bench.require(len(seeded) == args.count, 'Seed count differs')
        before, integrity, pending = bench.snapshot(database)
        bench.require(len(before) == integrity == args.count and pending == 0, 'Seed publication is incomplete')
        report['seed'] = dict(images=args.count, rice=3,
            sizes=sorted({row['file_size'] for row in seeded}), objects=len({row[3] for row in before}),
            sessions=len({row[6] for row in before}),
            common_size_population=sum(row['file_size'] == 5760 for row in seeded))
        inputs = server.tmp / 'inputs'
        inputs.mkdir()
        sources, all_inputs = {}, []
        def source(number):
            if number not in sources:
                # The restoration control owns a whole one-image session; a
                # conversion must never submit a partial existing session.
                raw, _ = image(number, 'Isolated restoration control' if number == args.count + 8 else None)
                path = inputs / f'input-{number}.fits'
                path.write_bytes(raw)
                item = dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
                sources[number] = item
                all_inputs.append(item)
            return sources[number]
        def duplicate_results(results, numbers):
            bench.require(len(results) == len(numbers), 'Duplicate response count differs')
            for result, number in zip(results, numbers):
                bench.require(result.get('ok') and result.get('skipped') and result['id'] == seeded[number]['id'],
                              f'Duplicate lost original identity: {result}')
        with httpx.Client(base_url=server.base, timeout=120, trust_env=False) as client:
            # Warm resolver/tool caches with an audited duplicate, outside profiles.
            duplicate_results(bench.upload(client, [source(3)]), [3])
            def wave(name, items, **fields):
                bench.payload(client.post('/__import_profile/start'))
                probes = []
                started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(bench.upload, client, items, run_id=uuid.uuid4().hex, **fields)
                    while not future.done():
                        tick = time.perf_counter()
                        client.get('/catalog.html').raise_for_status()
                        probes.append(time.perf_counter() - tick)
                        time.sleep(.05)
                    results = future.result()
                elapsed = time.perf_counter() - started
                measured = bench.payload(client.post('/__import_profile/finish'))
                report['waves'][name] = dict(seconds=elapsed, static_probe_samples=len(probes),
                    static_probe_max_seconds=max(probes, default=None), profile=measured)
                print(f'{name}: {len(items)} files, {elapsed:.3f}s', flush=True)
                return results
            old = [0, 1, 2, args.count // 2, args.count - 1]
            duplicate_results(wave('existing-duplicates', [source(n) for n in old], compress='false', frame='flat'), old)
            bench.require(bench.snapshot(database)[0] == before, 'Duplicates changed seed metadata')
            plain = wave('new-plain', [source(args.count + n) for n in range(8)], compress='false')
            rice = wave('new-safe-rice', [source(args.count + n) for n in range(8, 11)], compress='true')
            for result in [*plain, *rice]:
                bench.require(result.get('ok') and not result.get('skipped'), f'New import failed: {result}')
            bench.require(all(row.get('compression') == 'rice' for row in rice), 'Safe Rice conversion was bypassed')
            repeated = wave('mixed-repeats', [source(0), source(args.count), source(args.count + 8)],
                            compress='true', frame='dark')
            bench.require(all(row.get('ok') and row.get('skipped') for row in repeated) and
                [row['id'] for row in repeated] == [seeded[0]['id'], plain[0]['id'], rice[0]['id']],
                'Mixed representation repeats changed identity')

            bad = inputs / 'malformed.fits'
            bad.write_bytes(b'SIMPLE invalid truncated FITS')
            invalid = dict(path=bad, sha256=bench.digest(bad), size=bad.stat().st_size)
            all_inputs.append(invalid)
            mixed = wave('malformed-between-valid', [source(1), invalid, source(args.count + 11)], compress='false')
            bench.require(mixed[0].get('skipped') and not mixed[1].get('ok') and mixed[2].get('ok') and
                          not mixed[2].get('skipped'), f'Malformed input damaged later publication: {mixed}')
            created = [*plain, *rice, mixed[2]]
            bench.require(len({row['id'] for row in created}) == 12, 'New rows reused IDs')
            for result, number in zip(created, range(args.count, args.count + 12)):
                stored = bench.stored_path(archive, result)
                bench.require(stored.is_file(), 'Published output missing')
                if result not in rice:
                    bench.require(stored.read_bytes() == image(number)[0], 'Plain import changed bytes')

            edited = next(row for row in reversed(seeded) if not row['rice'] and row['file_size'] == 5760)
            target = bench.stored_path(archive, {'stored': edited['file']})
            stat = target.stat()
            changed = bytearray(target.read_bytes())
            changed[2888] ^= 1
            target.write_bytes(changed)
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            changed_path = inputs / 'restored-mtime.fits'
            changed_path.write_bytes(changed)
            item = dict(path=changed_path, sha256=hashlib.sha256(changed).hexdigest(), size=len(changed))
            all_inputs.append(item)
            result, = wave('restored-mtime-edit', [item], compress='false')
            bench.require(result.get('skipped') and result['id'] == edited['id'], 'Edited archive bytes were missed')
            edited['sha256'] = item['sha256']

            missing = next(row for row in reversed(seeded) if not row['rice'] and row['id'] != edited['id'])
            bench.stored_path(archive, {'stored': missing['file']}).unlink()
            restored, = wave('restore-missing-original', [source(missing['number'])], compress='false')
            bench.require(restored.get('ok') and restored.get('restored') and restored['id'] == missing['id'],
                          'Missing original was not restored into its original record')

            bench.payload(client.post('/__import_profile/start'))
            job = bench.payload(client.post('/api/library/compression', json={
                'mode': 'uncompress', 'anchor_id': rice[0]['id'], 'image_ids': [rice[0]['id']]}))
            deadline = time.monotonic() + 60
            while job['status'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
                time.sleep(.05)
                job = bench.payload(client.get('/api/library/compression/' + job['id']))
            report['restoration_profile'] = bench.payload(client.post('/__import_profile/finish'))
            bench.require(job['status'] == 'completed', f'Native restoration failed: {job}')

            after, integrity, pending = bench.snapshot(database)
            bench.require(len(after) == integrity == args.count + 12 and pending == 0, 'Final catalog/integrity count differs')
            old_after = [tuple(row[:8]) + (before[i][8],) + tuple(row[9:])
                         if row[0] == edited['id'] else row for i, row in enumerate(after[:args.count])]
            bench.require(old_after == before, 'Seed identity/class/session/location changed')
            for row in seeded:
                bench.require(bench.digest(bench.stored_path(archive, {'stored': row['file']})) == row['sha256'],
                              'Seed archive bytes differ')
            with closing(sqlite3.connect(database)) as db:
                rows = db.execute('SELECT id,file,content_sha256 FROM images ORDER BY id').fetchall()
                history = db.execute('SELECT SUM(n_files) FROM import_batches').fetchone()[0]
            bench.require(history == 12, 'HTTP image/history publication differs')
            for number_id, relative, digest in rows:
                path = bench.stored_path(archive, {'stored': relative})
                bench.require(bench.digest(path) == digest, 'Catalog SHA differs from actual archive bytes')
                if number_id == rice[0]['id']:
                    bench.require(path.read_bytes() == source(args.count + 8)['path'].read_bytes(),
                                  'Native restoration changed whole-file bytes')
            for item in all_inputs:
                bench.require(bench.digest(item['path']) == item['sha256'], 'Input bytes changed')
            bench.require(not any(path.name.startswith('.nightseam-') or path.suffix == '.part'
                for path in archive.rglob('*')), 'Staging leftovers remain')
            report.update(status='passed', archived_files_verified=len(rows), source_files_verified=len(all_inputs),
                seed_metadata_preserved=True, restored_mtime_edit_detected=True, missing_original_restored=True,
                malformed_input_isolated=True, exact_rice_restoration=True, new_images=12, history_units=history,
                staging_leftovers=0)
            bench.payload(client.post('/__import_profile/shutdown'))
        bench.require(server.proc.wait(timeout=20) == 0, 'Source process did not shut down cleanly')
    except BaseException as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        if server.tmp is not None and (server.tmp / 'child.log').is_file():
            shutil.copyfile(server.tmp / 'child.log', args.output.parent / 'populated-child.log')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=6000)
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/populated-import-profile.json')
    args = parser.parse_args()
    if args.count < 12:
        parser.error('count must be at least 12')
    run(args)


if __name__ == '__main__':
    main()
