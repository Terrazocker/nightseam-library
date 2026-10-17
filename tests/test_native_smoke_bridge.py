"""Exercise the native smoke probe against real JavaScript Promise settlement."""
import json

from py_mini_racer import MiniRacer
import pytest

from scripts.check_native_window import host_capabilities


CAPABILITIES = {'window_chrome': 'native', 'native_folder_picker': True}
PROBE = '__nightseamNativeSmokeCapabilities'


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
