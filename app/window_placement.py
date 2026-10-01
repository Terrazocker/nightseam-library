"""Safe, machine-local placement persistence for the packaged catalog window."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MIN_WINDOW_SIZE = (500, 380)
DEFAULT_WINDOW_SIZE = (1440, 920)
CAPTION_WIDTH = 120
CAPTION_HEIGHT = 32
_MAX_COORDINATE = 10_000_000
_MAX_DIMENSION = 100_000


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True)
class Monitor:
    """A physical desktop work area, independent of pywebview's DPI wrappers."""

    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0
    primary: bool = False


@contextmanager
def _physical_desktop(user32):
    """Keep Win32 coordinates physical without changing process-wide awareness."""
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous = setter(-4) if setter is not None else None
    try:
        yield
    finally:
        if previous:
            setter(previous)


def windows_monitors() -> list[Monitor]:
    """Query physical work areas before the WinForms backend sets DPI awareness."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    shcore = ctypes.WinDLL("shcore", use_last_error=True)

    class Info(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.POINTER(wintypes.RECT),
                                      wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Info)]
    shcore.GetScaleFactorForMonitor.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_int)]
    monitors = []

    def visit(handle, _dc, _rect, _data):
        info = Info()
        info.size = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return True
        factor = ctypes.c_int(100)
        if shcore.GetScaleFactorForMonitor(handle, ctypes.byref(factor)) != 0:
            factor.value = 100
        work = info.work
        monitors.append(Monitor(work.left, work.top, work.right - work.left,
                                work.bottom - work.top, factor.value / 100,
                                bool(info.flags & 1)))
        return True

    with _physical_desktop(user32):
        if not user32.EnumDisplayMonitors(None, None, callback_type(visit), 0):
            raise OSError("could not enumerate Windows monitor work areas")
    return monitors


def _scale_for(work: Bounds, screens) -> float:
    for screen, area in zip(screens, _screen_areas(screens)):
        if area == work:
            value = getattr(screen, "scale", 1.0)
            if isinstance(value, (int, float)) and 0.5 <= value <= 8:
                return float(value)
    return 1.0


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_bounds(value) -> Bounds | None:
    if not isinstance(value, dict):
        return None
    raw = (value.get("x"), value.get("y"), value.get("width"),
           value.get("height"))
    if not all(_integer(item) for item in raw):
        return None
    x, y, width, height = raw
    if (abs(x) > _MAX_COORDINATE or abs(y) > _MAX_COORDINATE or
            not 1 <= width <= _MAX_DIMENSION or
            not 1 <= height <= _MAX_DIMENSION):
        return None
    return Bounds(x, y, width, height)


def _screen_areas(screens: Iterable[object]) -> list[Bounds]:
    areas: list[Bounds] = []
    for screen in screens or ():
        frame = getattr(screen, "frame", None)
        if frame is None:
            x, y = getattr(screen, "x", None), getattr(screen, "y", None)
            width = getattr(screen, "width", None)
            height = getattr(screen, "height", None)
        else:
            x = getattr(frame, "X", getattr(frame, "x", None))
            y = getattr(frame, "Y", getattr(frame, "y", None))
            width = getattr(frame, "Width", getattr(frame, "width", None))
            height = getattr(frame, "Height", getattr(frame, "height", None))
        if not all(_integer(value) for value in (x, y, width, height)):
            continue
        if width > 0 and height > 0:
            areas.append(Bounds(x, y, width, height))
    return areas


def _primary_work_area(screens: Iterable[object], areas: list[Bounds]) -> Bounds:
    """Prefer the work area of the display containing the desktop origin."""
    for screen in screens or ():
        if getattr(screen, "primary", False):
            return Bounds(screen.x, screen.y, screen.width, screen.height)
        x, y = getattr(screen, "x", None), getattr(screen, "y", None)
        width, height = getattr(screen, "width", None), getattr(screen, "height", None)
        if not all(_integer(value) for value in (x, y, width, height)):
            continue
        if x <= 0 < x + width and y <= 0 < y + height:
            frame = getattr(screen, "frame", None)
            if frame is None:
                return Bounds(x, y, width, height)
            work = Bounds(getattr(frame, "X", getattr(frame, "x", x)),
                          getattr(frame, "Y", getattr(frame, "y", y)),
                          getattr(frame, "Width", getattr(frame, "width", width)),
                          getattr(frame, "Height", getattr(frame, "height", height)))
            return work
    return areas[0]


def _intersection_area(a: Bounds, b: Bounds) -> int:
    width = max(0, min(a.right, b.right) - max(a.x, b.x))
    height = max(0, min(a.bottom, b.bottom) - max(a.y, b.y))
    return width * height


def _caption_reachable(bounds: Bounds, work: Bounds, scale=1.0) -> bool:
    caption = Bounds(bounds.x, bounds.y, bounds.width,
                     min(bounds.height, round(CAPTION_HEIGHT * scale)))
    width = max(0, min(caption.right, work.right) - max(caption.x, work.x))
    height = max(0, min(caption.bottom, work.bottom) - max(caption.y, work.y))
    return (width >= min(round(CAPTION_WIDTH * scale), bounds.width) and
            height >= min(round(24 * scale), caption.height))


def _clamp_to_work_area(bounds: Bounds, work: Bounds, scale=1.0) -> Bounds:
    min_width, min_height = (round(value * scale) for value in MIN_WINDOW_SIZE)
    width = max(min_width, min(bounds.width, max(min_width, work.width)))
    height = max(min_height, min(bounds.height, max(min_height, work.height)))
    visible_width = min(round(CAPTION_WIDTH * scale), width)
    visible_height = min(round(CAPTION_HEIGHT * scale), height)
    if width <= work.width:
        low_x, high_x = work.x, work.right - width
    else:
        low_x, high_x = work.x - width + visible_width, work.right - visible_width
    if height <= work.height:
        low_y, high_y = work.y, work.bottom - height
    else:
        low_y, high_y = work.y, work.bottom - visible_height
    x = min(max(bounds.x, low_x), high_x)
    y = min(max(bounds.y, low_y), high_y)
    return Bounds(x, y, width, height)


def _centered_default(work: Bounds, scale=1.0) -> Bounds:
    width = max(round(MIN_WINDOW_SIZE[0] * scale),
                min(round(DEFAULT_WINDOW_SIZE[0] * scale), work.width))
    height = max(round(MIN_WINDOW_SIZE[1] * scale),
                 min(round(DEFAULT_WINDOW_SIZE[1] * scale), work.height))
    return Bounds(work.x + (work.width - width) // 2,
                  work.y + (work.height - height) // 2,
                  width, height)


def initial_placement(record, screens: Iterable[object]) -> tuple[Bounds, bool]:
    """Validate saved geometry and recover it onto a currently usable monitor.

    Saved negative coordinates are valid for left/above monitors. A missing
    display is detected by the saved caption no longer intersecting any work
    area; recovery then centers the existing minimum-size window on the first
    current screen. Snapped rectangles are intentionally kept as ordinary
    normal bounds, so Windows recalculates Snap state on the next launch.
    """
    screens = list(screens or ())
    areas = _screen_areas(screens)
    if not areas:
        areas = [Bounds(0, 0, 1920, 1080)]
    first = _primary_work_area(screens, areas)
    default = _centered_default(first, _scale_for(first, screens))
    if (not isinstance(record, dict) or not _integer(record.get("version")) or
            record.get("version") not in (1, 2) or
            not isinstance(record.get("maximized"), bool)):
        return default, True
    saved = _read_bounds(record.get("normal"))
    if saved is None:
        return default, record["maximized"]

    if record["version"] == 1:
        # The old API divided ALL coordinates by the current window DPI,
        # including negative/global monitor origins. Test each current monitor
        # rather than inventing one global DIP desktop on mixed-DPI systems.
        candidates = [(Bounds(*(round(v * _scale_for(area, screens)) for v in
                        (saved.x, saved.y, saved.width, saved.height))), area)
                      for area in areas]
        candidates = [(bounds, area) for bounds, area in candidates
                      if _caption_reachable(bounds, area, _scale_for(area, screens))]
        if not candidates:
            return default, record["maximized"]
        saved, work = max(candidates, key=lambda pair: _intersection_area(*pair))
        return _clamp_to_work_area(saved, work, _scale_for(work, screens)), record["maximized"]

    reachable = [area for area in areas
                 if _caption_reachable(saved, area, _scale_for(area, screens))]
    if not reachable:
        return default, record["maximized"]
    work = max(reachable, key=lambda area: _intersection_area(saved, area))
    old_scale = record.get("scale", 1.0)
    new_scale = _scale_for(work, screens)
    if isinstance(old_scale, (int, float)) and not isinstance(old_scale, bool) and 0.5 <= old_scale <= 8:
        saved = Bounds(saved.x, saved.y, round(saved.width * new_scale / old_scale),
                       round(saved.height * new_scale / old_scale))
    return _clamp_to_work_area(saved, work, new_scale), record["maximized"]


class WindowPlacement:
    """Debounced window geometry writer with explicit maximize/minimize policy."""

    def __init__(self, path: Path | str, *, screens: Iterable[object]):
        self.path = Path(path)
        self.screens = list(screens or ())
        self.normal_bounds, self.maximized = initial_placement(
            self._read_record(), self.screens)
        self.minimized = False
        self._window = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._worker_thread: threading.Thread | None = None
        self._deadline: float | None = None
        self._debounce_delay = 0.45
        self._generation = 0
        self._closed = False

    def _read_record(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return value

    def attach(self, window) -> None:
        self._window = window
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored
        window.events.minimized += self._on_minimized
        window.events.resized += self._schedule
        window.events.moved += self._schedule
        window.events.closing += self._on_closing
        before_show = getattr(window.events, "before_show", None)
        if before_show is not None:
            before_show += self._before_show

    def _before_show(self) -> None:
        """WinForms exists now: bypass its global-coordinate DPI multiplication."""
        form = getattr(self._window, "native", None)
        if form is None or not hasattr(form, "WindowState"):
            return
        try:
            from System.Drawing import Rectangle, Size
            from System.Windows.Forms import FormWindowState

            bounds = self.normal_bounds
            maximized = self.maximized
            areas = _screen_areas(self.screens)
            work = max(areas, key=lambda area: _intersection_area(bounds, area))
            scale = _scale_for(work, self.screens)
            form.WindowState = FormWindowState.Normal
            form.Bounds = Rectangle(bounds.x, bounds.y, bounds.width, bounds.height)
            form.MinimumSize = Size(*(round(value * scale) for value in MIN_WINDOW_SIZE))
            if maximized:
                form.WindowState = FormWindowState.Maximized
        except Exception:
            # Placement is optional; a backend/interop failure must never replace
            # a functioning native frame with a browser fallback.
            logging.getLogger("nightseam").warning(
                "Could not apply local window placement", exc_info=True)

    def _read_window_snapshot(self):
        window = self._window
        if window is None:
            return None
        form = getattr(window, "native", None)
        if form is not None and hasattr(form, "InvokeRequired"):
            result = []

            def read():
                if form.IsDisposed:
                    return
                state = str(form.WindowState)
                rectangle = form.Bounds if state == "Normal" else form.RestoreBounds
                result.append((Bounds(int(rectangle.X), int(rectangle.Y),
                                      int(rectangle.Width), int(rectangle.Height)),
                               state))

            # Read ONE rectangle and its state on the GUI thread. An asynchronous
            # dispatch lets the worker stop when closing while the GUI thread is
            # busy; closing itself reads directly without waiting for the worker.
            if form.InvokeRequired:
                from System import Action
                done = threading.Event()
                failures = []

                def dispatched_read():
                    try:
                        read()
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        done.set()

                form.BeginInvoke(Action(dispatched_read))
                while not done.wait(0.05):
                    if self._closed:
                        return None
                if failures:
                    raise failures[0]
            else:
                read()
            return result[0] if result else None
        # Non-WinForms hosts (and small fake test windows) retain their API units.
        bounds = self.normal_bounds if self.maximized or self.minimized else Bounds(
            int(window.x), int(window.y), int(window.width), int(window.height))
        return (bounds,
                "Minimized" if self.minimized else "Maximized" if self.maximized else "Normal")

    def _schedule(self, *_args) -> None:
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            self._deadline = time.monotonic() + self._debounce_delay
            if self._worker_thread is None:
                self._worker_thread = threading.Thread(
                    target=self._debounce_worker, name="nightseam-window-placement",
                    daemon=True)
                self._worker_thread.start()
            self._condition.notify_all()

    def _debounce_worker(self) -> None:
        while True:
            with self._condition:
                while self._deadline is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                remaining = self._deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                generation = self._generation
                self._deadline = None
            self._flush_generation(generation)

    def _on_maximized(self, *_args) -> None:
        with self._lock:
            self.maximized = True
            self.minimized = False
        self._schedule()

    def _on_minimized(self, *_args) -> None:
        with self._lock:
            self.minimized = True

    def _on_restored(self, *_args) -> None:
        with self._lock:
            self.maximized = False
            self.minimized = False
        self._schedule()

    def _on_closing(self, *_args) -> None:
        with self._condition:
            self._closed = True
            self._deadline = None
            self._condition.notify_all()
        self.flush()

    def _snapshot_locked(self) -> dict[str, object]:
        return {
            "version": 2,
            "normal": {
                "x": self.normal_bounds.x,
                "y": self.normal_bounds.y,
                "width": self.normal_bounds.width,
                "height": self.normal_bounds.height,
            },
            "maximized": bool(self.maximized),
            "scale": self._normal_scale(),
        }

    def _normal_scale(self) -> float:
        areas = _screen_areas(self.screens)
        if not areas:
            return 1.0
        work = max(areas, key=lambda area: _intersection_area(self.normal_bounds, area))
        return _scale_for(work, self.screens)

    def flush(self, *_args) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._flush_generation(generation)

    def _flush_generation(self, generation) -> None:
        with self._lock:
            if generation != self._generation:
                return
        try:
            current = self._read_window_snapshot()
        except Exception:
            logging.getLogger("nightseam").warning(
                "Could not read local window placement", exc_info=True)
            current = None
        with self._lock:
            if generation != self._generation:
                return
            self._deadline = None
            if current is not None:
                bounds, state = current
                if state != "Minimized":
                    self.maximized = state == "Maximized"
                    if bounds.width > 0 and bounds.height > 0:
                        self.normal_bounds = bounds
            snapshot = self._snapshot_locked()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self.path.with_name(f".{self.path.name}.tmp")
                temp_path.write_text(json.dumps(snapshot, separators=(",", ":")),
                                     encoding="utf-8")
                os.replace(temp_path, self.path)
            except OSError:
                logging.getLogger("nightseam").warning(
                    "Could not save local window placement", exc_info=True)
                try:
                    temp_path.unlink(missing_ok=True)
                except (OSError, UnboundLocalError):
                    pass
