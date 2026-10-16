"""Pinned ingest indexes exclude other operations until their handles close."""
from concurrent.futures import ThreadPoolExecutor
import threading

from app.archive_lock import ArchiveLock


def test_request_lease_allows_its_workers_and_delays_other_operations():
    lock, batch = ArchiveLock(), object()
    with lock:
        lock.reserve(batch)
        with lock:
            assert lock._is_owned()
    assert not lock.acquire(blocking=False)
    assert not lock.acquire(timeout=.01)
    started, finished = threading.Event(), threading.Event()
    def mutation():
        started.set()
        with lock:
            finished.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(mutation)
        try:
            assert started.wait(2)
            with lock.for_batch(batch):
                assert lock._is_owned() and not finished.is_set()
        finally:
            with lock.for_batch(batch):
                lock.unreserve(batch)
        future.result(timeout=2)
    assert finished.is_set()
    assert lock.acquire(blocking=False)
    lock.release()
