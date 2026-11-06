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

VERSION = 5
FULL_RES_MAX_SIDE = 4096     # larger frames are measured on their central square
BOX_VARIANCE = 0.25          # px^2 per axis the 2x2 box filter adds to every star
BACKGROUND_BOX = 64          # SEP background mesh, measured pixels
DETECT_SIGMA = 5.0           # extraction threshold in background RMS
MIN_AREA = 9                 # pixels above the threshold per star
SHAPE_STARS = 50             # brightest clean stars whose size and shape are measured
SIGNAL_STARS = 50            # brightest stars whose summed flux tracks transparency
HFR_APERTURE = 6.0           # half-flux aperture: this many times the stars' rms extent
TRAIL_EXCESS_OK = 0.05       # elongation above the night's roundest that still looks round
TRAIL_EXCESS_BAD = 0.10      # from here stars are visibly trailed (b/a ~10% lower)
TRAIL_SIGNIFICANCE = 3.0     # ... and the excess must be this many standard errors
ELONGATION_LIMIT = 0.40      # 1 - b/a (b/a 0.6): trailed whatever the rest of the night did
SIZE_BAD = 1.33              # star size this much larger than the session's best
STARS_BAD = 0.50             # half the session's usual stars or starlight lost: cloud
NOISE_BAD = 0.25             # background noise 25% above the session's quietest
UNUSABLE_CAP = 40            # highest score a frame failing any limit gets
# Comparing sessions (conditions):
CONDITION_MODES = ("median", "best", "mean")
BEST_FRACTION = 0.10         # "best" is the mean of a session's best tenth of frames
SKY_RATIO_BAD = 4.0          # sky 4x the darkest comparable session: full penalty
SEEING_RATIO_BAD = 1.5       # stars 50% wider than the sharpest comparable session
STARLIGHT_RATIO_BAD = 0.5    # half the starlight of the clearest comparable session
TRAILED_SHARE_BAD = 0.5      # half the frames trailed
CONDITION_WEIGHTS = {"sky": 0.35, "seeing": 0.30, "starlight": 0.20, "trailing": 0.15}


class FrameUnreadable(ValueError):
    pass


# ---------------------------------------------------------------- pixels

def read_frame(raw: bytes) -> tuple[np.ndarray, dict]:
    """The first image in a FITS file's bytes as a 2-D float32 luminance, and
    the capture setup its header describes (frame_setup).

    Handles plain, Rice/tile-compressed (.fz) and gzip FITS. Colour cubes are
    averaged over their planes; a Bayer mosaic is left as is (the 2x2 box
    filter folds its pattern). Pixel values are physical ADU (BZERO applied),
    the same units as the header's camera offset."""
    from astropy.io import fits
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        with fits.open(io.BytesIO(raw), memmap=False, lazy_load_hdus=True) as hdus:
            primary = dict(hdus[0].header)
            for hdu in hdus:
                if getattr(hdu, "is_image", False) and hdu.header.get("NAXIS", 0) >= 2:
                    data = hdu.data
                    if data is None:
                        continue
                    data = np.asarray(data, dtype=np.float32)
                    if data.ndim == 3:
                        data = data.mean(axis=0)
                    if data.ndim == 2 and min(data.shape) >= 32:
                        # A tile-compressed image keeps its cards on its own
                        # HDU; anything missing there comes from the primary.
                        return data, frame_setup({**primary, **dict(hdu.header)})
    except (OSError, ValueError, TypeError) as exc:
        raise FrameUnreadable(f"FITS pixels could not be read: {exc}") from None
    raise FrameUnreadable("No 2-D image found in the file.")


def frame_pixels(raw: bytes) -> np.ndarray:
    return read_frame(raw)[0]


def _number(header: dict, *keys) -> float | None:
    for key in keys:
        value = header.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(number):
            return number
    return None


def frame_setup(header: dict) -> dict:
    """What turns a frame's numbers into units another session can share.

    exposure: seconds. pedestal: the camera offset in ADU every pixel carries
    before any light (BIAS, as the Seestar writes it); without it a sky level
    is offset plus sky and its ratios understate real changes - a Seestar S30
    frame read 961-986 ADU over a 953 ADU offset, so the sky itself was 8-33.
    pixel_scale: arcsec per pixel from pixel size, binning and focal length,
    or from a WCS. setup: instrument, gain, filter and binning - the frames
    whose sky and starlight rates may be compared directly."""
    exposure = _number(header, "EXPTIME", "EXPOSURE")
    pedestal = _number(header, "BIAS")
    binning = _number(header, "XBINNING") or 1.0
    pixel_um, focal_mm = _number(header, "XPIXSZ"), _number(header, "FOCALLEN")
    scale = None
    if pixel_um and focal_mm and pixel_um > 0 and focal_mm > 0:
        scale = 206.265 * pixel_um * binning / focal_mm
    else:
        degrees = _number(header, "CDELT1", "CD1_1")
        if degrees:
            scale = abs(degrees) * 3600.0
        else:
            scale = _number(header, "SECPIX", "PIXSCALE", "SCALE")
    if scale is not None and not 0.01 < scale < 3600:
        scale = None
    instrument = str(header.get("INSTRUME") or header.get("TELESCOP") or "").strip()
    gain = _number(header, "GAIN")
    setup = " | ".join([instrument or "unknown camera",
                        f"gain {gain:g}" if gain is not None else "gain ?",
                        str(header.get("FILTER") or "no filter").strip(),
                        f"bin {binning:g}"])
    from .ingest import is_stack
    return {"exposure": exposure if exposure and exposure > 0 else None,
            "pedestal": pedestal, "pixel_scale": scale, "setup": setup,
            "stacked": is_stack(header, "")}


def _box2(data: np.ndarray) -> np.ndarray:
    """2x2 box average at full sampling: every output pixel covers one whole
    Bayer quad, so the colour pattern folds away without halving the pixels
    a small star is drawn with (binning a 1920x1080 Seestar frame left its
    stars too few pixels to show trailing). It blurs every star by exactly
    BOX_VARIANCE per axis, which measure() takes back out."""
    out = (data[:-1, :-1] + data[1:, :-1] + data[:-1, 1:] + data[1:, 1:]) * np.float32(0.25)
    return np.ascontiguousarray(out, dtype=np.float32)


def _centre(data: np.ndarray) -> np.ndarray:
    """The central FULL_RES_MAX_SIDE square of a larger frame, at full sampling
    (binning squashed a large sensor's stars below a pixel and hid their
    shape; the centre also leaves out the corners' field curvature)."""
    h, w = data.shape
    side = FULL_RES_MAX_SIDE
    y0, x0 = max(0, (h - side) // 2), max(0, (w - side) // 2)
    return data[y0:y0 + side, x0:x0 + side]


def measure(data: np.ndarray) -> dict:
    """Background, noise, star count, star size and elongation of one frame.

    Frames are measured at full sampling through a 2x2 box filter; one larger
    than FULL_RES_MAX_SIDE is measured on its central square of that size.
    SEP models the background, extracts sources at DETECT_SIGMA times its RMS
    and gives each one second moments. Only the SHAPE_STARS brightest clean,
    unsaturated stars describe the frame: faint stars look stretched from
    noise alone. star_size is their median half-flux radius (HFR) in pixels
    measured across any trail - it includes a soft star's wings that
    threshold-limited moments cut off, and answers focus and seeing only;
    elongation is their median 1 - b/a (0 round, toward 1 a line) with its
    standard error and answers tracking. Both have the box filter's own blur
    removed.

    Deblending is off: SEP would cut a long trail into pieces, flag them as
    blended and leave only the rounder survivors to measure."""
    import sep

    if data.ndim != 2:
        raise FrameUnreadable("Expected a 2-D image.")
    cropped = max(data.shape) > FULL_RES_MAX_SIDE
    if cropped:
        data = _centre(data)
    finite = np.isfinite(data)
    if not finite.all():
        data = np.where(finite, data, np.nanmedian(data))
    img = _box2(data)
    try:
        background = sep.Background(img, bw=BACKGROUND_BOX, bh=BACKGROUND_BOX, fw=3, fh=3)
    except Exception as exc:          # SEP raises plain Exception
        raise FrameUnreadable(f"Background could not be modelled: {exc}") from None
    level, noise = float(background.globalback), float(background.globalrms)
    metrics = {"background": level, "noise": noise, "stars": 0, "signal": 0.0,
               "star_size": None, "elongation": None, "elongation_err": None, "cropped": cropped}
    if not noise > 0:
        return metrics
    residual = img - background.back()
    sep.set_extract_pixstack(max(300000, img.size // 4))
    try:
        sources = sep.extract(residual, DETECT_SIGMA, err=noise, minarea=MIN_AREA,
                              deblend_cont=1.0, clean=True)
    except Exception as exc:
        raise FrameUnreadable(f"Stars could not be extracted: {exc}") from None
    metrics["stars"] = int(len(sources))
    # Saturated cores are flat-topped and measure too large; sources cut by
    # the frame edge or with overflowing or singular moments describe nothing.
    ceiling = 0.98 * float(img.max())
    broken = sep.OBJ_TRUNC | sep.OBJ_DOVERFLOW | sep.OBJ_SINGU
    clean = sources[((sources["flag"] & broken) == 0)
                    & (img[sources["ycpeak"], sources["xcpeak"]] < ceiling)
                    & (sources["a"] > 0) & (sources["b"] > 0)]
    if len(clean) < 3:
        return metrics
    clean = clean[np.argsort(clean["flux"])[::-1]]
    # Starlight that got through: thin cloud dims every star long before it
    # hides them, so the brightest stars' summed flux reacts first.
    metrics["signal"] = float(clean["flux"][:SIGNAL_STARS].sum())
    def axes(stars):
        # a and b are the rms extents along the star's axes; the box filter
        # added BOX_VARIANCE to both.
        major = np.sqrt(np.clip(stars["a"] ** 2 - BOX_VARIANCE, 1e-4, None))
        minor = np.sqrt(np.clip(stars["b"] ** 2 - BOX_VARIANCE, 1e-4, None))
        return major, minor

    # Shape: the brightest clean stars, whatever their neighbours. A trail is
    # long, so any isolation rule scaled to the stars' extent would throw the
    # trailed bright stars out and keep the faint ones (measured: 0.29 instead
    # of the true 0.85 on a badly trailed Seestar frame). The median shrugs
    # off the odd merged double.
    shape = clean[:SHAPE_STARS]
    major, minor = axes(shape)
    elongation = 1.0 - minor / major
    # Size: isolated stars only. The half-flux aperture must hold the whole
    # trail, and a neighbour's light inside it makes a star read soft. Fall
    # back to the brightest stars when a crowded field has too few isolated.
    rmax = float(np.clip(HFR_APERTURE * np.median(shape["a"]), 4.0, 60.0))
    candidates = clean[:SHAPE_STARS * 4]
    distance = np.hypot(candidates["x"][:, None] - sources["x"][None, :],
                        candidates["y"][:, None] - sources["y"][None, :])
    distance[distance < 0.5] = np.inf                  # the star itself
    isolated = candidates[distance.min(axis=1) > 1.5 * rmax]
    sized = isolated[:SHAPE_STARS] if len(isolated) >= 10 else shape
    metrics["size_stars"] = int(len(sized))
    try:
        hfr, _ = sep.flux_radius(residual, sized["x"], sized["y"],
                                 np.full(len(sized), rmax), 0.5, subpix=5)
    except Exception as exc:
        raise FrameUnreadable(f"Star sizes could not be measured: {exc}") from None
    good = (hfr > 0) & np.isfinite(hfr)
    if not good.any():
        return metrics
    # A Gaussian's HFR is 1.1774 sigma; the box added BOX_VARIANCE to sigma^2.
    hfr = np.sqrt(np.clip(hfr[good] ** 2 - 1.1774 ** 2 * BOX_VARIANCE, 1e-4, None))
    # Size ACROSS the trail: trailing stretches only the long axis, so the
    # star's HFR scaled to its short axis tracks focus and seeing alone and
    # does not count a trailed frame twice. (An elliptical star's HFR follows
    # sqrt(major * minor); times sqrt(minor / major) leaves the minor width.)
    sized_major, sized_minor = axes(sized)
    across = hfr * np.sqrt(sized_minor[good] / sized_major[good])
    metrics["star_size"] = float(np.median(across))
    metrics["elongation"] = float(np.median(elongation))
    # Standard error of a median: 1.2533 sigma / sqrt(n), sigma from the MAD.
    spread = 1.4826 * float(np.median(np.abs(elongation - np.median(elongation))))
    metrics["elongation_err"] = float(1.2533 * spread / np.sqrt(len(elongation)))
    return metrics


def measure_file_bytes(raw: bytes) -> dict:
    """measure() of a FITS file's bytes, with its capture setup attached.

    A stacked image (its header counts several combined frames) is not a
    single exposure and is not measured: `skipped` says why. Such files sit
    in lights/ when stacking software saved them there."""
    data, setup = read_frame(raw)
    if setup.pop("stacked"):
        return {**setup, "skipped": "Stacked image, not a single exposure."}
    return {**measure(data), **setup}


def frame_rates(f: dict) -> dict:
    """A measured frame in units sessions can share (None where unknown).

    sky_rate: sky above the camera offset per second per square arcsecond
    (per pixel when the scale is unknown). seeing: star FWHM in arcseconds -
    twice the half-flux radius for a Gaussian star, measured across any
    trail. starlight_rate: the brightest stars' summed flux per second."""
    exposure, scale = f.get("exposure"), f.get("pixel_scale")
    area = scale * scale if scale else 1.0
    sky = starlight = seeing = None
    if exposure and f.get("background") is not None:
        sky = max(0.0, f["background"] - (f.get("pedestal") or 0.0)) / exposure / area
    if exposure and f.get("signal"):
        starlight = f["signal"] / exposure
    if f.get("star_size"):
        seeing = 2.0 * f["star_size"] * (scale or 1.0)
    return {"sky_rate": sky, "seeing": seeing, "starlight_rate": starlight}


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
    "star_size", "elongation", "elongation_err"}] (measured ones only).
    total: the session's light frames. Each frame scores 0..100 from four
    penalties against the session's own best (star size, elongation, star
    count and starlight, sky glow); trailing needs a visible and significant
    excess, and an elongation above ELONGATION_LIMIT is trailed regardless.
    The session grade is the mean frame score; usable counts the frames that
    fail none of the limits."""
    summary = {"version": VERSION, "total": total, "measured": len(frames),
               "grade": None, "usable": 0, "frames": [], "conditions": None}
    usable_frames = [f for f in frames if f.get("star_size")]
    if not usable_frames:
        summary["frames"] = [{"id": f["id"], "t": f.get("date_obs"), "score": 0,
                              "usable": False, "reason": "no stars"} for f in frames]
        if frames:
            summary["grade"] = 0
        return summary
    sizes = np.array([f["star_size"] for f in usable_frames])
    stars = np.array([f["stars"] or 0 for f in frames], dtype=float)
    noises = np.array([f["noise"] for f in frames if f.get("noise")], dtype=float)
    ref_noise = float(np.percentile(noises, 10)) if len(noises) else None
    skies = np.array([_sky(f) for f in frames if _sky(f) is not None], dtype=float)
    ref_sky = float(np.percentile(skies, 10)) if len(skies) else None
    ref_size = float(np.percentile(sizes, 20))
    elongations = np.array([f["elongation"] for f in usable_frames], dtype=float)
    ref_elongation = float(np.percentile(elongations, 20))
    errors = [f.get("elongation_err") for f in usable_frames if f.get("elongation_err")]
    ref_error = float(np.median(errors)) if errors else 0.0
    ref_stars = max(1.0, float(np.percentile(stars, 80)))
    signals = np.array([f.get("signal") or 0.0 for f in frames], dtype=float)
    ref_signal = float(np.percentile(signals, 80)) if signals.any() else 0.0
    scores = []
    for f in frames:
        reasons = []
        if not f.get("star_size"):
            size_pen = trail_pen = 1.0
            reasons.append("no stars")
        else:
            size_pen = _clamp((f["star_size"] / ref_size - 1) / (SIZE_BAD - 1))
            # Trailing is the elongation a frame adds over the night's roundest
            # frames (how round a star measures also depends on the optics and
            # sampling). It counts once it is both visible (TRAIL_EXCESS_*) and
            # well beyond the measurement's own error, so noise never trails a
            # frame. A clearly stretched star is trailed whatever the night did.
            excess = f["elongation"] - ref_elongation
            error = float(np.hypot(f.get("elongation_err") or ref_error, ref_error))
            significant = excess > TRAIL_SIGNIFICANCE * error
            trail_pen = _clamp((excess - TRAIL_EXCESS_OK) / (TRAIL_EXCESS_BAD - TRAIL_EXCESS_OK))
            if not significant:
                trail_pen = min(trail_pen, 0.99)
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
        # A brighter sky costs a frame only through the noise it adds: with a
        # camera whose own read noise dominates (a Seestar S30: ~42 ADU noise
        # over 8-33 ADU of sky) the sky can double at almost no cost. So the
        # penalty follows the noise; the sky level itself is reported.
        glow_pen = 0.0
        if ref_noise and f.get("noise"):
            glow_pen = _clamp((f["noise"] / ref_noise - 1) / NOISE_BAD)
            if glow_pen >= 1:
                reasons.append("bright, noisy sky")
        glow_pct = None
        if ref_sky and _sky(f) is not None:
            glow_pct = 100 * (_sky(f) / ref_sky - 1)
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
            "trail_excess": None if f.get("elongation") is None else round(f["elongation"] - ref_elongation, 3),
            "stars": f.get("stars"), "noise": f.get("noise"),
            "signal_pct": None if signal_pct is None else round(signal_pct, 1),
            "sky_glow_pct": None if glow_pct is None else round(glow_pct, 1)})
    summary["grade"] = int(round(sum(scores) / len(scores)))
    summary["usable"] = sum(1 for frame in summary["frames"] if frame["usable"])
    summary["conditions"] = session_conditions(frames, summary["frames"])
    return summary


def _sky(f: dict) -> float | None:
    """Sky above the camera offset, in the frame's own ADU."""
    if f.get("background") is None:
        return None
    return max(0.0, f["background"] - (f.get("pedestal") or 0.0))


def _statistic(values: list[float], mode: str, higher_is_better: bool) -> float | None:
    """One session's value: its median frame, its mean, or its best tenth."""
    values = [v for v in values if v is not None and np.isfinite(v)]
    if not values:
        return None
    if mode == "median":
        return float(np.median(values))
    if mode == "mean":
        return float(np.mean(values))
    ordered = sorted(values, reverse=higher_is_better)
    return float(np.mean(ordered[:max(1, int(np.ceil(len(ordered) * BEST_FRACTION)))]))


def session_conditions(frames: list[dict], graded: list[dict]) -> dict:
    """A session's conditions in units other sessions share, per statistic.

    For each of median, best (mean of the best BEST_FRACTION of frames) and
    mean: sky rate, seeing, starlight rate and elongation, plus the share of
    trailed frames. setup names the camera/gain/filter/binning most frames
    used; offset_known and scale_known say whether every frame carried a
    camera offset and a pixel scale (the sky rate is only physical when the
    offset is known)."""
    setups = [f.get("setup") for f in frames if f.get("setup")]
    setup = max(set(setups), key=setups.count) if setups else None
    # Only frames in the session's main setup share its units: a stack saved
    # into lights/ by stacking software, or a frame from a second camera,
    # would mix other exposures, offsets and scales into the session's value.
    same = [f for f in frames if f.get("setup") == setup] if setup else []
    others = len(frames) - len(same)
    rates = [frame_rates(f) for f in same]
    graded_by_id = {g["id"]: g for g in graded}
    trailed = sum(1 for f in same if "trailed" in (graded_by_id.get(f["id"], {}).get("reason") or ""))
    result = {
        "setup": setup,
        "frames": len(same),
        "other_setups": others,
        "offset_known": bool(same) and all(f.get("pedestal") is not None for f in same),
        "scale_known": bool(same) and all(f.get("pixel_scale") for f in same),
        "trailed_pct": round(100 * trailed / len(same), 1) if same else None,
        "modes": {},
    }
    for mode in CONDITION_MODES:
        result["modes"][mode] = {
            "sky": _statistic([r["sky_rate"] for r in rates], mode, False),
            "seeing": _statistic([r["seeing"] for r in rates], mode, False),
            "starlight": _statistic([r["starlight_rate"] for r in rates], mode, True),
            "elongation": _statistic([f.get("elongation") for f in same], mode, False),
        }
    return result


def compare_sessions(entries: list[tuple[str, dict]]) -> None:
    """Rank sessions against each other in place: (object key, quality).

    Each comparable measure is set against the best session that shares its
    units - sky by setup (camera offset removed, per second and arcsec^2),
    seeing in arcseconds across every setup with a known pixel scale (else by
    setup, in pixels), starlight by object AND setup (other targets have other
    stars). A session alone in a group is not compared on that measure. The
    conditions score combines the compared measures with the share of trailed
    frames; it stays empty until something could be compared. rank/of order
    an object's scored sessions."""
    for mode in CONDITION_MODES:
        groups: dict[tuple, list[dict]] = {}
        rows = []
        for object_key, quality in entries:
            conditions = (quality or {}).get("conditions")
            if not conditions or not conditions.get("setup"):
                continue
            values = conditions["modes"][mode]
            row = {"object": object_key, "conditions": conditions, "values": values, "out": {}}
            rows.append(row)
            setup = conditions["setup"]
            seeing_group = ("seeing", "arcsec") if conditions["scale_known"] else ("seeing", setup)
            for measure, group in (("sky", ("sky", setup)), ("seeing", seeing_group),
                                   ("starlight", ("starlight", object_key, setup))):
                if values.get(measure) is not None:
                    groups.setdefault(group, []).append(row)
                    row["out"][measure] = group
        references = {}
        for group, members in groups.items():
            measure = group[0]
            numbers = [m["values"][measure] for m in members]
            references[group] = (max(numbers) if measure == "starlight" else min(numbers), len(members))
        for row in rows:
            values, conditions = row["values"], row["conditions"]
            out = {"sky": values["sky"], "seeing": values["seeing"], "starlight": values["starlight"],
                   "elongation": values["elongation"], "compared": {}}
            penalties, weights = 0.0, 0.0
            for measure, group in row["out"].items():
                reference, members = references[group]
                if not reference:
                    continue
                ratio = values[measure] / reference
                out[f"{measure}_ratio"] = round(ratio, 3)
                out["compared"][measure] = members
                if members < 2:
                    continue
                if measure == "sky":
                    penalty = _clamp(np.log2(max(ratio, 1.0)) / np.log2(SKY_RATIO_BAD))
                elif measure == "seeing":
                    penalty = _clamp((ratio - 1) / (SEEING_RATIO_BAD - 1))
                else:
                    penalty = _clamp((1 - ratio) / (1 - STARLIGHT_RATIO_BAD))
                penalties += CONDITION_WEIGHTS[measure] * penalty
                weights += CONDITION_WEIGHTS[measure]
            score = None
            if weights:
                trail = _clamp((conditions.get("trailed_pct") or 0) / 100 / TRAILED_SHARE_BAD)
                penalties += CONDITION_WEIGHTS["trailing"] * trail
                weights += CONDITION_WEIGHTS["trailing"]
                score = int(round(100 * (1 - penalties / weights)))
            out["score"] = score
            conditions["modes"][mode] = out
            row["result"] = out
        by_object: dict[str, list[dict]] = {}
        for row in rows:
            if row["result"]["score"] is not None:
                by_object.setdefault(row["object"], []).append(row["result"])
        for scored in by_object.values():
            scored.sort(key=lambda r: -r["score"])
            for position, result in enumerate(scored, 1):
                result["rank"], result["of"] = position, len(scored)


def attach_session_quality(objects: list[dict], measurements: dict[int, dict],
                           digests: dict[int, str | None]) -> None:
    """Add a `quality` summary to every session of group_library's output.

    `digests` maps image id to its stored content digest: the public library
    records leave digests out, and a measurement counts only for the bytes
    it was made from. Sessions are then compared across the library."""
    from .database import FRAME_QUALITY_MEASURES
    entries = []
    for obj in objects:
        object_key = obj.get("object_id") or "u:" + imglib.name_key(obj.get("name") or "")
        by_id = {image["id"]: image for image in obj.get("images", []) if image.get("id") is not None}
        for session in obj.get("sessions", []):
            lights = []
            for i in session.get("image_ids", []):
                if i not in by_id or not is_gradable(by_id[i]):
                    continue
                row = measurements.get(i)
                # A stack filed under lights/ is no exposure of this night.
                if fresh({"content_sha256": digests.get(i)}, row) and row["status"] == "skipped":
                    continue
                lights.append(by_id[i])
            measured = []
            for image in lights:
                row = measurements.get(image["id"])
                if not fresh({"content_sha256": digests.get(image["id"])}, row):
                    continue
                values = {key: row.get(key) for key in FRAME_QUALITY_MEASURES}
                if row["status"] != "ok":
                    values.update(background=None, noise=None, stars=0, signal=0.0,
                                  star_size=None, elongation=None, elongation_err=None)
                measured.append({"id": image["id"], "date_obs": image.get("date_obs"), **values})
            measured.sort(key=lambda f: (f["date_obs"] is None, f["date_obs"] or ""))
            session["quality"] = session_summary(measured, len(lights)) if lights else None
            if session["quality"] and session["quality"].get("conditions"):
                entries.append((object_key, session["quality"]))
    compare_sessions(entries)


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
