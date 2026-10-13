"""scripts/check_js_syntax.py: the JS check workflow must actually PARSE the
modules it claims to guard.

The scenario these tests exist for: a duplicate `const` in `web/app.js` reached
the working tree, and check_js.py reported OK - its three checkers only match
text (import paths, DOM ids, identifiers), nothing parsed JavaScript, so the
broken module was found by a manual `node --check` instead of by the gate.

Tests use the repository's owned tmp_path fixture. Checker scratch stays under
ignored logs/; persistent cleanup refusals fail the gate instead of hiding files.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)          # the file guards __main__
    return module


SYN = _load("ns_check_js_syntax", ROOT / "scripts" / "check_js_syntax.py")

GOOD_JS = "export const answer = 42;\n"
# the real-world failure: a second declaration of the same binding
BAD_JS = "const a = 1;\nconst a = 2;\n"

# node is required for the syntax check itself (a missing node is a HARD exit-2
# failure, TO-03, never a skip); these tests skip only when the environment
# cannot run node at all, which is the same condition the checker reports.
NODE = SYN.find_node()
NODE_REASON = "" if NODE else "node is not installed"
if NODE and not NODE_REASON:
    NODE_REASON = SYN.node_usable(NODE)
needs_node = pytest.mark.skipif(
    bool(NODE_REASON), reason=f"the JS syntax check needs node ({NODE_REASON})")


@pytest.fixture()
def scratch(checker_repository):
    """Per-test JS input, removed by the strict owned tmp_path fixture."""
    d = checker_repository / "js-syntax-input"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def checker_repository(tmp_path, monkeypatch):
    """Keep checker output private; default discovery still scans shipping roots."""
    private_repo = tmp_path / "checker-repository"
    private_repo.mkdir()
    monkeypatch.setattr(SYN, "REPO", private_repo)
    return private_repo


def leftovers():
    """Scratch in this test's owned store, never another invocation's logs."""
    return sorted(p.name for p in (SYN.REPO / "logs").glob(SYN.SCRATCH_PREFIX + "*"))


# --------------------------------------------------------------------------
# the failure that motivated the check
# --------------------------------------------------------------------------
@needs_node
def test_duplicate_const_is_reported_with_file_and_line(scratch, capsys):
    bad = scratch / "dup.js"
    bad.write_text(BAD_JS, encoding="utf-8")

    code = SYN.main(["--path", str(bad)])
    out = capsys.readouterr().out

    assert code == 1, out
    assert "SYNTAX" in out
    assert "dup.js:2" in out                 # node's line, the shipped name
    assert "SyntaxError" in out              # ... and node's own message
    assert ".mjs" not in out                 # the temp copy never leaks
    assert "checked 1 js module(s), 1 syntax error(s)" in out


@needs_node
def test_a_valid_module_passes(scratch, capsys):
    good = scratch / "ok.js"
    good.write_text(GOOD_JS, encoding="utf-8")

    assert SYN.main(["--path", str(good)]) == 0
    out = capsys.readouterr().out
    assert "checked 1 js module(s), 0 syntax error(s)" in out


@needs_node
def test_a_directory_is_checked_module_by_module(scratch, capsys):
    (scratch / "one.js").write_text(GOOD_JS, encoding="utf-8")
    (scratch / "two.js").write_text(BAD_JS, encoding="utf-8")

    code = SYN.main(["--path", str(scratch)])
    out = capsys.readouterr().out

    assert code == 1, out
    assert "two.js:2" in out
    assert "one.js" not in out               # one line per FAILURE only
    assert "checked 2 js module(s), 1 syntax error(s)" in out


# --------------------------------------------------------------------------
# the real tree
# --------------------------------------------------------------------------
@needs_node
def test_the_real_repository_tree_parses(capsys):
    code = SYN.main([])                      # the default ship set
    out = capsys.readouterr().out

    assert code == 0, out
    assert "0 syntax error(s)" in out
    assert "checked 0 js module(s)" not in out


def test_default_discovery_is_the_shipping_set():
    files = SYN.collect([])
    rel = {p.relative_to(ROOT).as_posix() for p in files}

    assert not any(p.startswith("web/") for p in rel)
    assert "static/js/catalog_main.js" in rel  # the catalog UI
    # Parse authored local core and its packaged content-hashed bake.
    assert "core/horizon.js" in rel
    assert "core/index.js" in rel
    assert any(p.startswith("static/js/core/") and p.endswith("/index.js")
               for p in rel)
    assert len(files) >= 20
    assert all(p.suffix == ".js" for p in files)
    assert not any(p.name.endswith(".min.js") for p in files)
    assert not any(set(p.parts) & SYN.IGNORED_DIRS for p in files)


def test_discovery_ignores_vendor_dirs_and_minified_files(scratch):
    (scratch / "keep.js").write_text(GOOD_JS, encoding="utf-8")
    (scratch / "lib.min.js").write_text(BAD_JS, encoding="utf-8")
    vendor = scratch / "node_modules"
    vendor.mkdir()
    (vendor / "dep.js").write_text(BAD_JS, encoding="utf-8")

    assert [p.name for p in SYN.collect([str(scratch)])] == ["keep.js"]
    assert SYN.collect([str(vendor)]) == []  # ignored even when named directly


@needs_node
def test_no_scratch_files_survive_a_run(scratch, capsys):
    assert leftovers() == []
    bad = scratch / "dup.js"
    bad.write_text(BAD_JS, encoding="utf-8")

    SYN.main(["--path", str(bad)])           # failure path
    SYN.main(["--path", str(scratch)])       # success path
    capsys.readouterr()

    assert leftovers() == []


def test_checker_scratch_is_owned_and_stays_in_logs():
    directory = SYN._scratch_dir()
    try:
        assert directory.parent == SYN.REPO / "logs"
        assert (directory / ".owner-pid").read_text(encoding="utf-8") == str(os.getpid())
    finally:
        SYN.remove_tree(directory)


@needs_node
def test_checker_preserves_unrelated_preexisting_and_concurrent_scratch(scratch, monkeypatch):
    store = SYN.REPO / "logs"
    store.mkdir()
    old = store / f"{SYN.SCRATCH_PREFIX}{os.getpid()}-0"
    old.mkdir()
    (old / "evidence.txt").write_text("preserve", encoding="utf-8")
    concurrent = store / f"{SYN.SCRATCH_PREFIX}other-process-0"
    actual_check = SYN.check_one
    owned = []

    def checking(node, source, directory, index, capture):
        owned.append(directory)
        concurrent.mkdir(exist_ok=True)
        (concurrent / "evidence.txt").write_text("in use", encoding="utf-8")
        return actual_check(node, source, directory, index, capture)

    monkeypatch.setattr(SYN, "check_one", checking)
    good = scratch / "ok.js"
    good.write_text(GOOD_JS, encoding="utf-8")
    assert SYN.main(["--path", str(good)]) == 0
    assert owned and all(not directory.exists() for directory in owned)
    assert (old / "evidence.txt").read_text(encoding="utf-8") == "preserve"
    assert (concurrent / "evidence.txt").read_text(encoding="utf-8") == "in use"


def test_cleanup_refusal_fails_an_otherwise_successful_gate(scratch, monkeypatch, capsys):
    good = scratch / "ok.js"
    good.write_text(GOOD_JS, encoding="utf-8")
    actual_remove = SYN.remove_tree
    refused = []

    def locked(directory):
        refused.append(directory)
        raise PermissionError("checker scratch is locked")

    monkeypatch.setattr(SYN, "find_node", lambda: "node")
    monkeypatch.setattr(SYN, "node_usable", lambda node: "")
    monkeypatch.setattr(SYN, "check_one", lambda *args: (True, ""))
    monkeypatch.setattr(SYN, "remove_tree", locked)
    try:
        assert SYN.main(["--path", str(good)]) == 2
        output = capsys.readouterr().out
        assert "FATAL" in output and "could not remove scratch" in output
        assert refused[0].is_dir()
    finally:
        for directory in refused:
            actual_remove(directory)


# --------------------------------------------------------------------------
# graceful degradation + CLI contract
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# a missing node is a HARD failure (TO-03), not a silent skip
# --------------------------------------------------------------------------
def test_fails_hard_without_node(monkeypatch, capsys):
    """TO-03: the repo rule is "a missing node is a HARD failure, never a skip"
    (the same rule as a browser that cannot launch). Returning 0 let check_js.py
    report OK while the only JS parser in the bundle did nothing."""
    monkeypatch.setattr(SYN, "find_node", lambda: None)

    code = SYN.main([])
    out = capsys.readouterr().out

    assert code == 2, out
    assert "FATAL" in out and "node" in out
    assert leftovers() == []


def test_fails_hard_when_node_cannot_be_run(monkeypatch, capsys):
    monkeypatch.setattr(SYN, "find_node", lambda: "node")
    monkeypatch.setattr(SYN, "node_usable", lambda node: "spawn denied")

    code = SYN.main([])
    out = capsys.readouterr().out

    assert code == 2, out
    assert "FATAL" in out and "spawn denied" in out


def test_bad_usage_exits_nonzero(capsys):
    assert SYN.main(["--nope"]) == 2
    assert SYN.main(["--path"]) == 2                    # missing value
    assert SYN.main(["--path", str(ROOT / "nope.js")]) == 2   # typo, not "clean"
    capsys.readouterr()


def test_main_ignores_the_embedding_process_argv(monkeypatch, capsys):
    """check_js.py calls main() with no arguments - it must NOT pick up
    check_js.py's own sys.argv, or a stray flag would silently shrink the scan."""
    monkeypatch.setattr(sys, "argv", ["check_js.py", "--path", "nonexistent"])
    monkeypatch.setattr(SYN, "find_node", lambda: None)  # stop after discovery

    assert SYN.main() == 2          # the missing-node FATAL (TO-03), not a clean 0
    out = capsys.readouterr().out
    assert "no such file or directory" not in out


def test_check_js_wires_the_syntax_check_in():
    text = (ROOT / "scripts" / "check_js.py").read_text(encoding="utf-8")
    assert "check_js_syntax" in text         # the gate the whole point rests on
