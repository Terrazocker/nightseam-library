"""Version and launch behavior of the independent catalog."""
from pathlib import Path
from app.version import app_version,project_version
import main
ROOT=Path(__file__).resolve().parents[1]
def test_catalog_version_is_declared_locally():
    assert project_version()==(ROOT/"VERSION").read_text(encoding="utf-8").strip()
    assert app_version()==project_version()
def test_source_and_packaged_launches_default_to_catalog():
    assert main.catalog_only_for_launch(frozen=False)
    assert main.catalog_only_for_launch(frozen=True)
    assert main.catalog_only_for_launch(False,frozen=False) is False
def test_website_build_inputs_are_absent():
    assert not (ROOT/"web").exists() and not (ROOT/"landing").exists()
