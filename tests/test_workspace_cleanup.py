"""Cleanup must protect tracked files, links, active owners and real data."""
import os
import subprocess

import pytest

from scripts.clean_workspace import clean, contained


def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def write(root, relative, value=b"fixture"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return path


def test_cleanup_preview_and_apply_preserve_data_and_recovery(tmp_path):
    root = checkout(tmp_path)
    scratch = write(root, "logs/.pytest-tmp/old/payload")
    os.utime(scratch.parent, (0, 0))
    protected = [write(root, name) for name in (
        "data/library.db", "dist/data/library.db", "dist/NightSeam.exe",
        "dist/release/current.zip", "logs/repository-split/recovery.bundle",
        ".venv-build/keep", "core/source.js")]
    assert clean(root) == 0
    assert scratch.exists()
    assert clean(root, apply=True) == 0
    assert not scratch.exists()
    assert all(path.exists() for path in protected)


def test_cleanup_refuses_whole_plan_if_it_contains_tracked_files(tmp_path):
    root = checkout(tmp_path)
    path = write(root, "tests/__pycache__/tracked.txt")
    other = write(root, "__pycache__/disposable.pyc")
    subprocess.run(["git", "add", "--", str(path)], cwd=root, check=True)
    with pytest.raises(ValueError, match="tracked content"):
        clean(root, apply=True)
    assert path.exists() and other.exists()


def test_cleanup_preserves_active_and_recent_scratch(tmp_path):
    root = checkout(tmp_path)
    active = write(root, "logs/.pytest-tmp/active/.owner-pid", str(os.getpid()).encode())
    recent = write(root, "logs/.pytest-tmp/recent/payload")
    os.utime(active.parent, (0, 0))
    assert clean(root, apply=True) == 0
    assert active.exists() and recent.exists()


def test_build_cleanup_keeps_three_backups_and_candidates_with_data(tmp_path):
    root = checkout(tmp_path)
    backups = [write(root, f"dist/NightSeam.before-{i}.exe") for i in range(5)]
    for i, path in enumerate(backups):
        os.utime(path, (i, i))
    candidate = write(root, "dist/split-candidate/NightSeam.exe")
    protected = write(root, "dist/catalog-progress/data/library.db")
    build = write(root, "build/temporary")
    assert clean(root, apply=True, build_output=True) == 0
    assert [p.exists() for p in backups] == [False, False, True, True, True]
    assert not candidate.exists() and not build.exists()
    assert protected.exists()


def test_build_cleanup_preserves_active_build_and_unknown_candidate_files(tmp_path):
    root = checkout(tmp_path)
    marker = write(root, "build/.owner-pid", str(os.getpid()).encode())
    unrelated = write(root, "dist/split-candidate/my-library.zip")
    assert clean(root, apply=True, build_output=True) == 0
    assert marker.exists() and unrelated.exists()


def test_unknown_owner_is_retained_even_with_zero_age(tmp_path):
    root = checkout(tmp_path)
    marker = write(root, "logs/.pytest-tmp/unknown/.owner-pid", b"0")
    assert clean(root, apply=True, older_than_hours=0) == 0
    assert marker.exists()


def test_abandoned_js_scratch_is_removed_but_live_owner_is_preserved(tmp_path):
    root = checkout(tmp_path)
    stale = write(root, "logs/.js-syntax-2147483647-0/payload")
    live = write(root, f"logs/.js-syntax-{os.getpid()}-0/payload")
    assert clean(root, apply=True, older_than_hours=0) == 0
    assert not stale.exists() and live.exists()


def test_health_discovery_cleanup_preserves_live_recent_unknown_and_other_logs(tmp_path):
    root = checkout(tmp_path)
    stale = write(root, 'logs/health-discovery-2147483647-abcdefgh/.owner-pid', b'2147483647')
    live = write(root, f'logs/health-discovery-{os.getpid()}-abcdefgh/.owner-pid', str(os.getpid()).encode())
    unknown = write(root, 'logs/health-discovery-2147483647-ijklmnop/.owner-pid', b'0')
    recent = write(root, 'logs/health-discovery-2147483647-qrstuvwx/.owner-pid', b'2147483647')
    unrelated = write(root, 'logs/health-discovery-recovery/payload')
    for path in (stale, live, unknown, unrelated):
        os.utime(path.parent, (0, 0))
    assert clean(root, apply=True) == 0
    assert not stale.exists()
    assert all(path.exists() for path in (live, unknown, recent, unrelated))


def test_cleanup_refuses_symlink_and_external_paths(tmp_path):
    root = checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError):
        contained(root, outside)
    link = root / "__pycache__"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="linked path"):
        clean(root, apply=True)
    assert outside.exists()


def test_cleanup_reports_persistent_removal_failure(monkeypatch, tmp_path):
    root = checkout(tmp_path)
    path = write(root, "__pycache__/locked.pyc")
    def fail(_):
        raise PermissionError("held by another process")
    monkeypatch.setattr("scripts.clean_workspace.remove_tree", fail)
    assert clean(root, apply=True) == 1
    assert path.exists()
