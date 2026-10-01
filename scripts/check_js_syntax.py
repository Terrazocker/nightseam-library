"""Parse every JS module that actually ships with Node, so a module that
cannot even be parsed (duplicate `const`, stray brace, unterminated template
literal) is caught before it reaches the working tree.

None of the sibling checkers parse JavaScript - import paths, DOM ids and
undefined identifiers are all text matching - so a module with a syntax error
looked clean. The measured case: a duplicate `const` in `web/app.js` survived
`check_js.py` and was only found by a manual `node --check`.

Covers the desktop UI (`static/js/*.js`), the web app (`web/*.js`) - the same
two graphs as the sibling checkers - plus the SHARED CORE source (`core/*.js`)
and both baked, content-hashed trees (`web/core/<hash>/`,
`static/js/core/<hash>/`), which are copies the browser loads verbatim.
(TO-06, 0.6.24 review: this docstring and AGENTS.md claimed the core coverage
while `ROOTS` excluded it.)

Usage:
    python scripts/check_js_syntax.py                  # everything that ships
    python scripts/check_js_syntax.py --path web       # one app
    python scripts/check_js_syntax.py --path web/app.js

Exit codes: 0 = every module parses, 1 = at least one syntax error, 2 = the
command line was wrong OR the check could not run at all. A missing node, a node
that cannot be spawned and an unwritable scratch directory are all HARD failures
with a FATAL line - never a silent skip: the aggregate `check_js.py` must not
report OK while the only JS parser in the bundle did nothing (TO-03, 0.6.24
review; the same rule as "a browser that cannot launch is a hard failure").
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# the JS that actually ships: the desktop UI and the web twin (flat modules)
ROOTS = [REPO / "static" / "js", REPO / "web"]

# ... plus the shared core and its baked copies, which are scanned RECURSIVELY:
# `core/` is the authored source and `<app>/core/<hash>/` are byte-for-byte copies
# the browser loads, so a syntax error in any of them ships (TO-06).
CORE_ROOTS = [REPO / "core", REPO / "web" / "core", REPO / "static" / "js" / "core"]

# never shipped / never ours (a --path can point at one of these by accident)
IGNORED_DIRS = {"node_modules", "whale", "dev"}

# scratch life: a throw-away dir under logs/ (gitignored), removed in a
# finally block - no permanent new directory, no developer-specific path
SCRATCH_PREFIX = ".js-syntax-"
CAPTURE_NAME = "node.out"

LINE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)$", re.M)
ERROR_RE = re.compile(
    r"^(?P<kind>(?:Syntax|Type|Range|Reference)?Error):\s*(?P<msg>.+?)\s*$",
    re.M)


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def _ignored(path: Path, base: Path) -> bool:
    if path.name.endswith(".min.js"):
        return True
    # relative to the scan root AND to the repo: `--path <repo>/web/node_modules`
    # must stay ignored even though node_modules IS that scan root
    for root in (base, REPO):
        try:
            rel = path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if any(part in IGNORED_DIRS for part in rel.parts[:-1]):
            return True
    return False


def discover(root: Path) -> list[Path]:
    """`*.js` in `root` plus `root/js/*.js` if it exists - the discovery shape
    the sibling checkers use. Non-recursive by design: only shipping modules."""
    if root.is_file():
        return [] if _ignored(root, root.parent) else [root]
    if not root.is_dir():
        return []
    found = sorted(root.glob("*.js"))
    jsdir = root / "js"
    if jsdir.is_dir():
        found += sorted(jsdir.glob("*.js"))
    return [p for p in found if not _ignored(p, root)]


def discover_tree(root: Path) -> list[Path]:
    """Every `*.js` UNDER `root`, recursively.

    The baked core trees live one level down (`<root>/<hash>/*.js`), which the
    flat `discover()` cannot see - the scope gap TO-06 recorded."""
    if not root.is_dir():
        return []
    return [p for p in sorted(root.rglob("*.js")) if not _ignored(p, root)]


def collect(paths: list[str]) -> list[Path]:
    """Modules for the given CLI paths ([] = the default ship set)."""
    if not paths:
        files: list[Path] = []
        for root in ROOTS:
            files += discover(root)
        for root in CORE_ROOTS:
            files += discover_tree(root)
        return list(dict.fromkeys(files))
    files = []
    for raw in paths:
        files += discover(Path(raw))
    # one module can be named twice (e.g. --path web --path web/app.js)
    return list(dict.fromkeys(files))


def shown(path: Path) -> str:
    """Repo-relative posix path when possible, absolute otherwise."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


# --------------------------------------------------------------------------
# node
# --------------------------------------------------------------------------
def find_node() -> str | None:
    return shutil.which("node")


def node_usable(node: str) -> str:
    """'' when node runs here, else the reason it cannot be used."""
    try:
        proc = subprocess.run([node, "--version"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60)
    except OSError as exc:                    # blocked spawn / broken install
        return str(exc)
    except subprocess.TimeoutExpired:
        return "node --version timed out"
    return "" if proc.returncode == 0 else f"node --version exited {proc.returncode}"


def _scratch_dir() -> Path | None:
    """A fresh throw-away directory, preferably inside the repo (logs/ is
    gitignored; the platform temp dir is not writable for spawned processes
    in the sandbox, so it is only the fallback)."""
    for base in (REPO / "logs", Path(tempfile.gettempdir())):
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        for i in range(100):
            candidate = base / f"{SCRATCH_PREFIX}{os.getpid()}-{i}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            except OSError:
                break
            return candidate
    return None


def _rewrite(text: str, tmp: Path, label: str) -> str:
    """Node reports the temp copy; print the module that actually ships."""
    for form in (tmp.as_uri(), str(tmp), tmp.name):
        text = text.replace(form, label)
    return text


def check_one(node: str, src: Path, scratch: Path, index: int,
              capture: Path) -> tuple[bool, str]:
    """Copy `src` to a temp `.mjs` (so `import`/`export` are legal to parse)
    and let node check it. Returns (ok, one-line message)."""
    tmp = scratch / f"{index:03d}_{src.name}.mjs"
    label = shown(src)
    tmp.write_bytes(src.read_bytes())
    try:
        # node's diagnostics go to a FILE, never a pipe: piped stdio is denied
        # in the sandbox ("Zugriff verweigert" / WinError 5)
        with open(capture, "w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.run([node, "--check", str(tmp)],
                                  stdout=fh, stderr=subprocess.STDOUT,
                                  cwd=str(scratch), timeout=120)
    finally:
        tmp.unlink(missing_ok=True)
    if proc.returncode == 0:
        return True, ""
    text = _rewrite(capture.read_text(encoding="utf-8", errors="replace"),
                    tmp, label)
    err = ERROR_RE.search(text)
    line = LINE_RE.search(text)
    where = f"{label}:{line.group('line')}" if line else label
    if err:
        return False, f"{where}: {err.group('kind')}: {err.group('msg')}"
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return False, f"{where}: {first or f'node exited {proc.returncode}'}"


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def parse_args(argv: list[str]) -> list[str]:
    """Paths to check; [] means the default ship set."""
    paths: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-p", "--path"):
            i += 1
            if i >= len(argv):
                raise ValueError("--path needs a directory or file argument")
            paths.append(argv[i])
        elif arg in ("-h", "--help"):
            print(__doc__.strip())
            raise SystemExit(0)
        elif arg.startswith("-"):
            raise ValueError(f"unknown option {arg!r}")
        else:
            paths.append(arg)               # a bare path works too
        i += 1
    return paths


def main(argv=None) -> int:
    """argv=None -> the default ship set (that is how check_js.py embeds it,
    and it deliberately does NOT read the embedding process's sys.argv)."""
    argv = list(argv) if argv is not None else []
    try:
        paths = parse_args(argv)
    except SystemExit as exc:                       # --help
        return int(exc.code or 0)
    except ValueError as exc:
        print(f"check_js_syntax: {exc}")
        return 2

    if paths:
        missing = [p for p in paths if not Path(p).exists()]
        if missing:                     # a typo must not pass as "clean"
            for raw in missing:
                print(f"check_js_syntax: no such file or directory: {raw}")
            return 2

    files = collect(paths)
    if not files:
        print("check_js_syntax: no js modules found - nothing to check")
        return 0

    node = find_node()
    if not node:
        # TO-03: the repo rule is "a missing node is a HARD failure, never a skip"
        # (AGENTS.md, the same rule as a browser that cannot launch). Returning 0
        # let the aggregate check_js.py report OK while the only JS *parser* in the
        # bundle did nothing.
        print("check_js_syntax FATAL: node is not installed - the JS syntax check "
              "cannot run")
        return 2
    reason = node_usable(node)
    if reason:
        print(f"check_js_syntax FATAL: cannot run node ({reason})")
        return 2

    scratch = _scratch_dir()
    if scratch is None:
        print("check_js_syntax FATAL: no writable scratch directory")
        return 2

    capture = scratch / CAPTURE_NAME
    errors = 0
    checked = 0
    try:
        for i, src in enumerate(files):
            try:
                ok, detail = check_one(node, src, scratch, i, capture)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"SYNTAX  {shown(src)}: cannot check: {exc}")
                errors += 1
                continue
            checked += 1
            if not ok:
                print(f"SYNTAX  {detail}")
                errors += 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"checked {checked} js module(s), {errors} syntax error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
