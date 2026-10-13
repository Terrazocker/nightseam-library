"""Build dependency gates fail on drift without changing the interpreter."""

import importlib.metadata as metadata
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ns_check_build_dependencies", ROOT / "scripts/check_build_dependencies.py")
CHECK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CHECK)


def test_actual_lock_covers_build_roots_and_exact_pins():
    pins = CHECK.read_lock(CHECK.LOCK)
    assert CHECK.ROOT_PACKAGES <= pins.keys()
    assert pins["pyinstaller"] == "6.22.2"
    assert pins["httpx2"] == "2.13.0"
    assert "pip" not in pins and "wheel" not in pins
    assert "setuptools" in pins  # required by PyInstaller


@pytest.mark.parametrize("tail", ["httpx>=0.27", "-r requirements.txt", "httpx==0.28.1; sys_platform == 'win32'",
                                  "httpx==0.28.1\nHTTPX==0.28.1"])
def test_malformed_or_duplicate_lock_rejected(tmp_path, tail):
    path = tmp_path / "lock.txt"
    path.write_text(tail, encoding="utf-8")
    with pytest.raises(ValueError):
        CHECK.read_lock(path)


@pytest.mark.parametrize("root", ["httpx2", "mini-racer", "astropy", "numpy"])
def test_lock_cannot_silently_omit_runtime_roots(tmp_path, root):
    path = tmp_path / "lock.txt"
    pins = CHECK.read_lock(CHECK.LOCK)
    pins.pop(root)
    path.write_text("\n".join(f"{name}=={version}" for name, version in pins.items()), encoding="utf-8")
    with pytest.raises(ValueError, match=root):
        CHECK.read_lock(path)


def test_inventory_reports_missing_and_wrong_versions():
    def installed(name):
        if name == "httpx2":
            raise metadata.PackageNotFoundError(name)
        return "6.22.3"

    errors = CHECK.inventory_errors({"httpx2": "2.13.0", "pyinstaller": "6.22.2"}, installed)
    assert errors == ["httpx2: missing (locked 2.13.0)",
                      "pyinstaller: installed 6.22.3, locked 6.22.2"]


def test_inventory_accepts_matching_pins_without_inspecting_unrelated_packages():
    pins = {"httpx2": "2.13.0", "pyinstaller": "6.22.2"}
    assert CHECK.inventory_errors(pins, pins.__getitem__) == []


def test_dependency_closure_detects_missing_and_incompatible_pins():
    dependencies = {"parent": ["missing>=1", "child>=2"], "child": []}
    errors = CHECK.closure_errors({"parent": "1.0", "child": "1.0"}, dependencies.__getitem__)
    assert any("missing is absent" in error for error in errors)
    assert any("child>=2 conflicts" in error for error in errors)


def test_optional_dependency_extras_not_included():
    dependencies = {"parent": ['absent; extra == "test"']}
    assert CHECK.closure_errors({"parent": "1.0"}, dependencies.__getitem__) == []


def test_dependency_closure_rejects_unhandled_required_extras():
    dependencies = {"parent": ["child[feature]>=1"], "child": []}
    assert any("unsupported extras" in error for error in
               CHECK.closure_errors({"parent": "1.0", "child": "1.0"}, dependencies.__getitem__))


def test_main_reports_drift_and_manual_setup_without_installing(monkeypatch, capsys):
    monkeypatch.setattr(CHECK, "runtime_errors", lambda: [])
    monkeypatch.setattr(CHECK, "inventory_errors", lambda pins: ["httpx2: missing (locked 2.13.0)"])
    monkeypatch.setattr(CHECK, "closure_errors", lambda pins: pytest.fail("must stop before missing-package closure"))
    assert CHECK.main() == 1
    output = capsys.readouterr().out
    assert "httpx2: missing" in output
    assert "pip install -r requirements-build-lock.txt" in output
    assert "No packages were changed" in output


def test_main_accepts_locked_environment(monkeypatch, capsys):
    monkeypatch.setattr(CHECK, "runtime_errors", lambda: [])
    monkeypatch.setattr(CHECK, "inventory_errors", lambda pins: [])
    monkeypatch.setattr(CHECK, "closure_errors", lambda pins: [])
    assert CHECK.main() == 0
    assert f"{len(CHECK.read_lock(CHECK.LOCK))} exact pins" in capsys.readouterr().out


def test_unsupported_python_rejected(monkeypatch):
    monkeypatch.setattr(CHECK.sys, "version_info", (3, 14, 0))
    assert CHECK.runtime_errors() == ["desktop builds require Windows x64 CPython 3.12"]
