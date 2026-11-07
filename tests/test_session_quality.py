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
    assert round_["elongation"] < 0.15
    assert trailed["elongation"] > quality.ELONGATION_LIMIT
    assert round_["background"] == pytest.approx(1000, rel=0.02)
    assert 3 < round_["noise"] < 15


@pytest.mark.parametrize("stretch", [1.0, 1.2, 1.5, 2.0, 3.0])
def test_elongation_matches_the_injected_axis_ratio(stretch):
    """Ground truth: Gaussian stars with b/a = 1/stretch, Bayer pattern on."""
    measured = quality.measure(star_field(shape=(1080, 1920), n=300, stretch=stretch, angle=0.9,
                                          bayer=True).astype(np.float32))
    assert measured["elongation"] == pytest.approx(1 - 1 / stretch, abs=0.04)
    assert 0 < measured["elongation_err"] < 0.01


@pytest.mark.parametrize("sigma", [1.3, 1.6, 2.0, 2.6, 3.5])
def test_star_size_is_the_half_flux_radius_across_any_trail(sigma):
    """A Gaussian's HFR is 1.1774 sigma; trailing must not inflate it."""
    round_ = quality.measure(star_field(shape=(1080, 1920), n=300, sigma=sigma).astype(np.float32))
    trailed = quality.measure(star_field(shape=(1080, 1920), n=300, sigma=sigma,
                                         stretch=1.8).astype(np.float32))
    assert round_["star_size"] == pytest.approx(1.1774 * sigma, rel=0.06)
    assert trailed["star_size"] == pytest.approx(round_["star_size"], rel=0.1)


def test_a_long_trail_is_measured_whole():
    """Deblending used to cut long trails into pieces that were then dropped."""
    measured = quality.measure(star_field(shape=(1080, 1920), n=200, stretch=4.0).astype(np.float32))
    assert measured["elongation"] > 0.6


def test_a_large_frame_is_measured_on_its_full_resolution_centre():
    measured = quality.measure(star_field(shape=(4200, 6300), n=900, sigma=3.0,
                                          stretch=1.5).astype(np.float32))
    assert measured["cropped"] is True
    assert measured["elongation"] == pytest.approx(1 / 3, abs=0.04)
    assert measured["star_size"] == pytest.approx(1.1774 * 3.0, rel=0.06)


def test_measure_tracks_sky_glow_and_ignores_a_bayer_pattern():
    dark = quality.measure_file_bytes(fits_bytes(star_field()))
    bright = quality.measure_file_bytes(fits_bytes(star_field(sky=1400)))
    bayer = quality.measure_file_bytes(fits_bytes(star_field(bayer=True)))
    assert bright["background"] > dark["background"] * 1.3
    assert bayer["star_size"] == pytest.approx(dark["star_size"], rel=0.1)
    assert bayer["elongation"] < 0.15


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

def _frame(i, size=2.9, elong=0.05, stars=150, bg=1000.0, err=0.015, noise=6.0, **extra):
    return {"id": i, "date_obs": f"2026-08-22T{20 + i // 60:02d}:{i % 60:02d}:00",
            "background": bg, "noise": noise, "stars": stars, "star_size": size, "elongation": elong,
            "elongation_err": err, **extra}


def test_steady_session_grades_high_and_every_frame_is_usable():
    summary = quality.session_summary([_frame(i) for i in range(12)], 12)
    assert summary["grade"] >= 95 and summary["usable"] == 12
    assert [f["id"] for f in summary["frames"]] == list(range(12))


def test_deteriorating_session_flags_trailed_soft_cloudy_and_bright_frames():
    frames = [_frame(i) for i in range(8)]
    frames += [_frame(8, elong=0.55), _frame(9, size=4.2), _frame(10, stars=40),
               _frame(11, bg=1400, noise=8.0)]
    summary = quality.session_summary(frames, 14)
    reasons = {f["id"]: f["reason"] for f in summary["frames"]}
    assert reasons[8] == "trailed stars"
    assert reasons[9] == "soft stars"
    assert reasons[10] == "cloud or haze"
    assert reasons[11] == "bright, noisy sky"
    assert summary["usable"] == 8 and summary["measured"] == 12 and summary["total"] == 14
    assert summary["grade"] < quality.session_summary([_frame(i) for i in range(8)], 8)["grade"]
    assert summary["frames"][11]["sky_glow_pct"] == pytest.approx(40.0)


def test_sky_glow_is_measured_above_the_camera_offset_and_costs_only_through_noise():
    """A Seestar S30 sits 8-33 ADU of sky over a 953 ADU offset, read noise
    ~42 ADU: the sky can triple while the noise barely moves."""
    frames = [_frame(i, bg=963.0, noise=42.0, pedestal=953.0) for i in range(8)]
    frames.append(_frame(8, bg=983.0, noise=42.5, pedestal=953.0))
    summary = quality.session_summary(frames, 9)
    late = summary["frames"][8]
    assert late["sky_glow_pct"] == pytest.approx(200.0)        # 30 over 10 ADU of sky, not +2%
    assert late["usable"] and late["score"] > 90


# ---------------------------------------------------------------- comparing sessions

SEESTAR = {"INSTRUME": "Seestar S30", "TELESCOP": "S30_9934000d", "EXPTIME": 20.0, "GAIN": 200,
           "XPIXSZ": 2.9, "XBINNING": 1, "FOCALLEN": 150.0, "FILTER": "LP", "BIAS": 953}


def test_frame_setup_reads_offset_scale_and_setup_from_the_header():
    setup = quality.frame_setup(SEESTAR)
    assert setup["exposure"] == 20.0 and setup["pedestal"] == 953
    assert setup["pixel_scale"] == pytest.approx(3.988, abs=0.001)      # 206.265 * 2.9 / 150
    assert setup["setup"] == "Seestar S30 | gain 200 | LP | bin 1"
    wcs = quality.frame_setup({"CDELT1": -0.0005, "EXPTIME": 60})
    assert wcs["pixel_scale"] == pytest.approx(1.8) and wcs["pedestal"] is None
    measured = quality.measure_file_bytes(fits_bytes(star_field(), **SEESTAR))
    assert measured["setup"] == "Seestar S30 | gain 200 | LP | bin 1" and measured["pedestal"] == 953


def test_frame_rates_are_per_second_and_square_arcsecond_above_the_offset():
    frame = {"background": 983.0, "pedestal": 953.0, "exposure": 20.0, "pixel_scale": 4.0,
             "signal": 2000.0, "star_size": 1.1}
    rates = quality.frame_rates(frame)
    assert rates["sky_rate"] == pytest.approx(30 / 20 / 16)
    assert rates["starlight_rate"] == pytest.approx(100.0)
    assert rates["seeing"] == pytest.approx(8.8)                     # FWHM = 2 HFR, in arcsec


def _session(sky, seeing_px=1.1, signal=2000.0, n=10, setup="S30 | gain 200 | LP | bin 1", trailed=0,
             scale=4.0, outlier=None):
    frames = [_frame(i, bg=953.0 + sky * 20 * scale * scale, noise=42.0, size=seeing_px, signal=signal,
                     pedestal=953.0, exposure=20.0, pixel_scale=scale, setup=setup,
                     elong=0.6 if i < trailed else 0.1) for i in range(n)]
    if outlier is not None:
        frames[-1] = {**frames[-1], "background": 953.0 + outlier * 20 * scale * scale}
    return quality.session_summary(frames, n)


def test_sessions_of_one_setup_are_ranked_on_a_shared_scale():
    perfect = _session(sky=0.10)
    full_moon = _session(sky=0.40, signal=1500.0)
    alone = _session(sky=0.20, setup="ASI2600 | gain 100 | L | bin 1", scale=1.0)
    quality.compare_sessions([("m31", perfect), ("m31", full_moon), ("m42", alone)])
    good, bright = perfect["conditions"]["modes"]["median"], full_moon["conditions"]["modes"]["median"]
    assert bright["sky_ratio"] == pytest.approx(4.0) and good["sky_ratio"] == pytest.approx(1.0)
    assert bright["starlight_ratio"] == pytest.approx(0.75)
    assert good["score"] > bright["score"]
    assert (good["rank"], good["of"], bright["rank"]) == (1, 2, 2)
    # Another camera is compared with nothing, not even seeing in arcseconds.
    lone = alone["conditions"]["modes"]["median"]
    assert lone["compared"]["seeing"] == 1 and lone["compared"]["sky"] == 1
    assert lone["seeing"] == pytest.approx(2.2)          # 2 x 1.1 px x 1.0"/px
    assert good["seeing_ratio"] == pytest.approx(1.0)    # 8.8" against its own camera's 8.8"
    assert lone["score"] is None


def test_sessions_are_compared_only_with_sessions_of_the_same_camera():
    """Camera A: three nights on M31; camera B: two. A ranks among A, B among B."""
    a = [_session(sky=s, seeing_px=p) for s, p in ((0.10, 1.0), (0.20, 1.2), (0.40, 1.5))]
    b = [_session(sky=s, seeing_px=p, setup="ASI2600 | gain 100 | L | bin 1", scale=1.0)
         for s, p in ((0.01, 0.5), (0.02, 0.6))]
    for i, session in enumerate(a + b):        # A was used first
        session["frames"][0]["t"] = f"2026-0{1 + i}-01T21:00:00"
    cameras = quality.compare_sessions([("m31", q) for q in a + b])
    assert cameras == [{"tag": "A", "camera": "S30", "sessions": 3},
                       {"tag": "B", "camera": "ASI2600", "sessions": 2}]
    median = lambda q: q["conditions"]["modes"]["median"]
    assert [q["conditions"]["camera_tag"] for q in a + b] == ["A", "A", "A", "B", "B"]
    assert [(median(q)["rank"], median(q)["of"]) for q in a] == [(1, 3), (2, 3), (3, 3)]
    assert [(median(q)["rank"], median(q)["of"]) for q in b] == [(1, 2), (2, 2)]
    # B's far darker, sharper numbers never become A's reference.
    assert median(a[0])["sky_ratio"] == pytest.approx(1.0)
    assert median(a[0])["seeing_ratio"] == pytest.approx(1.0)
    assert all(median(q)["compared"] == {"sky": 3, "seeing": 3, "starlight": 3} for q in a)
    assert all(median(q)["compared"] == {"sky": 2, "seeing": 2, "starlight": 2} for q in b)
    assert median(a[0])["score"] == median(b[0])["score"] == 100


def test_one_camera_compares_seeing_across_its_own_setups():
    low, high = _session(sky=0.10, seeing_px=1.0), _session(sky=0.10, seeing_px=1.5,
                                                            setup="S30 | gain 80 | LP | bin 1")
    quality.compare_sessions([("m31", low), ("m31", high)])
    assert high["conditions"]["modes"]["median"]["compared"]["seeing"] == 2
    assert high["conditions"]["modes"]["median"]["seeing_ratio"] == pytest.approx(1.5)
    assert high["conditions"]["modes"]["median"]["compared"]["sky"] == 1     # other gain


def test_sessions_without_a_camera_name_are_never_compared():
    unknown = "unknown camera | gain ? | no filter | bin 1"
    first, second = _session(sky=0.10, setup=unknown), _session(sky=0.40, setup=unknown)
    cameras = quality.compare_sessions([("m31", first), ("m31", second)])
    assert cameras == []
    for session in (first, second):
        conditions = session["conditions"]
        assert conditions["camera"] is None and conditions["camera_tag"] is None
        assert conditions["modes"]["median"]["score"] is None
        assert conditions["modes"]["median"]["compared"] == {}


def test_camera_tags_run_past_z():
    assert [quality.camera_tag(i) for i in (0, 1, 25, 26, 27, 701, 702)] == \
        ["A", "B", "Z", "AA", "AB", "ZZ", "AAA"]
    assert quality.setup_camera("Seestar S30 | gain 80 | LP | bin 1") == "Seestar S30"
    assert quality.setup_camera("unknown camera | gain ? | LP | bin 1") is None
    assert quality.setup_camera(None) is None


def test_conditions_use_only_the_sessions_main_setup():
    """A StackingWizard stack saved into lights/ has no camera offset."""
    frames = [_frame(i, bg=963.0, noise=42.0, pedestal=953.0, exposure=20.0, pixel_scale=4.0,
                     setup="S30 | gain 200 | LP | bin 1") for i in range(9)]
    frames.append(_frame(9, bg=5000.0, noise=10.0, exposure=4980.0, setup="unknown camera | gain ? | LP | bin 1"))
    conditions = quality.session_summary(frames, 10)["conditions"]
    assert conditions["offset_known"] and conditions["frames"] == 9 and conditions["other_setups"] == 1
    assert conditions["modes"]["mean"]["sky"] == pytest.approx(10 / 20 / 16)


def test_a_session_with_nothing_to_compare_gets_no_conditions_score():
    only = _session(sky=0.10)
    quality.compare_sessions([("m31", only)])
    assert only["conditions"]["modes"]["median"]["score"] is None


def test_best_median_and_mean_differ_with_an_outlier():
    clean = _session(sky=0.10)
    spoiled = _session(sky=0.10, outlier=2.0)            # one frame under a passing car's lights
    quality.compare_sessions([("m31", clean), ("m31", spoiled)])
    modes = spoiled["conditions"]["modes"]
    assert modes["median"]["sky_ratio"] == pytest.approx(1.0)
    assert modes["best"]["sky_ratio"] == pytest.approx(1.0)
    assert modes["mean"]["sky_ratio"] == pytest.approx(2.9)
    assert modes["mean"]["score"] < modes["median"]["score"]


def test_trailed_frames_lower_the_conditions_score():
    steady, windy = _session(sky=0.10), _session(sky=0.10, trailed=5)
    quality.compare_sessions([("m31", steady), ("m31", windy)])
    assert windy["conditions"]["trailed_pct"] == 50.0
    assert windy["conditions"]["modes"]["median"]["score"] < steady["conditions"]["modes"]["median"]["score"]


def test_trailing_is_a_visible_and_significant_excess_over_the_nights_roundest():
    """A real Seestar night: the roundest frames measured about 0.12 (the
    optics' own shape), trailed ones 0.22 and more."""
    frames = [_frame(i, elong=0.12 + 0.01 * (i % 4)) for i in range(20)]
    frames += [_frame(20, elong=0.30), _frame(21, elong=0.20)]
    summary = quality.session_summary(frames, 22)
    reasons = {f["id"]: f["reason"] for f in summary["frames"]}
    assert reasons[20] == "trailed stars"
    assert summary["frames"][20]["trail_excess"] == pytest.approx(0.17, abs=0.01)
    assert reasons[21] == ""                       # +0.07: penalised, still usable
    assert summary["frames"][21]["score"] < summary["frames"][0]["score"]
    assert all(reasons[i] == "" for i in range(20))
    # A clearly stretched star stays trailed even when the whole night is.
    smeared = quality.session_summary([_frame(i, elong=0.5) for i in range(5)], 5)
    assert smeared["usable"] == 0


def test_noise_never_trails_a_frame():
    """The same excess counts only when it stands well clear of the
    measurement's own error (few or faint stars)."""
    frames = [_frame(i, elong=0.12) for i in range(10)]
    frames.append(_frame(10, elong=0.25, err=0.06))     # +0.13 but only ~2 standard errors
    frames.append(_frame(11, elong=0.25, err=0.01))     # +0.13 at ~7 standard errors
    reasons = {f["id"]: f["reason"] for f in quality.session_summary(frames, 12)["frames"]}
    assert reasons[10] == "" and reasons[11] == "trailed stars"


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


def test_stacked_images_in_lights_are_skipped_not_graded(library):
    """StackingWizard saves NCOMBINE-tagged masters; filed under lights/ they
    must not count as frames of the night."""
    db, _, add = library
    single = add("single.fit", fits_bytes(star_field(), **SEESTAR))
    stack_header = {key: value for key, value in SEESTAR.items() if key != "BIAS"}
    stacked = add("NGC_6960_wizardstack_249f.fits", fits_bytes(star_field(seed=3), NCOMBINE=249, **stack_header))
    worker = quality.QualityWorker(db)
    assert worker.measure_one(single) == "ok" and worker.measure_one(stacked) == "ok"
    rows = db.frame_quality()
    assert rows[stacked]["status"] == "skipped" and "Stacked" in rows[stacked]["error"]
    images = db.images()
    objects = [{"images": images, "sessions": [{"image_ids": [single, stacked]}]}]
    quality.attach_session_quality(objects, rows, {i["id"]: i["content_sha256"] for i in images})
    summary = objects[0]["sessions"][0]["quality"]
    assert summary["total"] == 1 and summary["measured"] == 1
    assert summary["conditions"]["offset_known"]


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


def _thread_pool(workers):
    """Stands in for the process pool where only the scheduling is tested."""
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor(workers)


def _wait_for(condition, worker, seconds=30):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, worker.snapshot()
        time.sleep(0.05)


def test_grade_now_measures_the_chosen_frames_without_resting(library):
    db, _, add = library
    ids = [add(f"{i}.fit", fits_bytes(star_field(seed=20 + i)), date=f"2026-08-22T21:0{i}:00")
           for i in range(4)]
    other = add("other.fit", fits_bytes(star_field(seed=30)), date="2026-07-01T21:00:00")
    busy = threading.Event()
    busy.set()                                 # background grading would never start
    worker = quality.QualityWorker(db, busy=busy.is_set, working=lambda: False,
                                   pool_factory=_thread_pool, workers=lambda: 3)
    worker.REST_FACTOR = 100.0                 # Grade now must not rest at all
    assert worker.grade_now(ids + [ids[0], True]) == 4
    assert worker.grade_now(ids) == 0          # already part of the run
    worker.start()
    try:
        _wait_for(lambda: worker.snapshot()["job"] is None, worker)
        assert set(db.frame_quality()) == set(ids)
        assert worker.snapshot()["measured"] == 4 and other not in db.frame_quality()
        assert worker.grade_now(ids) == 0      # nothing left to grade there
        assert worker.snapshot()["job"] is None
    finally:
        worker.shutdown()


def test_grade_now_of_everything_uses_real_processes(library):
    db, _, add = library
    ids = [add(f"{i}.fit", fits_bytes(star_field(seed=40 + i)), date=f"2026-08-22T21:0{i}:00")
           for i in range(3)]
    worker = quality.QualityWorker(db, busy=lambda: True, working=lambda: False,
                                   workers=lambda: 2)
    assert worker.grade_now() == 3
    worker.start()
    try:
        _wait_for(lambda: worker.snapshot()["job"] is None, worker, seconds=120)
        rows = db.frame_quality()
        assert set(rows) == set(ids) and all(rows[i]["status"] == "ok" for i in ids)
        assert all(rows[i]["stars"] > 100 for i in ids)
    finally:
        worker.shutdown()


def test_grade_now_waits_while_an_archive_job_runs(library):
    db, _, add = library
    light = add("a.fit", fits_bytes(star_field()))
    working = threading.Event()
    working.set()
    worker = quality.QualityWorker(db, busy=lambda: True, working=working.is_set,
                                   pool_factory=_thread_pool, workers=lambda: 2)
    worker.IDLE_POLL = 0.05
    worker.grade_now([light])
    worker.start()
    try:
        _wait_for(lambda: worker.snapshot()["state"] == "waiting", worker)
        assert db.frame_quality() == {}
        working.clear()
        _wait_for(lambda: light in db.frame_quality(), worker)
    finally:
        worker.shutdown()


def test_stop_ends_a_grade_now_run_and_pause_holds_all_grading(library):
    db, _, add = library
    ids = [add(f"{i}.fit", fits_bytes(star_field(seed=50 + i)), date=f"2026-08-22T21:0{i}:00")
           for i in range(3)]
    worker = quality.QualityWorker(db, busy=lambda: False, pool_factory=_thread_pool,
                                   workers=lambda: 1)
    worker.grade_now(ids)
    assert worker.stop() is True               # before the thread ever ran it
    assert worker.snapshot()["job"] is None and worker.stop() is False
    worker.pause()
    assert worker.snapshot()["paused"] is True
    worker.IDLE_POLL = worker.MIN_REST = 0.05
    worker.start()
    try:
        _wait_for(lambda: worker.snapshot()["state"] == "paused", worker)
        time.sleep(0.3)
        assert db.frame_quality() == {}        # paused: not even background grading
        assert worker.snapshot()["pending"] == 3
    finally:
        worker.shutdown()
    # The pause is remembered across restarts until resumed.
    again = quality.QualityWorker(db, pool_factory=_thread_pool, workers=lambda: 1)
    assert again.snapshot()["paused"] is True
    assert again.grade_now([ids[0]]) == 1      # asking for a grade resumes
    assert again.snapshot()["paused"] is False
    assert quality.QualityWorker(db).snapshot()["paused"] is False


def test_a_broken_pool_hands_its_frames_back_to_background_grading(library):
    from concurrent.futures.process import BrokenProcessPool

    class Broken:
        def __init__(self, workers):
            pass

        def submit(self, *args):
            from concurrent.futures import Future
            future = Future()
            future.set_exception(BrokenProcessPool("a worker died"))
            return future

        def shutdown(self, **kwargs):
            pass

    db, _, add = library
    light = add("a.fit", fits_bytes(star_field()))
    worker = quality.QualityWorker(db, busy=lambda: True, working=lambda: False,
                                   pool_factory=Broken, workers=lambda: 2)
    worker.grade_now([light])
    worker.start()
    try:
        _wait_for(lambda: worker.snapshot()["job"] is None, worker)
        assert "measuring process failed" in worker.snapshot()["error"]
        assert db.frame_quality() == {}        # no frame was marked as failed
    finally:
        worker.shutdown()


def test_grade_now_workers_stay_within_cores_and_memory(monkeypatch):
    monkeypatch.setattr(quality.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(quality, "_available_memory", lambda: 4 * quality.GRADE_NOW_MEMORY + 1)
    assert quality.grade_now_workers() == 4
    monkeypatch.setattr(quality, "_available_memory", lambda: None)
    assert quality.grade_now_workers() == 16
    monkeypatch.setattr(quality, "_available_memory", lambda: 10)
    assert quality.grade_now_workers() == 1


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


def test_quality_now_and_control_routes(client):
    light = client.add("a.fit", fits_bytes(star_field()))
    client.add("b.fit", fits_bytes(star_field(seed=9)), date="2026-08-22T20:05:00")
    now = client.post("/api/library/quality/now", json={"image_ids": [light]}).json()
    assert now["queued"] == 1 and now["job"]["total"] == 1 and now["paused"] is False
    everything = client.post("/api/library/quality/now", json={}).json()
    assert everything["queued"] == 1 and everything["job"]["total"] == 2
    stopped = client.post("/api/library/quality/control", json={"action": "stop"}).json()
    assert stopped["job"] is None
    assert client.post("/api/library/quality/control", json={"action": "pause"}).json()["paused"] is True
    assert client.db.setting("quality_paused") is True
    assert client.post("/api/library/quality/control", json={"action": "resume"}).json()["paused"] is False
    assert client.post("/api/library/quality/control", json={"action": "delete"}).status_code == 422
    # Grading controls are no archive work: they never hold background grading off.
    from app import server
    server._state["last_archive_request"] = -1e9
    client.post("/api/library/quality/control", json={"action": "pause"})
    client.post("/api/library/quality/now", json={"image_ids": [light]})
    assert server._state["last_archive_request"] == -1e9
    payload = client.get("/api/library").json()
    assert payload["quality_cameras"] == []    # nothing measured yet
