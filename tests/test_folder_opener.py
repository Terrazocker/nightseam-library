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


def dialog(monkeypatch, available, returncode=0, stdout=''):
    calls = []
    monkeypatch.setattr(folder_opener.shutil, 'which',
                        lambda name: f'/system-test/{name}' if name in available else None)
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(folder_opener.subprocess, 'run', run)
    return calls


def test_zenity_dialog_is_literal_and_reports_the_choice(tmp_path, monkeypatch):
    start = tmp_path / 'captures $(literal); with spaces'
    start.mkdir()
    calls = dialog(monkeypatch, {'zenity', 'kdialog'}, stdout=f'{start}\n')
    assert folder_opener.pick_folder_dialog('Choose', start) == str(start)
    (args, kwargs), = calls
    assert args == ['/system-test/zenity', '--file-selection', '--directory', '--title', 'Choose',
                    '--filename', folder_opener.os.path.join(str(start), '')]
    assert 'shell' not in kwargs and kwargs['stdin'] is folder_opener.subprocess.DEVNULL


def test_kdialog_is_the_fallback_and_cancel_returns_none(tmp_path, monkeypatch):
    calls = dialog(monkeypatch, {'kdialog'}, returncode=1)
    assert folder_opener.pick_folder_dialog('Choose', tmp_path) is None
    assert calls[0][0] == ['/system-test/kdialog', '--getexistingdirectory', str(tmp_path), '--title', 'Choose']


def test_dialog_failure_or_absence_raises(monkeypatch):
    dialog(monkeypatch, {'zenity'}, returncode=5)
    with pytest.raises(OSError):
        folder_opener.pick_folder_dialog('Choose')
    dialog(monkeypatch, set())
    with pytest.raises(OSError) as raised:
        folder_opener.pick_folder_dialog('Choose')
    assert raised.value.errno == errno.ENOENT


def test_frozen_dialog_drops_bundle_libraries(monkeypatch):
    monkeypatch.setattr(folder_opener, 'sys', SimpleNamespace(platform='linux', frozen=True))
    monkeypatch.setenv('LD_LIBRARY_PATH', '/private-test/_MEI')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', '/system-test/libraries')
    calls = dialog(monkeypatch, {'zenity'}, stdout='/captures\n')
    folder_opener.pick_folder_dialog('Choose')
    assert calls[0][1]['env']['LD_LIBRARY_PATH'] == '/system-test/libraries'


@pytest.mark.parametrize('platform, display, tools, expected', [
    ('linux', {'DISPLAY': ':0'}, {'zenity'}, True),
    ('linux', {'WAYLAND_DISPLAY': 'wayland-0'}, {'kdialog'}, True),
    ('linux', {}, {'zenity'}, False),
    ('linux', {'DISPLAY': ':0'}, set(), False),
    ('win32', {'DISPLAY': ':0'}, {'zenity'}, False),
])
def test_dialog_needs_linux_a_display_and_a_tool(monkeypatch, platform, display, tools, expected):
    monkeypatch.setattr(folder_opener, 'sys', SimpleNamespace(platform=platform))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY'):
        monkeypatch.delenv(name, raising=False)
    for name, value in display.items():
        monkeypatch.setenv(name, value)
    dialog(monkeypatch, tools)
    assert folder_opener.folder_dialog_available() is expected
