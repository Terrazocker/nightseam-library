"""Cache-token guard for the shipped data file web/catalog.json.

web/catalog.json is ~4 MB and NOT a JS module, so it is invalidated by the
named `CATALOG_TOKEN` in web/app.js (`fetch('catalog.json?v=' + CATALOG_TOKEN)`)
plus `web/catalog.manifest.json`. scripts/check_catalog_token.py is the enforced
check (also wired into scripts/check_js.py); this test runs it and proves by
mutation - on COPIES, never the real tree - that a catalog byte change without a
matching token bump fails, and a matched bump passes.
"""

import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _guard():
    spec = importlib.util.spec_from_file_location(
        "check_catalog_token", ROOT / "scripts" / "check_catalog_token.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_catalog_token_matches_bytes_and_single_fetch_site():
    guard = _guard()
    assert guard.scan() == [], "catalog cache-bust pairing is broken"
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert len(re.findall(r"const CATALOG_TOKEN = '(\d+)'", app)) == 1
    assert len(re.findall(r"fetch\('catalog\.json", app)) == 1, "exactly one catalog fetch site"
    man = json.loads((WEB / "catalog.manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256((WEB / "catalog.json").read_bytes()).hexdigest() == man["sha256"]


def test_committed_catalog_change_requires_a_token_bump(monkeypatch):
    """TO-04 (TL3): the branch that compares the tree against HEAD is the ONLY
    check for "manifest bumped, CATALOG_TOKEN not" - and nothing proved it runs
    (the existing mutation copies the files but leaves ROOT real, so git sees a
    clean repository). Simulate the committed shape instead."""
    import types

    guard = _guard()

    def fake_git(args):
        if args[:2] == ["diff", "--quiet"]:
            return types.SimpleNamespace(returncode=1, stdout="")   # differs
        if args[0] == "diff":
            return types.SimpleNamespace(returncode=0, stdout="- const OTHER = 1;\n")
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(guard, "_git", fake_git)
    monkeypatch.setattr(guard, "_git_ok", lambda: True)

    problems = guard.scan()
    assert any("CATALOG_TOKEN" in p for p in problems), problems

    # the same catalog change WITH the token bumped in app.js is clean
    def fake_git_bumped(args):
        if args[:2] == ["diff", "--quiet"]:
            return types.SimpleNamespace(returncode=1, stdout="")
        if args[0] == "diff":
            return types.SimpleNamespace(
                returncode=0, stdout="- const CATALOG_TOKEN = '2';\n+ const CATALOG_TOKEN = '3';\n")
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(guard, "_git", fake_git_bumped)
    assert not [p for p in guard.scan() if "CATALOG_TOKEN" in p]


def test_an_unusable_git_is_reported_not_silent(monkeypatch, capsys):
    """TO-04 (TL3): without git the committed-change rule cannot run at all - it
    used to vanish without a word, so the guard printed OK for a check that never
    executed."""
    guard = _guard()
    monkeypatch.setattr(guard, "_git_ok", lambda: False)
    guard.main()
    out = capsys.readouterr().out
    assert "did NOT run" in out, out


def test_web_token_register_matches_the_sources():
    """FIX-7 (review TL2/F2): the AGENTS.md 'Current:' register is the only
    place the per-module cache tokens are tracked, and it had gone stale
    (app.js 55 vs 56, horizon 37 vs 38, format 14 vs 15, core 14 vs 15).
    Pin doc == source for every listed token."""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    register = {full.rsplit("/", 1)[-1]: tok for full, _ext, tok in
                re.findall(r"`([\w./]+)\.(js|css)\?v=(\d+)`", agents)}
    assert register, "AGENTS.md no longer carries the token register"
    index = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert register["app"] == re.search(r"app\.js\?v=(\d+)", index).group(1)
    for name in ("style", "themes"):
        assert register[name] == \
            re.search(name + r"\.css\?v=(\d+)", index).group(1)
    for name, tok in re.findall(r"\./([\w.]+)\.js\?v=(\d+)", app):
        if name == "core":
            continue                     # enforced against the manifest below
        assert register.get(name) == tok, (name, tok, register.get(name))
    man = json.loads((ROOT / "core" / "core.manifest.json").read_text(
        encoding="utf-8"))
    assert register["core"] == str(man["token"])


def test_catalog_change_without_token_bump_fails_and_matched_bump_passes(
        tmp_path, monkeypatch):
    """Mutation proof (owner-requested), performed on a COPY of the three files
    so the real repository is never modified.

    (a) changing web/catalog.json without bumping CATALOG_TOKEN/manifest FAILS;
    (b) bumping both together PASSES."""
    for name in ("app.js", "catalog.json", "catalog.manifest.json"):
        shutil.copy(WEB / name, tmp_path / name)
    guard = _guard()
    monkeypatch.setattr(guard, "APP", tmp_path / "app.js")
    monkeypatch.setattr(guard, "CATALOG", tmp_path / "catalog.json")
    monkeypatch.setattr(guard, "MANIFEST", tmp_path / "catalog.manifest.json")

    cat0 = (tmp_path / "catalog.json").read_bytes()
    app0 = (tmp_path / "app.js").read_text(encoding="utf-8")
    sha0 = hashlib.sha256(cat0).hexdigest()
    token0 = re.search(r"const CATALOG_TOKEN = '(\d+)'", app0).group(1)
    assert guard.scan() == [], "the copied baseline must pass"

    # (a) catalog bytes change, nothing else -> manifest no longer matches
    (tmp_path / "catalog.json").write_bytes(cat0 + b" ")
    assert guard.scan() != [], "a bare catalog change must FAIL the guard"

    # (b) bump the token AND the manifest sha together -> the PAIRING is new, so
    # the embedded verified baseline is stale and must be refreshed in the same
    # change (TR-01, 0.6.32 review made the rule git-state independent, exactly
    # like check_js_tokens.py); it must NOT be reported as a missed bump.
    new_token = str(int(token0) + 1)
    (tmp_path / "app.js").write_text(
        app0.replace(f"const CATALOG_TOKEN = '{token0}'",
                     f"const CATALOG_TOKEN = '{new_token}'"), encoding="utf-8")
    new_sha = hashlib.sha256((tmp_path / "catalog.json").read_bytes()).hexdigest()
    (tmp_path / "catalog.manifest.json").write_text(
        json.dumps({"token": new_token, "sha256": new_sha}), encoding="utf-8")
    problems = guard.scan()
    assert any("baseline is stale" in p for p in problems), problems
    assert not any("still" in p and "CATALOG_TOKEN" in p for p in problems), \
        f"a matched bump must not be called a missed bump: {problems}"

    # ... and `--update` refreshes it -> PASS
    monkeypatch.setattr(guard, "load_baseline",
                        lambda src=None: {"token": new_token,
                                          "sha256": new_sha})
    assert guard.scan() == [], "a matched bump with a refreshed baseline must PASS"

    # the real repository was never touched
    assert (WEB / "catalog.json").read_bytes() == cat0
    assert hashlib.sha256((WEB / "catalog.json").read_bytes()).hexdigest() == sha0
    assert (WEB / "app.js").read_text(encoding="utf-8") == app0


def test_committed_catalog_change_without_a_bump_fails(monkeypatch, tmp_path):
    """TR-01 (0.6.32 review): the git rule only fires while the change is
    UNCOMMITTED (`git diff HEAD` is empty once it is committed) and the
    manifest/token pair is self-consistent as soon as the manifest sha is
    refreshed - which is exactly what the documented workflow asks for. A
    COMMITTED catalog change with an unbumped CATALOG_TOKEN therefore passed, and
    neither `pre_tag_check.py` nor `check_release.py` ran this guard at all. The
    embedded baseline makes the rule independent of the git state."""
    import types

    for name in ("app.js", "catalog.json", "catalog.manifest.json"):
        shutil.copy(WEB / name, tmp_path / name)
    guard = _guard()
    monkeypatch.setattr(guard, "APP", tmp_path / "app.js")
    monkeypatch.setattr(guard, "CATALOG", tmp_path / "catalog.json")
    monkeypatch.setattr(guard, "MANIFEST", tmp_path / "catalog.manifest.json")
    # the COMMITTED shape: git reports a clean tree (nothing to diff against)
    monkeypatch.setattr(guard, "_git_ok", lambda: True)
    monkeypatch.setattr(
        guard, "_git",
        lambda args: types.SimpleNamespace(returncode=0, stdout=""))
    assert guard.scan() == [], "the copied tree must start clean"

    # catalog bytes change and the manifest sha is refreshed (as documented),
    # but CATALOG_TOKEN is NOT bumped -> must FAIL on a clean tree
    cat = tmp_path / "catalog.json"
    cat.write_bytes(cat.read_bytes() + b" ")
    app = (tmp_path / "app.js").read_text(encoding="utf-8")
    token0 = re.search(r"const CATALOG_TOKEN = '(\d+)'", app).group(1)
    (tmp_path / "catalog.manifest.json").write_text(json.dumps(
        {"token": token0,
         "sha256": hashlib.sha256(cat.read_bytes()).hexdigest()}), encoding="utf-8")
    problems = guard.scan()
    assert any("CATALOG_TOKEN" in p and "still" in p for p in problems), problems
