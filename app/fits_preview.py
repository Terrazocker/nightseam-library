"""Bounded grayscale FITS previews; originals and integrity records stay read-only."""
from __future__ import annotations

import io
import math
import os
import stat
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.visualization import AsinhStretch
from PIL import Image

from .paths import safe_join, safe_rel

MAX_HDUS = 64
MAX_ROW_PIXELS = 100_000
MAX_TILE_PIXELS = 8_000_000
MAX_READ_BYTES = 64 * 1024 * 1024


class PreviewUnavailable(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Preview:
    png: bytes
    width: int
    height: int
    hdu: int
    first_plane: bool


class _BorrowedFile:
    """Keep the caller's handle open and route all Astropy reads through a cap.

    Deliberately expose no raw/buffer/fileno: Astropy would otherwise use
    numpy.fromfile and bypass the read budget for real files.
    """
    def __init__(self, source, cancelled=None):
        self.source = source
        self.remaining = MAX_READ_BYTES
        self.cancelled = cancelled

    @property
    def closed(self):
        return self.source.closed

    @property
    def mode(self):
        return "rb"

    def seek(self, offset, whence=os.SEEK_SET):
        return self.source.seek(offset, whence)

    def tell(self):
        return self.source.tell()

    def close(self):
        pass

    def read(self, size=-1):
        if self.cancelled and self.cancelled():
            raise PreviewUnavailable("Preview stopped during application shutdown.", 409)
        if size < 0 or size > self.remaining:
            raise PreviewUnavailable("The preview exceeds the bounded FITS reading limit.")
        data = self.source.read(size)
        self.remaining -= len(data)
        return data


def _grayscale(sample):
    finite = np.isfinite(sample)
    values = sample[finite]
    if not values.size:
        raise PreviewUnavailable("The preview samples have no finite pixels to display.")
    scale = max(np.max(np.abs(values)), 1.0)
    scaled = values / scale
    lo, hi = np.percentile(scaled, (1, 99.5))
    if hi <= lo:
        lo, hi = scaled.min(), scaled.max()
    if hi <= lo:
        gray = np.full(sample.shape, 128, dtype=np.uint8)
    else:
        normalized = (np.where(finite, sample, 0.0) / scale - lo) / (hi - lo)
        stretched = AsinhStretch(.1)(np.clip(normalized, 0.0, 1.0))
        gray = np.rint(stretched * 255).astype(np.uint8)
    gray[~finite] = 0
    output = io.BytesIO()
    Image.fromarray(np.flipud(gray)).save(output, format="PNG")
    return output.getvalue()


def render_preview(source, max_px=512, cancelled=None):
    """Read bounded rows/tile bands instead of complete image/cube arrays."""
    if not isinstance(max_px, int) or not 1 <= max_px <= 1024:
        raise PreviewUnavailable("Preview size must be between 1 and 1024 pixels.")
    source.seek(0, os.SEEK_END)
    length = source.tell()
    source.seek(0)
    try:
        with warnings.catch_warnings(record=True), fits.open(
                _BorrowedFile(source, cancelled), mode="readonly", memmap=False,
                lazy_load_hdus=True, ignore_missing_end=False) as hdus:
            for index in range(MAX_HDUS):
                try:
                    hdu = hdus[index]
                except IndexError:
                    break
                if not isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU, fits.CompImageHDU)) or isinstance(hdu, fits.GroupsHDU):
                    continue
                shape = hdu.shape
                if len(shape) < 2 or not all(axis > 0 for axis in shape):
                    continue
                if len(shape) > 8 or shape[-1] > MAX_ROW_PIXELS:
                    raise PreviewUnavailable("The image dimensions exceed the preview reader's limits.")
                height, width = shape[-2:]
                info = hdu.fileinfo()
                if isinstance(hdu, fits.CompImageHDU):
                    tiles = tuple(min(axis, tile) for axis, tile in zip(shape, hdu.tile_shape))
                    # A section decodes all tiles across this row band,
                    # including any spans in cube axes, not just one tile.
                    if width * math.prod(tiles[:-1]) > MAX_TILE_PIXELS:
                        raise PreviewUnavailable("The compressed image tiles exceed the preview reader's limits.")
                    required = info["datSpan"]
                else:
                    required = math.prod(shape) * abs(hdu.header["BITPIX"]) // 8
                if info["datLoc"] + required > length:
                    raise PreviewUnavailable("The FITS image data is truncated.")
                step = max(1, math.ceil(max(width, height) / max_px))
                sample = np.empty((math.ceil(height / step), math.ceil(width / step)), dtype=np.float64)
                prefix = (0,) * (len(shape) - 2)
                for output_row, input_row in enumerate(range(0, height, step)):
                    row = hdu.section[prefix + (input_row, slice(None))]
                    sample[output_row] = row[::step]
                if cancelled and cancelled():
                    raise PreviewUnavailable("Preview stopped during application shutdown.", 409)
                return Preview(_grayscale(sample), width, height, index, len(shape) > 2)
    except PreviewUnavailable:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise PreviewUnavailable("The FITS image data could not be read.") from exc
    raise PreviewUnavailable("No image HDU with at least two dimensions was found.")


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _target(root, relative, identity):
    clean = safe_rel(relative)
    if clean is None:
        raise PreviewUnavailable("The recorded exposure path is unsafe.", 404)
    info = root.stat()
    if root.is_symlink() or root.is_junction() or (info.st_dev, info.st_ino) != identity:
        raise PreviewUnavailable("The archive changed. Refresh the library and try again.", 409)
    current = root
    for segment in clean.split("/"):
        current /= segment
        if current.is_symlink() or current.is_junction():
            raise PreviewUnavailable("Linked exposure paths cannot be previewed.", 404)
    target = safe_join(root, clean)
    if target is None or not target.is_relative_to(root):
        raise PreviewUnavailable("The recorded exposure path is unsafe.", 404)
    return target


def preview_image(db, image_id, max_px=512, cancelled=None):
    if not db.archive_lock.acquire(blocking=False):
        raise PreviewUnavailable("An archive operation is running. Wait for it to finish, then retry the preview.", 409)
    try:
        record = db.image(image_id)
        if record is None:
            raise PreviewUnavailable("Exposure not found in the library.", 404)
        try:
            root = Path(db.image_dir()).resolve(strict=True)
            info = root.stat()
            if not stat.S_ISDIR(info.st_mode):
                raise PreviewUnavailable("The archive folder is unavailable.", 404)
            identity = (info.st_dev, info.st_ino)
            target = _target(root, record["file"], identity)
            before = target.stat()
            if not stat.S_ISREG(before.st_mode):
                raise PreviewUnavailable("The exposure is not a regular file.")
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as source:
                checked = _target(root, record["file"], identity)
                if checked != target or _signature(os.fstat(source.fileno())) != _signature(before):
                    raise PreviewUnavailable("The exposure changed while opening it. Try again.", 409)
                result = render_preview(source, max_px, cancelled)
                current = _target(root, record["file"], identity).stat()
                if cancelled and cancelled():
                    raise PreviewUnavailable("Preview stopped during application shutdown.", 409)
                if _signature(os.fstat(source.fileno())) != _signature(before) or _signature(current) != _signature(before):
                    raise PreviewUnavailable("The exposure changed during the preview. Try again.", 409)
                return result
        except FileNotFoundError:
            raise PreviewUnavailable("The exposure or archive folder is missing.", 404) from None
        except PermissionError:
            raise PreviewUnavailable("The exposure cannot be read. Check archive permissions.", 403) from None
        except (OSError, RuntimeError) as exc:
            raise PreviewUnavailable("The exposure or archive is unavailable. Try again.", 409) from exc
    finally:
        db.archive_lock.release()
