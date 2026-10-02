"""Independent catalog version, declared in VERSION and baked at build time."""
from pathlib import Path
import re
import sys

def project_version() -> str:
    value=(Path(__file__).resolve().parents[1]/"VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",value):
        raise ValueError("VERSION must contain a semantic release version")
    return value

def app_version() -> str:
    if getattr(sys,"frozen",False):
        from ._version_baked import BAKED_VERSION
        return BAKED_VERSION
    return project_version()


def build_version(root=None) -> str:
    """Clean release tags use VERSION; off-tag or changed snapshots say so."""
    import subprocess
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    base=(root/"VERSION").read_text(encoding="utf-8").strip()
    def git(*args):
        result=subprocess.run(["git",*args],cwd=root,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30)
        if result.returncode: raise RuntimeError("Git version metadata is unavailable")
        return result.stdout.strip()
    tags=git("tag","--points-at","HEAD").splitlines()
    if f"v{base}" not in tags: base+="-g"+git("rev-parse","--short=8","HEAD")
    if git("status","--porcelain"): base+="-dirty"
    return base
