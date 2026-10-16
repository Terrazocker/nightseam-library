"""Exclusive archive operations, with a request lease for pinned ingest indexes."""
from contextlib import contextmanager
import threading
import time


class ArchiveLock:
    def __init__(self):
        self._lock = threading.RLock()
        self._condition = threading.Condition()
        self._local = threading.local()
        self._lease = None

    def _allowed(self):
        return self._lease is None or getattr(self._local, 'batch', None) is self._lease

    def acquire(self, blocking=True, timeout=-1):
        if self._lock._is_owned():
            return self._lock.acquire(blocking, timeout)
        deadline = None if timeout < 0 else time.monotonic() + timeout
        while True:
            with self._condition:
                while not self._allowed():
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if not blocking or (remaining is not None and remaining <= 0):
                        return False
                    self._condition.wait(remaining)
            remaining = -1 if deadline is None else max(0, deadline - time.monotonic())
            if not self._lock.acquire(blocking, remaining if blocking else -1):
                return False
            with self._condition:
                if self._allowed():
                    return True
            self._lock.release()

    def release(self):
        self._lock.release()

    def _is_owned(self):
        return self._lock._is_owned()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()

    def reserve(self, batch):
        assert self._is_owned()
        with self._condition:
            if self._lease is not None:
                raise RuntimeError('An archive request already owns the pinned index.')
            self._lease = batch

    def unreserve(self, batch):
        assert self._is_owned()
        with self._condition:
            if self._lease is batch:
                self._lease = None
                self._condition.notify_all()

    @contextmanager
    def for_batch(self, batch):
        previous = getattr(self._local, 'batch', None)
        self._local.batch = batch
        try:
            with self:
                yield
        finally:
            self._local.batch = previous
