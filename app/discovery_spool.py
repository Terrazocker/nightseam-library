"""Disposable disk-backed queues for read-only archive-health discovery."""
from contextlib import closing
from collections import Counter
import os
from pathlib import Path
import sqlite3
import tempfile

from .paths import logs_dir, safe_rel

CACHE_KIB = 1024


class DiscoveryStorageError(RuntimeError):
    pass


class DiscoverySpool:
    """Keep directory fanout and ordered filenames out of Python collections.

    BLOB UTF-8/surrogatepass preserves Python codepoint ordering and exact raw
    filesystem strings, including POSIX surrogateescaped names. Duplicate
    observations are retained. This database is disposable, never recovery or
    archive state; journaling and synchronization are deliberately unnecessary.
    """
    def __init__(self):
        self.workspace = self.connection = None
        self.pending_count = self.untracked_count = 0
        self.indexed_count = 0
        self.baseline_sources = Counter()

    def __enter__(self):
        try:
            self.workspace = tempfile.TemporaryDirectory(
                prefix=f'health-discovery-{os.getpid()}-', dir=logs_dir())
            directory = Path(self.workspace.name)
            (directory / '.owner-pid').write_text(str(os.getpid()), encoding='utf-8')
            self.connection = sqlite3.connect(directory / 'discovery.sqlite')
            for statement in ('PRAGMA journal_mode=OFF', 'PRAGMA synchronous=OFF',
                    f'PRAGMA cache_size=-{CACHE_KIB}', 'PRAGMA cache_spill=ON',
                    'PRAGMA mmap_size=0', 'PRAGMA temp_store=FILE',
                    'CREATE TABLE pending(path BLOB NOT NULL)',
                    'CREATE TABLE untracked(path BLOB NOT NULL, occurrence INTEGER NOT NULL, '
                    'PRIMARY KEY(path,occurrence)) WITHOUT ROWID', 'BEGIN'):
                with closing(self.execute(statement)):
                    pass
        except BaseException as exc:
            self.close()
            if isinstance(exc, (OSError, sqlite3.Error)):
                raise DiscoveryStorageError(f'Archive-health temporary discovery storage failed: {exc}') from exc
            raise
        return self

    def copy_records(self, rows, checkpoint):
        """Freeze complete native SQLite values one row at a time, in ID order."""
        with closing(self.execute('CREATE TABLE known(path BLOB PRIMARY KEY) WITHOUT ROWID')):
            pass
        insert = None
        for row in rows:
            checkpoint()
            record = dict(row)
            if insert is None:
                columns = ','.join('"' + key.replace('"', '""') + '"' +
                                   (' INTEGER PRIMARY KEY' if key == 'id' else '') for key in record)
                with closing(self.execute(f'CREATE TABLE indexed_records({columns})')):
                    pass
                insert = 'INSERT INTO indexed_records VALUES(' + ','.join('?' for _ in record) + ')'
            with closing(self.execute(insert, tuple(record.values()))):
                pass
            clean = safe_rel(record['file'])
            if clean is not None:
                key = os.path.normcase(clean).encode('utf-8', 'surrogatepass')
                with closing(self.execute('INSERT OR IGNORE INTO known(path) VALUES(?)', (key,))):
                    pass
            self.baseline_sources[record.get('baseline_source') or 'none'] += 1
            self.indexed_count += 1
        checkpoint()

    def contains(self, path):
        key = os.path.normcase(path).encode('utf-8', 'surrogatepass')
        with closing(self.execute('SELECT 1 FROM known WHERE path=?', (key,))) as cursor:
            return cursor.fetchone() is not None

    def records(self):
        if not self.indexed_count:
            return
        with closing(self.execute('SELECT * FROM indexed_records ORDER BY id')) as cursor:
            names = [column[0] for column in cursor.description]
            try:
                for row in cursor:
                    yield dict(zip(names, row))
            except sqlite3.Error as exc:
                raise DiscoveryStorageError(f'Archive-health temporary discovery storage failed: {exc}') from exc

    def execute(self, statement, parameters=()):
        try:
            return self.connection.execute(statement, parameters)
        except sqlite3.Error as exc:
            raise DiscoveryStorageError(f'Archive-health temporary discovery storage failed: {exc}') from exc

    def push_directory(self, path):
        with closing(self.execute('INSERT INTO pending(path) VALUES(?)',
                                  (path.encode('utf-8', 'surrogatepass'),))):
            pass
        self.pending_count += 1

    def pop_directory(self):
        with closing(self.execute('SELECT rowid,path FROM pending ORDER BY rowid DESC LIMIT 1')) as cursor:
            row = cursor.fetchone()
        if row is None:
            raise IndexError('No pending discovery directory')
        with closing(self.execute('DELETE FROM pending WHERE rowid=?', (row[0],))):
            pass
        self.pending_count -= 1
        return row[1].decode('utf-8', 'surrogatepass')

    def add_untracked(self, path):
        with closing(self.execute('INSERT INTO untracked(path,occurrence) VALUES(?,?)',
                                  (path.encode('utf-8', 'surrogatepass'), self.untracked_count))):
            pass
        self.untracked_count += 1

    def untracked(self):
        with closing(self.execute('SELECT path FROM untracked ORDER BY path')) as cursor:
            try:
                for row in cursor:
                    yield row[0].decode('utf-8', 'surrogatepass')
            except sqlite3.Error as exc:
                raise DiscoveryStorageError(f'Archive-health temporary discovery storage failed: {exc}') from exc

    def close(self):
        try:
            if self.connection is not None:
                try:
                    self.connection.close()
                except sqlite3.Error as exc:
                    raise DiscoveryStorageError(f'Archive-health temporary discovery storage close failed: {exc}') from exc
                finally:
                    self.connection = None
        finally:
            if self.workspace is not None:
                self.workspace.cleanup()
                self.workspace = None

    def __exit__(self, *exc):
        self.close()
