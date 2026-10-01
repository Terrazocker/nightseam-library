"""App version: the git tag when running from source; a value baked into the
frozen exe at build time (build.ps1 writes app/_version_baked.py, gitignored)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    from ._version_baked import BAKED_VERSION  # type: ignore
except ImportError:
    BAKED_VERSION = None


def app_version() -> str:
    if getattr(sys, "frozen", False) and BAKED_VERSION:
        return BAKED_VERSION
    root = Path(__file__).resolve().parents[1]
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=3)
        tag = out.stdout.strip().lstrip("v")
        if tag:
            return tag
    except Exception:
        pass
    return "dev"