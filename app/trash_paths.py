"""Reserved archive trash layout; only recognized managed folders are excluded."""
import os
from pathlib import Path

from .paths import safe_join

TRASH_FOLDER = ".nightseam-trash"
MARKER = b'{"format":"nightseam-exposure-trash","version":1}\n'


def managed_trash(root):
    folder = Path(root) / TRASH_FOLDER
    marker = safe_join(root, f"{TRASH_FOLDER}/format.json")
    if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
        return False
    raw_marker = folder / 'format.json'
    if marker is None or raw_marker.is_symlink() or raw_marker.is_junction():
        return False
    try:
        with marker.open("rb") as source:
            return source.read(len(MARKER) + 1) == MARKER
    except OSError:
        return False


def archive_paths(root):
    """Prune managed trash before reindex descends, keeping other paths visible."""
    managed = managed_trash(root)
    def refused(error):
        raise error
    for parent, directories, files in os.walk(root, followlinks=False, onerror=refused):
        if Path(parent) == root and managed:
            directories[:] = [name for name in directories if name != TRASH_FOLDER]
        for name in files:
            yield Path(parent) / name
