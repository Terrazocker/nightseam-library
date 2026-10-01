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


def test_shell_sources_scan_pages_and_modules():
    """CO-01 (0.6.32 review): both shells ship a LIVE inline `<script>` in
    `index.html`, so the moved-concept scan must read the pages too (`.mjs` is
    code a shell can ship as well). An HTML page contributes only its script
    BODIES - the MOVED patterns are substring regexes, so scanning the prose
    would fire on it."""
    guard = _guard()
    web = {p.name for p in guard.shell_sources(ROOT / "web")}
    assert "index.html" in web, "web/index.html must be scanned (inline script)"
    assert "app.js" in web
    src = guard.source_text(ROOT / "web" / "index.html")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "<script" not in src, "the web entry document should ship no inline script bodies"
    assert 'data-theme="workbench"' in html
    assert "nightseam_theme" not in src, "theme selection is no longer bootstrapped inline"
    assert "Catalog: OpenNGC" not in src, "the page prose must not be scanned"


def _stage(tmp_path: Path):
    """A full, minimal core copy: core/, both baked trees, both stubs and the
    two shell modules that import them."""
    real = _guard()
    import json
    manifest = json.loads((ROOT / "core" / "core.manifest.json").read_text(encoding="utf-8"))
    hash_ = manifest["hash"]
    shutil.copytree(ROOT / "core", tmp_path / "core")
    for rel, shell in (("web", "web/horizon.js"), ("static/js", "static/js/pano.js")):
        base = tmp_path / Path(rel)
        base.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / Path(rel) / "core", base / "core")
        shutil.copy(ROOT / Path(rel) / "core.js", base / "core.js")
        shutil.copy(ROOT / shell, base / Path(shell).name)
    guard = _guard()
    guard.CORE = tmp_path / "core"
    guard.MANIFEST = guard.CORE / "core.manifest.json"
    guard.WEB_DIR = tmp_path / "web"
    guard.STATIC_JS = tmp_path / "static" / "js"
    guard.APP_DIR = tmp_path / "app"
    guard.SCRIPTS_DIR = tmp_path / "scripts"
    guard.APP_DIR.mkdir()
    guard.SCRIPTS_DIR.mkdir()
    return guard, hash_


def test_mutations_fail_the_guard(tmp_path):
    guard, hash_ = _stage(tmp_path)
    assert guard.scan() == [], "the staged copy must start clean"

    # (a) a core byte changes -> hash mismatch
    (guard.CORE / "horizon.js").write_bytes(
        (guard.CORE / "horizon.js").read_bytes() + b"\n")
    assert guard.scan(), "a bare core change must FAIL the guard"

    # restore, then tamper with the baked copy (b)
    shutil.copy(ROOT / "core" / "horizon.js", guard.CORE / "horizon.js")
    assert guard.scan() == []
    baked = guard.WEB_DIR / "core" / hash_ / "horizon.js"
    baked.write_bytes(baked.read_bytes() + b"\n")
    assert guard.scan(), "a changed baked byte must FAIL the guard"

    # restore, stale stub (c)
    shutil.copy(ROOT / "web" / "core" / hash_ / "horizon.js", baked)
    stub = guard.WEB_DIR / "core.js"
    stub.write_text("export { NO_BLOCK } from './core/deadbeef/index.js';\n",
                    encoding="utf-8")
    assert guard.scan(), "a stale stub must FAIL the guard"

    # restore, token mismatch (d)
    shutil.copy(ROOT / "web" / "core.js", stub)
    shell = guard.WEB_DIR / "horizon.js"
    import re
    text = shell.read_text(encoding="utf-8")
    assert re.search(r"\?v=\d+", text), "the shell must carry a core token"
    shell.write_text(re.sub(r"\?v=\d+", "?v=99", text), encoding="utf-8")
    assert guard.scan(), "a token mismatch must FAIL the guard"

    # restore, moved concept re-implemented in a shell (e)
    shutil.copy(ROOT / "web" / "horizon.js", shell)
    shell.write_text(shell.read_text(encoding="utf-8")
                     + "\nexport function profileSegments() { return []; }\n",
                     encoding="utf-8")
    assert guard.scan(), "a moved concept outside core/ must FAIL the guard"

    # restore, then the Python side (Phase C): a retired module reappears (f)
    shutil.copy(ROOT / "web" / "horizon.js", shell)
    assert guard.scan() == []
    (guard.APP_DIR / "nightcalc.py").write_text("def compute_night():\n    pass\n",
                                                encoding="utf-8")
    assert guard.scan(), "a retired Python astronomy module must FAIL the guard"

    # ... or a script re-implements a moved core (g)
    (guard.APP_DIR / "nightcalc.py").unlink()
    (guard.SCRIPTS_DIR / "bake.py").write_text("def altaz(ra, dec):\n    return 0\n",
                                               encoding="utf-8")
    assert guard.scan(), "Python astronomy math outside core/ must FAIL the guard"

    # ... or the re-implementation evades the plain patterns (h): an ARROW
    # shell definition, a comment-separated arrow, an OBJECT-PROPERTY, a
    # same-line method, an accessor and an `async def` Python one (TS3, review
    # + adversarial verification; CO-02/TR-02, 0.6.32 review: the property,
    # method and getter positions used to slip past).
    (guard.SCRIPTS_DIR / "bake.py").unlink()
    shell = guard.WEB_DIR / "horizon.js"
    for evasion in ("\nconst profileSegments = () => [];\n",
                    "\nconst profileSegments = /* c */ (a) => [];\n",
                    "\nprofileSegments = (a) => [];\n",
                    "\nfunction profileSegments(a) { return []; }\n",
                    "\nconst H = { profileSegments: (a) => [] };\n",
                    "\nconst H = { ra: 0, profileSegments(a) { return []; } };\n",
                    "\nconst H = { profileSegments: function (a) { return []; } };\n",
                    "\nconst H = { get profileSegments() { return []; } };\n",
                    "\nconst H = { async profileSegments(a) { return []; } };\n"):
        shell.write_text(shell.read_text(encoding="utf-8") + evasion,
                         encoding="utf-8")
        assert guard.scan(), f"evasion not detected: {evasion!r}"
        shutil.copy(ROOT / "web" / "horizon.js", shell)
        assert guard.scan() == []

    # ... or in an INLINE <script> of a shell PAGE (CO-01, 0.6.32 review: the
    # MOVED scan read `*.js` only, while both shells ship a live inline script).
    page = guard.WEB_DIR / "index.html"
    shutil.copy(ROOT / "web" / "index.html", page)
    assert guard.scan() == []
    page.write_text(page.read_text(encoding="utf-8").replace(
        "</head>",
        "<script>function julianDay(d){return 2451545.0}</script></head>"),
        encoding="utf-8")
    assert guard.scan(), "an inline-script re-implementation must FAIL the guard"
    page.unlink()
    assert guard.scan() == []
    (guard.SCRIPTS_DIR / "bake.py").write_text(
        "async def altaz(ra, dec):\n    return 0\n", encoding="utf-8")
    assert guard.scan(), "an async Python astronomy re-implementation must FAIL"
    (guard.SCRIPTS_DIR / "bake.py").unlink()

    # the real repository was never touched
    assert _guard().scan() == []


def test_baked_core_retention_keeps_current_and_n1(tmp_path):
    """XL-02 (0.6.24 review): the documented N-1 retention policy is now the
    code's BEHAVIOUR - a re-bake keeps the current content-hashed dir plus the
    immediately previous one (a visitor holding a cached old `core.js` stub must
    still resolve its tree) and removes anything older, while the guard accepts
    exactly that set and reports a stale dir or a dropped N-1 dir.

    Before this, `check_core.py` required exactly ONE dir, so a maintainer who
    followed the documented policy failed the guard."""
    guard = _guard()
    build = guard._load_build()

    # the policy itself
    assert build.retention_keep("newhash", "prevhash") == {"newhash", "prevhash"}
    assert build.retention_keep("newhash", None) == {"newhash"}
    assert build.retention_keep("same", "same") == {"same"}

    # XL-02: an IDEMPOTENT re-bake carries the recorded previous forward (a no-op
    # bake must not forget - and then prune - the dir a cached stub may need)
    assert build.next_previous("newhash", "prevhash", "newhash") == "prevhash"
    assert build.next_previous("oldhash", None, "newhash") == "oldhash"
    assert build.next_previous(None, None, "newhash") is None
    assert build.next_previous("newhash", "newhash", "newhash") is None

    # The one-time model retirement path drops both a prior N-1 and the old
    # current tree. Normal bakes still choose and retain N-1.
    assert build.previous_for_bake("oldhash", "olderhash", "newhash") == "oldhash"
    assert build.previous_for_bake(
        "oldhash", "olderhash", "newhash",
        retire_model_bearing_previous=True) is None
    assert build.retention_keep(
        "newhash", build.previous_for_bake(
            "oldhash", "olderhash", "newhash",
            retire_model_bearing_previous=True)) == {"newhash"}

    # the baker keeps current + N-1 and prunes the rest
    root = tmp_path / "core"
    for name in ("oldest", "prevhash", "newhash"):
        (root / name).mkdir(parents=True)
    removed = build.prune_baked_dirs(root, build.retention_keep("newhash", "prevhash"))
    assert removed == ["oldest"]
    assert sorted(d.name for d in root.iterdir()) == ["newhash", "prevhash"]

    # the guard accepts exactly that state ...
    assert guard.baked_dir_problems("web", root, "newhash", "prevhash") == []
    # ... and fails on a stale dir, on a dropped N-1 dir, and on a missing tree
    (root / "stale").mkdir()
    problems = guard.baked_dir_problems("web", root, "newhash", "prevhash")
    assert problems and "stale" in problems[0], problems
    assert guard.baked_dir_problems("web", root, "newhash", None), (
        "a dir that is neither current nor recorded N-1 must be reported")
    assert guard.baked_dir_problems("web", tmp_path / "nope", "newhash", None)

    # the wiring: both scripts must actually use the shared policy
    import inspect
    assert "prune_baked_dirs(" in inspect.getsource(build.main)
    assert '"previous": previous' in inspect.getsource(build.main)
    assert "baked_dir_problems(" in inspect.getsource(guard.scan)


def test_model_bake_retirement_prunes_old_tree_and_records_null(monkeypatch, tmp_path):
    """The one-time feature retirement is wired through the real baker.

    A normal changed bake preserves the old current tree as N-1. The explicit
    retirement bake then removes that tree and records `previous: null`.
    """
    import json
    import sys

    guard = _guard()
    build = guard._load_build()
    repo = tmp_path / "repo"
    core = repo / "core"
    core.mkdir(parents=True)
    (core / "index.js").write_text("export { ping } from './ping.js';\n", encoding="utf-8")
    (core / "ping.js").write_text("export function ping() { return 1; }\n", encoding="utf-8")
    (core / "core.manifest.json").write_text(json.dumps({
        "hash": "oldhash", "token": 7, "previous": "olderhash", "files": {},
    }), encoding="utf-8")
    new_hash, _files = build.tree_hash(core)

    web = repo / "web"
    static_js = repo / "static" / "js"
    for root in (web / "core", static_js / "core"):
        for old in ("oldhash", "olderhash"):
            model_tree = root / old
            model_tree.mkdir(parents=True)
            (model_tree / "skybrightness.js").write_text("retired model", encoding="utf-8")

    monkeypatch.setattr(build, "CORE", core)
    monkeypatch.setattr(build, "MANIFEST", core / "core.manifest.json")
    monkeypatch.setattr(build, "WEB_DIR", web)
    monkeypatch.setattr(build, "STATIC_JS", static_js)
    monkeypatch.setattr(sys, "argv", ["build_core.py"])
    assert build.main() == 0
    normal = json.loads((core / "core.manifest.json").read_text(encoding="utf-8"))
    assert normal["previous"] == "oldhash"
    assert {p.name for p in (web / "core").iterdir()} == {"oldhash", new_hash}

    monkeypatch.setattr(sys, "argv", ["build_core.py", "--retire-model-bearing-previous"])
    assert build.main() == 0
    retired = json.loads((core / "core.manifest.json").read_text(encoding="utf-8"))
    assert retired["previous"] is None
    for root in (web / "core", static_js / "core"):
        assert {p.name for p in root.iterdir()} == {new_hash}
        assert not (root / new_hash / "skybrightness.js").exists()


def test_a_moved_concept_in_a_shell_subdirectory_is_caught():
    """CO-03 (0.6.24 review): the importer/token and MOVED scans used
    `glob("*.js")` - one level only - so a core concept re-implemented in a NEW
    subdirectory (`web/lib/`, `static/js/lib/`, ...) passed BOTH, while the guard's
    contract is that a moved concept cannot be re-implemented outside `core/`.

    The scratch directory is created and removed here; the real tree is restored
    before the final assertion."""
    guard = _guard()
    assert guard.scan() == []
    lib = guard.WEB_DIR / "lib"
    lib.mkdir()
    planted = lib / "geom.js"
    try:
        planted.write_text("export function ceilFor(p, az) { return 0; }\n",
                           encoding="utf-8")
        assert guard.scan(), "a moved concept under web/lib/ must FAIL the guard"
        # a subdirectory importer names the stub through '../'
        planted.write_text("import { $ } from '../core.js?v=99';\nexport const x = $;\n",
                           encoding="utf-8")
        problems = guard.scan()
        assert any("core token" in p for p in problems), problems
    finally:
        planted.unlink()
        lib.rmdir()
    assert guard.scan() == []
