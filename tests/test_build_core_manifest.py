"""A corrupt retention manifest must never mutate existing core bakes."""

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = "0123456789abcdef"


@pytest.fixture
def copied_build(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "build_core_manifest_test", ROOT / "scripts/build_core.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    shutil.copytree(ROOT / "core", tmp_path / "core")
    shutil.copytree(ROOT / "static/js/core", tmp_path / "static/js/core")
    shutil.copy(ROOT / "static/js/core.js", tmp_path / "static/js/core.js")
    build.CORE = tmp_path / "core"
    build.MANIFEST = build.CORE / "core.manifest.json"
    build.STATIC_JS = tmp_path / "static/js"
    previous = build.STATIC_JS / "core" / PREVIOUS
    previous.mkdir()
    (previous / "retention-evidence.txt").write_bytes(b"preserve prior bake")
    monkeypatch.setattr("sys.argv", ["build_core.py"])
    return build, tmp_path


def _snapshot(root):
    return {
        path.relative_to(root).as_posix():
            (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("field,value", [
    ("hash", None), ("hash", ""), ("hash", "abc"),
    ("hash", "ABCDEF0123456789"), ("hash", "../outside"),
    ("hash", 123), ("hash", []), ("hash", {}),
    ("previous", ""), ("previous", "bad"),
    ("previous", "ABCDEF0123456789"), ("previous", 123),
    ("previous", False), ("previous", []), ("previous", {}),
    ("token", 0), ("token", -1), ("token", True), ("token", False),
    ("token", 1.0), ("token", 1.5), ("token", "44"),
    ("token", None), ("token", []), ("token", {}),
])
def test_semantically_invalid_manifest_refuses_without_changes(
        copied_build, field, value, capsys):
    build, root = copied_build
    manifest = json.loads(build.MANIFEST.read_text(encoding="utf-8"))
    manifest["previous"] = PREVIOUS
    manifest[field] = value
    build.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")
    before = _snapshot(root)

    assert build.main() == 1
    output = capsys.readouterr().out
    assert "refusing to bake over a corrupt manifest" in output
    assert field in output
    assert "restore or delete core/core.manifest.json deliberately" in output
    assert _snapshot(root) == before


@pytest.mark.parametrize("content", [
    "{bad", "null", "[]", "42", '"manifest"',
    '{"hash":"0123456789abcdef"}', '{"token":44}',
])
def test_malformed_or_incomplete_manifest_refuses_without_changes(
        copied_build, content, capsys):
    build, root = copied_build
    build.MANIFEST.write_text(content, encoding="utf-8")
    before = _snapshot(root)
    assert build.main() == 1
    assert "refusing to bake over a corrupt manifest" in capsys.readouterr().out
    assert _snapshot(root) == before


@pytest.mark.parametrize("previous", [None, PREVIOUS, "absent"])
def test_valid_manifest_preserves_token_and_retention(copied_build, previous):
    build, _ = copied_build
    manifest = json.loads(build.MANIFEST.read_text(encoding="utf-8"))
    if previous == "absent":
        manifest.pop("previous", None)
    else:
        manifest["previous"] = previous
    build.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")

    assert build.main() == 0
    result = json.loads(build.MANIFEST.read_text(encoding="utf-8"))
    assert result["hash"] == manifest["hash"]
    assert result["token"] == manifest["token"]
    assert result["previous"] == (PREVIOUS if previous == PREVIOUS else None)
    expected = {manifest["hash"]}
    if previous == PREVIOUS:
        expected.add(PREVIOUS)
    assert {path.name for path in (build.STATIC_JS / "core").iterdir()} == expected


def test_valid_changed_tree_increments_token_and_retains_prior_hash(copied_build):
    build, _ = copied_build
    manifest = json.loads(build.MANIFEST.read_text(encoding="utf-8"))
    manifest["previous"] = PREVIOUS
    build.MANIFEST.write_text(json.dumps(manifest), encoding="utf-8")
    # Change only an audit copy, without altering astronomy or the real core.
    index = build.CORE / "index.js"
    index.write_bytes(index.read_bytes() + b"\n// private changed-tree test\n")

    assert build.main() == 0
    result = json.loads(build.MANIFEST.read_text(encoding="utf-8"))
    assert result["hash"] != manifest["hash"]
    assert result["token"] == manifest["token"] + 1
    assert result["previous"] == manifest["hash"]
    assert {path.name for path in (build.STATIC_JS / "core").iterdir()} == {
        result["hash"], manifest["hash"]}


def test_deliberately_absent_manifest_allows_first_bake(copied_build):
    build, _ = copied_build
    build.MANIFEST.unlink()
    expected_hash, expected_files = build.tree_hash()

    assert build.main() == 0
    result = json.loads(build.MANIFEST.read_text(encoding="utf-8"))
    assert result == {
        "hash": expected_hash, "token": 1, "previous": None,
        "files": expected_files}
    assert {path.name for path in (build.STATIC_JS / "core").iterdir()} == {
        expected_hash}
    stub = (build.STATIC_JS / build.STUB_NAME).read_text(encoding="utf-8")
    assert f"'./core/{expected_hash}/index.js'" in stub
