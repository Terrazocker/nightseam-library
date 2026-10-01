"""Guard: a shipped web module whose BYTES changed must bump its cache token.

XL-01 (review 0.6.24, release-integrity half): the v0.6.24 tag shipped the
0.6.23 per-module cache tokens while carrying 0.6.24 bytes
(`index.html app.js?v=59`, imports `astro.js?v=9`, `horizon.js?v=40`,
`webstore.js?v=19`, `format.js?v=16`, `nightrank.js?v=6`); the bump had to be
repaired in the post-tag commit 208f7d6. AGENTS.md documents the manual rule
("bump the token of every module whose BEHAVIOUR changed in the SAME commit")
and `check_catalog_token.py` covers the catalog DATA file, but nothing compared
a module's bytes against its own token, so a missed bump reached a tag.

THIS guard closes that hole with the same idea as `web/catalog.manifest.json` +
`CATALOG_TOKEN`: a VERIFIED BASELINE embedded below as JSON records the last
state in which the bytes/token pairing was verified

    {"modules": {"app.js": {"token": "60", "sha256": "<64 hex>"}, ...}}

and the one rule is the one that bites users:

  * module bytes differ from the baseline while the module's token still equals
    the baseline token -> the bytes were edited WITHOUT a bump -> FAIL;
  * bytes changed AND the token moved -> the pairing is new; refresh the
    baseline (`--update`) and commit it with that change;
  * token moved over unchanged bytes -> allowed (that is the post-tag repair of
    a missed bump, 208f7d6; it only costs one revalidation).

The baseline is therefore the last VERIFIED state, never the tag's declared
token. That matters: the six modules 208f7d6 repaired (app 59->60, astro 9->10,
format 16->17, horizon 40->41, nightrank 6->7, webstore 19->20) already differ
from the tag's stale token, so a tag-based rule cannot see a forgot-bump on
exactly the modules the certified defect named (review XL-01-F1). With the
baseline, editing `web/astro.js` without moving its token fails whether the
stale token reads 9 (the tag) or 10 (the current tree).

Hashing is LINE-ENDING-NORMALIZED (CR bytes stripped before sha256): this repo
sets `core.autocrlf=true`, so a routine `git checkout` writes web/ modules with
CRLF while the baseline was built from LF. Comparing raw bytes produced spurious
"changed but its cache token is still ?v=N" failures on a fresh Windows clone
(review XL-01-F2), which would block `check_js.py` and the pre-tag gate.

Historical replay: `scan_at(ref, base_ref)` compares two COMMITS with git's own
view (`git diff --quiet`), so it reports the v0.6.24 defect exactly
(`scan_at("v0.6.24", "v0.6.23")` -> the six stale modules).

Wiring: `scripts/check_js.py` runs `scan()` and `scripts/pre_tag_check.py` runs
it before a tag. Refresh the baseline ONLY when the pairing legitimately moved:

    python scripts/check_js_tokens.py --update

which rewrites the generated block below; commit the script with that change.
`python scripts/check_js_tokens.py --self-test` pins the two behaviours the
repair review demanded (the 0.6.25 shape and CRLF normalization).

Only web/ modules (and the generated `web/core.js` stub, whose token lives in
`web/app.js`) carry cache tokens - `static/` is loaded from disk by the
pywebview shell.

Usage: python scripts/check_js_tokens.py [--update] [--self-test]
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
CORE = ROOT / "core"
SELF = Path(__file__).resolve()

# index.html: <script type="module" src="app.js?v=60"> + stylesheet links
# (other attributes may precede src/href, e.g. type="module")
_HTML_TOKEN_RE = re.compile(
    r'(?:src|href)\s*=\s*"([\w./-]+\.(?:js|css))\?v=(\d+)"')
# the entry point that carries every module's token
_APP_TOKEN_RE = re.compile(r"app\.js\?v=(\d+)")
# a module import/re-export with a token: "./astro.js?v=10"
_IMPORT_TOKEN_RE = re.compile(r"[\"'](\./[\w./-]+\.js)\?v=(\d+)")

# The verified bytes/token pairing for the shipped web modules. GENERATED DATA:
# `--update` rewrites the assignment between the two marker LINES below - never
# edit it by hand, and keep the marker lines exactly as they are.
_BASELINE_START = "# --- BEGIN GENERATED VERIFIED BASELINE ---"
_BASELINE_END = "# --- END GENERATED VERIFIED BASELINE ---"
# --- BEGIN GENERATED VERIFIED BASELINE ---
_VERIFIED_BASELINE_JSON = r"""
{
 "modules": {
  "app.js": {
   "sha256": "e23126f83c1be550094f7e5b3ff9f014405880201d7383cb377b9fb8bfbb406f",
   "token": "137"
  },
  "astro.js": {
   "sha256": "701e6c144d71e29f4d52193efdd7100afddc6711ff1307bc4f43f04b5a3c9a89",
   "token": "30"
  },
  "core.js": {
   "sha256": "0ee3dd575fec693eca9b1dda20d89d81a2719bf911b6a3d5261809f8fb68efa1",
   "token": "44"
  },
  "format.js": {
   "sha256": "3a242e96a125ec1f021ce8c680304c67db802f4645acaa2a04f151cca85776c0",
   "token": "37"
  },
  "horizon.js": {
   "sha256": "734e004817c73253c209c8c76560d25f95dca2a1b058c64923b6f3af95772fe5",
   "token": "69"
  },
  "nightrank.js": {
   "sha256": "439739548f095b801d303ba57a050341e9bfabe9455aba8f6bbd19458c10a4b1",
   "token": "29"
  },
  "style.css": {
   "sha256": "55badf7b5088038b4c5d8e4c9dbf9cb529bbb39e8d143d7c945a13e535f11453",
   "token": "44"
  },
  "themes.css": {
   "sha256": "d9672cebd86596884594d1b83ab1cb616236b512ea6529278ad442381f81fa3a",
   "token": "16"
  },
  "viztheme.js": {
   "sha256": "78c5502fff9083dd46136133d95383148e69bf97c4da3b1521722177f727e27a",
   "token": "6"
  },
  "webstore.js": {
   "sha256": "db09b96797c2508c61cc53ea55f633b509820401a9741ec08fa54b64e34d53b6",
   "token": "41"
  }
 }
}
"""
# --- END GENERATED VERIFIED BASELINE ---


# ------------------------------------------------------------------ helpers

def _norm(data: bytes) -> bytes:
    """Line-ending-normalized bytes: CRLF and CR both hash like LF."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _sha(data: bytes) -> str:
    return hashlib.sha256(_norm(data)).hexdigest()


def _read_web_file(web: Path, name: str) -> str | None:
    p = web / name
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


class WebTree:
    """One snapshot of the shipped web tree: declared tokens + file bytes."""

    def __init__(self, tokens: dict[str, str], contents: dict[str, bytes],
                 problems: list[str] | None = None):
        self.tokens = tokens
        self.contents = contents
        self.problems = problems or []

    def hashes(self) -> dict[str, str]:
        return {name: _sha(data) for name, data in self.contents.items()}


def _collect_tokens(index: str | None, app: str | None,
                    read) -> dict[str, str]:
    """{relative module path -> declared cache token}.

    `read` resolves a module name to its bytes (None when it does not exist) so
    the same parser works for the working tree and for a git ref."""
    tokens: dict[str, str] = {}
    if index:
        for ref, tok in _HTML_TOKEN_RE.findall(index):
            tokens[ref.lstrip("./")] = tok
        m = _APP_TOKEN_RE.search(index)
        if m:
            tokens["app.js"] = m.group(1)
    if app:
        for ref, tok in _IMPORT_TOKEN_RE.findall(app):
            name = ref[2:].lstrip("./")         # './astro.js' -> 'astro.js'
            if name in tokens and tokens[name] != tok:
                # one module, two tokens - the loader would serve two versions
                tokens[name] = f"{tokens[name]}/{tok}"
            else:
                tokens.setdefault(name, tok)
    return tokens


def load_web_modules(web: Path) -> WebTree:
    """The live web tree at `web`."""
    tokens = _collect_tokens(_read_web_file(web, "index.html"),
                             _read_web_file(web, "app.js"),
                             lambda n: (web / n).read_bytes()
                             if (web / n).is_file() else None)
    contents: dict[str, bytes] = {}
    problems: list[str] = []
    for name in sorted(tokens):
        p = web / name
        if not p.is_file():
            problems.append(f"web/{name} is referenced with a cache token but "
                            f"does not exist")
            continue
        contents[name] = p.read_bytes()
    return WebTree(tokens, contents, problems)


def describe_modules_from_git(ref: str) -> WebTree:
    """The same view of the web tree as it exists at git ref `ref`."""
    tokens = _collect_tokens(
        _decode(_git_show(ref, "web/index.html")),
        _decode(_git_show(ref, "web/app.js")),
        lambda n: _git_show(ref, f"web/{n}"))
    contents: dict[str, bytes] = {}
    for name in sorted(tokens):
        data = _git_show(ref, f"web/{name}")
        if data is not None:
            contents[name] = data
    return WebTree(tokens, contents)


def find_missed_bumps(baseline_tokens: dict, baseline_hashes: dict,
                      cur: WebTree, base_label: str) -> list[str]:
    """The one guarded direction: bytes changed but the token did NOT move.

    `baseline_tokens` / `baseline_hashes` describe the last VERIFIED state (the
    embedded baseline, or another commit for `scan_at`). A token that moved over
    unchanged bytes passes (the 208f7d6 repair shape). A module with no baseline
    entry is new and cannot have missed a bump - `stale_manifest_entries`
    reports it so the caller can refresh the baseline."""
    problems: list[str] = []
    hashes = cur.hashes()
    for name, tok in sorted(cur.tokens.items()):
        base_tok = baseline_tokens.get(name)
        if base_tok is None:
            continue
        if baseline_hashes.get(name) == hashes.get(name):
            continue                    # bytes identical - nothing was edited
        if tok != base_tok:
            continue                    # bytes AND token moved - a real bump
        problems.append(
            f"web/{name} changed but its cache token is still ?v={tok} "
            f"(bump every module whose bytes changed in the same commit, then "
            f"re-check {base_label})")
    return problems


def compare_modules(base: WebTree, cur: WebTree,
                    base_ref: str = "the baseline") -> list[str]:
    """Compatibility wrapper for callers that hold two snapshots (the original
    API of this guard): missed-bump findings for `cur` against `base`."""
    return find_missed_bumps(base.tokens, base.hashes(), cur, base_ref)


def missing_from_tree(baseline_tokens: dict, cur: WebTree) -> list[str]:
    """Baseline modules the current tree no longer ships (a deletion is a
    deliberate change too: drop the row with `--update`)."""
    return sorted(name for name in baseline_tokens if name not in cur.tokens)


def stale_manifest_entries(baseline_tokens: dict, baseline_hashes: dict,
                           cur: WebTree) -> list[str]:
    """Modules whose verified pairing moved, so the baseline row is obsolete.

    A token that moved (bytes identical or not) makes the baseline stale: the
    baseline is what records "this token names these bytes", so it must be
    refreshed and committed with the change. These are NOT findings."""
    stale: list[str] = []
    hashes = cur.hashes()
    for name, tok in sorted(cur.tokens.items()):
        base_tok = baseline_tokens.get(name)
        base_hash = baseline_hashes.get(name)
        if base_tok is None or base_hash is None:
            stale.append(name)
        elif tok != base_tok or base_hash != hashes.get(name):
            stale.append(name)
    return stale


# ------------------------------------------------------- verified baseline

def _baseline_block(modules: dict[str, dict]) -> str:
    payload = json.dumps({"modules": modules}, indent=1, sort_keys=True)
    return (_BASELINE_START + "\n"
            + '_VERIFIED_BASELINE_JSON = r"""\n'
            + payload + '\n"""\n'
            + _BASELINE_END)


def load_baseline(text: str | None = None) -> dict[str, dict]:
    """{module path -> {'token': str, 'sha256': str}} from the embedded block.

    `text` lets callers/self-tests supply a different module source; by default
    the block currently in THIS file is read. The marker is matched as a whole
    LINE (`\\n` suffix) so the constant definitions above never match."""
    src = text if text is not None else SELF.read_text(encoding="utf-8")
    start = _BASELINE_START + "\n"
    end = "\n" + _BASELINE_END
    if start not in src or end not in src:
        return {}
    body = src.split(start, 1)[1].split(end, 1)[0]
    if "_VERIFIED_BASELINE_JSON" not in body or 'r"""' not in body:
        return {}
    payload = body.split('r"""', 1)[1].split('"""', 1)[0]
    try:
        data = json.loads(payload)
    except Exception:  # noqa: BLE001 - a corrupt baseline must not pass
        return {}
    modules = data.get("modules")
    return modules if isinstance(modules, dict) else {}


def baseline_rows(cur: WebTree) -> dict[str, dict]:
    hashes = cur.hashes()
    return {name: {"token": tok, "sha256": hashes.get(name, "")}
            for name, tok in sorted(cur.tokens.items())}


def _baseline_maps(baseline: dict[str, dict]) -> "tuple[dict, dict]":
    return ({n: str(r.get("token")) for n, r in baseline.items()},
            {n: str(r.get("sha256")) for n, r in baseline.items()})


def update_refusals(cur: WebTree,
                    baseline: dict[str, dict] | None = None) -> list[str]:
    """Missed bumps that FORBID refreshing the baseline (review R2-02).

    `--update` documents itself as the remedy for a stale baseline, so running
    it on a tree whose bytes changed without a token bump used to RECORD the
    unbumped token and make the same tree pass - the bypass the round-2 review
    reproduced. Refresh only the rows whose token actually moved."""
    base = load_baseline() if baseline is None else baseline
    tokens, hashes = _baseline_maps(base)
    return find_missed_bumps(tokens, hashes, cur, "the verified baseline")


def refreshed_rows(cur: WebTree, baseline: dict[str, dict]) -> dict[str, dict]:
    """The rows `--update` may write: a module whose token did NOT move keeps
    its verified hash (any byte change there is a refused missed bump), a module
    whose token moved records the new pairing, and new modules are added."""
    hashes = cur.hashes()
    rows: dict[str, dict] = {}
    for name, tok in sorted(cur.tokens.items()):
        base = baseline.get(name)
        if base is not None and str(base.get("token")) == tok:
            rows[name] = {"token": tok, "sha256": str(base.get("sha256"))}
        else:
            rows[name] = {"token": tok, "sha256": hashes.get(name, "")}
    return rows


def write_baseline(cur: WebTree, script: Path | None = None) -> Path | None:
    """Rewrite the embedded baseline block, or return None when REFUSED.

    Refuses while `update_refusals` is non-empty: the token must be bumped in
    the same change first, so the documented remedy can never bless the exact
    defect the guard exists to catch (review R2-02). The only file this
    generated data may touch is the guard itself (or `script`, for tests)."""
    p = script or SELF
    src = p.read_text(encoding="utf-8")
    if update_refusals(cur, load_baseline(src)):
        return None
    start = _BASELINE_START + "\n"
    end = "\n" + _BASELINE_END
    if start not in src or end not in src:
        raise RuntimeError(f"baseline markers missing from {p}")
    head = src.split(start, 1)[0]
    tail = src.split(end, 1)[1]
    p.write_text(head
                 + _baseline_block(refreshed_rows(cur, load_baseline(src)))
                 + tail, encoding="utf-8")
    return p


# ------------------------------------------------------------------ git

def _decode(data: bytes | None) -> str | None:
    return data.decode("utf-8", "replace") if data is not None else None


def _git(args: list[str], timeout: int = 30):
    try:
        return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=timeout)
    except Exception:  # noqa: BLE001 - git may be unavailable
        return None


def _git_bytes(args: list[str], timeout: int = 30) -> bytes | None:
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                           timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    return r.stdout if r.returncode == 0 else None


def _git_show(ref: str, path: str) -> bytes | None:
    return _git_bytes(["show", f"{ref}:{path}"])


def _git_diff_quiet(base_ref: str, cur_ref: str, path: str) -> bool | None:
    """True when `path` differs between the two refs (git's own, EOL-normalized
    view). None when git cannot answer."""
    r = _git(["diff", "--quiet", base_ref, cur_ref, "--", path])
    if r is None:
        return None
    return r.returncode != 0


def _git_ok() -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"])
    return bool(r and r.returncode == 0 and r.stdout.strip() == "true")


# ------------------------------------------------------------------ scan

def scan_at(ref: str, base_ref: str) -> list[str]:
    """Missed-bump findings for the web tree committed at `ref` compared with
    `base_ref` (git's own view of the bytes, so EOL conversion cannot matter)."""
    if not _git_ok():
        return ["not a git work tree - cannot compare a committed web tree"]
    if base_ref == ref:
        return []
    cur = describe_modules_from_git(ref)
    if not cur.tokens:
        return [f"{ref} ships no tokenized web modules"]
    base = describe_modules_from_git(base_ref)
    problems: list[str] = []
    for name, tok in sorted(cur.tokens.items()):
        base_tok = base.tokens.get(name)
        if base_tok is None:
            continue
        if tok != base_tok:
            continue                # the commit moved the token - a real bump
        if _git_diff_quiet(base_ref, ref, f"web/{name}") is True:
            problems.append(
                f"web/{name} changed between {base_ref} and {ref} but its cache "
                f"token is still ?v={tok} (bump every module whose bytes "
                f"changed in the same commit)")
    return problems


def tree_problems(cur: WebTree, baseline: dict[str, dict]) -> list[str]:
    """Findings for one tree against one baseline (the testable core of scan).

    R2-02 (round-2 review): the "baseline is stale - run --update" hint is NOT
    emitted while a missed bump is present. `--update` itself refuses in that
    state, so pointing the reader at it would be the exact bypass the review
    reproduced: record the unbumped token and make the tree pass."""
    problems: list[str] = list(cur.problems)
    if not baseline:
        problems.append(
            "no verified baseline embedded in scripts/check_js_tokens.py - run "
            "`python scripts/check_js_tokens.py --update` and commit it")
        return problems
    tokens, hashes = _baseline_maps(baseline)
    missed = find_missed_bumps(tokens, hashes, cur, "the verified baseline")
    problems += missed
    gone = missing_from_tree(tokens, cur)
    if gone:
        problems.append(
            "the verified baseline records modules the tree no longer ships "
            "(run `python scripts/check_js_tokens.py --update` if that is "
            "intended): " + ", ".join("web/" + g for g in gone[:8])
            + (" ..." if len(gone) > 8 else ""))
    if not missed:
        stale = stale_manifest_entries(tokens, hashes, cur)
        if stale:
            problems.append(
                "the verified baseline is stale (run "
                "`python scripts/check_js_tokens.py --update` and commit it with "
                "the change): " + ", ".join("web/" + s for s in stale[:8])
                + (" ..." if len(stale) > 8 else ""))
    return problems


def scan() -> list[str]:
    """Findings for the working tree against the embedded verified baseline.

    R2-03 (round-2 review): the detection rule is the security-relevant part of
    this guard, so `scan()` verifies the detector once per process before
    trusting it. Both `scripts/check_js.py` and `scripts/pre_tag_check.py` go
    through here, so a regression in `find_missed_bumps` fails the normal
    checks instead of only the opt-in `--self-test`."""
    if not WEB.is_dir():
        return [f"{WEB} is missing"]
    if not detector_ok():
        return ["the missed-bump detector failed its built-in self-check - the "
                "guard cannot be trusted (run `python scripts/check_js_tokens.py "
                "--self-test`)"]
    return tree_problems(load_web_modules(WEB), load_baseline())


def _self_test(quiet: bool = False) -> int:
    """Pins for the behaviours the 0.6.24 repair reviews demanded.

    Runs on a throwaway tree (never the repo):
      1. the 0.6.25 shape - baseline bytes with token 10, tree with NEW bytes
         and the SAME token 10 -> must FAIL (a tag-based rule was blind to
         exactly this because the post-tag repair already moved 9 -> 10);
      2. a CRLF copy of the same content is NOT a finding (core.autocrlf=true),
         while a real edit on top of that CRLF copy still is;
      3. `--update` REFUSES while a missed bump is present and succeeds after a
         real token bump (review R2-02), and the stale-baseline hint is never
         emitted alongside a missed-bump finding.
    """
    import tempfile

    fails: list[str] = []

    def check(label, cond):
        if not quiet:
            print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)

    # Prefer a throwaway dir under the system temp; fall back to a gitignored
    # repo-local scratch for sandboxes that deny temp-dir creation.
    td = tempfile.mkdtemp(prefix="ns-token-selftest-")
    try:
        root = Path(td)
        (root / "probe").mkdir()
    except OSError:
        shutil.rmtree(td, ignore_errors=True)
        root = ROOT / ".pt-token-selftest"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
    try:
        base, cur = root / "base", root / "cur"

        def put(d: Path, app: str, astro: str, crlf: bool = False) -> None:
            (d / "web").mkdir(parents=True, exist_ok=True)
            texts = {
                "index.html": ('<link rel="stylesheet" href="style.css?v=16">\n'
                               '<script type="module" src="app.js?v=60">'
                               '</script>\n'),
                "app.js": app,
                "astro.js": astro,
                "style.css": ":root{color:#fff}\n",
            }
            for name, text in texts.items():
                data = text.encode("utf-8")
                if crlf:
                    data = data.replace(b"\n", b"\r\n")
                (d / "web" / name).write_bytes(data)

        app10 = "import * as E from './astro.js?v=10';\n"
        app9 = "import * as E from './astro.js?v=9';\n"
        put(base, app9, "export const a = 1;\n")
        put(cur, app10, "export const a = 2;\n")
        verified = load_web_modules(cur / "web")
        # 1. edit astro.js again, token still 10
        (cur / "web" / "astro.js").write_bytes(b"export const a = 3;\n")
        edited = load_web_modules(cur / "web")
        problems = find_missed_bumps(verified.tokens, verified.hashes(), edited,
                                     "the verified baseline")
        check("0.6.25 shape (new bytes, token still 10) FAILS",
              bool(problems) and "web/astro.js" in problems[0]
              and "still ?v=10" in problems[0])

        # 2. LF baseline vs CRLF copy with identical content
        lf, crlf = root / "lf", root / "crlf"
        put(lf, app10, "export const a = 2;\n")
        put(crlf, app10, "export const a = 2;\n", crlf=True)
        lf_tree = load_web_modules(lf / "web")
        crlf_tree = load_web_modules(crlf / "web")
        check("CRLF copy hashes like LF", lf_tree.hashes() == crlf_tree.hashes())
        check("CRLF copy is not a missed bump",
              find_missed_bumps(lf_tree.tokens, lf_tree.hashes(), crlf_tree,
                                "the verified baseline") == [])
        (crlf / "web" / "style.css").write_bytes(b":root{color:#000}\r\n")
        edited_crlf = load_web_modules(crlf / "web")
        check("a real edit on a CRLF copy still FAILS",
              any("web/style.css" in p for p in
                  find_missed_bumps(lf_tree.tokens, lf_tree.hashes(),
                                    edited_crlf, "the verified baseline")))

        # 3. the embedded baseline block round-trips
        check("baseline block round-trips",
              load_baseline(_baseline_block({"app.js": {"token": "1",
                                                        "sha256": "x"}}))
              == {"app.js": {"token": "1", "sha256": "x"}})

        # 4. R2-02: --update refuses to bless a missed bump, and the stale
        #    hint is never printed alongside the missed-bump finding.
        rows = baseline_rows(verified)
        src = SELF.read_text(encoding="utf-8")
        guard_copy = root / "guard_copy.py"
        guard_copy.write_text(
            src.split(_BASELINE_START + "\n", 1)[0]
            + _baseline_block(rows)
            + src.split("\n" + _BASELINE_END, 1)[1], encoding="utf-8")

        before = guard_copy.read_bytes()
        check("--update REFUSES while a missed bump is present",
              write_baseline(edited, guard_copy) is None
              and guard_copy.read_bytes() == before)
        hint_problems = tree_problems(edited, rows)
        check("missed bump reported", any("still ?v=10" in p
                                          for p in hint_problems))
        check("stale hint suppressed alongside a missed bump",
              not any("baseline is stale" in p for p in hint_problems))

        # A real release bump: the dependency token AND the entry point's own
        # token move in the same change (app.js's bytes change either way).
        (cur / "web" / "app.js").write_bytes(
            b"import * as E from './astro.js?v=11';\n")
        (cur / "web" / "index.html").write_bytes(
            b'<link rel="stylesheet" href="style.css?v=16">\n'
            b'<script type="module" src="app.js?v=61"></script>\n')
        bumped = load_web_modules(cur / "web")
        check("--update succeeds after the real token bump",
              write_baseline(bumped, guard_copy) == guard_copy)
        refreshed = load_baseline(guard_copy.read_text(encoding="utf-8"))
        check("refreshed baseline records both moved tokens",
              refreshed["astro.js"]["token"] == "11"
              and refreshed["astro.js"]["sha256"] == bumped.hashes()["astro.js"]
              and refreshed["app.js"]["token"] == "61")
        bumped_problems = tree_problems(bumped, rows)
        check("stale hint IS offered for a legitimate bump",
              any("baseline is stale" in p for p in bumped_problems)
              and not any("still ?v=" in p for p in bumped_problems))
        (cur / "web" / "app.js").write_bytes(
            b"import * as E from './astro.js?v=10';\n")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("check_js_tokens self-test:",
          "FAIL (%d)" % len(fails) if fails else "OK")
    return 1 if fails else 0


_DETECTOR_OK: bool | None = None


def detector_ok() -> bool:
    """The detector's own pins, evaluated once per process (review R2-03)."""
    global _DETECTOR_OK
    if _DETECTOR_OK is None:
        _DETECTOR_OK = _self_test(quiet=True) == 0
    return _DETECTOR_OK


def main(argv: list[str] | None = None) -> int:
    """argv is explicit on purpose (TR-06, 0.6.32 review).

    scripts/check_js.py embeds this module and calls `main()` with no arguments;
    falling back to `list(sys.argv[1:])` made the EMBEDDING process's flags leak
    in, so `python scripts/check_js.py --self-test` printed the guard's own
    self-test (and `check_js: OK`) instead of running the missed-bump scan - and
    `check_js.py --update` would have rewritten this tracked guard's baseline.
    `check_js_syntax.main()` deliberately does the same thing (pinned by
    tests/test_js_syntax_check.py); the `__main__` block below passes sys.argv
    explicitly, so the CLI still accepts the flags."""
    args = list(argv or [])
    if "--self-test" in args:
        return _self_test()
    if "--update" in args:
        cur = load_web_modules(WEB)
        if cur.problems:
            for p in cur.problems:
                print("  js-token: " + p)
            print("check_js_tokens: FAIL (cannot record a broken tree)")
            return 1
        refusals = update_refusals(cur)
        if refusals:
            for p in refusals:
                print("  js-token: " + p)
            print("check_js_tokens: REFUSED - bump the token(s) above in the "
                  "same change, then re-run --update")
            return 1
        p = write_baseline(cur)
        print(f"check_js_tokens: wrote the verified baseline into "
              f"{p.relative_to(ROOT)} ({len(cur.tokens)} modules) - commit it "
              f"with this change")
        return 0
    problems = scan()
    for p in problems:
        print("  js-token: " + p)
    print("check_js_tokens:", "FAIL (%d)" % len(problems) if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
