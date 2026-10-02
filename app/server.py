"""FastAPI application: REST API for catalog, night planning, panoramas, sites."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .backup import _export_zip_to, apply_import_zip, build_export_zip
from .database import (Database, PROFILE_BINS, _finalize_num_setting, new_id,
                       _validate_import_landmarks,
                       _validate_import_provenance,
                       confidence_values_ok, normalize_horizon_landmarks,
                       normalize_horizon_provenance, profile_values_ok)
from .fits import parse_dec_deg, parse_ra_hours
from .imglib import CatalogResolver, name_key
from .ingest import (_archive_io, calibration_import_target, ingest_one, obj_slug, reindex_library,
                     rename_archive_folder)
from .paths import TOKEN_RE, data_dir, resource_dir, safe_join, safe_token, static_dir
from .rig_presets import presets_payload as rig_presets_payload
from .rig_presets import remove_seeded_once as unseed_rig_presets
from .theme import normalize_theme
from .version import app_version

from starlette.background import BackgroundTask

APP_VERSION = app_version()

# panorama ids come from new_id() (hex + digits) or an imported bundle. The
# crop route interpolates the id into a FILE NAME, so an id that is not a plain
# token is refused outright - and the self-heal glob must never be fed a pattern
# metacharacter.
_PID_RE = TOKEN_RE

# per-request upload caps, checked against Content-Length before the body is
# spooled (ingest: MAX_FILES_PER_REQUEST files, each capped at 1 GiB inside
# app/ingest.py; import: the whole-setup ZIP)
MAX_FILES_PER_REQUEST = 24
_MAX_REQUEST_BYTES = {
    "/api/images/ingest": MAX_FILES_PER_REQUEST * (1 << 30),
    "/api/import": 2 * (1 << 30),
    "/api/panorama": 64 * (1 << 20),
}

# PY-05: SQLite INTEGER ids are signed 64-bit. Pydantic accepts arbitrary
# precision ints, so a hand-crafted URL like /api/images/999999999999999999999/
# preview reached sqlite3 and raised OverflowError -> a 500 + traceback instead
# of a clean "not found". `_int_id` bounds the path param by hand so the
# behaviour is a documented 404 (a pydantic-constrained `int` path param would
# answer 422 and its error body would echo the huge value).
_INT_ID_MAX = 2 ** 63 - 1

_state = {"last_request": time.time(), "shutdown": False}

# Catalog releases are independent of the former combined website releases.
# Update checks remain unavailable until a catalog-specific source is configured.

# Content-Security-Policy for the HTML the server serves (SP-02, defence in
# depth). The load-bearing part is that NO remote origin may provide script:
# `script-src 'self'` is what makes a hostile DNS/CA/host compromise unable to
# run code in the 127.0.0.1 origin even if some future code tries a JSONP tag.
#   * BUG-002: `'unsafe-inline'` is GONE from script-src. The inline bootstrap
#     and event handlers were removed; imported custom ids and `imagetyp` values
#     are rendered without executable markup.
#   * the two inline `onerror=` image fallbacks that used to live in
#     static/js/{targets,tonight}.js are event listeners now, so no handler needs
#     the exemption at all.
#   * style-src keeps `'unsafe-inline'`: the shipped themes and the JS set
#     `style=`/`element.style` all over the UI, and inline STYLE cannot execute.
#   * img-src is pinned to the hosts the desktop actually renders images from
#     (cached Wikipedia/Wikidata thumbnails are full URLs; DSS cutouts and the
#     API payloads answer from /api/object/..., whose full-size bytes are shown
#     through a same-page blob URL).
#   * connect-src keeps the weather panel working and stays restricted to it.
CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://upload.wikimedia.org "
    "https://commons.wikimedia.org https://alasky.cds.unistra.fr; "
    "connect-src 'self' https://api.open-meteo.com https://air-quality-api.open-meteo.com; "
    "worker-src 'self'; "
    "font-src 'self'"
)


def _int_id(value: int) -> int:
    """A stored-row id that SQLite can actually hold, else 404 (PY-05)."""
    if not 0 <= value <= _INT_ID_MAX:
        raise HTTPException(404, "not found")
    return value


def _restore_error(message, summary: dict) -> str:
    """The error text for a failed restore, naming the state it left behind.

    PY-03: an import that fails AFTER `import_bundle` replaced the library used
    to answer a bare `400 Failed to write restored files` (or `zip member too
    large`), which reads as "bundle rejected - nothing happened" while the user's
    data had in fact been replaced. Say so instead."""
    if summary.get("restore_incomplete"):
        return (f"{message} - the setup database was already replaced, so the "
                "library is in a partially restored state; the pre-import data "
                "is in <db>.backup-auto.zip")
    return str(message)


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


async def _drain_upload(file: UploadFile, dest: Path, limit: int,
                        label: str) -> int:
    """Stream an upload into `dest` in 1 MB chunks, aborting as soon as
    `limit` is exceeded (partial file removed). Peak memory stays at one
    chunk regardless of upload size. Returns the byte count."""
    total = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        400, f"{label} too large (max {limit // (1024 * 1024)} MB)")
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"{label} is empty")
    return total


# ------------------------------------------------------------------ models

class SiteIn(BaseModel):
    name: str
    lat: float
    lon: float
    elev: int = 0

class SettingsIn(BaseModel):
    active_site_id: Optional[int] = None
    active_pano_id: Optional[str] = None
    min_useful_alt: Optional[float] = None
    moon_aware: Optional[bool] = None
    filters: Optional[dict] = None
    size_unit: Optional[str] = None
    active_rig_id: Optional[int] = None
    favorites: Optional[list] = None
    wizard_done: Optional[bool] = None
    image_dir: Optional[str] = None
    edge_space: Optional[int] = None
    adv_mode: Optional[bool] = None
    bortle: Optional[int] = None
    timezone: Optional[str] = None
    theme: Optional[str] = None
    weather_auto: Optional[bool] = None
    view_full_day: Optional[bool] = None
    min_vis: Optional[int] = None


class RigIn(BaseModel):
    name: str
    focal_mm: float
    sensor_w: float
    sensor_h: float


class CustomIn(BaseModel):
    name: str
    ra: str                      # decimal hours or "5h 34.5m" / "5 34 30"
    dec: str                     # decimal degrees or "+22 01"
    otype: str = "galaxy"
    # T6-F1 (review of t6): a non-finite magnitude used to be stored verbatim,
    # which made GET /api/catalog answer 500 forever (allow_nan=False). It is
    # rejected by the explicit `_finite(body.vmag, "Magnitude")` check in
    # api_add_custom rather than by a pydantic `allow_inf_nan=False` here: a
    # model-level rejection produces a 422 whose error body ECHOES the offending
    # `inf` in `detail[0].input`, and serializing that response 500s again
    # (`ValueError: Out of range float values are not JSON compliant: inf`).
    # The route check yields the clean 400 the API promises.
    vmag: Optional[float] = None
    size_am: Optional[str] = ""
    constellation: Optional[str] = ""
    blurb: Optional[str] = ""


class PanoramaUpdate(BaseModel):
    name: Optional[str] = None
    az_offset: Optional[float] = None
    alt_bottom: Optional[float] = None
    alt_top: Optional[float] = None
    fov: Optional[float] = None
    strokes: Optional[list] = None
    profile: Optional[list] = None
    profile_top: Optional[list] = None
    active: Optional[bool] = None
    paint_mode: Optional[str] = None
    block_above: Optional[bool] = None
    landmarks: Optional[list] = None
    provenance: Optional[dict] = None
    confidence: Optional[list] = None


class LibraryEditIn(BaseModel):
    name: str                       # current object_name of an unresolved group
    new_name: Optional[str] = None
    otype: Optional[str] = None


class FolderOpenIn(BaseModel):
    rel: str                      # relative path inside the archive folder


# ------------------------------------------------------------------ coord parsing
# parse_ra_hours / parse_dec_deg live in app/fits.py (single source of truth)

def fov_arcmin(focal_mm: float, sensor_mm: float) -> float:
    """Field of view in arcminutes for a given focal length and sensor dimension."""
    return round(2 * math.degrees(math.atan(sensor_mm / 2 / focal_mm)) * 60, 1)


def _check_site(lat: float, lon: float, elev: int) -> None:
    """Reject coordinates that cannot describe a place on Earth (a lat of 999
    otherwise produced silently meaningless tracks)."""
    for value, lo, hi, label in ((lat, -90, 90, "Latitude"),
                                 (lon, -180, 180, "Longitude"),
                                 (elev, -500, 9000, "Elevation")):
        if value is None or not math.isfinite(float(value)) or \
                not (lo <= float(value) <= hi):
            raise HTTPException(400, f"{label} must be between {lo} and {hi}")


def _finite(value: float, label: str) -> float:
    """Reject NaN/Infinity on the API boundary (PY3, review): the response is
    serialized with allow_nan=False, so one stored NaN would 500 /api/state
    forever - and the desktop awaits that route at boot."""
    if value is None or not math.isfinite(float(value)):
        raise HTTPException(400, f"{label} must be a finite number")
    return float(value)


def _check_profile(values, label: str) -> None:
    """Panorama obstruction arrays are exactly PROFILE_BINS numbers or empty.

    Without this a short/corrupt profile was stored and then failed OPEN at
    read time (ceil_lookup returns "nothing blocks"), silently disabling the
    horizon mask the user painted. The rule itself lives in
    `database.profile_values_ok` so the PUT route and the import boundary
    cannot drift apart."""
    if values is None:
        return
    if not isinstance(values, list) or len(values) not in (0, PROFILE_BINS):
        raise HTTPException(
            400, f"{label} must be empty or exactly {PROFILE_BINS} values")
    if not profile_values_ok(values):
        raise HTTPException(400, f"{label} must contain only finite numbers")


def _check_strokes(values) -> None:
    """PY-03: `strokes` is the third stored panorama array, and the ONE both
    editors iterate (`for (const s of p.strokes)`).

    Until this existed the import boundary accepted any JSON-encodable value for
    it, so `{}` / `"abc"` / `7` could be stored and the desktop editor then threw
    `TypeError: ... is not iterable` for that record. Same fail-closed rule as the
    profile arrays; `None` counts as "not mentioned"."""
    if values is None:
        return
    if not isinstance(values, list) or not all(isinstance(s, dict) for s in values):
        raise HTTPException(400, "strokes must be a list of stroke objects")


def _check_horizon_metadata(landmarks, provenance, confidence) -> None:
    """Validate the additive horizon annotations at the API boundary."""
    if landmarks is not None:
        try:
            _validate_import_landmarks(landmarks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if provenance is not None:
        try:
            _validate_import_provenance(provenance)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if confidence is not None and not confidence_values_ok(confidence):
        raise HTTPException(400,
                            f"confidence must be empty or exactly {PROFILE_BINS} "
                            "values between 0 and 1")


# ------------------------------------------------------------------ image prefetch

_prefetch_started = False


def start_image_prefetch(db: Database) -> None:
    """Warm the local image cache for the whole catalog in the background so
    object thumbnails appear instantly later on."""
    global _prefetch_started
    if _prefetch_started:
        return
    _prefetch_started = True

    def cached_on_disk(o) -> bool:
        from . import images
        slug = images._slug(o["id"], o.get("wiki") or "", o.get("messier") or "")
        return (images.CACHE_DIR / f"{slug}.bin").exists()

    def worker():
        from . import images
        from .images import fetch_image
        log = logging.getLogger("nightseam")
        time.sleep(5)                      # let the UI settle first
        objs = db.all_objects()
        done = 0
        for o in objs:                     # pass 1
            if images.is_offline():
                time.sleep(20)             # network outage -> pause quietly
                continue
            try:
                fetch_image(o)
            except Exception:              # noqa: BLE001 - offline etc.
                pass
            done += 1
            if done % 25 == 0:
                log.info("image cache warm-up: %d/%d", done, len(objs))
            time.sleep(0.7)                # stay polite to the remote APIs
        log.info("image cache warm-up finished (%d objects)", done)

        time.sleep(60)                     # cooldown, then one retry round
        retry = [o for o in objs if not cached_on_disk(o)]
        if retry:
            log.info("image cache retry round for %d objects", len(retry))
            got = 0
            for o in retry:
                if images.is_offline():
                    time.sleep(20)
                    continue
                try:
                    blob, _ct = fetch_image(o, ignore_negative=True)
                    got += 1 if blob else 0
                except Exception:          # noqa: BLE001
                    pass
                time.sleep(1.2)
            log.info("image cache retry finished (+%d)", got)

    threading.Thread(target=worker, daemon=True, name="img-prefetch").start()


# ------------------------------------------------------------------ factory

def create_app(db: Database, *, prefetch: bool = True,
               catalog_only: bool = False) -> FastAPI:
    app = FastAPI(title="NightSeam", version=APP_VERSION, docs_url=None,
                  redoc_url=None, openapi_url=None)

    def serialize_archive(function):
        """Sync routes run in workers; lock before any metadata/file snapshot."""
        @wraps(function)
        def locked(*args, **kwargs):
            with db.archive_lock:
                return function(*args, **kwargs)
        return locked

    # Rig presets are a PICKER, not defaults: a fresh setup must be empty, so
    # nothing is ever inserted here. 0.6.1/0.6.2 did auto-seed - this drops
    # those untouched leftovers once, so an existing database ends up empty too.
    unseed_rig_presets(db)

    # DNS-rebinding guard: the local API must only ever answer requests whose
    # Host header names this machine (loopback). A malicious website could
    # otherwise rebind a domain to 127.0.0.1 and have the visitor's browser
    # issue requests against the local server - and with no CORS enabled the
    # responses stay unreadable, which makes this the one real vector.
    # Explicitly binding --host to something else + NIGHTSEAM_ALLOW_HOST
    # extends the allowed set for power users.
    _ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
    _extra_host = os.environ.get("NIGHTSEAM_ALLOW_HOST", "").strip().lower()
    if _extra_host:
        _ALLOWED_HOSTS.add(_extra_host)

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
        # SP-02: the privileged localhost origin must not accept remote script.
        # Attach the policy to every HTML document the server serves (the SPA
        # shell and FastAPI's own error pages; a 4xx/5xx body is HTML and needs
        # it just as much, since a cross-site <script> tag can read nothing from
        # a JSON response anyway but the browser still runs what it loads).
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype.startswith("text/html"):
            resp.headers.setdefault("Content-Security-Policy", CSP_HEADER)
        # the UI ships as plain files served from the exe/source bundle; a
        # stale cached index.html next to fresh ES modules breaks the app
        # (missing DOM ids). Never cache the UI shell or its assets - including
        # themes.css, which carries the Workbench component overrides.
        if request.url.path in ("/", "/index.html", "/catalog.html", "/style.css",
                                "/themes.css", "/favicon.ico") or \
                request.url.path.startswith("/js/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---------------- state / settings ----------------

    def _effective_min_alt(db) -> float:
        """The authoritative min-altitude rule: an explicit min_useful_alt
        wins; otherwise the Bortle-derived default 17 + 2*bortle ("darker
        skies -> lower default"). Used by api_state and the detail endpoint -
        previously the detail endpoint fell back to a hard 25.0 even when
        Bortle said 31."""
        mua = db.setting("min_useful_alt")
        if mua is None:
            mua = 17.0 + 2.0 * float(db.setting("bortle", 4))
        return mua

    @app.get("/api/state")
    def api_state():
        panos = db.panoramas()
        mua = _effective_min_alt(db)
        # PY-02: an imported "abc"/null used to make int() raise below, and
        # static/js/main.js awaits this route before rendering anything - so one
        # crafted bundle bricked the desktop at every boot. The import boundary
        # refuses such a value now; this is the second line of defence for a row
        # that got in some other way.
        min_vis = _finalize_num_setting(db.setting("min_vis", 1), 0, 100)
        return {
            "version": APP_VERSION,
            "sites": db.sites(),
            "active_site_id": db.setting("active_site_id"),
            "panoramas": [{"id": p["id"], "name": p["name"], "active": bool(p["active"]),
                           "az_offset": p["az_offset"], "alt_bottom": p["alt_bottom"],
                           "alt_top": p["alt_top"], "fov": p.get("fov", 360.0),
                           "paint_mode": p.get("paint_mode") or "obstacle",
                           "block_above": bool(p.get("block_above"))} for p in panos],
            "active_pano_id": db.setting("active_pano_id"),
            "min_useful_alt": mua,
            "min_useful_alt_explicit": db.setting("min_useful_alt") is not None,
            "moon_aware": bool(db.setting("moon_aware", False)),
            "filters": db.setting("filters", {"broadband": True,
                                              "narrowband_ha": False,
                                              "narrowband_oiii": False}),
            "size_unit": db.setting("size_unit", "arcmin"),
            "rigs": db.rigs(),
            "rig_presets": rig_presets_payload(),   # "Add rig" picker (no defaults)
            "active_rig_id": db.setting("active_rig_id"),
            "favorites": db.setting("favorites", []),
            "wizard_done": bool(db.setting("wizard_done", False)),
            "profile_len": PROFILE_BINS,
            "image_dir": db.image_dir(),
            "edge_space": db.setting("edge_space", 16),
            "adv_mode": bool(db.setting("adv_mode", False)),
            "bortle": db.setting("bortle", 4),
            "timezone": db.setting("timezone") or "",
            "theme": normalize_theme(db.setting("theme", None)),
            "weather_auto": bool(db.setting("weather_auto", False)),
            "view_full_day": bool(db.setting("view_full_day", False)),
            # the target list's Min-visibility gate (0 = off); see targets.js
            "min_vis": min_vis if min_vis is not None else 1,
        }

    @app.post("/api/settings")
    @serialize_archive
    def api_set_settings(body: SettingsIn):
        if body.active_site_id is not None:
            db.set_setting("active_site_id", body.active_site_id)
        if body.active_pano_id is not None:
            # PY-05: this boundary accepted ANY string while every id-consuming
            # route rejects a non-token id (`_pano_or_404`, `api_delete_pano`), so
            # /api/state advertised an "active panorama" that no route could ever
            # resolve. Apply the documented token rule here too; "" / null mean
            # "no active panorama".
            pid = body.active_pano_id or None
            if pid is not None and safe_token(pid) is None:
                raise HTTPException(
                    400, "active_pano_id must be a plain panorama id")
            try:
                db.set_active_panorama(pid)
            except ValueError as exc:
                # BUG-006: a well-formed id must still NAME a row - the pointer
                # is advertised by /api/state and resolved by planning, so a
                # dangling one is malformed input, not a silent no-op.
                raise HTTPException(400, str(exc)) from None
        if body.min_useful_alt is not None:
            db.set_setting("min_useful_alt",
                           _finite(body.min_useful_alt, "min_useful_alt"))
        if body.moon_aware is not None:
            db.set_setting("moon_aware", bool(body.moon_aware))
        if body.filters is not None:
            clean = {k: bool(v) for k, v in body.filters.items()
                     if k in ("broadband", "narrowband_ha", "narrowband_oiii")}
            db.set_setting("filters", clean)
        if body.size_unit is not None:
            if body.size_unit not in ("arcmin", "arcsec", "deg"):
                raise HTTPException(400, "size_unit must be arcmin, arcsec or deg")
            db.set_setting("size_unit", body.size_unit)
        if body.active_rig_id is not None:
            db.set_setting("active_rig_id", body.active_rig_id or None)
        if body.favorites is not None:
            db.set_setting("favorites", [str(x) for x in body.favorites])
        if body.wizard_done is not None:
            db.set_setting("wizard_done", bool(body.wizard_done))
        if body.image_dir is not None:
            db.set_setting("image_dir", body.image_dir.strip() or "")
        if body.edge_space is not None:
            db.set_setting("edge_space",
                           max(0, min(1000, int(body.edge_space))))
        if body.adv_mode is not None:
            db.set_setting("adv_mode", bool(body.adv_mode))
        if body.bortle is not None:
            db.set_setting("bortle", max(1, min(9, int(body.bortle))))
        if body.timezone is not None:
            db.set_setting("timezone", body.timezone.strip()[:64])
        if body.theme is not None:
            db.set_setting("theme", normalize_theme(body.theme.strip()[:32]))
        if body.weather_auto is not None:
            db.set_setting("weather_auto", bool(body.weather_auto))
        if body.view_full_day is not None:
            db.set_setting("view_full_day", bool(body.view_full_day))
        if body.min_vis is not None:
            db.set_setting("min_vis", max(0, min(100, int(body.min_vis))))
        return {"ok": True}

    # ---------------- sites ----------------

    @app.get("/api/sites")
    def api_sites():
        return db.sites()

    @app.post("/api/sites")
    def api_add_site(body: SiteIn):
        _check_site(body.lat, body.lon, body.elev)
        sid = db.add_site(body.name.strip(), body.lat, body.lon, body.elev)
        if db.setting("active_site_id") is None:
            db.set_setting("active_site_id", sid)
        return {"id": sid}

    @app.put("/api/sites/{sid}")
    def api_update_site(sid: int, body: SiteIn):
        _check_site(body.lat, body.lon, body.elev)
        db.update_site(_int_id(sid), body.name.strip(), body.lat, body.lon,
                       body.elev)
        return {"ok": True}

    @app.delete("/api/sites/{sid}")
    def api_delete_site(sid: int):
        db.delete_site(_int_id(sid))
        return {"ok": True}

    # ---------------- rigs ----------------

    @app.post("/api/rigs")
    def api_add_rig(body: RigIn):
        _finite(body.focal_mm, "Focal length")
        _finite(body.sensor_w, "Sensor width")
        _finite(body.sensor_h, "Sensor height")
        if body.focal_mm <= 0 or body.sensor_w <= 0 or body.sensor_h <= 0:
            raise HTTPException(400, "Focal length and sensor size must be positive")
        fov_w = fov_arcmin(body.focal_mm, body.sensor_w)
        fov_h = fov_arcmin(body.focal_mm, body.sensor_h)
        rid = db.add_rig(body.name.strip(), body.focal_mm, body.sensor_w,
                         body.sensor_h, fov_w, fov_h)
        if db.setting("active_rig_id") is None:
            db.set_setting("active_rig_id", rid)
        return {"id": rid, "fov_w": fov_w, "fov_h": fov_h}

    @app.delete("/api/rigs/{rid}")
    def api_delete_rig(rid: int):
        db.delete_rig(_int_id(rid))
        return {"ok": True}

    # ---------------- catalog + images (IO) ----------------

    @app.get("/api/catalog")
    def api_catalog(adv: bool = False):
        return [{k: v for k, v in o.items() if k != "_adv"}
                for o in db.all_objects(advanced=adv)]

    @app.get("/api/catalog/provenance")
    def api_catalog_provenance():
        """Return the generated, read-only catalog provenance sidecar.

        The planning catalog deliberately keeps its compact row schema; this
        endpoint exposes source versions, aliases and designation history for
        audit/export without making that metadata part of every planning
        request.  A missing or malformed shipped sidecar is an installation
        error, not an empty catalog, so fail explicitly instead of returning a
        plausible but incomplete provenance document.
        """
        path = resource_dir() / "app" / "data" / "catalog_provenance.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise HTTPException(503, "Catalog provenance is not installed")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HTTPException(503, "Catalog provenance is unavailable") from exc
        if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                or not isinstance(payload.get("objects"), dict)):
            raise HTTPException(503, "Catalog provenance is invalid")
        return payload

    @app.get("/api/object/{oid}/image")
    def api_object_image(oid: str, thumb: bool = False):
        if catalog_only:
            raise HTTPException(404, "object images are unavailable in catalog-only mode")
        from .images import fetch_image, fetch_image_cached
        obj = db.get_object(oid)
        if obj is None:
            raise HTTPException(404)
        # thumbnails (targets table) only use the on-disk cache so a page of
        # 250 rows never triggers a Wikipedia/DSS lookup each; the detail
        # view (thumb=0) still runs the full fetch chain.
        data, ctype = fetch_image_cached(obj) if thumb else fetch_image(obj)
        if data is not None:
            return Response(content=data, media_type=ctype,
                            headers={"Cache-Control":
                                     "public, max-age=604800, immutable"})
        # graceful offline/missing fallback: generated type-colored placeholder
        svg = placeholder_svg(obj)
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=600"})

    # ---------------- panoramas ----------------

    @app.post("/api/images/cache/clear")
    def api_clear_image_cache():
        """SP-10: the explicit "clear cached images" action.

        `app/images.py` bounds the cache automatically (byte cap + age cap,
        enforced on write); this is the user-triggered path. POST because it
        changes state - and it must not be a GET, which any page the user
        visits can reach with `<img src=...>` (the same class of issue as
        /api/shutdown, SP-03).
        """
        from .images import clear_cache
        return {"ok": True, "freed": clear_cache()}

    def _pano_or_404(pid: str) -> dict:
        """Panorama row for an id that is safe to use as a filename component.

        `POST /api/panorama/{pid}/image` builds `f"{pid}.{ext}"` and writes it
        under data/panos, so an id that is not a plain token must be treated as
        "not found" rather than reaching the filesystem (a crafted setup ZIP
        could plant `..\\evil`)."""
        if safe_token(pid) is None:
            raise HTTPException(404)
        row = db.get_panorama(pid)
        if row is None:
            raise HTTPException(404)
        return row

    @app.post("/api/panorama")
    async def api_upload_pano(file: UploadFile = File(...)):
        ext = Path(file.filename or "").suffix.lower().lstrip(".")
        if ext not in ("jpg", "jpeg", "png", "webp"):
            raise HTTPException(400, "Use JPG, PNG or WebP")
        pid = new_id()
        fname = f"{pid}.{ext}"
        out = data_dir() / "panos" / fname
        tmp = out.with_name(f".{pid}.{uuid.uuid4().hex}.upload.tmp")

        def publish():
            with db.archive_lock:
                tmp.replace(out)
                name = Path(file.filename).stem or "Panorama"
                db.add_panorama(pid, name, fname)
                db.update_panorama(pid, active=False)

        try:
            await _drain_upload(file, tmp, 60 * 1024 * 1024, "Image")
            await _archive_io(publish)
        finally:
            await _archive_io(lambda: tmp.unlink(missing_ok=True))
        return {"id": pid}

    @app.get("/api/panoramas")
    def api_panoramas():
        return db.panoramas()

    @app.get("/api/panorama/{pid}/image")
    @serialize_archive
    def api_pano_image(pid: str):
        row = _pano_or_404(pid)
        # the stored name is untrusted data (a setup ZIP can plant it): it must
        # resolve INSIDE data/panos, never to an arbitrary file
        f = safe_join(data_dir() / "panos", row.get("file"))
        if f is None:
            raise HTTPException(404)
        if not f.exists():
            # self-heal: db may point at a renamed/deleted variant
            candidates = sorted((data_dir() / "panos").glob(f"{pid}.*")) \
                if _PID_RE.match(pid) else []
            if candidates:
                f = candidates[0]
                db.update_panorama(pid, file=f.name)
            else:
                raise HTTPException(404)
        return FileResponse(f)

    @app.post("/api/panorama/{pid}/image")
    async def api_pano_replace_image(pid: str, file: UploadFile = File(...),
                                     az_offset: Optional[float] = Form(None),
                                     fov: Optional[float] = Form(None)):
        """Replace the stored panorama image (used by the crop tool).
        Optionally applies the recalibrated az_offset / fov in one step."""
        _pano_or_404(pid)
        ext = Path(file.filename or "").suffix.lower().lstrip(".")
        if ext not in ("jpg", "jpeg", "png", "webp"):
            raise HTTPException(400, "Use JPG, PNG or WebP")
        fname = f"{pid}.{ext}"
        # per-request temp name: two concurrent replaces for the same panorama
        # must not interleave writes on one shared file
        tmp = data_dir() / "panos" / f".{pid}.{uuid.uuid4().hex}.upload.tmp"
        # a cropped/replaced image invalidates the whole painted mask: the
        # sky-mode profile_top would otherwise keep blocking above the old band
        fields: dict = {"file": fname, "strokes": [], "profile": [],
                        "profile_top": [], "paint_mode": "obstacle",
                        "block_above": False}
        if az_offset is not None:
            fields["az_offset"] = _finite(az_offset, "az_offset")
        if fov is not None:
            fields["fov"] = _finite(fov, "fov")
        def publish():
            with db.archive_lock:
                row = _pano_or_404(pid)  # re-read after any concurrent restore
                out = data_dir() / "panos" / fname
                old = safe_join(data_dir() / "panos", row.get("file"))
                tmp.replace(out)
                if old is not None and old.exists() and old.name != fname:
                    old.unlink(missing_ok=True)
                db.update_panorama(pid, **fields)

        try:
            await _drain_upload(file, tmp, 60 * 1024 * 1024, "Image")
            await _archive_io(publish)
        finally:
            await _archive_io(lambda: tmp.unlink(missing_ok=True))
        return {"ok": True}

    @app.put("/api/panorama/{pid}")
    @serialize_archive
    def api_update_pano(pid: str, body: PanoramaUpdate):
        row = _pano_or_404(pid)
        if body.paint_mode is not None and \
                body.paint_mode not in ("obstacle", "sky"):
            raise HTTPException(400, "paint_mode must be 'obstacle' or 'sky'")
        _check_profile(body.profile, "profile")
        _check_profile(body.profile_top, "profile_top")
        _check_strokes(body.strokes)
        _check_horizon_metadata(body.landmarks, body.provenance, body.confidence)
        for label in ("az_offset", "alt_bottom", "alt_top", "fov"):
            value = getattr(body, label)
            if value is not None:
                _finite(value, label)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        fields.pop("active", None)      # activation is handled atomically below
        if "landmarks" in fields:
            fields["landmarks"] = normalize_horizon_landmarks(fields["landmarks"])
        if "provenance" in fields:
            fields["provenance"] = normalize_horizon_provenance(fields["provenance"])
        if fields:
            db.update_panorama(pid, **fields)
        # explicit active flag: True makes THIS pano the only active one; False
        # clears the global pointer. None = not mentioned.
        if body.active is not None:
            db.set_active_panorama(pid if body.active else None)
        return {"ok": True}

    @app.delete("/api/panorama/{pid}")
    @serialize_archive
    def api_delete_pano(pid: str):
        # deleting is idempotent (200 for an unknown id), but the id must be a
        # plain token before it is used to build anything path-like
        row = db.get_panorama(pid) if safe_token(pid) else None
        if row:
            f = safe_join(data_dir() / "panos", row.get("file"))
            if f is not None and f.exists():
                f.unlink(missing_ok=True)
        db.delete_panorama(pid)
        return {"ok": True}

    # ---------------- setup export / import ----------------

    @app.get("/api/export")
    def api_export():
        """Stream the setup ZIP from a spool file next to the database.

        The image library can be many GB, so the archive is never assembled in
        RAM: it is written to disk and streamed, then removed in the background
        (the import side already works this way)."""
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        spool = db.path.parent / f".export-{uuid.uuid4().hex}.zip"
        try:
            with open(spool, "wb") as fh:
                _export_zip_to(db, fh)
        except Exception as exc:  # noqa: BLE001
            spool.unlink(missing_ok=True)
            raise HTTPException(500, f"Export failed: {exc}") from None
        return FileResponse(
            spool, media_type="application/zip",
            filename=f"nightseam-setup-{stamp}.zip",
            background=BackgroundTask(spool.unlink, missing_ok=True))

    @app.post("/api/import")
    async def api_import(file: UploadFile = File(...)):
        # backup zips embed the whole image library, so allow a generous cap.
        # Spool to a temp file next to the db so the blob never lives in RAM
        # (apply_import_zip accepts a Path).
        spool = db.path.parent / f".import-{uuid.uuid4().hex}.zip"
        # PY-03: `apply_import_zip` fills this summary in place, so the route can
        # tell "the bundle was rejected" (nothing was replaced) from "the restore
        # failed AFTER the library was replaced" - a plain 400 must never be read
        # as the former when it is the latter.
        summary: dict = {}
        try:
            await _drain_upload(file, spool, 2 * 1024 * 1024 * 1024,
                                "Backup zip")
            try:
                summary = await _archive_io(apply_import_zip, db, spool, summary)
            except ValueError as exc:
                raise HTTPException(400, _restore_error(exc, summary)) from None
            except OSError as exc:      # e.g. disk full while restoring
                raise HTTPException(
                    500, _restore_error(
                        f"Failed to write restored files: {exc}", summary)
                ) from None
        finally:
            spool.unlink(missing_ok=True)
        return summary

    # ---------------- custom targets ----------------

    @app.post("/api/catalog/custom")
    def api_add_custom(body: CustomIn):
        ra = parse_ra_hours(body.ra)
        dec = parse_dec_deg(body.dec)
        if ra is None or not (0 <= ra < 24):
            raise HTTPException(400, "RA must be 0-24 h (decimal or '5h 34.5m')")
        if dec is None or not (-90 <= dec <= 90):
            raise HTTPException(400, "Dec must be -90..+90 (decimal or '+22 01')")
        # T6-F1 (review of t6): the create route was the last unguarded door to
        # the certified PY-04 failure mode - a non-finite vmag was stored
        # verbatim and every later GET /api/catalog answered 500. This is the
        # boundary that yields the clean 400 (see the CustomIn note above for
        # why a pydantic allow_inf_nan=False is NOT used).
        vmag = _finite(body.vmag, "Magnitude") if body.vmag is not None else None
        oid = "u_" + new_id()
        db.add_custom({
            "id": oid,
            "primary_name": body.name.strip() or "Unnamed target",
            "otype": body.otype if body.otype in (
                "galaxy", "globular", "open_cluster", "nebula", "planetary",
                "supernova_remnant", "star_cloud", "asterism") else "galaxy",
            "ra_hours": round(ra, 5),
            "dec_deg": round(dec, 5),
            "vmag": vmag,
            "size_am": body.size_am,
            "constellation": body.constellation.upper()[:3],
            "blurb": body.blurb,
        })
        return {"id": oid}

    @app.delete("/api/catalog/custom/{cid}")
    def api_delete_custom(cid: str):
        if not cid.startswith("u_") or not db.is_custom(cid):
            raise HTTPException(400, "Only custom targets can be deleted")
        db.delete_custom(cid)
        return {"ok": True}

    # ---------------- image archive ----------------

    @app.post("/api/library/reindex")
    @serialize_archive
    def api_library_reindex():
        """Scan the archive folder and record files not yet in the DB."""
        try:
            return reindex_library(db, Path(db.image_dir()))
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
        catalog = db.all_objects(advanced=True)
        resolver = CatalogResolver(catalog)
        sess = session.strip()
        if not _SESSION_NAME_RE.match(sess):
            sess = None                  # not a sane folder name -> auto session
        run = run_id.strip()
        if not run or not _RUN_ID_RE.match(run):
            run = uuid.uuid4().hex  # one private grouping key for this request

        def record_import(r, current_archive):
            # Called under finalization's archive lock: restore cannot replace
            # a just-imported row before its history is committed.
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

        results = [await ingest_one(db, catalog, archive, f, frame, sess,
                                    resolver=resolver, on_import=record_import,
                                    enforce_archive_root=True, session_target=target)
                   for f in files]
        return {"results": results}

    @app.get("/api/library/imports")
    def api_library_imports():
        return db.import_batches()

    @app.post("/api/library/open_folder")
    def api_library_open_folder(body: FolderOpenIn):
        rel = body.rel.strip().replace("\\", "/").strip("/")
        if not rel or any(p == ".." for p in rel.split("/")):
            raise HTTPException(400, "bad folder path")
        base = Path(db.image_dir()).resolve()
        folder = (base / rel).resolve()
        if folder != base and base not in folder.parents:
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
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name required")
        new_name = (body.new_name or "").strip() or name
        otype = body.otype or None
        if otype is not None and otype not in (
                "galaxy", "globular", "open_cluster", "nebula", "planetary",
                "supernova_remnant", "star_cloud", "asterism"):
            raise HTTPException(400, f"unknown type: {otype}")
        rows = [i for i in db.images()
                if not i["object_id"]
                and name_key(i["object_name"]) == name_key(name)]
        if not rows:
            raise HTTPException(404, "Object not found in library")
        # PY-01: the move returns the REAL destination of every frame. A rename
        # into a folder that already holds a same-named frame used to `replace`
        # it (two rows aliasing one file, the replaced pixels gone); the frame is
        # uniquified now and the stored path is updated to where it really is.
        moved = rename_archive_folder(Path(db.image_dir()), name, new_name)
        if moved is None:
            # the folder could not be moved (files open in another program):
            # leave the stored paths alone rather than pointing them at a
            # folder that does not exist
            raise HTTPException(
                500, "Could not move the archive folder - close any program "
                     "using those files and try again")
        db.rename_images(name, new_name, otype, obj_slug(new_name), moved)
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
            w = csvmod.writer(buf)
            w.writerow(["id", "file", "orig_name", "object_id", "object_name",
                        "otype", "ra_hours", "dec_deg", "exposure", "date_obs",
                        "filter", "telescope", "imagetyp", "ingested_at"])
            for i in imgs:
                w.writerow([i.get("id"), i.get("file"), i.get("orig_name"),
                            i.get("object_id"), i.get("object_name"),
                            i.get("otype"), i.get("ra_hours"),
                            i.get("dec_deg"), i.get("exposure"),
                            i.get("date_obs"), i.get("filter"),
                            i.get("telescope"), i.get("imagetyp"),
                            i.get("ingested_at")])
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

    @app.get("/api/images/{img_id}/preview")
    def api_image_preview(img_id: int):
        from . import fits as fitsio
        row = db.image(_int_id(img_id))
        if row is None:
            raise HTTPException(404, "image not found")
        # same rule as the panorama route: a stored path must stay inside the
        # archive folder (imports can plant arbitrary values in `file`)
        p = safe_join(db.image_dir(), row.get("file"))
        if p is None or not p.is_file():
            raise HTTPException(404, "file missing on disk")
        try:
            with open(p, "rb") as f:
                png = fitsio.preview_png_file(f)
        except OSError:
            raise HTTPException(500, "cannot read archive file")
        if png is None:
            raise HTTPException(404, "not an image HDU")
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control":
                                 "public, max-age=604800, immutable"})

    @app.post("/api/update-check")
    def api_update_check():
        """Old clients cannot compare catalog releases to combined web versions."""
        raise HTTPException(
            404, "updates are unavailable until a catalog release source is configured")

    @app.get("/api/shutdown")
    def api_shutdown_get():
        """SP-03: GET must not shut the app down.

        The route used to answer GET as well, which made it reachable from any
        page the user visits (`<img src="http://127.0.0.1:8407/api/shutdown">`
        carries neither `Sec-Fetch-Site` nor `Origin` - the case the anti
        cross-site guard has to fail open on). Answer 405 with the allowed
        method instead of letting it fall through to the static mount's 404."""
        raise HTTPException(405, "Use POST /api/shutdown",
                            headers={"Allow": "POST"})

    @app.post("/api/shutdown")
    def api_shutdown():
        """Ask the app to exit.

        POST only (SP-03): it changes state, and a GET is reachable from ANY
        page the user visits with `<img src="http://127.0.0.1:8407/api/shutdown">`
        - a request that carries neither `Sec-Fetch-Site` nor `Origin`, which is
        exactly the case the anti-cross-site guard has to fail open on (a
        non-browser client sends neither). The UI (and the idle-exit probe) use
        an explicit POST, which the guard can classify. Cross-site callers are
        still rejected by the middleware. The flag lets main.py's loop shut down
        gracefully - closing the server and clearing data/instance.json; the
        timer is the fallback for native-window mode, where nothing polls the
        flag."""
        _state["shutdown"] = True

        def _hard_exit():
            try:
                (data_dir() / "instance.json").unlink(missing_ok=True)
            except OSError:
                pass
            os._exit(0)

        threading.Timer(1.5, _hard_exit).start()
        return {"ok": True}

    # ---------------- static files ----------------

    sd = static_dir()

    @app.get("/")
    def index():
        # The packaged executable starts at this archive-only shell. Source
        # runs keep the full planner shell unless main.py selects this mode.
        filename = "catalog.html" if catalog_only else "index.html"
        return FileResponse(sd / filename)

    @app.get("/index.html")
    def planner_index():
        # The planner shell stays available to source/maintenance launches,
        # but the packaged catalog-only server must not expose it by URL.
        if catalog_only:
            raise HTTPException(404, "planner shell is unavailable in catalog-only mode")
        return FileResponse(sd / "index.html")

    @app.get("/catalog.html")
    def catalog_index():
        return FileResponse(sd / "catalog.html")

    app.mount("/", StaticFiles(directory=str(sd)), name="static")

    if prefetch and not catalog_only:
        start_image_prefetch(db)

    return app


# ------------------------------------------------------------------ formatting

TYPE_COLORS = {
    "galaxy": "#ff9e64", "globular": "#ffd166", "open_cluster": "#7ee787",
    "nebula": "#ff7b72", "planetary": "#79c0ff",
    "supernova_remnant": "#d2a8ff", "star_cloud": "#e3b341",
    "asterism": "#9ca5b5",
}


def placeholder_svg(obj: dict) -> bytes:
    """Deterministic offline placeholder: starfield-ish gradient + designation."""
    color = TYPE_COLORS.get(obj.get("otype"), "#79c0ff")
    name = obj.get("messier") or obj.get("ngcic") or obj.get("primary_name") or "?"
    import html as _html
    name = _html.escape(str(name).split("/")[0])
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='400' height='400' viewBox='0 0 400 400'>
<defs><radialGradient id='g' cx='35%' cy='30%' r='90%'>
<stop offset='0%' stop-color='#1c2740'/><stop offset='100%' stop-color='#0a0d16'/>
</radialGradient></defs>
<rect width='400' height='400' fill='url(#g)'/>
<g fill='#ffffff'>
<circle cx='60' cy='70' r='1.3' opacity='.5'/><circle cx='330' cy='50' r='1.1' opacity='.4'/>
<circle cx='120' cy='320' r='1.2' opacity='.45'/><circle cx='300' cy='280' r='1.5' opacity='.55'/>
<circle cx='210' cy='90' r='1.0' opacity='.35'/><circle cx='80' cy='200' r='1.0' opacity='.4'/>
<circle cx='350' cy='190' r='1.2' opacity='.4'/><circle cx='180' cy='360' r='1.1' opacity='.35'/>
</g>
<circle cx='200' cy='185' r='46' fill='none' stroke='{color}' stroke-width='2.5' stroke-dasharray='6 5' opacity='.85'/>
<circle cx='200' cy='185' r='10' fill='{color}' opacity='.9'/>
<text x='200' y='268' text-anchor='middle' font-family='Segoe UI, sans-serif' font-size='26'
 font-weight='600' fill='#d7dce8'>{name}</text>
<text x='200' y='294' text-anchor='middle' font-family='Segoe UI, sans-serif' font-size='13'
 fill='#8b93a8'>image unavailable offline</text></svg>"""
    return svg.encode("utf-8")
