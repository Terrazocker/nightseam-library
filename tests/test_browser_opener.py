"""System browser handoff must never inherit frozen private libraries."""
from types import SimpleNamespace
import threading

import pytest

import main
from app import folder_opener


@pytest.mark.parametrize('original', [None, '/system-original'])
def test_linux_browser_handoff_restores_library_environment(monkeypatch, original):
    monkeypatch.setattr(main, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(folder_opener, 'sys', SimpleNamespace(platform='linux', frozen=True))
    monkeypatch.setenv('LD_LIBRARY_PATH', '/private/_MEI')
    if original is None:
        monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    else:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    monkeypatch.setattr(folder_opener.shutil, 'which', lambda _: '/system/xdg-open')
    calls, reaped = [], threading.Event()
    class Process:
        def wait(self):
            reaped.set()
            return 0
    def spawn(args, **options):
        calls.append((args, options))
        return Process()
    monkeypatch.setattr(folder_opener.subprocess, 'Popen', spawn)
    url = 'http://127.0.0.1:8407/catalog.html?literal=$(unchanged);x=y'
    assert main.open_browser(url)
    assert reaped.wait(3)
    args, options = calls[0]
    assert args == ['/system/xdg-open', url]
    assert not options.get('shell')
    assert options['env'].get('LD_LIBRARY_PATH') == original
    assert folder_opener.os.environ['LD_LIBRARY_PATH'] == '/private/_MEI'


def test_missing_linux_opener_keeps_manual_url_available(monkeypatch, caplog):
    monkeypatch.setattr(main, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(folder_opener.shutil, 'which', lambda _: None)
    assert not main.open_browser('http://127.0.0.1:8407/')
    assert 'xdg-utils' in caplog.text and 'http://127.0.0.1:8407/' in caplog.text


@pytest.mark.parametrize('result', [False, True])
def test_other_platforms_keep_webbrowser_result(monkeypatch, result):
    monkeypatch.setattr(main, 'sys', SimpleNamespace(platform='win32'))
    calls = []
    def opened(url):
        calls.append(url)
        return result
    monkeypatch.setattr(main.webbrowser, 'open', opened)
    assert main.open_browser('http://127.0.0.1:8407/') is result
    assert calls == ['http://127.0.0.1:8407/']
