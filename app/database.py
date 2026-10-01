"""SQLite storage: seeded DSO catalog + user data (sites, rigs, panoramas, settings)."""

from __future__ import annotations
from contextlib import nullcontext

import csv
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path

from . import catalog_data
from .paths import resource_dir, safe_rel, safe_token
from .theme import normalize_theme

# Panorama obstruction arrays are exactly 720 numbers (0.5-degree world-azimuth
# bins) or empty. This is the storage-format rule shared by the PUT route and
# the import boundary (core/horizon.js owns the math).
PROFILE_BINS = 720
CONFIDENCE_BINS = PROFILE_BINS
_HORIZON_LANDMARK_KINDS = {
    "tree", "building", "ridge", "pole", "roof", "balcony", "other",
}
_HORIZON_PROVENANCE_SOURCES = {
    "manual", "compass", "phone_metadata", "plate_solve", "imported",
    "unknown",
}


def normalize_horizon_landmarks(value) -> list[dict]:
    """Return the bounded, HTML-safe annotation shape used by both shells.

    Landmarks are descriptive metadata, not an input to the planner. The
    storage boundary still normalizes them because names/notes are rendered by
    the editors and imported JSON is untrusted. Invalid entries are dropped;
    the whole panorama remains usable when one annotation is malformed.
    """
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(value[:128]):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:96]
        try:
            az = float(item.get("azimuth", item.get("az")))
            alt = float(item.get("altitude", item.get("alt")))
        except (TypeError, ValueError):
            continue
        if not name or not math.isfinite(az) or not math.isfinite(alt):
            continue
        raw_id = str(item.get("id") or "").strip()[:64]
        ident = raw_id if safe_token(raw_id) == raw_id else f"landmark-{i + 1}"
        while ident in seen:
            ident = f"{ident[:55]}-{i + 1}"
        seen.add(ident)
        az = az % 360
        alt = max(-90, min(90, alt))
        raw_kind = item.get("kind")
        kind = (raw_kind if isinstance(raw_kind, str)
                and raw_kind in _HORIZON_LANDMARK_KINDS else "other")
        out.append({
            "id": ident,
            "name": name,
            "kind": kind,
            "azimuth": round(az, 2),
            "altitude": round(alt, 2),
            "notes": str(item.get("notes") or "").strip()[:320],
        })
    return out


def normalize_horizon_provenance(value) -> dict:
    """Normalize calibration provenance while preserving old empty records."""
    item = value if isinstance(value, dict) else {}
    raw_source = item.get("source")
    source = (raw_source if isinstance(raw_source, str)
              and raw_source in _HORIZON_PROVENANCE_SOURCES else "unknown")
    return {
        "source": source,
        "capturedAt": str(item.get("capturedAt", item.get("captured_at")) or "").strip()[:40],
        "orientation": str(item.get("orientation") or "").strip()[:160],
        "operator": str(item.get("operator") or "").strip()[:96],
        "notes": str(item.get("notes") or "").strip()[:320],
    }


def confidence_values_ok(values) -> bool:
    """True for [] or one finite confidence value per world profile bin."""
    if not isinstance(values, list) or len(values) not in (0, CONFIDENCE_BINS):
        return False
    return all(v is None or (
        not isinstance(v, bool) and isinstance(v, (int, float))
        and math.isfinite(float(v)) and 0 <= float(v) <= 1
    ) for v in values)


def normalize_horizon_confidence(values) -> list:
    """Always expose a 720-entry confidence vector to API consumers."""
    if not isinstance(values, list) or len(values) not in (0, CONFIDENCE_BINS):
        return [None] * CONFIDENCE_BINS
    return [round(float(v), 3) if v is not None else None for v in values]


def _validate_import_landmarks(value) -> None:
    if value is None:
        return
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("panorama landmarks must be a list of at most 128 objects")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("panorama landmarks must contain objects")
        if not str(item.get("name") or "").strip():
            raise ValueError("panorama landmark names must not be empty")
        for key in ("azimuth", "altitude"):
            alt_key = "az" if key == "azimuth" else "alt"
            v = item.get(key, item.get(alt_key))
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(float(v)):
                raise ValueError(f"panorama landmark {key} must be finite")
        kind = item.get("kind")
        if kind is not None and (
                not isinstance(kind, str) or kind not in _HORIZON_LANDMARK_KINDS):
            raise ValueError("panorama landmark kind is not recognized")


def _validate_import_provenance(value) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("panorama provenance must be an object")
    source = value.get("source")
    if source is not None and (
            not isinstance(source, str) or source not in _HORIZON_PROVENANCE_SOURCES):
        raise ValueError("panorama provenance source is not recognized")


def _validate_import_confidence(value) -> None:
    if value is None:
        return
    if not confidence_values_ok(value):
        raise ValueError(
            f"panorama confidence must be empty or exactly {CONFIDENCE_BINS} "
            "finite values between 0 and 1")


def profile_values_ok(values) -> bool:
    """True for the canonical panorama array: [] or exactly PROFILE_BINS finite
    non-bool numbers. A profile that violates this used to be stored verbatim
    and then failed OPEN at planning time - the exact invariant `_check_profile`
    exists to protect."""
    if not isinstance(values, list) or len(values) not in (0, PROFILE_BINS):
        return False
    return all(not isinstance(v, bool) and isinstance(v, (int, float))
               and math.isfinite(float(v)) for v in values)


def _validate_import_profile(values, label: str) -> None:
    """Fail-closed import check; `None` counts as the empty profile."""
    if values is None:
        return
    if not profile_values_ok(values):
        raise ValueError(
            f"panorama {label} must be empty or exactly {PROFILE_BINS} finite "
            "numbers")


def _validate_import_strokes(values) -> None:
    """PY-03: `strokes` is the THIRD stored panorama array and had no shape check.

    It holds the painted mask and BOTH editors iterate it (`for (const s of
    p.strokes)`), so any JSON-encodable value was accepted - `{}`, `"abc"`, `7` -
    and the desktop editor then threw `TypeError: ... is not iterable` for that
    record. Fail closed at the boundary like the two profile arrays; `None`
    counts as the empty stroke list.
    """
    if values is None:
        return
    if not isinstance(values, list) or not all(isinstance(s, dict) for s in values):
        raise ValueError("panorama strokes must be a list of stroke objects")


def _reject_duplicate_paths(records, label: str) -> None:
    """PY-01: two bundle records must not normalize to ONE stored path.

    `safe_rel` canonicalizes `a/./b`, `a//b` and `a/x/../b` to the same string, so
    distinct records could share a target: the restore loop then wrote both
    members to one file (ZIP order, last writer wins) while `import_bundle` had
    already inserted both rows, and the summary counted both - a quiet frame loss.
    No NightSeam export can produce that, so a bundle that does is refused instead.
    """
    seen: set[str] = set()
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        rel = safe_rel(rec.get("file"))
        if rel is None:
            continue            # unsafe path: dropped later, not a duplicate
        if rel in seen:
            raise ValueError(
                f"imported {label} contain two records for the same stored path "
                f"({rel}); refusing to let one overwrite the other")
        seen.add(rel)


# Numeric image/custom columns that are serialized into JSON responses with
# `allow_nan=False`; ONE stored non-finite value makes the route 500 forever.
_NUMERIC_RECORD_FIELDS = {
    "images": ("ra_hours", "dec_deg", "exposure"),
    "customs": ("ra_hours", "dec_deg", "vmag"),
}


def finite_or_none(value):
    """The storage boundary never accepts a non-finite number (PY04-F2).

    `images.ra_hours/dec_deg/exposure` are read back into `/api/library`, which
    Starlette serializes with `allow_nan=False` - so a stored `inf`/`nan` makes
    the route answer 500 on every later request, and re-importing the user's own
    export could re-introduce it. A non-finite float carries no usable
    information; store NULL, exactly how the FITS parsers represent an
    unparseable card. Non-float values are passed through unchanged, so ints,
    strings and `None` keep their existing behaviour."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


_BOOL_SETTINGS = ("moon_aware", "adv_mode", "weather_auto", "view_full_day",
                  "wizard_done")
_STR_SETTINGS = ("timezone", "theme")
_SIZE_UNITS = ("arcmin", "arcsec", "deg")

# Settings stored as a number but read back through an int()/float() coercion.
# The importer refuses a value it cannot coerce (fail closed) and `/api/state`
# additionally clamps/falls back (defence in depth for a row that got in some
# other way) - PY-02.
_NUM_SETTINGS = (("bortle", 1, 9), ("edge_space", 0, 1000), ("min_vis", 0, 100))


def _finalize_num_setting(value, lo, hi):
    """A finite int in [lo, hi], or None when the stored value is unusable."""
    if value is None or isinstance(value, bool) \
            or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return max(lo, min(hi, int(value)))


def _validate_import_settings(settings: dict) -> None:
    """Validate the settings shapes that server code reads.

    Imported settings were stored verbatim, so a hostile/broken bundle could
    put `bortle: null` into the table and 500 `/api/state` at every boot
    (`float(db.setting("bortle"))`). Unknown keys pass through unchanged
    (older bundles carry retired flags like `planning_client_compute`)."""
    for key in _BOOL_SETTINGS:
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError(f"settings.{key} must be a boolean")
    for key in _STR_SETTINGS:
        if key in settings and not isinstance(settings[key], str):
            raise ValueError(f"settings.{key} must be a string")
    if "active_pano_id" in settings and settings["active_pano_id"] is not None \
            and not isinstance(settings["active_pano_id"], str):
        raise ValueError("settings.active_pano_id must be a string or null")
    # PY-05: the same token rule the routes apply. Without it a bundle could
    # install an "active panorama" that /api/state advertises and no id-taking
    # route ever resolves.
    _pid = settings.get("active_pano_id")
    if _pid and safe_token(_pid) is None:
        raise ValueError(
            "settings.active_pano_id must be a plain panorama id")
    # PY-02: `min_vis` is the FOURTH int-coerced setting and had no check at
    # all, so `"min_vis": "abc"` imported cleanly and then made every
    # `/api/state` answer 500 (`int(db.setting("min_vis", 1))`) - the desktop
    # awaits that route before rendering anything, so one crafted bundle
    # bricked it at every boot. Same rule as bortle/edge_space.
    for key, lo, hi in _NUM_SETTINGS:
        if key in settings:
            v = settings[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(float(v)):
                raise ValueError(f"settings.{key} must be a finite number")
            settings[key] = max(lo, min(hi, int(v)))
    if "min_useful_alt" in settings and settings["min_useful_alt"] is not None:
        v = settings["min_useful_alt"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            raise ValueError("settings.min_useful_alt must be a finite number")
    if "size_unit" in settings and settings["size_unit"] not in _SIZE_UNITS:
        raise ValueError("settings.size_unit must be arcmin, arcsec or deg")
    # Sky-brightness providers were retired. Older setup bundles may still
    # carry this key, including values that are no longer recognized; discard
    # it before validation so a valid backup remains restorable.
    settings.pop("sky_brightness_provider", None)
    if "filters" in settings and not isinstance(settings["filters"], dict):
        raise ValueError("settings.filters must be an object")
    if "favorites" in settings and not isinstance(settings["favorites"], list):
        raise ValueError("settings.favorites must be a list")
    for key in ("active_site_id", "active_rig_id"):
        if key in settings and settings[key] is not None \
                and (isinstance(settings[key], bool)
                     or not isinstance(settings[key], int)):
            raise ValueError(f"settings.{key} must be an integer or null")

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id TEXT PRIMARY KEY,
    primary_name TEXT,
    messier TEXT, ngcic TEXT, other TEXT,
    otype TEXT,
    ra_hours REAL, dec_deg REAL,
    vmag REAL, size_am TEXT,
    constellation TEXT,
    wiki TEXT,
    blurb TEXT,
    search TEXT
);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    elev INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rigs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    focal_mm REAL NOT NULL,
    sensor_w REAL NOT NULL,
    sensor_h REAL NOT NULL,
    fov_w REAL NOT NULL,
    fov_h REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_objects (
    id TEXT PRIMARY KEY,
    primary_name TEXT NOT NULL,
    otype TEXT NOT NULL DEFAULT 'galaxy',
    ra_hours REAL NOT NULL,
    dec_deg REAL NOT NULL,
    vmag REAL,
    size_am TEXT DEFAULT '',
    constellation TEXT DEFAULT '',
    blurb TEXT DEFAULT '',
    search TEXT
);
CREATE TABLE IF NOT EXISTS panoramas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    file TEXT NOT NULL,
    az_offset REAL NOT NULL DEFAULT 0,
    alt_bottom REAL NOT NULL DEFAULT 0,
    alt_top REAL NOT NULL DEFAULT 60,
    strokes TEXT NOT NULL DEFAULT '[]',
    profile TEXT NOT NULL DEFAULT '[]',
    profile_top TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    paint_mode TEXT NOT NULL DEFAULT 'obstacle',
    block_above INTEGER NOT NULL DEFAULT 0,
    landmarks TEXT NOT NULL DEFAULT '[]',
    provenance TEXT NOT NULL DEFAULT '{}',
    confidence TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL,
    orig_name TEXT NOT NULL,
    object_id TEXT,
    object_name TEXT NOT NULL,
    otype TEXT,
    ra_hours REAL,
    dec_deg REAL,
    exposure REAL,
    date_obs TEXT,
    session_date_obs TEXT,
    filter TEXT,
    telescope TEXT,
    imagetyp TEXT,
    content_sha256 TEXT,
    file_size INTEGER,
    file_mtime_ns INTEGER,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_object_id_id
    ON images(object_id, id);
CREATE INDEX IF NOT EXISTS idx_images_object_name_id
    ON images(object_name, id);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT NOT NULL,
    date_obs TEXT,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    object_id TEXT,
    object_name TEXT NOT NULL,
    slug TEXT NOT NULL,
    session TEXT NOT NULL,
    frame TEXT NOT NULL,
    n_files INTEGER NOT NULL,
    total_exposure REAL NOT NULL DEFAULT 0,
    first_obs TEXT,
    image_dir TEXT NOT NULL,
    run_id TEXT
);
CREATE TABLE IF NOT EXISTS advanced_objects (
    id TEXT PRIMARY KEY,
    primary_name TEXT,
    messier TEXT, ngcic TEXT, other TEXT,
    otype TEXT,
    ra_hours REAL, dec_deg REAL,
    vmag REAL, size_am TEXT,
    constellation TEXT,
    wiki TEXT,
    blurb TEXT,
    search TEXT,
    popularity INTEGER DEFAULT 0
);
"""


def _accepted_records(panoramas: list, images: list) -> dict:
    """The records `import_bundle` will really store, with their canonical path.

    ONE predicate, used by the insert loops below AND by `app/backup.py` to
    decide which payload files to write (PY-06, SP-05): the restore used to
    iterate the RAW bundle list, so a record whose id failed `safe_token` never
    became a row but still had its payload written - an orphan file in
    `data/panos`, never cleaned, reported as `pano_images: 1` beside
    `panoramas: 0`.
    """
    acc_panos = []
    for p in panoramas:
        rel = safe_rel(p.get("file"))
        # the ID is interpolated into a filename by the crop route, so an id
        # that could act as a path is dropped too
        pid_ok = safe_token(p.get("id"))
        if rel is None or pid_ok is None:
            continue
        acc_panos.append({"id": pid_ok, "file": rel, "raw": p})
    acc_images = []
    for im in images:
        rel = safe_rel(im.get("file"))
        if rel is None:
            continue
        acc_images.append({"file": rel, "raw": im})
    return {"panoramas": acc_panos, "images": acc_images,
            "skipped": {"panoramas": len(panoramas) - len(acc_panos),
                        "images": len(images) - len(acc_images)}}


def _reject_duplicate_custom_ids(customs: list) -> None:
    """PY-07: two bundle targets sharing an id become ONE row (INSERT OR
    REPLACE) while the summary reports both - the silent-loss shape PY-01 was
    fixed for, in its record-id form. No NightSeam export can produce it."""
    ids = [c.get("id") for c in customs]
    if len(set(ids)) != len(ids):
        raise ValueError(
            "imported customs contain two records with the same id; "
            "refusing to let one replace the other")


# Imported custom ids: `u_` marks a user row (the delete/custom routes key off
# it) and the rest must be a plain token, because an imported id is interpolated
# into UI ATTRIBUTES (`data-delcust="${o.id}"` in static/js/setup.js) - an id
# carrying a quote therefore used to become an executable event handler in the
# desktop shell, whose CSP still allowed inline script (BUG-002).
_CUSTOM_PREFIX = "u_"


def custom_id_problem(cid) -> str | None:
    """Why an imported custom id is unusable (None = fine). The ONE rule, shared
    by `prepare_import_bundle` and the insert loop (review PY4 + BUG-002)."""
    if not isinstance(cid, str) or not cid.startswith(_CUSTOM_PREFIX):
        return f"custom target id must start with '{_CUSTOM_PREFIX}' (got {cid!r})"
    if safe_token(cid) != cid:
        return ("custom target id must be a plain token of letters, digits, "
                f"'-' and '_' (got {cid!r})")
    return None


# The archive's frame classes. `imagetyp` is free-form TEXT in the DB and is
# rendered into a `title` attribute by static/js/archive.js, so an imported value
# has to be normalized to one of these before it is stored (BUG-002) - the FITS
# ingest path already normalizes through `ingest.classify_frame`.
_IMAGETYP_CACHE: list = []


def normalize_session_date_obs(value) -> str | None:
    """NULL uses capture time; '' explicitly targets the undated session."""
    if value is None or value == "":
        return value
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
            r"(?:Z|[+-]\d{2}:\d{2})?", value):
        raise ValueError("image session_date_obs must be an ISO timestamp, empty or null")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        raise ValueError("image session_date_obs must be a valid ISO timestamp") from None


def normalize_import_imagetyp(value):
    """An imported `imagetyp` as one of light/dark/flat/light (BUG-002).

    `classify_frame` is the ONE normalizer and it lives in `app.ingest`, which
    imports this module - hence the lazy import (no cycle at module load; the
    call runs once per import, not per request).
    """
    if not _IMAGETYP_CACHE:
        from .ingest import classify_frame
        _IMAGETYP_CACHE.append(classify_frame)
    return _IMAGETYP_CACHE[0](value)


def import_bundle_shape(setup) -> None:
    """Reject any malformed bundle STRUCTURE as a ValueError (BUG-004).

    The restore used to call `.get()` on the decoded top level and on every
    record before proving either was an object, so `panoramas: ["not-an-object"]`
    escaped as an AttributeError -> HTTP 500 (and a scalar top level escaped even
    earlier). A structural failure is malformed input: it must be a controlled
    400, and it must be detected BEFORE the safety copy and before any file is
    written. Raises ValueError; returns None when the shape is usable."""
    if not isinstance(setup, dict):
        raise ValueError(f"malformed setup bundle: expected a JSON object, "
                         f"got {type(setup).__name__}")
    for key in ("sites", "rigs", "panoramas", "customs", "images"):
        records = setup.get(key, [])
        if not isinstance(records, list):
            raise ValueError(f"malformed setup bundle: {key} must be a list of "
                             f"objects, got {type(records).__name__}")
        if not all(isinstance(r, dict) for r in records):
            raise ValueError(f"malformed setup bundle: {key} must be a list of "
                             "objects")
    if "settings" in setup and not isinstance(setup["settings"], dict):
        raise ValueError("malformed setup bundle: settings must be an object")


def prepare_import_bundle(settings: dict, sites: list, rigs: list,
                          panoramas: list, customs: list,
                          images: list) -> dict:
    """Validate a whole bundle BEFORE anything is written, and normalize it.

    Side-effect free apart from the in-place normalization `import_bundle`
    always did (numeric settings clamped to their allowed range, the record
    collections replaced by their ACCEPTED subset). This is the check
    `apply_import_zip` runs before it writes a payload file (BUG-001): anything
    that used to fail halfway through the database transaction now fails here,
    with nothing on disk or in the database touched.

    Raises ValueError for every malformed shape/field; returns the same
    `{"panoramas": [...], "images": [...], "skipped": {...}}` dict
    `_accepted_records` produces."""
    import_bundle_shape({"settings": settings, "sites": sites, "rigs": rigs,
                         "panoramas": panoramas, "customs": customs,
                         "images": images})
    # FIX-4 (review PY1/PY2): fail closed BEFORE any write. A malformed profile
    # would fail open at planning time and a hostile setting could brick
    # /api/state at every boot; the transaction below is never entered.
    _validate_import_settings(settings)
    # ``classic`` (and the empty value used by older desktop builds) is the
    # legacy name for the flat/default look. Store the new canonical name while
    # the bundle is still side-effect free, so the database and export format
    # converge on one default without a separate migration pass.
    if "theme" in settings:
        settings["theme"] = normalize_theme(settings["theme"])
    # PY3 follow-up (adversarial verification): site/rig/pano numbers are read
    # into /api/state, which rejects NaN on serialization - validate them on the
    # import boundary too (None is allowed; JSON null is fine).
    def _fin(label, v):
        if v is None:
            return
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            raise ValueError(f"imported {label} must be a finite number")
    for s in sites:
        for key in ("lat", "lon", "elev"):
            _fin(f"site {key}", s.get(key))
    for r in rigs:
        for key in ("focal_mm", "sensor_w", "sensor_h", "fov_w", "fov_h"):
            _fin(f"rig {key}", r.get(key))
    for p in panoramas:
        for key in ("az_offset", "alt_bottom", "alt_top", "fov"):
            _fin(f"panorama {key}", p.get(key))
        _validate_import_profile(p.get("profile"), "profile")
        _validate_import_profile(p.get("profile_top"), "profile_top")
        _validate_import_landmarks(p.get("landmarks"))
        _validate_import_provenance(p.get("provenance"))
        _validate_import_confidence(p.get("confidence"))
        # PY-03: the third stored array, iterated by both editors
        _validate_import_strokes(p.get("strokes"))
    # PY04-F2 (review round 2): the image rows were the ONE record type the
    # boundary did not check, and `json.loads` happily produces the bare tokens
    # Infinity/NaN. `images[]` is read back by /api/library, so a crafted - or
    # self-exported - bundle could store `inf` and 500 that route forever. Fail
    # closed BEFORE the transaction, like the records above; the custom target
    # numbers get the same treatment because /api/catalog serializes them too.
    for im in images:
        for key in _NUMERIC_RECORD_FIELDS["images"]:
            _fin(f"image {key}", im.get(key))
        # BUG-002: normalize the frame class before it is stored OR rendered.
        im["imagetyp"] = normalize_import_imagetyp(im.get("imagetyp"))
        im["session_date_obs"] = normalize_session_date_obs(im.get("session_date_obs"))
    for c in customs:
        for key in _NUMERIC_RECORD_FIELDS["customs"]:
            _fin(f"custom {key}", c.get(key))
        problem = custom_id_problem(c.get("id"))
        if problem:
            raise ValueError(problem)
    # PY-01: `safe_rel` canonicalization can map several records onto one stored
    # path; the restore loop would then overwrite and the summary would still
    # over-count. Refuse the bundle (no export produces this shape).
    _reject_duplicate_paths(images, "images")
    _reject_duplicate_paths(panoramas, "panoramas")
    # PY-07: two targets sharing an id would collapse to one row under
    # INSERT OR REPLACE while the summary still reported both.
    _reject_duplicate_custom_ids(customs)
    # ONE acceptance pass (PY-06/SP-05): the caller's payload loop and the
    # inserts below must agree on which records exist.
    return _accepted_records(panoramas, images)


class Database:
    def __init__(self, db_path: Path):
        self.path = db_path
        # reentrant: read methods wrap their queries too, so the shared
        # sqlite connection is never used from two threads at once
        self.lock = threading.RLock()
        # Serialize archive import/reindex publication without blocking reads of
        # the shared SQLite connection during filesystem work.
        self.archive_lock = threading.RLock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # One-way removal of the retired sky-brightness preference. Do this
        # during database open, before the server can make its automatic
        # safety backup, so future exports contain no dormant provider state.
        self.conn.execute("DELETE FROM settings WHERE key='sky_brightness_provider'")
        # Glass is no longer shipped. Normalize any saved theme before state is
        # read or a safety backup is made.
        self.conn.execute("UPDATE settings SET value=? WHERE key='theme'",
                          (json.dumps("workbench"),))
        # NOTE: databases outlive the code (users carry them between
        # versions), so schema evolution MUST be additive: new tables go
        # into SCHEMA (CREATE TABLE IF NOT EXISTS), new columns use the
        # try/ALTER/except pattern below. Never drop or re-shape existing
        # columns - old user DBs will break. Seed tables are insert-only.
        try:  # migration for databases created before fov existed
            self.conn.execute("ALTER TABLE panoramas ADD COLUMN fov REAL NOT NULL DEFAULT 360")
        except sqlite3.OperationalError:
            pass
        try:  # migration: user-defined type for ingested images
            self.conn.execute("ALTER TABLE images ADD COLUMN otype TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # migration: frame classification (light/bias/dark/flat)
            self.conn.execute("ALTER TABLE images ADD COLUMN imagetyp TEXT")
        except sqlite3.OperationalError:
            pass
        # Originals from older versions are fingerprinted lazily on import or
        # reindex. Keep existing duplicate rows intact; this is not a UNIQUE
        # constraint and never deletes user files.
        for column, kind in (("session_date_obs", "TEXT"),
                             ("content_sha256", "TEXT"), ("file_size", "INTEGER"),
                             ("file_mtime_ns", "INTEGER")):
            try:
                self.conn.execute(f"ALTER TABLE images ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_content_sha256 "
            "ON images(content_sha256)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_file_size ON images(file_size)")
        try:  # migration: inverse painting mode ('obstacle' | 'sky')
            self.conn.execute(
                "ALTER TABLE panoramas ADD COLUMN paint_mode "
                "TEXT NOT NULL DEFAULT 'obstacle'")
        except sqlite3.OperationalError:
            pass
        try:  # migration: upper ceiling for sky-mode bands (blocked above)
            self.conn.execute(
                "ALTER TABLE panoramas ADD COLUMN profile_top "
                "TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        try:  # migration: sky mode "block above the band" opt-in (default off:
              # everything above the painted band is free sky)
            self.conn.execute(
                "ALTER TABLE panoramas ADD COLUMN block_above "
                "INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:  # migration: named obstruction landmarks
            self.conn.execute(
                "ALTER TABLE panoramas ADD COLUMN landmarks "
                "TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        try:  # migration: calibration provenance (source/orientation/notes)
            self.conn.execute(
                "ALTER TABLE panoramas ADD COLUMN provenance "
                "TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass
        try:  # migration: confidence per canonical world-azimuth bin
            self.conn.execute(
                "ALTER TABLE panoramas ADD COLUMN confidence "
                "TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        try:  # migration: import-run marker for merging chunked uploads
            self.conn.execute("ALTER TABLE import_batches ADD COLUMN run_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:  # migration: popularity scores for tiebreaking
            self.conn.execute("ALTER TABLE objects ADD COLUMN popularity INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:  # migration: popularity for advanced objects too
            self.conn.execute("ALTER TABLE advanced_objects ADD COLUMN popularity INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()
        self.seed_catalog()
        self.apply_popularity()
        self.migrate_legacy_object_ids()
        self.make_backup()

    def make_backup(self) -> None:
        """Keep one rolling backup of the user database next to the original.

        PY-07: this used `shutil.copyfile`, a byte copy taken with no lock while
        the server thread keeps committing - a write landing mid-copy could leave
        a torn, unusable recovery artifact and nothing detected it. SQLite's own
        backup API reads a consistent snapshot under its locking."""
        try:
            if not (self.path.exists() and self.path.stat().st_size > 0):
                return
            dest = sqlite3.connect(str(self.path) + ".backup")
            try:
                with self.lock:
                    self.conn.backup(dest)
            finally:
                dest.close()
        except (OSError, sqlite3.Error):
            pass

    # ---------------- catalog ----------------

    def seed_catalog(self):
        """Seed BOTH catalog tables from the bundled OpenNGC CSV.

        Standard mode is the most popular `catalog_data.STANDARD_TOP_N` objects
        and lives in `objects`; every other OpenNGC object lives in
        `advanced_objects` (the two are disjoint, so advanced mode is the whole
        catalog). There is no hand-curated list any more.

        Both tables are PURE SEED DATA - user objects live in `custom_objects` -
        so they are rebuilt whenever the stored split no longer matches what the
        CSV + popularity table select. That is what makes a catalog rebuild or a
        popularity change re-select standard mode WITHOUT a schema migration, and
        it is why the rebuild compares the actual id sets, not just the counts
        (a popularity change keeps 200 standard rows but changes WHICH 200)."""
        csv_path = resource_dir() / "app" / "data" / "openned.csv"
        rows = _read_catalog_csv(csv_path)
        if not rows:
            return
        ids = [r["id"] for r in rows]
        standard = catalog_data.standard_ids(ids)
        by_id = {r["id"]: r for r in rows}
        std_rows = [by_id[i] for i in ids if i in standard]
        adv_rows = [by_id[i] for i in ids if i not in standard]
        with self.lock:
            have_std = {r[0] for r in self.conn.execute("SELECT id FROM objects")}
            have_adv = {r[0] for r in self.conn.execute(
                "SELECT id FROM advanced_objects")}
            if have_std == standard and have_adv == set(ids) - standard:
                return
            self.conn.execute("DELETE FROM objects")
            self.conn.execute("DELETE FROM advanced_objects")
            for table, batch in (("objects", std_rows),
                                 ("advanced_objects", adv_rows)):
                self.conn.executemany(
                    f"INSERT OR IGNORE INTO {table}(id,primary_name,messier,"
                    f"ngcic,other,otype,ra_hours,dec_deg,vmag,size_am,"
                    f"constellation,wiki,blurb,search) VALUES "
                    f"(:id,:primary_name,:messier,:ngcic,:other,:otype,"
                    f":ra_hours,:dec_deg,:vmag,:size_am,:constellation,"
                    f":wiki,:blurb,:search)",
                    batch)
            self.conn.commit()

    def advanced_count(self) -> int:
        """How many rows `advanced_objects` holds (CD-04: this accessor was
        defined twice in the class; the second definition silently shadowed the
        first, so an edit to the dead copy changed nothing)."""
        with self.lock:
            r = self.conn.execute(
                "SELECT COUNT(*) FROM advanced_objects").fetchone()
            return r[0]

    def apply_popularity(self) -> int:
        """Stamp the popularity scores onto the seeded rows (idempotent, runs
        on every start so a DB seeded before a score existed catches up).
        Returns the number of scored entries."""
        from .popularity_data import POPULARITY
        with self.lock:
            for table in ("objects", "advanced_objects"):
                self.conn.executemany(
                    f"UPDATE {table} SET popularity = ? WHERE id = ?",
                    [(s, o) for o, s in POPULARITY.items()])
            self.conn.commit()
        return len(POPULARITY)

    def migrate_legacy_object_ids(self) -> int:
        """Rewrite library ids that came from the pre-0.6.26 curated catalog.

        A capture stores the catalogue id it resolved to at INGEST time, and the
        OpenNGC switch renamed objects (`m42` -> `ngc1976`), so without this an
        existing library would show them all as unresolved. `catalog_data.
        LEGACY_ID_MAP` holds the old -> new ids; the rewrite touches only the two
        tables that name catalog objects (never custom targets, whose ids are
        `u_*`) and is idempotent - afterwards no row carries an old id. Objects
        OpenNGC has no entry for (Abell 21, Barnard's Loop, ...) keep their stale
        id on purpose: `app/imglib.py` re-resolves those from the frame's own
        OBJECT header/coordinates at display time."""
        from .catalog_data import LEGACY_ID_MAP
        if not LEGACY_ID_MAP:
            return 0
        olds = list(LEGACY_ID_MAP)
        marks = ",".join("?" * len(olds))
        moved = 0
        with self.lock:
            for table in ("images", "import_batches"):
                if not self.conn.execute(
                        f"SELECT 1 FROM {table} WHERE object_id IN ({marks}) "
                        f"LIMIT 1", olds).fetchone():
                    continue
                for old in olds:
                    cur = self.conn.execute(
                        f"UPDATE {table} SET object_id=? WHERE object_id=?",
                        (LEGACY_ID_MAP[old], old))
                    moved += cur.rowcount or 0
            self.conn.commit()
        return moved

    def all_objects(self, advanced: bool = False) -> list[dict]:
        with self.lock:
            builtin = [dict(r) for r in self.conn.execute(
                "SELECT * FROM objects ORDER BY messier IS NULL, "
                "CAST(SUBSTR(messier,2) AS INTEGER), id")]
            custom = [dict(r) for r in self.conn.execute(
                "SELECT * FROM custom_objects ORDER BY primary_name")]
            out = custom + builtin
            if advanced:
                adv = [dict(r) for r in self.conn.execute(
                    "SELECT * FROM advanced_objects ORDER BY id")]
                for r in adv:
                    r["_adv"] = True
                out.extend(adv)
        return _drop_retired_fields(out)

    def get_object(self, oid: str) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM objects WHERE id=?",
                                  (oid,)).fetchone()
            if r:
                return dict(r)
            r = self.conn.execute("SELECT * FROM custom_objects WHERE id=?",
                                  (oid,)).fetchone()
            if not r:
                r = self.conn.execute(
                    "SELECT * FROM advanced_objects WHERE id=?",
                    (oid,)).fetchone()
            if not r:
                return None
            d = dict(r)
        # normalize to the shape the rest of the app expects
        for k in ("messier", "ngcic", "other"):
            d.setdefault(k, "")
        for k in ("wiki",):
            d.setdefault(k, None)
        return _drop_retired_fields([d])[0]

    # ---------------- custom objects ----------------

    def add_custom(self, d: dict) -> str:
        # T6-F1 (review of t6): the numeric columns are read back into
        # /api/catalog, which Starlette serializes with `allow_nan=False` - one
        # stored inf/nan 500s that route forever. Refuse rather than sanitize:
        # silently NULLing would change what the caller asked to store (the
        # same rule the import boundary applies via `_fin`).
        for key in _NUMERIC_RECORD_FIELDS["customs"]:
            value = d.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)):
                raise ValueError(
                    f"custom target {key} must be a finite number")
        search = " | ".join(filter(None, [
            d["primary_name"].lower(),
            (d.get("otype") or "").lower(),
            "custom", d.get("id"),
        ]))
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO custom_objects"
                "(id,primary_name,otype,ra_hours,dec_deg,vmag,size_am,"
                "constellation,blurb,search) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d["primary_name"], d.get("otype", "galaxy"),
                 d["ra_hours"], d["dec_deg"], d.get("vmag"),
                 d.get("size_am") or "", d.get("constellation") or "",
                 d.get("blurb") or "", search))
            self.conn.commit()
        return d["id"]

    def delete_custom(self, cid: str):
        with self.lock:
            self.conn.execute("DELETE FROM custom_objects WHERE id=?", (cid,))
            self.conn.commit()

    def customs(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM custom_objects ORDER BY primary_name")]

    def is_custom(self, cid: str) -> bool:
        with self.lock:
            r = self.conn.execute(
                "SELECT 1 FROM custom_objects WHERE id=?", (cid,)).fetchone()
            return r is not None

    # ---------------- sites ----------------

    def sites(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM sites ORDER BY id")]

    def add_site(self, name: str, lat: float, lon: float, elev: int) -> int:
        with self.lock:
            c = self.conn.execute(
                "INSERT INTO sites(name,lat,lon,elev) VALUES(?,?,?,?)",
                (name, lat, lon, elev))
            self.conn.commit()
            return c.lastrowid

    def delete_site(self, sid: int):
        """Delete a site; the active pointer moves to another site (or clears)
        instead of dangling - a dangling pointer made /api/night answer 400
        "No observing location configured" with no way back through the UI."""
        with self.lock:
            self.conn.execute("DELETE FROM sites WHERE id=?", (sid,))
            if self.setting("active_site_id") == sid:
                rows = list(self.conn.execute("SELECT id FROM sites ORDER BY id"))
                self.set_setting("active_site_id", rows[0]["id"] if rows else None)
            self.conn.commit()

    def update_site(self, sid: int, name: str, lat: float, lon: float, elev: int):
        with self.lock:
            self.conn.execute(
                "UPDATE sites SET name=?,lat=?,lon=?,elev=? WHERE id=?",
                (name, lat, lon, elev, sid))
            self.conn.commit()

    # ---------------- rigs ----------------

    def rigs(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM rigs ORDER BY id")]

    def add_rig(self, name: str, focal_mm: float, sensor_w: float,
                sensor_h: float, fov_w: float, fov_h: float) -> int:
        with self.lock:
            c = self.conn.execute(
                "INSERT INTO rigs(name,focal_mm,sensor_w,sensor_h,fov_w,fov_h) "
                "VALUES(?,?,?,?,?,?)",
                (name, focal_mm, sensor_w, sensor_h, fov_w, fov_h))
            self.conn.commit()
            return c.lastrowid

    def delete_rig(self, rid: int):
        with self.lock:
            self.conn.execute("DELETE FROM rigs WHERE id=?", (rid,))
            if self.setting("active_rig_id") == rid:
                self.set_setting("active_rig_id", None)
            self.conn.commit()

    # ---------------- panoramas ----------------

    def panoramas(self) -> list[dict]:
        with self.lock:
            out = []
            for r in self.conn.execute(
                    "SELECT * FROM panoramas ORDER BY rowid DESC"):
                d = dict(r)
                # PY-03: a hand-edited or legacy row whose column is not valid
                # JSON used to raise here, 500ing EVERY listing - and the desktop's
                # first boot request is /api/state, so the app was unbootable until
                # the DB was repaired. Fall back to the column's canonical empty
                # value; the write boundaries (`_validate_import_*`, `_check_profile`,
                # `_check_strokes`) are what keep malformed shapes out in the first
                # place.
                for key in ("strokes", "profile", "profile_top", "landmarks",
                            "provenance", "confidence"):
                    try:
                        d[key] = json.loads(d[key])
                    except (TypeError, ValueError):
                        d[key] = {} if key == "provenance" else []
                if not isinstance(d["strokes"], list):
                    d["strokes"] = []       # both editors iterate this
                d["landmarks"] = normalize_horizon_landmarks(d["landmarks"])
                d["provenance"] = normalize_horizon_provenance(d["provenance"])
                d["confidence"] = normalize_horizon_confidence(d["confidence"])
                d["block_above"] = bool(d.get("block_above"))
                out.append(d)
            return out

    def active_panorama(self) -> dict | None:
        for p in self.panoramas():
            if p["active"]:
                return p
        return None

    def set_active_panorama(self, pid: str | None) -> None:
        """Make `pid` the ONLY active panorama (or none when pid is None).

        One transaction / one commit: activating a panorama through
        `PUT /api/panorama/{pid}` used to leave earlier rows active, and
        `active_panorama()` returns the newest of them, so `/api/state` and
        `/api/night` could disagree about which horizon governs planning.

        BUG-006: the pointer may only name a row that EXISTS. The route checks
        its target, but `/api/settings` (and an import) can move the pointer
        directly, and `/api/state` advertises it - so an unknown id is refused
        here, in the same transaction that would have stored it."""
        with self.lock:
            if pid is not None:
                known = self.conn.execute(
                    "SELECT 1 FROM panoramas WHERE id=?", (pid,)).fetchone()
                if not known:
                    raise ValueError(f"no such panorama: {pid}")
            self.conn.execute("UPDATE panoramas SET active=0")
            if pid is not None:
                self.conn.execute(
                    "UPDATE panoramas SET active=1 WHERE id=?", (pid,))
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES('active_pano_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(pid),))
            self.conn.commit()

    def add_panorama(self, pid: str, name: str, filename: str) -> str:
        with self.lock:
            self.conn.execute(
                "INSERT INTO panoramas(id,name,file) VALUES(?,?,?)",
                (pid, name, filename))
            self.conn.commit()
        return pid

    def update_panorama(self, pid: str, **fields):
        allowed = {"name", "file", "az_offset", "alt_bottom", "alt_top",
                   "strokes", "profile", "profile_top", "active", "fov",
                   "paint_mode", "block_above", "landmarks", "provenance",
                   "confidence"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("strokes", "profile", "profile_top", "landmarks",
                     "provenance", "confidence"):
                v = json.dumps(v)
            if k in ("active", "block_above"):
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(pid)
        with self.lock:
            self.conn.execute(f"UPDATE panoramas SET {', '.join(sets)} WHERE id=?", vals)
            self.conn.commit()

    def get_panorama(self, pid: str) -> dict | None:
        for p in self.panoramas():
            if p["id"] == pid:
                return p
        return None

    def delete_panorama(self, pid: str):
        """Delete a panorama and keep `active_pano_id` naming a real row.

        BUG-006: the DELETE removed the row and its image but left the setting
        pointing at it, so `/api/state` advertised an "active panorama" that
        planning could not resolve (the desktop then planned against empty sky
        while the UI claimed a horizon). Cleared here, in the SAME transaction,
        so the two can never disagree - and only when the deleted row was the
        one the pointer named (deleting an inactive pano must not clear the
        user's active choice)."""
        with self.lock:
            active = self.conn.execute(
                "SELECT 1 FROM settings WHERE key='active_pano_id' "
                "AND value=?", (json.dumps(pid),)).fetchone()
            self.conn.execute("DELETE FROM panoramas WHERE id=?", (pid,))
            if active:
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES('active_pano_id',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(None),))
            self.conn.commit()

    # ---------------- settings ----------------

    def setting(self, key: str, default=None):
        with self.lock:
            r = self.conn.execute(
                "SELECT value FROM settings WHERE key=?",
                (key,)).fetchone()
            if not r:
                return default
            try:
                value = json.loads(r["value"])
                return normalize_theme(value) if key == "theme" else value
            except Exception:
                return default

    def set_setting(self, key: str, value):
        if key == "theme":
            value = normalize_theme(value)
        with (self.archive_lock if key == "image_dir" else nullcontext()), self.lock:
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))
            self.conn.commit()

    def image_dir(self) -> str:
        """User-chosen image archive folder, or the default when unset."""
        from .paths import default_archive_dir
        v = self.setting("image_dir")
        if not v:
            return str(default_archive_dir())
        return str(v)

    # ---------------- ingested images ----------------

    def add_image(self, rec: dict) -> int:
        with self.lock:
            try:
                c = self.conn.execute(
                    "INSERT INTO images(file,orig_name,object_id,object_name,"
                    "ra_hours,dec_deg,exposure,date_obs,session_date_obs,filter,telescope,imagetyp,"
                    "ingested_at,content_sha256,file_size,file_mtime_ns) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec["file"], rec["orig_name"], rec.get("object_id"),
                     rec["object_name"], finite_or_none(rec.get("ra_hours")),
                     finite_or_none(rec.get("dec_deg")),
                     finite_or_none(rec.get("exposure")), rec.get("date_obs"),
                     normalize_session_date_obs(rec.get("session_date_obs")),
                     rec.get("filter"), rec.get("telescope"), rec.get("imagetyp"),
                     rec["ingested_at"], rec.get("content_sha256"),
                     rec.get("file_size"), rec.get("file_mtime_ns")))
                self.conn.commit()
                return c.lastrowid
            except BaseException:
                self.conn.rollback()
                raise

    def set_image_fingerprint(self, img_id: int, digest: str,
                              size: int, mtime_ns: int) -> None:
        """Cache a fingerprint measured from the current local original."""
        with self.lock:
            try:
                self.conn.execute(
                    "UPDATE images SET content_sha256=?,file_size=?,file_mtime_ns=? "
                    "WHERE id=?", (digest, size, mtime_ns, img_id))
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def images(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM images ORDER BY id")]

    def images_with_digest(self, digest: str) -> list[dict]:
        """Fingerprint candidates in deterministic order, using the index."""
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM images WHERE content_sha256=? ORDER BY id",
                (digest,))]

    def image_fingerprint_candidates(self, digest: str, size: int) -> list[dict]:
        """Indexed candidates plus legacy originals needing their first hash."""
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM images WHERE content_sha256=? OR file_size=? "
                "OR file_size IS NULL "
                "ORDER BY CASE WHEN content_sha256=? THEN 0 ELSE 1 END,id",
                (digest, size, digest))]

    def images_for_object(self, object_id: str | None,
                          object_name: str) -> list[dict]:
        """Frames relevant to session-folder selection, in insertion order.

        Unresolved imports historically match every row with the same exact
        object_name, including rows that already have an object_id. Keep that
        predicate while using indexes to avoid loading the full image library.
        """
        with self.lock:
            if object_id is not None:
                rows = self.conn.execute(
                    "SELECT * FROM images WHERE object_id=? ORDER BY id",
                    (object_id,))
            else:
                rows = self.conn.execute(
                    "SELECT * FROM images WHERE object_name=? ORDER BY id",
                    (object_name,))
            return [dict(r) for r in rows]

    def image(self, img_id: int) -> dict | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM images WHERE id=?", (img_id,)).fetchone()
            return dict(r) if r else None

    def rename_images(self, old_name: str, new_name: str, otype: str | None,
                      new_folder: str, moved: dict[str, str] | None = None):
        """Rename an unresolved object group (name, type and stored paths).

        Matching is whitespace/case-insensitive, mirroring the library grouping
        (`imglib.name_key`), so one rename cannot leave sibling spellings behind
        pointing at a folder that was moved away.

        `moved` is the map the disk move returned ({old relative path: new
        relative path}). PY-01: the stored path now becomes the file's REAL
        destination, so a frame that had to be uniquified when the rename merged
        into an existing folder is not left aliased with the frame it would have
        overwritten."""
        from .imglib import name_key          # local import: keeps the
        target = name_key(old_name)           # module import graph acyclic
        moved = moved or {}
        with self.lock:
            cur = self.conn.cursor()
            rows = [dict(r) for r in cur.execute(
                "SELECT id, file, object_name FROM images "
                "WHERE object_id IS NULL").fetchall()]
            ids = [r["id"] for r in rows if name_key(r["object_name"]) == target]
            for i in ids:
                cur.execute("UPDATE images SET object_name=?, otype=? WHERE id=?",
                            (new_name, otype, i))
            for r in rows:
                if r["id"] not in ids:
                    continue
                if r["file"] in moved:
                    new_file = moved[r["file"]]
                else:
                    parts = r["file"].split("/", 1)
                    new_file = (f"{new_folder}/{parts[1]}"
                                if len(parts) == 2 and parts[0] != new_folder
                                else r["file"])
                if new_file != r["file"]:
                    cur.execute("UPDATE images SET file=? WHERE id=?",
                                (new_file, r["id"]))
            self.conn.commit()

    # ---------------- observations (journal) ----------------

    def add_observation(self, object_id: str, date_obs: str | None,
                        notes: str | None) -> int:
        with self.lock:
            c = self.conn.execute(
                "INSERT INTO observations(object_id,date_obs,notes,created_at) "
                "VALUES(?,?,?,?)",
                (object_id, date_obs or None, notes or "",
                 datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")))
            self.conn.commit()
            return c.lastrowid

    def observations(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM observations ORDER BY id")]

    def observations_for(self, object_id: str) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM observations WHERE object_id=? ORDER BY id",
                (object_id,))]

    def delete_observation(self, obs_id: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM observations WHERE id=?", (obs_id,))
            self.conn.commit()

    # ---------------- import history (batches) ----------------

    def add_import_batch(self, rec: dict) -> int:
        with self.lock:
            c = self.conn.execute(
                "INSERT INTO import_batches(created_at,object_id,object_name,"
                "slug,session,frame,n_files,total_exposure,first_obs,image_dir,run_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (rec["created_at"], rec.get("object_id"), rec["object_name"],
                 rec["slug"], rec["session"], rec["frame"], rec["n_files"],
                 round(float(rec.get("total_exposure") or 0.0), 1),
                 rec.get("first_obs"), rec["image_dir"], rec.get("run_id")))
            self.conn.commit()
            return c.lastrowid

    def merge_import_batch(self, run_id: str, rec: dict) -> int:
        """Record a batch, merging into the row of the same import run
        (one upload action split into chunks = one history entry)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT id, n_files, total_exposure, first_obs FROM import_batches "
                "WHERE run_id=? AND slug=? AND session=? AND frame=?",
                (run_id, rec["slug"], rec["session"], rec["frame"])).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE import_batches SET n_files=?, total_exposure=?, "
                    "first_obs=? WHERE id=?",
                    (row["n_files"] + rec["n_files"],
                     round(row["total_exposure"] + rec["total_exposure"], 1),
                     min((value for value in (row["first_obs"], rec.get("first_obs"))
                          if value), default=None), row["id"]))
                self.conn.commit()
                return row["id"]
            return self.add_import_batch({**rec, "run_id": run_id})

    def import_batches(self, limit: int = 60) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM import_batches ORDER BY id DESC LIMIT ?",
                (max(1, limit),))]

    def all_settings(self) -> dict:
        with self.lock:
            out = {}
            for r in self.conn.execute("SELECT key,value FROM settings"):
                try:
                    value = json.loads(r["value"])
                    out[r["key"]] = (normalize_theme(value)
                                      if r["key"] == "theme" else value)
                except Exception:
                    pass
            return out

    def import_bundle(self, settings: dict, sites: list, rigs: list,
                      panoramas: list, customs: list | None = None,
                      images: list | None = None,
                      observations: list | None = None,
                      accepted: dict | None = None) -> dict:
        with self.archive_lock:
            return self._import_bundle_locked(settings, sites, rigs, panoramas,
                                              customs, images, observations, accepted)

    def _import_bundle_locked(self, settings: dict, sites: list, rigs: list,
                              panoramas: list, customs: list | None = None,
                              images: list | None = None,
                              observations: list | None = None,
                              accepted: dict | None = None) -> dict:
        """Replace all user data with an imported bundle (catalog untouched).

        The replace runs inside ONE transaction and rolls back on any failure:
        the DELETEs would otherwise stay live on this long-lived connection and
        be committed by the next unrelated write, silently wiping the user's
        data after a failed import.

        The legacy `observations` table is deliberately NOT cleared - it is kept
        for schema compatibility and is no longer exported or imported.

        `import_batches` (the "Recent imports" history) IS cleared: it is
        per-install bookkeeping for the library that this restore replaces, so
        keeping rows that point at pre-restore object/session folders would be a
        misleading, inconsistent state. The pre-import history is preserved in
        the `<db>.backup-auto.zip` safety copy.

        File paths inside the bundle are UNTRUSTED: every `panoramas[].file` /
        `images[].file` goes through `paths.safe_rel`, which rejects absolute,
        drive/UNC and `..` paths. A record with such a value is dropped instead
        of stored (it would otherwise be resolved by the read/delete routes
        long after the import). Returns `{"panoramas": n, "images": n}`, the
        number of dropped records.

        `accepted` (PY-06/SP-05) is an out-parameter: the caller can pass a dict
        and receive `{"panoramas": [...], "images": [...]}` - the records that
        really reached the tables, with their canonical `file`. `app/backup.py`
        drives its payload writes from `_accepted_records` (the SAME predicate),
        so a bundle record whose id failed `safe_token` cannot leave an orphan
        file behind and the summary cannot report a pair that was never stored.
        """
        customs = customs or []
        images = images or []
        observations = observations or []
        # Every shape/field check lives in ONE side-effect-free place (BUG-001,
        # BUG-002, BUG-004) so the restore can run it BEFORE it writes a payload
        # file - and the transaction below is never entered with bad input. The
        # acceptance pass (PY-06/SP-05) is the LAST step of it, so the caller's
        # payload loop and the inserts here agree on which records exist.
        store = prepare_import_bundle(settings, sites, rigs, panoramas,
                                      customs, images)
        panoramas, images = store["panoramas"], store["images"]
        skipped = store["skipped"]
        accepted_panos: list[dict] = []
        accepted_images: list[dict] = []
        with self.lock:
            cur = self.conn.cursor()
            try:
                cur.execute("DELETE FROM sites")
                cur.execute("DELETE FROM rigs")
                cur.execute("DELETE FROM panoramas")
                cur.execute("DELETE FROM settings")
                cur.execute("DELETE FROM custom_objects")
                cur.execute("DELETE FROM images")
                # history refers to the library this restore replaces
                cur.execute("DELETE FROM import_batches")
                for s in sites:
                    cur.execute(
                        "INSERT INTO sites(id,name,lat,lon,elev) VALUES(?,?,?,?,?)",
                        (s.get("id"), s.get("name"), s.get("lat"),
                         s.get("lon"), s.get("elev", 0)))
                for r in rigs:
                    cur.execute(
                        "INSERT INTO rigs(id,name,focal_mm,sensor_w,sensor_h,fov_w,fov_h) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (r.get("id"), r.get("name"), r.get("focal_mm"),
                         r.get("sensor_w"), r.get("sensor_h"),
                         r.get("fov_w"), r.get("fov_h")))
                for acc in panoramas:
                    p = acc["raw"]
                    accepted_panos.append(acc)
                    cur.execute(
                        "INSERT INTO panoramas(id,name,file,az_offset,alt_bottom,"
                        "alt_top,strokes,profile,profile_top,active,fov,paint_mode,"
                        "block_above,landmarks,provenance,confidence) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (acc["id"], p.get("name"), acc["file"],
                         p.get("az_offset", 0), p.get("alt_bottom", 0),
                         p.get("alt_top", 45), json.dumps(p.get("strokes", [])),
                         json.dumps(p.get("profile", [])),
                         json.dumps(p.get("profile_top", [])),
                         1 if p.get("active") else 0, p.get("fov", 360),
                         p.get("paint_mode", "obstacle"),
                         1 if p.get("block_above") else 0,
                         json.dumps(normalize_horizon_landmarks(p.get("landmarks", []))),
                         json.dumps(normalize_horizon_provenance(p.get("provenance", {}))),
                         json.dumps(p.get("confidence", []))))
                for c in customs:
                    # PY4 (review): the id must keep the `u_` custom prefix -
                    # a bundle carrying a builtin id (e.g. "m31") would collide
                    # with the seeded catalog and could not be deleted. Since
                    # BUG-002 it must ALSO be a plain token (it is interpolated
                    # into UI attributes): ONE rule, `custom_id_problem`, shared
                    # with `prepare_import_bundle` so the pre-write check and
                    # this insert can never disagree.
                    problem = custom_id_problem(c.get("id"))
                    if problem:
                        raise ValueError(problem)
                    cur.execute(
                        "INSERT OR REPLACE INTO custom_objects"
                        "(id,primary_name,otype,ra_hours,dec_deg,vmag,size_am,"
                        "constellation,blurb,search) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (c.get("id"), c.get("primary_name"),
                         c.get("otype", "galaxy"), c.get("ra_hours"),
                         c.get("dec_deg"), c.get("vmag"),
                         c.get("size_am") or "", c.get("constellation") or "",
                         c.get("blurb") or "",
                         " | ".join(filter(None, [
                             str(c.get("primary_name", "")).lower(),
                             str(c.get("otype", "")).lower(), "custom",
                             str(c.get("id", ""))]))))
                for acc in images:
                    im = acc["raw"]
                    accepted_images.append(acc)
                    cur.execute(
                        "INSERT OR REPLACE INTO images"
                        "(id,file,orig_name,object_id,object_name,otype,ra_hours,"
                        "dec_deg,exposure,date_obs,session_date_obs,filter,telescope,imagetyp,"
                        "ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (im.get("id"), acc["file"], im.get("orig_name", ""),
                         im.get("object_id"), im.get("object_name", ""),
                         im.get("otype"), im.get("ra_hours"), im.get("dec_deg"),
                         im.get("exposure"), im.get("date_obs"), im.get("session_date_obs"),
                         im.get("filter"), im.get("telescope"),
                         im.get("imagetyp"), im.get("ingested_at", "")))
                for ob in observations:
                    cur.execute(
                        "INSERT OR REPLACE INTO observations"
                        "(id,object_id,date_obs,notes,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (ob.get("id"), ob.get("object_id"), ob.get("date_obs"),
                         ob.get("notes") or "", ob.get("created_at", "")))
                for k, v in settings.items():
                    cur.execute(
                        "INSERT INTO settings(key,value) VALUES(?,?)",
                        (k, json.dumps(v)))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        if accepted is not None:
            accepted["panoramas"] = accepted_panos
            accepted["images"] = accepted_images
        return skipped


# Columns an older database still carries and the catalogue no longer ships.
# `distance` was ALWAYS empty - OpenNGC has no distance column (its `Pax` is a
# parallax, and for galaxies a foreground star's: M31 reads 6.0 mas => 167 pc)
# and redshift is not a distance - so 0.6.32 removed the field (DECISIONS.md).
# The tables are `SELECT *`ed, so without this an existing user database would
# put the retired column straight back into /api/catalog and the web payload.
_RETIRED_COLUMNS = ("distance",)


def _drop_retired_fields(rows: list[dict]) -> list[dict]:
    for r in rows:
        for col in _RETIRED_COLUMNS:
            r.pop(col, None)
    return rows


def _read_catalog_csv(csv_path: Path) -> list[dict]:
    """The bundled OpenNGC catalog as seed-ready dicts (empty when the file is
    missing, so a broken install still boots). Malformed rows are skipped, never
    fatal - the CSV is data, not code.

    CD-03 (0.6.32 review): parsed with the `csv` module, NOT `line.split(";")`.
    The generator (`scripts/build_adv_catalog.py`) writes with
    `csv.writer(..., delimiter=";")`, which QUOTES a field containing `;`, `"`
    or a newline - a naive split mis-counts such a row and it was silently
    dropped here. The comment/blank-line/field-count behaviour is unchanged
    (a short comment line is exactly one field, so it is skipped by the count
    check as before; `csv.reader` also handles a quoted embedded newline, which
    the line-by-line split could not). `scripts/match_popularity.py` keeps an
    identical copy of this loop - both readers are pinned by
    `tests/test_catalog_csv_readers.py`.
    """
    if not csv_path.is_file():
        return []
    cols = ("id", "primary_name", "messier", "ngcic", "other", "otype",
            "ra_hours", "dec_deg", "vmag", "size_am", "constellation",
            "wiki", "blurb", "search")
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        for parts in csv.reader(f, delimiter=";"):
            if not parts:
                continue                      # blank line -> [] (was skipped)
            if parts[0].startswith("#"):
                continue                      # comment line
            if len(parts) != len(cols):
                continue
            d = dict(zip(cols, parts))
            try:
                d["ra_hours"] = float(d["ra_hours"]) if d["ra_hours"] else None
                d["dec_deg"] = float(d["dec_deg"]) if d["dec_deg"] else None
                d["vmag"] = float(d["vmag"]) if d["vmag"] else None
            except ValueError:
                continue          # malformed row - skip, don't crash boot
            rows.append(d)
    return rows


def new_id() -> str:
    return uuid.uuid4().hex[:10] + format(int(time.time()) % 100000, "05d")
