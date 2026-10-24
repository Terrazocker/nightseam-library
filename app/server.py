"""FastAPI application: REST API for the image archive (library, import, sky map)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .archive_health import ArchiveHealthManager
from .compression import CompressionManager, recover as recover_compression
from .database import Database
from .folder_opener import folder_dialog_available, open_folder, pick_folder_dialog
from .archive_trash import recover_pending, pending_current
from .imglib import CatalogResolver, name_key
from .ingest import (ArchiveRenameRecoveryError, ReindexCancelled, DedupBatch, _archive_io,
                     calibration_import_target, ingest_one, obj_slug,
                     reindex_library, rename_archive_folder, undo_archive_rename)
from .paths import safe_join, safe_rel, static_dir
from .upload_stream import MalformedUpload, UploadInterrupted, multipart_parts
from .version import app_version
from . import diagnostics, local_import

APP_VERSION = app_version()
log = logging.getLogger(__name__)


# per-request upload caps, checked against Content-Length before the body is
# spooled. The UI batches by both bytes and count; larger file-count batches
# amortize archive verification without increasing the previous body/worker
# budgets. Individual exposures remain capped at 1 GiB in app/ingest.py.
MAX_FILES_PER_REQUEST = 96
# Files a folder import keeps staging ahead of publication.
FOLDER_IMPORT_WINDOW = 48
_ARCHIVE_WRITES = ('/api/images/ingest', '/api/images/folder/jobs',
                   '/api/library/reindex', '/api/library/edit')
_MAX_REQUEST_BYTES = {
    "/api/images/ingest": 24 * (1 << 30),
}


_state = {"last_request": time.time()}

# The local catalog loads no remote resources. Inline styles support the
# Workbench layout; scripts must come from this origin.
CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "worker-src 'self'; "
    "font-src 'self'"
)




def _same_origin_request(request) -> bool:
    """False for browser requests that provably come from another origin.

    A local port is not protected by CORS for "simple" requests, so without
    this check any website the user visits could call /api/library/edit or
    another state-changing endpoint. `Sec-Fetch-Site` is sent by current
    browsers and `Origin` accompanies cross-origin requests; non-browser
    clients (PowerShell, curl, TestClient) send neither and stay allowed."""
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site and site not in ("same-origin", "none"):
        return False
    origin = (request.headers.get("origin") or "").strip()
    if origin == "null":
        # PY6 (review): `Origin: null` never belongs to a legitimate local app
        # (sandboxed iframes / legacy send it); non-browser clients send no
        # Origin header at all and stay allowed.
        return False
    if origin:
        host = (request.headers.get("host") or "").strip().lower()
        if origin.rstrip("/").lower() not in (f"http://{host}", f"https://{host}"):
            return False
    return True


def _spreadsheet_text(value):
    """Neutralize formula-leading text at CSV export, retaining stored values."""
    if isinstance(value, str) and value.lstrip().startswith(
            ("=", "+", "-", "@", "＝", "＋", "－", "＠")):
        return "\t" + value
    return value


async def _reindex_with_disconnect(db, request):
    """Stop the worker cooperatively and drain it before releasing the request."""
    cancel = threading.Event()
    def scan_current_archive():
        # Settings changes use the same lock. Capture the root only after we
        # own it, so a queued scan cannot index an obsolete archive setting.
        with db.archive_lock:
            return reindex_library(db, Path(db.image_dir()), cancel)

    worker = asyncio.create_task(asyncio.to_thread(scan_current_archive))
    try:
        while not worker.done():
            if await request.is_disconnected():
                cancel.set()
            await asyncio.wait({worker}, timeout=0.05)
        return worker.result()
    except BaseException:
        cancel.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not worker.cancelled():
            worker.exception()
        raise


# ------------------------------------------------------------------ models

class ObservingSiteIn(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Coordinates must be numbers, not booleans")
        return value


class SettingsIn(BaseModel):
    image_dir: Optional[str] = None
    size_unit: Optional[str] = None
    observing_site: ObservingSiteIn | None = None


class LibraryEditIn(BaseModel):
    name: str                       # current object_name of an unresolved group
    new_name: Optional[str] = None
    otype: Optional[str] = None


class FolderOpenIn(BaseModel):
    rel: str                      # relative path inside the archive folder


class FolderScanIn(BaseModel):
    folder: str = Field(max_length=64)  # token from the native folder picker


class FolderDialogIn(BaseModel):
    purpose: Literal['archive', 'destination']
    current: str = Field('', max_length=4096)  # where the dialog starts, if it exists


class FolderJobIn(FolderScanIn):
    files: list[str] = Field(min_length=1, max_length=local_import.MAX_FOLDER_FILES)  # relative to the folder
    frame: str = ''
    session: str = ''
    run_id: str = ''
    compress: bool = False
    compression_mode: Literal['fast', 'safe'] = 'safe'
    session_anchor_id: Optional[int] = None


class HealthScanIn(BaseModel):
    mode: Literal["quick", "deep"] = "quick"


class CompressionIn(BaseModel):
    mode: Literal['compress', 'uncompress', 'copy']
    compression_mode: Literal['fast', 'safe'] = 'safe'
    anchor_id: int | None = Field(default=None, gt=0)
    image_ids: list[int] = Field(default_factory=list, max_length=100000)
    destination: str | None = Field(default=None, max_length=4096)


class HealthAcknowledgementIn(BaseModel):
    image_id: int | None = None
    scope: Literal['image', 'session', 'all'] = 'image'
    session_id: str | None = Field(default=None, max_length=64)
    action: Literal["forget_missing", "accept_contents"]
    confirmed: Literal[True]


# ------------------------------------------------------------------ factory

def create_app(db: Database) -> FastAPI:
    diagnostics.install_error_collector()
    health = ArchiveHealthManager(db)
    compression = CompressionManager(db)
    ingest_workers = asyncio.Semaphore(max(1, min(24, os.cpu_count() or 1)))
    ingest_admission = asyncio.Lock()
    resolver_lock = threading.Lock()
    resolver_cache = None

    def build_ingest_catalog():
        nonlocal resolver_cache
        with resolver_lock:
            revision = db.catalog_revision()
            if resolver_cache is None or resolver_cache[0] != revision:
                catalog = db.all_objects(advanced=True)
                resolver_cache = revision, catalog, CatalogResolver(catalog)
            return resolver_cache[1:]
    @asynccontextmanager
    async def lifespan(app):
        try:
            await run_in_threadpool(recover_compression, db)
            await run_in_threadpool(recover_pending, db, health.stopping.is_set)
            yield
        finally:
            for job in list(folder_jobs.values()):
                if job.task is not None and not job.task.done():
                    job.task.cancel()
                    await asyncio.gather(job.task, return_exceptions=True)
            await run_in_threadpool(compression.shutdown)
            await run_in_threadpool(health.shutdown)
    app = FastAPI(title="NightSeam", version=APP_VERSION, docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.archive_health = health
    app.state.compression = compression

    def serialize_archive(function):
        """Sync routes run in workers; lock before any metadata/file snapshot."""
        @wraps(function)
        def locked(*args, **kwargs):
            with db.archive_lock:
                if db.pending_compression():
                    raise HTTPException(409, 'Compression recovery is pending. Restart NightSeam with the original disks available.')
                return function(*args, **kwargs)
        return locked

    # DNS-rebinding guard: the local API must only ever answer requests whose
    # Host header names this machine (loopback). A malicious website could
    # otherwise rebind a domain to 127.0.0.1 and have the visitor's browser
    # issue requests against the local server - and with no CORS enabled the
    # responses stay unreadable, which makes this the one real vector.
    # Explicitly binding --host to something else + NIGHTSEAM_ALLOW_HOST
    # extends the allowed set for power users.
    _ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
    _extra_host = os.environ.get("NIGHTSEAM_ALLOW_HOST", "").strip().lower()
    if _extra_host:
        _ALLOWED_HOSTS.add(_extra_host)

    # Middleware ORDER matters (F-09): guard_host is registered LAST, so
    # Starlette runs it OUTERMOST - a rejected (foreign host / cross-site)
    # request never reaches `touch`, and constant probing cannot keep the
    # idle-exit from ever firing. touch therefore only records activity for
    # requests the guard accepted.
    @app.middleware("http")
    async def touch(request, call_next):
        _state["last_request"] = time.time()
        # Reject an oversized upload from its declared length BEFORE Starlette
        # spools the body to a temp file: the per-file cap inside the ingest
        # route can only run once the whole request has been buffered.
        limit = _MAX_REQUEST_BYTES.get(request.url.path)
        if limit is not None:
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > limit:
                return JSONResponse(
                    {"detail": f"upload too large (max "
                               f"{limit // (1024 * 1024)} MB per request)"},
                    status_code=413)
        # A publication worker may hold the connection lock while waiting on
        # SQLite. Queued requests must wait off the event loop so static UI and
        # unrelated async requests can still respond during that contention.
        if (request.method == 'POST' and request.url.path in _ARCHIVE_WRITES and
                await run_in_threadpool(pending_current, db)):
            return JSONResponse({'detail': 'An interrupted trash operation needs recovery. Inspect retained recovery records with scripts/restore_legacy_trash.py before changing the archive.'}, status_code=409)
        if (request.method == 'POST' and (request.url.path in _ARCHIVE_WRITES or
                request.url.path.endswith('/acknowledge')) and
                await run_in_threadpool(db.pending_compression)):
            return JSONResponse({'detail': 'Wait for compression to finish. If recovery failed, restart NightSeam with the original disks available.'}, status_code=409)
        resp = await call_next(request)
        # F-16: every response the app serves carries nosniff - cheap defense
        # that also covers non-HTML responses (the SVG placeholder) which the
        # CSP attachment below deliberately skips.
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith(("/api/library/health", '/api/library/compression')):
            resp.headers["Cache-Control"] = "no-store"
        # SP-02: the privileged localhost origin must not accept remote script.
        # Attach the policy to every HTML document the server serves (the SPA
        # shell and FastAPI's own error pages; a 4xx/5xx body is HTML and needs
        # it just as much, since a cross-site <script> tag can read nothing from
        # a JSON response anyway but the browser still runs what it loads).
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype.startswith("text/html"):
            resp.headers.setdefault("Content-Security-Policy", CSP_HEADER)
        # the UI ships as plain files served from the exe/source bundle; a
        # stale cached catalog.html next to fresh ES modules breaks the app
        # (missing DOM ids). Never cache the UI shell or its assets - including
        # themes.css, which carries the Workbench component overrides.
        if request.url.path in ("/", "/index.html", "/catalog.html", "/style.css",
                                "/themes.css", "/favicon.ico") or \
                request.url.path.startswith("/js/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.middleware("http")
    async def guard_host(request, call_next):
        host = (request.headers.get("host") or "").strip()
        hostname = host.rsplit(":", 1)[0].strip("[]").lower() if ":" in host else host.lower()
        if hostname not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if not _same_origin_request(request):
            return JSONResponse({"detail": "cross-site request rejected"},
                                status_code=403)
        return await call_next(request)

    # ---------------- state / settings ----------------

    @app.get('/api/diagnostics')
    def api_diagnostics():
        return Response(json.dumps(diagnostics.report(), ensure_ascii=False, indent=2),
            media_type='application/json', headers={
                'Content-Disposition': 'attachment; filename="nightseam-support-report.json"',
                'Cache-Control': 'no-store'})

    @app.get("/api/state")
    def api_state():
        # The catalog shell boots from this payload: only the fields its JS
        # actually reads (archive.js, catalog_main.js) are served.
        return {
            "version": APP_VERSION,
            "image_dir": db.image_dir(),
            "size_unit": db.setting("size_unit", "arcmin"),
            "observing_site": db.setting("observing_site"),
            "folder_dialog": folder_dialog_available(),
        }

    @app.post("/api/settings")
    @serialize_archive
    def api_set_settings(body: SettingsIn):
        changes = {}
        if body.size_unit is not None:
            if body.size_unit not in ("arcmin", "arcsec", "deg"):
                raise HTTPException(400, "size_unit must be arcmin, arcsec or deg")
            changes["size_unit"] = body.size_unit
        if body.image_dir is not None:
            with db.lock:
                if db.conn.execute('SELECT 1 FROM compression_journal LIMIT 1').fetchone():
                    raise HTTPException(409, 'An interrupted compression operation needs recovery before changing the archive folder. Restart NightSeam with its original archive available.')
            if pending_current(db) and body.image_dir.strip() != db.image_dir():
                raise HTTPException(409, 'An interrupted trash operation needs recovery before changing the archive folder. Use scripts/restore_legacy_trash.py to inspect retained recovery records.')
            # F-15: reject (never truncate) an oversized archive path - a
            # silently cut path would name a DIFFERENT folder.
            if len(body.image_dir.strip()) > 1024:
                raise HTTPException(400, "image_dir must be 1024 characters or fewer")
            changes["image_dir"] = body.image_dir.strip() or ""
        if "observing_site" in body.model_fields_set:
            changes["observing_site"] = (body.observing_site.model_dump()
                                         if body.observing_site is not None else None)
        if changes:
            db.update_settings(changes)
        return {"ok": True}

    # ---------------- image archive ----------------

    @app.post('/api/library/compression', status_code=202)
    def api_compression_start(body: CompressionIn):
        if body.mode == 'copy' and not body.destination:
            raise HTTPException(422, 'Choose a destination folder.')
        if body.mode != 'copy' and (not body.anchor_id or not body.image_ids or
                any(type(i) is not int or i <= 0 for i in body.image_ids)):
            raise HTTPException(422, 'Select a complete recorded session.')
        try:
            return compression.start(body.mode, body.anchor_id, body.image_ids, body.destination, body.compression_mode)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/api/library/compression')
    def api_compression_latest():
        try:
            return compression.snapshot()
        except KeyError:
            return None

    @app.get('/api/library/compression/{job_id}')
    def api_compression_status(job_id: str):
        try:
            return compression.snapshot(job_id)
        except KeyError:
            raise HTTPException(404, 'Conversion not found in this application instance.') from None

    @app.post('/api/library/compression/{job_id}/cancel')
    def api_compression_cancel(job_id: str):
        try:
            compression.cancel(job_id)
            return compression.snapshot(job_id)
        except KeyError:
            raise HTTPException(404, 'Conversion not found.') from None

    @app.post("/api/library/health", status_code=202)
    def api_health_start(body: HealthScanIn):
        try:
            return health.start(body.mode)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/library/health")
    def api_health_latest():
        try:
            return health.snapshot()
        except KeyError:
            return None

    @app.get("/api/library/health/{job_id}")
    def api_health_status(job_id: str):
        try:
            return health.snapshot(job_id)
        except KeyError:
            raise HTTPException(404, "Health report not found in this application instance") from None

    @app.post("/api/library/health/{job_id}/cancel")
    def api_health_cancel(job_id: str):
        try:
            health.cancel(job_id)
            return health.snapshot(job_id)
        except KeyError:
            raise HTTPException(404, "Health scan not found") from None

    @app.get("/api/library/health/{job_id}/report")
    def api_health_report(job_id: str):
        try:
            result = health.snapshot(job_id, full=True)
        except KeyError:
            raise HTTPException(404, "Health report not found") from None
        if result["status"] in ("running", "cancelling"):
            raise HTTPException(409, "Wait for the scan or cancellation to finish")
        return Response(json.dumps(result, indent=2).encode("utf-8"), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="nightseam-archive-health.json"'})

    @app.post("/api/library/health/{job_id}/acknowledge")
    def api_health_acknowledge(job_id: str, body: HealthAcknowledgementIn):
        try:
            return health.acknowledge(job_id, body.image_id, body.action,
                scope=body.scope, session_id=body.session_id)
        except KeyError:
            raise HTTPException(404, "Health report not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/reindex")
    async def api_library_reindex(request: Request):
        """Index new files and repair uniquely fingerprinted missing paths."""
        try:
            return await _reindex_with_disconnect(db, request)
        except ReindexCancelled:
            raise HTTPException(409, "Reindex cancelled before publication; no library records changed.") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    _SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
    _RUN_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

    class OrderedWorkers:
        """One file's ingest worker slot, granted in selection order.

        Streamed files finish arriving slightly out of order. Out-of-order slot
        holders could all wait to publish behind a predecessor that cannot get
        a slot; granting slots by index keeps the earliest file runnable.
        """
        def __init__(self, ready, following):
            self.ready, self.following = ready, following

        async def acquire(self):
            await self.ready.wait()
            await ingest_workers.acquire()
            self.following.set()

        def release(self):
            ingest_workers.release()

    def serialize_ingest(function):
        @wraps(function)
        async def admitted(*args, **kwargs):
            # Wait asynchronously before a request starts pinning archive files;
            # queued imports must not occupy all filesystem executor workers.
            async with ingest_admission:
                return await function(*args, **kwargs)
        return admitted

    _INGEST_OPTIONS = frozenset(('frame', 'session', 'run_id', 'compress', 'compression_mode',
                                 'session_anchor_id', 'file_count'))

    def ingest_options(fields):
        """Validate import options as FastAPI's former Form parameters did."""
        def option(name, kind, default):
            value = fields.get(name, '')
            if value == '':
                return default
            try:
                return TypeAdapter(kind).validate_python(value)
            except ValidationError as exc:
                raise HTTPException(422, f'Invalid {name}: {exc.errors()[0]["msg"]}') from None
        options = {'frame': fields.get('frame', ''), 'session': fields.get('session', ''),
            'run_id': fields.get('run_id', ''), 'compress': option('compress', bool, False),
            'compression_mode': option('compression_mode', Literal['fast', 'safe'], 'safe'),
            'session_anchor_id': option('session_anchor_id', int, None),
            'file_count': option('file_count', int, None)}
        count = options['file_count']
        if count is not None and count > MAX_FILES_PER_REQUEST:
            raise HTTPException(400, f"too many files in one request (max "
                                     f"{MAX_FILES_PER_REQUEST}) - the client uploads in chunks")
        if count is not None and count < 1:
            raise HTTPException(422, 'Invalid file_count: must be at least 1')
        return options

    class Unattempted(Exception):
        pass

    class Refused(Exception):
        pass

    class IngestRun:
        """One import request's staging and ordered publication.

        Streamed uploads and local folder files share it. Each file stages on
        its own; publication follows selection order, and a refusal or the
        first database failure stops every later file.
        """
        def __init__(self):
            self.options = self.archive = self.target = self.sess = self.run = None
            self.catalog = self.resolver = self.dedup_batch = None
            self.tasks, self.turns, self.admitted = [], [asyncio.Event()], [asyncio.Event()]
            self.turns[0].set()
            self.admitted[0].set()
            self.body_done = asyncio.Event()
            self.refused = False
            self.database_failure_index = None
            self.index_cancel = threading.Event()

        def record_import(self, r, current_archive):
            # Called under finalization's archive lock, so a concurrent reindex
            # or edit cannot interleave with the history write.
            parts = str(r.get("stored") or "").replace("\\", "/").split("/")
            if len(parts) < 3:
                return
            db.merge_import_batch(self.run, {
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                "slug": parts[0], "session": parts[1],
                "frame": r.get("imagetyp") or "light",
                "object_id": (r.get("object") or {}).get("id"),
                "object_name": (r.get("object") or {}).get("name") or parts[0],
                "n_files": 1, "total_exposure": float(r.get("exposure") or 0.0),
                "first_obs": r.get("date_obs"), "image_dir": str(current_archive)})

        async def start(self, fields):
            current = ingest_options(fields)
            self.archive = Path(db.image_dir())
            frame = current['frame']
            if current['session_anchor_id'] is not None:
                if current['session'].strip():
                    raise HTTPException(400, "Use a selected session or a folder override, not both.")
                try:
                    self.target = await _archive_io(calibration_import_target, db, self.archive,
                                                    current['session_anchor_id'], frame.strip().lower())
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
            try:
                await run_in_threadpool(self.archive.mkdir, parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(
                    500, f"Cannot create archive folder {self.archive}: {exc}")
            # Build once per catalog revision, outside the event loop. Image-only
            # writes do not rebuild thousands of name/coordinate lookup entries.
            self.catalog, self.resolver = await run_in_threadpool(build_ingest_catalog)
            sess = current['session'].strip()
            self.sess = sess if _SESSION_NAME_RE.match(sess) else None  # else auto session
            run = current['run_id'].strip()
            # one private grouping key for this request
            self.run = run if run and _RUN_ID_RE.match(run) else uuid.uuid4().hex
            # Upload sizes are unknown until each part ends.
            self.dedup_batch = await _archive_io(DedupBatch.prepare, db, self.archive, None,
                self.index_cancel, on_cancel=lambda index: index.close(), cancel_event=self.index_cancel)
            self.options = current

        def add(self, source):
            self.turns.append(asyncio.Event())
            self.admitted.append(asyncio.Event())
            self.tasks.append(asyncio.create_task(self._import(len(self.tasks), source)))
            return self.tasks[-1]

        async def _import(self, index, source):
            options, turns, admitted = self.options, self.turns, self.admitted
            name = Path(source.filename or "image.fit").name
            async def before_publish():
                await turns[index].wait()
                if options['file_count'] is None:
                    await self.body_done.wait()
                if self.refused:
                    raise Refused
                if self.database_failure_index is not None:
                    raise Unattempted
            try:
                # Local sources open here so an unreadable file fails alone.
                opening = getattr(source, "open_source", None)
                failure = opening and await _archive_io(opening)
                if failure:
                    result = {"file": name, "ok": False, "error": failure}
                else:
                    result = await ingest_one(
                        db, self.catalog, self.archive, source, options['frame'], self.sess,
                        resolver=self.resolver, on_import=self.record_import,
                        enforce_archive_root=True, session_target=self.target, compress=options['compress'],
                        compression_mode=options['compression_mode'], before_publish=before_publish,
                        dedup_batch=self.dedup_batch, workers=OrderedWorkers(admitted[index], admitted[index + 1]))
            except ValueError:
                log.exception("Exposure validation failed for %s", source.filename)
                result = {"file": name, "ok": False,
                          "error": "Exposure has an invalid archive filename or metadata."}
            except sqlite3.Error:
                log.exception("Database failure during exposure import")
                self.database_failure_index = index
                result = {"file": name, "ok": False, "unconfirmed": True,
                          "error": "Database error; this file's outcome is unconfirmed. Refresh the library before retrying."}
            except Unattempted:
                result = {"file": name, "ok": False,
                    "unattempted": True, "error": "Not imported because the database failed earlier in this request."}
            except (Refused, UploadInterrupted):
                result = {"file": name, "ok": False, "unattempted": True,
                          "error": "Not imported because the upload request was interrupted or refused."}
            finally:
                source.abandon()
                # Early validation failures still wait for their predecessor:
                # publication order and database-failure boundaries stay exact.
                if not asyncio.current_task().cancelling():
                    await admitted[index].wait()
                    await turns[index].wait()
                admitted[index + 1].set()
                turns[index + 1].set()
            if self.database_failure_index is not None and index > self.database_failure_index:
                result = {"file": name, "ok": False,
                    "unattempted": True, "error": "Not imported because the database failed earlier in this request."}
            return result

        def refuse(self):
            # Refused, malformed or disconnected requests publish nothing further.
            self.refused = True
            self.body_done.set()

        async def drain(self, exc):
            if isinstance(exc, asyncio.CancelledError):
                for task in self.tasks:
                    task.cancel()
            # Workers drain before staging is released.
            await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.dedup_batch is not None:
                await _archive_io(self.dedup_batch.close)

        async def finish(self):
            self.body_done.set()
            try:
                return await asyncio.gather(*self.tasks)
            except BaseException:
                for task in self.tasks:
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
                raise
            finally:
                await _archive_io(self.dedup_batch.close)

    @app.post("/api/images/ingest")
    @serialize_ingest
    async def api_ingest_images(request: Request):
        """Import multipart FITS parts while they arrive.

        Option fields must precede the files. Each file streams into its own
        archive stage; nothing is spooled to a temporary copy first. Without a
        leading file_count, publication waits until the complete body has
        arrived and passed request validation (file count, option order), so a
        refused request publishes nothing. With file_count, files publish as
        soon as they are ready; a later protocol error or disconnect stops
        further publication, as an interrupted request always has.
        """
        fields = {}
        job = IngestRun()
        parts = multipart_parts(request)
        try:
            try:
                async for kind, name, value in parts:
                    if kind == 'field':
                        if name in _INGEST_OPTIONS:
                            if job.options is not None:
                                raise HTTPException(400, "Import options must precede the files.")
                            fields[name] = value
                        continue
                    if name != 'files':
                        value.abandon()
                        continue
                    if job.options is None:
                        await job.start(fields)
                    limit = job.options['file_count'] or MAX_FILES_PER_REQUEST
                    if len(job.tasks) >= limit:
                        raise HTTPException(
                            400, f"too many files in one request (max {limit}) - the client uploads in chunks")
                    job.add(value)
            except MalformedUpload as exc:
                raise HTTPException(400, str(exc)) from None
            if job.options is None:
                raise HTTPException(422, "No files were uploaded.")
        except BaseException as exc:
            job.refuse()
            await parts.aclose()
            await job.drain(exc)
            raise
        return {"results": await job.finish()}

    folder_dialog = {'lock': threading.Lock(), 'last': None}

    async def show_folder_dialog(title, start):
        """The Linux desktop's folder dialog for the browser UI; one at a time."""
        if not folder_dialog_available():
            raise HTTPException(409, 'No folder dialog is available here. Install zenity or kdialog, or type the path.')
        if not folder_dialog['lock'].acquire(blocking=False):
            raise HTTPException(409, 'A folder dialog is already open.')
        try:
            return await run_in_threadpool(pick_folder_dialog, title, start)
        except OSError as exc:
            raise HTTPException(500, str(exc)) from None
        finally:
            folder_dialog['lock'].release()

    @app.post("/api/images/folder/pick")
    async def api_folder_pick():
        """Pick a folder to import, as the Windows window's bridge does.

        Returns an opaque token, so the page never names a source path.
        """
        chosen = await show_folder_dialog('Choose a folder to import', folder_dialog['last'])
        token = chosen and local_import.register(chosen)
        if not token:
            return {'cancelled': True}
        folder_dialog['last'] = chosen
        return {'token': token, 'name': Path(chosen).name or chosen}

    @app.post("/api/folder-dialog")
    async def api_folder_dialog(body: FolderDialogIn):
        """Pick the archive folder or a copy destination, as the window's Browse… does.

        These fields hold paths the user saves, so the chosen path is returned.
        """
        title = 'Choose the archive folder' if body.purpose == 'archive' else 'Choose a destination folder'
        chosen = await show_folder_dialog(title, body.current.strip() or None)
        return {'path': chosen} if chosen else {'cancelled': True}

    def picked_folder(token):
        root = local_import.folder(token)
        if root is None:
            raise HTTPException(404, "Choose the folder again; this selection is no longer available.")
        return root

    @app.post("/api/images/folder/scan")
    async def api_folder_scan(body: FolderScanIn):
        """List the FITS exposures in a folder chosen in the native picker."""
        root = picked_folder(body.folder)
        try:
            return await run_in_threadpool(local_import.scan, root, Path(db.image_dir()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except OSError as exc:
            raise HTTPException(400, f"Cannot read folder {root}: {exc.strerror or exc}") from None

    class FolderJob:
        """One continuous folder import that the page polls for results.

        A folder request carries no file bytes, so one job takes the whole
        folder and keeps a fixed window of files staging while earlier ones
        publish. Batches drained that pipeline at every boundary, which left a
        cold source disk idle between bursts.
        """
        def __init__(self, sources):
            self.id = uuid.uuid4().hex
            self.sources = sources
            self.results = []
            self.status = 'running'
            self.task = None

        def snapshot(self, since=0):
            return {'id': self.id, 'status': self.status, 'total': len(self.sources),
                    'done': len(self.results), 'results': self.results[since:]}

        async def run(self, run):
            pending = deque()
            upcoming = iter(self.sources)

            def admit():
                source = next(upcoming, None)
                if source is not None:
                    pending.append(run.add(source))
            try:
                try:
                    for _ in range(FOLDER_IMPORT_WINDOW):
                        admit()
                    while pending:
                        self.results.append(await pending.popleft())
                        admit()
                except BaseException as exc:
                    run.refuse()
                    await run.drain(exc)
                    # Files that finished before the stop keep their outcome.
                    for task in pending:
                        if task.done() and not task.cancelled() and task.exception() is None:
                            self.results.append(task.result())
                        else:
                            break
                    raise
                await run.finish()
                self.status = 'completed'
            except asyncio.CancelledError:
                self.status = 'cancelled'
                self.stopped('Not imported because NightSeam was closing.')
                raise
            except Exception:
                log.exception('Folder import failed')
                self.status = 'failed'
                self.stopped('Not imported because the folder import stopped; refresh the library before retrying.')
            finally:
                ingest_admission.release()

        def stopped(self, error):
            for source in self.sources[len(self.results):]:
                self.results.append({'file': source.filename, 'ok': False, 'unattempted': True, 'error': error})

    folder_jobs = {}

    @app.post("/api/images/folder/jobs", status_code=202)
    async def api_folder_job_start(body: FolderJobIn):
        """Start importing a picked folder's scanned files in one continuous run.

        Upload staging and publication rules apply unchanged; source files are
        only read. Poll the job for per-file results in selection order.
        """
        root = picked_folder(body.folder)
        if any(job.status == 'running' for job in folder_jobs.values()):
            raise HTTPException(409, 'A folder import is already running.')
        archive = Path(db.image_dir())
        try:
            sources = await run_in_threadpool(
                lambda: [local_import.exposure(root, archive, rel) for rel in body.files])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        fields = {name: value for name, value in body.model_dump(exclude={'folder', 'files'}).items()
                  if value is not None}
        run = IngestRun()
        # The job holds import admission for its whole run, as a request does.
        await ingest_admission.acquire()
        try:
            await run.start(fields)
        except BaseException as exc:
            run.refuse()
            await run.drain(exc)
            ingest_admission.release()
            raise
        # Every source is known up front; nothing waits for more of a body.
        run.body_done.set()
        job = FolderJob(sources)
        for old in [key for key, value in folder_jobs.items() if value.status != 'running'][:-3]:
            del folder_jobs[old]
        folder_jobs[job.id] = job
        job.task = asyncio.create_task(job.run(run))
        return job.snapshot()

    @app.get("/api/images/folder/jobs/{job_id}")
    async def api_folder_job(job_id: str, since: int = Query(0, ge=0)):
        job = folder_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, 'Folder import not found in this application instance.')
        return job.snapshot(since)

    @app.get("/api/library/imports")
    def api_library_imports():
        return db.import_batches()

    @app.post("/api/library/open_folder")
    def api_library_open_folder(body: FolderOpenIn):
        # F-13: the shared safe_join rail validates the segment rules the
        # hand-rolled check missed (control characters, device names) - a raw
        # NUL used to escape as a 500 instead of a clean 400. The archive root
        # itself stays openable via "." (safe_join refuses the root, so that
        # case is handled before it). F-15: reject an oversized rel cheaply
        # before it reaches the filesystem.
        rel = body.rel.strip().replace("\\", "/").strip("/")
        if not rel or len(rel) > 512 or any(p == ".." for p in rel.split("/")):
            raise HTTPException(400, "bad folder path")
        base = Path(db.image_dir()).resolve()
        if rel == ".":
            folder = base
        else:
            folder = safe_join(base, rel)
            if folder is None:
                raise HTTPException(400, "folder outside the archive")
        if not folder.is_dir():
            raise HTTPException(404, "folder not found on disk")
        try:
            open_folder(folder)
        except OSError as exc:
            raise HTTPException(500, f"cannot open folder: {exc}")
        return {"ok": True}

    @app.get("/api/library")
    def api_library():
        from . import imglib
        imgs = db.images()
        objs = imglib.group_library(imgs, db.all_objects(advanced=True))
        # Construct/render in this worker: returning a dict makes FastAPI walk
        # and encode the complete library on the ASGI event loop afterwards.
        return JSONResponse(jsonable_encoder({
            "image_dir": db.image_dir(),
            "total_images": len(imgs),
            "total_exposure": round(
                sum(float(i.get("exposure") or 0.0) for i in imgs), 1),
            "objects": objs,
        }))

    @app.post("/api/library/edit")
    @serialize_archive
    def api_library_edit(body: LibraryEditIn):
        """Rename/retype an unresolved library object (object_id IS NULL)."""
        if db.pending_trash():
            raise HTTPException(409, 'Interrupted trash recovery must finish before editing archive objects. Use scripts/restore_legacy_trash.py to inspect retained recovery records.')
        # F-15: names become ARCHIVE FOLDER names (obj_slug keeps letters,
        # digits, spaces, _ and - but has no length bound) - the UI's
        # maxlength=80 is the real contract, and the stored object_name column
        # is capped at 120 at ingest. Reject oversized names instead of
        # creating paths that cannot be addressed on Windows.
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name required")
        if len(name) > 120:
            raise HTTPException(400, "name must be 120 characters or fewer")
        new_name = (body.new_name or "").strip() or name
        if len(new_name) > 120:
            raise HTTPException(400, "new_name must be 120 characters or fewer")
        otype = body.otype or None
        if otype is not None and otype not in (
                "galaxy", "globular", "open_cluster", "nebula", "planetary",
                "supernova_remnant", "star_cloud", "asterism"):
            raise HTTPException(400, f"unknown type: {otype}")
        images = db.images()
        rows = [i for i in images
                if not i["object_id"]
                and name_key(i["object_name"]) == name_key(name)]
        if not rows:
            raise HTTPException(404, "Object not found in library")
        archive = Path(db.image_dir())
        source_slug = obj_slug(name).casefold()
        if source_slug != obj_slug(new_name).casefold():
            # One folder move cannot relocate all members of a multi-root group.
            for row in rows:
                rel = safe_rel(row.get("file"))
                path = safe_join(archive, rel)
                if (rel is None or rel.split("/", 1)[0].casefold() != source_slug
                        or path is None or not path.is_file()):
                    raise HTTPException(
                        409, "Cannot rename this object: its files span different archive folders "
                             "or a stored file is missing. Preserve the archive and repair its paths first.")
            # Different logical objects can share a physical slug (Obj! and
            # Obj?), including resolved objects. Refuse a whole-folder move
            # before touching any bytes when another group's row lives there.
            for image in images:
                rel = safe_rel(image.get("file"))
                if rel is None or rel.split("/", 1)[0].casefold() != source_slug:
                    continue
                same_group = (not image["object_id"] and
                              name_key(image["object_name"]) == name_key(name))
                if not same_group:
                    raise HTTPException(
                        409, "Cannot rename this archive folder: it contains "
                             "files belonging to another object group")
        # PY-01: the move returns the REAL destination of every frame. A rename
        # into a folder that already holds a same-named frame used to `replace`
        # it (two rows aliasing one file, the replaced pixels gone); the frame is
        # uniquified now and the stored path is updated to where it really is.
        try:
            moved = rename_archive_folder(archive, name, new_name)
        except ArchiveRenameRecoveryError as exc:
            log.exception("Archive folder rename failed with incomplete recovery")
            raise HTTPException(
                500, "Archive rename failed and recovery is incomplete. "
                     "Database records were not changed. Inspect and preserve "
                     "the affected archive files, resolve the filesystem "
                     "problem, then run Reindex folders. It repairs only unique "
                     "full-file fingerprint matches and reports ambiguous matches; "
                     "unrestored paths: " +
                     ", ".join(path for path, _ in exc.failures)) from exc
        if moved is None:
            # the folder could not be moved (files open in another program):
            # leave the stored paths alone rather than pointing them at a
            # folder that does not exist
            raise HTTPException(
                500, "Could not move the archive folder - close any program "
                     "using those files and try again")
        try:
            db.rename_images(name, new_name, otype, obj_slug(new_name), moved)
        except BaseException as exc:
            # F-22: the disk move already happened - undo it so the files are
            # back where the (unchanged) database rows point, then surface the
            # database failure instead of leaving a mixed state.
            failures = undo_archive_rename(archive, moved)
            if failures:
                log.exception("Database update failed after archive rename")
                for path, message in failures:
                    log.error("Archive rename undo failed for %s: %s", path, message)
                raise HTTPException(
                    500, "Archive rename database update failed and recovery "
                         "is incomplete. Inspect and preserve the affected "
                         "archive files, resolve the filesystem problem, then "
                         "run Reindex folders. It repairs only unique full-file "
                         "fingerprint matches and reports ambiguous matches; unrestored "
                         "paths: " + ", ".join(path for path, _ in failures)) from exc
            raise
        return {"ok": True, "name": new_name, "otype": otype}

    @app.get("/api/library/export")
    def api_library_export(format: str = "json", object_name: str | None = None):
        from . import imglib
        if format not in ("csv", "json"):
            raise HTTPException(400, "format must be csv or json")
        imgs = db.images()
        objs = None
        if object_name is not None:
            # Match the name the Library actually displays, rather than the
            # original FITS alias or stale name stored on individual frames.
            objs = [obj for obj in imglib.group_library(
                imgs, db.all_objects(advanced=True)) if obj["name"] == object_name]
            if not objs:
                raise HTTPException(404, "Object not found in library")
            image_ids = {image["id"] for obj in objs for image in obj["images"]}
            # Preserve raw row order/CSV columns; grouped JSON records already
            # pass through public_image_record and omit local fingerprints.
            imgs = [image for image in imgs if image["id"] in image_ids]
        filename = "nightseam-object" if object_name is not None else "nightseam-library"
        if format == "csv":
            import csv as csvmod
            import io
            buf = io.StringIO()
            # Keep protective tabs inside quoted cells; stored text is unchanged.
            w = csvmod.writer(buf, quoting=csvmod.QUOTE_ALL)
            columns = ["id", "file", "orig_name", "object_id", "object_name",
                       "otype", "ra_hours", "dec_deg", "exposure", "date_obs",
                       "filter", "telescope", "imagetyp", "ingested_at"]
            w.writerow(columns)
            for i in imgs:
                w.writerow([_spreadsheet_text(i.get(column)) for column in columns])
            return Response(
                content=buf.getvalue().encode("utf-8-sig"),
                media_type="text/csv",
                headers={"Content-Disposition":
                         f'attachment; filename="{filename}.csv"'})
        if format == "json":
            if objs is None:
                objs = imglib.group_library(imgs, db.all_objects(advanced=True))
            return Response(
                content=json.dumps({
                    "image_dir": db.image_dir(),
                    "total_images": len(imgs),
                    "total_exposure": round(sum(
                        float(i.get("exposure") or 0.0) for i in imgs), 1),
                    "objects": objs,
                }, indent=1).encode("utf-8"),
                media_type="application/json",
                headers={"Content-Disposition":
                         f'attachment; filename="{filename}.json"'})
        raise HTTPException(400, "format must be csv or json")

    # ---------------- static files ----------------

    sd = static_dir()

    @app.get("/")
    def index():
        return FileResponse(sd / "catalog.html")

    @app.get("/catalog.html")
    def catalog_index():
        return FileResponse(sd / "catalog.html")

    app.mount("/", StaticFiles(directory=str(sd)), name="static")

    return app
