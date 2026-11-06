"""Session quality prototype: per-frame measurements and a session grade.

Each light frame is measured once, slowly and in the background: sky
background, background noise, how many stars stand out, how large they are
and how stretched they are. A session's grade compares its frames with the
best of that same session (with an absolute backstop for trailing), so it
answers "how steady and usable was this night", not "how good is this
telescope". Measurements are discardable: they are keyed to the image's
content digest and the measurement VERSION and are re-made when either
changes.

The measuring itself is SEP (Source Extractor as a library): its background
model and RMS, and its source extraction with second moments per star. This
module only prepares the pixels, filters the catalogue and grades sessions.
"""
from __future__ import annotations

import gzip
import io
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from . import imglib, paths

log = logging.getLogger("nightseam.quality")

VERSION = 3
FULL_RES_MAX_SIDE = 4096     # frames up to this size are measured at full sampling
MAX_BINNED_SIDE = 2048       # larger frames are binned to at most ~2k px
BACKGROUND_BOX = 64          # SEP background mesh, measured pixels
DETECT_SIGMA = 5.0           # extraction threshold in background RMS
MIN_AREA_FULL = 9            # pixels above the threshold per star, full sampling
MIN_AREA_BINNED = 5          # the same for binned frames
MAX_STARS = 300              # brightest clean stars used for size and shape
SIGNAL_STARS = 50            # brightest stars whose summed flux tracks transparency
ELONGATION_RATIO_OK = 1.3    # elongation up to this many times the session's roundest
ELONGATION_RATIO_BAD = 1.8   # from here stars look trailed (Veil Seestar night: 290/296
                             # frames agree with StackingWizard's trailing verdict)
ELONGATION_FLOOR = 0.10      # a session's roundest never counts as rounder than this
ELONGATION_LIMIT = 0.45      # 1 - b/a: trailed whatever the rest of the night did
SIZE_BAD = 1.33              # star size this much larger than the session's best
STARS_BAD = 0.50             # half the session's usual stars or starlight lost: cloud
GLOW_BAD = 0.25              # sky 25% brighter than the session's darkest
UNUSABLE_CAP = 40            # highest score a frame failing any limit gets


class FrameUnreadable(ValueError):
    pass


# ---------------------------------------------------------------- pixels

def frame_pixels(raw: bytes) -> np.ndarray:
    """The first image in a FITS file's bytes as a 2-D float32 luminance.

    Handles plain, Rice/tile-compressed (.fz) and gzip FITS. Colour cubes are
    averaged over their planes; a Bayer mosaic is left as is (binning removes
    its pattern)."""
    from astropy.io import fits
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        with fits.open(io.BytesIO(raw), memmap=False, lazy_load_hdus=True) as hdus:
            for hdu in hdus:
                if getattr(hdu, "is_image", False) and hdu.header.get("NAXIS", 0) >= 2:
                    data = hdu.data
                    if data is None:
                        continue
                    data = np.asarray(data, dtype=np.float32)
                    if data.ndim == 3:
                        data = data.mean(axis=0)
                    if data.ndim == 2 and min(data.shape) >= 32:
                        return data
    except (OSError, ValueError, TypeError) as exc:
        raise FrameUnreadable(f"FITS pixels could not be read: {exc}") from None
    raise FrameUnreadable("No 2-D image found in the file.")


def _bin(data: np.ndarray, factor: int) -> np.ndarray:
    h = data.shape[0] // factor * factor
    w = data.shape[1] // factor * factor
    view = data[:h, :w].reshape(h // factor, factor, w // factor, factor)
    return np.ascontiguousarray(view.mean(axis=(1, 3), dtype=np.float32))


def _box2(data: np.ndarray) -> np.ndarray:
    """2x2 box average at full sampling: every output pixel covers one whole
    Bayer quad, so the colour pattern folds away without halving the pixels
    a small star is drawn with (binning a 1920x1080 Seestar frame left its
    stars too few pixels to show trailing)."""
    out = (data[:-1, :-1] + data[1:, :-1] + data[:-1, 1:] + data[1:, 1:]) * np.float32(0.25)
    return np.ascontiguousarray(out, dtype=np.float32)


def measure(data: np.ndarray) -> dict:
    """Background, noise, star count, star size and elongation of one frame.

    Frames up to FULL_RES_MAX_SIDE are measured at full sampling through a
    2x2 box filter; larger ones are binned until at most MAX_BINNED_SIDE wide
    (binning also folds a Bayer quad). SEP then models the background,
    extracts sources at DETECT_SIGMA times its RMS and gives each one second
    moments. star_size is the median second-moment radius sqrt(x2 + y2) in
    ORIGINAL pixels; elongation is the median 1 - b/a of the brightest clean,
    unsaturated stars (0 round, toward 1 a line)."""
    import sep

    if data.ndim != 2:
        raise FrameUnreadable("Expected a 2-D image.")
    finite = np.isfinite(data)
    if not finite.all():
        data = np.where(finite, data, np.nanmedian(data))
    if max(data.shape) <= FULL_RES_MAX_SIDE:
        factor, min_area = 1, MIN_AREA_FULL
        img = _box2(data)
    else:
        factor, min_area = 2, MIN_AREA_BINNED
        while max(data.shape) / factor > MAX_BINNED_SIDE:
            factor *= 2
        img = _bin(data, factor)
    try:
        background = sep.Background(img, bw=BACKGROUND_BOX, bh=BACKGROUND_BOX, fw=3, fh=3)
    except Exception as exc:          # SEP raises plain Exception
        raise FrameUnreadable(f"Background could not be modelled: {exc}") from None
    level, noise = float(background.globalback), float(background.globalrms)
    metrics = {"background": level, "noise": noise, "stars": 0, "signal": 0.0,
               "star_size": None, "elongation": None, "bin": factor}
    if not noise > 0:
        return metrics
    residual = img - background.back()
    sep.set_extract_pixstack(max(300000, img.size // 4))
    try:
        sources = sep.extract(residual, DETECT_SIGMA, err=noise, minarea=min_area,
                              deblend_cont=0.005, clean=True)
    except Exception as exc:
        raise FrameUnreadable(f"Stars could not be extracted: {exc}") from None
    # Saturated cores are flat-topped and measure too large; flagged sources
    # are blended or cut by the edge. Neither describes the seeing.
    ceiling = 0.98 * float(img.max())
    clean = sources[(sources["flag"] == 0) & (img[sources["ycpeak"], sources["xcpeak"]] < ceiling)
                    & (sources["a"] > 0) & (sources["b"] > 0)]
    metrics["stars"] = int(len(sources))
    if len(clean) < 3:
        return metrics
    clean = clean[np.argsort(clean["flux"])[::-1][:MAX_STARS]]
    # Starlight that got through: thin cloud dims every star long before it
    # hides them, so the brightest stars' summed flux reacts first.
    metrics["signal"] = float(clean["flux"][:SIGNAL_STARS].sum()) * factor * factor
    radius = np.sqrt(clean["x2"] + clean["y2"]) * factor
    elongation = 1.0 - clean["b"] / clean["a"]
    metrics["star_size"] = float(np.median(radius))
    metrics["elongation"] = float(np.median(elongation))
    return metrics


def measure_file_bytes(raw: bytes) -> dict:
    return measure(frame_pixels(raw))


# ---------------------------------------------------------------- grading

def is_gradable(image: dict) -> bool:
    """Light frames inside sessions; stacks and calibration are not graded."""
    return (image.get("imagetyp") or "light") == "light"


def fresh(image: dict, row: dict | None) -> bool:
    return (row is not None and row.get("version") == VERSION and
            row.get("content_sha256") == image.get("content_sha256"))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def session_summary(frames: list[dict], total: int) -> dict:
    """Grade one session from its measured light frames, in capture order.

    frames: [{"id", "date_obs", "background", "noise", "stars", "signal",
    "star_size", "elongation"}] (measured ones only). total: the session's light frames.
    Each frame scores 0..100 from four penalties against the session's own
    best (star size, elongation, star count and starlight, sky glow); an
    elongation above ELONGATION_LIMIT counts as trailed regardless.
    The session grade is the mean frame score; usable counts the frames that
    fail none of the limits."""
    summary = {"version": VERSION, "total": total, "measured": len(frames),
               "grade": None, "usable": 0, "frames": []}
    usable_frames = [f for f in frames if f.get("star_size")]
    if not usable_frames:
        summary["frames"] = [{"id": f["id"], "t": f.get("date_obs"), "score": 0,
                              "usable": False, "reason": "no stars"} for f in frames]
        if frames:
            summary["grade"] = 0
        return summary
    sizes = np.array([f["star_size"] for f in usable_frames])
    stars = np.array([f["stars"] or 0 for f in frames], dtype=float)
    glows = np.array([f["background"] for f in frames if f.get("background") is not None], dtype=float)
    ref_size = float(np.percentile(sizes, 20))
    elongations = np.array([f["elongation"] for f in usable_frames], dtype=float)
    ref_elongation = max(ELONGATION_FLOOR, float(np.percentile(elongations, 20)))
    ref_stars = max(1.0, float(np.percentile(stars, 80)))
    signals = np.array([f.get("signal") or 0.0 for f in frames], dtype=float)
    ref_signal = float(np.percentile(signals, 80)) if signals.any() else 0.0
    ref_glow = float(np.percentile(glows, 10)) if len(glows) else None
    scores = []
    for f in frames:
        reasons = []
        if not f.get("star_size"):
            size_pen = trail_pen = 1.0
            reasons.append("no stars")
        else:
            size_pen = _clamp((f["star_size"] / ref_size - 1) / (SIZE_BAD - 1))
            # Trailing compared with the night's roundest frames: how round a
            # star measures depends on the optics and sampling, not only on
            # tracking. A clearly stretched star is trailed whatever the night.
            ratio = f["elongation"] / ref_elongation
            trail_pen = _clamp((ratio - ELONGATION_RATIO_OK) / (ELONGATION_RATIO_BAD - ELONGATION_RATIO_OK))
            if f["elongation"] >= ELONGATION_LIMIT:
                trail_pen = 1.0
            if size_pen >= 1:
                reasons.append("soft stars")
            if trail_pen >= 1:
                reasons.append("trailed stars")
        cloud_pen = _clamp((1 - (f.get("stars") or 0) / ref_stars) / STARS_BAD)
        signal_pct = None
        if ref_signal > 0:
            signal_ratio = (f.get("signal") or 0.0) / ref_signal
            signal_pct = 100 * signal_ratio
            cloud_pen = max(cloud_pen, _clamp((1 - signal_ratio) / STARS_BAD))
        if cloud_pen >= 1:
            reasons.append("cloud or haze")
        glow_pen = 0.0
        glow_pct = None
        if ref_glow and f.get("background") is not None and ref_glow > 0:
            glow_pct = 100 * (f["background"] / ref_glow - 1)
            glow_pen = _clamp((f["background"] / ref_glow - 1) / GLOW_BAD)
            if glow_pen >= 1:
                reasons.append("bright sky")
        score = 100 * (1 - (0.3 * size_pen + 0.3 * trail_pen + 0.3 * cloud_pen + 0.1 * glow_pen))
        if reasons:                 # a frame that fails a limit never reads as fair
            score = min(score, UNUSABLE_CAP)
        score = int(round(max(0.0, score)))
        scores.append(score)
        summary["frames"].append({
            "id": f["id"], "t": f.get("date_obs"), "score": score, "usable": not reasons,
            "reason": ", ".join(reasons),
            "star_size": None if f.get("star_size") is None else round(f["star_size"], 3),
            "elongation": None if f.get("elongation") is None else round(f["elongation"], 3),
            "stars": f.get("stars"), "noise": f.get("noise"),
            "signal_pct": None if signal_pct is None else round(signal_pct, 1),
            "sky_glow_pct": None if glow_pct is None else round(glow_pct, 1)})
    summary["grade"] = int(round(sum(scores) / len(scores)))
    summary["usable"] = sum(1 for frame in summary["frames"] if frame["usable"])
    return summary


def attach_session_quality(objects: list[dict], measurements: dict[int, dict],
                           digests: dict[int, str | None]) -> None:
    """Add a `quality` summary to every session of group_library's output.

    `digests` maps image id to its stored content digest: the public library
    records leave digests out, and a measurement counts only for the bytes
    it was made from."""
    for obj in objects:
        by_id = {image["id"]: image for image in obj.get("images", []) if image.get("id") is not None}
        for session in obj.get("sessions", []):
            lights = [by_id[i] for i in session.get("image_ids", []) if i in by_id and is_gradable(by_id[i])]
            measured = []
            for image in lights:
                row = measurements.get(image["id"])
                if not fresh({"content_sha256": digests.get(image["id"])}, row):
                    continue
                if row["status"] == "ok":
                    measured.append({"id": image["id"], "date_obs": image.get("date_obs"),
                                     **{key: row.get(key) for key in ("background", "noise", "stars", "signal",
                                                                      "star_size", "elongation")}})
                else:
                    measured.append({"id": image["id"], "date_obs": image.get("date_obs"),
                                     "background": None, "noise": None, "stars": 0, "signal": 0.0,
                                     "star_size": None, "elongation": None})
            measured.sort(key=lambda f: (f["date_obs"] is None, f["date_obs"] or ""))
            session["quality"] = session_summary(measured, len(lights)) if lights else None


# ---------------------------------------------------------------- worker

def _lower_thread_priority():
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -2)  # LOWEST
        except (AttributeError, OSError):
            pass


class QualityWorker:
    """Grades light frames one at a time while the archive is idle.

    `busy()` reports archive activity (imports, reindex, health, compression,
    recent archive requests). The worker never reads while busy, reads one
    file's bytes under the archive lock (so no file is held open while an
    archive operation wants it), measures with the lock released, then rests
    `REST_FACTOR` times as long as the measurement took. Sessions are graded
    newest first; `prioritize` moves chosen frames to the front."""

    IDLE_POLL = 2.0
    REST_FACTOR = 3.0
    MIN_REST = 0.5
    REFRESH = 300.0

    def __init__(self, db, busy=lambda: False):
        self.db = db
        self.busy = busy
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._queue: list[int] = []
        self._priority: list[int] = []
        self._queued_at = 0.0
        self.thread = None
        self.state = "starting"
        self.measured_this_run = 0
        self.current = None
        self.revision = 0

    # -- control
    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True, name="session-quality")
            self.thread.start()

    def shutdown(self, timeout=5.0):
        self._stop.set()
        self._wake.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def prioritize(self, image_ids) -> int:
        ids = [i for i in image_ids if isinstance(i, int) and not isinstance(i, bool)]
        with self._lock:
            known = set(self._priority)
            self._priority.extend(i for i in ids if i not in known)
        self._wake.set()
        return len(ids)

    def snapshot(self) -> dict:
        with self._lock:
            pending = len(self._queue) + len(self._priority)
            return {"state": self.state, "pending": pending, "priority": len(self._priority),
                    "measured": self.measured_this_run, "current": self.current,
                    "revision": self.revision}

    # -- work
    def _plan(self) -> list[int]:
        """Unmeasured light frames, newest session first, capture order within."""
        images = self.db.images()
        rows = self.db.frame_quality()
        todo = [im for im in images if is_gradable(im) and not fresh(im, rows.get(im["id"]))]
        todo.sort(key=lambda im: (imglib.image_session_metadata(im).get("session_start") or "",
                                  im.get("date_obs") or ""), reverse=True)
        # newest session first, but frames within a session in capture order
        by_session: dict[str, list[dict]] = {}
        for im in todo:
            by_session.setdefault(imglib.image_session_metadata(im)["session_key"] or "", []).append(im)
        ordered = []
        for members in by_session.values():
            ordered.extend(sorted(members, key=lambda im: im.get("date_obs") or ""))
        return [im["id"] for im in ordered]

    def _next(self) -> int | None:
        with self._lock:
            if self._priority:
                return self._priority.pop(0)
        if not self._queue or time.monotonic() - self._queued_at > self.REFRESH:
            plan = self._plan()
            with self._lock:
                self._queue = plan
                self._queued_at = time.monotonic()
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def _read(self, image: dict, archive: Path) -> bytes | None:
        lock = self.db.archive_lock
        if not lock.acquire(blocking=False):
            return None
        try:
            target = paths.safe_join(archive, image.get("file") or "")
            if target is None or not target.is_file():
                raise FileNotFoundError(image.get("file"))
            return target.read_bytes()
        finally:
            lock.release()

    def measure_one(self, image_id: int) -> str:
        """Measure one frame now (no idle check). Returns ok/skipped/error/busy."""
        image = self.db.image(image_id)
        if image is None or not is_gradable(image):
            return "skipped"
        if fresh(image, self.db.frame_quality().get(image_id)):
            return "skipped"
        archive = Path(self.db.image_dir())
        try:
            raw = self._read(image, archive)
        except OSError as exc:
            self.db.set_frame_quality(image_id, image.get("content_sha256"), VERSION, None,
                                      f"File could not be read: {exc}")
            return "error"
        if raw is None:
            return "busy"
        try:
            metrics = measure_file_bytes(raw)
            error = None
        except FrameUnreadable as exc:
            metrics, error = None, str(exc)
        except MemoryError:
            metrics, error = None, "Not enough memory to measure this frame."
        del raw
        current = self.db.image(image_id)
        if current is None or current.get("content_sha256") != image.get("content_sha256"):
            return "skipped"           # changed while measuring; the next plan picks it up
        self.db.set_frame_quality(image_id, image.get("content_sha256"), VERSION, metrics, error)
        return "error" if error else "ok"

    def _run(self):
        _lower_thread_priority()
        while not self._stop.is_set():
            try:
                if self.busy():
                    self.state = "waiting"
                    self._wake.wait(self.IDLE_POLL)
                    self._wake.clear()
                    continue
                image_id = self._next()
                if image_id is None:
                    self.state = "done"
                    self.current = None
                    self._wake.wait(30.0)
                    self._wake.clear()
                    with self._lock:
                        self._queued_at = 0.0     # replan after new imports
                    continue
                self.state = "working"
                self.current = image_id
                started = time.monotonic()
                outcome = self.measure_one(image_id)
                elapsed = time.monotonic() - started
                if outcome == "busy":
                    with self._lock:
                        self._priority.insert(0, image_id)
                    self.state = "waiting"
                    self._wake.wait(self.IDLE_POLL)
                    self._wake.clear()
                    continue
                if outcome in ("ok", "error"):
                    self.measured_this_run += 1
                    self.revision += 1
                rest = max(self.MIN_REST, self.REST_FACTOR * elapsed) if outcome != "skipped" else 0
                if rest:
                    self._stop.wait(rest)
            except Exception:              # a prototype must never take the app down
                log.exception("session quality worker step failed")
                self._stop.wait(10.0)
        self.state = "stopped"
