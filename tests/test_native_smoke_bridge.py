"""Exercise the native smoke probe against real JavaScript Promise settlement."""
import json
from contextlib import contextmanager
import sys
from types import ModuleType, SimpleNamespace

from py_mini_racer import MiniRacer
import pytest

from scripts import check_native_window as native
from scripts.check_native_window import host_capabilities


CAPABILITIES = {'window_chrome': 'native', 'native_folder_picker': True}
PROBE = '__nightseamNativeSmokeCapabilities'


@pytest.mark.parametrize('failure', [None, 'navigation', 'capabilities'])
def test_native_readiness_observes_initial_page_and_detaches_without_closing_app(monkeypatch, failure):
    events = []
    base = 'http://127.0.0.1:12345/'
    class Page:
        url = base + 'catalog.html'
        def locator(self, selector):
            assert selector == 'body[data-window-chrome="native"]'
            return SimpleNamespace(wait_for=lambda **kwargs: events.append('native-body'))
    page = Page()
    context = SimpleNamespace(pages=[])
    class Discovery:
        def send(self, method):
            assert method == 'Target.getTargets'
            events.append('targets')
            context.pages = [page]
        def detach(self):
            events.append('detach')
    browser = SimpleNamespace(contexts=[context], new_browser_cdp_session=lambda: Discovery())
    def connect(url, timeout):
        assert url == 'http://127.0.0.1:23456' and timeout == 30000
        events.append('connect')
        return browser
    @contextmanager
    def playwright():
        events.append('driver-start')
        try:
            yield SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
        finally:
            events.append('driver-stop')
    api = ModuleType('playwright.sync_api')
    api.sync_playwright = playwright
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', api)
    monkeypatch.setattr(native.harness, '_http', lambda *args, **kwargs: events.append('endpoint') or {'ready': True})
    def wait(probe, description, **kwargs):
        result = probe()
        if failure == 'navigation' and 'navigation' in description:
            raise RuntimeError('injected navigation timeout')
        return result
    monkeypatch.setattr(native, 'wait_for', wait)
    def capabilities(actual):
        assert actual is page
        events.append('capabilities')
        return {} if failure == 'capabilities' else CAPABILITIES
    monkeypatch.setattr(native, 'host_capabilities', capabilities)
    def check():
        with native.native_catalog(SimpleNamespace(debug_port=23456, base=base)) as actual:
            events.append('ready')
            assert actual is page
    if failure:
        with pytest.raises(RuntimeError):
            check()
        assert 'ready' not in events
    else:
        check()
        assert events.index('native-body') < events.index('capabilities') < events.index('ready')
    assert events.count('connect') == events.count('detach') == events.count('driver-stop') == 1
    assert events[-1] == 'driver-stop'


class JavaScriptPage:
    def __init__(self, context):
        self.context = context

    def evaluate(self, expression, argument):
        result = self.context.eval(
            f'JSON.stringify(({expression})({json.dumps(argument)}))')
        return json.loads(result) if isinstance(result, str) else None


@pytest.fixture
def bridge():
    with MiniRacer() as context:
        context.eval('''
            globalThis.window = globalThis;
            globalThis.calls = 0;
            globalThis.pywebview = {api: {host_capabilities: () => {
                calls++;
                return new Promise((resolve, reject) => {
                    globalThis.complete = resolve;
                    globalThis.refuse = reject;
                });
            }}};
        ''')
        yield JavaScriptPage(context)


def assert_clean_single_call(page):
    assert page.context.eval('calls') == 1
    assert page.context.eval(f'Object.hasOwn(window, {json.dumps(PROBE)})') is False


def test_native_probe_retains_pending_promise_and_returns_capabilities(bridge):
    original = bridge.evaluate
    pending = []

    def evaluate(expression, argument):
        result = original(expression, argument)
        if result is None and bridge.context.eval(
                f'Object.hasOwn(window, {json.dumps(PROBE)})'):
            pending.append(bridge.context.eval(f'window.{PROBE}.promise instanceof Promise'))
            assert bridge.context.eval('calls') == 1
            bridge.context.eval(f'complete({json.dumps(CAPABILITIES)})')
        return result

    bridge.evaluate = evaluate
    assert host_capabilities(bridge) == CAPABILITIES
    assert pending == [True]
    assert_clean_single_call(bridge)


def test_native_probe_rejection_fails_without_retry(bridge):
    bridge.context.eval('''pywebview.api.host_capabilities = () => {
        calls++;
        return Promise.reject(new Error('bridge refused'));
    }''')
    with pytest.raises(RuntimeError, match='Native host capabilities rejected: bridge refused'):
        host_capabilities(bridge)
    assert_clean_single_call(bridge)


def test_native_probe_missing_callback_times_out_without_retry(bridge):
    with pytest.raises(RuntimeError, match='Timed out waiting for native WebView2 host capabilities'):
        host_capabilities(bridge, timeout=.01)
    assert_clean_single_call(bridge)
