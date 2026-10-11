"""SQLite storage: seeded DSO catalog + the user image library and settings.

Planner-era tables (sites, rigs, panoramas, observations) are no longer created
for new databases; databases that still carry them keep those tables untouched
(additive-migration policy) and no code reads them."""

from __future__ import annotations
from contextlib import nullcontext

import csv
import json
import logging
import math
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, UTC
from pathlib import Path

from . import catalog_data
from .paths import resource_dir, safe_rel
from .theme import normalize_theme

log = logging.getLogger(__name__)
BACKUP_HISTORY_LIMIT = 5

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


class Database:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        # reentrant: read methods wrap their queries too, so the shared
        # sqlite connection is never used from two threads at once
        self.lock = threading.RLock()
        # Serialize archive import/reindex publication without blocking reads of
        # the shared SQLite connection during filesystem work.
        self.archive_lock = threading.RLock()
        existing = self.path.exists() and self.path.stat().st_size > 0
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            # Capture the original schema and settings before any one-way
            # migration or catalog replacement. An unavailable recovery copy
            # refuses opening rather than risking an unprotected upgrade.
            if existing:
                self._write_backup_snapshot("pre-migration")
            self._initialize()
        except BaseException:
            self.conn.close()
            raise

    def _initialize(self):
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
        # (Panorama-table migrations were removed with the planner; databases
        # that still carry those tables keep their columns, unread.)
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
        """Publish a validated snapshot and retain five per recovery phase.

        The historical copies outlive updates to the compatible rolling
        ``.backup`` file. SQLite's backup API includes committed WAL contents;
        never copy the live database file directly. All staging is adjacent to
        the database so publication is an atomic same-filesystem replacement.
        """
        if not (self.path.exists() and self.path.stat().st_size > 0):
            return
        with self.lock:
            snapshot = self._write_backup_snapshot("open")
            staging = self.path.with_name(self.path.name + ".backup-stage-" + uuid.uuid4().hex)
            try:
                # This source is already closed and validated, unlike the live
                # database. Preserve the old rolling copy until copy completes.
                shutil.copyfile(snapshot, staging)
                staging.replace(Path(str(self.path) + ".backup"))
            except BaseException:
                log.exception("Could not publish rolling database backup for %s", self.path)
                self._remove_backup_staging(staging)
                raise

    @staticmethod
    def _validate_backup(connection):
        result = [row[0] for row in connection.execute("PRAGMA quick_check")]
        if result != ["ok"]:
            raise sqlite3.DatabaseError("Database recovery snapshot failed quick_check: " + "; ".join(result))

    @staticmethod
    def _remove_backup_staging(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.exception("Could not remove failed database backup staging: %s", path)
            raise

    def _write_backup_snapshot(self, phase):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        final = self.path.with_name(f"{self.path.name}.backup.{phase}.{stamp}-{uuid.uuid4().hex}")
        staging = self.path.with_name(self.path.name + ".backup-stage-" + uuid.uuid4().hex)
        try:
            dest = sqlite3.connect(str(staging))
            try:
                with self.lock:
                    if self.conn.in_transaction:
                        raise sqlite3.OperationalError("Cannot back up uncommitted database changes")
                    self.conn.backup(dest)
                    self._validate_backup(dest)
            finally:
                dest.close()
            staging.replace(final)
            prefix = f"{self.path.name}.backup.{phase}."
            pattern = re.compile(re.escape(prefix) + r"\d{8}T\d{12}Z-[0-9a-f]{32}$")
            historical = sorted(p for p in self.path.parent.glob(prefix + "*")
                                if pattern.fullmatch(p.name) and p.is_file() and not p.is_symlink())
            for obsolete in historical[:-BACKUP_HISTORY_LIMIT]:
                obsolete.unlink()
            return final
        except BaseException:
            log.exception("Could not preserve %s database recovery snapshot for %s", phase, self.path)
            self._remove_backup_staging(staging)
            raise

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
            # F-11: the rebuild is several statements on the shared
            # connection - a failure mid-way must not leave half a catalog for
            # the next unrelated commit to publish.
            try:
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
            except BaseException:
                self.conn.rollback()
                raise

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
            try:
                for table in ("objects", "advanced_objects"):
                    self.conn.executemany(
                        f"UPDATE {table} SET popularity = ? WHERE id = ?",
                        [(s, o) for o, s in POPULARITY.items()])
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise
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
            try:
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
            except BaseException:
                self.conn.rollback()
                raise
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
                image_id = self._insert_image(rec)
                self.conn.commit()
                return image_id
            except BaseException:
                self.conn.rollback()
                raise

    def _insert_image(self, rec: dict) -> int:
        """Insert without committing; callers own the transaction and lock."""
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
        return c.lastrowid

    def apply_reindex_plan(self, expected_images: list[dict], additions: list[dict],
                           fingerprints: list[tuple], relinks: list[tuple], *,
                           cancel_check=None) -> None:
        """Apply a filesystem scan atomically if its entire input is unchanged.

        Fingerprints are (id, digest, size, mtime_ns); relinks additionally
        include the verified relative path after id. Relinks preserve all
        database-only metadata and the original record identity. Filesystem
        work stays outside the connection lock; this short transaction checks
        for concurrent edits before committing any scan result.
        """
        with self.lock:
            try:
                if cancel_check is not None:
                    cancel_check()
                self.conn.execute("BEGIN IMMEDIATE")
                current = [dict(r) for r in self.conn.execute("SELECT * FROM images ORDER BY id")]
                if current != expected_images:
                    raise RuntimeError("Image library changed during reindex; retry the scan")
                ids = {row["id"] for row in current}
                for img_id, digest, size, mtime_ns in fingerprints:
                    if cancel_check is not None:
                        cancel_check()
                    if img_id not in ids:
                        raise ValueError("Reindex fingerprint refers to an unknown image")
                    self.conn.execute(
                        "UPDATE images SET content_sha256=?,file_size=?,file_mtime_ns=? WHERE id=?",
                        (digest, size, mtime_ns, img_id))
                for img_id, file, digest, size, mtime_ns in relinks:
                    if cancel_check is not None:
                        cancel_check()
                    if img_id not in ids:
                        raise ValueError("Reindex repair refers to an unknown image")
                    file = safe_rel(file)
                    if file is None:
                        raise ValueError("Reindex repair has an unsafe stored path")
                    self.conn.execute(
                        "UPDATE images SET file=?,content_sha256=?,file_size=?,file_mtime_ns=? WHERE id=?",
                        (file, digest, size, mtime_ns, img_id))
                for rec in additions:
                    if cancel_check is not None:
                        cancel_check()
                    file = safe_rel(rec["file"])
                    if file is None:
                        raise ValueError("Reindex addition has an unsafe stored path")
                    self._insert_image({**rec, "file": file})
                if cancel_check is not None:
                    cancel_check()
                self.conn.commit()
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

        Unresolved imports use the same normalized-name identity as library
        grouping; resolved catalog objects remain separated by object_id.
        """
        with self.lock:
            if object_id is not None:
                rows = self.conn.execute(
                    "SELECT * FROM images WHERE object_id=? ORDER BY id",
                    (object_id,))
            else:
                from .imglib import name_key
                target = name_key(object_name)
                rows = self.conn.execute(
                    "SELECT * FROM images WHERE object_id IS NULL ORDER BY id")
                return [dict(r) for r in rows if name_key(r["object_name"]) == target]
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
        overwritten. Paths absent from that map remain unchanged; ``new_folder``
        is retained as a compatibility parameter and never guesses a move."""
        from .imglib import name_key          # local import: keeps the
        target = name_key(old_name)           # module import graph acyclic
        moved = moved or {}
        with self.lock:
            try:
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
                        new_file = safe_rel(moved[r["file"]])
                        if new_file is None:
                            raise ValueError("Rename publication has an unsafe stored path")
                    else:
                        # A name change is not evidence that this original
                        # moved. Only the filesystem publisher's explicit map
                        # may change a stored path.
                        new_file = r["file"]
                    if new_file != r["file"]:
                        cur.execute("UPDATE images SET file=? WHERE id=?",
                                    (new_file, r["id"]))
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    # ---------------- import history (batches) ----------------

    def add_import_batch(self, rec: dict) -> int:
        with self.lock:
            try:
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
            except BaseException:
                self.conn.rollback()
                raise

    def merge_import_batch(self, run_id: str, rec: dict) -> int:
        """Record a batch, merging into the row of the same import run
        (one upload action split into chunks = one history entry)."""
        with self.lock:
            try:
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
            except BaseException:
                self.conn.rollback()
                raise

    def import_batches(self, limit: int = 60) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM import_batches ORDER BY id DESC LIMIT ?",
                (max(1, limit),))]


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


