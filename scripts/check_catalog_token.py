"""Guard: web/catalog.json (a ~4 MB shipped DATA file) must be cache-busted.

`web/catalog.json` is not a JS module, so it cannot carry a `?v=` in an import.
The web app fetches it through ONE named token (`const CATALOG_TOKEN` in
`web/app.js`, used as `fetch('catalog.json?v=' + CATALOG_TOKEN)`), and
`web/catalog.manifest.json` records the bytes that belong to the current token.
When the catalog changes, the token MUST be bumped in the same change, or
returning visitors keep the stale ~4 MB file (heuristic HTTP caching) and the
data fix never reaches them. This is exactly the 0.6.12 catalog-id dedup.

Checks (all hard failures, exit 1):
  * web/app.js declares `const CATALOG_TOKEN = '<n>'`
  * exactly ONE `fetch('catalog.json` site (a second, token-less site would
    bypass the invalidation)
  * web/catalog.manifest.json exists and its token/sha256 match the tree
  * (when git is available) if web/catalog.json differs from HEAD, the token in
    web/app.js must differ from HEAD too
  * the tree matches the VERIFIED BASELINE embedded below (TR-01, 0.6.32
    review): bytes that differ from the baseline while the token still equals
    the baseline token = FAIL.

TR-01 (0.6.32 review): the git rule above only fires while the change is
UNCOMMITTED (`git diff HEAD` is empty after the commit), and the manifest/token
pair is self-consistent as soon as the manifest sha is refreshed - which is
exactly what the documented workflow asks for. A COMMITTED catalog change with an
unbumped token therefore passed, and the ~4 MB file kept being served under the
old URL to every returning visitor. The baseline below is the commit-state
independent copy of the js-tokens idea (scripts/check_js_tokens.py): it records
the last state in which the bytes/token pairing was VERIFIED, so the rule does
not care whether the bad change is committed. Refresh it in the SAME change that
legitimately bumps the token:

    python scripts/check_catalog_token.py --update

`--update` REFUSES while a missed bump is present, so the documented remedy can
never record an unbumped token and bless the defect. Wire-up: scripts/check_js.py
runs this guard, and scripts/pre_tag_check.py + scripts/check_release.py block a
tag/staging on it.

Usage: python scripts/check_catalog_token.py [--update]
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
CATALOG = ROOT / "web" / "catalog.json"
MANIFEST = ROOT / "web" / "catalog.manifest.json"
SELF = Path(__file__).resolve()

_TOKEN_RE = re.compile(r"const CATALOG_TOKEN = '(\d+)'")
_FETCH_RE = re.compile(r"fetch\('catalog\.json")

# The verified bytes/token pairing for web/catalog.json. GENERATED DATA:
# `--update` rewrites the assignment between the two marker LINES below - never
# edit it by hand, and keep the marker lines exactly as they are.
_BASELINE_START = "# --- BEGIN GENERATED VERIFIED CATALOG BASELINE ---"
_BASELINE_END = "# --- END GENERATED VERIFIED CATALOG BASELINE ---"
# --- BEGIN GENERATED VERIFIED CATALOG BASELINE ---
_VERIFIED_BASELINE_JSON = r"""
{
 "sha256": "55369ef0e7636a1979b69205227d67ab0262862eaeef9ffeeb58d77b99920c49",
 "token": "10"
}
"""

# --- END GENERATED VERIFIED CATALOG BASELINE ---


def _git(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        # Decode explicitly: with `text=True` alone Python uses the PLATFORM
        # default (cp1252 on Windows), so a diff containing any non-ASCII byte -
        # an em dash in a comment is enough - raised UnicodeDecodeError, the
        # bare except turned it into None, and the HEAD harden below then
        # reported "CATALOG_TOKEN does not [differ]" for a perfectly correct
        # bump. That is a false failure that blocks a valid commit.
        return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=30)
    except Exception:  # noqa: BLE001 - git may be unavailable
        return None


def _git_ok() -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"])
    return bool(r and r.returncode == 0 and r.stdout.strip() == "true")


def token_of(app_text: str) -> str | None:
    m = _TOKEN_RE.search(app_text)
    return m.group(1) if m else None


def load_baseline(src: str | None = None) -> dict:
    """The embedded verified pairing; {} when missing/unreadable."""
    text = SELF.read_text(encoding="utf-8") if src is None else src
    start = _BASELINE_START + "\n"
    end = "\n" + _BASELINE_END
    if start not in text or end not in text:
        return {}
    block = text.split(start, 1)[1].split(end, 1)[0]
    m = re.search(r'_VERIFIED_BASELINE_JSON\s*=\s*r?"""(.*?)"""', block, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def baseline_problems(token: str | None, sha: str, baseline: dict) -> list[str]:
    """The commit-state independent rule (TR-01).

    * bytes differ from the baseline while the token still EQUALS the baseline
      token -> the bytes were edited WITHOUT a bump -> FAIL (this is the case
      the git rule cannot see once it is committed);
    * bytes differ AND the token moved -> the pairing is new; refresh the
      baseline in the same change (`--update`) - until then this is reported, so
      the NEXT unbumped edit cannot hide behind a stale baseline;
    * token moved over unchanged bytes -> the legitimate post-tag repair shape;
      it only costs one revalidation, and like the case above it needs a refresh.
    """
    base_token = str(baseline.get("token") or "")
    base_sha = str(baseline.get("sha256") or "")
    if not base_token or not base_sha:
        return ["no verified catalog baseline embedded in "
                "scripts/check_catalog_token.py - run `python "
                "scripts/check_catalog_token.py --update` and commit it"]
    problems: list[str] = []
    if sha != base_sha and token == base_token:
        problems.append(
            f"web/catalog.json bytes differ from the verified baseline but "
            f"CATALOG_TOKEN in web/app.js is still '{token}' - bump the token "
            f"(and web/catalog.manifest.json) in the SAME change, then refresh "
            f"the baseline")
    elif sha != base_sha or token != base_token:
        problems.append(
            "the verified catalog baseline is stale (run `python "
            "scripts/check_catalog_token.py --update` and commit it with this "
            "change)")
    return problems


def update_refusals(token: str | None, sha: str,
                    baseline: dict | None = None) -> list[str]:
    """Missed bumps that FORBID refreshing the baseline (TR-01).

    `--update` documents itself as the remedy for a stale baseline, so running it
    on a tree whose bytes changed without a token bump would RECORD the unbumped
    token and make the same tree pass - the bypass `check_js_tokens.py` already
    refuses (review R2-02). Refresh only when the token really moved."""
    base = load_baseline() if baseline is None else baseline
    base_token = str(base.get("token") or "")
    base_sha = str(base.get("sha256") or "")
    if base_token and base_sha and sha != base_sha and token == base_token:
        return [f"web/catalog.json bytes changed while CATALOG_TOKEN is still "
                f"'{token}' - bump the token first, then re-run --update"]
    return []


def write_baseline(script: Path | None = None) -> Path | None:
    """Rewrite the embedded baseline block, or None when REFUSED."""
    p = script or SELF
    src = p.read_text(encoding="utf-8")
    token = token_of(APP.read_text(encoding="utf-8"))
    sha = hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    if update_refusals(token, sha, load_baseline(src)):
        return None
    start = _BASELINE_START + "\n"
    end = "\n" + _BASELINE_END
    if start not in src or end not in src:
        raise RuntimeError(f"baseline markers missing from {p}")
    block = (f'{start}_VERIFIED_BASELINE_JSON = r"""\n'
             + json.dumps({"sha256": sha, "token": token}, indent=1,
                          sort_keys=True)
             + f'\n"""\n{end}')
    p.write_text(src.split(start, 1)[0] + block + src.split(end, 1)[1],
                 encoding="utf-8")
    return p


def scan() -> list[str]:
    problems: list[str] = []
    app_text = APP.read_text(encoding="utf-8")

    token = token_of(app_text)
    if token is None:
        problems.append("web/app.js has no `const CATALOG_TOKEN = '<n>'`")

    sites = len(_FETCH_RE.findall(app_text))
    if sites != 1:
        problems.append(f"expected exactly 1 `fetch('catalog.json...')` site, found {sites}")

    sha = hashlib.sha256(CATALOG.read_bytes()).hexdigest()

    if not MANIFEST.is_file():
        problems.append("web/catalog.manifest.json is missing")
    else:
        try:
            man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"web/catalog.manifest.json is not valid JSON: {exc}")
            man = {}
        if token is not None and str(man.get("token")) != token:
            problems.append(
                f"manifest token {man.get('token')!r} != CATALOG_TOKEN {token!r} "
                f"(bump both together)")
        if man.get("sha256") != sha:
            problems.append(
                "web/catalog.json bytes changed but web/catalog.manifest.json "
                "was not updated (bump CATALOG_TOKEN and the manifest sha together)")

    # TR-01: commit-state independent - the embedded verified baseline.
    problems += baseline_problems(token, sha, load_baseline())

    # Hardening: a catalog change relative to HEAD must come with a token change.
    if _git_ok():
        cat_changed = _git(["diff", "--quiet", "HEAD", "--", "web/catalog.json"])
        if cat_changed is not None and cat_changed.returncode != 0:
            app_diff = _git(["diff", "HEAD", "--", "web/app.js"])
            if app_diff is None:
                # _git returns None only when the call itself failed. Claiming
                # "the token did not differ" in that case is a LIE that blocks a
                # correct commit, so say what actually went wrong.
                problems.append(
                    "could not read `git diff HEAD -- web/app.js` to check the "
                    "catalog-token bump (git unavailable or its output "
                    "undecodable?)")
            elif "CATALOG_TOKEN" not in (app_diff.stdout or ""):
                problems.append(
                    "web/catalog.json differs from HEAD but CATALOG_TOKEN in "
                    "web/app.js does not (bump the token in the same change)")
    return problems


def main(argv: list[str] | None = None) -> int:
    """argv is explicit on purpose: scripts/check_js.py embeds this module and
    calls `main()` with no arguments, and it must NOT inherit the embedding
    process's sys.argv (TR-06, 0.6.32 review: `check_js.py --update` used to
    reach `check_js_tokens.py`'s flags through exactly that leak). The
    `__main__` block below passes sys.argv explicitly."""
    args = list(argv or [])
    if "--update" in args:
        if write_baseline() is None:
            print("  catalog-token: REFUSED - web/catalog.json bytes changed "
                  "while CATALOG_TOKEN did not; bump the token first, then "
                  "re-run --update")
            print("check_catalog_token: REFUSED")
            return 1
        print("check_catalog_token: wrote the verified baseline into "
              "scripts/check_catalog_token.py - commit it with this change")
        return 0
    problems = scan()
    for p in problems:
        print("  catalog-token: " + p)
    if not _git_ok():
        # TO-04 (TL3): the committed-change rule is the ONLY git-based check for
        # "manifest bumped, CATALOG_TOKEN not", and git being missing/denied used
        # to make it vanish without a word - the guard then printed OK for a
        # check that never ran. The embedded baseline above still runs.
        print("  catalog-token: NOTE - git is unavailable, so the committed-change "
              "check (web/catalog.json vs HEAD + CATALOG_TOKEN) did NOT run")
    print("check_catalog_token:", "FAIL (%d)" % len(problems) if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
