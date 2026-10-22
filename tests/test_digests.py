"""Content SHA-256: Windows CNG must match hashlib exactly, or not be used."""

import hashlib
import os
import random
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import digests


@pytest.fixture()
def fresh(monkeypatch):
    monkeypatch.setattr(digests, "_factory", None)
    monkeypatch.setattr(digests, "IMPLEMENTATION", None)


def test_windows_uses_cng(fresh):
    digests.sha256()
    assert digests.IMPLEMENTATION == ("cng" if os.name == "nt" else "hashlib")


@pytest.mark.parametrize("size", [0, 1, 63, 64, 65, 1 << 20, (1 << 20) + 17, 9 * (1 << 20) + 3])
def test_digest_matches_hashlib_for_every_buffer_type(size):
    data = random.Random(size).randbytes(size)
    expected = hashlib.sha256(data).digest()
    assert digests.sha256(data).digest() == expected
    for kind in (bytes, bytearray, memoryview, lambda value: memoryview(bytearray(value))):
        hasher = digests.sha256()
        step = max(1, size // 7)
        for start in range(0, size, step):
            hasher.update(kind(data[start:start + step]))
        assert hasher.digest() == expected
        assert hasher.hexdigest() == expected.hex() == hasher.hexdigest()


def test_copy_branches_the_running_state():
    hasher = digests.sha256(b"night")
    branch = hasher.copy()
    hasher.update(b"seam")
    branch.update(b"fall")
    assert hasher.hexdigest() == hashlib.sha256(b"nightseam").hexdigest()
    assert branch.hexdigest() == hashlib.sha256(b"nightfall").hexdigest()


def test_finished_hash_refuses_more_data():
    hasher = digests.sha256(b"abc")
    hasher.digest()
    with pytest.raises(ValueError):
        hasher.update(b"more")


def test_concurrent_hashes_stay_independent():
    blocks = [random.Random(i).randbytes(3 << 20) for i in range(12)]
    def work(block):
        hasher = digests.sha256()
        for start in range(0, len(block), 1 << 16):
            hasher.update(block[start:start + (1 << 16)])
        return hasher.hexdigest()
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(work, blocks)) == [hashlib.sha256(b).hexdigest() for b in blocks]


def test_unavailable_or_wrong_cng_falls_back_to_hashlib(fresh, monkeypatch):
    monkeypatch.setattr(digests, "_load_cng", lambda: None)
    assert digests.sha256(b"abc").hexdigest() == hashlib.sha256(b"abc").hexdigest()
    assert digests.IMPLEMENTATION == "hashlib"
