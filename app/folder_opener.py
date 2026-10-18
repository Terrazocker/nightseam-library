"""Ask the desktop file manager to show an already validated archive folder."""
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


def open_desktop_target(target):
    """Launch Linux desktop URLs/paths without the frozen app's libraries."""
    executable = shutil.which('xdg-open')
    if executable is None:
        raise OSError(errno.ENOENT, 'xdg-open is unavailable; install xdg-utils to open links or archive folders')
    environment = dict(os.environ)
    if getattr(sys, 'frozen', False):
        # System desktop programs must not load the onefile bundle's libraries.
        original = environment.get('LD_LIBRARY_PATH_ORIG')
        if original is None:
            environment.pop('LD_LIBRARY_PATH', None)
        else:
            environment['LD_LIBRARY_PATH'] = original
    # No shell: spaces/metacharacters in archive paths remain literal arguments.
    process = subprocess.Popen([executable, target], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=environment)
    def reap():
        code = process.wait()
        if code:
            logging.getLogger('nightseam').warning('The desktop opener exited with status %s', code)
    threading.Thread(target=reap, name='desktop-opener', daemon=True).start()
