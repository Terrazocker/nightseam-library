"""Linux desktop opening uses literal arguments and fails cleanly headlessly."""
import errno
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import folder_opener
from app.database import Database
from app.server import create_app


def linux(monkeypatch):
    monkeypatch.delattr(folder_opener.os, 'startfile', raising=False)
    monkeypatch.setattr(folder_opener, 'sys', SimpleNamespace(platform='linux'))


@pytest.mark.parametrize('original', [None, '/system-test/libraries'])
def test_linux_folder_path_is_literal_and_launcher_is_reaped(tmp_path, monkeypatch, original):
    linux(monkeypatch)
    monkeypatch.setattr(folder_opener, 'sys', SimpleNamespace(platform='linux', frozen=True))
    monkeypatch.setenv('LD_LIBRARY_PATH', '/private-test/_MEI')
    if original is None:
        monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    else:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    calls, reaped = [], threading.Event()
    monkeypatch.setattr(folder_opener.shutil, 'which', lambda name: '/private-test/xdg-open')
    class Process:
        def wait(self):
            reaped.set()
            return 0
    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        return Process()
    monkeypatch.setattr(folder_opener.subprocess, 'Popen', spawn)
    path = tmp_path / 'archive $(literal); with spaces'
    folder_opener.open_folder(path)
    assert reaped.wait(3)
    args, options = calls[0]
    assert args == ['/private-test/xdg-open', str(path)]
    assert not options.get('shell') and options['start_new_session']
    assert options['env'].get('LD_LIBRARY_PATH') == original
    assert folder_opener.os.environ['LD_LIBRARY_PATH'] == '/private-test/_MEI'


@pytest.mark.parametrize('failure', ['missing', 'spawn'])
def test_linux_opener_errors_are_explanatory_api_responses(tmp_path, monkeypatch, failure):
    linux(monkeypatch)
    monkeypatch.setattr(folder_opener.shutil, 'which', lambda _: None if failure == 'missing' else '/private-test/xdg-open')
    def refused(*args, **kwargs):
        raise OSError(errno.EACCES, 'injected launcher refusal')
    monkeypatch.setattr(folder_opener.subprocess, 'Popen', refused)
    db = Database(tmp_path / 'catalog.db')
    archive = tmp_path / 'archive'
    archive.mkdir()
    db.update_settings({'image_dir': str(archive)})
    try:
        with TestClient(create_app(db)) as client:
            response = client.post('/api/library/open_folder', json={'rel': '.'})
            assert response.status_code == 500
            assert ('xdg-open' if failure == 'missing' else 'injected launcher refusal') in response.json()['detail']
            assert client.get('/api/state').status_code == 200
            assert client.post('/api/library/open_folder', json={'rel': '../outside'}).status_code == 400
    finally:
        db.conn.close()
