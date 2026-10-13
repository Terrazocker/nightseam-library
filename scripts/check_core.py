"""Fail when the baked core trees drift from core/, or when a moved concept is
re-implemented outside core/.

Local source/bake contracts:
  * core/ bytes hash to the manifest hash (bake not re-run -> fail)
  * static/js/core/<hash>/ is byte-identical to core/
  * retained hash directories match the manifest current plus N-1 policy
  * the stable local stub points at the manifest hash
  * core/ stays pure (no DOM/storage/network identifiers)
  * the moved concepts cannot reappear as definitions in the shells

Wired into scripts/check_js.py and scripts/check_release.py.
Usage: python scripts/check_core.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
MANIFEST = CORE / "core.manifest.json"
STATIC_JS = ROOT / "static" / "js"
APP_DIR = ROOT / "app"
SCRIPTS_DIR = ROOT / "scripts"

# Functions that must exist only in core/. Keep in sync with core/index.js.
# Two deliberate shell-level variants stay OUT (pinned by test_core_drift.py's
# MOVED pin): `clearMask` (the desktop editor keeps its own UI-level variant)
# and `popularityFactor` (the shells wrap it to resolve m.popularity first).
MOVED = (
    "rebuildMask", "altOf", "rowOfAlt", "xOfAz", "azOfX", "wrapsFor",
    "profileFromMask", "normalizeProfileBins", "profileSegments", "fovBinRel",
    "visibleSkyBands", "skyBandPolygons", "computeProfile",
    "ceilFor", "bandTopFor", "overhangRuns", "assumedOpenRuns",
    "assumedOpenHatchPolys", "normalizeHorizonLandmarks",
    "normalizeHorizonProvenance", "normalizeHorizonConfidence", "profileDifference",
    "cropKeptBand", "cropStrokes", "clipProfileToFov", "cropPano",
    "fmtSize", "sizeArcmin", "fmtFov", "fmtFovPair", "framePct", "magVisible",
    "skyClampZoom", "skyViewMatrix", "skyScreenToBase", "skyClampFocus",
    "searchBlob", "isCustomId", "wikiTitles",
    "tierForRank", "assignPopularityTiers",
    "offsetEastMin", "zoneOffsetAtLocalNoon", "wallFromUtcMs", "utcFromWallMs",
    "aboveHorizon", "sampleVisible", "statusOf", "effectiveMinAlt",
    "visibilityReason", "summarizeVisibility",
    "vmagKnownAndUnder", "inTonightPool",
    "nextPlannerRevision", "plannerChange", "panoramaRevision",
    # core/panohistory.js - the panorama editor's snapshot/undo model. These
    # names used to be re-implemented per shell (the desktop's pushPanoUndo
    # built its own snapshot object), which is what the guard prevents.
    "snapshot", "applySnapshot", "newHistory", "historyCommit",
    "historyCanUndo", "historyCanRedo", "historyUndo", "historyRedo",
    "historyMarkSaved", "historyDirty",
    "moonSensitivity", "moonMinDist", "effectiveMoonGlow", "moonPenalty",
    "bortleMoonFactor", "nightStatsBatch",
    "computeNightTracks", "scoreNightTracks",
    "julianDay", "dateFromJD", "gmstDeg", "precessJ2000ToDate",
    "precessDateToJ2000", "refractionDeg", "altaz", "altazFrame",
    "altazFrameApply", "sunRaDec", "moonRaDec", "moonIllumination",
    "moonAge",
    "sunAlt", "sunCrossings", "solarSession", "solarSessionWindows",
    "nightInfo", "pickDarkWindow", "scrubWindowMs",
    "scrubBounds", "scrubClamp", "sepDeg", "nightStats",
)
# The Python astronomy/planning layer was deleted in Phase C (0.6.20): these
# snake_case cores must not reappear as Python definitions in app/ or scripts/.
# (app/fits.py's parse_ra_hours/parse_dec_deg are PARSERS, not astronomy.)
MOVED_PY = (
    "julian_day", "gmst_deg", "precess_j2000_to_date",
    "precess_j2000_to_date_inverse", "altaz", "sun_ra_dec", "moon_ra_dec",
    "moon_illumination", "moon_age", "sun_alt",
    "find_twilight", "make_sun_alt_fn", "night_window", "local_utc",
    "compute_night", "object_detail", "ceil_lookup", "longest_run",
)
RETIRED_PY_MODULES = ("astro.py", "planning.py", "nightcalc.py")
# Shell files deliberately carry thin arrow wrappers around these core
# functions (they inject shell state: esc(), S.minAlt, cfg); a const/arrow
# definition of them is NOT a moved-concept violation. `clearMask` and
# `popularityFactor` are not in MOVED at all (documented above).
SHELL_WRAPPERS = {"fmtSize", "statusOf", "effectiveMinAlt"}
# SHELL-UNIFICATION PASS: the web's panorama-history functions became thin
# delegates to core/panohistory.js (the desktop calls the core snapshot/apply
# helpers from its own undo stack under different local names). A `function
# name(...)` wrapper is legitimate ONLY while it actually delegates - so the
# plain-function exemption is granted per name and its body must contain the
# core call. A shell that re-implements one instead of calling core still fails.
SHELL_WRAPPER_DELEGATES = {
    "snapshot": ("coreSnapshotPano",),
    "applySnapshot": ("coreApplySnapshot",),
    "newHistory": ("coreSnapshotPano",),
    "historyCommit": ("coreHistoryCommit",),
    "historyCanUndo": ("coreHistoryCanUndo",),
    "historyCanRedo": ("coreHistoryCanRedo",),
    "historyUndo": ("coreHistoryUndo",),
    "historyRedo": ("coreHistoryRedo",),
    "historyMarkSaved": ("coreHistoryMarkSaved",),
    "historyDirty": ("coreHistoryDirty",),
}
# CO-03 (0.6.32 review): the purity list named CAPABILITIES but no LOADER, so
# `eval(...)`, `new Function(...)`, `importScripts(...)` and a dynamic
# `import('data:text/javascript,...')` all passed inside core/. A `data:`-URL
# import is a code-injection path with no identifier the old list knew.
FORBIDDEN = (
    r"\bdocument\s*\.", r"\bwindow\s*\.", r"\blocalStorage\b",
    r"\bsessionStorage\b", r"\bindexedDB\b", r"\bXMLHttpRequest\b",
    r"\bprocess\s*\.", r"node:", r"\bfetch\s*\(",
    r"\beval\s*\(", r"\bnew\s+Function\b", r"\bimportScripts\s*\(",
    r"\bimport\s*\(",
)

# CO-01 (0.6.32 review): both shells ship a live inline `<script>`
# (`web/index.html`, `static/index.html`), so a moved concept pasted into a page
# was invisible to the `*.js`-only scan. `.mjs` is scanned for the same reason -
# both are code a shell can ship.
SHELL_SUFFIXES = (".js", ".mjs", ".html")
_SCRIPT_BODY_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def _load_build():
    path = Path(__file__).resolve().parent / "build_core.py"
    spec = importlib.util.spec_from_file_location("build_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def baked_dir_problems(label: str, dest_root: Path, hash_: str,
                       previous: str | None) -> list[str]:
    """XL-02 (0.6.24 review): the baked hash dirs must be EXACTLY the policy set -
    the current dir plus, when the manifest names one, the retained N-1 dir.

    Before this the guard required exactly one dir, so the documented retention
    policy (keep N-1 so a cached old `core.js` stub still resolves) was impossible
    to satisfy: a maintainer who followed the docs failed the guard. The policy
    itself lives in `build_core.retention_keep()` so baker and guard cannot drift."""
    if not dest_root.is_dir():
        return [f"{label}: baked core directory is missing"]
    build = _load_build()
    allowed = sorted(build.retention_keep(hash_, previous))
    dirs = sorted(d.name for d in dest_root.iterdir() if d.is_dir())
    if dirs == allowed:
        return []
    stale = sorted(set(dirs) - set(allowed))
    if stale:
        return [f"{label}: stale baked hash dir(s) {stale} - the retention policy "
                f"keeps only {allowed} (current + N-1)"]
    return [f"{label}: baked hash dirs {dirs} != {allowed} - run "
            "scripts/build_core.py (the N-1 dir is kept on purpose)"]


def shell_sources(base: Path) -> list[Path]:
    """Every CODE file in a shell tree, RECURSIVELY, minus the baked core copies.

    CO-01 (0.6.32 review): the scan covered `*.js` only, while BOTH shells ship a
    live inline `<script>` in `index.html` - so math pasted into the page passed
    every guard. `.mjs` is scanned for the same reason (it is code a shell can
    ship); `source_text()` extracts the script bodies from HTML.

    CO-03 (0.6.24 review): the importer/token and MOVED scans used `glob("*.js")`,
    so a shared concept re-implemented in a NEW subdirectory (`web/lib/`,
    `static/js/lib/`, ...) passed BOTH - while the guard's contract is that a
    moved concept cannot be re-implemented outside `core/`. The baked
    `core/<hash>/` trees are copies of `core/`, checked through their source
    digests, so they are skipped here; the top-level `core.js` stub is kept."""
    out = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SHELL_SUFFIXES:
            continue
        parts = p.relative_to(base).parts
        if len(parts) > 1 and parts[0] == "core":
            continue
        out.append(p)
    return out


def source_text(path: Path) -> str:
    """The JavaScript a shell file really ships.

    A module is scanned whole; an HTML page contributes ONLY its `<script>`
    bodies, because the MOVED patterns are substring regexes over identifiers and
    a whole-file scan would fire on ordinary prose (`index.html` describes the
    concepts in words)."""
    text = _read(path)
    if path.suffix.lower() != ".html":
        return text
    return "\n".join(_SCRIPT_BODY_RE.findall(text))


def scan() -> list[str]:
    problems: list[str] = []
    if not CORE.is_dir():
        return ["core/ is missing"]
    if not MANIFEST.is_file():
        return ["core/core.manifest.json is missing - run scripts/build_core.py"]
    try:
        man = json.loads(_read(MANIFEST))
    except (OSError, ValueError) as exc:
        return [f"core/core.manifest.json unreadable: {exc}"]

    hash_ = str(man.get("hash") or "")
    files = man.get("files") or {}
    # The manifest token is a historical per-bake register counter (see
    # DECISIONS.md): the stub carries the content HASH, not the token, so
    # there is no token equality to validate here - do not re-add a dead read.
    previous = str(man.get("previous") or "") or None
    build = _load_build()

    cur_hash, cur_files = build.tree_hash(CORE)
    if cur_hash != hash_:
        problems.append(f"core/ hash {cur_hash} != manifest {hash_} - "
                        "re-run scripts/build_core.py")
    if cur_files != files:
        problems.append("core/ per-file hashes differ from the manifest - "
                        "re-run scripts/build_core.py")

    for label, dest_root in (("static", STATIC_JS / "core"),):
        problems.extend(baked_dir_problems(label, dest_root, hash_, previous))
        for rel, digest in sorted(files.items()):
            fp = dest_root / hash_ / rel
            if not fp.is_file():
                problems.append(f"{label}: baked core is missing {rel}")
            elif hashlib.sha256(build.normalize(fp.read_bytes())).hexdigest() != digest:
                problems.append(f"{label}: {rel} differs from core/")

    for label, stub in (("static", STATIC_JS / "core.js"),):
        if not stub.is_file():
            problems.append(f"{label}: {stub.name} stub is missing")
            continue
        if f"./core/{hash_}/index.js" not in _read(stub):
            problems.append(f"{label}: {stub.name} stub does not point at {hash_}")

    static_edge = any(re.search(r"['\"]\./core\.js['\"]", source_text(p))
                      for p in shell_sources(STATIC_JS) if p.name != "core.js")
    if not static_edge:
        problems.append("static: no './core.js' import found")

    for p in build.core_files(CORE):
        text = _read(p)
        for pattern in FORBIDDEN:
            if re.search(pattern, text):
                problems.append(f"core/{p.name}: forbidden pattern {pattern}")

    for base in (STATIC_JS,):
        for p in shell_sources(base):
            if p.name == "core.js":
                continue
            text = source_text(p)
            for name in MOVED:
                # `function name(`, any assignment `name =` (const/let/var,
                # reassignment, comment-separated arrow), an OBJECT PROPERTY
                # (`name: (…) => …`, `name: function …`) and a method/getter
                # (`name(…) {`, `get name() {`) - an arrow, property or method
                # re-implementation must not slip past the guard (TS3, review;
                # CO-02/TR-02, 0.6.32 review: the property/method positions were
                # missed, so `{ julianDay(dt) {…} }` and `{ get gmstDeg() {…} }`
                # passed). The documented shell wrappers are exempt from the
                # non-function forms only.
                plain = re.search(r"(export\s+)?(async\s+)?function\s+"
                                  + name + r"\b", text)
                delegated = False
                if plain:
                    calls = SHELL_WRAPPER_DELEGATES.get(name)
                    if calls is None or not any(c in text for c in calls):
                        problems.append(
                            f"{p.name}: {name}() must live in core/ only")
                        continue
                    # a documented DELEGATE: the wrapper form is accepted, but
                    # the sneak check below still applies to the same name
                    delegated = True
                sneak = None
                if name not in SHELL_WRAPPERS and not delegated:
                    # only FUNCTION-VALUED assignments (or property/method
                    # definitions): MOVED names are also natural local variable
                    # names (`const moonAge = ...`, `const sunAlt = []`).
                    sneak = re.search(
                        r"(?<![\w.$=!<>])" + name +
                        r"\s*=\s*(?:/\*[\s\S]*?\*/\s*)?"
                        r"(?:\(|async\b|function\b|[\w$]+\s*=>)", text) or \
                        re.search(
                            r"(?:^|[{,;}\n])\s*" + name + r"\s*:\s*"
                            r"(?:\(|async\b|function\b|[\w$]+\s*=>)", text) or \
                        re.search(r"(?:^|[{,;}\n])\s*"
                                  r"(?:get\s+|set\s+|async\s+|\*\s*)?"
                                  + name + r"\s*\([^)]*\)\s*\{", text)
                if sneak or (plain and not delegated):
                    problems.append(f"{p.name}: {name}() must live in core/ only")

    # Phase C: Python is IO/platform only - no astronomy/planning math may be
    # re-implemented in app/ or scripts/ (the retired modules or their cores).
    for name in RETIRED_PY_MODULES:
        if (APP_DIR / name).exists():
            problems.append(f"app/{name}: retired in Phase C - astronomy lives "
                            "in core/")
    for base in (APP_DIR, SCRIPTS_DIR):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            text = _read(p)
            for name in MOVED_PY:
                # `async def` counts too (TS3, review).
                if re.search(r"^\s*(?:async\s+)?def\s+" + name + r"\s*\(",
                             text, re.M):
                    problems.append(
                        f"{p.relative_to(base)}: {name}() must live in core/ only")
    return problems


def main() -> int:
    problems = scan()
    for problem in problems:
        print(f"  {problem}")
    print("check_core:", "FAIL" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
