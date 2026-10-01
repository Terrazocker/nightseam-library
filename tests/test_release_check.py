"""Release integrity: scripts/check_release.py + the curation gate in
scripts/build_changelog.py.

Pure-function tests only — no network, no build, no PyInstaller, no
developer data/, and no tmp_path (the sandbox's temp-dir ACL makes tmp_path
tests fail outside a full-access shell). One read-only smoke test collects
the real repo, which touches nothing but tracked files.

The scenario these tests exist for: the shipped 0.6.0 exe was baked before
the v0.6.0 tag existed, and make_release.py happily published it under a
fresh version. `evaluate()` must report that as an ERROR.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules[cls.__module__]
    sys.modules[name] = module
    spec.loader.exec_module(module)          # both files guard __main__
    return module


CR = _load("ns_check_release", ROOT / "scripts" / "check_release.py")
BC = _load("ns_build_changelog", ROOT / "scripts" / "build_changelog.py")

RELEASE = "0.6.9"
SHA = "a" * 64          # the zip's real sha256 stands in for a small fixture
EXE_SHA = "e" * 64      # ditto for NightSeam.exe


# --------------------------------------------------------------------------
# fixtures (plain dicts — no filesystem)
# --------------------------------------------------------------------------
def stage_state(version=RELEASE, **overrides):
    """A consistent dist/release snapshot.

    `zips`/`sha256` are the REAL staged files, `version_json` is the metadata
    scripts/make_release.py recorded for the zip its `url` names — the two must
    agree (that is the point of the sha256/size check).
    """
    zip_name = f"NightSeam-{version}.zip"
    url = f"/downloads/{zip_name}"
    entry = {"members": ["LICENSE", "NOTICE", "NightSeam.exe"],
             "exe_size": 8, "error": None}
    state = {
        "exists": True,
        "files": ["LICENSE", "NOTICE", "NightSeam.exe", "NightSeam-latest.zip",
                  zip_name, "ns-version.js", "version.json"],
        "version_json": {"version": version, "url": url, "sha256": SHA, "size": 10},
        "version_json_error": None,
        "ns_version": {"version": version, "url": url},
        "zips": {zip_name: 10, "NightSeam-latest.zip": 10},
        "sha256": {zip_name: SHA, "NightSeam-latest.zip": SHA},
        "zip_entries": {zip_name: dict(entry),
                        "NightSeam-latest.zip": dict(entry)},
        "exe_size": 8,
        "exe_sha256": EXE_SHA,
    }
    state.update(overrides)
    return state


def state(**overrides):
    """A fully consistent state: every source agrees on 0.6.9."""
    base = dict(
        describe=f"v{RELEASE}",
        baked=RELEASE,
        baked_present=True,
        baked_mtime=100.0,
        exe_mtime=200.0,
        exe_sha256=EXE_SHA,
        web={"version": RELEASE, "describe": f"v{RELEASE}", "built": "2026-09-11T00:00:00+00:00"},
        web_present=True,
        web_problem=None,
        changelog=[
            {"version": RELEASE, "date": "2026-09-11", "summary": "curated text"},
            {"version": "0.6.0", "date": "2026-09-10", "summary": "older text"},
        ],
        changelog_present=True,
        changelog_problem=None,
        stage=stage_state(),
        curated={RELEASE: "curated text", "0.6.0": "older text"},
    )
    base.update(overrides)
    return CR.State(**base)


def errors(findings):
    return [f for f in findings if f.level == CR.ERROR]


def checks(findings, level=CR.ERROR):
    return sorted(f.check for f in findings if f.level == level)


def message_for(findings, check, level=CR.ERROR):
    hits = [f.message for f in findings if f.check == check and f.level == level]
    return " | ".join(hits)


# --------------------------------------------------------------------------
# version parsing
# --------------------------------------------------------------------------
def test_normalize_and_base_version():
    assert CR.normalize_version("v0.6.9") == "0.6.9"
    assert CR.normalize_version("  v0.6.9-dirty ") == "0.6.9-dirty"
    assert CR.base_version("0.6.9-1-ga777d88-dirty") == "0.6.9"
    assert CR.base_version("v0.6.9") == "0.6.9"
    assert CR.base_version("") == ""


def test_version_key_and_compare():
    assert CR.version_key("0.6.9") == (0, 6, 9)
    assert CR.version_key("v0.6.10") > CR.version_key("0.6.9")
    assert CR.compare_versions("0.6.9", "0.6.9-1-gabc1234") == 0   # same release line
    assert CR.compare_versions("0.6.10", "0.6.9") == 1
    assert CR.compare_versions("0.5.12", "0.6.0") == -1


def test_is_exact_tag():
    assert CR.is_exact_tag("v0.6.9")
    assert not CR.is_exact_tag("v0.6.9-dirty")
    assert not CR.is_exact_tag("v0.6.8-1-ga777d88")
    assert not CR.is_exact_tag("")


def test_parse_baked_version():
    assert CR.parse_baked_version("BAKED_VERSION = '0.6.0'\n") == "0.6.0"
    assert CR.parse_baked_version('BAKED_VERSION = "0.6.0"') == "0.6.0"
    assert CR.parse_baked_version("# nothing baked\n") is None
    assert CR.parse_baked_version(None) is None
    assert CR.parse_baked_version("BAKED_VERSION = ''") == ""


def test_parse_ns_version():
    text = 'window.__NS_RELEASE={"version": "0.6.0", "url": "/downloads/NightSeam-0.6.0.zip"};\n'
    payload = CR.parse_ns_version(text)
    assert payload["version"] == "0.6.0"
    assert payload["url"].endswith("NightSeam-0.6.0.zip")
    assert CR.parse_ns_version("var x = 1;") is None
    assert CR.parse_ns_version("window.__NS_RELEASE={oops};") is None


def test_parse_changelog():
    ok, problem = CR.parse_changelog('[{"version":"0.6.0","date":"2026-09-10","summary":"x"}]')
    assert problem is None and ok[0]["version"] == "0.6.0"

    for bad in ('not json', '{"version": "0.6.0"}', '[{"version":"0.6.0","date":"","summary":"x"}]'):
        entries, problem = CR.parse_changelog(bad)
        assert entries is None and problem
    assert CR.parse_changelog(None) == (None, "file is missing")


# --------------------------------------------------------------------------
# the changelog schema: entries name release tags
# --------------------------------------------------------------------------
def test_every_entry_in_the_real_changelog_matches_the_schema():
    """The historical format must stay accepted (0.4.2 ... 0.6.0 today)."""
    text = (ROOT / "landing" / "changelog.json").read_text(encoding="utf-8")
    entries, problem = CR.parse_changelog(text)
    assert problem is None, problem
    assert entries, "the shipped changelog has entries"
    for entry in entries:
        assert CR._RELEASE_VERSION_RE.match(entry["version"]), entry["version"]


def test_valid_changelog_entry_is_accepted():
    entries, problem = CR.parse_changelog(
        '[{"version": "0.5.12", "date": "2026-09-10", "summary": "x"}]')
    assert problem is None and entries[0]["version"] == "0.5.12"
    assert errors(CR.evaluate(state(changelog=entries))) == []
    assert errors(CR.evaluate(state(
        changelog=[{"version": RELEASE, "date": "2026-09-11", "summary": "x"}]))) == []


def test_malformed_changelog_version_is_a_hard_error():
    """`version_key("garbage")` is (0,): it used to sort below the release and
    only WARN, so corruption could hide behind a repairable-looking warning."""
    assert CR.version_key("garbage") == (0,)
    for bad in ("garbage", "v0.6.9", "0.6.9-dirty", "0.6.9-1-ga777d88",
                "NightSeam 0.6.9", "0.6.9.1", "0.6", "1.2.x"):
        text = f'[{{"version": "{bad}", "date": "2026-09-11", "summary": "x"}}]'
        entries, problem = CR.parse_changelog(text)
        assert entries is None, bad
        assert "not a release tag" in problem, (bad, problem)
        findings = CR.evaluate(state(changelog=None, changelog_problem=problem))
        assert "changelog" in checks(findings)
        assert CR.exit_code(findings) == 1


def test_missing_changelog_version_is_a_hard_error():
    for entry in ('{"date": "2026-09-11", "summary": "x"}',
                  '{"version": "", "date": "2026-09-11", "summary": "x"}',
                  '{"version": 69, "date": "2026-09-11", "summary": "x"}'):
        entries, problem = CR.parse_changelog(f"[{entry}]")
        assert entries is None and "version" in problem, entry
        findings = CR.evaluate(state(changelog=None, changelog_problem=problem))
        assert CR.exit_code(findings) == 1


def test_incompatible_changelog_version_is_a_hard_error():
    """A well-formed version from another release line cannot be sorted against
    the release: `2.0.0` is ahead (never released), `0.6` is not a tag shape."""
    for bad in ("0.6", "0.6.9.1"):
        entries, problem = CR.parse_changelog(
            f'[{{"version": "{bad}", "date": "2026-09-11", "summary": "x"}}]')
        assert entries is None and problem
    ahead = CR.evaluate(state(changelog=[
        {"version": "2.0.0", "date": "2026-09-11", "summary": "future"}]))
    assert "never released" in message_for(ahead, "changelog")


def test_changelog_ahead_of_the_release_fails_even_when_curated():
    """Curating a version does not make it released: the entry still names a
    release the artifacts do not have."""
    findings = CR.evaluate(state(
        changelog=[{"version": "0.7.0", "date": "2026-09-11", "summary": "x"}],
        curated={RELEASE: "text", "0.7.0": "text"}))
    assert "never released" in message_for(findings, "changelog")
    assert CR.exit_code(findings) == 1


def test_all_entries_behind_the_release_is_only_a_warning():
    findings = CR.evaluate(state(changelog=[
        {"version": "0.5.12", "date": "2026-09-10", "summary": "old"}]))
    assert errors(findings) == []
    assert "changelog" in checks(findings, CR.WARNING)


def test_empty_changelog_is_an_error():
    findings = CR.evaluate(state(changelog=[]))
    assert "changelog" in checks(findings)
    assert "no entries" in message_for(findings, "changelog")
    assert CR.exit_code(findings) == 1


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------
def test_consistent_state_is_clean():
    findings = CR.evaluate(state())
    assert findings == []
    assert CR.exit_code(findings) == 0
    assert "OK" in CR.summarize(findings)


def test_stage_can_be_excluded():
    findings = CR.evaluate(state(stage={}), include_stage=False)
    assert findings == []


# --------------------------------------------------------------------------
# the authoritative version + tree state
# --------------------------------------------------------------------------
def test_unusable_describe_is_the_only_error():
    findings = CR.evaluate(state(describe=""))
    assert checks(findings) == ["describe"]
    assert CR.exit_code(findings) == 1


def test_dirty_tree_is_an_error_unless_allowed():
    # a snapshot build labels EVERYTHING with the dirty describe, so the staged
    # artifacts carry the same string
    dirty = "0.6.9-dirty"
    findings = CR.evaluate(state(describe=f"v{dirty}", baked=dirty,
                                 stage=stage_state(dirty)))
    assert "tree" in checks(findings)

    allowed = CR.evaluate(state(describe=f"v{dirty}", baked=dirty,
                                stage=stage_state(dirty)),
                          allow_dirty=True)
    assert errors(allowed) == []
    assert "tree" in checks(allowed, CR.WARNING)
    # web/version.json and the changelog compare on the base version, so a
    # '-dirty' describe does not turn them into mismatches
    assert "web" not in checks(allowed, CR.ERROR)


def test_off_tag_head_is_an_error():
    off = "0.6.8-1-ga777d88"
    findings = CR.evaluate(state(
        describe=f"v{off}", baked=off,
        web={"version": "0.6.8", "describe": "v0.6.8"},
        changelog=[{"version": "0.6.8", "date": "2026-09-10", "summary": "text"}],
        curated={"0.6.8": "text"},
        stage=stage_state(off)))
    assert checks(findings) == ["tree"]      # the tree state is the only problem
    assert CR.exit_code(findings) == 1


# --------------------------------------------------------------------------
# the describe grammar (a fabricated describe is never a release)
# --------------------------------------------------------------------------
def test_describe_grammar_accepts_every_real_git_output():
    for good in ("v0.6.9", "0.6.9", "v0.6.9-dirty", "0.6.9-dirty",
                 "v0.6.8-1-ga777d88", "v0.6.8-1-ga777d88-dirty", "v1"):
        assert CR.describe_problem(good) is None, good
    for exact in ("v0.6.9", "0.6.9", "v1"):
        assert CR.is_exact_tag(exact), exact
    for off in ("v0.6.9-dirty", "v0.6.8-1-ga777d88", "v0.6.8-1-ga777d88-dirty"):
        assert not CR.is_exact_tag(off), off


def test_malformed_or_fabricated_describe_always_fails():
    for bad in ("", "   ", "0.6.9-dirty-dirty", "0.6.9-dirty-DIRTY",
                "v0.6.8-1-ga777d88-dirty-dirty", "v0.6.8-1-ga777d88-dirt",
                "garbage", "vv0.6.9", "v0.6.9-99-gZZZZZZ", "v0.6.9-",
                "-dirty", "v0.0.0"):
        assert CR.describe_problem(bad), bad
        # both with and without --allow-dirty: a fabricated string can never be
        # forgiven, and it is the ONLY finding (nothing else is evaluated)
        for allow in (False, True):
            findings = CR.evaluate(state(describe=bad), allow_dirty=allow)
            assert checks(findings) == ["describe"], (bad, allow)
            assert CR.exit_code(findings) == 1


def test_sha_only_describe_from_a_tagless_clone_is_rejected():
    """`git describe --always` falls back to a bare commit sha: not a version."""
    for sha_only in ("a777d88", "a777d88-dirty"):
        assert CR.describe_problem(sha_only)
        findings = CR.evaluate(state(describe=sha_only), allow_dirty=True)
        assert checks(findings) == ["describe"]


def test_clean_tree_describe_is_accepted():
    assert CR.is_exact_tag(f"v{RELEASE}")
    assert CR.evaluate(state()) == []


def test_legitimate_dirty_tree_is_accepted_only_with_allow_dirty():
    dirty = f"{RELEASE}-dirty"
    findings = CR.evaluate(state(describe=f"v{dirty}", baked=dirty,
                                 stage=stage_state(dirty)))
    assert "tree" in checks(findings)          # ERROR without the flag
    allowed = CR.evaluate(state(describe=f"v{dirty}", baked=dirty,
                                stage=stage_state(dirty)), allow_dirty=True)
    assert errors(allowed) == []
    assert "tree" in checks(allowed, CR.WARNING)
    assert CR.is_dirty_tag(f"v{dirty}")


def test_allow_dirty_does_not_bypass_the_other_checks():
    dirty = f"{RELEASE}-dirty"
    common = dict(describe=f"v{dirty}", baked=dirty,
                  stage=stage_state(dirty))

    # 1. a wrong tag/version is still a contradiction
    wrong = CR.evaluate(state(**{**common, "baked": "0.6.0"}), allow_dirty=True)
    assert "baked" in checks(wrong)

    # 2. the curated-changelog gate still applies to a dirty tag
    uncurated = CR.evaluate(state(**common, curated={"0.6.0": "old"}),
                            allow_dirty=True)
    assert "tree" in checks(uncurated, CR.WARNING)
    assert "no curated summary" in message_for(uncurated, "changelog")
    assert CR.exit_code(uncurated) == 1

    # 3. staged metadata is still verified
    bad_stage = stage_state(dirty)
    bad_stage["version_json"] = dict(bad_stage["version_json"], sha256="b" * 64)
    staged = CR.evaluate(state(**{**common, "stage": bad_stage}),
                         allow_dirty=True)
    assert "sha256" in message_for(staged, "stage")

    # 4. a stage left over from another release is still caught
    stale = CR.evaluate(state(**{**common, "stage": stage_state("0.6.0")}),
                        allow_dirty=True)
    assert "stale" in message_for(stale, "stage")


# --------------------------------------------------------------------------
# the stale exe (the shipped 0.6.0 hazard)
# --------------------------------------------------------------------------
def test_stale_bake_is_an_error():
    """0.6.0 exe, v0.6.9 tag: make_release must refuse to publish it."""
    findings = CR.evaluate(state(baked="0.6.0"))
    assert "baked" in checks(findings)
    assert "0.6.0" in message_for(findings, "baked")
    assert RELEASE in message_for(findings, "baked")
    assert CR.exit_code(findings) == 1


def test_missing_bake_is_an_error():
    findings = CR.evaluate(state(baked=None, baked_present=False, baked_mtime=None))
    assert "baked" in checks(findings)
    assert "build.ps1" in message_for(findings, "baked")


def test_bake_newer_than_exe_is_an_error():
    findings = CR.evaluate(state(baked_mtime=300.0, exe_mtime=200.0))
    assert "baked" in checks(findings)
    assert "newer than" in message_for(findings, "baked")


def test_missing_exe_is_an_error():
    findings = CR.evaluate(state(exe_mtime=None))
    assert "exe" in checks(findings)


# --------------------------------------------------------------------------
# web/version.json
# --------------------------------------------------------------------------
def test_web_version_ahead_is_an_error():
    findings = CR.evaluate(state(web={"version": "0.7.0", "describe": "v0.7.0"}))
    assert "web" in checks(findings)
    assert CR.exit_code(findings) == 1


def test_web_version_behind_is_only_a_warning():
    findings = CR.evaluate(state(web={"version": "0.6.8", "describe": "v0.6.8"}))
    assert errors(findings) == []
    assert "web" in checks(findings, CR.WARNING)
    # TO-07 (TL5): the classification is DELIBERATE, and the warning must name the
    # repair (re-bake web/version.json) so an operator knows what to do before the
    # next deploy - the record was corrected to say exactly that.
    msg = " ".join(str(getattr(f, "message", getattr(f, "msg", ""))) for f in findings)
    assert "build_web.py version" in msg, msg


def test_the_build_writes_its_spec_to_ignored_dist():
    """Without `--specpath`, PyInstaller writes NightSeam.spec into the repo root
    and dirties a successful release build. The build writes generated specs into
    gitignored `dist/`, no spec is tracked, and the toolchain is pinned."""
    import re

    ps1 = (ROOT / "build.ps1").read_text(encoding="utf-8")
    assert "--specpath" in ps1, "build.ps1 must keep generated specs out of the repo root"
    # with --specpath the spec sits in dist/, and PyInstaller resolves --add-data
    # sources relative to the SPEC DIR - a relative "static" became dist\static and
    # the 0.6.25 build died. Every source path must be absolute.
    for flag in ("--add-data", "--icon"):
        hits = [l for l in ps1.splitlines() if l.strip().startswith(flag)]
        assert hits, f"{flag} not found in build.ps1"
        assert all("$PSScriptRoot" in l for l in hits), (
            f"{flag} must use an absolute ($PSScriptRoot) source path: {hits}")
    req = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert re.search(r"^pyinstaller==", req, re.M), (
        "PyInstaller must be pinned exactly, not '>=6.0'")


def test_web_version_internally_inconsistent_is_an_error():
    findings = CR.evaluate(state(web={"version": "0.6.9", "describe": "v0.6.8"}))
    assert "web" in checks(findings)


def test_missing_web_version_is_only_a_warning():
    findings = CR.evaluate(state(web=None, web_present=False))
    assert errors(findings) == []
    assert "web" in checks(findings, CR.WARNING)


def test_unparsable_web_version_is_an_error_not_a_warning():
    """A present-but-broken stamp is a broken bake, not 'the web was never
    stamped' — the missing-file branch must not swallow it."""
    findings = CR.evaluate(state(web=None, web_present=True,
                                 web_problem="file could not be read"))
    assert "web" in checks(findings)
    assert "could not be read" in message_for(findings, "web")
    assert CR.exit_code(findings) == 1


def test_web_version_that_is_not_a_version_is_an_error():
    """`garbage` used to sort as (0,) → 'behind the release' → a WARNING."""
    findings = CR.evaluate(state(web={"version": "garbage", "describe": "garbage"}))
    assert "web" in checks(findings)
    assert "not a version string" in message_for(findings, "web")
    assert CR.exit_code(findings) == 1
    # a real describe-shaped bake (build_web.py copies `git describe`) is fine
    assert not CR._DESCRIBE_RE.match("")
    for good in ("0.6.9", "0.6.9-dirty", "0.6.8-1-ga777d88-dirty"):
        assert CR._DESCRIBE_RE.match(good), good


# --------------------------------------------------------------------------
# landing/changelog.json
# --------------------------------------------------------------------------
def test_changelog_newest_ahead_of_release_is_an_error():
    findings = CR.evaluate(state(changelog=[
        {"version": "0.7.0", "date": "2026-09-11", "summary": "unreleased"}]))
    assert "changelog" in checks(findings)
    assert "never released" in message_for(findings, "changelog")


def test_changelog_behind_is_only_a_warning():
    findings = CR.evaluate(state(changelog=[
        {"version": "0.6.0", "date": "2026-09-10", "summary": "old"}]))
    assert errors(findings) == []
    assert "changelog" in checks(findings, CR.WARNING)


def test_malformed_changelog_is_an_error():
    findings = CR.evaluate(state(changelog=None, changelog_problem="entry 0 has no usable 'summary'"))
    assert "changelog" in checks(findings)


def test_missing_curated_entry_for_the_released_tag_is_an_error():
    findings = CR.evaluate(state(curated={"0.6.0": "old"}))
    assert "changelog" in checks(findings)
    assert "no curated summary" in message_for(findings, "changelog")


def test_curation_gate_only_applies_to_an_exact_tag():
    off = "0.6.8-1-ga777d88"
    findings = CR.evaluate(state(
        describe=f"v{off}", baked=off,
        web={"version": "0.6.8", "describe": "v0.6.8"},
        changelog=[{"version": "0.6.8", "date": "2026-09-10", "summary": "text"}],
        curated={"0.6.0": "old"},           # 0.6.8 has no curated summary
        stage=stage_state(off)))
    assert "no curated summary" not in message_for(findings, "changelog")


def test_unsorted_changelog_warns():
    findings = CR.evaluate(state(changelog=[
        {"version": "0.6.0", "date": "2026-09-10", "summary": "old"},
        {"version": RELEASE, "date": "2026-09-11", "summary": "new"}]))
    assert "changelog" in checks(findings, CR.WARNING)
    assert errors(findings) == []


# --------------------------------------------------------------------------
# dist/release artifacts
# --------------------------------------------------------------------------
def test_stale_stage_is_one_precise_error():
    findings = CR.evaluate(state(stage=stage_state(version="0.6.0")))
    assert checks(findings) == ["stage"]
    assert "0.6.0" in message_for(findings, "stage")
    assert RELEASE in message_for(findings, "stage")


def test_zip_name_mismatch_is_an_error():
    """version.json is right, the zip in the stage is named after another one."""
    bad = stage_state()
    bad["zips"] = {"NightSeam-0.6.8.zip": 10, "NightSeam-latest.zip": 10}
    bad["sha256"] = {"NightSeam-0.6.8.zip": "aa", "NightSeam-latest.zip": "aa"}
    findings = CR.evaluate(state(stage=bad))
    assert checks(findings) == ["stage"]
    assert "NightSeam-0.6.8.zip" in message_for(findings, "stage")


def test_version_json_url_mismatch_is_an_error():
    bad = stage_state()
    bad["version_json"] = dict(bad["version_json"], url="/downloads/NightSeam-0.6.0.zip")
    findings = CR.evaluate(state(stage=bad))
    assert "url" in message_for(findings, "stage")


def test_ns_version_advertising_another_version_is_an_error():
    bad = stage_state()
    bad["ns_version"] = {"version": "0.6.0", "url": "/downloads/NightSeam-0.6.0.zip"}
    findings = CR.evaluate(state(stage=bad))
    assert "ns-version.js" in message_for(findings, "stage")


def test_latest_zip_must_match_the_versioned_zip():
    bad = stage_state()
    bad["sha256"] = dict(bad["sha256"], **{"NightSeam-latest.zip": "bb"})
    findings = CR.evaluate(state(stage=bad))
    assert "NightSeam-latest.zip" in message_for(findings, "stage")


def test_missing_staged_exe_is_an_error():
    bad = stage_state()
    bad["files"] = [f for f in bad["files"] if f != "NightSeam.exe"]
    findings = CR.evaluate(state(stage=bad))
    assert "NightSeam.exe" in message_for(findings, "stage")


def test_empty_stage_is_only_a_warning():
    findings = CR.evaluate(state(stage={"exists": False, "files": []}))
    assert errors(findings) == []
    assert "stage" in checks(findings, CR.WARNING)


# --------------------------------------------------------------------------
# version.json's recorded sha256/size vs the staged zip (fix: a wrong hash and
# a wrong size used to pass, so /downloads published checksum metadata that
# matched nothing and a download size that was simply false)
# --------------------------------------------------------------------------
def test_staged_version_json_sha256_must_match_the_zip():
    bad = stage_state()
    bad["version_json"] = dict(bad["version_json"], sha256="b" * 64)
    findings = CR.evaluate(state(stage=bad))
    assert "sha256" in message_for(findings, "stage")
    assert CR.exit_code(findings) == 1


def test_staged_version_json_size_must_match_the_zip():
    bad = stage_state()
    bad["version_json"] = dict(bad["version_json"], size=20414868)
    findings = CR.evaluate(state(stage=bad))
    assert "bytes" in message_for(findings, "stage")


def test_staged_metadata_check_reads_the_file_not_just_the_metadata():
    """Control: changing the FILE (not the recorded hash) fails too, so the
    check is a real comparison and not a constant."""
    bad = stage_state()
    bad["sha256"] = {name: "c" * 64 for name in bad["sha256"]}
    findings = CR.evaluate(state(stage=bad))
    assert "sha256" in message_for(findings, "stage")


def test_staged_version_json_without_hash_or_size_is_an_error():
    bad = stage_state()
    bad["version_json"] = {"version": RELEASE,
                           "url": f"/downloads/NightSeam-{RELEASE}.zip"}
    findings = CR.evaluate(state(stage=bad))
    message = message_for(findings, "stage")
    assert "sha256" in message and "size" in message


def test_staged_version_json_metadata_must_be_well_formed():
    for override in ({"sha256": "AA" * 32},          # uppercase/not hex-lower
                     {"sha256": "a" * 63},           # truncated
                     {"sha256": None},
                     {"size": 0},
                     {"size": -1},
                     {"size": "20414868"},
                     {"size": True}):
        bad = stage_state()
        bad["version_json"] = dict(bad["version_json"], **override)
        findings = CR.evaluate(state(stage=bad))
        assert "version.json records" in message_for(findings, "stage"), override


def test_staged_metadata_is_not_checked_for_a_stale_stage():
    """A stale stage already reports one precise error (re-run make_release)."""
    findings = CR.evaluate(state(stage=stage_state(version="0.6.0")))
    assert checks(findings) == ["stage"]
    assert len(errors(findings)) == 1


# --------------------------------------------------------------------------
# the zip itself, and the exe inside it
# --------------------------------------------------------------------------
def test_staged_zip_without_the_exe_is_an_error():
    bad = stage_state()
    bad["zip_entries"] = {f"NightSeam-{RELEASE}.zip":
                          {"members": ["LICENSE", "NOTICE"], "exe_size": None,
                           "error": None}}
    findings = CR.evaluate(state(stage=bad))
    assert "no NightSeam.exe" in message_for(findings, "stage")


def test_unreadable_staged_zip_is_an_error():
    bad = stage_state()
    bad["zip_entries"] = {f"NightSeam-{RELEASE}.zip":
                          {"members": None, "exe_size": None,
                           "error": "File is not a zip file"}}
    findings = CR.evaluate(state(stage=bad))
    assert "not a readable zip" in message_for(findings, "stage")


def test_zip_exe_of_a_different_size_than_the_staged_exe_is_an_error():
    bad = stage_state()
    bad["zip_entries"] = {f"NightSeam-{RELEASE}.zip":
                          {"members": ["NightSeam.exe"], "exe_size": 7,
                           "error": None}}
    findings = CR.evaluate(state(stage=bad))
    assert "different exe" in message_for(findings, "stage")


def test_stale_staged_exe_is_an_error():
    """A rebuild after staging leaves the old exe in the package; its own
    hash metadata stays self-consistent, so only this comparison catches it."""
    findings = CR.evaluate(state(stage=stage_state(exe_sha256="f" * 64)))
    assert "older" in message_for(findings, "stage")
    assert CR.exit_code(findings) == 1


def test_staged_exe_identity_is_skipped_when_dist_has_no_exe():
    """The missing dist/{exe} case is reported by the 'exe' check, not twice."""
    findings = CR.evaluate(state(exe_mtime=None, exe_sha256=None))
    assert "exe" in checks(findings)
    assert "older" not in message_for(findings, "stage")


def test_ns_version_url_must_name_the_release_zip():
    bad = stage_state()
    bad["ns_version"] = {"version": RELEASE,
                         "url": "/downloads/NightSeam-0.6.0.zip"}
    findings = CR.evaluate(state(stage=bad))
    assert "ns-version.js" in message_for(findings, "stage")


# --------------------------------------------------------------------------
# changelog curation gate (scripts/build_changelog.py)
# --------------------------------------------------------------------------
TAGS = ["v0.6.8", "v0.6.7", "v0.6.0", "v0.5.12", "v0.5.11", "v0.4.1", "v0.4.0"]
CURATED_SAMPLE = {"0.6.0": "text", "0.5.12": "text", "0.5.11": "text"}


def test_split_uncurated_separates_the_release_window_from_the_backlog():
    backlog, older = BC.split_uncurated(TAGS, CURATED_SAMPLE)
    assert backlog == ["0.6.8", "0.6.7"]          # newer than the newest curated
    assert older == ["0.4.1", "0.4.0"]           # historical, never curated
    assert "0.5.12" not in older and "0.5.12" not in backlog


def test_split_uncurated_is_empty_when_the_newest_tag_is_curated():
    backlog, older = BC.split_uncurated(["v0.6.9", *TAGS], {**CURATED_SAMPLE, "0.6.9": "text"})
    assert backlog == []
    assert older == ["0.6.8", "0.6.7", "0.4.1", "0.4.0"]


def test_split_uncurated_without_any_curated_entry_reports_everything():
    backlog, older = BC.split_uncurated(TAGS, {})
    assert backlog == [t.lstrip("v") for t in TAGS]
    assert older == []


def test_report_returns_the_blocking_count():
    assert BC.report(["0.6.8", "0.6.7"], ["0.4.1"], "0.6.0") == 2
    assert BC.report([], [], "0.6.0") == 0


def test_build_entries_only_emits_curated_tags_newest_first():
    dates = {"v0.6.0": "2026-09-10", "v0.5.12": "2026-09-10", "v0.5.11": "2026-09-02"}
    entries = BC.build_entries(TAGS, lambda tag: dates[tag], CURATED_SAMPLE)
    assert [e["version"] for e in entries] == ["0.6.0", "0.5.12", "0.5.11"]
    assert entries[0]["date"] == "2026-09-10"
    assert all(e["summary"] for e in entries)


# --------------------------------------------------------------------------
# wiring smoke test (read-only, tracked files only)
# --------------------------------------------------------------------------
def test_collect_reads_the_repo_and_loads_the_curated_table():
    collected = CR.collect(ROOT)
    assert isinstance(collected.describe, str)
    assert isinstance(collected.stage, dict)
    assert "0.6.0" in collected.curated          # loaded from build_changelog.py
    assert collected.curated["0.6.0"].startswith("AstroPilot is now NightSeam")
    # the new stage collection really reads the staged artifacts (read-only)
    stage = collected.stage
    if stage["exists"] and stage["zips"]:
        assert stage["zip_entries"], "every staged zip gets a contents entry"
        for name, entry in stage["zip_entries"].items():
            assert entry["error"] is None, (name, entry["error"])
            assert "NightSeam.exe" in entry["members"], name
        assert stage["exe_sha256"] is None or CR._SHA256_RE.match(stage["exe_sha256"])
    assert collected.exe_sha256 is None or CR._SHA256_RE.match(collected.exe_sha256)


def test_git_describe_uses_dirty_semantics():
    # the authoritative source must be the --dirty form: without it a snapshot
    # advertises a clean tag (the 0.6.0 exe hazard)
    assert "--dirty" in CR.DESCRIBE_ARGS
    describe = CR.git_describe(ROOT)
    assert isinstance(describe, str)
    if describe:
        assert describe.startswith("v") or describe[0].isdigit()


def test_git_describe_failure_is_a_finding_not_a_traceback():
    """No git / no usable scratch dir must yield '', which check_describe turns
    into a normal ERROR instead of crashing the release gate."""
    missing = ROOT / "no-such-root-for-the-release-check"
    assert CR.git_describe(missing) == ""
    findings = CR.evaluate(state(describe=""), allow_dirty=True)
    assert checks(findings) == ["describe"]


def test_read_never_raises_on_a_directory_or_missing_path():
    assert CR._read(ROOT) is None                 # a directory is not a file
    assert CR._read(ROOT / "no-such-file.json") is None
    assert CR._mtime(ROOT / "no-such-file.json") is None


def test_findings_render_ascii_only():
    findings = CR.evaluate(state())
    findings += CR.evaluate(state(describe="v0.6.9-dirty", baked="0.6.0",
                                  web={"version": "0.7.0", "describe": "v0.7.0"},
                                  curated={}, stage={}))
    assert findings
    for finding in findings:
        rendered = finding.render()
        assert rendered.isascii(), rendered
        assert finding.level.upper() in rendered and finding.check in rendered
    assert CR.console_safe("a \u2014 b \u2026 c").isascii()


def test_run_prints_a_report_and_returns_an_exit_code():
    import io
    buf = io.StringIO()
    code = CR.run(allow_dirty=True, include_stage=False, root=ROOT, stream=buf)
    text = buf.getvalue()
    assert "authoritative version: git describe --tags --always --dirty" in text
    assert text.isascii()
    # The exit code must EQUAL what the findings imply for this root - asserting
    # `code in (0, 1)` would accept a crash (which also exits 1) as a pass.
    findings = CR.evaluate(CR.collect(ROOT), allow_dirty=True, include_stage=False)
    expected = CR.exit_code(findings)
    assert expected in (0, 1)
    assert code == expected, (code, expected, [f.render() for f in findings])


def test_run_returns_0_when_nothing_is_wrong_and_1_when_something_is():
    """Both exit paths, with the finding that decides them made explicit."""
    import io
    findings = CR.evaluate(CR.collect(ROOT), allow_dirty=True, include_stage=False)
    errors = [f for f in findings if f.level == CR.ERROR]
    buf = io.StringIO()
    code = CR.run(allow_dirty=True, include_stage=False, root=ROOT, stream=buf)
    assert code == (1 if errors else 0), [f.render() for f in findings]
    # the report must name every error it counted (no silent findings)
    for f in errors:
        assert f.check in buf.getvalue(), f.render()


def test_required_runtime_modules_are_present_and_tracked():
    findings = CR.check_source_module_findings(ROOT)
    assert findings == [], [f.render() for f in findings]


def test_release_refuses_an_untracked_catalog_dependency(monkeypatch):
    original_git = CR.git

    def without_icons(args, root=ROOT):
        rc, output = original_git(args, root)
        if args == ["ls-files", "--", "app", "static/js", "web", "core"]:
            output = "\n".join(line for line in output.splitlines()
                               if line != "static/js/catalog_icons.js")
        return rc, output

    monkeypatch.setattr(CR, "git", without_icons)
    findings = CR.check_source_module_findings(ROOT)
    assert any(f.level == CR.ERROR and "catalog_icons.js is not tracked" in f.message
               for f in findings)


def test_make_release_consults_the_core_drift_check(monkeypatch):
    """FIX-9 (review TL1): check_release.py::run() appended
    check_core_findings(), but make_release.run_check() did not - its 'release
    check: ok' could not see a core/ drift. The core check must be consulted
    before staging (mutation: remove the call -> this test fails)."""
    import pytest
    MR = _load("ns_make_release", ROOT / "scripts" / "make_release.py")
    called = []

    def fake_core(root=ROOT):
        called.append(root)
        return [CR.Finding(CR.ERROR, "core", "simulated core drift")]

    monkeypatch.setattr(MR, "check_core_findings", fake_core)
    monkeypatch.setattr(MR, "collect", lambda *a, **k: None)
    monkeypatch.setattr(MR, "evaluate", lambda *a, **k: [])
    with pytest.raises(SystemExit):
        MR.run_check("0.6.21", False, False, "before staging")
    assert called, "make_release.run_check must consult check_core_findings()"
