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
from datetime import UTC, datetime
from pathlib import Path

from . import fits as fitsio
from . import imglib
from . import paths
from .database import Database, normalize_session_date_obs

log = logging.getLogger(__name__)

MAX_INGEST_BYTES = 1 << 30          # 1 GiB per file
HEADER_READ = 1_500_000             # fits._MAX_BLOCKS blocks ~= 1.47 MB
_COPY_CHUNK = 1 << 20               # 1 MB streaming buffer
_STAGE_LOCK = threading.RLock()
_ACTIVE_STAGES: set[Path] = set()
_STAGE_NAME = re.compile(r"^\.nightseam-import-(\d+)-[A-Za-z0-9_]+\.part$")
_STALE_STAGE_SECONDS = 3600


async def _archive_io(function, *args, on_cancel=None):
    """Finish in-flight filesystem work before cancellation can clean it up."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
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


def _find_duplicate(db: Database, archive: Path, digest: str, size: int,
                    exclude_id: int | None = None) -> dict | None:
    """Find matching local bytes, including originals from older versions.

    Caller holds db.archive_lock for lookup/finalization. Database methods take
    their own short-lived connection lock; hashing never holds it. Size and
    mtime are hints only: a matching cached digest is always checked against
    the actual archived file before an upload is skipped. Missing originals
    therefore cannot prevent users importing a replacement.
    """
    for rec in db.image_fingerprint_candidates(digest, size):
        if rec["id"] == exclude_id:
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
        measured = _fingerprint_file(original)
        if measured is None:
            continue
        actual, actual_size, mtime_ns = measured
        if (cached, rec.get("file_size"), rec.get("file_mtime_ns")) != measured:
            db.set_image_fingerprint(rec["id"], actual, actual_size, mtime_ns)
        if actual_size == size and actual == digest:
            return rec
    return None


def _duplicate_result(name: str, rec: dict) -> dict:
    return {"file": name, "ok": True, "skipped": True,
            "reason": "already_imported", "id": rec["id"],
            "stored": rec["file"],
            "object": {"id": rec.get("object_id"),
                       "name": rec.get("object_name"),
                       "designation": imglib.designation_of(None, rec)},
            **{key: rec.get(key) for key in
               ("exposure", "date_obs", "session_date_obs", "filter", "telescope", "imagetyp")}}


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
    return re.sub(r"[^A-Za-z0-9 _-]+", "", object_name).strip() or "misc"


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


def session_folder_name(epoch: float | None) -> str:
    """On-disk session folder: 'YYYY-MM-DD_HHMM' in the machine-local zone,
    or 'undated' for frames without a parseable DATE-OBS."""
    if epoch is None:
        return "undated"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d_%H%M")


def _session_dir(db: Database, archive: Path, slug: str, object_id,
                 object_name: str, date_obs: str | None,
                 session_override: str | None = None) -> Path:
    """<archive>/<slug>/<session>/ - the session the frame joins (mirrors
    imglib.compute_sessions), so different nights get different folders.
    An explicit `session` upload target (calibration buttons) wins."""
    if session_override:
        return archive / slug / session_override
    existing = db.images_for_object(object_id, object_name)
    joined = imglib.session_for_image(existing, {"date_obs": date_obs})
    start = imglib.date_epoch(joined.get("start")) if joined else None
    return archive / slug / session_folder_name(start)


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
        epoch = imglib.date_epoch(stamp)
        session_date = normalize_session_date_obs(stamp) if epoch is not None else ""
        # Keep a modern session's existing folder. Legacy Object/lights anchors
        # gain the canonical Object/session/frame layout without moving files.
        session = parts[1] if len(parts) == 4 else session_folder_name(epoch)
        if paths.safe_join(archive, f"{parts[0]}/{session}/{FRAME_TYPES[frame]}") is None:
            raise ValueError("Selected session has an unsafe calibration folder.")
        return {"anchor_id": anchor_id, "anchor": imglib.public_image_record(anchor),
                "slug": parts[0], "session": session,
                "session_date_obs": session_date}


def _unique_path(path: Path) -> Path:
    """`path`, or its first free `stem_2`/`stem_3`/... variant.

    The ONE uniquify rule for the archive: `_archive_target` uses it for a new
    ingest and `rename_archive_folder` uses it when a rename merges into a
    folder that already holds a same-named frame (PY-01) - an existing file is
    never replaced."""
    if not path.exists():
        return path
    suffix = path.suffix
    stem = path.stem
    n = 2
    while True:
        candidate = path.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _archive_target(archive: Path, name: str, slug: str,
                    session: Path, frame: str) -> Path:
    """Unique destination <archive>/<slug>/<session>/<frames>/<stem>..."""
    suffix = Path(name).suffix or ".fit"
    stem = (Path(name).stem or "image")[:80]
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
                     session_target: dict | None = None) -> dict:
    """Stream one uploaded .fit/.fits into the archive and record it.

    `f` is a fastapi UploadFile. Only the header block is buffered; the
    remainder is streamed to disk. Returns a per-file result dict.
    """
    name = Path(f.filename or "image.fit").name
    if not name.lower().endswith((".fit", ".fits")):
        return {"file": name, "ok": False, "error": "not a .fit/.fits file"}
    if getattr(f, "size", None) and f.size > MAX_INGEST_BYTES:
        return {"file": name, "ok": False,
                "error": f"file too large (max {MAX_INGEST_BYTES // (1 << 20)} MB)"}

    header = await f.read(HEADER_READ)
    try:
        hdr = fitsio.read_fits_header(header)
    except Exception as exc:  # noqa: BLE001
        return {"file": name, "ok": False,
                "error": f"header parse failed: {exc}"}
    if not hdr:
        return {"file": name, "ok": False,
                "error": "Unsupported exposure file (SIMPLE header missing)"}

    stem = Path(name).stem
    hint = str(hdr.get("OBJECT") or "").strip() or stem
    ra_h = fitsio.parse_ra_hours(hdr.get("OBJCTRA") or hdr.get("RA"))
    dec_d = fitsio.parse_dec_deg(hdr.get("OBJCTDEC") or hdr.get("DEC"))
    active_resolver = resolver if resolver is not None else \
        imglib.CatalogResolver(catalog)
    row = (active_resolver.row_by_id.get(session_target["anchor"].get("object_id"))
           if session_target else active_resolver.resolve(hint, ra_h, dec_d))

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
    date_obs = fitsio.parse_date_obs(hdr.get("DATE-OBS") or hdr.get("DATE"))
    filt = str(hdr.get("FILTER") or "").strip() or None
    tel = str(hdr.get("TELESCOP") or hdr.get("INSTRUME") or "").strip() or None
    frame = classify_frame(hdr.get("IMAGETYP"))
    forced = (frame_override or "").strip().lower()
    if forced in FRAME_TYPES:           # explicit upload target wins
        frame = forced

    stage = None
    digest = hashlib.sha256()
    try:
        archive = await _archive_io(archive.resolve)
        stage = await _archive_io(_prepare_stage, archive, on_cancel=_remove_stage)
        streamed = await _stream_to(archive, stage, f, header, digest)
        if streamed is None:
            return {"file": name, "ok": False,
                    "error": f"file too large (max {MAX_INGEST_BYTES // (1 << 20)} MB)"}
        return await _archive_io(
            _finalize_import, db, archive, stage, digest, name, object_id,
            object_name, ra_h, dec_d, exposure, date_obs, filt, tel, frame,
            session_override, row, on_import, enforce_archive_root, session_target)
    except OSError as exc:
        log.warning("ingest: cannot write %s: %s", archive, exc)
        return {"file": name, "ok": False,
                "error": f"could not write to the archive: {exc.strerror or exc}"}
    finally:
        if stage is not None:
            await _archive_io(_remove_stage, stage)


def _finalize_import(db, archive, stage, digest, name, object_id, object_name,
                     ra_h, dec_d, exposure, date_obs, filt, tel, frame,
                     session_override, row, on_import=None,
                     enforce_archive_root=False, session_target=None):
    target = None
    committed = promoted = False
    try:
        size = stage.stat().st_size
        # Upload/reindex publication is serialized independently of SQLite.
        # File hashing/copying never holds the database connection lock.
        with db.archive_lock:
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
            duplicate = _find_duplicate(db, archive, digest.hexdigest(), size)
            if duplicate is not None:
                return _duplicate_result(name, duplicate)
            missing = _missing_original(db, archive, digest.hexdigest(), size)
            if missing is not None:
                existing, target = missing
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
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
                return result
            slug = session_target["slug"] if session_target else obj_slug(object_name)
            session = (archive / slug / session_target["session"] if session_target else
                       _session_dir(db, archive, slug, object_id,
                                    object_name, date_obs, session_override))
            target = _archive_target(archive, name, slug, session, frame)
            for attempt in range(8):
                if session_target and paths.safe_join(
                        archive, str(target.relative_to(archive))) is None:
                    return {"file": name, "ok": False,
                            "error": "Selected session has an unsafe calibration target."}
                try:
                    _promote_stage(stage, target)
                    break
                except FileExistsError:
                    if attempt == 7:
                        raise
                    target = _archive_target(archive, name, slug, session, frame)
            promoted = True
            stored = paths.safe_rel(str(target.relative_to(archive)))
            if stored is None:
                raise ValueError("invalid archive target")
            stat = target.stat()
            try:
                img_id = db.add_image({
                    "file": stored, "orig_name": name, "object_id": object_id,
                    "object_name": object_name, "ra_hours": ra_h, "dec_deg": dec_d,
                    "exposure": exposure, "date_obs": date_obs, "filter": filt,
                    "session_date_obs": session_target["session_date_obs"] if session_target else None,
                    "telescope": tel, "imagetyp": frame,
                    "content_sha256": digest.hexdigest(), "file_size": size,
                    "file_mtime_ns": stat.st_mtime_ns,
                    "ingested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                })
                committed = True
                result = {
                    "file": name, "ok": True, "id": img_id,
                    "object": {"id": object_id, "name": object_name,
                               "designation": imglib.designation_of(
                                   row, {"object_name": object_name})},
                    "exposure": exposure, "date_obs": date_obs, "filter": filt,
                    "session_date_obs": session_target["session_date_obs"] if session_target else None,
                    "telescope": tel, "imagetyp": frame, "stored": stored,
                }
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            if on_import is not None:
                on_import(result, archive)
    finally:
        if target is not None and promoted and not committed:
            target.unlink(missing_ok=True)
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

    out = await _archive_io(target.open, "wb", on_cancel=lambda stream: stream.close())
    try:
        await _archive_io(write_chunk, out, first_chunk)
        while True:
            chunk = await f.read(_COPY_CHUNK)
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
    for entry in archive.rglob("*"):
        _check_reindex_cancelled(cancel_event)
        if entry.suffix.lower() not in (".fit", ".fits"):
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
    for rel, target in candidates:
        _check_reindex_cancelled(cancel_event)
        recorded = known.get(_norm_rel(rel))
        if recorded:
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
            with target.open("rb") as source:
                header = fitsio.read_fits_header(source.read(HEADER_READ))
        except OSError:
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
        additions.append({
            "file": rel, "orig_name": target.name, "object_id": object_id,
            "object_name": object_name, "ra_hours": ra_h, "dec_deg": dec_d,
            "exposure": exposure,
            "date_obs": fitsio.parse_date_obs(hdr.get("DATE-OBS") or hdr.get("DATE")),
            "filter": str(hdr.get("FILTER") or "").strip() or None,
            "telescope": str(hdr.get("TELESCOP") or hdr.get("INSTRUME") or "").strip() or None,
            "imagetyp": FRAME_DIRS[parts[-2].lower()],
            "content_sha256": measured[0], "file_size": measured[1],
            "file_mtime_ns": measured[2],
            "ingested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        })
        result["added"] += 1
        result["skipped"] += len(destinations) - 1

    # Files can also change outside this process's archive lock. Refuse a plan
    # whose measured input has disappeared, moved outside the archive or changed.
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
    _check_reindex_cancelled(cancel_event)
    db.apply_reindex_plan(expected, additions, list(fingerprints.values()), relinks,
                          cancel_check=lambda: _check_reindex_cancelled(cancel_event))
    return result
