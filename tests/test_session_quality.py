"""Session quality prototype: SEP frame measurement, session grading, the idle
background worker and its API."""

import gzip
import io
import threading
import time

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient

from app import quality
from app.database import Database
from app.server import create_app
from _quality_fields import fits_bytes, star_field


# ---------------------------------------------------------------- measurement

def test_measure_ranks_round_blurred_and_trailed_stars():
    round_ = quality.measure_file_bytes(fits_bytes(star_field()))
    blurred = quality.measure_file_bytes(fits_bytes(star_field(sigma=2.4)))
    trailed = quality.measure_file_bytes(fits_bytes(star_field(stretch=2.5)))
    assert round_["stars"] > 100
    assert blurred["star_size"] > round_["star_size"] * 1.15
    assert round_["elongation"] < quality.ELONGATION_OK
    assert trailed["elongation"] > quality.ELONGATION_BAD
    assert round_["background"] == pytest.approx(1000, rel=0.02)
    assert 3 < round_["noise"] < 15


def test_measure_tracks_sky_glow_and_ignores_a_bayer_pattern():
    dark = quality.measure_file_bytes(fits_bytes(star_field()))
    bright = quality.measure_file_bytes(fits_bytes(star_field(sky=1400)))
    bayer = quality.measure_file_bytes(fits_bytes(star_field(bayer=True)))
    assert bright["background"] > dark["background"] * 1.3
    assert bayer["star_size"] == pytest.approx(dark["star_size"], rel=0.1)
    assert bayer["elongation"] < quality.ELONGATION_OK


def test_frame_pixels_reads_rice_and_gzip_fits_and_refuses_junk():
    data = star_field(shape=(200, 300), n=30)
    hdus = fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(data, compression_type="RICE_1")])
    packed = io.BytesIO()
    hdus.writeto(packed)
    assert np.array_equal(quality.frame_pixels(packed.getvalue()), data.astype(np.float32))
    plain = fits_bytes(data)
    assert np.array_equal(quality.frame_pixels(gzip.compress(plain)), data.astype(np.float32))
    cube = fits_bytes(np.stack([data, data, data]))
    assert quality.frame_pixels(cube).shape == data.shape
    with pytest.raises(quality.FrameUnreadable):
        quality.frame_pixels(b"SIMPLE  =                    T" + b" " * 2850)


def test_measure_survives_an_empty_sky():
    flat = np.full((300, 400), 500, dtype=np.uint16)
    metrics = quality.measure(flat.astype(np.float32))
    assert metrics["stars"] == 0 and metrics["star_size"] is None


# ---------------------------------------------------------------- grading

def _frame(i, size=2.9, elong=0.05, stars=150, bg=1000.0):
    return {"id": i, "date_obs": f"2026-08-22T{20 + i // 60:02d}:{i % 60:02d}:00",
            "background": bg, "noise": 6.0, "stars": stars, "star_size": size, "elongation": elong}


def test_steady_session_grades_high_and_every_frame_is_usable():
    summary = quality.session_summary([_frame(i) for i in range(12)], 12)
    assert summary["grade"] >= 95 and summary["usable"] == 12
    assert [f["id"] for f in summary["frames"]] == list(range(12))


def test_deteriorating_session_flags_trailed_soft_cloudy_and_bright_frames():
    frames = [_frame(i) for i in range(8)]
    frames += [_frame(8, elong=0.55), _frame(9, size=4.2), _frame(10, stars=40), _frame(11, bg=1400)]
    summary = quality.session_summary(frames, 14)
    reasons = {f["id"]: f["reason"] for f in summary["frames"]}
    assert reasons[8] == "trailed stars"
    assert reasons[9] == "soft stars"
    assert reasons[10] == "cloud or haze"
    assert reasons[11] == "bright sky"
    assert summary["usable"] == 8 and summary["measured"] == 12 and summary["total"] == 14
    assert summary["grade"] < quality.session_summary([_frame(i) for i in range(8)], 8)["grade"]
    assert summary["frames"][11]["sky_glow_pct"] == pytest.approx(40.0)


def test_thin_cloud_is_caught_by_lost_starlight_before_stars_vanish():
    clear = quality.measure_file_bytes(fits_bytes(star_field(seed=7)))
    thin = quality.measure_file_bytes(fits_bytes(star_field(seed=7, clouds=0.3)))
    assert thin["stars"] > 0.7 * clear["stars"]          # most stars still found
    assert thin["signal"] < 0.45 * clear["signal"]       # but most of their light is gone
    frames = [{**_frame(i), "signal": clear["signal"]} for i in range(6)]
    frames.append({**_frame(6, stars=thin["stars"]), "signal": thin["signal"]})
    summary = quality.session_summary(frames, 7)
    assert summary["frames"][6]["reason"] == "cloud or haze"
    assert summary["frames"][6]["signal_pct"] < 45


def test_unreadable_frames_count_as_unusable():
    frames = [_frame(0), _frame(1), {"id": 2, "date_obs": None, "background": None, "noise": None,
                                     "stars": 0, "star_size": None, "elongation": None}]
    summary = quality.session_summary(frames, 3)
    assert summary["frames"][2]["usable"] is False and summary["usable"] == 2
    assert quality.session_summary([], 5)["grade"] is None


def test_attach_uses_only_fresh_measurements_of_light_frames():
    images = [{"id": 1, "imagetyp": "light", "content_sha256": "a", "date_obs": "2026-08-22T20:00:00"},
              {"id": 2, "imagetyp": "light", "content_sha256": "b", "date_obs": "2026-08-22T20:01:00"},
              {"id": 3, "imagetyp": "dark", "content_sha256": "c", "date_obs": "2026-08-22T20:02:00"}]
    row = lambda sha, version=quality.VERSION: {"content_sha256": sha, "version": version, "status": "ok",
                                                "background": 1000.0, "noise": 6.0, "stars": 150,
                                                "star_size": 2.9, "elongation": 0.05}
    objects = [{"images": images, "sessions": [{"image_ids": [1, 2, 3]}]}]
    digests = {1: "a", 2: "b", 3: "c"}
    quality.attach_session_quality(objects, {1: row("a"), 2: row("stale"), 3: row("c")}, digests)
    summary = objects[0]["sessions"][0]["quality"]
    assert summary["total"] == 2 and summary["measured"] == 1
    quality.attach_session_quality(objects, {1: row("a", version=0)}, digests)
    assert objects[0]["sessions"][0]["quality"]["measured"] == 0


# ---------------------------------------------------------------- worker

@pytest.fixture
def library(tmp_path):
    archive = tmp_path / "archive"
    db = Database(tmp_path / "quality.sqlite3")
    db.set_setting("image_dir", str(archive))

    def add(name, data, imagetyp="light", date="2026-08-22T20:00:00"):
        target = archive / "M 31" / "night" / "lights" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return db.add_image(dict(file=f"M 31/night/lights/{name}", orig_name=name, object_name="M 31",
                                 imagetyp=imagetyp, date_obs=date, content_sha256=name,
                                 ingested_at="2026-08-23T00:00:00"))
    try:
        yield db, archive, add
    finally:
        db.conn.close()


def test_worker_measures_lights_and_skips_other_classes(library):
    db, _, add = library
    light = add("a.fit", fits_bytes(star_field()))
    dark = add("d.fit", fits_bytes(star_field()), imagetyp="dark")
    stack = add("s.fit", fits_bytes(star_field()), imagetyp="stack")
    worker = quality.QualityWorker(db)
    assert worker.measure_one(light) == "ok"
    assert worker.measure_one(light) == "skipped"         # already fresh
    assert worker.measure_one(dark) == "skipped"
    assert worker.measure_one(stack) == "skipped"
    rows = db.frame_quality()
    assert set(rows) == {light} and rows[light]["status"] == "ok" and rows[light]["stars"] > 100


def test_worker_records_unreadable_and_missing_frames(library):
    db, archive, add = library
    broken = add("broken.fit", b"SIMPLE  = T" + b" " * 3000)
    missing = add("gone.fit", fits_bytes(star_field()))
    (archive / "M 31" / "night" / "lights" / "gone.fit").unlink()
    worker = quality.QualityWorker(db)
    assert worker.measure_one(broken) == "error"
    assert worker.measure_one(missing) == "error"
    rows = db.frame_quality()
    assert rows[broken]["status"] == rows[missing]["status"] == "error"


def test_worker_never_reads_while_the_archive_lock_is_held(library):
    db, _, add = library
    light = add("a.fit", fits_bytes(star_field()))
    worker = quality.QualityWorker(db)
    held, release = threading.Event(), threading.Event()

    def holder():
        with db.archive_lock:
            held.set()
            release.wait(5)
    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(5)
        assert worker.measure_one(light) == "busy"
        assert db.frame_quality() == {}
    finally:
        release.set()
        thread.join()
    assert worker.measure_one(light) == "ok"


def test_background_loop_waits_while_busy_then_grades_newest_session_first(library):
    db, _, add = library
    old = add("old.fit", fits_bytes(star_field(seed=2)), date="2026-07-01T21:00:00")
    new = add("new.fit", fits_bytes(star_field(seed=3)), date="2026-08-22T21:00:00")
    busy = threading.Event()
    busy.set()
    worker = quality.QualityWorker(db, busy=busy.is_set)
    worker.IDLE_POLL = worker.MIN_REST = 0.05
    worker.REST_FACTOR = 0.0
    worker.start()
    try:
        time.sleep(0.4)
        assert worker.snapshot()["state"] == "waiting" and db.frame_quality() == {}
        busy.clear()
        deadline = time.monotonic() + 20
        while len(db.frame_quality()) < 2:
            assert time.monotonic() < deadline, worker.snapshot()
            time.sleep(0.05)
        rows = db.frame_quality()
        assert rows[new]["measured_at"] <= rows[old]["measured_at"]
    finally:
        worker.shutdown()
    assert not worker.thread.is_alive()


def test_prioritized_frames_go_first(library):
    db, _, add = library
    first = add("a.fit", fits_bytes(star_field(seed=4)), date="2026-08-22T21:00:00")
    chosen = add("b.fit", fits_bytes(star_field(seed=5)), date="2026-07-01T21:00:00")
    worker = quality.QualityWorker(db)
    assert worker.prioritize([chosen, True, "x"]) == 1
    assert worker._next() == chosen
    assert worker._next() == first


# ---------------------------------------------------------------- API

@pytest.fixture
def client(library):
    db, archive, add = library
    with TestClient(create_app(db), base_url="http://127.0.0.1") as c:
        c.add, c.db = add, db
        yield c


def test_library_payload_carries_session_quality(client):
    light = client.add("a.fit", fits_bytes(star_field()))
    client.add("b.fit", fits_bytes(star_field(seed=9)), date="2026-08-22T20:05:00")
    worker = client.app.state.quality
    assert worker.measure_one(light) == "ok"
    (obj,) = client.get("/api/library").json()["objects"]
    (session,) = obj["sessions"]
    q = session["quality"]
    assert q["total"] == 2 and q["measured"] == 1 and q["grade"] is not None
    assert q["frames"][0]["id"] == light


def test_quality_status_and_grade_routes(client):
    light = client.add("a.fit", fits_bytes(star_field()))
    status = client.get("/api/library/quality/status").json()
    assert status["enabled"] is False          # tests never start the background thread
    graded = client.post("/api/library/quality/grade", json={"image_ids": [light]}).json()
    assert graded["queued"] == 1 and graded["priority"] == 1
    assert client.post("/api/library/quality/grade", json={"image_ids": "x"}).status_code == 422
