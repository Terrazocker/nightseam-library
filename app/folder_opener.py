"""Desktop helpers: show an archive folder, or let the user pick one on Linux."""
import errno
import logging
import os
import shutil
import subprocess
import sys
import threading


def open_folder(folder):
    startfile = getattr(os, 'startfile', None)
    if startfile is not None:
        startfile(folder)
        return
    if sys.platform != 'linux':
        raise OSError(errno.ENOSYS, 'Opening folders is unavailable on this platform')
    open_desktop_target(str(folder))


def _system_environment():
    environment = dict(os.environ)
    if getattr(sys, 'frozen', False):
        # System desktop programs must not load the onefile bundle's libraries.
        original = environment.get('LD_LIBRARY_PATH_ORIG')
        if original is None:
            environment.pop('LD_LIBRARY_PATH', None)
        else:
            environment['LD_LIBRARY_PATH'] = original
    return environment


def open_desktop_target(target):
    """Launch Linux desktop URLs/paths without the frozen app's libraries."""
    executable = shutil.which('xdg-open')
    if executable is None:
        raise OSError(errno.ENOENT, 'xdg-open is unavailable; install xdg-utils to open links or archive folders')
    # No shell: spaces/metacharacters in archive paths remain literal arguments.
    process = subprocess.Popen([executable, target], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        env=_system_environment())
    def reap():
        code = process.wait()
        if code:
            logging.getLogger('nightseam').warning('The desktop opener exited with status %s', code)
    threading.Thread(target=reap, name='desktop-opener', daemon=True).start()


def _dialog_tool():
    for name in ('zenity', 'kdialog'):
        executable = shutil.which(name)
        if executable is not None:
            return name, executable
    return None


def folder_dialog_available():
    """Whether this Linux session can show a folder dialog for the browser UI."""
    return (sys.platform == 'linux' and bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
            and _dialog_tool() is not None)


def pick_folder_dialog(title, start=None):
    """Show the desktop's folder dialog; return the chosen folder, or None if cancelled.

    The Linux browser build has no native window bridge, and a browser page can
    only upload a folder's files. Running the dialog here lets the server read
    the folder in place, as the Windows window does. Blocks until it closes.
    """
    tool = _dialog_tool()
    if tool is None:
        raise OSError(errno.ENOENT, 'No folder dialog is available; install zenity or kdialog')
    name, executable = tool
    start = str(start) if start and os.path.isdir(start) else os.path.expanduser('~')
    if name == 'zenity':
        arguments = ['--file-selection', '--directory', '--title', title, '--filename', os.path.join(start, '')]
    else:
        arguments = ['--getexistingdirectory', start, '--title', title]
    completed = subprocess.run([executable, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, env=_system_environment())
    if completed.returncode == 1:
        return None  # cancelled or closed
    chosen = completed.stdout.strip()
    if completed.returncode != 0 or not chosen:
        raise OSError(errno.EIO, f'The folder dialog failed ({name} exited with status {completed.returncode})')
    return chosen
