"""Import exposures straight from a folder chosen in the native picker.

WebView2 confirms every <input webkitdirectory> selection with its own upload
prompt and then sends every file over the loopback socket. The desktop bridge
instead registers the folder the user picked and hands the page an opaque
token; the page never names a source path. Import requests name files relative
to that folder, and the server reads them in place, read-only.
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from . import fits_metadata
from .ingest import _archive_io

MAX_FOLDER_FILES = 100_000
_MAX_FOLDERS = 32
_lock = threading.Lock()
_folders: dict[str, Path] = {}


def register(path) -> str | None:
    """Remember a folder the user picked; return its token, or None."""
    try:
        folder = Path(path).resolve()
    except (OSError, ValueError):
        return None
    if not folder.is_dir():
        return None
    token = secrets.token_hex(16)
    with _lock:
        while len(_folders) >= _MAX_FOLDERS:
            _folders.pop(next(iter(_folders)))
        _folders[token] = folder
    return token


def folder(token) -> Path | None:
    with _lock:
        return _folders.get(str(token or ""))


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _within(path, parent) -> bool:
    path, parent = _key(path), _key(parent)
    return path == parent or path.startswith(parent.rstrip("\\/") + os.sep)


def _link(entry: os.DirEntry) -> bool:
    is_junction = getattr(entry, "is_junction", None)
    return entry.is_symlink() or bool(is_junction and is_junction())


def scan(root: Path, archive: Path) -> dict:
    """List the folder's FITS exposures, subfolders included.

    Links and junctions are not followed, and an archive inside the folder is
    skipped so the catalog never re-imports its own files. A folder inside the
    archive is refused; subfolders that cannot be listed are counted.
    """
    archive = archive.resolve()
    if _within(root, archive):
        raise ValueError("This folder is inside your archive. Choose the folder your exposures were captured to.")
    files, unreadable, archive_skipped, pending = [], 0, False, [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                entries = sorted(entries, key=lambda entry: entry.name.casefold())
        except OSError:
            if current is root:
                raise
            unreadable += 1  # a subfolder this account cannot list
            continue
        for entry in entries:
            try:
                if _link(entry):
                    continue
                if entry.is_dir():
                    if _within(entry.path, archive):
                        archive_skipped = True
                    else:
                        pending.append(entry.path)
                    continue
                if not entry.is_file() or not fits_metadata.is_fits_name(entry.name):
                    continue
                size = entry.stat().st_size
            except OSError:
                continue  # vanished or unreadable while listing
            files.append({"rel": Path(entry.path).relative_to(root).as_posix(), "size": size})
            if len(files) > MAX_FOLDER_FILES:
                raise ValueError(f"This folder holds more than {MAX_FOLDER_FILES} exposures. Import its subfolders separately.")
    files.sort(key=lambda item: item["rel"].casefold())
    return {"name": root.name or str(root), "path": str(root), "files": files,
            "archive_skipped": archive_skipped, "unreadable_folders": unreadable}


def exposure(root: Path, archive: Path, rel) -> LocalExposure:
    """The source named by a scan's relative path; ValueError when it is not one."""
    parts = str(rel).replace("\\", "/").split("/") if isinstance(rel, str) and len(rel) <= 1024 else []
    if not parts or any(part in ("", ".", "..") or ":" in part or "\0" in part for part in parts):
        raise ValueError("Import file names must be paths inside the selected folder.")
    path = root.joinpath(*parts)
    # A junction inside the folder must not lead outside it or into the archive.
    resolved = path.resolve()
    if not _within(resolved, root) or _within(resolved, archive.resolve()):
        raise ValueError("Import file names must be paths inside the selected folder.")
    return LocalExposure(path)


class LocalExposure:
    """A source file that ingest_one reads like a spooled upload."""

    def __init__(self, path: Path):
        self.path = path
        self.filename = path.name
        self.size = None
        self.file = None

    def open_source(self) -> str | None:
        """Open the file for reading; return a per-file error, or None."""
        try:
            source = self.path.open("rb")
        except FileNotFoundError:
            return "Source file is no longer in the selected folder."
        except PermissionError:
            return "Cannot read source file (access denied or in use by another program)."
        except OSError as exc:
            return f"Cannot read source file: {exc.strerror or exc}"
        try:
            self.size = os.fstat(source.fileno()).st_size
        except OSError:
            source.close()
            return "Cannot read source file."
        self.file = source
        return None

    async def read(self, size: int = -1) -> bytes:
        return await _archive_io(self.file.read, size)

    def abandon(self) -> None:
        if self.file is not None:
            self.file.close()
