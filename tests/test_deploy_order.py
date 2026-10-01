"""Deploy order: HTML entry points must be uploaded LAST.

0.6.1 broke the live web app because `uploadtree` walked alphabetically:
`app.js` (new, calling `WS.seedDefaultRigs`) landed before `webstore.js` (still
the old file without that export), and any visitor loading during the deploy
window cached that broken pair - "WS.seedDefaultRigs is not a function".
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

import ftps_deploy  # noqa: E402 - web/ only lands on sys.path here


def test_html_always_sorts_after_modules():
    """The rule in one line: a page goes up after the files it references."""
    assert ftps_deploy.upload_order(Path("aaa.html")) > \
        ftps_deploy.upload_order(Path("zzz.js"))
    assert ftps_deploy.upload_order(Path("about.html")) < \
        ftps_deploy.upload_order(Path("index.html"))


@pytest.mark.parametrize("tree", ["web", "landing"])
def test_index_html_is_uploaded_last(tree):
    folder = ROOT / tree
    if not folder.is_dir():
        pytest.skip(f"{tree}/ is not present")
    names = [p.name for p in sorted(folder.iterdir(), key=ftps_deploy.upload_order)]
    assert names[-1] == "index.html", f"{tree}/ would upload {names[-1]} last"
    # every asset the page names (modules, styles, JSON, images) is in place
    # before it: other .html pages may precede it, assets may not
    cut = names.index("index.html")
    late_assets = [n for n in names[cut:] if not n.endswith(".html")]
    assert not late_assets, late_assets


def test_skip_set_still_covers_deploy_tooling():
    for name in ("ftps_deploy.py", "build_web.py", ".gitignore", "x.pyc"):
        assert ftps_deploy.skip(name), name
    assert not ftps_deploy.skip("app.js")
    assert not ftps_deploy.skip("version.json")
