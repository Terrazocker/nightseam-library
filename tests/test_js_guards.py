"""Mutation proofs for the two JS guards that had none (TO-05, 0.6.24 review).

`check_js_imports.py` and `check_dom_ids.py` were pinned only by "the real tree is
clean", which cannot distinguish a working guard from one that no longer detects
anything. Each test stages the failure shape in a scratch tree and requires the
guard to report it.

(The encoding guard's mutation proof lives in
tests/test_encoding.py::test_the_review_workspace_is_not_policed; the browser
gate's behavioural pin is
tests/test_browser_gate.py::test_browser_gate_driver_start_failure_is_exit_2.)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_broken_import_is_detected(monkeypatch, capsys, tmp_path):
    """Both halves: a specifier that resolves to no file AND a named import the
    target does not export."""
    mod = _load("check_js_imports")
    (tmp_path / "entry.js").write_text(
        "import { gone } from './lib.js';\n"
        "import { x } from './missing-file.js';\n"
        "export const x = 1;\n", encoding="utf-8")
    (tmp_path / "lib.js").write_text("export const present = 1;\n", encoding="utf-8")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "iter_js_files", lambda: sorted(tmp_path.glob("*.js")))

    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "BROKEN" in out
    assert "missing-file.js" in out
    assert "'gone' is not exported" in out


def test_a_missing_dom_id_is_detected(tmp_path):
    """A JS `$('#id')` with no matching element id in that app's HTML must fail."""
    mod = _load("check_dom_ids")
    html = tmp_path / "index.html"
    js = tmp_path / "mod.js"
    js.write_text("const a = $('#present');\nconst b = $('#absent');\n",
                  encoding="utf-8")

    html.write_text('<div id="present"></div>', encoding="utf-8")
    assert mod.check("scratch", html, [js]) == 1

    html.write_text('<div id="present"></div><div id="absent"></div>',
                    encoding="utf-8")
    assert mod.check("scratch", html, [js]) == 0


def test_dom_guard_discovers_new_transitive_modules(tmp_path):
    """A newly imported module is checked without editing a filename list."""
    mod = _load("check_dom_ids")
    html = tmp_path / "index.html"
    html.write_text('<script type="module" src="/entry.js?v=2"></script>', encoding="utf-8")
    (tmp_path / "entry.js").write_text("import './bridge.js';\n", encoding="utf-8")
    (tmp_path / "bridge.js").write_text(
        "export * from './exports.js?v=1';\nimport('./new.js');\n", encoding="utf-8")
    (tmp_path / "exports.js").write_text("import './entry.js';\n", encoding="utf-8")
    (tmp_path / "new.js").write_text("const node = $('#newly-missing');\n", encoding="utf-8")
    (tmp_path / "unloaded.js").write_text("const node = $('#unloaded');\n", encoding="utf-8")
    discovered = mod.discover_modules(html)
    assert {p.name for p in discovered} == {"entry.js", "bridge.js", "exports.js", "new.js"}
    assert mod.check("scratch", html, discovered) == 1
    html.write_text(html.read_text(encoding="utf-8") + '<div id="newly-missing"></div>',
                    encoding="utf-8")
    assert mod.check("scratch", html, mod.discover_modules(html)) == 0
    excluded = {("bridge.js", "new.js")}
    assert "new.js" not in {p.name for p in mod.discover_modules(html, excluded)}
    # A module excluded from one shell-conditional edge is still checked if
    # another, reachable edge imports it.
    with (tmp_path / "entry.js").open("a", encoding="utf-8") as entry:
        entry.write("import './new.js';\n")
    assert "new.js" in {p.name for p in mod.discover_modules(html, excluded)}


def test_dom_guard_reports_missing_module_entry(tmp_path):
    mod = _load("check_dom_ids")
    html = tmp_path / "index.html"
    html.write_text('<script type="module" src="gone.js"></script>', encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="module file missing"):
        mod.discover_modules(html)
