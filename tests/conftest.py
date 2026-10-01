import shutil
import sys
import uuid
from pathlib import Path

import pytest

# make the project root importable when running `pytest` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path(request):
    """A temp dir this sandbox can CREATE and DELETE (overrides pytest's own).

    `pytest.ini` sets `--basetemp=.pytest-tmp`, and that directory can acquire an
    owner-SID-only DACL in this environment: every `tmp_path` test then ERRORS at
    fixture cleanup with WinError 5 (an environment artifact, not a test
    failure), and the repository's own scratch dir cannot be removed. Keeping the
    per-test dirs under the gitignored `logs/` - which the sandbox token owns -
    makes the suite run cleanly with no extra plugin or command-line flag.
    """
    base = ROOT / "logs" / ".pytest-tmp"
    base.mkdir(parents=True, exist_ok=True)
    d = base / uuid.uuid4().hex[:12]
    d.mkdir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _never_touch_the_user_profile(monkeypatch):
    """Keep the DEFAULT archive folder inside the repo for every test.

    `Database.image_dir()` falls back to `paths.default_archive_dir()`
    (``~/Documents/NightSeam Images``) whenever a test's database has no
    `image_dir` setting, and a restore then WRITES there - a test that forgot to
    set the setting silently polluted the developer's real Documents folder
    (found during review: m31.fit / x.fit / sub/pwn.bat had landed there).
    Tests that want a specific archive still set `image_dir` themselves; this
    only guarantees the fallback can never reach the user's profile.

    The path is under `logs/` (gitignored, writable) and is NOT created here:
    the writer creates it on demand, so this fixture can never fail a run in a
    sandbox that cannot mkdir inside the pytest temp tree.

    It also redirects the DATA dir. `app/paths.py::data_dir()` resolves to the
    REPO's real `data/` and even mkdirs `data/panos`, so a test that imported a
    bundle carrying a panorama payload wrote that payload into the developer's
    data directory - it was found twice as a stray `data/panos/ok.jpg` (1 byte
    during the 0.6.32 review, 8 bytes during the fix session). The timing was the
    nasty part: the assertion guarding it (`test_api.py`) reads that same real
    directory, so the pollution happened precisely when the test was FAILING.
    `app/backup.py`, `app/server.py` and `app/images.py` bind `data_dir` with
    `from .paths import data_dir`, so the module attribute must be patched in
    each of them - patching `app.paths` alone would not reach the callers.
    """
    monkeypatch.setattr("app.paths.default_archive_dir",
                        lambda: ROOT / "logs" / ".pytest-default-archive")
    sandbox = ROOT / "logs" / ".pytest-data"

    def _sandbox_placement() -> Path:
        return sandbox / "local" / "window-placement.json"

    monkeypatch.setattr("app.paths.window_placement_path", _sandbox_placement)

    def _sandbox_data() -> Path:
        # mirror paths.data_dir(): the writers assume <data>/panos exists
        (sandbox / "panos").mkdir(parents=True, exist_ok=True)
        return sandbox

    for target in ("app.paths.data_dir", "app.backup.data_dir",
                   "app.server.data_dir"):
        monkeypatch.setattr(target, _sandbox_data, raising=False)
    # only if app.images is already imported - importing it here would create the
    # real data/imgcache as a side effect of collecting tests
    images = sys.modules.get("app.images")
    if images is not None:
        monkeypatch.setattr(images, "CACHE_DIR", sandbox / "imgcache",
                            raising=False)
