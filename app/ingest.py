"""Image archive ingest: parse FITS headers, resolve objects, store files.

Files are streamed to the archive (only the header is buffered in memory)
and capped at MAX_INGEST_BYTES so an oversized upload cannot exhaust RAM
or disk.
"""

from __future__ import annotations

import logging
import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from itertools import chain

from . import fits as fitsio
from . import imglib
from . import fits_metadata
from . import paths
from .file_version import pinned_version
from .database import Database, normalize_session_date_obs

log = logging.getLogger(__name__)

MAX_INGEST_BYTES = 1 << 30          # 1 GiB per file
HEADER_READ = 1_500_000             # fits._MAX_BLOCKS blocks ~= 1.47 MB
_COPY_CHUNK = 1 << 20               # 1 MB streaming buffer
_STAGE_LOCK = threading.RLock()
_ACTIVE_STAGES: set[Path] = set()
_STAGE_NAME = re.compile(r"^\.nightseam-import-(\d+)-[A-Za-z0-9_]+\.part$")
_STALE_STAGE_SECONDS = 3600
_FINGERPRINT_CACHE_LIMIT = 20000
_BATCH_PIN_LIMIT = 4096
_BATCH_HANDLE_LIMIT = 16384
# Admission/fallback hashing can open one version pin and one read stream in
# addition to the retained/provisional request pins. Keep that headroom free.
_BATCH_IO_HANDLES = 2
_CANDIDATE_PAGE_SIZE = 256
_WINDOWS_BATCH_PINS = os.name == 'nt'


@contextmanager
def _waiting_archive_lock(db, cancel):
    while not db.archive_lock.acquire(timeout=.1):
        _check_reindex_cancelled(cancel)
    try:
        _check_reindex_cancelled(cancel)
        yield
    finally:
        db.archive_lock.release()


class DedupBatch:
    """Request-local negative index, with Windows bytes/lineage pinned stable.

    A batch checks earlier same-size files once rather than once per upload,
    admitting newly published files lazily when they become candidates.
    Open writers, unsupported versions and candidates above the handle bound
    retain the conservative individual checks. Positive matches always rehash.
    No database hash alone admits an entry; an earlier actual read or
    publication of the written bytes in this process may, while its durable
    change-journal version is unchanged.
    """
    def __init__(self):
        self.stack = ExitStack()
        self.entries = {}
        self.db = None
        self.root = None
        self.identity = None
        self.folders = set()
        self.directories = set()
        self.leased = False

    def close(self):
        if self.db is None:
            self.stack.close()
        else:
            with self.db.archive_lock.for_batch(self):
                try:
                    self.stack.close()
                finally:
                    if self.leased:
                        self.db.archive_lock.unreserve(self)
                    self.leased = False
                    self.db = self.root = self.identity = None
                    self.entries.clear()
                    self.folders.clear()
                    self.directories.clear()

    def negative(self, record, incoming):
        entry = self.entries.get(record['id'])
        return entry is not None and entry[0] == record['file'] and entry[1] != incoming

    def fingerprint(self, db, record, path, incoming, cancel=None, *, admit_only=False):
        """Measure a candidate and retain stable negative proof within this request.

        Caller owns the batch's archive lock. Only Windows writer-excluding pins
        qualify. A file not published by this process is hashed from actual
        archive bytes on first lookup, never from a database digest; positives
        continue to rehash.
        Failed admission retains no new directory/file handles.
        """
        if (self.root is not None and self.db is db and
                record['id'] not in self.entries and len(self.entries) < _BATCH_PIN_LIMIT and
                len(self.entries) + len(self.directories) + _BATCH_IO_HANDLES < _BATCH_HANDLE_LIMIT):
            from .archive_trash import _contained, _parent
            try:
                with ExitStack() as context:
                    _check_reindex_cancelled(cancel)
                    # safe_join resolves internal aliases. Validate the lexical
                    # row path even when its resolved parent is already pinned.
                    if _contained(self.root, record['file'], self.identity) != path:
                        raise ValueError('Candidate is outside its pinned archive.')
                    parent = path.parent
                    opened = None
                    if parent not in self.folders:
                        # Reserve one slot for the leaf before opening any new
                        # lineage. Failed suffixes never exceed the pin budget.
                        options = {'held': self.directories, 'max_new': _BATCH_HANDLE_LIMIT - _BATCH_IO_HANDLES -
                                   len(self.entries) - len(self.directories) - 1} if os.name == 'nt' else {}
                        opened = context.enter_context(_parent(self.root, record['file'], self.identity, **options))
                    version = context.enter_context(pinned_version(path))
                    before = version() if version else None
                    if before is not None:
                        # The outer handle already excludes writers and remains
                        # alive for this request. Read current bytes under that
                        # same lifetime instead of opening another version pin,
                        # unless an earlier actual read has the same durable
                        # (change-journal) version.
                        durable = _durable_version(version)
                        measured = _cached_negative(db, path, durable, incoming)
                        read = measured is None
                        if read:
                            measured = (_fingerprint_file(path, cancel) if cancel is not None else
                                        _fingerprint_file(path))
                        _check_reindex_cancelled(cancel)
                        unchanged = version() == before
                        if read:
                            _remember_fingerprint(db, path, durable, measured,
                                                  unchanged and _durable_version(version) == durable)
                        if measured is not None and unchanged:
                            if not self.leased:
                                db.archive_lock.reserve(self)
                                self.leased = True
                            self.stack.enter_context(context.pop_all())
                            if os.name == 'nt' and opened is not None:
                                self.directories.update(opened)
                            self.folders.add(parent)
                            self.entries[record['id']] = record['file'], measured[0]
                        return measured
            except (OSError, ValueError):
                pass
        if admit_only:
            return None
        _check_reindex_cancelled(cancel)
        return _dedup_fingerprint(db, path, incoming, cancel)

    @classmethod
    def prepare(cls, db, archive, sizes, cancel=None):
        """Capture the request root without reading unrelated archive bytes.

        Candidates acquire their file/lineage pins only when a negative lookup
        actually needs them. Exact duplicates can therefore finish after one
        current-byte verification, independent of the same-size population.
        `sizes` is None when a streamed request does not know its uploads yet.
        """
        index = cls()
        if not _WINDOWS_BATCH_PINS or sizes is not None and not sizes or not hasattr(db.archive_lock, 'reserve'):
            return index
        try:
            with _waiting_archive_lock(db, cancel):
                root = archive.resolve(strict=True)
                info = root.stat()
                index.db, index.root = db, root
                index.identity = info.st_dev, info.st_ino
            return index
        except BaseException:
            index.close()
            raise


async def _archive_io(function, *args, on_cancel=None, cancel_event=None, **kwargs):
    """Finish in-flight filesystem work before cancellation can clean it up."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if cancel_event is not None:
            cancel_event.set()
        # Repeated disconnect/shutdown cancellation must not leave a worker
        # using a stage after its cleanup has removed it.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            result = task.result()
            if on_cancel is not None:
                await _archive_io(on_cancel, result)
        except BaseException:
            pass
        raise


def _process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return False  # active stages in this process are tracked separately
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # denied/unknown: preserve
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _prepare_stage(archive: Path) -> Path:
    archive.mkdir(parents=True, exist_ok=True)
    with _STAGE_LOCK:
        # Only the PID-bearing namespace we own is eligible. Legacy or unknown
        # .part files are preserved; live imports (including other processes)
        # and recent leftovers cannot be mistaken for abandoned stages.
        for candidate in archive.glob(".nightseam-import-*.part"):
            match = _STAGE_NAME.fullmatch(candidate.name)
            if match is None or candidate in _ACTIVE_STAGES or candidate.is_symlink():
                continue
            try:
                if (time.time() - candidate.stat().st_mtime > _STALE_STAGE_SECONDS
                        and not _process_alive(int(match[1]))):
                    candidate.unlink()
            except OSError:
                pass
        fd, temporary = tempfile.mkstemp(prefix=f".nightseam-import-{os.getpid()}-",
                                         suffix=".part", dir=archive)
        os.close(fd)
        stage = Path(temporary)
        _ACTIVE_STAGES.add(stage)
        return stage


def _remove_stage(stage: Path) -> None:
    with _STAGE_LOCK:
        try:
            stage.unlink(missing_ok=True)
        finally:
            _ACTIVE_STAGES.discard(stage)


def _fingerprint_file(path: Path, cancel_event=None) -> tuple[str, int, int] | None:
    """Hash current bytes, refusing files that change during the read."""
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(_COPY_CHUNK):
                _check_reindex_cancelled(cancel_event)
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return digest.hexdigest(), after.st_size, after.st_mtime_ns
    except OSError:
        return None


def _candidate_rows(db, selectors, *, limit, upper=None, after=None, fingerprints=True):
    """Merge indexed ID-ordered streams instead of sorting all remaining hits.

    UNION removes overlap between hash/size/unknown-size matches. A stable ID
    uniquely identifies the row, so merging preserves the former OR ordering.
    Selectors are internal SQL constants; all values remain bound parameters.
    """
    bounds = ('' if upper is None else ' AND id<=?') + ('' if after is None else ' AND id>?')
    ids = (() if upper is None else (upper,)) + (() if after is None else (after,))
    projection = 'id,file' + (',content_sha256,file_size,file_mtime_ns' if fingerprints else '')
    query = ' UNION '.join(f'SELECT {projection} FROM images WHERE {where}{bounds}'
                           for where, _ in selectors) + ' ORDER BY id LIMIT ?'
    values = tuple(value for _, args in selectors for value in (*args, *ids)) + (limit,)
    with db.lock:
        cursor = db.conn.execute(query, values)
        try:
            return [dict(row) for row in cursor]
        finally:
            cursor.close()


def _image_candidates(db, selectors, cancel=None):
    """Read bounded pages without retaining a cursor/DB lock during file IO.

    The archive lock serializes publication; an upper ID also prevents later
    insertions from extending this search. Every matching candidate up to that
    boundary remains eligible, even after admission reaches its pin budget.
    """
    _check_reindex_cancelled(cancel)
    with db.lock:
        upper = db.conn.execute('SELECT MAX(id) FROM images').fetchone()[0]
    if upper is None:
        return
    last = None
    while True:
        _check_reindex_cancelled(cancel)
        records = _candidate_rows(db, selectors, limit=_CANDIDATE_PAGE_SIZE, upper=upper, after=last)
        if not records:
            return
        yield from records
        if len(records) < _CANDIDATE_PAGE_SIZE:
            return
        last = records[-1]['id']


def _batch_candidates(db, digest, size, cancel=None):
    return _image_candidates(db, [('content_sha256=?', (digest,)), ('file_size=?', (size,)),
                                 ('file_size IS NULL', ())], cancel)


def _alias_rows(db, digest, size, upper, after):
    bounds = ' AND c.image_id<=?' + ('' if after is None else ' AND c.image_id>?')
    ids = (upper,) + (() if after is None else (after,))
    select = ('SELECT images.id AS id,images.file,c.current_sha256 AS mapped_sha256 '
              'FROM image_compression c JOIN images ON images.id=c.image_id WHERE ')
    query = ' UNION '.join(select + predicate + bounds for predicate in
        ('c.original_sha256=? AND c.original_size=?', 'c.compressed_sha256=? AND c.compressed_size=?'))
    with db.lock:
        cursor = db.conn.execute(query + ' ORDER BY id LIMIT ?',
            (digest, size, *ids, digest, size, *ids, _CANDIDATE_PAGE_SIZE))
        try:
            return [dict(row) for row in cursor]
        finally:
            cursor.close()


def _alias_candidates(db, digest, size, cancel=None):
    _check_reindex_cancelled(cancel)
    with db.lock:
        upper = db.conn.execute('SELECT MAX(image_id) FROM image_compression').fetchone()[0]
    last = None
    while upper is not None:
        _check_reindex_cancelled(cancel)
        records = _alias_rows(db, digest, size, upper, last)
        yield from records
        if len(records) < _CANDIDATE_PAGE_SIZE:
            return
        last = records[-1]['id']


def _verified_image(db, record):
    """Hydrate only the row whose recorded path was actually verified."""
    image = db.image(record['id'])
    return image if image is not None and image['file'] == record['file'] else None


def _find_duplicate(db: Database, archive: Path, digest: str, size: int,
                    exclude_id: int | None = None, dedup_batch=None, cancel_event=None) -> dict | None:
    """Find matching local bytes, including originals from older versions.

    Caller holds db.archive_lock for lookup/finalization. Database methods take
    their own short-lived connection lock; hashing never holds it. Size and
    mtime are hints only: a matching cached digest is always checked against
    the actual archived file before an upload is skipped. Missing originals
    therefore cannot prevent users importing a replacement.
    """
    # A recorded conversion retains the original complete-byte identity as an
    # alias. Check current stored bytes against that mapping before deduping.
    for rec in _alias_candidates(db, digest, size, cancel_event):
        _check_reindex_cancelled(cancel_event)
        if rec['id'] == exclude_id:
            continue
        path = paths.safe_join(archive, rec['file'])
        measured = (_fingerprint_file(path, cancel_event) if cancel_event is not None else
                    _fingerprint_file(path)) if path is not None else None
        _check_reindex_cancelled(cancel_event)
        if measured and measured[0] == rec['mapped_sha256']:
            image = _verified_image(db, rec)
            if image is not None:
                return image
    # Recorded hashes only select the cheap first tier: actual archived bytes
    # must still match. A failed/missing/stale hint falls through to the complete
    # same-size search, including externally changed files with restored mtime.
    for rec in _image_candidates(db, [('content_sha256=?', (digest,))], cancel_event):
        _check_reindex_cancelled(cancel_event)
        if rec['id'] == exclude_id:
            continue
        path = paths.safe_join(archive, rec['file'])
        measured = (_fingerprint_file(path, cancel_event) if cancel_event is not None else
                    _fingerprint_file(path)) if path is not None else None
        _check_reindex_cancelled(cancel_event)
        if measured and measured[:2] == (digest, size):
            if (rec.get('content_sha256'), rec.get('file_size'), rec.get('file_mtime_ns')) != measured:
                db.set_image_fingerprint(rec['id'], *measured)
            image = _verified_image(db, rec)
            if image is not None:
                return image
    if dedup_batch is not None:
        candidates = _batch_candidates(db, digest, size, cancel_event)
    else:
        candidates = db.image_fingerprint_candidates(digest, size)
    for rec in candidates:
        _check_reindex_cancelled(cancel_event)
        if rec["id"] == exclude_id:
            continue
        if dedup_batch is not None:
            if dedup_batch.negative(rec, digest):
                continue
        original = paths.safe_join(archive, rec["file"])
        if original is None:
            continue
        try:
            stat = original.stat()
        except OSError:
            continue
        if stat.st_size != size or not original.is_file():
            continue
        cached = rec.get("content_sha256")
        # Restores and external tools can preserve size/mtime while replacing
        # bytes. A different cached digest must not reject a same-size original.
        measured = (dedup_batch.fingerprint(db, rec, original, digest, cancel_event)
                    if dedup_batch is not None else _dedup_fingerprint(db, original, digest, cancel_event))
        _check_reindex_cancelled(cancel_event)
        if measured is None:
            continue
        actual, actual_size, mtime_ns = measured
        if (cached, rec.get("file_size"), rec.get("file_mtime_ns")) != measured:
            db.set_image_fingerprint(rec["id"], actual, actual_size, mtime_ns)
        if actual_size == size and actual == digest:
            image = _verified_image(db, rec) if dedup_batch is not None else rec
            if image is not None:
                return image
    return None


def _dedup_fingerprint(db, path, incoming, cancel_event=None):
    """Avoid rereading unchanged negative candidates; positives are rehashed.

    Only actual byte reads, or this process's own publication of bytes it
    hashed while writing, populate this bounded, process-local cache, keyed by
    durable file versions (POSIX fstat/ctime, or Windows file id, timestamps and
    change-journal USN). Stored DB hashes and size/mtime alone never authorize
    skipping an upload.
    Callers serialize it with archive_lock.
    """
    with pinned_version(path) as version:
        before = version() if version else None
        durable = _durable_version(version) if before is not None else None
        cached = _cached_negative(db, path, durable, incoming)
        if cached is not None:
            return cached
        measured = _fingerprint_file(path, cancel_event) if cancel_event is not None else _fingerprint_file(path)
        _remember_fingerprint(db, path, durable, measured,
                              before is not None and version() == before and _durable_version(version) == durable)
        return measured


def _durable_version(version):
    durable = getattr(version, 'durable', None)
    return durable() if durable is not None else None


def _cached_negative(db, path, durable, incoming):
    """Reuse an earlier actual read only for a different digest at the same durable version."""
    cache = getattr(db, '_ingest_fingerprints', None)
    cached = cache.get(path) if durable is not None and cache else None
    if cached and cached[0] == durable and cached[1][0] != incoming:
        cache.move_to_end(path)
        return cached[1]
    return None


def _remember_fingerprint(db, path, durable, measured, unchanged):
    cache = getattr(db, '_ingest_fingerprints', None)
    if cache is None:
        cache = db._ingest_fingerprints = OrderedDict()
    if durable is not None and measured and unchanged:
        # Freeze admission at the bound rather than evicting on a full
        # ascending scan, which would make every subsequent scan thrash.
        if path in cache or len(cache) < _FINGERPRINT_CACHE_LIMIT:
            cache[path] = durable, measured
            cache.move_to_end(path)
    else:
        cache.pop(path, None)


def _remember_published(db, path, digest, size):
    """Seed the negative cache with the bytes this import just wrote.

    The digest was computed over exactly the bytes written to the stage that
    became `path`; the durable version is captured under the same archive lock
    that published it. Any later change advances that version, so the entry
    then misses and the file is read again. Positives still always rehash.
    """
    try:
        # NTFS records the rename's journal entry and change time when the
        # first handle after it closes; capture from the next, settled handle.
        with pinned_version(path):
            pass
        with pinned_version(path) as version:
            if version is None or version() is None:
                return
            durable = _durable_version(version)
            info = path.stat()
            if info.st_size == size:
                _remember_fingerprint(db, path, durable, (digest, size, info.st_mtime_ns), True)
    except OSError:
        pass


def _known_duplicate(db, archive, digest, size, enforce_root, session_target, frame, dedup_batch=None,
                     cancel_event=None):
    """Cheap exact-positive preflight avoids encoding already stored uploads.

    Finalization still checks all current same-size candidates and serializes
    publication. This early return only skips bytes freshly verified on disk.
    """
    with _batch_archive_lock(db, dedup_batch):
        _check_reindex_cancelled(cancel_event)
        if db.pending_compression() or db.pending_trash():
            return None
        if enforce_root and Path(db.image_dir()).resolve() != archive:
            return None
        if session_target is not None:
            try:
                if calibration_import_target(db, archive, session_target['anchor_id'], frame) != session_target:
                    return None
            except ValueError:
                return None
        for record in chain(_alias_candidates(db, digest, size, cancel_event),
                            _image_candidates(db, [('content_sha256=?', (digest,))], cancel_event)):
            _check_reindex_cancelled(cancel_event)
            target = paths.safe_join(archive, record['file'])
            expected = record.pop('mapped_sha256', digest)
            measured = (_fingerprint_file(target, cancel_event) if cancel_event is not None else
                        _fingerprint_file(target)) if target is not None else None
            _check_reindex_cancelled(cancel_event)
            if measured and measured[0] == expected:
                image = _verified_image(db, record)
                if image is not None:
                    return image
    return None


def _batch_archive_lock(db, batch):
    return db.archive_lock.for_batch(batch) if hasattr(db.archive_lock, 'for_batch') else db.archive_lock


def _duplicate_result(name: str, rec: dict) -> dict:
    return {"file": name, "ok": True, "skipped": True,
            "reason": "already_imported", "id": rec["id"],
            "stored": rec["file"],
            "object": {"id": rec.get("object_id"),
                       "name": rec.get("object_name"),
                       "designation": imglib.designation_of(None, rec)},
            **{key: rec.get(key) for key in
               ("exposure", "date_obs", "session_date_obs", "filter", "telescope", "imagetyp",
                "site_lat", "site_lon", *imglib.SESSION_FIELDS)}}


def _missing_original(db: Database, archive: Path, digest: str,
                      size: int) -> tuple[dict, Path] | None:
    """A trusted earlier fingerprint permits restoring a missing original."""
    for rec in db.images_with_digest(digest):
        if rec.get("file_size") != size:
            continue
        target = paths.safe_join(archive, rec["file"])
        if target is None:
            continue
        try:
            target.stat()
        except FileNotFoundError:
            return rec, target
        except OSError:
            continue
    return None


def _promote_stage(stage: Path, target: Path) -> None:
    """Publish without replacing an original created by another process.

    Hard links are atomic and avoid a second copy on supported filesystems.
    Exclusive creation also works on archive drives that do not support them.
    """
    try:
        os.link(stage, target)
    except FileExistsError:
        raise
    except OSError:
        created = False
        try:
            with target.open("xb") as destination:
                created = True
                with stage.open("rb") as source:
                    shutil.copyfileobj(source, destination, _COPY_CHUNK)
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise


def obj_slug(object_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9 _-]+", "", object_name).strip() or "misc"
    return paths.avoid_windows_device(slug)


FRAME_TYPES = {          # normalized IMAGETYP -> archive subfolder
    "light": "lights", "bias": "bias", "dark": "darks", "flat": "flats",
}
FRAME_DIRS = {v: k for k, v in FRAME_TYPES.items()}   # subfolder -> type


def classify_frame(imagetyp: str | None) -> str:
    """Normalize a FITS IMAGETYP value into 'light'/'bias'/'dark'/'flat'.

    Handles the multi-word spellings capture software writes ("Dark Frame",
    "Flat Field", "Bias Frame") as well as the single-word ones, and the
    "Master ..." qualifier stacking apps write on stacked calibration frames
    (PY-09: "Master Dark"/"Master Flat" used to fall through to `light`, so
    master calibration frames were archived under `lights/` and counted as
    light frames)."""
    t = re.sub(r"[^a-z]", "", str(imagetyp or "").lower())
    if t.startswith("master"):
        t = t[len("master"):]
    if t in ("", "l", "science") or t.startswith("light"):
        return "light"
    if t.startswith(("bias", "zero")):
        return "bias"
    if t.startswith("dark"):
        return "dark"
    if t.startswith("flat") or t.startswith("skyflat"):
        return "flat"
    return "light"          # unknown types default to lights


def session_folder_name(metadata) -> str:
    """A stable UTC interval folder, independent of import order or host zone."""
    kind = metadata["session_kind"]
    if kind == "undated":
        return "undated"
    start = metadata.get("session_start")
    if kind == "unassigned":
        return "unassigned_" + start[:10]
    stamp = datetime.fromisoformat(start.replace("Z", "+00:00"))
    identity = hashlib.sha256(metadata["session_key"].encode("utf-8")).hexdigest()[:16]
    return f"{kind}_{stamp:%Y-%m-%d_%H%M%S}_{identity}"


def _header_session(db: Database, header: dict, date_obs, target=None) -> dict:
    """Preserve measured location; explicit calibration copies membership only."""
    from .solar import session_metadata
    latitude, longitude = fitsio.parse_site_coordinates(header)
    if "SITELAT" not in header and "SITELONG" not in header:
        default = db.setting("observing_site")
        if isinstance(default, dict):
            latitude, longitude = fitsio.parse_site_coordinates(
                {"SITELAT": default.get("latitude"), "SITELONG": default.get("longitude")})
    metadata = session_metadata(date_obs, latitude, longitude)
    if target is not None:
        metadata = {key: target.get(key) for key in imglib.SESSION_FIELDS}
    return {"site_lat": latitude, "site_lon": longitude, **metadata}


def _session_dir(db: Database, archive: Path, slug: str, object_id,
                 object_name: str, date_obs: str | None,
                 session_override: str | None = None, metadata=None) -> Path:
    """Pick the immutable interval label; existing captures cannot move it."""
    if session_override:
        return archive / slug / session_override
    if metadata is None:
        metadata = _header_session(db, {}, date_obs)
    return archive / slug / session_folder_name(metadata)


def _reindex_session_metadata(db: Database, rec: dict, header: dict, storage_compressed=None) -> dict:
    # A missing header location may use the originally stored observing site;
    # a later default-site setting must not rewrite measured capture metadata.
    if ("SITELAT" not in header and "SITELONG" not in header and
            rec.get("site_lat") is not None and rec.get("site_lon") is not None):
        header = {**header, "SITELAT": rec["site_lat"], "SITELONG": rec["site_lon"]}
    captured = fitsio.capture_date_obs(header)
    logical_date = captured if rec.get("session_date_obs") is None else rec["session_date_obs"]
    metadata = _header_session(db, header, logical_date)
    metadata["date_obs"] = captured
    metadata["storage_compressed"] = storage_compressed
    if rec.get("session_date_obs") is not None and rec.get("session_key"):
        metadata.update({key: rec.get(key) for key in imglib.SESSION_FIELDS})
    return metadata


def calibration_import_target(db: Database, archive: Path, anchor_id: int,
                              frame: str) -> dict:
    """Resolve an existing logical session without trusting upload metadata."""
    if frame not in ("dark", "flat", "bias"):
        raise ValueError("A selected-session import must use dark, flat or bias.")
    if (not isinstance(anchor_id, int) or isinstance(anchor_id, bool)
            or not 1 <= anchor_id <= (1 << 63) - 1):
        raise ValueError("Selected session anchor must be a positive image id in range.")
    with db.archive_lock:
        anchor = db.image(anchor_id)
        if anchor is None:
            raise ValueError("Selected session no longer exists. Select a session again.")
        clean = paths.safe_rel(anchor.get("file"))
        parts = clean.split("/") if clean else []
        if (len(parts) not in (2, 3, 4)
                or (len(parts) > 2 and parts[-2].lower() not in FRAME_DIRS)
                or paths.safe_join(archive, clean) is None):
            raise ValueError("Selected session has an unsupported or unsafe archive path.")
        if anchor.get("object_id") is not None:
            images = db.images_for_object(anchor["object_id"], anchor["object_name"])
        else:
            # Unresolved Library objects group whitespace/case variants together.
            key = imglib.name_key(anchor.get("object_name") or "")
            images = [image for image in db.images()
                      if image.get("object_id") is None and
                      imglib.name_key(image.get("object_name") or "") == key]
        selected = imglib.session_for_anchor(images, anchor_id)
        if selected is None:
            raise ValueError("Selected session no longer exists. Select a session again.")
        stamp = selected.get("start")
        epoch = fitsio.date_epoch(stamp)
        session_date = normalize_session_date_obs(stamp) if epoch is not None else ""
        # Keep a modern session's existing folder. Legacy Object/lights anchors
        # gain the canonical Object/session/frame layout without moving files.
        session = parts[1] if len(parts) == 4 else session_folder_name(selected)
        if paths.safe_join(archive, f"{parts[0]}/{session}/{FRAME_TYPES[frame]}") is None:
            raise ValueError("Selected session has an unsafe calibration folder.")
        return {"anchor_id": anchor_id, "anchor": imglib.public_image_record(anchor),
                "slug": parts[0], "session": session,
                "session_date_obs": session_date,
                **{key: selected[key] for key in imglib.SESSION_FIELDS}}


def _unique_path(path: Path) -> Path:
    """`path`, or its first free `stem_2`/`stem_3`/... variant.

    The ONE uniquify rule for the archive: `_archive_target` uses it for a new
    ingest and `rename_archive_folder` uses it when a rename merges into a
    folder that already holds a same-named frame (PY-01) - an existing file is
    never replaced."""
    if not path.exists():
        return path
    stem, suffix = fits_metadata.filename_parts(path.name)
    n = 2
    while True:
        candidate = path.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _archive_target(archive: Path, name: str, slug: str,
                    session: Path, frame: str) -> Path:
    """Unique destination <archive>/<slug>/<session>/<frames>/<stem>..."""
    stem, suffix = fits_metadata.filename_parts(name)
    suffix = suffix or ".fit"
    # Stored paths must pass safe_rel: no control characters or device names.
    stem = re.sub(r"[\x00-\x1f\x7f]", "_", stem or "image")[:80]
    stem = paths.avoid_windows_device(stem)
    sub = session / FRAME_TYPES.get(frame, "lights")
    sub.mkdir(parents=True, exist_ok=True)
    for t in FRAME_TYPES.values():      # keep all four frame folders beside it
        (session / t).mkdir(parents=True, exist_ok=True)
    target = sub / f"{stem}{suffix}"
    return _unique_path(target)


async def ingest_one(db: Database, catalog: list[dict], archive: Path,
                     f, frame_override: str | None = None,
                     session_override: str | None = None,
                     resolver: imglib.CatalogResolver | None = None,
                     on_import=None, enforce_archive_root: bool = False,
                     session_target: dict | None = None,
                     compress: bool = False, compression_mode: str = 'safe',
                     before_publish=None, dedup_batch=None, workers=None) -> dict:
    """Stream one supported FITS exposure into the archive and record it.

    `f` is a fastapi UploadFile or an upload_stream.StreamedUpload. Only the
    header block is buffered; the remainder is streamed to disk. `workers`, an
    asyncio.Semaphore, is acquired only once the bytes are staged: a streamed
    request's later parts must keep arriving while earlier files wait to publish.
    Returns a per-file result dict.
    """
    if compression_mode not in ('fast', 'safe'):
        raise ValueError('Compression mode must be fast or safe.')
    name = Path(f.filename or "image.fit").name
    if not fits_metadata.is_fits_name(name):
        return {"file": name, "ok": False,
                "error": "Unsupported exposure filename (use FITS, .fz or FITS.gz)."}
    if getattr(f, "size", None) and f.size > MAX_INGEST_BYTES:
        return {"file": name, "ok": False,
                "error": f"file too large (max {MAX_INGEST_BYTES // (1 << 20)} MB)"}

    header = await f.read(HEADER_READ)
    if not header.startswith((b"SIMPLE", b"\x1f\x8b")):
        return {"file": name, "ok": False,
                "error": "Unsupported exposure file (SIMPLE header missing)"}

    stage = prepared = None
    working = False
    digest = hashlib.sha256()
    try:
        archive = await _archive_io(archive.resolve)
        stage = await _archive_io(_prepare_stage, archive, on_cancel=_remove_stage)
        streamed = await _stream_to(archive, stage, f, header, digest)
        if streamed is None:
            return {"file": name, "ok": False,
                    "error": f"file too large (max {MAX_INGEST_BYTES // (1 << 20)} MB)"}
        if workers is not None:
            await workers.acquire()
            working = True
        try:
            storage_info = {}
            hdr = await _archive_io(fits_metadata.read_metadata, stage, storage_info=storage_info)
        except fits_metadata.FitsMetadataError as exc:
            return {"file": name, "ok": False, "error": f"FITS metadata read failed: {exc}"}
        if not hdr:
            return {"file": name, "ok": False, "error": "Unsupported exposure file (SIMPLE header missing)"}

        stem, _ = fits_metadata.filename_parts(name)
        stem = stem or "image"
        hint = str(hdr.get("OBJECT") or "").strip() or stem
        ra_h = fitsio.parse_ra_hours(hdr.get("OBJCTRA") or hdr.get("RA"))
        dec_d = fitsio.parse_dec_deg(hdr.get("OBJCTDEC") or hdr.get("DEC"))
        active_resolver = resolver if resolver is not None else \
            imglib.CatalogResolver(catalog)
        row = (active_resolver.row_by_id.get(session_target["anchor"].get("object_id"))
               if session_target else await _archive_io(active_resolver.resolve, hint, ra_h, dec_d))

        if row:
            object_id = row["id"]
            object_name = str(row.get("primary_name") or hint)[:120]
            ra_h, dec_d = row["ra_hours"], row["dec_deg"]
        else:
            object_id = None
            object_name = (str(hdr.get("OBJECT") or "").strip() or stem)[:120]
        if session_target is not None:
            anchor = session_target["anchor"]
            object_id, object_name = anchor.get("object_id"), anchor["object_name"]
            ra_h, dec_d = anchor.get("ra_hours"), anchor.get("dec_deg")
            row = active_resolver.row_by_id.get(object_id)

        exposure = None
        for k in ("EXPTIME", "EXPOSURE", "EXPTIM", "ELAPTIME"):
            v = hdr.get(k)
            if isinstance(v, (int, float)) and v >= 0:
                exposure = float(v)
                break
        date_obs = fitsio.capture_date_obs(hdr)
        filt = str(hdr.get("FILTER") or "").strip() or None
        tel = str(hdr.get("TELESCOP") or hdr.get("INSTRUME") or "").strip() or None
        frame = classify_frame(hdr.get("IMAGETYP"))
        forced = (frame_override or "").strip().lower()
        if forced in FRAME_TYPES:           # explicit upload target wins
            frame = forced

        cancel = threading.Event()
        if compress:
            duplicate = await _archive_io(_known_duplicate, db, archive, digest.hexdigest(),
                stage.stat().st_size, enforce_archive_root, session_target, frame, dedup_batch, cancel,
                cancel_event=cancel)
            if duplicate is not None:
                return _duplicate_result(name, duplicate)
            try:
                prepared = await _archive_io(_prepare_compression, archive, stage, digest.hexdigest(),
                    name, compression_mode, cancel, cancel_event=cancel, on_cancel=_discard_prepared)
            except ValueError as exc:
                return {'file': name, 'ok': False, 'error': f'Lossless Rice compression failed: {exc} No exposure was published.'}
        if before_publish is not None:
            await before_publish()
        return await _archive_io(
            _finalize_import, db, archive, stage, digest, name, object_id,
            object_name, ra_h, dec_d, exposure, date_obs, filt, tel, frame,
            session_override, row, on_import, enforce_archive_root, session_target, hdr,
            compress, cancel, compression_mode, prepared, dedup_batch,
            storage_compressed=storage_info['compressed'], cancel_event=cancel)
    except OSError as exc:
        log.warning("ingest: cannot write %s: %s", archive, exc)
        return {"file": name, "ok": False,
                "error": f"could not write to the archive: {exc.strerror or exc}"}
    finally:
        try:
            if prepared is not None:
                await _archive_io(_discard_prepared, prepared)
            if stage is not None:
                await _archive_io(_remove_stage, stage)
        finally:
            if working:
                workers.release()


def _discard_prepared(prepared):
    for temporary in prepared['temporaries']:
        _remove_stage(temporary)


def _prepare_compression(archive, stage, digest, name, mode, cancel):
    """Private, cancellable CPU/IO preparation; no archive or SQLite lock."""
    from . import compression
    prepared = dict(temporaries=[], stage=stage, digest=digest, size=stage.stat().st_size,
                    mapping=None, result={})
    def checkpoint():
        if cancel.is_set():
            raise compression.Cancelled
    try:
        checkpoint()
        try:
            file_kind = 'unsupported' if name.lower().endswith('.gz') else compression.kind(stage, checkpoint)
        except fits_metadata.FitsMetadataError:
            file_kind = 'unsupported'
        if file_kind != 'integer':
            prepared['result'] = dict(compression='unchanged', compression_reason=(
                'Already Rice-compressed; stored unchanged.' if file_kind == 'rice' else
                'Unsupported or already compressed FITS; stored unchanged. Rice requires 8/16/32-bit integer images.'))
            return prepared
        packed = _prepare_stage(archive)
        prepared['temporaries'].append(packed)
        packed.unlink()
        restored = None
        if mode == 'safe':
            restored = _prepare_stage(archive)
            prepared['temporaries'].append(restored)
            restored.unlink()
        info = archive.stat()
        measured = compression.encode_verified(stage, packed, restored, archive,
            (info.st_dev, info.st_ino), (digest, prepared['size']), checkpoint,
            verify_roundtrip=mode == 'safe')
        prepared.update(stage=packed, digest=measured[0], size=measured[1],
            mapping=dict(original_sha256=digest, original_size=prepared['size'], original_name=name,
                compressed_sha256=measured[0], compressed_size=measured[1], current_sha256=measured[0]),
            result=dict(compression='rice', compression_mode=mode))
        return prepared
    except BaseException:
        _discard_prepared(prepared)
        raise


def _finalize_import(db, archive, stage, digest, name, object_id, object_name,
                     ra_h, dec_d, exposure, date_obs, filt, tel, frame,
                     session_override, row, on_import=None,
                     enforce_archive_root=False, session_target=None, header=None,
                     compress=False, cancel_event=None, compression_mode='safe', prepared=None, dedup_batch=None,
                     storage_compressed=None):
    from . import compression
    target = None
    extra_stages = []
    committed = promoted = False

    def checkpoint():
        if cancel_event is not None and cancel_event.is_set():
            raise compression.Cancelled

    def encode(verify_roundtrip=True):
        packed = _prepare_stage(archive)
        extra_stages.append(packed)
        restored = None
        if verify_roundtrip:
            restored = _prepare_stage(archive)
            extra_stages.append(restored)
        # fpack refuses existing output files, including empty reservations.
        packed.unlink()
        if restored is not None:
            restored.unlink()
        identity = (archive.stat().st_dev, archive.stat().st_ino)
        measured = compression.encode_verified(stage, packed, restored, archive,
            identity, (digest.hexdigest(), size), checkpoint, verify_roundtrip=verify_roundtrip)
        return packed, measured, dict(original_sha256=digest.hexdigest(), original_size=size,
            original_name=name, compressed_sha256=measured[0], compressed_size=measured[1], current_sha256=measured[0])

    def verify_publication(path, expected):
        with path.open('r+b') as destination:
            destination.flush()
            os.fsync(destination.fileno())
        root_info = archive.stat()
        measured = compression.fingerprint(archive, path.relative_to(archive).as_posix(),
            (root_info.st_dev, root_info.st_ino), checkpoint)
        if measured[:2] != expected[:2]:
            raise compression.RiceCompressionError('Published exposure failed its SHA-256 check.')
        return measured

    try:
        size = stage.stat().st_size
        # Upload/reindex publication is serialized independently of SQLite.
        # File hashing/copying never holds the database connection lock.
        with _batch_archive_lock(db, dedup_batch):
            if db.pending_compression():
                return {'file': name, 'ok': False, 'error': 'Compression recovery must finish before importing exposures. Restart NightSeam with its original disks available.'}
            if db.pending_trash():
                return {'file': name, 'ok': False,
                        'error': 'Interrupted trash recovery must finish before importing exposures. Use scripts/restore_legacy_trash.py to inspect retained recovery records.'}
            if enforce_archive_root and Path(db.image_dir()).resolve() != archive:
                return {"file": name, "ok": False,
                        "error": "Archive location changed during import; import this file again."}
            if session_target is not None:
                try:
                    current = calibration_import_target(
                        db, archive, session_target["anchor_id"], frame)
                except ValueError:
                    current = None
                if current != session_target:
                    return {"file": name, "ok": False,
                            "error": "Selected session changed during import; select it again."}
            checkpoint()
            duplicate = _find_duplicate(db, archive, digest.hexdigest(), size,
                                        dedup_batch=dedup_batch, cancel_event=cancel_event)
            if duplicate is not None:
                return _duplicate_result(name, duplicate)
            missing = _missing_original(db, archive, digest.hexdigest(), size)
            if missing is not None:
                existing, target = missing
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    checkpoint()
                    _promote_stage(stage, target)
                except FileExistsError:
                    measured = _fingerprint_file(target)
                    if measured is None or measured[:2] != (digest.hexdigest(), size):
                        raise
                    return _duplicate_result(name, existing)
                promoted = True
                stat = target.stat()
                db.set_image_fingerprint(existing["id"], digest.hexdigest(),
                                         size, stat.st_mtime_ns)
                committed = True
                result = _duplicate_result(name, existing)
                result.update(skipped=False, restored=True, reason="original_restored")
                if compress:
                    result.update(compression='unchanged', compression_reason='Existing exposure restored in its recorded format.')
                return result
            # Restore either known representation into the existing record's
            # recorded format, retaining folder, class and session.
            with db.lock:
                aliases = [dict(r) for r in db.conn.execute(
                    'SELECT images.*,c.original_sha256,c.original_size,c.original_name,'
                    'c.compressed_sha256,c.compressed_size,c.current_sha256 FROM images '
                    'JOIN image_compression c ON c.image_id=images.id '
                    'WHERE (c.original_sha256=? AND c.original_size=? AND c.current_sha256=c.compressed_sha256) '
                    'OR (c.compressed_sha256=? AND c.compressed_size=? AND c.current_sha256=c.original_sha256) '
                    'ORDER BY images.id', (digest.hexdigest(), size, digest.hexdigest(), size))]
            for alias in aliases:
                root_info = archive.stat()
                candidate = compression._contained(archive, alias['file'], (root_info.st_dev, root_info.st_ino))
                if candidate.exists():
                    continue
                if alias['current_sha256'] == alias['compressed_sha256']:
                    prepared, measured, mapping = encode()
                    mapping['original_name'] = alias['original_name']
                    restore_format = 'rice'
                else:
                    prepared = _prepare_stage(archive)
                    extra_stages.append(prepared)
                    prepared.unlink()
                    compression.native(compression.tools()['funpack'], ['-C'], stage, prepared, checkpoint)
                    measured = compression.fingerprint(archive, prepared.name,
                        (root_info.st_dev, root_info.st_ino), checkpoint)
                    if measured[:2] != (alias['original_sha256'], alias['original_size']):
                        raise compression.RiceCompressionError('The original SHA-256 could not be restored.')
                    mapping = {key: alias[key] for key in ('original_sha256', 'original_size', 'original_name',
                        'compressed_sha256', 'compressed_size', 'current_sha256')}
                    restore_format = 'unchanged'
                target = candidate
                target.parent.mkdir(parents=True, exist_ok=True)
                checkpoint()
                _promote_stage(prepared, target)
                promoted = True
                published = verify_publication(target, measured)
                checkpoint()
                db.restore_compressed_image(alias['id'], published[0], published[1], published[2], mapping)
                committed = True
                result = _duplicate_result(name, alias)
                result.update(skipped=False, restored=True, reason='original_restored', compression=restore_format, compression_mode='safe')
                if restore_format == 'unchanged':
                    result['compression_reason'] = 'Existing exposure restored in its recorded uncompressed format.'
                return result
            publish_stage, stored_digest, stored_size, mapping = stage, digest.hexdigest(), size, None
            compression_result = {}
            if compress:
                checkpoint()
                if prepared is None:
                    raise ValueError('Missing private compression preparation.')
                publish_stage, stored_digest, stored_size = prepared['stage'], prepared['digest'], prepared['size']
                mapping, compression_result = prepared['mapping'], prepared['result']
            metadata = _header_session(db, header or {}, date_obs, session_target)
            slug = session_target["slug"] if session_target else obj_slug(object_name)
            session = (archive / slug / session_target["session"] if session_target else
                       _session_dir(db, archive, slug, object_id,
                                    object_name, date_obs, session_override, metadata))
            storage_name = name + '.fz' if mapping else name
            target = _archive_target(archive, storage_name, slug, session, frame)
            for attempt in range(8):
                if session_target and paths.safe_join(
                        archive, str(target.relative_to(archive))) is None:
                    return {"file": name, "ok": False,
                            "error": "Selected session has an unsafe calibration target."}
                try:
                    checkpoint()
                    _promote_stage(publish_stage, target)
                    break
                except FileExistsError:
                    if attempt == 7:
                        raise
                    target = _archive_target(archive, storage_name, slug, session, frame)
            promoted = True
            stored = paths.safe_rel(str(target.relative_to(archive)))
            if stored is None:
                raise ValueError("invalid archive target")
            stat = target.stat()
            try:
                checkpoint()
                if mapping:
                    verify_publication(target, (stored_digest, stored_size))
                    # Restore to the sanitized, collision-resolved archive name.
                    mapping['original_name'] = target.name[:-3]
                with db.import_unit():
                    img_id = db.add_image({
                        **metadata,
                        "file": stored, "orig_name": name, "object_id": object_id,
                        "object_name": object_name, "ra_hours": ra_h, "dec_deg": dec_d,
                        "exposure": exposure, "date_obs": date_obs, "filter": filt,
                        "session_date_obs": session_target["session_date_obs"] if session_target else None,
                        "telescope": tel, "imagetyp": frame,
                        "content_sha256": stored_digest, "file_size": stored_size,
                        "compression": mapping,
                        "storage_compressed": True if mapping else storage_compressed,
                        "integrity_source": ('verified_rice_ingest' if compression_mode == 'safe' else 'rice_ingest_fast') if mapping else 'import',
                        "file_mtime_ns": stat.st_mtime_ns,
                        "ingested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    result = {
                        **compression_result,
                        **metadata,
                        "file": name, "ok": True, "id": img_id,
                        "object": {"id": object_id, "name": object_name,
                                   "designation": imglib.designation_of(
                                       row, {"object_name": object_name})},
                        "exposure": exposure, "date_obs": date_obs, "filter": filt,
                        "session_date_obs": session_target["session_date_obs"] if session_target else None,
                        "telescope": tel, "imagetyp": frame, "stored": stored,
                    }
                    if on_import is not None:
                        on_import(result, archive)
                committed = True
                if not mapping:
                    # The stage is a hard link to the published file; dropping
                    # it changes the file's journal version, so it goes first.
                    _remove_stage(publish_stage)
                    _remember_published(db, target, stored_digest, stored_size)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
    except compression.RiceCompressionError as exc:
        return {'file': name, 'ok': False, 'error': f'Lossless Rice compression failed: {exc} No exposure was published.'}
    finally:
        if target is not None and promoted and not committed:
            target.unlink(missing_ok=True)
        for temporary in extra_stages:
            _remove_stage(temporary)
    return result


async def _stream_to(archive: Path, target: Path, f, first_chunk: bytes,
                     digest=None) -> str | None:
    """Write first_chunk + the rest of `f` to target, enforcing the size cap.
    Returns the stored path relative to the archive root, or None if the file
    exceeds MAX_INGEST_BYTES. An OSError (disk full, permissions, locked file)
    propagates, so the caller can report the real cause instead of blaming the
    size cap."""
    total = len(first_chunk)
    if total > MAX_INGEST_BYTES:
        return None
    source = getattr(f, "file", None)
    if source is not None:
        # Starlette has already spooled multipart data. Keep the sequential
        # read/write/hash loop in ONE cancellable worker rather than crossing
        # the event-loop/thread boundary twice for every megabyte. The worker
        # completes before stage cleanup on disconnect, as with every other
        # archive operation. Async-only upload adapters retain the fallback.
        return await _archive_io(_copy_spooled_upload, archive, target, source,
                                 first_chunk, digest)
    def write_chunk(out, chunk):
        out.write(chunk)
        if digest is not None:
            digest.update(chunk)

    # Streamed uploads hand over whole received chunks without joining copies.
    read = getattr(f, "read1", None) or f.read
    out = await _archive_io(target.open, "wb", on_cancel=lambda stream: stream.close())
    try:
        await _archive_io(write_chunk, out, first_chunk)
        while True:
            chunk = await read(_COPY_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_INGEST_BYTES:
                return None
            await _archive_io(write_chunk, out, chunk)
    finally:
        await _archive_io(out.close)
    return str(target.relative_to(archive)).replace("\\", "/")


def _copy_spooled_upload(archive, target, source, first_chunk, digest):
    total = 0
    with target.open("wb") as out:
        chunk = first_chunk
        while chunk:
            total += len(chunk)
            if total > MAX_INGEST_BYTES:
                return None
            out.write(chunk)
            if digest is not None:
                digest.update(chunk)
            chunk = source.read(_COPY_CHUNK)
    return str(target.relative_to(archive)).replace("\\", "/")


class ArchiveRenameRecoveryError(OSError):
    """A failed rename left files that could not be returned to their origins."""

    def __init__(self, failures: list[tuple[str, str]]):
        super().__init__("archive rename recovery is incomplete")
        self.failures = failures


class ArchiveRenameMap(dict[str, str]):
    """File mapping with an independent undo record for an entire directory."""

    def __init__(self, *, whole_directory: tuple[str, str] | None = None):
        super().__init__()
        self.whole_directory = whole_directory


def rename_archive_folder(archive: Path, old_name: str,
                          new_name: str) -> dict[str, str] | None:
    """Move <archive>/<old slug> to <archive>/<new slug>, merging if the new
    folder already exists. Returns the map of moved frames ({old relative path:
    new relative path}, forward slashes) - `{}` when there was nothing to move -
    or None when the move was refused or failed with complete undo. The caller
    must NOT rewrite stored paths on None. An incomplete undo raises
    ArchiveRenameRecoveryError carrying the unrestored relative paths; the
    caller must report that recovery is incomplete and preserve the bytes.

    A frame whose name already exists in the destination is UNIQUIFIED
    (`Light_001.fit` -> `Light_001_2.fit`), exactly like `_archive_target` does
    for a new ingest. It used to be `replace`d in place, which silently
    destroyed those pixels while the DB kept two rows aliasing one file
    (PY-01, blocker). The returned map is what the caller feeds to
    `db.rename_images`, so the stored paths name the files that really exist."""
    archive = archive.resolve()
    old = paths.safe_join(archive, obj_slug(old_name))
    new = paths.safe_join(archive, obj_slug(new_name))
    if old is None or new is None:
        return None
    if old == new or not old.is_dir():
        return {}
    moved = ArchiveRenameMap()

    def _rel(p: Path) -> str:
        return str(p.relative_to(archive)).replace("\\", "/")

    # F-22: every completed move is journalled so a mid-merge failure can be
    # UNDONE - without this, files already replaced into the destination stay
    # there while the database keeps the old paths ("missing originals").
    try:
        if new.exists():
            for f in sorted(old.rglob("*")):
                if not f.is_file():
                    continue
                dest = _unique_path(new / f.relative_to(old))
                source_rel, dest_rel = _rel(f), _rel(dest)
                # Validate resolved confinement, but move the lexical entries
                # so a symlink's target is never substituted for the entry.
                if paths.safe_join(archive, source_rel) is None or \
                        paths.safe_join(archive, dest_rel) is None:
                    raise OSError("archive rename path escapes the archive root")
                dest.parent.mkdir(parents=True, exist_ok=True)
                _move_one(f, dest)
                moved[source_rel] = dest_rel
            for d in sorted([p for p in old.rglob("*") if p.is_dir()],
                            key=lambda p: len(p.parts), reverse=True):
                d.rmdir()
            old.rmdir()
        else:
            # Discover every file BEFORE moving the directory. An enumeration
            # error must leave the original tree intact, and a failed rename
            # must not attempt to undo a merely planned map.
            planned = ArchiveRenameMap(whole_directory=(_rel(old), _rel(new)))
            for f in sorted(old.rglob("*")):
                if f.is_file():
                    planned[_rel(f)] = _rel(new / f.relative_to(old))
            old.rename(new)
            moved = planned
    except OSError as exc:
        failures = undo_archive_rename(archive, moved)
        for target, message in failures:
            log.error("rename undo failed for %s: %s - inspect and preserve "
                      "the affected files, resolve the filesystem problem, "
                      "then run Reindex folders", target, message)
        log.warning("rename: could not move %s -> %s: %s (undone: %d/%d)",
                    old, new, exc, len(moved) - len(failures), len(moved))
        if failures:
            raise ArchiveRenameRecoveryError(failures) from exc
        return None
    return moved


def _move_one(src: Path, dst: Path) -> None:
    """One journaled rename move (module-level so tests can inject faults)."""
    src.replace(dst)


def undo_archive_rename(archive: Path,
                        moved: dict[str, str]) -> list[tuple[str, str]]:
    """Reverse a completed folder move, newest first (F-22).

    Best-effort by design: every restorable file is put back, and anything that
    cannot be restored is REPORTED (relative paths) instead of swallowed, so a
    failed undo is visible. A whole-directory move is reversed independently
    of its file map, preserving empty directories and unrecorded children.
    Never raises."""
    whole_directory = getattr(moved, "whole_directory", None)
    if whole_directory is not None:
        old_rel, new_rel = whole_directory
        try:
            src = paths.safe_join(archive, old_rel)
            dst = paths.safe_join(archive, new_rel)
            if src is None or dst is None:
                return [(new_rel, "path escaped the archive root")]
            if src.exists() or src.is_symlink():
                return [(new_rel, "original directory is occupied; refusing to overwrite it")]
            dst.rename(src)
        except OSError as exc:
            return [(new_rel, str(exc))]
        return []

    failures: list[tuple[str, str]] = []
    for old_rel, new_rel in reversed(list(moved.items())):
        try:
            dst = paths.safe_join(archive, new_rel)
            src = paths.safe_join(archive, old_rel)
            if dst is None or src is None:
                failures.append((new_rel, "path escaped the archive root"))
                continue
            src.parent.mkdir(parents=True, exist_ok=True)
            dst.replace(src)
        except OSError as exc:
            failures.append((new_rel, str(exc)))
    return failures


def _norm_rel(rel) -> str:
    """Normalized relative path for dedup - separator- and case-insensitive."""
    return str(rel).replace("\\", "/").lower()


class ReindexCancelled(Exception):
    """A cooperative archive scan ended before database publication."""


def _check_reindex_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ReindexCancelled("Reindex cancelled. No library records were changed.")


def reindex_library(db: Database, archive: Path, cancel_event=None) -> dict:
    # Serialize archive operations while scanning; SQLite reads remain available.
    with db.archive_lock:
        return _reindex_library_locked(db, archive, cancel_event)


def _reindex_library_locked(db: Database, archive: Path, cancel_event=None) -> dict:
    """Plan one atomic archive scan, preserving metadata when repairing paths.

    A discovered file can repair exactly one missing record only when its full
    byte digest and size match that record's previously stored fingerprint and
    exactly one destination exists. Ambiguous matches are reported, never
    guessed. Missing rows and every physical archive file remain untouched.
    Hashing, parsing and filesystem validation precede the SQLite transaction.
    """
    if db.pending_compression():
        raise ValueError('Compression recovery must finish before reindexing. Restart NightSeam with its original disks available.')
    if db.pending_trash():
        raise ValueError('Interrupted trash recovery must finish before reindex. Use scripts/restore_legacy_trash.py to inspect retained recovery records.')
    result = {"found": 0, "added": 0, "skipped": 0, "failed": 0,
              "repaired": 0, "conflicts": 0}
    _check_reindex_cancelled(cancel_event)
    if not archive.is_dir():
        return result
    archive = archive.resolve()
    expected = db.images()
    known = {}
    live = {}
    missing = {}
    for rec in expected:
        _check_reindex_cancelled(cancel_event)
        known.setdefault(_norm_rel(rec["file"]), []).append(rec)
        target = paths.safe_join(archive, rec["file"])
        if target is None:
            continue
        try:
            stat = target.stat()
        except FileNotFoundError:
            digest = rec.get("content_sha256")
            size = rec.get("file_size")
            if (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                    and isinstance(size, int) and not isinstance(size, bool) and size >= 0):
                missing.setdefault((digest, size), []).append(rec)
            continue
        except OSError:
            continue
        if target.is_file():
            live[rec["id"]] = (rec, target, stat)

    candidates = []
    from .trash_paths import archive_paths
    for entry in archive_paths(archive):
        _check_reindex_cancelled(cancel_event)
        if not fits_metadata.is_fits_name(entry.name):
            continue
        rel = paths.safe_rel(entry.relative_to(archive).as_posix())
        target = paths.safe_join(archive, rel)
        if target is None:
            raise ValueError("Unsafe archive file path. No library records were changed.")
        if target.is_file():
            parts = rel.split("/")
            if len(parts) not in (3, 4) or parts[-2].lower() not in FRAME_DIRS:
                raise ValueError(
                    f"Unexpected archive folder structure at '{entry.parent}'. "
                    "Expected <object>/<session>/<frame-type>/<file.fit> or legacy "
                    "<object>/<frame-type>/<file.fit>, where frame-type is lights, "
                    "darks, flats or bias. Use Import Exposures for flat source folders. "
                    "No library records were changed.")
            candidates.append((rel, target))
    candidates.sort()
    result["found"] = len(candidates)
    fingerprints = {}
    measured_paths = {}
    header_states = {}
    storage_formats = {}

    def read_header(target):
        _check_reindex_cancelled(cancel_event)
        before = target.stat()
        storage_info = {}
        header = fits_metadata.read_metadata(
            target, lambda: _check_reindex_cancelled(cancel_event), storage_info=storage_info)
        storage_formats[target] = storage_info.get('compressed')
        after = target.stat()
        state = (before.st_size, before.st_mtime_ns)
        if state != (after.st_size, after.st_mtime_ns):
            raise ValueError("Archive changed during reindex. No library records were changed.")
        if header:
            header_states[target] = state
        return header

    def measure(target):
        _check_reindex_cancelled(cancel_event)
        if target not in measured_paths:
            measured_paths[target] = (_fingerprint_file(target) if cancel_event is None
                                      else _fingerprint_file(target, cancel_event))
        _check_reindex_cancelled(cancel_event)
        return measured_paths[target]

    def fingerprint_live(rec, target):
        measured = measure(target)
        if measured is not None:
            fingerprints[rec["id"]] = (rec["id"], *measured)
            actual_candidates = live_by_identity.setdefault(measured[:2], [])
            if not any(entry[0]["id"] == rec["id"] for entry in actual_candidates):
                actual_candidates.append(live[rec["id"]])
            size = live[rec["id"]][2].st_size
            uncertain_by_size[size] = [entry for entry in uncertain_by_size.get(size, [])
                                       if entry[0]["id"] != rec["id"]]
        return measured

    new = {}
    session_updates = {}
    for rel, target in candidates:
        _check_reindex_cancelled(cancel_event)
        recorded = known.get(_norm_rel(rel))
        if recorded:
            try:
                header = read_header(target)
            except (OSError, fits_metadata.FitsMetadataError):
                header = None
            if header:
                for rec in recorded:
                    session_updates[rec["id"]] = _reindex_session_metadata(db, rec, header, storage_formats[target])
            stat = target.stat()
            needs_hash = any(not rec.get("content_sha256") or
                             rec.get("file_size") != stat.st_size or
                             rec.get("file_mtime_ns") != stat.st_mtime_ns
                             for rec in recorded)
            if needs_hash:
                measured = measure(target)
                if measured is None:
                    result["failed"] += 1
                    continue
                for rec in recorded:
                    fingerprints[rec["id"]] = (rec["id"], *measured)
            result["skipped"] += 1
            continue
        try:
            header = read_header(target)
        except (OSError, fits_metadata.FitsMetadataError):
            header = None
        if not header:
            result["failed"] += 1
            continue
        measured = measure(target)
        if measured is None:
            result["failed"] += 1
            continue
        digest, size, _ = measured
        new.setdefault((digest, size), []).append((rel, target, header, measured))

    # Index the snapshot once. A cached digest can be stale even when a restore
    # preserves size and mtime. For new files, every unmeasured live original of
    # the same size needs current-byte proof; after one read it moves into the
    # measured identity index. Scans containing only known paths stay cached.
    live_by_identity, uncertain_by_size = {}, {}
    for rec, target, stat in live.values():
        cached = fingerprints.get(rec["id"])
        entry = (rec, target, stat)
        if cached:
            live_by_identity.setdefault(cached[1:3], []).append(entry)
        else:
            uncertain_by_size.setdefault(stat.st_size, []).append(entry)

    additions, relinks = [], []
    resolver = None
    for identity, destinations in new.items():
        _check_reindex_cancelled(cancel_event)
        digest, size = identity
        matching_live = []
        possible = (live_by_identity.get(identity, []) +
                    uncertain_by_size.get(size, []))
        for rec, target, stat in possible:
            cached = fingerprints.get(rec["id"])
            if cached:
                if cached[1:3] == identity:
                    matching_live.append(rec)
                continue
            if stat.st_size != size:
                continue
            actual = fingerprint_live(rec, target)
            if actual is not None and actual[:2] == identity:
                matching_live.append(rec)
        stale = missing.get(identity, [])
        if stale:
            if len(stale) != 1 or len(destinations) != 1 or matching_live:
                result["conflicts"] += len(destinations)
                continue
            rel, _, _, measured = destinations[0]
            relinks.append((stale[0]["id"], rel, *measured))
            session_updates[stale[0]["id"]] = _reindex_session_metadata(
                db, stale[0], destinations[0][2], storage_formats[destinations[0][1]])
            result["repaired"] += 1
            continue
        if matching_live:
            result["skipped"] += len(destinations)
            continue
        rel, target, hdr, measured = destinations[0]
        parts = rel.split("/")
        folder_name = parts[0]
        hint = folder_name.strip() or str(hdr.get("OBJECT") or "").strip() or target.stem
        ra_h = fitsio.parse_ra_hours(hdr.get("OBJCTRA") or hdr.get("RA"))
        dec_d = fitsio.parse_dec_deg(hdr.get("OBJCTDEC") or hdr.get("DEC"))
        if resolver is None:
            resolver = imglib.CatalogResolver(db.all_objects(advanced=True))
        row = resolver.resolve(hint, ra_h, dec_d)
        if row is None and str(hdr.get("OBJECT") or "").strip() not in ("", hint):
            row = resolver.resolve(str(hdr.get("OBJECT") or "").strip(), ra_h, dec_d)
        if row:
            object_id = row["id"]
            object_name = str(row.get("primary_name") or hint)[:120]
            ra_h, dec_d = row["ra_hours"], row["dec_deg"]
        else:
            object_id = None
            object_name = (folder_name or str(hdr.get("OBJECT") or "").strip()
                           or target.stem)[:120]
        exposure = None
        for key in ("EXPTIME", "EXPOSURE", "EXPTIM", "ELAPTIME"):
            value = hdr.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                exposure = float(value)
                break
        captured = fitsio.capture_date_obs(hdr)
        additions.append({
            **_header_session(db, hdr, captured),
            "file": rel, "orig_name": target.name, "object_id": object_id,
            "object_name": object_name, "ra_hours": ra_h, "dec_deg": dec_d,
            "exposure": exposure,
            "date_obs": captured,
            "filter": str(hdr.get("FILTER") or "").strip() or None,
            "telescope": str(hdr.get("TELESCOP") or hdr.get("INSTRUME") or "").strip() or None,
            "imagetyp": FRAME_DIRS[parts[-2].lower()],
            "content_sha256": measured[0], "file_size": measured[1],
            "file_mtime_ns": measured[2],
            "storage_compressed": storage_formats[target],
            "integrity_source": "reindex",
            "ingested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        })
        result["added"] += 1
        result["skipped"] += len(destinations) - 1

    # Files can also change outside this process's archive lock. Refuse a plan
    # whose measured input has disappeared, moved outside the archive or changed.
    for target, state in header_states.items():
        _check_reindex_cancelled(cancel_event)
        current = paths.safe_join(archive, target.relative_to(archive).as_posix())
        stat = target.stat()
        if current != target or (stat.st_size, stat.st_mtime_ns) != state:
            raise ValueError("Archive changed during reindex. No library records were changed.")
    for target, measured in measured_paths.items():
        _check_reindex_cancelled(cancel_event)
        if measured is None:
            continue
        rel = target.relative_to(archive).as_posix()
        current = paths.safe_join(archive, rel)
        if current != target:
            raise ValueError("Archive changed during reindex. No library records were changed.")
        stat = target.stat()
        if (stat.st_size, stat.st_mtime_ns) != measured[1:]:
            raise ValueError("Archive changed during reindex. No library records were changed.")
    # Refresh explicit attachments from their ordinary target's metadata, not
    # from the calibration's own location. Prefer the exact logical anchor;
    # otherwise require one unique refreshed interval in the prior membership.
    # This also keeps an unassigned attachment with its night when the original
    # observing location is supplied later through header/default backfill.
    for rec in expected:
        metadata = session_updates.get(rec["id"])
        if metadata is None or rec.get("session_date_obs") is None:
            continue
        anchor_date = normalize_session_date_obs(rec["session_date_obs"])
        anchors, prior_members = [], []
        for anchor in expected:
            if anchor.get("session_date_obs") is not None:
                continue
            if rec.get("session_key"):
                if imglib.image_session_metadata(anchor)["session_key"] != rec["session_key"]:
                    continue
                prior_members.append(session_updates.get(anchor["id"]) or
                                     imglib.image_session_metadata(anchor))
            if (anchor.get("object_id") != rec.get("object_id") or
                    (rec.get("object_id") is None and imglib.name_key(anchor["object_name"]) !=
                     imglib.name_key(rec["object_name"]))):
                continue
            try:
                candidate_date = normalize_session_date_obs(anchor.get("date_obs") or "")
            except ValueError:
                # Old capture parsers accepted impossible ISO dates. A broken
                # unrelated record cannot invalidate a valid explicit target.
                continue
            if candidate_date == anchor_date:
                anchors.append(session_updates.get(anchor["id"]) or
                               imglib.image_session_metadata(anchor))
        anchors = anchors or prior_members
        if len({anchor["session_key"] for anchor in anchors}) == 1:
            metadata.update({key: anchors[0][key] for key in imglib.SESSION_FIELDS})
    planned_sessions = [{"id": rec["id"], **session_updates[rec["id"]]}
                        for rec in expected if rec["id"] in session_updates and
                        any(rec.get(key) != value for key, value in session_updates[rec["id"]].items())]
    _check_reindex_cancelled(cancel_event)
    db.apply_reindex_plan(expected, additions, list(fingerprints.values()), relinks,
                          cancel_check=lambda: _check_reindex_cancelled(cancel_event),
                          session_updates=planned_sessions)
    return result
