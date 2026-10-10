import os
import sys
import tempfile
from pathlib import Path

import pytest

# make the project root importable when running `pytest` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.clean_workspace import remove_tree

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path(request):
    """A temp dir this sandbox can CREATE and DELETE (overrides pytest's own).

    The old root `.pytest-tmp` directory could acquire an
    owner-SID-only DACL in this environment: every `tmp_path` test then ERRORS at
    fixture cleanup with WinError 5 (an environment artifact, not a test
    failure), and the repository's own scratch dir cannot be removed. Keeping the
    per-test dirs under the gitignored `logs/` - which the sandbox token owns -
    makes the suite run cleanly with no extra plugin or command-line flag.
    """
    base = ROOT / "logs" / ".pytest-tmp"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="test-", dir=base))
    (d / ".owner-pid").write_text(str(os.getpid()), encoding="utf-8")
    original_cwd = Path.cwd()
    try:
        yield d
    finally:
        # monkeypatch.chdir is restored by its own later fixture teardown.
        # Windows cannot remove the directory while this process stands in it.
        if Path.cwd().is_relative_to(d):
            os.chdir(original_cwd)
        remove_tree(d)


@pytest.fixture(autouse=True)
def _never_touch_the_user_profile(monkeypatch, tmp_path):
    """Isolate data, fallback archives and placement for every test.

    Patch bound imports as well as app.paths so restore/cache writes reach this
    test's private directory. Tests may override archive settings explicitly;
    the default can never reach a profile archive or either application store.
    """
    monkeypatch.setattr("app.paths.default_archive_dir",
                        lambda: tmp_path / "default-archive")
    sandbox = tmp_path / "data"

    def _sandbox_placement() -> Path:
        return sandbox / "local" / "window-placement.json"

    monkeypatch.setattr("app.paths.window_placement_path", _sandbox_placement)

    def _sandbox_data() -> Path:
        # mirror paths.data_dir(): the writers assume <data>/panos exists
        (sandbox / "panos").mkdir(parents=True, exist_ok=True)
        return sandbox

    for target in ("app.paths.data_dir", "app.server.data_dir"):
        monkeypatch.setattr(target, _sandbox_data, raising=False)
    # only if app.images is already imported - importing it here would create the
    # real data/imgcache as a side effect of collecting tests
    images = sys.modules.get("app.images")
    if images is not None:
        monkeypatch.setattr(images, "CACHE_DIR", sandbox / "imgcache",
                            raising=False)
    # Many test clients intentionally share Database instances across requests.
    # Own their connections for this test and close them AFTER dependent client
    # fixtures stop, BEFORE tmp_path teardown tries to delete SQLite files.
    from app.database import Database
    original_init = Database.__init__
    connections = []

    def owned_init(self, *args, **kwargs):
        try:
            original_init(self, *args, **kwargs)
        finally:
            if hasattr(self, "conn"):
                connections.append(self.conn)

    monkeypatch.setattr(Database, "__init__", owned_init)
    try:
        yield
    finally:
        for connection in connections:
            connection.close()
