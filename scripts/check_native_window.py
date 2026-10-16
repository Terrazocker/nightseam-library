"""Smoke-test a real packaged WinForms/WebView2 window in private scratch.

Requires Windows and Playwright in the separate browser environment. Never
accepts browser fallback as native success. Does not simulate physical DPI/Snap.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests/browser'))
import harness
from process_tree import _Accounting


def wait_for(probe, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(.1)
    raise RuntimeError(f'Timed out waiting for {description}')


class NativeWindows:
    def __init__(self, tree):
        self.tree = tree
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback, wintypes.LPARAM], wintypes.BOOL),
            'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetWindowTextW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
            'IsWindow': ([wintypes.HWND], wintypes.BOOL),
            'IsZoomed': ([wintypes.HWND], wintypes.BOOL),
            'GetWindowLongPtrW': ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            'GetWindowRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'SetWindowPos': ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            'PostMessageW': ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            'GetDpiForWindow': ([wintypes.HWND], wintypes.UINT),
            'SystemParametersInfoW': ([wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT], wintypes.BOOL),
            'SetThreadDpiAwarenessContext': ([wintypes.HANDLE], wintypes.HANDLE),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.kernel.IsProcessInJob.restype = wintypes.BOOL
        # Only this test thread's coordinate interpretation changes.
        self.previous_dpi = self.check(self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)))

    @staticmethod
    def check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def owns(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            owned = wintypes.BOOL()
            return bool(self.kernel.IsProcessInJob(handle, self.tree.job, ctypes.byref(owned)) and owned.value)
        finally:
            self.kernel.CloseHandle(handle)

    def windows(self):
        found = []
        @self.callback
        def collect(hwnd, _):
            if self.user.IsWindowVisible(hwnd) and self.owns(hwnd):
                kind, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(512)
                self.user.GetClassNameW(hwnd, kind, len(kind))
                self.user.GetWindowTextW(hwnd, title, len(title))
                found.append((hwnd, kind.value, title.value))
            return True
        self.check(self.user.EnumWindows(collect, 0))
        return found

    def main_window(self):
        return next((hwnd for hwnd, kind, title in self.windows()
                     if kind.startswith('WindowsForms10.') and title == 'NightSeam — Image Catalog'), None)

    def rectangle(self, hwnd):
        if not self.owns(hwnd):
            raise RuntimeError('The private native window disappeared')
        rectangle = wintypes.RECT()
        self.check(self.user.GetWindowRect(hwnd, ctypes.byref(rectangle)))
        return (rectangle.left, rectangle.top, rectangle.right - rectangle.left,
                rectangle.bottom - rectangle.top)

    def move(self, hwnd, bounds):
        if not self.owns(hwnd):
            raise RuntimeError('Refusing to move a window outside the private test job')
        self.check(self.user.SetWindowPos(hwnd, None, *bounds, 0x0014))

    def post(self, hwnd, message, wparam=0):
        if not self.owns(hwnd):
            raise RuntimeError('Refusing to control a window outside the private test job')
        self.check(self.user.PostMessageW(hwnd, message, wparam, 0))

    def processes_exited(self):
        state = _Accounting()
        self.check(self.tree.api.QueryInformationJobObject(
            self.tree.job, 1, ctypes.byref(state), ctypes.sizeof(state), None))
        return state.ActiveProcesses == 0

    def restore_thread(self):
        if self.previous_dpi:
            self.user.SetThreadDpiAwarenessContext(self.previous_dpi)


def run(executable):
    from playwright.sync_api import sync_playwright
    artifacts = harness.ARTIFACTS
    artifacts.mkdir(parents=True, exist_ok=True)
    server = harness.DesktopServer(executable, seed=False, native=True, debug_port=harness.free_port())
    windows = None
    checks = []
    def passed(name):
        checks.append(name)
        print(f'[PASS] native {name}', flush=True)
    try:
        server.start(timeout=60)
        archive = server.tmp / 'archive'
        archive.mkdir(exist_ok=True)
        windows = NativeWindows(server.tree)
        hwnd = wait_for(windows.main_window, 'owned native WinForms window', timeout=60)
        debug_url = f'http://127.0.0.1:{server.debug_port}'
        def debug_ready():
            try:
                return harness._http(debug_url + '/json/version', timeout=1)
            except OSError:
                return None
        wait_for(debug_ready, 'private WebView2 debugging endpoint')
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(debug_url, timeout=30000)
            page = next((page for context in browser.contexts for page in context.pages
                         if page.url.startswith(server.base)), None)
            if page is None:
                raise RuntimeError('Native WebView2 did not contain the private catalog page')
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function("document.body.dataset.windowChrome === 'native'")
            assert page.evaluate('window.pywebview.api.host_capabilities()') == {
                'window_chrome': 'native', 'native_folder_picker': True}
            passed('real-webview2-bridge')

            flags = windows.user.GetWindowLongPtrW(hwnd, -16)
            required = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
            assert flags & required == required, 'Native frame lacks caption/resize/system/min/max styles'
            passed('standard-native-frame')

            windows.post(hwnd, 0x0112, 0xF120)  # WM_SYSCOMMAND / SC_RESTORE
            wait_for(lambda: not windows.user.IsZoomed(hwnd), 'restored native window')
            work = wintypes.RECT()
            windows.check(windows.user.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0))
            width, height = min(900, work.right - work.left - 80), min(650, work.bottom - work.top - 80)
            if width < 500 or height < 380:
                raise RuntimeError('Windows desktop work area is too small for the native smoke test')
            target = work.left + 40, work.top + 40, width, height
            font = page.evaluate('getComputedStyle(document.documentElement).fontSize')
            windows.move(hwnd, target)
            wait_for(lambda: windows.rectangle(hwnd) == target, 'moved and resized native bounds')
            assert page.evaluate('getComputedStyle(document.documentElement).fontSize') == font
            passed('move-resize-stable-density')

            windows.post(hwnd, 0x0112, 0xF030)  # SC_MAXIMIZE
            wait_for(lambda: windows.user.IsZoomed(hwnd), 'maximized native window')
            windows.post(hwnd, 0x0112, 0xF120)
            wait_for(lambda: not windows.user.IsZoomed(hwnd) and windows.rectangle(hwnd) == target,
                     'restored normal bounds')
            passed('maximize-restore')

            page.locator('#catalog-tab-import').click()
            before = page.locator('#arcDir').input_value()
            assert Path(server.api('api/state')['image_dir']).resolve() == archive.resolve()
            page.locator('#btnArcBrowse').click()
            dialog = wait_for(lambda: next((handle for handle, kind, _ in windows.windows()
                                           if kind == '#32770'), None), 'owned native folder picker')
            windows.post(dialog, 0x0010)  # WM_CLOSE cancels without selecting anything.
            wait_for(lambda: not windows.user.IsWindow(dialog), 'folder-picker cancellation')
            assert page.evaluate('window.pywebview.api.host_capabilities()')['native_folder_picker'] is True
            assert page.locator('#arcDir').input_value() == before
            assert Path(server.api('api/state')['image_dir']).resolve() == archive.resolve()
            passed('real-folder-picker-cancel')

            page.screenshot(path=str(artifacts / 'native-webview.png'))
            dpi = windows.user.GetDpiForWindow(hwnd)
            windows.post(hwnd, 0x0010)
            server.proc.wait(timeout=25)
            assert server.proc.returncode == 0, f'Native app exited with {server.proc.returncode}'
            wait_for(windows.processes_exited, 'all native app descendants to exit', timeout=20)
            harness._wait_listener_closed(server.port)
            passed('graceful-close-and-process-cleanup')
        placement = server.tmp / 'local-app-data/NightSeam/window-placement.json'
        record = json.loads(placement.read_text(encoding='utf-8'))
        assert record['normal'] == dict(zip(('x', 'y', 'width', 'height'), target))
        assert record['maximized'] is False
        assert not placement.with_name('.window-placement.json.tmp').exists()
        passed('private-placement-persistence')
        (artifacts / 'native-window.json').write_text(json.dumps({
            'checks': checks, 'window_dpi': dpi, 'normal_bounds': target,
            'physical_dpi_changes_tested': False, 'snap_gestures_tested': False}, indent=2) + '\n', encoding='utf-8')
        print(f'check_native_window: {len(checks)}/7 passed')
        return 0
    except Exception:
        if server.tmp is not None:
            for log in (server.tmp / 'logs').glob('nightseam.log*'):
                shutil.copyfile(log, artifacts / f'native-{log.name}')
        if windows is not None:
            (artifacts / 'native-failure-windows.json').write_text(
                json.dumps({'passed': checks, 'owned_windows': windows.windows()}, indent=2) + '\n',
                encoding='utf-8')
        raise
    finally:
        if windows is not None:
            windows.restore_thread()
        server.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=harness.DEFAULT_EXE)
    args = parser.parse_args()
    if os.name != 'nt':
        print('check_native_window: Windows is required; no native checks executed')
        return 2
    try:
        return run(args.exe)
    except Exception:
        import traceback
        traceback.print_exc()
        print('check_native_window: FAIL (native UI or infrastructure failure)')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
