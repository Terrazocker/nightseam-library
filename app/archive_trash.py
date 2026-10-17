"""Recovery-only support for exposures retained by the retired trash feature."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, UTC
from functools import lru_cache
import ctypes
import json
import logging
import os
from pathlib import Path
import stat
import threading
import uuid

from .archive_health import _contained, _inspect, _signature, _PINNED_PATHS
from .paths import safe_rel, safe_token, safe_join
from .trash_paths import TRASH_FOLDER, MARKER, managed_trash

log = logging.getLogger(__name__)
_PINNED_LINEAGE = threading.local()


class PinnedParents:
    """Reuse live Windows directory pins across workers in one operation."""
    def __init__(self, locations):
        self.stack, self.parents = ExitStack(), set()
        self.folders, self.targets, self.folder_ids = {}, {}, {}
        try:
            if os.name == 'nt':
                for root, relative, identity in locations:
                    target = _contained(root, relative, identity)
                    key = root, identity, target.parent
                    if key not in self.parents:
                        self.stack.enter_context(_parent(root, relative, identity))
                        self.parents.add(key)
                    clean = safe_rel(relative)
                    parent = clean.rsplit('/', 1)[0] if '/' in clean else ''
                    self.folders[root, identity, parent] = target.parent
                    info = target.parent.lstat()
                    self.folder_ids[root, identity, parent] = info.st_dev, info.st_ino
                    self.targets[root, identity, clean] = target
        except BaseException:
            self.close()
            raise

    @contextmanager
    def use(self):
        previous = getattr(_PINNED_LINEAGE, 'parents', ())
        previous_paths = getattr(_PINNED_PATHS, 'context', None)
        _PINNED_LINEAGE.parents = self.parents
        _PINNED_PATHS.context = self
        try:
            yield
        finally:
            _PINNED_LINEAGE.parents = previous
            _PINNED_PATHS.context = previous_paths

    def target(self, root, relative, identity):
        clean = safe_rel(relative)
        if clean is None:
            raise ValueError('Invalid relative archive path.')
        parent = clean.rsplit('/', 1)[0] if '/' in clean else ''
        folder = self.folders.get((root, identity, parent))
        if folder is None:
            return None
        info = folder.lstat()
        if (info.st_file_attributes & 0x400 or not stat.S_ISDIR(info.st_mode) or
                (info.st_dev, info.st_ino) != self.folder_ids[root, identity, parent]):
            raise ValueError('The pinned archive directory changed.')
        lexical = folder / clean.rsplit('/', 1)[-1]
        try:
            leaf_info = lexical.lstat()
        except FileNotFoundError:
            leaf_info = None
        if leaf_info is not None and leaf_info.st_file_attributes & 0x400:
            raise ValueError('Linked archive paths are not followed.')
        key = root, identity, clean
        target = self.targets.get(key)
        if target is None:
            target = safe_join(root, clean)
            if target is None or target != lexical:
                raise ValueError('The archive path changed.')
            self.targets[key] = target
        return target

    def close(self):
        self.stack.close()
        self.parents.clear()
        self.folders.clear()
        self.targets.clear()
        self.folder_ids.clear()


class TrashUnavailable(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _checkpoint(cancelled):
    if cancelled and cancelled():
        raise TrashUnavailable("Trash operation stopped during application shutdown.")


@contextmanager
def _transaction(db):
    with db.lock:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            yield db.conn
            db.conn.commit()
        except BaseException:
            db.conn.rollback()
            raise


def _entries(db):
    with db.lock:
        return [dict(row) for row in db.conn.execute("SELECT * FROM image_trash ORDER BY trashed_at DESC")]


def _snapshot(db, image_id):
    with db.lock:
        image = db.conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        integrity = db.conn.execute("SELECT * FROM image_integrity WHERE image_id=?", (image_id,)).fetchone()
        return (dict(image) if image else None, dict(integrity) if integrity else None)


def _decoded(db, entry):
    if safe_token(entry["token"]) != entry["token"]:
        raise TrashUnavailable("Unsafe trash record token.")
    image, integrity = json.loads(entry["image_json"]), json.loads(entry["integrity_json"])
    with db.lock:
        columns = {row[1] for row in db.conn.execute("PRAGMA table_info(images)")}
        baseline_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(image_integrity)")}
    if (not isinstance(image, dict) or set(image) != columns or
            type(image.get("id")) is not int or image["id"] <= 0 or safe_rel(image.get("file")) is None or
            (integrity is not None and (not isinstance(integrity, dict) or set(integrity) != baseline_columns or integrity["image_id"] != image["id"]))):
        raise TrashUnavailable("Trash recovery metadata is invalid; preserve its retained files.")
    return image, integrity


def _root(db, entry=None):
    configured = db.image_dir()
    original = Path(configured)
    if original.is_symlink() or original.is_junction():
        raise TrashUnavailable("Linked archive folders cannot be used for trash.")
    root = original.resolve(strict=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise TrashUnavailable("The archive folder is unavailable.")
    identity = (info.st_dev, info.st_ino)
    if entry and (configured != entry["archive"] or tuple(map(str, identity)) != (entry["root_dev"], entry["root_ino"])):
        raise TrashUnavailable("Select the original archive folder before restoring this exposure.")
    return configured, root, identity


def _payload(entry):
    token = safe_token(entry["token"])
    if token is None:
        raise TrashUnavailable("Unsafe trash record token.")
    return f"{TRASH_FOLDER}/{token}/payload.bin"


@contextmanager
def _windows_directory_pin(folder):
    """Exclude directory deletion/rename with the existing lineage pin flags."""
    if folder.is_symlink() or folder.is_junction():
        raise TrashUnavailable('Linked archive paths cannot be moved.')
    kernel = _windows_directory_api()
    handle = kernel.CreateFileW(str(folder), 0x80000000, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
            raise TrashUnavailable('An archive directory changed during the operation.')
        yield
    finally:
        kernel.CloseHandle(handle)


@lru_cache(maxsize=1)
def _windows_directory_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel


@contextmanager
def _parent(root, relative, identity, *, held=None, max_new=None):
    """Pin lineage, optionally borrowing the caller's live Windows directories.

    Returned new directory keys stay provisional: the caller publishes them
    only if it retains this context for the same lifetime as its held keys.
    max_new refuses a Windows suffix before opening beyond its caller's budget.
    POSIX callers still receive their anchored directory descriptor.
    """
    target = _contained(root, relative, identity)
    if os.name == 'nt' and held is None and (root, identity, target.parent) in getattr(_PINNED_LINEAGE, 'parents', ()):
        yield None
        return
    with ExitStack() as stack:
        if os.name == "nt":
            opened = set() if held is not None else None
            count = 0
            for folder in [*reversed(target.parent.parents), target.parent]:
                if held is not None and folder in held:
                    continue
                if max_new is not None and count >= max_new:
                    raise OSError('Request directory pin budget exhausted.')
                stack.enter_context(_windows_directory_pin(folder))
                count += 1
                if opened is not None:
                    opened.add(folder)
            _contained(root, relative, identity)
            # Caller publishes these keys only if it retains this context. A
            # refused candidate releases its provisional suffix immediately.
            yield opened
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(root, flags)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != identity:
                raise TrashUnavailable("The archive folder changed.")
            for part in safe_rel(relative).split("/")[:-1]:
                child = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, child)
                fd = child
            _contained(root, relative, identity)
            yield fd


def _move(root, source, destination, identity):
    """Atomic rename that never overwrites an existing destination."""
    start = _contained(root, source, identity)
    end = _contained(root, destination, identity)
    with _parent(root, source, identity) as source_fd, _parent(root, destination, identity) as destination_fd:
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            kernel.MoveFileExW.restype = ctypes.c_int
            if not kernel.MoveFileExW(str(start), str(end), 0x8):  # WRITE_THROUGH, never REPLACE_EXISTING
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            libc = ctypes.CDLL(None, use_errno=True)
            rename = getattr(libc, "renameat2", None)
            if rename is None:
                raise TrashUnavailable("This platform does not support safe no-overwrite trash moves.")
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            if rename(source_fd, os.fsencode(start.name), destination_fd, os.fsencode(end.name), 1):
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(end))
        _contained(root, destination, identity)


def _mkdir(root, relative, identity, exclusive=False):
    if relative == '.':
        return
    current = []
    for part in safe_rel(relative).split("/"):
        current.append(part)
        target = _contained(root, "/".join(current), identity)
        with _parent(root, "/".join(current), identity) as fd:
            try:
                if fd is None:
                    target.mkdir()
                else:
                    os.mkdir(target.name, dir_fd=fd)
            except FileExistsError:
                if exclusive and len(current) == len(safe_rel(relative).split('/')):
                    raise
                pass
        if not target.is_dir():
            raise TrashUnavailable("An archive folder is occupied by a file.")
        _contained(root, "/".join(current), identity)


def _verify(root, relative, identity, entry, cancelled=None):
    info, digest = _inspect(root, relative, identity, True, lambda: _checkpoint(cancelled), lambda _: None)
    if digest != entry["payload_sha256"] or info.st_size != entry["payload_size"]:
        raise TrashUnavailable("The retained exposure changed. Preserve it and inspect the archive before restoring.")
    if info.st_nlink != 1:
        raise TrashUnavailable('Hard-linked retained exposures cannot be moved safely.')
    return info


@contextmanager
def _file(root, relative, identity, mode):
    target = _contained(root, relative, identity)
    with _parent(root, relative, identity) as parent_fd:
        before = target.stat() if mode == 'rb' else None
        flags = os.O_RDONLY if mode == 'rb' else os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(target if parent_fd is None else target.name, flags, 0o600,
                     **({} if parent_fd is None else {'dir_fd': parent_fd}))
        with os.fdopen(fd, mode) as file:
            opened = os.fstat(file.fileno())
            if not stat.S_ISREG(opened.st_mode) or (before and _signature(before, cross_source=True) != _signature(opened, cross_source=True)):
                raise TrashUnavailable('Trash metadata changed while opening it.')
            yield file
            after = _contained(root, relative, identity).stat()
            if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
                raise TrashUnavailable('Trash metadata changed during the operation.')


def _manifest(root, identity, entry):
    target = _contained(root, f"{TRASH_FOLDER}/{entry['token']}/metadata.json", identity)
    if not target.is_file() or target.stat().st_size > 1024 * 1024:
        raise TrashUnavailable("Trash recovery metadata is missing or invalid.")
    with _file(root, f"{TRASH_FOLDER}/{entry['token']}/metadata.json", identity, 'rb') as file:
        content = file.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise TrashUnavailable('Trash recovery metadata exceeds its size limit.')
        saved = json.loads(content)
    expected = dict(format='nightseam-exposure-trash', version=1,
                    **{key: value for key, value in entry.items() if key != 'recovery_error'})
    expected['state'] = 'pending_trash'
    if saved != expected:
        raise TrashUnavailable("Trash recovery metadata changed. Preserve the retained exposure and inspect Trash.")


def _aliases(db, image):
    key = safe_rel(image["file"]).casefold()
    if any(row["id"] != image["id"] and (safe_rel(row["file"]) or "").casefold() == key for row in db.images()):
        raise TrashUnavailable("Another catalog record uses this file. Repair shared paths before moving it.")


def _publish_restore(db, entry, image, integrity):
    with _transaction(db) as conn:
        if db.image_dir() != entry["archive"] or db.image(image["id"]) is not None:
            raise TrashUnavailable("The original catalog ID is already occupied.")
        _aliases(db, image)
        if db.images_with_digest(entry['payload_sha256']):
            raise TrashUnavailable("Identical exposure bytes are already in the library. Restore will not add a duplicate.")
        for table, record in (("images", image), ("image_integrity", integrity)):
            if record is not None:
                columns = ','.join('"'+column.replace('"','""')+'"' for column in record)
                conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in record)})", tuple(record.values()))
        conn.execute("UPDATE image_trash SET state='restored',recovery_error=NULL WHERE token=?", (entry["token"],))


def _error(db, entry, error):
    with _transaction(db) as conn:
        conn.execute("UPDATE image_trash SET recovery_error=? WHERE token=?", (str(error), entry["token"]))
    log.warning("Trash recovery required for %s: %s", entry["token"], error)


def _discard_intent(db, root, identity, entry):
    folder = f"{TRASH_FOLDER}/{entry['token']}"
    target = _contained(root, folder, identity)
    if not target.exists():
        with _transaction(db) as conn:
            conn.execute("DELETE FROM image_trash WHERE token=?", (entry["token"],))
        return
    metadata = _contained(root, f"{folder}/metadata.json", identity)
    with _parent(root, f"{folder}/metadata.json", identity) as fd:
        try:
            if fd is None:
                metadata.unlink()
            else:
                os.unlink(metadata.name, dir_fd=fd)
        except FileNotFoundError:
            pass
    if target.exists():
        with _parent(root, folder, identity) as fd:
            if fd is None:
                target.rmdir()
            else:
                os.rmdir(target.name, dir_fd=fd)
        # Refuse unexpected files; never recursively delete recovery evidence.
    with _transaction(db) as conn:
        conn.execute("DELETE FROM image_trash WHERE token=?", (entry["token"],))


def _recover(db, entry, cancelled=None):
    image, integrity = _decoded(db, entry)
    _, root, identity = _root(db, entry)
    original = _contained(root, image["file"], identity)
    retained = _contained(root, _payload(entry), identity)
    if original.exists() == retained.exists():
        raise TrashUnavailable("Interrupted trash operation has both or neither file locations. Preserve both and inspect Trash.")
    if entry["state"] == "pending_trash":
        if _snapshot(db, image["id"]) != (image, integrity):
            raise TrashUnavailable("The catalog changed during interrupted trash recovery.")
        if retained.exists():
            _manifest(root, identity, entry)
            _verify(root, _payload(entry), identity, entry, cancelled)
            _mkdir(root, str(Path(image["file"]).parent).replace('\\', '/'), identity)
            _move(root, _payload(entry), image["file"], identity)
        # With no retained payload, no removal needs undoing. Leave any external
        # edits at the original location for the existing integrity baseline.
        _discard_intent(db, root, identity, entry)
    else:
        if db.image(image["id"]) is not None:
            raise TrashUnavailable("The catalog changed during interrupted restore recovery.")
        if original.exists():
            _verify(root, image["file"], identity, entry, cancelled)
            _move(root, image["file"], _payload(entry), identity)
        else:
            _verify(root, _payload(entry), identity, entry, cancelled)
        with _transaction(db) as conn:
            conn.execute("UPDATE image_trash SET state='trashed',recovery_error=NULL WHERE token=?", (entry["token"],))


def recover_pending(db, cancelled=None):
    """Reconcile only the configured archive; failed recovery remains visible."""
    if not pending_current(db) or not db.archive_lock.acquire(blocking=False):
        return
    try:
        for entry in _entries(db):
            if entry["state"].startswith("pending_") and entry["archive"] == db.image_dir():
                try:
                    _checkpoint(cancelled)
                    _recover(db, entry, cancelled)
                except (OSError, ValueError, RuntimeError) as error:
                    _error(db, entry, error)
    finally:
        db.archive_lock.release()


def pending_current(db):
    return db.pending_trash()


@contextmanager
def _operation(db, cancelled):
    if not db.archive_lock.acquire(blocking=False):
        raise TrashUnavailable("An archive operation is running. Wait for it to finish, then retry.")
    try:
        _checkpoint(cancelled)
        recover_pending(db, cancelled)
        if pending_current(db):
            raise TrashUnavailable("An interrupted trash operation needs recovery. Preserve the affected files and inspect them with scripts/restore_legacy_trash.py.")
        yield
    finally:
        db.archive_lock.release()


def restore_image(db, token, cancelled=None):
    if safe_token(token) != token:
        raise TrashUnavailable("Invalid trash token.", 404)
    with _operation(db, cancelled):
        entry = next((item for item in _entries(db) if item['token'] == token), None)
        if entry is None:
            raise TrashUnavailable("Trash entry not found.", 404)
        if entry['state'] == 'restored':
            image_id = json.loads(entry['image_json'])['id']
            if db.image(image_id) is None:
                raise TrashUnavailable('This trash entry was already restored; use its newer trash entry if it was removed again.')
            return dict(ok=True, image_id=image_id)
        image, integrity = _decoded(db, entry)
        _, root, identity = _root(db, entry)
        _aliases(db, image)
        if db.image(image['id']) is not None or _contained(root, image['file'], identity).exists():
            raise TrashUnavailable("The original file or catalog ID is occupied. Restore will not replace it.")
        if db.images_with_digest(entry['payload_sha256']):
            raise TrashUnavailable("Identical exposure bytes are already in the library. Restore will not add a duplicate.")
        _verify(root, _payload(entry), identity, entry, cancelled)
        _manifest(root, identity, entry)
        _mkdir(root, str(Path(image['file']).parent).replace('\\','/'), identity)
        with _transaction(db) as conn:
            conn.execute("UPDATE image_trash SET state='pending_restore' WHERE token=?", (token,))
        entry['state'] = 'pending_restore'
        try:
            _checkpoint(cancelled)
            _move(root, _payload(entry), image['file'], identity)
            _verify(root, image['file'], identity, entry, cancelled)
            _checkpoint(cancelled)
            _publish_restore(db, entry, image, integrity)
        except BaseException as error:
            try:
                _recover(db, entry)
            except BaseException as recovery:
                _error(db, entry, recovery)
                raise TrashUnavailable("Restore failed and recovery is incomplete. Preserve the archive files and inspect scripts/restore_legacy_trash.py for recovery details.", 500) from error
            raise
        return dict(ok=True, image_id=image['id'])


def list_trash(db):
    # Listing is a metadata read. Opportunistic recovery never waits on a
    # thumbnail/scan; restore revalidates every row and file under the lock.
    recover_pending(db)
    result = []
    for entry in _entries(db):
        if entry['state'] == 'restored':
            continue
        image, _ = _decoded(db, entry)
        result.append(dict(token=entry['token'], image_id=image['id'], filename=image['orig_name'],
                           file=image['file'], archive=entry['archive'], trashed_at=entry['trashed_at'],
                           state=entry['state'], recovery_error=entry['recovery_error'],
                           can_restore=entry['state']=='trashed' and entry['archive']==db.image_dir()))
    return dict(items=result)
