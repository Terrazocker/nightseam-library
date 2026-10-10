"""Version and launch behavior of the independent catalog."""
from pathlib import Path
from app.version import app_version,project_version
import main
ROOT=Path(__file__).resolve().parents[1]
def test_catalog_version_is_declared_locally():
    assert project_version()==(ROOT/"VERSION").read_text(encoding="utf-8").strip()
    assert app_version()==project_version()
def test_source_and_packaged_launches_default_to_catalog():
    """The planner retirement removed the launch-mode flag entirely: main.py
    always opens the catalog shell and no planner switch survives."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "--planner" not in src
    assert "catalog_only" not in src
def test_website_build_inputs_are_absent():
    assert not (ROOT/"web").exists() and not (ROOT/"landing").exists()
