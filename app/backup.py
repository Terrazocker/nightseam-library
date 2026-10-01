"""Setup export / import as a single ZIP.

Covers settings, sites, rigs, panoramas (with images), custom targets and
the ingested images library (rows + files). Restoring rewrites the stored
files back into the configured image directory.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .database import (Database, import_bundle_shape, prepare_import_bundle,
                       finite_or_none)
from .paths import data_dir, safe_join, safe_rel
from .imglib import public_image_record
from .version import app_version

APP_VERSION = app_version()


def _finite_records(rows, fields):
    """Rows with non-finite numbers in `fields` nulled out (PY04-F2).

    `json.dumps` writes a bare `Infinity`/`NaN` token for a non-finite float,
    which no strict JSON parser accepts - so a library that already carries
    such a row (however it got there) produced an export that could not be
    re-imported. Emitting NULL for an unusable number keeps the user's own
    export importable and can never restore the corruption."""
    out = []
    for row in rows:
        rec = dict(row)
        for key in fields:
            if key in rec:
                rec[key] = finite_or_none(rec[key])
        out.append(rec)
    return out


def _safe_join(root: Path, rel: str) -> Path | None:
    """Join rel onto root, refusing anything that escapes the root.

    Thin alias of `paths.safe_join` (kept because tests and the export path
    call it by this name); the implementation is shared with the server's
    read/delete routes so both sides of a restore agree on what is safe."""
    return safe_join(root, rel)


MAX_MEMBER_BYTES = 1 << 30          # 1 GiB per zip member (same cap as ingest)
# SP-01: the 1 GiB member cap bounds ONE member, so a small ZIP could still
# expand to terabytes of writes into data/panos + the archive (measured 996:1
# amplification with the default limits). This is the budget for everything the
# restore writes; a legitimate setup ZIP is far below it (the export is one
# image library, and the upload itself is capped at 2 GiB compressed).
MAX_RESTORE_BYTES = 4 << 30         # 4 GiB of restored payload, total
MAX_RESTORE_FILES = 5000            # and a matching bound on the record count


def _reject_oversized_bundle(panos: list, images: list,
                             zf: zipfile.ZipFile) -> None:
    """SP-01: refuse the whole restore when the declared payloads exceed the
    budget, BEFORE anything is written or the database is replaced.

    The zip's own directory records each member's uncompressed `file_size`, so a
    100:1 deflate bomb is visible here without decompressing a byte - and the
    per-restore budget is what the previous code lacked entirely: it capped one
    member at 1 GiB and never the sum, so a 42 KB ZIP could write 40 MiB and a
    2 GiB upload could fill the disk."""
    declared = 0
    for label, records in (("panos", panos), ("library", images)):
        for rec in records:
            rel = safe_rel(rec.get("file"))
            if rel is None:
                continue
            try:
                declared += zf.getinfo(f"{label}/{rel}").file_size
            except KeyError:
                continue
    if declared > MAX_RESTORE_BYTES:
        raise ValueError(
            f"setup bundle expands to more than "
            f"{MAX_RESTORE_BYTES // (1 << 30)} GB of restored files; refusing")
    if len(panos) + len(images) > MAX_RESTORE_FILES:
        raise ValueError(
            f"setup bundle carries more than {MAX_RESTORE_FILES} records; "
            "refusing")


def _read_member(zf: zipfile.ZipFile, name: str,
                 limit: int = MAX_MEMBER_BYTES) -> bytes:
    """Read one zip member with a hard size cap.

    `zipfile.read()` materialises a member whole, so one crafted member (a
    deflate bomb) could exhaust RAM even though the upload itself is capped.
    Raises ValueError when the member is too large."""
    info = zf.getinfo(name)          # KeyError -> caller skips the member
    if info.file_size > limit:
        raise ValueError(f"zip member too large: {name}")
    out = bytearray()
    with zf.open(info) as src:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            out += chunk
            if len(out) > limit:
                raise ValueError(f"zip member too large: {name}")
    return bytes(out)


def _export_zip_to(db: Database, target) -> int:
    """Write the setup ZIP to any writable file object; returns byte count."""
    with db.archive_lock:
        return _export_zip_to_locked(db, target)


def _export_zip_to_locked(db: Database, target) -> int:
    setup = {
        "app": "NightSeam",
        "version": APP_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "settings": db.all_settings(),
        "sites": db.sites(),
        "rigs": db.rigs(),
        "panoramas": db.panoramas(),
        "customs": _finite_records(db.customs(), ("ra_hours", "dec_deg", "vmag")),
        "images": _finite_records([public_image_record(im) for im in db.images()],
                                  ("ra_hours", "dec_deg", "exposure")),
    }
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("setup.json", json.dumps(setup, indent=1))
        for p in setup["panoramas"]:
            f = _safe_join(data_dir() / "panos", p["file"])
            if f and f.is_file():
                z.write(f, f"panos/{p['file']}")
        image_dir = Path(db.image_dir())
        for img in setup["images"]:
            src = _safe_join(image_dir, img["file"])
            if src and src.is_file():
                z.write(src, f"library/{img['file']}")
    return target.tell()


def build_export_zip(db: Database) -> bytes:
    """The setup ZIP as bytes (kept for the HTTP download + tests)."""
    buf = io.BytesIO()
    _export_zip_to(db, buf)
    return buf.getvalue()


def _write_safety_copy(db: Database, dest: Path) -> tuple[str | None, str | None]:
    """Best-effort streaming safety copy of the CURRENT setup to `dest`.

    Streams the ZIP straight to disk (never a second full copy in RAM), so
    even a multi-GB image library cannot double the import's memory peak.
    Returns `(path, None)` on success and `(None, reason)` on failure.

    PY-02: the failure used to be a bare `None`, indistinguishable in the summary
    the client sees from "not attempted" - so the documented recovery promise
    ("before touching anything, the CURRENT setup is written to
    `<db>.backup-auto.zip`") could be absent with no signal at all."""
    try:
        with open(dest, "wb") as f:
            _export_zip_to(db, f)
        return str(dest), None
    except Exception as exc:  # noqa: BLE001 - best-effort safety net
        return None, f"{type(exc).__name__}: {exc}"


class _PayloadStage:
    """Temporary payloads plus a rollback journal for the files they replace.

    BUG-001: `apply_import_zip` wrote every payload into place BEFORE
    `Database.import_bundle` validated the bundle, so a bundle with a valid
    colliding payload and one invalid record left the user's file replaced
    ("ORIGINAL" -> "REPLACED") while the error said only that the import failed.
    A restore now has three phases, and no user file changes in the first two:

      1. `stage()` writes the member into `<target>.import-<n>.part`, i.e. onto
         the DESTINATION filesystem (so the later swap is one atomic rename, even
         across drives/temp dirs), and - before the first stage for that target -
         copies any existing file of that name into the private journal directory
         beside it.
      2. `Database.import_bundle` replaces the rows in ONE transaction.
      3. `commit()` swaps the staged files in and drops the journal.

    Any exception before `commit()` returns runs `rollback()`, which puts every
    journalled file back where it was and deletes every staged file - so a
    refused or failed restore leaves the payload tree byte-identical. The journal
    is a directory (`payload.journal`) rather than hidden copies in the target
    folder, so a crashed restore leaves ONE directory to clean up next to the
    files, never a set of disguised `.bak` payloads the pano/library readers
    could pick up.

    The `.part` shape is the one PY-03 already used, so a concurrent reader
    (an image request, a reindex) never sees a half-copied payload.
    """

    def __init__(self, pano_root: Path, image_root: Path):
        self.pano_root = Path(pano_root)
        self.image_root = Path(image_root)
        self.journal = self.pano_root / "payload.journal"
        self._seq = 0
        self.staged: list[tuple[Path, Path]] = []     # (staged file, target)
        self.backups: list[tuple[Path, Path]] = []    # (original, journal copy)
        self._seen: set[str] = set()

    def _snapshot(self, target: Path) -> None:
        """Copy an existing payload into the journal without hiding the original.

        `_reject_duplicate_paths` has already refused a bundle that maps two
        records onto one path, so this can never run twice for one target."""
        key = str(target)
        if key in self._seen:
            return
        self._seen.add(key)
        if not target.is_file():
            return
        self.journal.mkdir(parents=True, exist_ok=True)
        saved = self.journal / f"{len(self.backups):05d}-{target.name}"
        shutil.copy2(target, saved)
        self.backups.append((target, saved))

    def stage(self, zf: zipfile.ZipFile, member: str, target: Path,
              budget: int) -> int:
        """Stream one member into a temp file; 0 when the member is absent.

        `budget` is the REMAINING SP-01 byte budget of the whole restore, so a
        crafted bundle cannot spend it all on one member. Raises ValueError when
        the member is larger than the budget."""
        try:
            info = zf.getinfo(member)
        except KeyError:
            return 0                    # no payload for that record: fine
        if info.file_size > budget:
            raise ValueError(f"zip member too large: {member}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._snapshot(target)
        self._seq += 1
        staged = target.with_name(f"{target.name}.import-{self._seq}.part")
        written = 0
        try:
            with zf.open(info) as src, open(staged, "wb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    # the budget bounds what is really written, not what the
                    # zip directory declares
                    if written > budget:
                        raise ValueError(f"zip member too large: {member}")
                    out.write(chunk)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        if not written:
            # an empty member is still "that record has a payload" for the
            # summary, so keep the (empty) file rather than losing the row
            self.staged.append((staged, target))
            return 1
        self.staged.append((staged, target))
        return written

    def commit(self) -> None:
        """Swap the staged payloads in, then drop the journal."""
        for staged, target in self.staged:
            staged.replace(target)
        self.discard()

    def discard(self) -> None:
        """Forget the journal (the restore succeeded, or was never started)."""
        shutil.rmtree(self.journal, ignore_errors=True)

    def rollback(self) -> None:
        """Restore every payload this stage touched; never raises."""
        for staged, _target in self.staged:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
        for target, saved in self.backups:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(saved), str(target))
            except OSError:
                pass
        self.staged.clear()
        self.backups.clear()
        self.discard()


def _write_payloads(zf: zipfile.ZipFile, accepted: dict, stage: _PayloadStage,
                    budget: int) -> tuple[int, int]:
    """Stage the payload files for the records `import_bundle` ACCEPTED.

    PY-06/SP-05: the loop used to iterate the RAW bundle list, so a record whose
    id failed `safe_token` never became a row but still had its payload written -
    an orphan file in `data/panos`, invisible and never cleaned, while the
    summary reported the pair `panoramas: 0, pano_images: 1`. Driving both sides
    from one accepted set is what keeps the count and the disk in agreement.

    BUG-001: nothing is swapped into place here - `stage` returns a journal the
    caller commits AFTER the database transaction. Returns
    `(panorama files, library files)`; `budget` is the remaining SP-01 byte
    budget, spent across both loops."""
    pano_files = 0
    for rec in accepted.get("panoramas", []):
        target = safe_join(stage.pano_root, rec["file"])
        if target is None:          # safe_rel already passed; belt and braces
            continue
        spent = stage.stage(zf, f"panos/{rec['file']}", target, budget)
        if spent:
            budget -= spent
            pano_files += 1

    image_files = 0
    stage.image_root.mkdir(parents=True, exist_ok=True)
    for rec in accepted.get("images", []):
        target = safe_join(stage.image_root, rec["file"])
        if target is None:
            continue
        spent = stage.stage(zf, f"library/{rec['file']}", target, budget)
        if spent:
            budget -= spent
            image_files += 1
    return pano_files, image_files


def apply_import_zip(db: Database, blob: bytes | Path,
                     summary: dict | None = None) -> dict:
    """Serialize the complete restore with every archive/payload mutation."""
    with db.archive_lock:
        return _apply_import_zip_locked(db, blob, summary)


def _apply_import_zip_locked(db: Database, blob: bytes | Path,
                             summary: dict | None = None) -> dict:
    """Replace all user data from an exported ZIP. Raises ValueError on
    malformed input. Returns a summary dict.

    `blob` may be raw zipped bytes or a path to a zip file on disk (the
    server spools large uploads to a temp file so the whole import never
    has to live in RAM twice). `summary` is an optional out-parameter: the
    caller receives the SAME dict, so a failure raised after the database was
    replaced still leaves `restore_incomplete` with it (PY-03).

    Before touching anything, the CURRENT setup is written to
    ``<db>.backup-auto.zip`` next to the database, so an interrupted or
    failed restore never destroys the pre-import data (it can be re-imported
    manually). The safety copy is best-effort: a failure to write it must
    not block the user's deliberate restore.

    The payload files are written BEFORE the database is replaced (PY-03): a
    bundle the restore cannot finish therefore leaves the database untouched
    (the restore is effectively atomic), and a response that names a failure can
    never be read as "nothing happened" after the library was already replaced.
    Should a failure happen after the replace anyway, the summary carries
    `restore_incomplete` so the client can say so instead of the caller's
    rollback repainting an already-replaced database.

    BUG-001: "the payloads are written first" is not enough on its own - the
    bundle used to be validated INSIDE `import_bundle`, i.e. after the payload
    files had already been replaced, so a refused restore still overwrote them.
    The order is now: structural + field validation (`import_bundle_shape`,
    `prepare_import_bundle`), the safety copy, then the payloads STAGED (never
    swapped in), then the database transaction, then the swap. Any failure
    before the swap runs the journal's rollback, so a rejected bundle leaves the
    database AND the payload tree exactly as they were."""
    zf = None
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob) if isinstance(blob, bytes)
                             else blob)
        setup = json.loads(_read_member(zf, "setup.json", 64 << 20)
                           .decode("utf-8"))
        # BUG-004: prove the STRUCTURE before anything reads a field from it. A
        # scalar top level or `panoramas: ["not-an-object"]` used to escape as an
        # AttributeError -> HTTP 500, and (for panoramas/images) only after the
        # safety copy had been written.
        import_bundle_shape(setup)
    except Exception as exc:  # noqa: BLE001
        if zf is not None:
            zf.close()
        raise ValueError(f"Not a valid NightSeam export: {exc}") from None
    if setup.get("app") != "NightSeam":
        zf.close()
        raise ValueError("This zip is not a NightSeam setup export")

    safety_path = db.path.with_suffix(".backup-auto.zip")
    replaced = False            # did import_bundle replace the database?
    out = summary if summary is not None else {}
    stage: _PayloadStage | None = None
    # The ZipFile handle must be closed on EVERY path: on Windows an open
    # handle makes the route's spool unlink fail, turning a clean 400 into a
    # 500 (FIX-4 exposes this through the new validation errors).
    try:
        sites = setup.get("sites", [])
        rigs = setup.get("rigs", [])
        panos = setup.get("panoramas", [])
        customs = setup.get("customs", [])
        images = setup.get("images", [])
        settings = setup.get("settings", {})
        # SP-01: refuse an oversized restore BEFORE the safety copy and before
        # anything is written.
        _reject_oversized_bundle(panos, images, zf)
        safety, safety_error = _write_safety_copy(db, safety_path)

        # `image_dir` is a FILESYSTEM ROOT, and the bundle is untrusted: taking
        # it from the ZIP let a crafted "setup export" redirect the restore
        # (arbitrary write, arbitrary name/content, mkdir -p) and, via the
        # export path, read any directory the app can reach. The documented
        # behaviour is that the library is restored into the CURRENTLY
        # CONFIGURED archive folder. The replace below wipes the settings
        # table, so the CURRENT value has to be re-injected (dropping the key
        # instead would reset the user's archive to the default) - and the
        # imported value is reported back to the user.
        current_image_dir = db.image_dir()
        imported_image_dir = settings.pop("image_dir", None)
        # ALWAYS re-inject the local value: the replace below wipes the
        # settings table, so simply dropping the key would reset the user's
        # archive folder to the default (and a restore that omits the key
        # would silently move the library into ~/Documents).
        settings["image_dir"] = current_image_dir

        # BUG-001/BUG-002/BUG-004: ONE side-effect-free validation pass, and it
        # runs BEFORE a single payload byte is staged. Everything that used to
        # fail inside the transaction (a hostile custom id, a NULL name, a
        # duplicate id, a non-finite number) fails here now, while the payload
        # tree is still untouched.
        accepted = prepare_import_bundle(settings, sites, rigs, panos,
                                         customs, images)

        # PY-03 + BUG-001: the payloads are STAGED first, and only for the
        # records `import_bundle` will accept (PY-06/SP-05 - the SAME predicate).
        # A write failure here leaves the database untouched and is rolled back,
        # so "the import failed" and "nothing was replaced" are one statement.
        stage = _PayloadStage(data_dir() / "panos", Path(current_image_dir))
        try:
            pano_files, image_files = _write_payloads(
                zf, accepted, stage, MAX_RESTORE_BYTES)
        except BaseException:
            stage.rollback()
            raise
        # import_bundle drops records whose stored path escapes its folder and
        # reports how many, so the summary below never over-counts a restore.
        store: dict = {}
        try:
            db.import_bundle(settings, sites, rigs, panos,
                             customs=customs, images=images, accepted=store)
        except (sqlite3.Error, AttributeError, TypeError) as exc:
            # PY-04: a bundle that gets past the read and then fails inside the
            # transaction (NULL name, non-integer site id, duplicate ids) used to
            # surface as an opaque 500 + traceback. It is malformed input, so it
            # is a ValueError -> the route's documented 400. The transaction has
            # rolled back; nothing was replaced.
            raise ValueError(f"malformed setup bundle: {exc}") from None
        except BaseException:
            # BUG-001: the database transaction rolled back, and the payloads
            # have not been swapped in yet - put the tree back as it was.
            stage.rollback()
            raise
        # The database is committed; the payloads become visible in one rename
        # each. A failure inside the swap is the ONLY remaining window, and it
        # restores every payload (including the ones already swapped).
        try:
            stage.commit()
        except OSError:
            stage.rollback()
            raise
        replaced = True

        out.update({"ok": True, "sites": len(sites), "rigs": len(rigs),
                    "panoramas": len(store.get("panoramas", [])),
                    "pano_images": pano_files,
                    "customs": len(customs),
                    "images": len(store.get("images", [])),
                    "library_files": image_files,
                    "auto_backup": safety,
                    # PY-02: non-null ONLY when the safety copy could not be
                    # written, so the client can say so instead of the
                    # documented promise silently degrading to "recoverable if
                    # it worked".
                    "auto_backup_error": safety_error})
        if imported_image_dir:
            out["image_dir_ignored"] = str(imported_image_dir)[:200]
        return out
    finally:
        if replaced and sys.exc_info()[0] is not None:
            # A step AFTER the replace failed: the caller's summary (this same
            # object) carries the marker, so a failed restore can never be
            # reported as a no-op. A successful restore never sets it - the
            # route's error path is the only reader.
            out["restore_incomplete"] = True
        if stage is not None and not replaced:
            # Belt and braces: every failure path above already rolled back, so
            # this only removes an empty journal - but a restore may NEVER leave
            # `.part` files or a journal directory behind, whatever went wrong.
            stage.rollback()
        zf.close()
