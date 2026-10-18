"""Preview or remove disposable local output; never traverse application data.

Default: old test scratch and caches. --build-output also removes compilation
scratch, known retired candidates, and excess executable backups (keep three).
Split recovery evidence, environments, current executable/releases and data stay.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {
    name: {"NightSeam.exe", "NightSeam.spec"} for name in (
        "catalog-progress", "catalog-ui-fix", "open-design-candidate",
        "session-actions-candidate", "split-candidate")
}
RETIRED.update({f"rollback-{version}": {f"NightSeam-{version}.zip", "version.json"}
                for version in ("0.6.9", "0.6.10", "0.6.11")})


def contained(root: Path, path: Path) -> Path:
    """Refuse symlinks/junctions, root itself, and any path outside this checkout."""
    root = root.resolve()
    if not path.is_absolute():
        path = root / path
    relative = path.absolute().relative_to(root)
    if not relative.parts:
        raise ValueError("refusing workspace root")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError(f"refusing linked path: {current}")
    path.resolve().relative_to(root)
    return path


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True  # malformed/unknown ownership is never permission to delete
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            return ctypes.get_last_error() != 87  # access denied = possibly active
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def active_owner(path: Path) -> bool:
    marker = path / ".owner-pid"
    if not marker.exists():
        return False
    try:
        return pid_alive(int(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return True


def remove_tree(path: Path) -> None:
    """Retry short-lived Windows locks and surface failures instead of hiding them."""
    def writable(function, filename, exc):
        if isinstance(exc, PermissionError):
            os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        else:
            raise exc

    for attempt in range(4):
        try:
            if path.is_dir():
                shutil.rmtree(path, onexc=writable)
            else:
                path.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(0.15 * (attempt + 1))


def candidates(root: Path, *, build_output=False, backups_only=False,
               keep_backups=3, older_than_hours=24):
    threshold = time.time() - older_than_hours * 3600
    selected = []
    if not backups_only:
        # Walk only code folders; never walk .git, data, dist, environments/recovery.
        for folder in (root, root / "app", root / "scripts", root / "tests"):
            if folder == root:
                caches = [root / "__pycache__", root / ".pytest_cache"]
            else:
                caches = []
                for parent, dirs, _ in os.walk(folder, followlinks=False):
                    dirs[:] = [d for d in dirs if not (Path(parent) / d).is_junction()]
                    if "__pycache__" in dirs:
                        caches.append(Path(parent) / "__pycache__")
                        dirs.remove("__pycache__")
            selected.extend(p for p in caches if p.exists())
        for relative in ("logs/.pytest-tmp", "logs/browser", ".pytest-tmp"):
            folder = contained(root, root / relative)
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if path.stat().st_mtime > threshold:
                    continue
                if path.is_dir() and active_owner(path):
                    continue
                selected.append(path)
        for relative in ("logs/.pytest-data", "logs/.pytest-default-archive"):
            path = contained(root, root / relative)
            if path.exists() and path.stat().st_mtime <= threshold and not active_owner(path):
                selected.append(path)
        logs = root / "logs"
        if logs.is_dir():
            for path in logs.iterdir():
                match = re.fullmatch(r"(?:\.js-syntax-(\d+)-\d+|test-js-syntax-(\d+)-[0-9a-f]{8}|health-discovery-(\d+)-[a-z0-9_]{8})", path.name)
                if not match or not path.is_dir() or path.stat().st_mtime > threshold:
                    continue
                owner = next(group for group in match.groups() if group is not None)
                if active_owner(path) or pid_alive(int(owner)):
                    continue
                selected.append(path)
        artifacts = root / "tests/browser/artifacts"
        if artifacts.is_dir():
            selected.extend(p for p in artifacts.iterdir()
                            if p.stat().st_mtime <= threshold)
    if build_output or backups_only:
        if keep_backups < 1:
            raise ValueError("at least one executable backup must be retained")
        backups = sorted((root / "dist").glob("NightSeam.before-*.exe"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        selected.extend(backups[keep_backups:])
    if build_output:
        if (root / "build").exists() and not active_owner(root / "build"):
            selected.append(root / "build")
        for name, allowed in RETIRED.items():
            path = root / "dist" / name
            if not path.exists():
                continue
            # Only retire known build-only candidates, never a directory with data.
            files = list(path.iterdir())
            if all(p.is_file() and p.name in allowed
                   for p in files):
                selected.append(path)
    return sorted(set(selected))


def clean(root: Path = ROOT, *, apply=False, verbose=False, **options) -> int:
    root = root.resolve()
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                             check=True, capture_output=True).stdout.decode("utf-8").split("\0")
    paths = candidates(root, **options)
    # Validate the whole plan before deleting anything. Refuse tracked descendants
    # and linked entries even within an otherwise disposable directory.
    for path in paths:
        contained(root, path)
        relative = path.relative_to(root).as_posix()
        if any(p == relative or p.startswith(relative + "/") for p in tracked if p):
            raise ValueError(f"refusing tracked content: {relative}")
        if path.is_dir():
            for parent, dirs, files in os.walk(path, followlinks=False):
                for name in dirs + files:
                    contained(root, Path(parent) / name)
    failures = 0
    total = 0
    for index, path in enumerate(paths):
        size = (sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                if path.is_dir() else path.stat().st_size)
        if verbose or index < 20:
            print(f"{'Remove' if apply else 'Would remove'} {path.relative_to(root)} ({size / 1048576:.2f} MiB)")
        if apply:
            try:
                remove_tree(path)
            except OSError as exc:
                print(f"FAILED {path}: {exc}")
                failures += 1
                continue
        total += size
    if not verbose and len(paths) > 20:
        print(f"... {len(paths) - 20} further paths (use --verbose to list all)")
    print(f"{'Removed' if apply else 'Planned'} {total / 1048576:.2f} MiB; failures: {failures}")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--build-output", action="store_true")
    parser.add_argument("--backups-only", action="store_true")
    parser.add_argument("--keep-backups", type=int, default=3)
    parser.add_argument("--older-than-hours", type=float, default=24)
    args = parser.parse_args()
    if args.older_than_hours < 0:
        parser.error("age must be nonnegative")
    try:
        return clean(**vars(args))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Cleanup refused: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
