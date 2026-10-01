"""Writable/runtime path resolution (works both from source and PyInstaller exe)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# Opaque record ids (panorama ids, custom-object ids). Some routes interpolate
# an id into a FILE NAME, so an id must never be able to act as a path.
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def safe_token(value) -> str | None:
    """Canonical opaque id, or None when it could act as a path.

    Panorama ids come from `new_id()` (hex + digits) or from an imported bundle,
    and `POST /api/panorama/{pid}/image` builds a filename from the id. A crafted
    bundle carrying an id like `..\\evil` therefore made the crop tool write
    outside `data/panos` (and a sibling id crashed the route with a 500) even
    though the *file* value was validated. Ids are stored and compared, never
    interpreted: anything outside [A-Za-z0-9_-] is refused.
    """
    if value is None:
        return None
    text = str(value)
    return text if TOKEN_RE.match(text) else None


_WINDOWS_DEVICES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# SP-05: C0 controls (0x00-0x1f) and DEL. Windows rejects these in a filename
# (`OSError: [Errno 22] Invalid argument`) and normalizes nothing about them, so
# a stored record carrying one can never be written or opened - it only exists
# to abort a restore with a 500. NUL is covered by the same rule.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _segment_ok(part: str) -> bool:
    """False for a path segment Windows would silently rewrite.

    A segment that ends in a dot or a space, or that consists only of dots, is
    normalized away by the filesystem ("..." and ".. ." resolve to the parent
    directory, and `a. ` becomes `a`), so accepting it would let a stored value
    mean something different from what was validated. Device names (NUL, CON,
    COM1, ...) address devices rather than files. Control characters are
    rejected (SP-05): Windows cannot create such a file, so the record would be
    stored and then fail at every read/write with an OSError.
    """
    if _CONTROL_RE.search(part):
        return False
    if part != part.rstrip(". "):
        return False
    if not part.strip(". "):
        return False
    stem = part.split(".", 1)[0].lower()
    return stem not in _WINDOWS_DEVICES


def safe_rel(rel) -> str | None:
    """Canonicalize an *untrusted* relative path, or return None when unsafe.

    Used for every path that arrives from stored/imported JSON (a setup ZIP's
    `panoramas[].file` / `images[].file`), so a crafted backup can never plant
    a value that a later read/delete/export resolves outside its folder.

    Rejects: absolute POSIX paths, UNC paths, Windows drive-absolute
    ("C:/x") and drive-relative ("C:x") paths, any `..` segment, NUL bytes,
    empty values, and segments Windows would normalize (see `_segment_ok`).
    Backslashes are treated as separators (the app stores forward-slash
    relative paths) and `.`/duplicate separators are removed, so the returned
    value is a normalized, root-relative path.
    """
    if rel is None:
        return None
    text = str(rel)
    # SP-05: check the RAW value, before `.strip()`/separator normalization - a
    # control character that only survives as padding (\x0b, \t) would otherwise
    # be removed here and the stored value would differ from what was validated.
    if _CONTROL_RE.search(text):
        return None
    text = text.strip().replace("\\", "/")
    if not text:
        return None
    if text.startswith("/") or _DRIVE_RE.match(text):
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if not all(_segment_ok(p) for p in parts):
        return None
    return "/".join(parts)


def safe_join(root: Path | str, rel) -> Path | None:
    """Resolve `rel` inside `root`, or None when it escapes.

    The second line of defence behind `safe_rel`: even a path that passes the
    textual check must still resolve to a location under `root` - this also
    catches symlinks pointing outside the folder. Callers must treat None as
    "not found" and never fall back to the raw value.
    """
    clean = safe_rel(rel)
    if clean is None:
        return None
    try:
        base = Path(root).resolve()
        target = (base / clean).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        target.relative_to(base)
    except ValueError:
        return None
    if target == base:
        return None          # the "path" addressed the root itself, not a file
    return target


def app_base_dir() -> Path:
    """Directory containing writable data (next to exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory holding bundled read-only assets (static/)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def static_dir() -> Path:
    return resource_dir() / "static"


def data_dir() -> Path:
    d = app_base_dir() / "data"
    (d / "panos").mkdir(parents=True, exist_ok=True)
    return d


def window_placement_path() -> Path:
    """Machine-local window geometry, kept out of setup backups and the DB."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = (Path(local_app_data) / "NightSeam" if local_app_data else
            data_dir())
    root.mkdir(parents=True, exist_ok=True)
    path = safe_join(root, "window-placement.json")
    if path is None:
        raise ValueError("invalid local window-placement path")
    return path


def logs_dir() -> Path:
    d = app_base_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_archive_dir() -> Path:
    """Default folder for ingested image files: Documents (or home)."""
    home = Path.home()
    docs = home / "Documents"
    return (docs if docs.is_dir() else home) / "NightSeam Images"
