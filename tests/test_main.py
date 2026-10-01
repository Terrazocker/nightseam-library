"""Tests for the single-instance / port handling in main.py.

`AGENTS.md` documents the "stale exe holds port 8407" trap, and the handoff is
what stops two NightSeam instances from sharing one SQLite file and one archive
folder - but none of it had a test. Everything here is local (a socket or a
throwaway HTTP server on a loopback port), no network, no real `data/`.
"""

import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

import main


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
    main.write_instance_file(12345, catalog_only=True)
    data = json.loads((tmp_path / "instance.json").read_text())
    assert data["port"] == 12345
    assert data["catalog_only"] is True
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
