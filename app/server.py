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
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .archive_health import ArchiveHealthManager
from .database import Database
from .fits_preview import PreviewUnavailable, preview_image
from .imglib import CatalogResolver, name_key
from .ingest import (ArchiveRenameRecoveryError, ReindexCancelled, _archive_io,
                     calibration_import_target, ingest_one, obj_slug,
                     reindex_library, rename_archive_folder, undo_archive_rename)
from .paths import safe_join, safe_rel, static_dir
from .version import app_version

APP_VERSION = app_version()
log = logging.getLogger(__name__)


# per-request upload caps, checked against Content-Length before the body is
# spooled (ingest: MAX_FILES_PER_REQUEST files, each capped at 1 GiB inside
# app/ingest.py)
MAX_FILES_PER_REQUEST = 24
_MAX_REQUEST_BYTES = {
    "/api/images/ingest": MAX_FILES_PER_REQUEST * (1 << 30),
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
    this check any website the user visits could call /api/shutdown or another
    state-changing endpoint. `Sec-Fetch-Site` is sent by current browsers and
    `Origin` accompanies cross-origin requests; non-browser clients (PowerShell,
    curl, TestClient) send neither and stay allowed."""
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


class HealthScanIn(BaseModel):
    mode: Literal["quick", "deep"] = "quick"


class HealthAcknowledgementIn(BaseModel):
    image_id: int
    action: Literal["forget_missing", "accept_contents"]
    confirmed: Literal[True]


# ------------------------------------------------------------------ factory

def create_app(db: Database) -> FastAPI:
    health = ArchiveHealthManager(db)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await run_in_threadpool(health.shutdown)
    app = FastAPI(title="NightSeam", version=APP_VERSION, docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.archive_health = health

    def serialize_archive(function):
        """Sync routes run in workers; lock before any metadata/file snapshot."""
        @wraps(function)
        def locked(*args, **kwargs):
            with db.archive_lock:
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
        resp = await call_next(request)
        # F-16: every response the app serves carries nosniff - cheap defense
        # that also covers non-HTML responses (the SVG placeholder) which the
        # CSP attachment below deliberately skips.
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/library/health") or (
                request.url.path.startswith("/api/images/") and request.url.path.endswith("/preview")):
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

    @app.get("/api/state")
    def api_state():
        # The catalog shell boots from this payload: only the fields its JS
        # actually reads (archive.js, catalog_main.js) are served.
        return {
            "version": APP_VERSION,
            "image_dir": db.image_dir(),
            "size_unit": db.setting("size_unit", "arcmin"),
            "observing_site": db.setting("observing_site"),
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

    @app.get("/api/images/{image_id}/preview")
    def api_image_preview(image_id: int = ApiPath(gt=0, le=(1 << 63) - 1),
                          size: int = Query(512, ge=64, le=1024)):
        try:
            result = preview_image(db, image_id, size, health.stopping.is_set)
        except PreviewUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return Response(result.png, media_type="image/png", headers={
            "X-FITS-HDU": str(result.hdu), "X-FITS-Size": f"{result.width}x{result.height}",
            "X-FITS-Plane": "1" if result.first_plane else "none", "Cache-Control": "no-store"})

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
            return health.acknowledge(job_id, body.image_id, body.action)
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

    @app.post("/api/images/ingest")
    async def api_ingest_images(files: list[UploadFile] = File(...),
                                frame: str = Form(""),
                                session: str = Form(""),
                                run_id: str = Form(""),
                                session_anchor_id: int | None = Form(None)):
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(
                400, f"too many files in one request (max "
                     f"{MAX_FILES_PER_REQUEST}) - the client uploads in chunks")
        archive = Path(db.image_dir())
        target = None
        if session_anchor_id is not None:
            if session.strip():
                raise HTTPException(400, "Use a selected session or a folder override, not both.")
            try:
                target = await _archive_io(calibration_import_target, db, archive,
                                           session_anchor_id, frame.strip().lower())
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        try:
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(archive.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                500, f"Cannot create archive folder {archive}: {exc}")
        # F-06: the catalog snapshot and resolver build are pure CPU/DB work
        # (measured ~350 ms combined on the full catalog); run both in the same
        # threadpool worker so they cannot stall the event loop. The awaited
        # call stays outside the archive-directory try/except above: a database
        # or resolver error must not surface as a directory-creation failure.
        def build_ingest_catalog():
            catalog = db.all_objects(advanced=True)
            return catalog, CatalogResolver(catalog)

        catalog, resolver = await run_in_threadpool(build_ingest_catalog)
        sess = session.strip()
        if not _SESSION_NAME_RE.match(sess):
            sess = None                  # not a sane folder name -> auto session
        run = run_id.strip()
        if not run or not _RUN_ID_RE.match(run):
            run = uuid.uuid4().hex  # one private grouping key for this request

        def record_import(r, current_archive):
            # Called under finalization's archive lock, so a concurrent reindex
            # or edit cannot interleave with the history write.
            parts = str(r.get("stored") or "").replace("\\", "/").split("/")
            if len(parts) < 3:
                return
            db.merge_import_batch(run, {
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
                "slug": parts[0], "session": parts[1],
                "frame": r.get("imagetyp") or "light",
                "object_id": (r.get("object") or {}).get("id"),
                "object_name": (r.get("object") or {}).get("name") or parts[0],
                "n_files": 1, "total_exposure": float(r.get("exposure") or 0.0),
                "first_obs": r.get("date_obs"), "image_dir": str(current_archive)})

        results = []
        for index, upload in enumerate(files):
            try:
                result = await ingest_one(
                    db, catalog, archive, upload, frame, sess,
                    resolver=resolver, on_import=record_import,
                    enforce_archive_root=True, session_target=target)
            except ValueError:
                log.exception("Exposure validation failed for %s", upload.filename)
                result = {"file": Path(upload.filename or "image.fit").name,
                          "ok": False, "error": "Exposure has an invalid archive filename or metadata."}
            except sqlite3.Error:
                log.exception("Database failure during exposure import")
                results.append({"file": Path(upload.filename or "image.fit").name,
                                "ok": False, "unconfirmed": True,
                                "error": "Database error; this file's outcome is unconfirmed. Refresh the library before retrying."})
                results.extend({"file": Path(item.filename or "image.fit").name,
                                "ok": False, "unattempted": True,
                                "error": "Not imported because the database failed earlier in this request."}
                               for item in files[index + 1:])
                break
            results.append(result)
        return {"results": results}

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
            os.startfile(folder)         # Windows file explorer
        except OSError as exc:
            raise HTTPException(500, f"cannot open folder: {exc}")
        return {"ok": True}

    @app.get("/api/library")
    def api_library():
        from . import imglib
        imgs = db.images()
        objs = imglib.group_library(imgs, db.all_objects(advanced=True))
        return {
            "image_dir": db.image_dir(),
            "total_images": len(imgs),
            "total_exposure": round(
                sum(float(i.get("exposure") or 0.0) for i in imgs), 1),
            "objects": objs,
        }

    @app.post("/api/library/edit")
    @serialize_archive
    def api_library_edit(body: LibraryEditIn):
        """Rename/retype an unresolved library object (object_id IS NULL)."""
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
