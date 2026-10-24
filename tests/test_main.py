"""Tests for the single-instance / port handling in main.py.

`AGENTS.md` documents the "stale exe holds port 8407" trap, and the handoff is
what stops two NightSeam instances from sharing one SQLite file and one archive
folder - but none of it had a test. Everything here is local (a socket or a
throwaway HTTP server on a loopback port), no network, no real `data/`.
"""

import http.server
import json
import os
import socket
import threading
from pathlib import Path

import pytest

import main


@pytest.mark.parametrize('existing', [False, True])
def test_required_native_failure_never_launches_browser_fallback(monkeypatch, existing):
    import logging
    import sys

    monkeypatch.setattr(main, 'existing_instance_url', lambda: 'http://127.0.0.1:1' if existing else None)
    monkeypatch.setattr(main, 'setup_logging', lambda _: logging.getLogger('native-required-test'))
    monkeypatch.setattr(main, 'run_native_window', lambda _: False)
    def forbidden(*args, **kwargs):
        pytest.fail('A required-native launch must never open a browser fallback')
    monkeypatch.setattr(main, 'launch_app_window', forbidden)
    monkeypatch.setattr(main.webbrowser, 'open', forbidden)
    monkeypatch.setattr(sys, 'argv', ['main.py', '--require-native-window', '--port', str(_free_port())])
    assert main.main() == 1
    assert not main._instance_file().exists()


def _listening_socket() -> tuple[socket.socket, int]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def _free_port() -> int:
    s, port = _listening_socket()
    s.close()
    return port


def test_port_in_use_detects_a_listener():
    s, port = _listening_socket()
    try:
        assert main.port_in_use("127.0.0.1", port) is True
    finally:
        s.close()
    assert main.port_in_use("127.0.0.1", port) is False


def test_free_port_keeps_a_free_preferred_port():
    port = _free_port()
    assert main.free_port(port, "127.0.0.1") == port


def test_free_port_moves_away_from_a_busy_port():
    s, port = _listening_socket()
    try:
        got = main.free_port(port, "127.0.0.1")
    finally:
        s.close()
    assert got != port
    assert isinstance(got, int) and 0 < got < 65536


@pytest.mark.parametrize("reuse", [False, True], ids=["plain", "so_reuseaddr"])
def test_free_port_moves_away_from_a_bound_but_unlistening_port(reuse):
    """The Windows hazard from AGENTS.md: a port that is BOUND without a
    listener (a uvicorn that reserved the socket before listen(), a crashed
    server, a socket held by another process) is invisible to a connect()
    probe, and a bind() probe WITH SO_REUSEADDR succeeds on it - so free_port()
    would hand back a port the app cannot actually use. The bind probe must
    therefore run WITHOUT SO_REUSEADDR. Both holder variants are checked: with
    SO_REUSEADDR on the holder (what uvicorn sets) the naive probe really does
    succeed, which is the documented trap."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 0))
    except OSError as exc:                      # OS refused to reserve a port
        s.close()
        pytest.skip(f"cannot bind a probe port here: {exc}")
    port = s.getsockname()[1]
    try:
        # no listen(): connect() cannot see this socket at all ...
        assert main.port_in_use("127.0.0.1", port) is False
        # ... so only a bind probe can, and it must not report it as free
        assert main.free_port(port, "127.0.0.1") != port
    finally:
        s.close()


def test_instance_file_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_instance_file",
                        lambda: tmp_path / "instance.json")
    main.write_instance_file(12345)
    data = json.loads((tmp_path / "instance.json").read_text())
    assert data["port"] == 12345
    assert data["pid"] == os.getpid()
    main.clear_instance_file()
    assert not (tmp_path / "instance.json").exists()
    main.clear_instance_file()               # idempotent


def test_existing_instance_url_returns_none_and_cleans_a_stale_entry(
        tmp_path, monkeypatch):
    f = tmp_path / "instance.json"
    monkeypatch.setattr(main, "_instance_file", lambda: f)
    f.write_text(json.dumps({"port": _free_port(), "pid": 1}))
    assert main.existing_instance_url() is None
    assert not f.exists(), "a dead instance entry must be removed"


def test_existing_instance_url_finds_a_live_instance(tmp_path, monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                       # noqa: N802 - http.server API
            body = b'{"version": "test"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):           # keep the test output clean
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    f = tmp_path / "instance.json"
    monkeypatch.setattr(main, "_instance_file", lambda: f)
    f.write_text(json.dumps({"port": port, "pid": 1}))
    try:
        assert main.existing_instance_url() == f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        srv.server_close()
    assert f.exists(), "a live instance entry must survive the probe"


def test_existing_instance_url_ignores_a_corrupt_entry(tmp_path, monkeypatch):
    f = tmp_path / "instance.json"
    monkeypatch.setattr(main, "_instance_file", lambda: f)
    f.write_text("not json at all")
    assert main.existing_instance_url() is None
    assert not f.exists()


def test_source_lifespan_stops_waiting_health_worker(tmp_path, monkeypatch):
    import logging
    import sys
    from app.database import Database
    db = Database(tmp_path / "health-main.db")
    real_create = main.create_app
    owned = {}
    def create(database):
        app = real_create(database); owned["health"] = app.state.archive_health; return app
    monkeypatch.setattr(main, "Database", lambda path: db)
    monkeypatch.setattr(main, "create_app", create)
    monkeypatch.setattr(main, "setup_logging", lambda console: logging.getLogger("health-main-test"))
    monkeypatch.setattr(main, "wait_for_exit", lambda server, idle: owned["health"].start("deep"))
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-browser", "--port", str(_free_port())])
    with db.archive_lock: assert main.main() == 0
    assert owned["health"].snapshot()["status"] == "cancelled"
    assert not owned["health"].thread.is_alive() and not main._instance_file().exists()


def test_source_shutdown_interrupts_active_acknowledgement(tmp_path, monkeypatch):
    import hashlib
    import logging
    import sys
    import time
    import httpx
    from app import archive_health
    from app.database import Database
    db = Database(tmp_path / "ack-main.db")
    root = tmp_path / "archive"; root.mkdir(); db.set_setting("image_dir", str(root))
    target = root / "edited.fit"; target.write_bytes(b"original")
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    image_id = db.add_image(dict(file=target.name, orig_name=target.name, object_name="M31", ingested_at="today",
        content_sha256=expected, file_size=target.stat().st_size, file_mtime_ns=target.stat().st_mtime_ns))
    target.write_bytes(b"edited" * (1024 * 1024))
    real_create, real_hash = main.create_app, hashlib.sha256
    owned, outcome = {}, {}; reading = threading.Event()
    def create(database):
        app = real_create(database); owned["health"] = app.state.archive_health; return app
    class SlowHash:
        def __init__(self): self.hash = real_hash()
        def update(self, data):
            self.hash.update(data); reading.set()
            assert owned["health"].stopping.wait(5), "shutdown did not signal active hashing"
        def hexdigest(self): return self.hash.hexdigest()
    def wait_for_exit(server, idle):
        health = owned["health"]; report = health.start("deep")
        deadline = time.monotonic() + 5
        while health.snapshot()["status"] == "running":
            assert time.monotonic() < deadline; time.sleep(.01)
        assert health.snapshot()["status"] == "completed"
        monkeypatch.setattr(archive_health.digests, "sha256", SlowHash)
        def request():
            try:
                outcome["response"] = httpx.post(
                    f"http://127.0.0.1:{server.config.port}/api/library/health/{report['id']}/acknowledge",
                    json={"image_id": image_id, "action": "accept_contents", "confirmed": True}, timeout=10)
            except Exception as exc: outcome["error"] = exc
        owned["request"] = threading.Thread(target=request, daemon=True); owned["request"].start()
        assert reading.wait(5)
    monkeypatch.setattr(main, "Database", lambda path: db)
    monkeypatch.setattr(main, "create_app", create)
    monkeypatch.setattr(main, "setup_logging", lambda console: logging.getLogger("health-ack-main-test"))
    monkeypatch.setattr(main, "wait_for_exit", wait_for_exit)
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-browser", "--port", str(_free_port())])
    try: assert main.main() == 0
    finally:
        if "request" in owned: owned["request"].join(timeout=6)
    assert not owned["request"].is_alive() and "error" not in outcome
    assert outcome["response"].status_code == 409
    assert db.integrity_snapshot()[1][0]["expected_sha256"] == expected
    assert not main._instance_file().exists()
