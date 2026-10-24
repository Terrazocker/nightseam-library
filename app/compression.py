"""Verified native Rice conversion and manual portable library copies.

All IO runs in a worker under archive_lock. SQLite critical sections contain
metadata only. A durable journal retains originals until publication commits.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing, ExitStack
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from . import digests
from . import fits_metadata as fits
from .archive_health import _contained, _inspect
from .archive_trash import _move, _mkdir, _parent, _transaction, PinnedParents
from .imglib import session_for_anchor
from .paths import resource_dir, safe_join, safe_rel, safe_token
from .file_version import pinned_version

log = logging.getLogger(__name__)
_TOOLS_LOCK = threading.Lock()
_TOOLS_CACHE = None


class Cancelled(Exception):
    pass


class RiceCompressionError(ValueError):
    """A staged conversion failed its lossless encoding/verification gate."""


def tool_bundle():
    """Select only a bundled, verified toolchain for a supported native host."""
    system, machine = platform.system(), platform.machine().lower()
    if machine in ('amd64', 'x86_64'):
        if system == 'Windows':
            return 'windows-x64', '.exe'
        if system == 'Linux':
            return 'linux-x64', ''
    raise ValueError(f'Bundled FITS utilities are not available for {system} {machine}.')


def tools():
    global _TOOLS_CACHE
    bundle, suffix = tool_bundle()
    root = resource_dir() / 'vendor/fits-tools' / bundle
    with _TOOLS_LOCK, ExitStack() as stack:
        paths = [root / name for name in ('provenance.json', f'fpack{suffix}', f'funpack{suffix}')]
        if not all(path.is_file() for path in paths):
            raise ValueError(f'The bundled FITS utilities for {bundle} are missing. '
                             'Run python scripts/build_fits_tools.py or reinstall NightSeam.')
        readers = [stack.enter_context(pinned_version(path)) for path in paths]
        versions = [read() if read else None for read in readers]
        token = (root, *versions) if all(version is not None for version in versions) else None
        if token is not None and _TOOLS_CACHE is not None and _TOOLS_CACHE[0] == token:
            return dict(_TOOLS_CACHE[1])
        provenance = json.loads(paths[0].read_text(encoding='utf-8'))
        result = {}
        for name, path in zip(('fpack', 'funpack'), paths[1:]):
            if hashlib.sha256(path.read_bytes()).hexdigest() != provenance['binaries'][path.name]['sha256']:
                raise ValueError('The bundled FITS utility failed its integrity check. Reinstall NightSeam.')
            result[name] = path
        if token is not None and [read() for read in readers] == versions:
            _TOOLS_CACHE = token, dict(result)
        return result


def native(executable, flags, source, destination, checkpoint):
    # No shell, source deletion or overwrite flags; output is private staging.
    # Keep source ancestry out of CFITSIO's extended filename parser. Preserve
    # absolute outputs normally (long '..' paths can fail on Windows copies).
    # Only shorten outputs whose archive ancestry contains parser syntax.
    # Popen's cwd never changes the server/worker cwd.
    source, destination = Path(source).absolute(), Path(destination).absolute()
    output_argument = str(destination)
    if any(character in output_argument for character in '[]()'):
        try:
            output_argument = Path(os.path.relpath(destination, source.parent)).as_posix()
        except ValueError:
            # An unrelated Windows drive cannot be addressed relatively.
            pass
    with subprocess.Popen([str(Path(executable).absolute()), *flags, '-O',
            output_argument, './' + source.name], cwd=source.parent,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        started = time.monotonic()
        try:
            while True:
                checkpoint()
                if time.monotonic() - started > 600:
                    raise ValueError('FITS conversion exceeded ten minutes for one exposure.')
                try:
                    output, error = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            process.kill()
            process.communicate()
            raise
        if process.returncode:
            message = (error or output).decode('utf-8', 'replace').strip()[-1200:]
            raise ValueError(f'FITS utility failed: {message}')


def layouts(path, checkpoint):
    """Inspect every HDU without decoding pixels; require complete plain FITS."""
    headers = []
    with path.open('rb') as stream:
        reader = fits._Reader(stream, False, path.stat().st_size, checkpoint)
        for index in range(fits.MAX_HDUS):
            header = fits._header(reader, primary=index == 0)
            if header is None:
                return headers
            headers.append(header)
            reader.skip(fits._data_size(header))
            if stream.tell() == reader.size:
                return headers
    raise ValueError('Too many FITS extensions for safe conversion.')


def kind(path, checkpoint):
    if path.name.lower().endswith('.gz'):
        return 'unsupported'
    headers = layouts(path, checkpoint)
    images = [h for i, h in enumerate(headers) if h.get('ZIMAGE') is True or
              ((i == 0 or str(h.get('XTENSION', '')).strip() == 'IMAGE') and h.get('NAXIS', 0) > 0)]
    if images and all(h.get('ZIMAGE') is True and h.get('ZCMPTYPE', '').strip() == 'RICE_1' for h in images):
        return 'rice'
    if any(h.get('ZIMAGE') for h in images) or not images:
        return 'unsupported'
    for header in images:
        if header.get('BITPIX') not in (8, 16, 32) or header.get('FZI2F') is True:
            return 'unsupported'
        if str(header.get('FZALGOR', 'RICE_1')).strip().upper() not in ('RICE', 'RICE_1'):
            return 'unsupported'
    return 'integer'


def fingerprint(root, relative, identity, checkpoint):
    stat, digest = _inspect(root, relative, identity, True, checkpoint, lambda _: None)
    return digest, stat.st_size, stat.st_mtime_ns


def encode_verified(source, output, restored, root, identity, expected, checkpoint, utilities=None,
                    *, verify_roundtrip=True, source_version=None):
    """Verify encoded storage; strict callers also require an exact round trip."""
    relative = lambda path: path.relative_to(root).as_posix()
    try:
        utilities = utilities or tools()
        native(utilities['fpack'], ['-r'], source, output, checkpoint)
        if kind(output, checkpoint) != 'rice':
            raise RiceCompressionError('The encoder did not produce lossless Rice FITS.')
        if verify_roundtrip:
            native(utilities['funpack'], ['-C'], output, restored, checkpoint)
            if fingerprint(root, relative(restored), identity, checkpoint)[:2] != expected[:2]:
                raise RiceCompressionError('The restored file does not match the original SHA-256 and size.')
        source_parent = source.parent.stat()
        source_unchanged = (source_version[0]() == source_version[1] if source_version else
            fingerprint(source.parent, source.name, (source_parent.st_dev, source_parent.st_ino), checkpoint)[:2] == expected[:2])
        if not source_unchanged:
            raise RiceCompressionError('The original changed during compression.')
        result = fingerprint(root, relative(output), identity, checkpoint)
        with output.open('r+b') as stage:
            stage.flush()
            os.fsync(stage.fileno())
        return result
    except RiceCompressionError:
        raise
    except ValueError as exc:
        raise RiceCompressionError(str(exc)) from exc


def _stage_name(token):
    if safe_token(token) != token:
        raise ValueError('Invalid compression recovery token.')
    return '.nightseam-rice-' + token


def _image_token(image_id):
    if type(image_id) is not int or image_id <= 0 or safe_token(str(image_id)) is None:
        raise ValueError('Invalid stored exposure ID.')
    return str(image_id)


def _cleanup(root, stage, identity):
    path = _contained(root, stage, identity)
    if not path.exists():
        return
    # Private stage has only flat regular files; never recursively follow links.
    for child in path.iterdir():
        relative = stage + '/' + child.name
        target = _contained(root, relative, identity)
        if not target.is_file() or target.is_symlink() or target.is_junction():
            raise ValueError('Unexpected compression recovery contents; preserve staging.')
        with _parent(root, relative, identity):
            target.unlink()
    with _parent(root, stage, identity):
        path.rmdir()


def _remove_copy_stage(description, token):
    path = Path(description['copy_stage'])
    parent = path.parent
    expected_name = '.NightSeam-library-'
    if (not path.is_absolute() or not path.name.startswith(expected_name) or not path.name.endswith('-' + token[:8]) or
            tuple(map(str, (parent.stat().st_dev, parent.stat().st_ino))) != tuple(description['copy_parent_identity']) or
            any(p.is_symlink() or p.is_junction() for p in [path, *path.parents])):
        raise ValueError('Library-copy recovery destination changed; preserve its files.')
    if path.exists():
        if any(p.is_symlink() or p.is_junction() for p in path.rglob('*')):
            raise ValueError('Linked library-copy staging path; preserve recovery files.')
        # This exact new folder was recorded before creation, in its selected
        # destination. Never delete the finished, non-hidden library folder.
        shutil.rmtree(path)


def recover(db):
    """Undo interrupted publication, or finish cleanup after a committed switch."""
    # A normal startup has nothing to recover. In particular, do not wait on
    # another archive operation merely to discover that the journal is empty.
    if not db.pending_compression():
        return
    with db.archive_lock:
        with db.lock:
            entries = [dict(r) for r in db.conn.execute('SELECT * FROM compression_journal')]
        for entry in entries:
            plan = json.loads(entry['plan_json'])
            if isinstance(plan, dict) and entry['state'] == 'committed':
                with _transaction(db) as conn:
                    conn.execute('DELETE FROM compression_journal WHERE token=?', (entry['token'],))
                continue
            root = Path(entry['archive']).resolve(strict=True)
            identity = tuple(map(int, (entry['root_dev'], entry['root_ino'])))
            stage = _stage_name(entry['token'])
            if isinstance(plan, dict):
                _remove_copy_stage(plan, entry['token'])
                plan = []
            if entry['state'] != 'committed':
                for item in reversed(plan):
                    backup_rel = stage + '/' + _image_token(item['id']) + '.original'
                    backup = _contained(root, backup_rel, identity)
                    if not backup.exists():
                        continue
                    original = _contained(root, item['old'], identity)
                    target = _contained(root, item['new'], identity)
                    if original.exists():
                        raise ValueError('Original path occupied during compression recovery.')
                    if target.exists():
                        if fingerprint(root, item['new'], identity, lambda: None)[:2] != (item['sha256'], item['size']):
                            raise ValueError('Converted file changed; preserve compression recovery files.')
                        with _parent(root, item['new'], identity):
                            target.unlink()
                    _move(root, backup_rel, item['old'], identity)
            _cleanup(root, stage, identity)
            with _transaction(db) as conn:
                conn.execute('DELETE FROM compression_journal WHERE token=?', (entry['token'],))


def _selection(db, anchor_id, expected_ids):
    images = db.images()
    anchor = next((r for r in images if r['id'] == anchor_id), None)
    if not anchor:
        raise ValueError('The selected exposure no longer exists. Reload the library.')
    session = session_for_anchor(db.images_for_object(anchor['object_id'], anchor['object_name']), anchor_id)
    ids = set(session['image_ids'])
    if ids != set(expected_ids) or len(expected_ids) != len(ids):
        raise ValueError('Session membership changed. Reload the library before converting it.')
    return [r for r in images if r['id'] in ids]


def _mapping(db, image_id):
    with db.lock:
        row = db.conn.execute('SELECT * FROM image_compression WHERE image_id=?', (image_id,)).fetchone()
        return dict(row) if row else None


def _copy_catalog(db, stage, final, manifest, checkpoint):
    # Archive membership is stable under archive_lock. A separate read connection
    # snapshots committed SQLite data without occupying the live connection lock
    # while writing or updating the copy, and includes committed WAL contents.
    uri = db.path.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as source, closing(sqlite3.connect(stage / 'nightseam.db')) as backup:
        source.backup(backup, pages=128, progress=lambda *_: checkpoint())
        backup.execute('DELETE FROM compression_journal')
        backup.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
            ('image_dir', json.dumps(str(final / 'images'))))
        stamp = datetime.now(UTC).isoformat()
        for item in manifest:
            checkpoint()
            rel = item['stored_file'][len('images/'):]
            mtime = (stage / item['stored_file']).stat().st_mtime_ns
            backup.execute('UPDATE images SET file=?,content_sha256=?,file_size=?,file_mtime_ns=?,storage_compressed=? WHERE id=?',
                (rel, item['stored_sha256'], item['stored_size'], mtime,
                 True if item['rice'] else item['record'].get('storage_compressed'), item['record']['id']))
            backup.execute('INSERT OR REPLACE INTO image_integrity VALUES(?,?,?,?,?,?)',
                (item['record']['id'], item['stored_sha256'], item['stored_size'], mtime, 'verified_library_copy', stamp))
            if item['original']:
                m = item['original']
                backup.execute('INSERT OR REPLACE INTO image_compression VALUES(?,?,?,?,?,?,?)',
                    tuple(m[k] for k in ('image_id', 'original_sha256', 'original_size', 'original_name', 'compressed_sha256', 'compressed_size', 'current_sha256')))
        backup.commit()
        if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('The copied catalog failed SQLite verification.')


def convert(db, mode, anchor_id, image_ids, cancel, publish, destination=None, compression_mode='safe'):
    if compression_mode not in ('fast', 'safe'):
        raise ValueError('Compression mode must be fast or safe.')
    abort = threading.Event()
    lineage = None
    def checkpoint():
        if cancel.is_set() or abort.is_set():
            raise Cancelled

    def parallel(items, function):
        def guarded(item):
            with lineage.use():
                return function(item)
        # Never recover or remove staging while a worker still uses it.
        with ThreadPoolExecutor(max_workers=max(1, min(24, os.cpu_count() or 1, len(items))),
                                thread_name_prefix='rice-worker') as pool:
            futures = [pool.submit(guarded, item) for item in items]
            try:
                for future in as_completed(futures):
                    yield future.result()
            except BaseException:
                abort.set()
                for future in futures:
                    future.cancel()
                raise
    acquired = False
    stage = None
    export_stage = None
    journal = False
    committed = False
    token = uuid.uuid4().hex
    report = dict(processed=0, converted=0, skipped=0, total=0, phase='waiting', bytes_before=0, bytes_after=0, published=0, publication_total=0, compression_mode=compression_mode,
                  skipped_missing=0, skipped_unsupported=0, skipped_files=[])
    def note_skip(record, reason):
        report['skipped_' + reason] += 1
        # Keep job polling bounded even for a very large damaged session.
        if len(report['skipped_files']) < 50:
            report['skipped_files'] = [*report['skipped_files'],
                dict(file=record['file'], reason=reason)]
    try:
        while not acquired:
            checkpoint()
            acquired = db.archive_lock.acquire(timeout=.1)
        recover(db)
        if db.pending_trash():
            raise ValueError('Recover the interrupted legacy trash operation before converting files.')
        checkpoint()
        configured, snapshot = db.integrity_snapshot()
        root = Path(configured).resolve(strict=True)
        identity = (root.stat().st_dev, root.stat().st_ino)
        if Path(configured).is_symlink() or Path(configured).is_junction():
            raise ValueError('Linked archive roots cannot be converted.')
        records = db.images() if mode == 'copy' else _selection(db, anchor_id, image_ids)
        if not records:
            raise ValueError('The library is empty.')
        utilities = tools()
        baselines = {r['id']: r for r in snapshot}
        report.update(total=len(records), publication_total=len(records), phase='preparing')
        available_records = []
        for record in records:
            checkpoint()
            source = _contained(root, record['file'], identity)
            try:
                source.stat()
            except FileNotFoundError:
                if mode != 'compress':
                    raise
                # A missing leaf/parent is an existing catalog finding, not an
                # excuse to ignore a vanished/replaced archive root or links.
                _contained(root, record['file'], identity)
                note_skip(record, 'missing')
                report['processed'] += 1
                report['skipped'] += 1
            else:
                available_records.append(record)
        publish(dict(report))
        stage = _stage_name(token)
        # Journal exists before staging, so interrupted preparation is recoverable.
        with _transaction(db) as conn:
            conn.execute('INSERT INTO compression_journal VALUES(?,?,?,?,?,?)',
                (token, str(root), str(identity[0]), str(identity[1]), 'prepared', '[]'))
        journal = True
        work_root, work_identity = root, identity
        if mode != 'copy':
            _mkdir(root, stage, identity, exclusive=True)
        if mode == 'copy':
            requested = Path(destination).expanduser()
            if not requested.is_absolute() or not requested.is_dir():
                raise ValueError('Choose an existing absolute destination folder.')
            if any(p.is_symlink() or p.is_junction() for p in [requested, *requested.parents]):
                raise ValueError('Linked destination folders cannot be used.')
            export_root = requested.resolve(strict=True)
            if export_root.is_relative_to(root) or root.is_relative_to(export_root):
                raise ValueError('Choose a destination outside the current archive.')
            export_name = 'NightSeam-library-' + datetime.now(UTC).strftime('%Y%m%d-%H%M%S') + '-' + token[:8]
            export_stage = export_root / ('.' + export_name)
            with _transaction(db) as conn:
                conn.execute('UPDATE compression_journal SET plan_json=? WHERE token=?',
                    (json.dumps(dict(copy_stage=str(export_stage), copy_parent_identity=[str(export_root.stat().st_dev), str(export_root.stat().st_ino)])), token))
            export_stage.mkdir()
            final = export_root / export_name
            work_root = export_stage
            work_identity = (work_root.stat().st_dev, work_root.stat().st_ino)
            _mkdir(work_root, stage, work_identity, exclusive=True)
            # Resolve/create shared output parents before workers start. A
            # partially created lineage must not race safe_join resolution.
            for relative_parent in {Path(r['file']).parent.as_posix() for r in records}:
                parent = safe_join(export_stage, 'images/' + relative_parent)
                if parent is None:
                    raise ValueError('Unsafe library-copy destination folder.')
                parent.mkdir(parents=True, exist_ok=True)
        locations = [(root, record['file'], identity) for record in available_records]
        locations.append((work_root, stage + '/anchor', work_identity))
        if mode == 'copy':
            locations.extend((work_root, 'images/' + record['file'], work_identity) for record in records)
        lineage = PinnedParents(locations)
        plan, manifest = [], []
        report['phase'] = 'compressing' if mode != 'uncompress' else 'uncompressing'
        publish(dict(report))
        def prepare(record, version=None, original_version=None):
            checkpoint()
            relative = safe_rel(record['file'])
            source = _contained(root, relative, identity)
            current = fingerprint(root, relative, identity, checkpoint)
            baseline = baselines[record['id']]
            if baseline.get('expected_sha256') and (current[0] != baseline['expected_sha256'] or
                    (baseline.get('expected_size') is not None and current[1] != baseline['expected_size'])):
                raise ValueError(f'Integrity check failed: {relative}. Check archive health first.')
            try:
                file_kind = kind(source, checkpoint)
            except fits.FitsMetadataError:
                if mode != 'copy':
                    raise
                file_kind = 'unsupported'
            mapping = _mapping(db, record['id'])
            output_rel = relative
            converted = False
            temporary = stage + '/' + _image_token(record['id']) + '.output'
            output = _contained(work_root, temporary, work_identity)
            if mode != 'uncompress' and file_kind == 'integer':
                output_rel = relative + '.fz'
                check_rel = stage + '/' + _image_token(record['id']) + '.roundtrip.fit'
                check_path = _contained(work_root, check_rel, work_identity)
                with _parent(root, relative, identity), _parent(work_root, temporary, work_identity):
                    packed = encode_verified(source, output, check_path, work_root, work_identity, current, checkpoint, utilities,
                        verify_roundtrip=compression_mode == 'safe',
                        source_version=(version, original_version) if original_version is not None else None)
                check_path.unlink(missing_ok=True)
                mapping = dict(image_id=record['id'], original_sha256=current[0], original_size=current[1],
                    original_name=Path(relative).name, compressed_sha256=packed[0], compressed_size=packed[1], current_sha256=packed[0])
                converted = True
            elif mode == 'uncompress' and file_kind == 'rice':
                if relative.lower().endswith('.fz'):
                    output_rel = relative[:-3]
                    if not output_rel.lower().endswith(('.fit', '.fits', '.fts')):
                        output_rel += '.fits'
                else:
                    output_rel = relative + '.fits'
                if mapping:
                    output_rel = (Path(relative).parent / mapping['original_name']).as_posix()
                with _parent(root, relative, identity), _parent(work_root, temporary, work_identity):
                    native(utilities['funpack'], ['-C'], source, output, checkpoint)
                unpacked = fingerprint(work_root, temporary, work_identity, checkpoint)
                if mapping and unpacked[:2] != (mapping['original_sha256'], mapping['original_size']):
                    raise ValueError(f'Original SHA-256 restoration failed: {relative}')
                if mapping:
                    mapping['current_sha256'] = unpacked[0]
                converted = True
            unchanged = (version() == original_version if original_version is not None else
                         fingerprint(root, relative, identity, checkpoint) == current)
            if not unchanged:
                raise ValueError(f'Source changed during conversion: {relative}')
            result = packed if converted and mode != 'uncompress' else unpacked if converted else current
            if converted and mode == 'uncompress':
                with output.open('r+b') as staged_file:
                    staged_file.flush()
                    os.fsync(staged_file.fileno())
            item = exported = None
            if mode == 'copy':
                target = safe_join(export_stage, 'images/' + output_rel)
                if target is None or target.exists():
                    raise ValueError(f'Duplicate or unsafe library-copy destination: {output_rel}')
                target.parent.mkdir(parents=True, exist_ok=True)
                with _parent(work_root if converted else root, temporary if converted else relative, work_identity if converted else identity):
                    with (output if converted else source).open('rb') as src, target.open('xb') as dst:
                        digest = digests.sha256()
                        while chunk := src.read(1 << 20):
                            checkpoint()
                            dst.write(chunk)
                            digest.update(chunk)
                        dst.flush()
                        os.fsync(dst.fileno())
                if digest.hexdigest() != result[0]:
                    raise ValueError('Library-copy verification failed.')
                # Re-read the destination, independently of the write stream.
                with target.open('rb') as verify:
                    digest = digests.sha256()
                    while chunk := verify.read(1 << 20):
                        checkpoint()
                        digest.update(chunk)
                if digest.hexdigest() != result[0]:
                    raise ValueError('Library-copy destination hash failed.')
                exported = dict(record=record, stored_file='images/' + output_rel,
                    stored_sha256=result[0], stored_size=result[1], original=mapping,
                    rice=file_kind == 'rice' or converted)
                if converted:
                    output.unlink()
            elif converted:
                destination_path = _contained(root, output_rel, identity)
                if output_rel == relative or destination_path.exists():
                    raise ValueError(f'Destination already exists: {output_rel}')
                item = dict(id=record['id'], old=relative, new=output_rel, sha256=result[0], size=result[1],
                    mtime_ns=result[2], source_sha256=current[0], source_size=current[1], mapping=mapping)
            reason = 'unsupported' if mode == 'compress' and file_kind == 'unsupported' else None
            return item, exported, converted, current[1], result[1], record, reason

        def prepare_pinned(record):
            source = _contained(root, record['file'], identity)
            with pinned_version(source) as version:
                before = version() if version else None
                return prepare(record, version, before)

        with closing(parallel(available_records, prepare_pinned)) as results:
            for item, exported, converted, before_size, after_size, record, reason in results:
                if item:
                    plan.append(item)
                if exported:
                    manifest.append(exported)
                    report['published'] += 1
                report['bytes_before'] += before_size
                report['bytes_after'] += after_size
                report['processed'] += 1
                report['converted' if converted else 'skipped'] += 1
                if reason:
                    note_skip(record, reason)
                publish(dict(report))
        plan.sort(key=lambda item: item['id'])
        manifest.sort(key=lambda item: item['record']['id'])
        if len({item['new'].casefold() for item in plan}) != len(plan):
            raise ValueError('Duplicate compression destination; originals retained.')
        report['publication_total'] = len(records) if mode == 'copy' else len(plan)
        checkpoint()
        if mode == 'copy':
            lineage.close()
            _cleanup(work_root, stage, work_identity)
            report['phase'] = 'committing'
            publish(dict(report))
            _copy_catalog(db, export_stage, final, manifest, checkpoint)
            with (export_stage / 'manifest.json').open('x', encoding='utf-8') as metadata:
                json.dump(dict(version=1, codec='RICE_1', restore_command='funpack -C -O restored.fit compressed.fit.fz', files=manifest), metadata, indent=2)
                metadata.write('\n')
                metadata.flush()
                os.fsync(metadata.fileno())
            checkpoint()
            export_stage.rename(final)
            export_stage = None
            with _transaction(db) as conn:
                conn.execute("UPDATE compression_journal SET state='committed' WHERE token=?", (token,))
            report['destination'] = str(final)
            committed = True
        else:
            with _transaction(db) as conn:
                conn.execute('UPDATE compression_journal SET plan_json=? WHERE token=?', (json.dumps(plan), token))
            report['phase'] = 'checking_originals'
            publish(dict(report))
            def verify_source(item):
                checkpoint()
                if fingerprint(root, item['old'], identity, checkpoint)[:2] != (item['source_sha256'], item['source_size']):
                    raise ValueError('Source changed before publication.')
            with closing(parallel(plan, verify_source)) as results:
                for _ in results:
                    pass
            report['phase'] = 'publishing'
            publish(dict(report))
            # Retain every original in staging; recovery undoes partial publication.
            def publish_file(item):
                checkpoint()
                _move(root, item['old'], stage + '/' + _image_token(item['id']) + '.original', identity)
                _move(root, stage + '/' + _image_token(item['id']) + '.output', item['new'], identity)
                if fingerprint(root, stage + '/' + _image_token(item['id']) + '.original', identity, checkpoint)[:2] != (item['source_sha256'], item['source_size']):
                    raise ValueError('Original changed during publication; recovering session.')
                if fingerprint(root, item['new'], identity, checkpoint)[:2] != (item['sha256'], item['size']):
                    raise ValueError('Converted file changed during publication; preserve recovery files.')
                return item['id']
            with closing(parallel(plan, publish_file)) as results:
                for _ in results:
                    report['published'] += 1
                    publish(dict(report))
            checkpoint()
            report['phase'] = 'committing'
            publish(dict(report))
            with _transaction(db) as conn:
                current_records = {r['id']: r for r in db.images()}
                if any(current_records.get(r['id']) != r for r in records):
                    raise ValueError('The library changed during conversion.')
                stamp = datetime.now(UTC).isoformat()
                for item in plan:
                    conn.execute('UPDATE images SET file=?,content_sha256=?,file_size=?,file_mtime_ns=?,storage_compressed=? WHERE id=?',
                        (item['new'], item['sha256'], item['size'], item['mtime_ns'], mode == 'compress', item['id']))
                    conn.execute('INSERT OR REPLACE INTO image_integrity VALUES(?,?,?,?,?,?)',
                        (item['id'], item['sha256'], item['size'], item['mtime_ns'], 'rice_conversion_fast' if mode == 'compress' and compression_mode == 'fast' else 'verified_rice_conversion', stamp))
                    if item['mapping']:
                        m = item['mapping']
                        conn.execute('INSERT OR REPLACE INTO image_compression VALUES(?,?,?,?,?,?,?)',
                            tuple(m[k] for k in ('image_id', 'original_sha256', 'original_size', 'original_name', 'compressed_sha256', 'compressed_size', 'current_sha256')))
                conn.execute("UPDATE compression_journal SET state='committed' WHERE token=?", (token,))
            committed = True
        report['phase'] = 'cleaning'
        publish(dict(report))
        lineage.close()
        recover(db)
        journal = False
        return dict(report, status='completed', phase='complete')
    finally:
        try:
            if lineage is not None:
                lineage.close()
            if journal:
                recover(db)
            if export_stage is not None and export_stage.exists():
                # Only our uniquely created destination staging directory.
                if export_stage.parent != export_root or export_stage.name != '.' + export_name:
                    raise ValueError('Invalid export cleanup path.')
                if any(p.is_symlink() or p.is_junction() for p in export_stage.rglob('*')):
                    raise ValueError('Linked export staging path; preserve recovery files.')
                shutil.rmtree(export_stage)
        except Exception:
            log.exception('Compression cleanup/recovery requires attention (committed=%s)', committed)
            raise
        finally:
            if acquired:
                db.archive_lock.release()


class CompressionManager:
    def __init__(self, db):
        self.db, self.lock = db, threading.RLock()
        self.job = None
        self.thread = None
        self.cancel_event = threading.Event()
        self.stopping = False

    def snapshot(self, job_id=None):
        with self.lock:
            if not self.job or (job_id is not None and self.job['id'] != job_id):
                raise KeyError(job_id)
            result = dict(self.job)
            if result['status'] in ('running', 'cancelling'):
                result['elapsed_seconds'] = round(time.monotonic() - self.started, 1)
            return result

    def start(self, mode, anchor_id=None, image_ids=(), destination=None, compression_mode='safe'):
        with self.lock:
            if self.stopping or (self.thread and self.thread.is_alive()):
                raise ValueError('A conversion is already running or the application is closing.')
            self.cancel_event = threading.Event()
            self.started = time.monotonic()
            self.job = dict(id=uuid.uuid4().hex, mode=mode, status='running', phase='waiting',
                total=0, processed=0, converted=0, skipped=0, elapsed_seconds=0, published=0, publication_total=0, compression_mode=compression_mode)
            self.thread = threading.Thread(target=self._run, args=(mode, anchor_id, image_ids, destination, compression_mode),
                daemon=True, name='fits-compression')
            self.thread.start()
            return dict(self.job)

    def _publish(self, value):
        with self.lock:
            self.job.update(value)

    def _run(self, mode, anchor, ids, destination, compression_mode):
        try:
            result = convert(self.db, mode, anchor, ids, self.cancel_event, self._publish, destination, compression_mode)
        except Cancelled:
            result = dict(status='cancelled', phase='cancelled')
        except Exception as exc:
            log.exception('FITS compression operation failed')
            result = dict(status='failed', phase='failed', error=str(exc))
        with self.lock:
            self.job.update(result, elapsed_seconds=round(time.monotonic() - self.started, 1))

    def cancel(self, job_id):
        with self.lock:
            self.snapshot(job_id)
            if self.job['status'] in ('running', 'cancelling'):
                self.job['status'] = 'cancelling'
                self.cancel_event.set()

    def shutdown(self):
        with self.lock:
            self.stopping = True
            self.cancel_event.set()
            thread = self.thread
        if thread:
            thread.join()
