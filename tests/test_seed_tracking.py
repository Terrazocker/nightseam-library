"""Shipped seed inputs belong in Git; actual libraries remain ignored."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = "app/data"
SEED = SEED_DIR + "/openned.csv"

def test_seed_inputs_and_private_library_have_separate_ignore_rules(tmp_path):
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(["git", "init", "--quiet", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    seed = tmp_path / SEED
    seed.parent.mkdir(parents=True)
    seed.write_text("seed", encoding="utf-8")
    private = tmp_path / "data/private.db"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"private")
    result = subprocess.run(["git", "check-ignore", str(seed)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1, "shipped seed data must not be ignored"
    result = subprocess.run(["git", "check-ignore", str(private)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, "user library data must stay ignored"

def test_required_seed_inputs_are_tracked_in_this_project():
    result = subprocess.run(["git", "ls-files", "--", SEED_DIR], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    tracked = set(result.stdout.splitlines())
    for name in ("openned.csv", "supplement.csv", "catalog_provenance.json"):
        relative = SEED_DIR + "/" + name
        assert relative in tracked and (ROOT / relative).is_file(), relative
