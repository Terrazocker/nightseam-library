"""NightSeam entry point.

Starts the local web server and opens the UI in the default browser.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from app import paths
from app.database import Database
from app.server import create_app
from app.version import app_version


def setup_logging(console: bool):
    from logging.handlers import RotatingFileHandler
    handlers = [RotatingFileHandler(paths.logs_dir() / "nightseam.log",
                                    maxBytes=1_000_000, backupCount=2,
                                    encoding="utf-8")]
    if console and sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)
    return logging.getLogger("nightseam")


def ensure_stdio():
    """Windowed (--noconsole) Windows builds have sys.stdout/stderr = None,
    which crashes any logging code that touches them."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


# ------------------------------------------------------------------ instance handoff

def _instance_file() -> Path:
    return paths.data_dir() / "instance.json"


def existing_instance_url() -> str | None:
    """URL of a live NightSeam instance (recorded in data/instance.json),
    or None. Stale entries are removed."""
    f = _instance_file()
    try:
        info = json.loads(f.read_text())
        port = int(info["port"])
        probe = f"http://127.0.0.1:{port}/api/state"
        import urllib.request
        urllib.request.urlopen(probe, timeout=2.0)
        return f"http://127.0.0.1:{port}"
    except Exception:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def write_instance_file(port: int) -> None:
    try:
        _instance_file().write_text(
            json.dumps({"port": port, "pid": os.getpid()}))
    except OSError:
        pass


def clear_instance_file() -> None:
    try:
        _instance_file().unlink(missing_ok=True)
    except OSError:
        pass


def port_in_use(host: str, port: int) -> bool:
    """True when something is already listening on host:port.

    A bind() probe cannot answer this on Windows: with SO_REUSEADDR (which
    uvicorn also sets) a second bind succeeds even while another process is
    listening. An actual connect() is unambiguous."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.35)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def free_port(preferred: int, host: str) -> int:
    def usable(p):
        if port_in_use(host, p):
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # deliberately NO SO_REUSEADDR: binding must fail when the port is
            # taken, otherwise the probe reports a busy port as free
            try:
                s.bind((host, p))
                return True
            except OSError:
                return False

    if usable(preferred):
        return preferred
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class NoSignalServer(__import__("uvicorn").Server):
    """uvicorn Server that does not touch signal handlers (we run in a thread)."""

    def install_signal_handlers(self) -> None:  # noqa: D102
        pass


class JsApi:
    """Native helpers exposed to the UI as window.pywebview.api (pywebview
    window only; browser fallback has no bridge)."""

    def __init__(self):
        self._window = None

    def bind_window(self, window) -> None:
        self._window = window

    def host_capabilities(self) -> dict[str, object]:
        """Describe host-owned chrome separately from page controls.

        A missing bridge is the browser fallback. When this bridge is present,
        the standard OS frame owns move/resize/caption behavior; the catalog
        page may use the native folder picker but must not draw window chrome.
        """
        return {
            "window_chrome": "native" if self._window is not None else "browser",
            "native_folder_picker": self._window is not None,
        }

    def pick_archive_dir(self, current: str = "") -> str | None:
        """Open a native folder-picker dialog for the image archive path."""
        import webview
        try:
            win = webview.windows[0]
            start = current if current and os.path.isdir(current) \
                else str(Path.home())
            picked = win.create_file_dialog(webview.FOLDER_DIALOG,
                                            directory=start)
        except Exception:  # noqa: BLE001
            return None
        if picked:
            return str(picked[0])
        return None


def run_native_window(url: str) -> bool:
    """Open a real desktop window via pywebview (Edge WebView2).
    Blocks until the window is closed. Returns False if unavailable."""
    log = logging.getLogger("nightseam")
    try:
        import webview  # pywebview
    except Exception as exc:  # noqa: BLE001
        log.info("pywebview not available (%s) - using browser fallback", exc)
        return False
    try:
        # WebView2 cancels every attachment download by default. Enabling the
        # built-in handler opens Windows Save As for catalog CSV exports
        # without navigating away from the UI.
        webview.settings["ALLOW_DOWNLOADS"] = True
        api = JsApi()
        options = {
            "width": 1440,
            "height": 920,
            "min_size": (500, 380),
            "background_color": "#0b0e14",
            "js_api": api,
            "maximized": True, "frameless": False, "resizable": True,
            "easy_drag": False, "zoomable": False,
        }
        app_icon_path = paths.static_dir() / "favicon.ico"
        app_icon = str(app_icon_path) if app_icon_path.is_file() else None
        placement = None
        try:
            from app.window_placement import WindowPlacement, windows_monitors

            # pywebview.screens is queried before WinForms enables DPI
            # awareness and cannot define a consistent mixed-DPI desktop.
            screens = (windows_monitors() if os.name == "nt" else
                       list(getattr(webview, "screens", ()) or ()))
            placement = WindowPlacement(paths.window_placement_path(),
                                        screens=screens)
            bounds, maximized = placement.normal_bounds, placement.maximized
            # Windows geometry is applied to the native form before Show.
            # Feeding physical x/y into pywebview would scale them again.
            if os.name != "nt":
                options.update(x=bounds.x, y=bounds.y, width=bounds.width,
                               height=bounds.height, maximized=maximized)
        except Exception:  # Optional geometry must not disable native UI.
            log.warning("Could not initialize local window placement",
                        exc_info=True)
        window = webview.create_window("NightSeam — Image Catalog", url, **options)
        api.bind_window(window)
        if placement is not None:
            try:
                placement.attach(window)
            except Exception:
                log.warning("Could not attach local window placement",
                            exc_info=True)
        # pywebview's WinForms backend consumes its start icon option too,
        # although the public parameter documentation only lists GTK/Qt.
        # static/ is bundled in source and frozen launches, so this is the same
        # project icon in both; build.ps1 also embeds it in the executable.
        webview.start(icon=app_icon)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("native window failed (%s) - falling back to app window", exc)
        return False


def launch_app_window(url: str) -> bool:
    """Fallback: Edge/Chrome launched in --app mode (own window, no tabs/URL bar)."""
    import os
    import subprocess

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad = os.environ.get("LocalAppData", "")
    candidates = [
        rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Google\Chrome\Application\chrome.exe",
        rf"{pf86}\Google\Chrome\Application\chrome.exe",
    ]
    if lad:
        candidates += [rf"{lad}\Microsoft\Edge\Application\msedge.exe",
                       rf"{lad}\Google\Chrome\Application\chrome.exe"]
    for exe in candidates:
        if exe and os.path.exists(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,920"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                logging.getLogger("nightseam").info("opened app window via %s",
                                                     os.path.basename(exe))
                return True
            except OSError:
                continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="NightSeam - image archive and DSO planning tool")
    ap.add_argument("--version", action="version",
                    version=f"NightSeam {app_version()}")
    ap.add_argument("--port", type=int, default=8407)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--browser", action="store_true",
                    help="open in a regular browser tab instead of an app window")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open any window (headless)")
    ap.add_argument("--console", action="store_true",
                    help="also log to console")
    ap.add_argument("--idle-exit-hours", type=float, default=12.0,
                    help="exit after this many hours without requests (0 = never)")
    args = ap.parse_args()

    ensure_stdio()
    log = setup_logging(args.console)

    # hand off to an already-running instance instead of shadowing it: two
    # instances would share one SQLite file and one archive folder, which can
    # truncate an in-flight ingest and tear the rolling .backup copy
    old_url = existing_instance_url()
    if old_url:
        launch_url = f"{old_url.rstrip('/')}/catalog.html"
        log.info("NightSeam already running - opening %s.", launch_url)
        if args.no_browser:
            return 0                     # headless launch: nothing to open
        if args.browser:
            webbrowser.open(launch_url)
        else:
            if not run_native_window(launch_url):
                if not launch_app_window(launch_url):
                    webbrowser.open(launch_url)
        return 0

    db = Database(paths.data_dir() / "nightseam.db")
    app = create_app(db)

    port = free_port(args.port, args.host)
    url = f"http://{args.host}:{port}/"

    import uvicorn
    config = uvicorn.Config(app, host=args.host, port=port,
                            log_level="warning", access_log=False,
                            lifespan="on", ws="none",
                            log_config=None)
    server = NoSignalServer(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    t0 = time.time()
    while time.time() - t0 < 15:
        try:
            with socket.create_connection((args.host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.15)
    else:
        log.error("Server failed to start on %s", url)
        return 1

    log.info("NightSeam running at %s", url)
    write_instance_file(port)

    idle_secs = args.idle_exit_hours * 3600.0
    ui_mode = "none" if args.no_browser else "browser" if args.browser else "window"

    try:
        if ui_mode == "window":
            if run_native_window(url):
                pass                               # window closed -> shut down
            elif launch_app_window(url):
                wait_for_exit(server, idle_secs)
            else:
                threading.Timer(0.6, lambda: webbrowser.open(url)).start()
                wait_for_exit(server, idle_secs)
        elif ui_mode == "browser":
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
            wait_for_exit(server, idle_secs)
        else:
            wait_for_exit(server, idle_secs)
    finally:
        app.state.archive_health.request_shutdown()
        app.state.compression.cancel_event.set()
        server.should_exit = True
        thread.join(timeout=15)
        if thread.is_alive():
            log.error("Server shutdown did not finish; archive work may still be stopping.")
            return 1
        clear_instance_file()
    return 0


def wait_for_exit(server, idle_secs: float) -> None:
    try:
        while server.should_exit is False:
            time.sleep(1.0)
            if idle_secs > 0 and time.time() - _last_request_ts() > idle_secs:
                logging.getLogger("nightseam").info("Idle timeout reached.")
                break
    except KeyboardInterrupt:
        pass


def _last_request_ts() -> float:
    from app import server as srv
    return srv._state["last_request"]


if __name__ == "__main__":
    raise SystemExit(main())
