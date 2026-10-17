"""Catalog browser coverage remains registered after separation."""
from pathlib import Path
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


process_tree = _load("process_tree", ROOT / "tests/browser/process_tree.py")
harness = _load("ns_browser_harness", ROOT / "tests/browser/harness.py")
def test_catalog_runner_has_no_website_suite():
    text=(ROOT/"scripts/check_browser.py").read_text(encoding="utf-8")
    assert "import catalog_suite" in text and "import catalog_map_labels" in text
    assert "import web_suite" not in text and "import desktop_suite" not in text
    assert 'targets=("catalog",)' in text
def test_catalog_browser_actions_are_preserved():
    text=(ROOT/"tests/browser/catalog_suite.py").read_text(encoding="utf-8")
    for name in ["desktop-catalog-only-launch","desktop-catalog-selected-session-actions","desktop-catalog-archive-folder-errors"]:
        assert name in text


def test_teardown_owns_descendants_after_the_launcher_exits(tmp_path):
    """Exercise the frozen-parent failure shape against a real child listener."""
    marker = tmp_path / "listener.json"
    child = (
        "import json,socket,time; from pathlib import Path; "
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); "
        f"Path({str(marker)!r}).write_text(json.dumps(s.getsockname()[1])); "
        "time.sleep(60)"
    )
    launcher = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tree = None
    try:
        tree = process_tree.ProcessTree([sys.executable, "-c", launcher],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "gate descendant never opened its listener"
        tree.proc.wait(timeout=8)
        port = json.loads(marker.read_text(encoding="utf-8"))
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) == 0
        tree.stop()
        tree.stop()  # idempotent even though the original parent exited first
        harness._wait_listener_closed(port)
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        assert unrelated.poll() is None, "teardown touched an unrelated process"
    finally:
        if tree is not None:
            tree.stop()
        unrelated.terminate()
        unrelated.wait(timeout=8)


def test_desktop_start_failure_removes_its_private_scratch(tmp_path, monkeypatch):
    exe = tmp_path / "candidate.exe"
    exe.write_bytes(b"not an executable")
    scratch = tmp_path / "browser"
    monkeypatch.setattr(harness, "SCRATCH", scratch)

    def refused(*args, **kwargs):
        marker = Path(kwargs["cwd"]) / ".owner-pid"
        assert marker.read_text(encoding="utf-8") == str(os.getpid())
        for name in ("TEMP", "TMP", "TMPDIR"):
            assert kwargs["env"][name] == kwargs["cwd"]
        raise OSError("launch refused")

    monkeypatch.setattr(harness, "ProcessTree", refused)
    server = harness.DesktopServer(exe)
    with pytest.raises(OSError, match="launch refused"):
        server.start()
    assert not list(scratch.iterdir())
    assert server.tmp is None
    server.stop()


@pytest.mark.parametrize("accept_archive", [False, True])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("restricted_runtime", [False, True])
def test_browser_start_requires_private_archive_even_without_seed(tmp_path, monkeypatch, accept_archive, native, restricted_runtime):
    exe = tmp_path / "candidate.exe"
    exe.write_bytes(b"private fake executable")
    scratch = tmp_path / "browser"
    monkeypatch.setattr(harness, "SCRATCH", scratch)
    monkeypatch.setenv('PYTHONPATH', str(tmp_path / 'developer-packages'))
    monkeypatch.setenv('NODE_PATH', str(tmp_path / 'developer-node'))
    server = harness.DesktopServer(exe, seed=False, native=native, debug_port=43210,
                                   restricted_runtime=restricted_runtime)
    calls, trees = [], []

    class FakeTree:
        def __init__(self, *args, **kwargs):
            assert ('--no-browser' in args[0]) is (not native)
            assert ('--require-native-window' in args[0]) is native
            assert Path(kwargs['env']['LOCALAPPDATA']).is_relative_to(server.tmp)
            assert Path(kwargs['env']['WEBVIEW2_USER_DATA_FOLDER']).is_relative_to(server.tmp)
            if restricted_runtime:
                assert kwargs['env']['PATH'] == str(server.tmp / 'empty-path')
                assert not list(Path(kwargs['env']['PATH']).iterdir())
                assert 'PYTHONPATH' not in kwargs['env'] and 'NODE_PATH' not in kwargs['env']
            else:
                assert kwargs['env']['PYTHONPATH'] == str(tmp_path / 'developer-packages')
            if native:
                assert '--remote-debugging-port=43210' in kwargs['env']['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']
            self.proc = self
            self.stopped = False
            trees.append(self)
        def poll(self):
            return None
        def stop(self):
            self.stopped = True

    def http(url, method="GET", body=None, **kwargs):
        calls.append((url, method, body))
        if method == "POST":
            assert Path(body["image_dir"]).is_relative_to(server.tmp)
            return {"ok": True}
        if len(calls) > 1 and accept_archive:
            return {"image_dir": str(server.tmp / "archive")}
        return {"image_dir": str(tmp_path / "outside-browser-scratch")}

    monkeypatch.setattr(harness, "ProcessTree", FakeTree)
    monkeypatch.setattr(harness, "_http", http)
    monkeypatch.setattr(harness, "_wait_listener_closed", lambda port: None)
    if accept_archive:
        try:
            server.start()
            assert Path(calls[1][2]["image_dir"]) == server.tmp / "archive"
            assert len(calls) == 3
        finally:
            server.stop()
    else:
        with pytest.raises(RuntimeError, match="archive configuration"):
            server.start()
    assert trees[0].stopped
    assert server.tmp is None and not list(scratch.iterdir())


def test_desktop_cleanup_failure_is_reported_and_retryable(tmp_path, monkeypatch):
    server = harness.DesktopServer()
    server.tmp = tmp_path / "private"
    server.tmp.mkdir()
    actual_remove = harness.shutil.rmtree

    def locked(*args, **kwargs):
        raise PermissionError("scratch remains locked")

    monkeypatch.setattr(harness.shutil, "rmtree", locked)
    monkeypatch.setattr(harness.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError, match="scratch remains locked"):
        server.stop()
    assert server.tmp.is_dir()
    monkeypatch.setattr(harness.shutil, "rmtree", actual_remove)
    server.stop()
    assert server.tmp is None


def test_desktop_start_timeout_stops_launched_process(tmp_path, monkeypatch):
    exe = tmp_path / "candidate.exe"
    exe.write_bytes(b"test launch handled by the factory")
    scratch = tmp_path / "browser"
    monkeypatch.setattr(harness, "SCRATCH", scratch)
    launched = []

    def factory(*args, **kwargs):
        tree = process_tree.ProcessTree(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        launched.append(tree)
        return tree

    monkeypatch.setattr(harness, "ProcessTree", factory)
    server = harness.DesktopServer(exe)
    with pytest.raises(RuntimeError, match="never answered"):
        server.start(timeout=0)
    assert launched[0].proc.poll() is not None
    assert not list(scratch.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Windows job crash-safety contract")
def test_windows_owner_exit_closes_job_and_stops_descendants(tmp_path):
    marker = tmp_path / "crash-listener.json"
    child = (
        "import json,socket,time; from pathlib import Path; "
        "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); "
        f"Path({str(marker)!r}).write_text(json.dumps(s.getsockname()[1])); "
        "time.sleep(60)"
    )
    owner = (
        "import os,sys,time; from pathlib import Path; "
        f"sys.path.insert(0,{str(ROOT / 'tests/browser')!r}); "
        "from process_tree import ProcessTree; "
        f"tree=ProcessTree([sys.executable,'-c',{child!r}]); "
        f"marker=Path({str(marker)!r}); "
        "deadline=time.monotonic()+10\n"
        "while not marker.exists() and time.monotonic()<deadline: time.sleep(.05)\n"
        "os._exit(0 if marker.exists() else 3)"
    )
    result = subprocess.run([sys.executable, "-c", owner], timeout=15,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    harness._wait_listener_closed(json.loads(marker.read_text(encoding="utf-8")))
