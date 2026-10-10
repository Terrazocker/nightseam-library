"""Native catalog window configuration and local placement regression checks."""

import json
import sys
import types
from pathlib import Path

import pytest

import main
from app.window_placement import Bounds, Monitor, WindowPlacement, initial_placement


_original_placement_path = main.paths.window_placement_path


class EventHook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in self.handlers:
            handler(*args)


class FakeWindow:
    def __init__(self):
        self.x, self.y = 40, 50
        self.width, self.height = 1200, 800
        self.events = types.SimpleNamespace(**{
            name: EventHook() for name in (
                "maximized", "restored", "minimized", "resized", "moved", "closing", "before_show")
        })


class FakeScreen:
    def __init__(self, x=0, y=0, width=1920, height=1040):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.frame = types.SimpleNamespace(X=x, Y=y, Width=width, Height=height)


def test_initial_placement_starts_maximized_and_centers_normal_bounds():
    bounds, maximized = initial_placement(None, [FakeScreen()])
    assert maximized is True
    assert bounds == Bounds(240, 60, 1440, 920)


def test_first_launch_selects_the_display_containing_the_desktop_origin():
    screens = [FakeScreen(-1920, 0), FakeScreen(0, 0)]
    bounds, maximized = initial_placement(None, screens)
    assert bounds == Bounds(240, 60, 1440, 920)
    assert maximized is True


def test_saved_negative_monitor_coordinates_are_preserved():
    screens = [FakeScreen(), FakeScreen(-1920, 0, 1920, 1040)]
    record = {"version": 1, "normal": {"x": -1800, "y": 80,
              "width": 1100, "height": 740}, "maximized": False}
    bounds, maximized = initial_placement(record, screens)
    assert bounds == Bounds(-1800, 80, 1100, 740)
    assert maximized is False


def test_offscreen_saved_monitor_recovers_to_current_work_area():
    record = {"version": 1, "normal": {"x": 4000, "y": 2300,
              "width": 1100, "height": 740}, "maximized": False}
    bounds, maximized = initial_placement(record, [FakeScreen()])
    assert bounds == Bounds(240, 60, 1440, 920)
    assert maximized is False


def test_oversized_and_partly_hidden_bounds_clamp_without_losing_max_state():
    record = {"version": 1, "normal": {"x": 1700, "y": 900,
              "width": 2000, "height": 1400}, "maximized": True}
    bounds, maximized = initial_placement(record, [FakeScreen()])
    assert bounds == Bounds(0, 0, 1920, 1040)
    assert maximized is True


def test_saved_snap_rectangle_reopens_as_normal_bounds():
    record = {"version": 1, "normal": {"x": 0, "y": 0,
              "width": 940, "height": 1040}, "maximized": False}
    bounds, maximized = initial_placement(record, [FakeScreen()])
    assert bounds == Bounds(0, 0, 940, 1040)
    assert maximized is False


def test_invalid_placement_data_falls_back_to_safe_maximized_default():
    invalid_records = [
        [],
        {"version": True, "normal": {}, "maximized": False},
        {"version": 1, "normal": {"x": 0, "y": 0, "width": 900,
                                    "height": 700}, "maximized": 0},
    ]
    for record in invalid_records:
        bounds, maximized = initial_placement(record, [FakeScreen()])
        assert bounds == Bounds(240, 60, 1440, 920)
        assert maximized is True


def test_invalid_saved_bounds_recover_without_changing_valid_maximize_state():
    record = {"version": 1, "normal": {"x": 99_000_000, "y": 0,
              "width": 900, "height": 700}, "maximized": False}
    bounds, maximized = initial_placement(record, [FakeScreen()])
    assert bounds == Bounds(240, 60, 1440, 920)
    assert maximized is False


def test_window_placement_keeps_normal_bounds_when_maximized_or_minimized(tmp_path):
    path = tmp_path / "window-placement.json"
    path.write_text(json.dumps({"version": 1, "normal": {
        "x": 40, "y": 50, "width": 1200, "height": 800},
        "maximized": False}), encoding="utf-8")
    manager = WindowPlacement(path, screens=[FakeScreen()])
    window = FakeWindow()
    manager.attach(window)

    window.events.moved.fire()
    manager.flush()
    assert json.loads(path.read_text(encoding="utf-8"))["normal"] == {
        "x": 40, "y": 50, "width": 1200, "height": 800}
    assert not path.with_name(".window-placement.json.tmp").exists()

    window.x, window.y, window.width, window.height = 0, 0, 1920, 1080
    window.events.maximized.fire()
    manager.flush()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["maximized"] is True
    assert saved["normal"] == {"x": 40, "y": 50, "width": 1200, "height": 800}
    window.events.minimized.fire()
    window.x, window.y, window.width, window.height = -32000, -32000, 0, 0
    manager.flush()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["maximized"] is True
    assert saved["normal"] == {"x": 40, "y": 50, "width": 1200, "height": 800}

    window.events.closing.fire()


def test_window_placement_flushes_on_close_and_restores_state(tmp_path):
    path = tmp_path / "window-placement.json"
    path.write_text(json.dumps({"version": 1, "normal": {
        "x": 40, "y": 50, "width": 1200, "height": 800},
        "maximized": False}), encoding="utf-8")
    manager = WindowPlacement(path, screens=[FakeScreen()])
    window = FakeWindow()
    manager.attach(window)
    window.x, window.y = -420, 90
    window.events.closing.fire()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["normal"]["x"] == -420
    assert saved["normal"]["y"] == 90

    restored, maximized = initial_placement(saved, [FakeScreen()])
    assert restored == Bounds(0, 90, 1200, 800)
    assert maximized is False


def test_placement_path_uses_local_app_data_without_touching_profile(monkeypatch, tmp_path):
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    path = _original_placement_path()
    assert path == local / "NightSeam" / "window-placement.json"
    assert path.parent.is_dir()


def test_catalog_uses_standard_native_frame_and_existing_icon(monkeypatch, tmp_path):
    options_seen = []
    titles_seen = []
    start_args = []
    webview = types.ModuleType("webview")
    webview.screens = [FakeScreen()]
    webview.settings = {"ALLOW_DOWNLOADS": False}

    def create_window(title, _url, **options):
        assert webview.settings["ALLOW_DOWNLOADS"] is True
        titles_seen.append(title)
        options_seen.append(options)
        return FakeWindow()

    def start(**kwargs):
        start_args.append(kwargs)

    webview.create_window = create_window
    webview.start = start
    monkeypatch.setitem(sys.modules, "webview", webview)
    static = tmp_path / "static"
    static.mkdir()
    (static / "favicon.ico").write_bytes(b"icon")
    monkeypatch.setattr(main.paths, "static_dir", lambda: static)
    monkeypatch.setattr(main.paths, "window_placement_path",
                        lambda: tmp_path / "local" / "window-placement.json")

    assert main.run_native_window("http://127.0.0.1/catalog.html") is True
    assert titles_seen[0] == "NightSeam — Image Catalog"
    assert options_seen[0]["frameless"] is False
    assert options_seen[0]["resizable"] is True
    assert options_seen[0]["min_size"] == (500, 380)
    assert options_seen[0]["easy_drag"] is False
    assert options_seen[0]["zoomable"] is False
    assert options_seen[0]["maximized"] is True
    assert start_args[0]["icon"] == str(static / "favicon.ico")


def test_host_capabilities_distinguish_browser_from_native_frame():
    api = main.JsApi()
    assert api.host_capabilities() == {
        "window_chrome": "browser", "native_folder_picker": False}
    api.bind_window(FakeWindow())
    assert api.host_capabilities() == {
        "window_chrome": "native", "native_folder_picker": True}
    assert not hasattr(api, "window_action")
    assert not hasattr(api, "resize_window")


@pytest.mark.parametrize("scale", [1.25, 1.5, 2.0])
def test_scaled_physical_monitors_keep_logical_density_and_saved_position(scale):
    monitor = Monitor(0, 0, round(1920 * scale), round(1040 * scale), scale)
    bounds, _ = initial_placement(None, [monitor])
    assert bounds == Bounds(round(240 * scale), round(60 * scale),
                            round(1440 * scale), round(920 * scale))
    record = {"version": 2, "normal": {"x": round(40 * scale),
              "y": round(50 * scale), "width": round(1200 * scale),
              "height": round(800 * scale)}, "maximized": False, "scale": scale}
    restored, maximized = initial_placement(record, [monitor])
    assert restored == Bounds(*(record["normal"][key] for key in ("x", "y", "width", "height")))
    assert maximized is False


@pytest.mark.parametrize("scale", [1.25, 1.5, 2.0])
def test_legacy_scaled_coordinates_convert_once_on_negative_mixed_monitor(scale):
    screens = [Monitor(0, 0, 1920, 1040, 1.0, True),
               Monitor(-round(1920 * scale), 0, round(1920 * scale),
                       round(1040 * scale), scale)]
    record = {"version": 1, "normal": {"x": -1800, "y": 80,
              "width": 1100, "height": 740}, "maximized": False}
    restored, _ = initial_placement(record, screens)
    assert restored == Bounds(round(-1800 * scale), round(80 * scale),
                               round(1100 * scale), round(740 * scale))


def test_physical_saved_geometry_adapts_size_to_changed_monitor_dpi():
    record = {"version": 2, "normal": {"x": -1800, "y": 80,
              "width": 1100, "height": 740}, "maximized": False, "scale": 1}
    bounds, _ = initial_placement(record, [Monitor(-2880, 0, 2880, 1560, 1.5)])
    assert bounds == Bounds(-1800, 80, 1650, 1110)


def test_global_profile_fixture_redirects_window_placement():
    assert main.paths.window_placement_path().is_relative_to(
        Path(__file__).resolve().parents[1] / "logs")


def test_debounce_defers_geometry_read_and_discards_stale_callbacks(monkeypatch, tmp_path):
    import app.window_placement as module

    workers = []

    class Worker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            workers.append(self)
        def start(self):
            pass

    monkeypatch.setattr(module.threading, "Thread", Worker)
    path = tmp_path / "placement.json"
    manager = WindowPlacement(path, screens=[FakeScreen()])
    window = FakeWindow()
    manager.attach(window)
    reads = []
    monkeypatch.setattr(manager, "_read_window_snapshot", lambda: (
        reads.append(True) or (Bounds(90, 100, 1200, 800), "Normal")))
    window.events.moved.fire()
    first_generation = manager._generation
    for _ in range(250):
        window.events.resized.fire()
    assert not reads
    assert len(workers) == 1
    manager._flush_generation(first_generation)
    assert not reads and not path.exists()
    last_generation = manager._generation
    manager._flush_generation(last_generation)
    assert len(reads) == 1
    assert json.loads(path.read_text())["normal"]["x"] == 90
    window.events.closing.fire()
    old_reads = len(reads)
    manager._flush_generation(last_generation)
    window.events.moved.fire()
    assert len(reads) == old_reads and len(workers) == 1


def test_native_snapshot_uses_one_rectangle_and_native_state(tmp_path):
    manager = WindowPlacement(tmp_path / "placement.json", screens=[FakeScreen()])
    window = FakeWindow()
    native = types.SimpleNamespace(InvokeRequired=False, IsDisposed=False,
        WindowState="Maximized", Bounds=types.SimpleNamespace(X=0, Y=0, Width=1920, Height=1040),
        RestoreBounds=types.SimpleNamespace(X=100, Y=120, Width=1250, Height=850))
    window.native = native
    manager.attach(window)
    manager.flush()
    saved = json.loads(manager.path.read_text())
    assert saved["normal"] == {"x": 100, "y": 120, "width": 1250, "height": 850}
    assert saved["maximized"] is True and saved["version"] == 2
    native.WindowState = "Minimized"
    native.RestoreBounds.Width = 0
    manager.flush()
    assert json.loads(manager.path.read_text())["normal"] == saved["normal"]


@pytest.mark.parametrize("failure", [ValueError("invalid path"), RuntimeError("screens unavailable")])
def test_optional_placement_failure_keeps_native_frame_and_downloads(monkeypatch, failure):
    webview = types.ModuleType("webview")
    webview.settings = {"ALLOW_DOWNLOADS": False}
    created = []
    webview.create_window = lambda title, url, **options: (
        created.append(options) or FakeWindow())
    webview.start = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(main.paths, "window_placement_path", lambda: (_ for _ in ()).throw(failure))
    import app.window_placement as module
    monkeypatch.setattr(module, "windows_monitors", lambda: [FakeScreen()])
    assert main.run_native_window("http://127.0.0.1/catalog.html")
    assert webview.settings["ALLOW_DOWNLOADS"] is True
    assert created[0]["frameless"] is False and created[0]["maximized"] is True


@pytest.mark.parametrize("scale", [1.25, 1.5, 2.0])
def test_before_show_replaces_backend_scaled_coordinates_with_physical_bounds(monkeypatch, tmp_path, scale):
    drawing = types.ModuleType("System.Drawing")
    drawing.Rectangle = lambda *values: values
    drawing.Size = lambda *values: values
    forms = types.ModuleType("System.Windows.Forms")
    forms.FormWindowState = types.SimpleNamespace(Normal="Normal", Maximized="Maximized")
    monkeypatch.setitem(sys.modules, "System.Drawing", drawing)
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", forms)
    manager = WindowPlacement(tmp_path / "placement.json", screens=[
        Monitor(-round(1920 * scale), 0, round(1920 * scale), round(1040 * scale), scale)])
    manager.maximized = False
    bounds = manager.normal_bounds
    # The backend creates/scales the form before before_show. Its generated
    # position must be replaced rather than multiplying a global origin again.
    form = types.SimpleNamespace(WindowState="Maximized",
        Bounds=tuple(round(value * scale) for value in (bounds.x, bounds.y, bounds.width, bounds.height)))
    manager._window = types.SimpleNamespace(native=form)
    manager._before_show()
    assert form.Bounds == (bounds.x, bounds.y, bounds.width, bounds.height)
    assert form.MinimumSize == (round(500 * scale), round(380 * scale))
    assert form.WindowState == "Normal"


def test_native_snapshot_marshals_the_entire_read_to_gui_thread(monkeypatch, tmp_path):
    system = types.ModuleType("System")
    system.Action = lambda callback: callback
    monkeypatch.setitem(sys.modules, "System", system)
    manager = WindowPlacement(tmp_path / "placement.json", screens=[FakeScreen()])

    class Form:
        InvokeRequired = True
        IsDisposed = False
        in_gui = False
        invokes = 0
        def BeginInvoke(self, callback):
            self.invokes += 1
            self.in_gui = True
            try:
                callback()
            finally:
                self.in_gui = False
        @property
        def WindowState(self):
            assert self.in_gui
            return "Normal"
        @property
        def Bounds(self):
            assert self.in_gui
            return types.SimpleNamespace(X=-400, Y=80, Width=1200, Height=800)

    form = Form()
    manager._window = types.SimpleNamespace(native=form)
    # Avoid property evaluation merely to discover the backend.
    snapshot = manager._read_window_snapshot()
    assert snapshot == (Bounds(-400, 80, 1200, 800), "Normal")
    assert form.invokes == 1


def test_debounce_worker_flushes_latest_burst_and_stops_on_close(monkeypatch, tmp_path):
    import threading

    manager = WindowPlacement(tmp_path / "placement.json", screens=[FakeScreen()])
    manager.maximized = False
    window = FakeWindow()
    manager.attach(window)
    manager._debounce_delay = 0.02
    done = threading.Event()
    original = manager._flush_generation

    def flush(generation):
        original(generation)
        if manager.path.exists():
            done.set()

    monkeypatch.setattr(manager, "_flush_generation", flush)
    window.events.moved.fire()
    worker = manager._worker_thread
    for x in range(100):
        window.x = x
        window.events.moved.fire()
        assert manager._worker_thread is worker
    assert done.wait(2)
    assert json.loads(manager.path.read_text())["normal"]["x"] == 99
    window.events.closing.fire()
    worker.join(1)
    assert not worker.is_alive()


def test_gui_snapshot_worker_can_stop_when_gui_dispatch_is_pending(monkeypatch, tmp_path):
    import threading

    system = types.ModuleType("System")
    system.Action = lambda callback: callback
    monkeypatch.setitem(sys.modules, "System", system)
    manager = WindowPlacement(tmp_path / "placement.json", screens=[FakeScreen()])
    manager._debounce_delay = 0.01
    queued = threading.Event()
    callbacks = []

    class Form:
        InvokeRequired = True
        IsDisposed = False
        WindowState = "Normal"
        Bounds = types.SimpleNamespace(X=60, Y=80, Width=1000, Height=700)
        def BeginInvoke(self, callback):
            callbacks.append(callback)
            queued.set()

    form = Form()
    window = FakeWindow()
    window.native = form
    manager.attach(window)
    window.events.moved.fire()
    assert queued.wait(2)
    worker = manager._worker_thread
    # Closing runs on the GUI thread: it reads directly, wakes the waiter,
    # and must never join a worker that needs that same GUI thread.
    form.InvokeRequired = False
    window.events.closing.fire()
    worker.join(1)
    assert not worker.is_alive()
    before = manager.path.read_bytes()
    callbacks[0]()
    assert manager.path.read_bytes() == before
