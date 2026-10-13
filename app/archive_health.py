"""Read-only, cancellable archive scans and explicitly confirmed resolutions."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from .paths import safe_join, safe_rel

log = logging.getLogger(__name__)
MAX_FINDINGS = 10_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ScanCancelled(Exception):
    pass


class ArchiveUnavailable(Exception):
    pass


class UnstableFile(ValueError):
    pass


def _signature(info, *, cross_source=False):
    # Windows Python 3.12 stat/fstat disagree on ctime (creation/change time).
    # Compare it only between observations from the same metadata source.
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return identity if cross_source and os.name == "nt" else (*identity, info.st_ctime_ns)


def _check_root(root, identity):
    try:
        info = root.stat()
        if root.is_symlink() or root.is_junction() or (info.st_dev, info.st_ino) != identity:
            raise ArchiveUnavailable
    except OSError:
        raise ArchiveUnavailable from None


def _contained(root, relative, identity):
    _check_root(root, identity)
    clean = safe_rel(relative)
    if clean is None:
        raise ValueError("The recorded path is not a safe relative archive path.")
    current = root
    for part in clean.split("/"):
        current /= part
        if current.is_symlink() or current.is_junction():
            raise ValueError("Linked archive paths are not followed.")
    target = safe_join(root, clean)
    if target is None or not target.is_relative_to(root):
        raise ValueError("The recorded path cannot be resolved inside the archive.")
    return target


def _directory_entries(root, relative, identity, checkpoint):
    """Anchor discovery rather than following independently checked names."""
    with ExitStack() as stack:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                            ctypes.c_void_p, wintypes.DWORD]
            class AttributeTag(ctypes.Structure):
                _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]
            folder = root / relative if relative else root
            # No FILE_SHARE_DELETE: pin the entire directory lineage against
            # renames/reparse replacement while Windows enumerates it.
            for directory in [*reversed(folder.parents), folder]:
                handle = kernel.CreateFileW(str(directory), 0x80, 3, None, 3,
                                            0x02000000 | 0x00200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                stack.callback(kernel.CloseHandle, handle)
                attributes = AttributeTag()
                if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if attributes.attributes & 0x400 or not attributes.attributes & 0x10:
                    raise ValueError("Linked directory paths are not followed.")
            _check_root(root, identity)
            iterator = stack.enter_context(os.scandir(folder))
            for entry in iterator:
                checkpoint()
                yield entry.name, entry.is_symlink() or Path(entry.path).is_junction(), entry.is_dir(follow_symlinks=False)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(root, flags)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != identity:
                raise ArchiveUnavailable
            for part in relative.split("/") if relative else ():
                child = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, child)
                fd = child
            iterator = stack.enter_context(os.scandir(fd))
            for entry in iterator:
                checkpoint()
                yield entry.name, entry.is_symlink(), entry.is_dir(follow_symlinks=False)


def _inspect(root, relative, identity, deep, checkpoint, read_progress):
    target = _contained(root, relative, identity)
    before = target.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("The recorded exposure is not a regular file.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(target, flags)
    with os.fdopen(fd, "rb") as source:
        checked = _contained(root, relative, identity)
        opened = os.fstat(source.fileno())
        if checked != target or _signature(opened, cross_source=True) != _signature(before, cross_source=True):
            raise UnstableFile("The path changed while opening it; it was not read.")
        observed = None
        if deep:
            digest = hashlib.sha256()
            while True:
                checkpoint()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                read_progress(len(chunk))
            observed = digest.hexdigest()
        checkpoint()
        after = os.fstat(source.fileno())
        current = _contained(root, relative, identity).stat()
        if _signature(opened) != _signature(after) or _signature(before) != _signature(current):
            raise UnstableFile("The exposure changed during inspection; no verification result is valid.")
        return after, observed


def scan_archive(db, mode, cancel, publish):
    report = dict(mode=mode, archive=None, phase="waiting", total=0, processed=0,
        bytes_read=0, verified=0, metadata_unchanged=0, unverified=0, findings=[],
        finding_counts={}, omitted_findings=0, baseline_sources={}, archive_available=False)
    counts = Counter()
    last_update = 0.0

    def progress(force=False):
        nonlocal last_update
        if force or time.monotonic() - last_update >= .2:
            last_update = time.monotonic()
            publish({**report, "findings": list(report["findings"])})

    def checkpoint():
        if cancel.is_set():
            raise ScanCancelled

    def finding(code, path, message, record=None, **extra):
        counts[code] += 1
        report["finding_counts"] = dict(counts)
        if len(report["findings"]) >= MAX_FINDINGS:
            report["omitted_findings"] += 1
            return
        item = dict(code=code, path=path, message=message,
                    image_id=record["id"] if record else None, **extra)
        if record and code in ("missing", "content_changed"):
            item["_record"] = dict(record)
        report["findings"].append(item)

    def read_progress(amount):
        report["bytes_read"] += amount
        progress()

    acquired = False
    try:
        progress(True)
        while not acquired:
            checkpoint()
            acquired = db.archive_lock.acquire(timeout=.1)
        checkpoint()
        archive, records = db.integrity_snapshot()
        report["archive"] = archive
        try:
            root = Path(archive).resolve(strict=True)
            info = root.stat()
            identity = (info.st_dev, info.st_ino)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError("Not a directory")
            listing = _directory_entries(root, "", identity, checkpoint)
            try:
                next(listing, None)
            finally:
                listing.close()
        except (OSError, ValueError, RuntimeError):
            finding("archive_unavailable", "", "The archive folder is unavailable or unreadable.")
            return {**report, "status": "unavailable", "phase": "finished"}
        report.update(archive_available=True, resolved_archive=str(root), archive_identity=list(identity), phase="discovering")
        report["baseline_sources"] = dict(Counter(rec.get("baseline_source") or "none" for rec in records))
        known = {os.path.normcase(clean) for rec in records if (clean := safe_rel(rec["file"])) is not None}
        pending, untracked = [""], []
        progress(True)
        while pending:
            checkpoint()
            _check_root(root, identity)
            folder = pending.pop()
            try:
                if folder:
                    _contained(root, folder, identity)
                entries = _directory_entries(root, folder, identity, checkpoint)
                try:
                    for name, linked, directory in entries:
                        relative = f"{folder}/{name}" if folder else name
                        if linked:
                            if os.path.normcase(relative) not in known:
                                finding("unsafe_path", relative, "Linked archive paths are not followed.")
                        elif directory:
                            pending.append(relative)
                        elif name.lower().endswith((".fit", ".fits")) and os.path.normcase(relative) not in known:
                            untracked.append(relative)
                        progress()
                finally:
                    entries.close()
            except ValueError as exc:
                finding("unsafe_path", folder, str(exc))
            except OSError:
                finding("unreadable_directory", folder, "This folder could not be enumerated; its contents were not checked.")
        report.update(phase="checking", total=len(records) + len(untracked))
        progress(True)
        for rec in records:
            checkpoint()
            _check_root(root, identity)
            relative = rec["file"]
            report["current_path"] = relative
            try:
                info, observed = _inspect(root, relative, identity, mode == "deep", checkpoint, read_progress)
                expected = rec.get("expected_sha256") or ""
                if not _SHA256.fullmatch(expected):
                    finding("no_baseline", relative, "No recorded integrity hash is available for comparison.", rec, observed_sha256=observed)
                elif mode == "deep":
                    if observed == expected:
                        report["verified"] += 1
                    else:
                        finding("content_changed", relative, "The bytes differ from the integrity baseline. This may be an intentional edit or damage.",
                                rec, expected_sha256=expected, observed_sha256=observed, baseline_source=rec["baseline_source"])
                elif rec.get("expected_size") is None or rec.get("expected_mtime_ns") is None:
                    finding("incomplete_metadata", relative, "The baseline lacks size or modification time. Run Deep verification.", rec)
                elif (info.st_size, info.st_mtime_ns) != (rec["expected_size"], rec["expected_mtime_ns"]):
                    finding("metadata_changed", relative, "Size or modification time differs. Run Deep verification to compare contents.", rec)
                else:
                    report["metadata_unchanged"] += 1
            except UnstableFile as exc:
                finding("unstable", relative, str(exc), rec)
            except FileNotFoundError:
                _check_root(root, identity)
                finding("missing", relative, "The recorded exposure is missing from the available archive.", rec)
            except ValueError as exc:
                _check_root(root, identity)
                finding("unsafe_path", relative, str(exc), rec)
            except (OSError, RuntimeError):
                _check_root(root, identity)
                finding("unreadable", relative, "This exposure could not be opened or inspected.", rec)
            report["processed"] += 1
            report["unverified"] = report["processed"] - report["verified"]
            progress()
        for relative in sorted(untracked):
            checkpoint()
            _check_root(root, identity)
            try:
                _contained(root, relative, identity).stat()
                finding("untracked", relative, "This exposure is not indexed. Review it before using Reindex folders.")
            except FileNotFoundError:
                _check_root(root, identity)
                finding("unstable", relative, "This exposure disappeared after discovery.")
            except ValueError as exc:
                finding("unsafe_path", relative, str(exc))
            except OSError:
                _check_root(root, identity)
                finding("unreadable", relative, "This discovered exposure could not be inspected.")
            report["processed"] += 1
            report["unverified"] = report["processed"] - report["verified"]
            progress()
        checkpoint()
        return {**report, "status": "completed", "phase": "finished", "current_path": None}
    except ScanCancelled:
        return {**report, "status": "cancelled", "phase": "finished", "current_path": None}
    except ArchiveUnavailable:
        finding("archive_unavailable", "", "The archive became unavailable or changed. Results are partial.")
        return {**report, "status": "unavailable", "phase": "finished", "archive_available": False}
    finally:
        if acquired:
            db.archive_lock.release()


class ArchiveHealthManager:
    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.job = None
        self.thread = None
        self.cancel_event = threading.Event()
        self.stopping = threading.Event()

    def _snapshot_locked(self, full=False):
        result = dict(self.job)
        items = self.job["findings"] if full else self.job["findings"][:200]
        result["findings"] = [{key: value for key, value in item.items() if key != "_record"} for item in items]
        result["finding_limit"] = MAX_FINDINGS
        result["acknowledged_count"] = sum(bool(item.get("acknowledged_at")) for item in self.job["findings"])
        if self.job["status"] in ("running", "cancelling"):
            result["elapsed_seconds"] = round(time.monotonic() - self.started, 2)
        return result

    def snapshot(self, job_id=None, *, full=False):
        with self.lock:
            if not self.job or (job_id is not None and job_id != self.job["id"]):
                raise KeyError(job_id)
            return self._snapshot_locked(full)

    def start(self, mode):
        if not self.operation_lock.acquire(blocking=False):
            raise ValueError("An archive health acknowledgement is in progress.")
        try:
            with self.lock:
                if self.stopping.is_set():
                    raise ValueError("The application is shutting down.")
                if self.job and self.job["status"] in ("running", "cancelling"):
                    raise ValueError("An archive health scan is already running.")
                self.cancel_event = threading.Event()
                self.job = dict(id=uuid.uuid4().hex, mode=mode, status="running", phase="waiting",
                    started_at=datetime.now(UTC).isoformat(), finished_at=None, total=0, processed=0,
                    bytes_read=0, findings=[], finding_counts={}, verified=0, metadata_unchanged=0,
                    unverified=0, omitted_findings=0, acknowledging=False)
                self.started = time.monotonic()
                self.thread = threading.Thread(target=self._run, daemon=True, name="archive-health")
                self.thread.start()
                return self._snapshot_locked()
        finally:
            self.operation_lock.release()

    def _publish(self, update):
        with self.lock:
            self.job.update(update)

    def _run(self):
        try:
            result = scan_archive(self.db, self.job["mode"], self.cancel_event, self._publish)
        except Exception:
            log.exception("Archive health scan failed")
            result = dict(status="failed", phase="finished", error="The scan failed. Review the application log; results are partial.")
        with self.lock:
            self.job.update(result)
            self.job["finished_at"] = datetime.now(UTC).isoformat()
            self.job["elapsed_seconds"] = round(time.monotonic() - self.started, 2)

    def cancel(self, job_id):
        with self.lock:
            if not self.job or self.job["id"] != job_id:
                raise KeyError(job_id)
            if self.job["status"] in ("running", "cancelling"):
                self.job["status"] = "cancelling"
                self.cancel_event.set()

    def acknowledge(self, job_id, image_id, action):
        if not self.operation_lock.acquire(blocking=False):
            raise ValueError("Another acknowledgement is in progress.")
        acquired = False
        try:
            with self.lock:
                if self.stopping.is_set():
                    raise ValueError("The application is shutting down.")
                if not self.job or self.job["id"] != job_id:
                    raise KeyError(job_id)
                if self.job["status"] != "completed":
                    raise ValueError("Only completed scans can be acknowledged. Run a new check.")
                code = {"forget_missing": "missing", "accept_contents": "content_changed"}.get(action)
                issue = next((item for item in self.job["findings"] if item.get("image_id") == image_id and item["code"] == code), None)
                if not issue:
                    raise ValueError("This finding does not support that action.")
                if issue.get("acknowledged_at"):
                    return self._snapshot_locked()
                report = dict(self.job)
                self.job["acknowledging"] = True
            acquired = self.db.archive_lock.acquire(blocking=False)
            if not acquired:
                raise ValueError("Another archive operation is in progress. Wait for it to finish.")
            if self.db.image_dir() != report["archive"]:
                raise ValueError("The archive location changed. Run a new check.")
            root = Path(report["archive"]).resolve(strict=True)
            identity = tuple(report["archive_identity"])
            if str(root) != report["resolved_archive"]:
                raise ValueError("The archive location changed. Run a new check.")
            _check_root(root, identity)
            listing = _directory_entries(root, "", identity, lambda: None)
            try:
                next(listing, None)
            finally:
                listing.close()
            digest = size = mtime_ns = None
            if action == "forget_missing":
                target = _contained(root, issue["path"], identity)
                try:
                    target.lstat()
                except FileNotFoundError:
                    _check_root(root, identity)
                else:
                    raise ValueError("The exposure exists again. Run a new check.")
            else:
                def checkpoint():
                    if self.stopping.is_set():
                        raise ValueError("Acknowledgement stopped during application shutdown.")
                info, digest = _inspect(root, issue["path"], identity, True, checkpoint, lambda size: None)
                if digest != issue["observed_sha256"]:
                    raise ValueError("The contents changed again since the scan. Run Deep verification again.")
                size, mtime_ns = info.st_size, info.st_mtime_ns
            _check_root(root, identity)
            if self.stopping.is_set():
                raise ValueError("Acknowledgement stopped during application shutdown.")
            stamp = self.db.acknowledge_integrity(issue["_record"], report["archive"], job_id,
                action, digest=digest, size=size, mtime_ns=mtime_ns)
            with self.lock:
                issue.update(acknowledged_at=stamp, acknowledgement=action)
                self.job["acknowledging"] = False
                return self._snapshot_locked()
        except (OSError, RuntimeError, ArchiveUnavailable):
            raise ValueError("The archive or exposure is unavailable or changed. Run a new check.") from None
        finally:
            if acquired:
                self.db.archive_lock.release()
            with self.lock:
                if self.job and self.job["id"] == job_id:
                    self.job["acknowledging"] = False
            self.operation_lock.release()

    def request_shutdown(self):
        self.stopping.set()
        with self.lock:
            self.cancel_event.set()

    def shutdown(self):
        self.request_shutdown()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Archive health worker did not stop during shutdown")
