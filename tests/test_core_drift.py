"""Drift guard for the baked core trees (scripts/check_core.py).

core/ is authored once; scripts/build_core.py copies it to content-hashed URLs
in web/ and static/js/ plus a stable entry stub. This test proves by mutation -
on COPIES, never the real tree - that every drift class fails the guard:
a changed core byte, a changed baked byte, a stale stub, a token mismatch and
a moved concept re-implemented in a shell. The real repository must pass.
"""

import importlib.util
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Two core functions have deliberate shell-level variants, documented in
# scripts/check_core.py: clearMask (desktop editor UI variant) and
# popularityFactor (the shells resolve m.popularity before delegating).
_MOVED_EXCEPTIONS = {"clearMask", "popularityFactor"}


def _core_exported_functions() -> set[str]:
    """Every function `core/` exports, derived from the source files.

    Matches both `export function name` and function-valued
    `export const name = (` / `async` / `function` (constants like NO_BLOCK
    are excluded - the guard has no reason to track them)."""
    names: set[str] = set()
    for p in sorted((ROOT / "core").glob("*.js")):
        text = p.read_text(encoding="utf-8")
        names.update(re.findall(r"export\s+(?:async\s+)?function\s+(\w+)",
                                text))
        names.update(re.findall(
            r"export\s+const\s+(\w+)\s*=\s*(?:\(|async\b|function\b)", text))
    return names


def _guard():
    spec = importlib.util.spec_from_file_location(
        "check_core", ROOT / "scripts" / "check_core.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_real_repository_core_bake_is_clean():
    assert _guard().scan() == [], "the baked core trees drifted from core/"


def test_moved_list_covers_every_core_function():
    """FIX-8 (review TS2): scripts/check_core.py::MOVED is hand-kept and had
    fallen behind core/index.js, leaving moved concepts (scrubWindowMs,
    computeNightTracks, ...) unflagged if a shell re-defined them."""
    guard = _guard()
    missing = _core_exported_functions() - set(guard.MOVED) - _MOVED_EXCEPTIONS
    assert not missing, f"add these to check_core.py MOVED: {sorted(missing)}"


def test_moved_list_has_no_stale_entries():
    guard = _guard()
    stale = set(guard.MOVED) - _core_exported_functions()
    assert not stale, f"check_core.py MOVED names no core function: {sorted(stale)}"


def test_moved_staleness_is_detected():
    """Mutation proof for the pin above: dropping one name from MOVED must be
    visible as 'missing' (the real list is never touched)."""
    guard = _guard()
    guard.MOVED = tuple(n for n in guard.MOVED if n != "scrubWindowMs")
    missing = _core_exported_functions() - set(guard.MOVED) - _MOVED_EXCEPTIONS
    assert "scrubWindowMs" in missing


def _stage(tmp_path):
    guard=_guard()
    shutil.copytree(ROOT/"core",tmp_path/"core")
    shutil.copytree(ROOT/"static/js/core",tmp_path/"static/js/core")
    shutil.copy(ROOT/"static/js/core.js",tmp_path/"static/js/core.js")
    shutil.copy(ROOT/"static/js/util.js",tmp_path/"static/js/util.js")
    guard.CORE=tmp_path/"core"
    guard.MANIFEST=guard.CORE/"core.manifest.json"
    guard.STATIC_JS=tmp_path/"static/js"
    guard.APP_DIR=tmp_path/"app"
    guard.SCRIPTS_DIR=tmp_path/"scripts"
    guard.APP_DIR.mkdir()
    guard.SCRIPTS_DIR.mkdir()
    return guard

def test_local_core_mutations_fail_the_guard(tmp_path):
    import json
    guard=_stage(tmp_path)
    assert guard.scan()==[]
    hash_=json.loads(guard.MANIFEST.read_text(encoding="utf-8"))["hash"]
    source=guard.CORE/"horizon.js"
    original=source.read_bytes()
    source.write_bytes(original+b"\n")
    assert guard.scan()
    source.write_bytes(original)
    baked=guard.STATIC_JS/"core"/hash_/"horizon.js"
    original=baked.read_bytes()
    baked.write_bytes(original+b"\n")
    assert guard.scan()
    baked.write_bytes(original)
    stub=guard.STATIC_JS/"core.js"
    original=stub.read_text(encoding="utf-8")
    stub.write_text("export {NO_BLOCK} from './core/deadbeef/index.js';\n",encoding="utf-8")
    assert guard.scan()
    stub.write_text(original,encoding="utf-8")
    shell=guard.STATIC_JS/"lib"
    shell.mkdir()
    planted=shell/"geom.js"
    for value in ["export function ceilFor() {return 0;}","export const box={ceilFor(p,az){return 0;}};"]:
        planted.write_text(value,encoding="utf-8")
        assert guard.scan()
    planted.unlink()
    assert guard.scan()==[]
    retired=guard.APP_DIR/"nightcalc.py"
    retired.write_text("def compute_night(): pass\n",encoding="utf-8")
    assert guard.scan()
    retired.unlink()
    assert guard.scan()==[]

def test_build_keeps_current_and_previous_local_bakes(tmp_path):
    guard=_guard()
    build=guard._load_build()
    root=tmp_path/"bakes"
    for name in ["current","previous","stale"]: (root/name).mkdir(parents=True)
    assert set(build.prune_baked_dirs(root,build.retention_keep("current","previous")))=={"stale"}
    assert guard.baked_dir_problems("static",root,"current","previous")==[]
    assert guard.baked_dir_problems("static",root,"current",None)
