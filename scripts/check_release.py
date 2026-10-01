"""Release integrity check — ONE authoritative version for every artifact.

    python scripts/check_release.py [--allow-dirty] [--no-stage]

The single source of truth for a release is

    git describe --tags --always --dirty

at build time: build.ps1 bakes it into app/_version_baked.py (and therefore
into dist/NightSeam.exe), web/build_web.py bakes it into web/version.json and
scripts/make_release.py names the zip / writes version.json + ns-version.js
from it.  This script compares those artifacts against each other and against
the tree, BEFORE anything is published.

Severities
    ERROR    the artifacts contradict each other or the tree — do not release.
             (stale exe bake, zip named after another version, ns-version.js
             advertising a different version than the landing page, a
             changelog entry for a version that was never released, no
             curated changelog text for the tag being released, …)
    WARNING  legitimately repairable later in the documented release order
             (web/version.json or landing/changelog.json still describing the
             previous release — both are re-baked/re-generated after the
             desktop zip is uploaded).

The "release version" is `git describe` with the leading `v` stripped.  A
describe that is neither dirty nor carries a `-N-g<sha>` suffix means HEAD is
exactly a tag; only then is the version a real release version and the
curated-changelog gate is applied.

Accepted describe grammar (`_DESCRIBE_RE`) — exactly what git emits for a
numbered tag, nothing else:

    <v>X.Y.Z                             HEAD is exactly that tag
    <v>X.Y.Z-<n>-g<sha>                  n commits past it
    <v>X.Y.Z-dirty                       tag + uncommitted changes
    <v>X.Y.Z-<n>-g<sha>-dirty            both

Anything else (``0.6.9-dirty-dirty``, a non-numeric tag, a bare sha from the
``--always`` fallback in a tagless clone) is an ERROR: a fabricated describe
string is not a version, so it can neither be released nor forgiven.

--allow-dirty downgrades the tree-state finding from ERROR to WARNING (the
operator accepting a labelled snapshot) — and ONLY that finding, and only for
a well-formed describe.  It never bypasses another check: the curated
changelog entry for a dirty tree sitting exactly on tag X.Y.Z is still
required, and the baked-version / staged-metadata checks still run.

`landing/changelog.json` entries name RELEASE TAGS, which
scripts/build_changelog.py derives from `git tag` (`tag.lstrip("v")`), so an
entry version must be plain `X.Y.Z`: no `v` prefix (the download page renders
`"v" + version` itself), no `-dirty`, no `-N-g<sha>`, no non-numeric text.
Anything else is a hard ERROR — a non-numeric version used to sort as (0,),
i.e. silently "older than the release", and only warned.

`dist/release/version.json` describes the zip named by its own `url`
(scripts/make_release.py hashes `NightSeam-<version>.zip` AFTER the zip is
written and before `NightSeam-latest.zip` is copied from it), so `sha256` and
`size` must be present, well formed and equal to the staged file's real
values; the landing page prints `size` as the download size.

Exit code: 0 when there is no ERROR, 1 otherwise.

scripts/make_release.py calls check_release(include_stage=False) before it
stages the zip and check_release(include_stage=True, allow_dirty=True) right
after, so a stale exe can never be published under a new version.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "dist"
EXE = DIST / "NightSeam.exe"
BAKED = ROOT / "app" / "_version_baked.py"
WEB_VERSION = ROOT / "web" / "version.json"
CHANGELOG = ROOT / "landing" / "changelog.json"
STAGE = DIST / "release"
ZIP_PREFIX = "NightSeam-"
LATEST_ZIP = "NightSeam-latest.zip"

# These modules are imported by tracked runtime files but are easy to leave
# behind as untracked working-tree additions.  A release assembled from a
# patch or clean checkout must carry them explicitly.
REQUIRED_RUNTIME_MODULES = (
    "app/theme.py",
    "app/window_placement.py",
    "static/js/catalog_icons.js",
    "static/js/viztheme.js",
    "web/viztheme.js",
)

# THE authoritative version.  --dirty: a snapshot must never claim a clean tag.
DESCRIBE_ARGS = ("describe", "--tags", "--always", "--dirty")

ERROR = "error"
WARNING = "warning"

_OFF_TAG = re.compile(r"-\d+-g[0-9a-f]{4,}")
_BAKED_RE = re.compile(r"""BAKED_VERSION\s*=\s*['"]([^'"]*)['"]""")
_NS_PAYLOAD = re.compile(r"=\s*(\{.*\})\s*;", re.S)

# A version number as this project tags them (AGENTS.md: tag `vX.Y.Z`).
_NUMBERED = r"\d+(?:\.\d+)*"
# Everything `git describe --tags --always --dirty` may legitimately emit for a
# numbered tag; see the module docstring.
_DESCRIBE_RE = re.compile(
    rf"^v?{_NUMBERED}(?:-\d+-g[0-9a-f]{{4,40}})?(?:-dirty)?$")
# A changelog entry names a release TAG: exactly three numeric components, no
# leading `v` (build_changelog.py strips it; the page re-adds it), no suffix.
_RELEASE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Typography is nice in the source, but a Windows console is cp1252 and a
# UTF-8 reading client then sees replacement characters, so everything PRINTED
# goes through console_safe().
_ASCII_FOLD = str.maketrans({
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...",
})


def console_safe(text: str) -> str:
    """Fold typographic punctuation and force ASCII (never raises)."""
    return (text or "").translate(_ASCII_FOLD).encode("ascii", "replace").decode("ascii")


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    level: str          # ERROR | WARNING
    check: str          # short id of the check that produced it
    message: str

    def render(self) -> str:
        return console_safe(f"{self.level.upper():<7} [{self.check}] {self.message}")


def has_errors(findings) -> bool:
    return any(f.level == ERROR for f in findings)


def exit_code(findings) -> int:
    return 1 if has_errors(findings) else 0


def summarize(findings) -> str:
    errors = sum(1 for f in findings if f.level == ERROR)
    warnings = len(findings) - errors
    if not findings:
        return console_safe("OK - every version source agrees.")
    return console_safe(
        f"{errors} error(s), {warnings} warning(s)"
        + (" - NOT release-ready." if errors else " - release-ready."))


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------
def normalize_version(describe: str) -> str:
    """`v0.6.9-dirty` -> `0.6.9-dirty` (leading v stripped, like every tool)."""
    return (describe or "").strip().lstrip("v").strip()


def base_version(version: str) -> str:
    """`0.6.9-1-ga777d88-dirty` -> `0.6.9` (the released tag, no git suffix)."""
    return normalize_version(version).split("-", 1)[0]


def version_key(version: str):
    """Sortable numeric key: `0.6.9` -> (0, 6, 9); `v1` -> (1,)."""
    nums = re.findall(r"\d+", base_version(version))
    return tuple(int(n) for n in nums) if nums else (0,)


def compare_versions(a: str, b: str) -> int:
    ka, kb = version_key(a), version_key(b)
    return (ka > kb) - (ka < kb)


def is_dirty(describe: str) -> bool:
    return (describe or "").strip().endswith("-dirty")


def is_exact_tag(describe: str) -> bool:
    """True when HEAD is exactly a clean tag (no `-dirty`, no `-N-g<sha>`)."""
    d = (describe or "").strip()
    return (bool(_DESCRIBE_RE.match(d)) and not is_dirty(d)
            and not _OFF_TAG.search(d))


def is_dirty_tag(describe: str) -> bool:
    """True for `v0.6.9-dirty`: HEAD is exactly tag v0.6.9, tree modified.

    This is the ONE condition --allow-dirty is allowed to forgive, and it still
    publishes the tag's version line (so that release still needs its curated
    summary).
    """
    d = (describe or "").strip()
    return (bool(_DESCRIBE_RE.match(d)) and is_dirty(d)
            and not _OFF_TAG.search(d))


def describe_problem(describe: str) -> str | None:
    """Why `describe` cannot name a release version, or None when it can.

    Only the grammar in _DESCRIBE_RE is accepted.  In particular the `--always`
    fallback (a bare commit sha in a tagless clone) is rejected: a commit is
    not a version, and it would otherwise look like "HEAD is exactly a tag".
    """
    d = (describe or "").strip()
    if not d:
        return "produced nothing"
    if not _DESCRIBE_RE.match(d):
        return (f"produced {d!r}, which is not a `git describe --tags --always "
                "--dirty` output (expected '<v>X.Y.Z[-<n>-g<sha>][-dirty]')")
    if base_version(d) == "0.0.0":
        return f"produced {d!r}, which names the placeholder tag 0.0.0"
    return None


def parse_baked_version(text: str | None) -> str | None:
    """Read BAKED_VERSION out of app/_version_baked.py's source text."""
    if not text:
        return None
    m = _BAKED_RE.search(text)
    return m.group(1).strip() if m else None


def parse_web_version(text: str | None):
    """web/version.json -> dict, or None when missing/unparsable."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_ns_version(text: str | None):
    """`window.__NS_RELEASE={...};` -> the dict, or None."""
    if not text:
        return None
    m = _NS_PAYLOAD.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_changelog(text: str | None):
    """landing/changelog.json -> (entries, problem).  entries is None on error.

    The schema is what scripts/build_changelog.py writes: a list of
    {version, date, summary} objects, version being a released tag `X.Y.Z`.
    A version that is not a plain release tag is corruption, not "an old
    release": `version_key("garbage")` is (0,), which used to sort BELOW the
    release and merely warn, so a garbled entry could hide behind a WARNING.
    """
    if not text:
        return None, "file is missing"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, f"not valid JSON ({exc})"
    if not isinstance(data, list):
        return None, "top level is not a list"
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            return None, f"entry {i} is not an object"
        for key in ("version", "date", "summary"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                return None, f"entry {i} has no usable '{key}'"
        version = entry["version"].strip()
        if not _RELEASE_VERSION_RE.match(version):
            return None, (
                f"entry {i} version {entry['version']!r} is not a release tag "
                "(expected 'X.Y.Z' as written by scripts/build_changelog.py - "
                "no 'v' prefix, no '-dirty'/'git describe' suffix, no "
                "non-numeric text)")
    return data, None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# git (never through a pipe: the DSH sandbox denies pipe creation to spawned
# processes, so capture_output=True dies with WinError 5 there)
# --------------------------------------------------------------------------
def git(args, root: Path = ROOT):
    """Run git, returning (returncode, stdout).  No pipes are ever created."""
    argv = ["git", *args]
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True)
        return proc.returncode, proc.stdout
    except OSError:
        pass
    # fallback: redirect stdout into a file inside the repo (the platform temp
    # dir is not writable for spawned processes under the sandbox)
    tmp_dir = root / "logs"
    if not tmp_dir.is_dir():
        tmp_dir = root
    tmp = tmp_dir / f".git-capture-{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            proc = subprocess.run(argv, cwd=str(root), stdout=fh,
                                  stderr=subprocess.DEVNULL)
        return proc.returncode, tmp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # no git and no writable scratch dir (e.g. a read-only checkout): report
        # a failed describe instead of crashing with a traceback, so
        # check_describe() can turn it into a normal finding.
        return 1, ""
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def git_describe(root: Path = ROOT) -> str:
    """`git describe --tags --always --dirty`, or "" when git failed."""
    rc, out = git(DESCRIBE_ARGS, root)
    return out.strip() if rc == 0 else ""


def load_curated(script_dir: Path = HERE) -> dict:
    """CURATED summaries from the sibling scripts/build_changelog.py."""
    path = Path(script_dir) / "build_changelog.py"
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_ns_build_changelog", path)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ns_build_changelog"] = module   # dataclass annotation lookup
    spec.loader.exec_module(module)          # __main__ guard keeps this inert
    curated = getattr(module, "CURATED", {})
    return dict(curated) if isinstance(curated, dict) else {}


# --------------------------------------------------------------------------
# collected state (the only impure part) + pure evaluation
# --------------------------------------------------------------------------
@dataclass
class State:
    describe: str = ""
    baked: str | None = None            # parsed BAKED_VERSION
    baked_present: bool = False
    baked_mtime: float | None = None
    exe_mtime: float | None = None
    exe_sha256: str | None = None
    web: dict | None = None
    web_present: bool = False
    web_problem: str | None = None
    changelog: list | None = None
    changelog_present: bool = False
    changelog_problem: str | None = None
    stage: dict = field(default_factory=dict)
    curated: dict = field(default_factory=dict)


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _mtime(path: Path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def collect_stage(stage: Path = STAGE) -> dict:
    """Cheap snapshot of dist/release (names, sizes, hashes, JSON payloads)."""
    state = {"exists": False, "files": [], "version_json": None,
             "version_json_error": None, "ns_version": None, "zips": {},
             "sha256": {}, "zip_entries": {}, "exe_size": None,
             "exe_sha256": None}
    if not stage.is_dir():
        return state
    state["exists"] = True
    state["files"] = sorted(p.name for p in stage.iterdir() if p.is_file())

    version_json = stage / "version.json"
    if version_json.is_file():
        text = _read(version_json)
        try:
            data = json.loads(text or "")
        except ValueError as exc:
            state["version_json_error"] = str(exc)
        else:
            if not isinstance(data, dict):
                state["version_json_error"] = "top level is not an object"
            else:
                state["version_json"] = data

    ns = stage / "ns-version.js"
    if ns.is_file():
        state["ns_version"] = parse_ns_version(_read(ns))

    staged_exe = stage / "NightSeam.exe"
    if staged_exe.is_file():
        try:
            state["exe_size"] = staged_exe.stat().st_size
            state["exe_sha256"] = sha256_file(staged_exe)
        except OSError:
            pass

    for name in state["files"]:
        if name.startswith(ZIP_PREFIX) and name.endswith(".zip"):
            path = stage / name
            try:
                state["zips"][name] = path.stat().st_size
                state["sha256"][name] = sha256_file(path)
            except OSError:
                continue
            # what the zip actually contains (central directory only, no
            # decompression): a zip without the exe installs nothing, and a
            # truncated one cannot even be opened.
            entry = {"members": None, "exe_size": None, "error": None}
            try:
                with zipfile.ZipFile(path) as zf:
                    sizes = {i.filename: i.file_size for i in zf.infolist()}
                entry["members"] = sorted(sizes)
                entry["exe_size"] = sizes.get("NightSeam.exe")
            except (OSError, zipfile.BadZipFile) as exc:
                entry["error"] = str(exc) or exc.__class__.__name__
            state["zip_entries"][name] = entry
    return state


def collect(root: Path = ROOT, curated: dict | None = None) -> State:
    root = Path(root)
    baked_path = root / "app" / "_version_baked.py"
    baked_text = _read(baked_path) if baked_path.is_file() else None
    web_path = root / "web" / "version.json"
    web_text = _read(web_path) if web_path.is_file() else None
    log_path = root / "landing" / "changelog.json"
    log_text = _read(log_path) if log_path.is_file() else None
    entries, log_problem = parse_changelog(log_text)
    web = parse_web_version(web_text)
    exe_path = root / "dist" / "NightSeam.exe"
    try:
        exe_sha256 = sha256_file(exe_path) if exe_path.is_file() else None
    except OSError:
        exe_sha256 = None
    if web is not None:
        web_problem = None
    elif web_text is None:
        # an UNREADABLE file is not a missing one: the missing case is a
        # WARNING ("the web build was never stamped"), a broken one is fatal.
        web_problem = ("file could not be read" if web_path.is_file()
                       else "file is missing")
    else:
        web_problem = "not a JSON object"
    return State(
        describe=git_describe(root),
        baked=parse_baked_version(baked_text),
        baked_present=baked_text is not None,
        baked_mtime=_mtime(baked_path),
        exe_mtime=_mtime(exe_path),
        exe_sha256=exe_sha256,
        web=web,
        web_present=web_path.is_file(),
        web_problem=web_problem,
        changelog=entries,
        changelog_present=log_text is not None,
        changelog_problem=log_problem,
        stage=collect_stage(root / "dist" / "release"),
        curated=(load_curated(root / "scripts") if curated is None
                 else dict(curated)),
    )


def check_describe(describe: str):
    problem = describe_problem(describe)
    if problem is None:
        return []
    return [Finding(ERROR, "describe",
                    f"`git describe --tags --always --dirty` {problem} — it is "
                    "the one authoritative version for the exe bake, "
                    "web/version.json and the release zip; run this from the "
                    "repo root on a machine with git and at least one release "
                    "tag (a fabricated describe string is never a release).")]


def check_tree_state(describe: str, allow_dirty: bool = False):
    d = (describe or "").strip()
    dirty = is_dirty(d)
    off_tag = bool(_OFF_TAG.search(d))
    if not (dirty or off_tag):
        return []
    version = normalize_version(d)
    why = " and ".join(w for w in (
        "the working tree is dirty" if dirty else "",
        f"HEAD is not exactly a tag ({d})" if off_tag else "") if w)
    # --allow-dirty forgives the tree state only, and only for a describe git
    # could really have emitted (check_describe has already failed the run on
    # anything else, so this guard is belt-and-braces).
    forgiven = allow_dirty and bool(_DESCRIBE_RE.match(d))
    return [Finding(WARNING if forgiven else ERROR, "tree",
                    f"{why} — everything published would be labelled "
                    f"'{version}'. Commit and tag first (AGENTS.md, "
                    "'Release order matters'), or pass --allow-dirty to "
                    "accept a labelled snapshot.")]


def check_baked(state: State, version: str):
    findings = []
    if not state.baked_present:
        findings.append(Finding(
            ERROR, "baked",
            "app/_version_baked.py is missing — dist/NightSeam.exe has no "
            "baked version; run build.ps1 (it bakes the version) first."))
    elif not state.baked:
        findings.append(Finding(
            ERROR, "baked",
            "app/_version_baked.py carries no readable BAKED_VERSION — "
            "re-run build.ps1."))
    elif state.baked != version:
        findings.append(Finding(
            ERROR, "baked",
            f"the version baked into app/_version_baked.py is '{state.baked}' "
            f"but git describe says '{version}' — dist/NightSeam.exe is stale; "
            "rebuild with build.ps1 at the tag you are releasing (a stale exe "
            "must never be published under a new version)."))
    if (state.baked_mtime is not None and state.exe_mtime is not None
            and state.baked_mtime > state.exe_mtime):
        findings.append(Finding(
            ERROR, "baked",
            "app/_version_baked.py is newer than dist/NightSeam.exe — the exe "
            "was built before the current bake, so it cannot report it; rebuild."))
    if state.exe_mtime is None:
        findings.append(Finding(
            ERROR, "exe",
            "dist/NightSeam.exe is missing — there is nothing to release; "
            "run build.ps1."))
    return findings


def check_web(state: State, version: str):
    if not state.web_present:
        return [Finding(WARNING, "web",
                        "web/version.json is missing — the web build has no "
                        "version stamp (its footer falls back to 'dev build'); "
                        "run `python web/build_web.py version` before the web "
                        "deploy.")]
    if state.web is None:
        return [Finding(ERROR, "web",
                        f"web/version.json is unreadable ({state.web_problem}).")]
    web_version = str(state.web.get("version", ""))
    web_describe = str(state.web.get("describe", ""))
    if web_describe and normalize_version(web_describe) != web_version:
        return [Finding(ERROR, "web",
                        f"web/version.json is internally inconsistent: version "
                        f"'{web_version}' vs describe '{web_describe}'.")]
    if not web_version:
        return [Finding(ERROR, "web",
                        "web/version.json has no 'version' field.")]
    if not _DESCRIBE_RE.match(web_version):
        # without this, a garbled version ("garbage") sorted as (0,) -> "behind
        # the release" -> a WARNING, i.e. a broken bake looked repairable
        return [Finding(ERROR, "web",
                        f"web/version.json version '{web_version}' is not a "
                        "version string — re-bake it with "
                        "`python web/build_web.py version`.")]
    cmp = compare_versions(web_version, version)
    if cmp > 0:
        return [Finding(ERROR, "web",
                        f"web/version.json says '{web_version}' but the release "
                        f"version is '{version}' — the web cannot advertise a "
                        "version that was never released.")]
    if cmp < 0:
        return [Finding(WARNING, "web",
                        f"web/version.json says '{web_version}' while the "
                        f"release version is '{version}' — re-bake it "
                        "(`python web/build_web.py version`) before the next "
                        "web deploy.")]
    return []


def check_changelog(state: State, version: str, allow_dirty: bool = False):
    if not state.changelog_present:
        return [Finding(ERROR, "changelog",
                        "landing/changelog.json is missing — the download "
                        "page's \"What's new\" has no data (it is a tracked "
                        "file; regenerate with scripts/build_changelog.py).")]
    if state.changelog is None:
        return [Finding(ERROR, "changelog",
                        "landing/changelog.json is malformed: "
                        f"{state.changelog_problem}.")]
    if not state.changelog:
        # a missing file was already fatal; an EMPTY one is just as broken (the
        # page renders nothing) and is never legitimate: the tracked file ships
        # one entry per curated release, and build_changelog.py rewrites it
        # wholesale from CURATED.
        return [Finding(ERROR, "changelog",
                        "landing/changelog.json has no entries at all — the "
                        "download page's \"What's new\" would be empty "
                        "(regenerate it with scripts/build_changelog.py).")]

    findings = []
    entries = state.changelog
    keys = [version_key(e["version"]) for e in entries]
    if keys != sorted(keys, reverse=True):
        findings.append(Finding(WARNING, "changelog",
                                "landing/changelog.json is not sorted newest "
                                "first — the download page renders it in file "
                                "order."))
    newest = max(entries, key=lambda e: version_key(e["version"]))["version"]
    cmp = compare_versions(newest, version)
    if cmp > 0:
        findings.append(Finding(
            ERROR, "changelog",
            f"landing/changelog.json's newest entry is '{newest}' but the "
            f"release version is '{version}' — the download page would "
            "advertise a version that was never released (regenerate with "
            "scripts/build_changelog.py after the release)."))
    elif cmp < 0:
        findings.append(Finding(
            WARNING, "changelog",
            f"landing/changelog.json's newest entry is '{newest}' while the "
            f"release version is '{version}' — regenerate it "
            "(`python scripts/build_changelog.py`) after the release so the "
            "download page shows it."))

    released = base_version(version)
    # The gate asks "is a released tag being published?": HEAD exactly on a tag,
    # or -- with --allow-dirty -- a dirty tree sitting exactly on that tag (that
    # snapshot still carries the tag's version line, so the page needs its
    # summary).  --allow-dirty never removes this gate; an off-tag HEAD
    # publishes no tag, so there is no released version to curate.
    needs_curation = is_exact_tag(state.describe) or (
        allow_dirty and is_dirty_tag(state.describe))
    if needs_curation and not str(state.curated.get(released) or "").strip():
        findings.append(Finding(
            ERROR, "changelog",
            f"no curated summary for '{released}' in scripts/build_changelog.py "
            "CURATED — the public changelog would ship this release with "
            "nothing to show. An EMPTY string is not an entry either (TR-05): "
            "add the real text, and commit it BEFORE tagging — a post-tag edit "
            "makes every published version '-dirty'."))
    return findings


def check_staged_zip_metadata(payload: dict, stage: dict, expected_zip: str):
    """version.json's sha256/size must describe the zip its own `url` names.

    scripts/make_release.py writes {version, url, sha256, size} for
    `dist/release/NightSeam-<version>.zip` — the hash/size are taken from that
    file AFTER zipping it and `NightSeam-latest.zip` is copied from it — so both
    fields must be present, well formed and equal to the staged file's real
    values.  The landing page prints `size` as the download size, and a wrong
    hash publishes checksum metadata that matches nothing.
    """
    findings = []
    zips = stage.get("zips") or {}
    sha256 = stage.get("sha256") or {}
    target = str(payload.get("url") or "").rsplit("/", 1)[-1] or expected_zip
    recorded_sha = payload.get("sha256")
    recorded_size = payload.get("size")
    sha_ok = isinstance(recorded_sha, str) and bool(_SHA256_RE.match(recorded_sha))
    size_ok = (isinstance(recorded_size, int)
               and not isinstance(recorded_size, bool) and recorded_size > 0)

    if not sha_ok:
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/version.json records sha256 {recorded_sha!r} - it "
            f"must be the lowercase hex sha256 of {target}; re-run "
            "scripts/make_release.py."))
    if not size_ok:
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/version.json records size {recorded_size!r} - it "
            f"must be the positive byte size of {target}; re-run "
            "scripts/make_release.py."))
    if target not in zips:
        return findings               # the missing zip is reported separately
    if size_ok and recorded_size != zips[target]:
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/version.json says {target} is {recorded_size} bytes "
            f"but the staged file is {zips[target]} bytes - the recorded "
            "metadata describes a different build; re-run "
            "scripts/make_release.py."))
    if sha_ok and recorded_sha != sha256.get(target):
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/version.json says {target} has sha256 "
            f"{recorded_sha} but the staged file hashes to "
            f"{sha256.get(target)} - the recorded metadata describes a "
            "different build; re-run scripts/make_release.py."))
    return findings


def check_staged_zip_contents(stage: dict, expected_zip: str):
    """The staged zip must be readable and really contain NightSeam.exe."""
    entry = (stage.get("zip_entries") or {}).get(expected_zip)
    if not entry:
        return []
    if entry.get("error"):
        return [Finding(ERROR, "stage",
                        f"dist/release/{expected_zip} is not a readable zip "
                        f"({entry['error']}) - the published download would be "
                        "unusable; re-run scripts/make_release.py.")]
    members = entry.get("members") or []
    if "NightSeam.exe" not in members:
        shown = ", ".join(members[:6]) or "no entries"
        return [Finding(ERROR, "stage",
                        f"dist/release/{expected_zip} contains no NightSeam.exe "
                        f"(it holds: {shown}) - the package would install "
                        "nothing; re-run scripts/make_release.py.")]
    zip_exe = entry.get("exe_size")
    loose_exe = stage.get("exe_size")
    if zip_exe is not None and loose_exe is not None and zip_exe != loose_exe:
        return [Finding(ERROR, "stage",
                        f"dist/release/{expected_zip} carries a NightSeam.exe of "
                        f"{zip_exe} bytes while the staged file is {loose_exe} "
                        "bytes - the zip was built from a different exe; re-run "
                        "scripts/make_release.py.")]
    return []


def check_stage(state: State, version: str):
    stage = state.stage or {}
    if not stage.get("exists") or not stage.get("files"):
        return [Finding(WARNING, "stage",
                        "dist/release/ is empty — no desktop release is staged "
                        "yet (run scripts/make_release.py).")]

    findings = []
    expected_zip = f"{ZIP_PREFIX}{version}.zip"
    zips = stage.get("zips") or {}
    files = stage.get("files") or []
    stale = False

    if stage.get("version_json_error"):
        findings.append(Finding(ERROR, "stage",
                                "dist/release/version.json is not valid JSON "
                                f"({stage['version_json_error']}) - the landing "
                                "page reads it."))
        staged_version = ""
    elif stage.get("version_json") is None:
        findings.append(Finding(ERROR, "stage",
                                "dist/release/version.json is missing - the "
                                "landing page and the update check read it."))
        staged_version = ""
    else:
        payload = stage["version_json"]
        staged_version = str(payload.get("version", ""))
        if staged_version != version:
            # the whole stage belongs to another version: one precise finding
            # instead of one per derived artifact
            stale = True
            findings.append(Finding(
                ERROR, "stage",
                f"dist/release/version.json says '{staged_version}' but the "
                f"release version is '{version}' - the staged package is stale "
                "or wrong; re-run scripts/make_release.py."))
        url = str(payload.get("url", ""))
        if not stale and url != f"/downloads/{expected_zip}":
            findings.append(Finding(
                ERROR, "stage",
                f"dist/release/version.json url is '{url}' but the release "
                f"version needs '/downloads/{expected_zip}'."))
        if not stale:
            findings += check_staged_zip_metadata(payload, stage, expected_zip)

    ns = stage.get("ns_version")
    if ns is None:
        findings.append(Finding(ERROR, "stage",
                                "dist/release/ns-version.js is missing or "
                                "unreadable — the desktop app's update checker "
                                "loads it (window.__NS_RELEASE)."))
    elif staged_version and str(ns.get("version", "")) != staged_version:
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/ns-version.js advertises '{ns.get('version', '')}' "
            f"but version.json says '{staged_version}' — installed apps would "
            "be offered a different version than the download page shows."))
    elif not stale and str(ns.get("url", "")) != f"/downloads/{expected_zip}":
        # both files are written from ONE url in make_release.py, so a mismatch
        # means one of them was hand-edited or left over from another release
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/ns-version.js points at '{ns.get('url', '')}' but the "
            f"release download is '/downloads/{expected_zip}' — version.json and "
            "ns-version.js must name the same zip."))

    versioned = sorted(n for n in zips if n != LATEST_ZIP)
    if not versioned:
        findings.append(Finding(ERROR, "stage",
                                f"dist/release/ holds no {expected_zip}."))
    elif not stale and versioned != [expected_zip]:
        findings.append(Finding(
            ERROR, "stage",
            f"staged zip name(s) {versioned} do not match the release version "
            f"'{version}' (expected {expected_zip})."))

    if LATEST_ZIP not in zips:
        findings.append(Finding(ERROR, "stage",
                                f"dist/release/{LATEST_ZIP} is missing — the "
                                "download page's no-JS fallback link needs it."))
    elif expected_zip in zips and (
            zips[LATEST_ZIP] != zips[expected_zip]
            or (stage.get("sha256") or {}).get(LATEST_ZIP)
            != (stage.get("sha256") or {}).get(expected_zip)):
        findings.append(Finding(
            ERROR, "stage",
            f"dist/release/{LATEST_ZIP} differs from {expected_zip} "
            "(size/sha256) — the no-JS fallback link would serve a different "
            "build than the versioned download."))

    if "NightSeam.exe" not in files:
        findings.append(Finding(ERROR, "stage",
                                "the staged package contains no NightSeam.exe."))

    if expected_zip in zips:
        findings += check_staged_zip_contents(stage, expected_zip)

    # The staged exe must still BE the exe in dist/: build.ps1 can be re-run
    # after staging (or the stage can be left over from yesterday's build), and
    # then the zip -- whose own hash metadata stays self-consistent, because
    # version.json was written next to it -- would ship the older build under
    # the new version.  That is the 0.6.0 hazard in another costume.
    staged_exe_sha = stage.get("exe_sha256")
    if (staged_exe_sha and state.exe_sha256
            and staged_exe_sha != state.exe_sha256):
        findings.append(Finding(
            ERROR, "stage",
            "dist/release/NightSeam.exe is not the exe that is in dist/ right "
            "now (different sha256) - the staged package would ship an older "
            "build than the one being released; re-run "
            "scripts/make_release.py."))
    return findings


def evaluate(state: State, allow_dirty: bool = False,
             include_stage: bool = True):
    """All findings for a collected State (pure — no I/O)."""
    findings = check_describe(state.describe)
    if findings:
        return findings
    version = normalize_version(state.describe)
    findings += check_tree_state(state.describe, allow_dirty)
    findings += check_baked(state, version)
    findings += check_web(state, version)
    findings += check_changelog(state, version, allow_dirty)
    if include_stage:
        findings += check_stage(state, version)
    return findings


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def check_core_findings(root: Path = ROOT):
    """Run scripts/check_core.py against the real repo (skipped for synthetic
    roots used by tests). Keeps evaluate() pure while still blocking staging,
    because build.ps1 bundles static/ and would ship a stale core bake."""
    root = Path(root)
    if root.resolve() != ROOT.resolve():
        return []
    script = root / "scripts" / "check_core.py"
    if not script.is_file():
        return []
    spec = importlib.util.spec_from_file_location("check_core", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [Finding(ERROR, "core", problem) for problem in mod.scan()]


def check_catalog_findings(root: Path = ROOT):
    """TR-01 (0.6.32 review): the catalog DATA token guard was run by
    `check_js.py` alone - neither this gate nor `pre_tag_check.py` called it, so
    a COMMITTED catalog change with an unbumped `CATALOG_TOKEN` could reach a
    released build and keep serving the old ~4 MB file. Same shape as
    `check_core_findings`: real repo only, keeps evaluate() pure."""
    root = Path(root)
    if root.resolve() != ROOT.resolve():
        return []
    findings = []
    for name, label in (("check_catalog_token.py", "catalog-token"),
                        ("check_catalog_provenance.py", "catalog-provenance")):
        script = root / "scripts" / name
        if not script.is_file():
            continue
        module_name = name[:-3].replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        findings.extend(Finding(ERROR, label, problem) for problem in mod.scan())
    return findings


def check_source_module_findings(root: Path = ROOT):
    """Fail when required imported runtime modules are missing or untracked.

    This is intentionally a small release-boundary check for modules whose
    absence breaks a clean source checkout even though the current dirty tree
    still imports successfully.  Synthetic roots used by pure release tests do
    not have the repository's Git index, so they are skipped like the other
    source-tree guards.
    """
    root = Path(root)
    if root.resolve() != ROOT.resolve():
        return []
    findings = []
    required = set(REQUIRED_RUNTIME_MODULES)
    # Check source modules as a set, including newly added imports that an
    # explicit allowlist would miss. The baked version is generated at build
    # time and intentionally excluded from the Git source boundary.
    for directory, pattern in (("app", "*.py"), ("static/js", "*.js"),
                               ("web", "*.js"), ("core", "*.js")):
        for path in (root / directory).glob(pattern):
            if path.name != "_version_baked.py":
                required.add(path.relative_to(root).as_posix())
    rc, output = git(["ls-files", "--", "app", "static/js", "web", "core"], root)
    tracked_modules = set(output.splitlines()) if rc == 0 else set()
    for relative in sorted(required):
        path = root / relative
        if not path.is_file():
            findings.append(Finding(
                ERROR, "source-modules",
                f"required runtime module {relative} is missing from the source tree"))
            continue
        if relative not in tracked_modules:
            findings.append(Finding(
                ERROR, "source-modules",
                f"required runtime module {relative} is not tracked by Git"))
    return findings


def run(allow_dirty: bool = False, include_stage: bool = True,
        root: Path = ROOT, stream=sys.stdout):
    """Collect, evaluate, print the report.  Returns the process exit code."""
    state = collect(root)
    findings = evaluate(state, allow_dirty=allow_dirty,
                        include_stage=include_stage)
    findings += check_core_findings(root)
    findings += check_catalog_findings(root)
    findings += check_source_module_findings(root)
    describe = state.describe or "<git describe failed>"
    print(console_safe(f"NightSeam release check - {root}"), file=stream)
    print(console_safe(
        "authoritative version: git describe --tags --always --dirty "
        f"-> {describe!r} (version {normalize_version(state.describe)!r})"),
        file=stream)
    for finding in findings:
        print("  " + finding.render(), file=stream)
    print(console_safe(summarize(findings)), file=stream)
    return exit_code(findings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when the NightSeam release artifacts disagree with "
                    "`git describe --tags --always --dirty`.")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="accept a dirty tree / HEAD not on a tag (the "
                             "version then carries -dirty or a commit suffix) "
                             "instead of failing")
    parser.add_argument("--no-stage", action="store_true",
                        help="skip the dist/release artifact checks (used by "
                             "make_release.py, which is about to re-stage them)")
    args = parser.parse_args(argv)
    return run(allow_dirty=args.allow_dirty, include_stage=not args.no_stage)


if __name__ == "__main__":
    raise SystemExit(main())
