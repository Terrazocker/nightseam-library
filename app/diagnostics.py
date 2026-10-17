"""Allowlisted support information; never export raw logs or library contents."""
from collections import deque
from datetime import datetime, timezone
from importlib import metadata
import json
import logging
import platform
import re
import sqlite3
import struct
import sys
import threading

from . import compression
from .paths import resource_dir
from .version import app_version

PACKAGES = ('fastapi', 'uvicorn', 'pywebview', 'mini-racer', 'astropy', 'numpy', 'Pillow')
_CATEGORIES = {
    'nightseam': ('desktop_runtime', 'The desktop runtime reported a problem.'),
    'uvicorn.error': ('server_runtime', 'The local server reported a problem.'),
    'app.server': ('catalog_operation', 'A catalog operation reported a problem.'),
    'app.compression': ('fits_operation', 'A FITS operation reported a problem.'),
    'app.database': ('database_operation', 'A database operation reported a problem.'),
    'app.archive_health': ('archive_health', 'An archive health operation reported a problem.'),
}
_EXCEPTIONS = (PermissionError, FileNotFoundError, sqlite3.IntegrityError,
               sqlite3.DatabaseError, ValueError, RuntimeError, OSError)


class ErrorCollector(logging.Handler):
    """Retain bounded primitive summaries, never LogRecords/tracebacks/messages."""
    def __init__(self, limit=25):
        super().__init__(logging.WARNING)
        self._events = deque(maxlen=limit)
        self._events_lock = threading.Lock()

    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        code, summary = _CATEGORIES.get(record.name,
            ('application', 'An application component reported a problem.'))
        try:
            stamp = datetime.fromtimestamp(record.created, timezone.utc)
        except (ValueError, OverflowError, OSError, TypeError):
            stamp = datetime.now(timezone.utc)
        kind = None
        if record.exc_info and isinstance(record.exc_info[1], BaseException):
            kind = next((allowed.__name__ for allowed in _EXCEPTIONS
                         if isinstance(record.exc_info[1], allowed)), 'other')
        event = {'time_utc': stamp.isoformat(timespec='seconds'), 'code': code,
            'severity': 'critical' if record.levelno >= logging.CRITICAL else
                        'error' if record.levelno >= logging.ERROR else 'warning',
            'summary': summary, 'exception_kind': kind}
        with self._events_lock:
            self._events.append(event)

    def snapshot(self):
        with self._events_lock:
            return [dict(event) for event in self._events]


_collector = ErrorCollector()
_install_lock = threading.Lock()


def install_error_collector(logger=None):
    logger = logger or logging.getLogger()
    with _install_lock:
        existing = next((handler for handler in logger.handlers if isinstance(handler, ErrorCollector)), None)
        if existing is not None:
            return existing
        logger.addHandler(_collector)
        return _collector


def _version(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,79}', value) else 'unavailable'


def fits_tools_report():
    try:
        bundle, suffix = compression.tool_bundle()
    except ValueError:
        return {'bundle': None, 'integrity': 'unsupported'}
    root = resource_dir() / 'vendor/fits-tools' / bundle
    result = {'bundle': bundle, 'integrity': 'unavailable'}
    try:
        if not all((root / name).is_file() for name in
                   ('provenance.json', f'fpack{suffix}', f'funpack{suffix}')):
            result['integrity'] = 'missing'
            return result
        compression.tools()  # Validate both utilities; do not execute them.
        result['integrity'] = 'verified'
        provenance = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))
        result['cfitsio_version'] = _version(provenance.get('cfitsio'))
        result['zlib_version'] = _version(provenance.get('zlib'))
    except OSError:
        result['integrity'] = 'unavailable'
    except Exception:
        # Messages/provenance may contain install/build paths. Export only a code.
        result['integrity'] = 'verification_failed'
    return result


def report():
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = _version(metadata.version(name))
        except Exception:
            packages[name] = 'unavailable'
    try:
        version = _version(app_version())
    except Exception:
        version = 'unavailable'
    system = platform.system()
    machine = platform.machine().lower()
    release = re.match(r'[0-9]+(?:\.[0-9]+){0,3}', platform.release())
    build = re.match(r'[0-9]+(?:\.[0-9]+){0,3}', platform.version())
    return {'schema_version': 1,
        'generated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'application': {'version': version, 'frozen': bool(getattr(sys, 'frozen', False))},
        'runtime': {'python': platform.python_version(), 'pointer_bits': struct.calcsize('P') * 8,
            'system': system if system in ('Windows', 'Linux', 'Darwin') else 'other',
            'release': release.group(0) if release else 'unavailable',
            'build': build.group(0) if build else 'unavailable',
            'architecture': machine if machine in ('amd64', 'x86_64', 'arm64', 'aarch64', 'i386', 'i686') else 'other',
            'packages': packages},
        'fits_tools': fits_tools_report(),
        'recent_events': _collector.snapshot(),
        'event_scope': 'Current process only; bounded categories, not full logs.'}
