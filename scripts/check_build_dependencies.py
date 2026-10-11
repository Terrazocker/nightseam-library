"""Read-only gate for the Windows x64 CPython 3.12 desktop build lock.

Never installs or repairs packages. Run using .venv-build/Scripts/python.exe.
Runtime requirements remain the supported lower-bound contract; this lock owns
the exact dependency versions used for tested local desktop artifacts.
"""

from __future__ import annotations

import importlib.metadata as metadata
import platform
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "requirements-build-lock.txt"
ROOT_PACKAGES = {"fastapi", "uvicorn", "python-multipart", "pywebview",
                 "pytest", "httpx", "httpx2", "pillow", "pyinstaller"}
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> dict[str, str]:
    pins = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError(f"{path.name}:{number}: require one exact name==version pin")
        name, version = canonical(match[1]), match[2]
        if name in pins:
            raise ValueError(f"{path.name}:{number}: duplicate package {name}")
        pins[name] = version
    missing = ROOT_PACKAGES - pins.keys()
    if missing:
        raise ValueError("build lock omits required roots: " + ", ".join(sorted(missing)))
    return pins


def inventory_errors(pins: dict[str, str], version=metadata.version) -> list[str]:
    errors = []
    for name, expected in sorted(pins.items()):
        try:
            installed = version(name)
        except metadata.PackageNotFoundError:
            errors.append(f"{name}: missing (locked {expected})")
            continue
        if installed != expected:
            errors.append(f"{name}: installed {installed}, locked {expected}")
    return errors


def closure_errors(pins: dict[str, str], requires=metadata.requires) -> list[str]:
    # Import only after the inventory gate verifies packaging itself.
    from packaging.requirements import Requirement

    errors = []
    for name in sorted(pins):
        for raw in requires(name) or []:
            dependency = Requirement(raw)
            if dependency.marker and not dependency.marker.evaluate({"extra": ""}):
                continue
            child = canonical(dependency.name)
            if child not in pins:
                errors.append(f"{name}: active dependency {child} is absent from lock")
            elif not dependency.specifier.contains(pins[child]):
                errors.append(f"{name}: {raw} conflicts with locked {pins[child]}")
            if dependency.extras or dependency.url:
                errors.append(f"{name}: unsupported extras/URL dependency: {raw}")
    return errors


def runtime_errors() -> list[str]:
    if (sys.version_info[:2] != (3, 12) or sys.platform != "win32"
            or platform.python_implementation() != "CPython" or struct.calcsize("P") != 8):
        return ["desktop builds require Windows x64 CPython 3.12"]
    return []


def main() -> int:
    errors = runtime_errors()
    try:
        pins = read_lock(LOCK)
        errors += inventory_errors(pins)
        if not errors:
            errors += closure_errors(pins)
    except (OSError, ValueError, metadata.PackageNotFoundError) as exc:
        errors.append(str(exc))
    if errors:
        for error in errors:
            print(f"Build dependency check FAILED: {error}")
        print("Prepare a dedicated Windows x64 Python 3.12 environment manually:\n"
              "  py -3.12 -m venv .venv-build\n"
              "  .venv-build\\Scripts\\python.exe -m pip install -r requirements-build-lock.txt\n"
              "Re-run this check before building. No packages were changed.")
        return 1
    print(f"Build dependencies verified: {len(pins)} exact pins; Windows x64 CPython 3.12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
