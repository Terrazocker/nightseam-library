"""Catalog browser coverage remains registered after separation."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_catalog_runner_has_no_website_suite():
    text=(ROOT/"scripts/check_browser.py").read_text(encoding="utf-8")
    assert "import catalog_suite" in text and "import catalog_map_labels" in text
    assert "import web_suite" not in text and "import desktop_suite" not in text
    assert 'targets=("catalog",)' in text
def test_catalog_browser_actions_are_preserved():
    text=(ROOT/"tests/browser/catalog_suite.py").read_text(encoding="utf-8")
    for name in ["desktop-catalog-only-launch","desktop-catalog-selected-session-actions","desktop-catalog-archive-folder-errors"]:
        assert name in text
