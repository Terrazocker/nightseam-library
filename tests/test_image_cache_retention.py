"""SP-10 (0.6.32 review): the Wikipedia/Wikidata/DSS2 image cache under
`data/imgcache` had no owner and no limit - every detail view leaves a
`<slug>.bin` + `<slug>.json` pair and nothing ever evicted one (measured 440
files / 24.8 MB; browsing the 12,176-object advanced catalog reaches ~1.3 GB).

Pins: the byte cap, the age cap, the "never delete a file younger than the
grace period" rule, the cheap-counter enforcement on the write path, and the
explicit clear action behind POST /api/images/cache/clear.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

import app.images as img
from app.database import Database
from app.server import create_app


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    d = tmp_path / "imgcache"
    monkeypatch.setattr(img, "CACHE_DIR", d)
    monkeypatch.setattr(img, "_cache_writes_since_check", 0)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _put(d, name: str, size: int, age_s: float = 0.0):
    p = d / name
    p.write_bytes(b"x" * size)
    if age_s:
        ts = time.time() - age_s
        os.utime(p, (ts, ts))
    return p


def test_byte_cap_evicts_oldest_first(cache, monkeypatch):
    monkeypatch.setattr(img, "CACHE_MAX_BYTES", 250)
    monkeypatch.setattr(img, "CACHE_MIN_AGE", 0)
    monkeypatch.setattr(img, "CACHE_MAX_AGE", 10 ** 9)
    old = _put(cache, "old.bin", 100, age_s=900)
    mid = _put(cache, "mid.bin", 100, age_s=600)
    new = _put(cache, "new.bin", 100, age_s=300)

    removed = img.prune_cache()
    assert removed == 1
    assert not old.exists(), "the least recently written payload goes first"
    assert mid.exists() and new.exists(), "the cap must not overshoot (250 >= 200)"
    assert img._cache_total_bytes() == 200


def test_age_cap_drops_a_payload_nobody_wanted_for_half_a_year(cache, monkeypatch):
    monkeypatch.setattr(img, "CACHE_MAX_BYTES", 10 ** 9)
    monkeypatch.setattr(img, "CACHE_MIN_AGE", 0)
    monkeypatch.setattr(img, "CACHE_MAX_AGE", 1000)
    stale = _put(cache, "stale.bin", 10, age_s=5000)
    fresh = _put(cache, "fresh.bin", 10, age_s=10)

    assert img.prune_cache() == 1
    assert not stale.exists()
    assert fresh.exists()


def test_a_file_inside_the_grace_window_is_never_deleted(cache, monkeypatch):
    """The request path resolves a slug and then reads the file: a prune that
    ran in between must not remove what we are about to serve."""
    monkeypatch.setattr(img, "CACHE_MAX_BYTES", 1)   # wildly over the cap
    monkeypatch.setattr(img, "CACHE_MIN_AGE", 300)
    monkeypatch.setattr(img, "CACHE_MAX_AGE", 1)     # age cap also exceeded
    just_written = _put(cache, "just-written.bin", 4096, age_s=0)

    assert img.prune_cache() == 0
    assert just_written.exists()
    assert just_written.read_bytes() == b"x" * 4096


def test_enforcement_is_a_counter_on_the_write_path(cache, monkeypatch):
    """One integer increment per write; the directory scan happens at most every
    CACHE_CHECK_EVERY writes."""
    monkeypatch.setattr(img, "CACHE_MAX_BYTES", 1)
    monkeypatch.setattr(img, "CACHE_MIN_AGE", 0)
    monkeypatch.setattr(img, "CACHE_MAX_AGE", 10 ** 9)
    monkeypatch.setattr(img, "CACHE_CHECK_EVERY", 3)
    _put(cache, "a.bin", 100, age_s=500)
    _put(cache, "b.bin", 100, age_s=400)

    img._prune_cache_opportunistically()          # 1
    img._prune_cache_opportunistically()          # 2
    assert (cache / "a.bin").exists()
    assert img._cache_writes_since_check == 2

    img._prune_cache_opportunistically()          # 3 -> the check runs
    assert img._cache_writes_since_check == 0
    assert not (cache / "a.bin").exists() and not (cache / "b.bin").exists()


def test_a_written_payload_triggers_the_check(cache, monkeypatch):
    """End-to-end through fetch_image: the write path calls the counter."""
    monkeypatch.setattr(img, "CACHE_CHECK_EVERY", 1)
    monkeypatch.setattr(img, "CACHE_MAX_BYTES", 1250)   # 1200-byte payload fits,
                                                        # 1300 total does not
    monkeypatch.setattr(img, "CACHE_MIN_AGE", 0)
    monkeypatch.setattr(img, "CACHE_MAX_AGE", 10 ** 9)
    old = _put(cache, "old.bin", 100, age_s=900)
    monkeypatch.setattr(img, "_download",
                        lambda url, timeout=10.0: (b"y" * 1200, "image/jpeg"))
    monkeypatch.setattr(img, "_wikidata_thumb", lambda title: "https://x/i.jpg")
    monkeypatch.setattr(img, "_wikipedia_thumb", lambda title: None)
    monkeypatch.setattr(img, "_dss_cutout", lambda *a, **k: None)
    monkeypatch.setattr(img, "_offline_until", 0.0)
    monkeypatch.setattr(img, "_consec_fail", 0)

    blob, ctype = img.fetch_image({"id": "ngc1", "wiki": "NGC 1"})
    assert blob and ctype == "image/jpeg"
    assert not old.exists(), "the counter fired on the write"
    assert (img.CACHE_DIR / f"{img._slug('ngc1', 'NGC 1', '')}.bin").exists(), (
        "the payload just written fits under the cap, so the prune must have "
        "evicted the OLD file, not the new one")


def test_a_file_matching_both_caps_is_counted_once(cache, monkeypatch):
    monkeypatch.setattr(img, "CACHE_MAX_BYTES", 10)
    monkeypatch.setattr(img, "CACHE_MIN_AGE", 0)
    monkeypatch.setattr(img, "CACHE_MAX_AGE", 100)
    _put(cache, "stale.bin", 100, age_s=900)     # over the byte cap AND the age cap
    _put(cache, "fresh.bin", 100, age_s=10)      # over the byte cap only
    assert img.prune_cache() == 2
    assert img._cache_files() == []


def test_clear_cache_removes_everything_and_reports_bytes(cache):
    _put(cache, "a.bin", 10)
    _put(cache, "a.json", 5)
    _put(cache, "b.bin", 20)
    assert img.clear_cache() == 35
    assert img._cache_files() == []
    # idempotent / a missing directory is not an error
    assert img.clear_cache() == 0


def test_prune_never_raises_on_a_missing_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(img, "CACHE_DIR", tmp_path / "does-not-exist")
    assert img.prune_cache() == 0
    assert img._cache_total_bytes() == 0


@pytest.fixture()
def client_cache_db(tmp_path, monkeypatch):
    d = tmp_path / "imgcache"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(img, "CACHE_DIR", d)
    db = Database(tmp_path / "nightseam.db")
    with TestClient(create_app(db, prefetch=False)) as c:
        yield c, d


def test_clear_route_frees_the_cache(client_cache_db):
    c, d = client_cache_db
    _put(d, "a.bin", 10)
    _put(d, "a.json", 5)
    r = c.post("/api/images/cache/clear")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "freed": 15}
    assert img._cache_files() == []


def test_clear_route_is_not_reachable_by_get(client_cache_db):
    """A GET would be reachable from any page the user visits with
    `<img src=...>` (the SP-03 class of problem)."""
    c, _d = client_cache_db
    assert c.get("/api/images/cache/clear").status_code in (404, 405)

