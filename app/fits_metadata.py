"""Bounded metadata-only FITS traversal; original bytes and pixels stay untouched.

Gzip is decoded only far enough to reach the selected header. Tiled images need
no pixel codec: their metadata is in the compressed-image table's plain header.
This reader does not verify pixel payloads or gzip trailers beyond that header.
"""
from __future__ import annotations

import gzip
import math
import time
import zlib
from pathlib import Path

from . import fits

_BASE_SUFFIXES = (".fits", ".fit", ".fts", ".fits.fz", ".fit.fz", ".fts.fz", ".fz")
FITS_SUFFIXES = tuple(sorted((*_BASE_SUFFIXES, *(s + ".gz" for s in _BASE_SUFFIXES)),
                             key=len, reverse=True))
MAX_HDUS = 32
MAX_HEADER_BYTES = fits._MAX_BLOCKS * fits._BLOCK
MAX_TOTAL_HEADER_BYTES = 4 << 20
MAX_GZIP_SCAN_BYTES = 64 << 20
MAX_GZIP_INPUT_BYTES = 64 << 20
MAX_SCAN_SECONDS = 5.0
_CHUNK = 64 << 10
_MAX_AXES = 16
_MAX_SIZE = (1 << 63) - 1


class FitsMetadataError(ValueError):
    """Unsupported, malformed or excessively expensive metadata traversal."""


def filename_parts(name: str) -> tuple[str, str]:
    """Keep a recognized compound suffix intact when choosing collision names."""
    for suffix in FITS_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[:-len(suffix)], name[-len(suffix):]
    path = Path(name)
    return path.stem, path.suffix


def is_fits_name(name: str) -> bool:
    return name.lower().endswith(FITS_SUFFIXES)


class _Reader:
    def __init__(self, source, compressed, size, checkpoint):
        self.source = source
        self.compressed = compressed
        self.size = size
        self.checkpoint = checkpoint
        self.deadline = time.monotonic() + MAX_SCAN_SECONDS
        self.expanded = 0
        self.header_bytes = 0
        self.header_complete = False

    def check(self):
        if self.checkpoint is not None:
            self.checkpoint()
        if time.monotonic() > self.deadline:
            raise FitsMetadataError("FITS metadata scan exceeded its time limit.")

    def read(self, count):
        chunks = []
        while count:
            self.check()
            amount = min(count, _CHUNK)
            if self.compressed:
                remaining = MAX_GZIP_SCAN_BYTES - self.expanded
                if remaining <= 0:
                    raise FitsMetadataError("Gzip FITS metadata scan exceeds the expanded-byte limit.")
                # read1 bounds each decoder step instead of expanding an entire file.
                chunk = self.source.read1(min(amount, remaining))
                self.expanded += len(chunk)
            else:
                chunk = self.source.read(amount)
            if not chunk:
                break
            chunks.append(chunk)
            count -= len(chunk)
        return b"".join(chunks)

    def skip(self, count):
        self.check()
        if self.compressed:
            if count > MAX_GZIP_SCAN_BYTES - self.expanded:
                raise FitsMetadataError("Gzip FITS metadata scan exceeds the expanded-byte limit.")
            while count:
                amount = min(count, _CHUNK)
                if len(self.read(amount)) != amount:
                    raise FitsMetadataError("Truncated FITS data before an image extension.")
                count -= amount
        else:
            if self.source.tell() + count > self.size:
                raise FitsMetadataError("Truncated FITS data before an image extension.")
            self.source.seek(count, 1)


class _GzipInput:
    """Bound gzip's raw reads too, including unbounded FNAME/FCOMMENT fields."""
    def __init__(self, source, reader):
        self.source = source
        self.reader = reader
        self.consumed = 0

    def read(self, count=-1):
        self.reader.check()
        remaining = MAX_GZIP_INPUT_BYTES - self.consumed
        if remaining <= 0:
            raise FitsMetadataError("Gzip FITS metadata scan exceeds the compressed-byte limit.")
        count = min(remaining, _CHUNK if count < 0 else count)
        chunk = self.source.read(count)
        self.consumed += len(chunk)
        return chunk


def _header(reader, *, primary=False, tolerant=False):
    records = []
    reader.header_complete = False
    for offset in range(0, MAX_HEADER_BYTES, fits._BLOCK):
        block = reader.read(fits._BLOCK)
        if not block and offset == 0 and not primary:
            return None
        reader.header_bytes += len(block)
        if reader.header_bytes > MAX_TOTAL_HEADER_BYTES:
            raise FitsMetadataError("FITS metadata exceeds the total header-byte limit.")
        if offset == 0 and not block.startswith(b"SIMPLE" if primary else b"XTENSION"):
            raise FitsMetadataError("Unsupported exposure file (FITS header missing).")
        for pos in range(0, len(block) - fits._REC + 1, fits._REC):
            record = block[pos:pos + fits._REC].decode("latin-1")
            if fits._is_end_card(record):
                if len(block) != fits._BLOCK and not tolerant:
                    raise FitsMetadataError("Truncated FITS header block.")
                reader.header_complete = True
                return fits._parse_records(records)
            records.append(record)
        if len(block) != fits._BLOCK:
            if tolerant:
                return fits._parse_records(records)
            raise FitsMetadataError("Truncated FITS metadata header.")
    if tolerant:
        return fits._parse_records(records)
    raise FitsMetadataError("FITS metadata header exceeds its byte limit.")


def _integer(header, key, default=None):
    value = header.get(key, default)
    if type(value) is not int or not 0 <= value <= _MAX_SIZE:
        raise FitsMetadataError(f"Invalid FITS {key} dimension or size.")
    return value


def _axes(header, prefix=""):
    count = _integer(header, prefix + "NAXIS", 0)
    if count > _MAX_AXES:
        raise FitsMetadataError("FITS image exceeds the axis-count limit.")
    return [_integer(header, f"{prefix}NAXIS{axis}") for axis in range(1, count + 1)]


def _image(header):
    tiled = header.get("ZIMAGE") is True
    if tiled or str(header.get("XTENSION", "")).strip() == "IMAGE":
        if tiled and (str(header.get("XTENSION", "")).strip() != "BINTABLE"
                      or not isinstance(header.get("ZCMPTYPE"), str)
                      or not header["ZCMPTYPE"].strip()):
            raise FitsMetadataError("Invalid tiled FITS image header.")
        bitpix = header.get("ZBITPIX" if tiled else "BITPIX")
        if type(bitpix) is not int or bitpix not in (8, 16, 32, 64, -32, -64):
            raise FitsMetadataError("Invalid FITS image BITPIX.")
        axes = _axes(header, "Z" if tiled else "")
        return bool(axes) and all(axes)
    return False


def _data_size(header):
    bitpix = header.get("BITPIX")
    if type(bitpix) is not int or bitpix not in (8, 16, 32, 64, -32, -64):
        raise FitsMetadataError("Invalid FITS BITPIX before an image extension.")
    axes = _axes(header)
    if header.get("GROUPS") is True:
        raise FitsMetadataError("Random-groups FITS metadata traversal is unsupported.")
    elements = math.prod(axes) if axes else 0
    size = abs(bitpix) // 8 * _integer(header, "GCOUNT", 1) * (
        _integer(header, "PCOUNT", 0) + elements)
    if size > _MAX_SIZE:
        raise FitsMetadataError("FITS HDU exceeds the size limit.")
    return ((size + fits._BLOCK - 1) // fits._BLOCK) * fits._BLOCK


def _merge(primary, selected):
    merged = dict(primary)
    # Aliases and coordinate pairs must not mix contradictory HDU metadata.
    for keys in (("EXPTIME", "EXPOSURE", "EXPTIM", "ELAPTIME"),
                 ("TELESCOP", "INSTRUME"), ("SITELAT", "SITELONG"),
                 ("OBJCTRA", "RA", "OBJCTDEC", "DEC")):
        if any(key in selected for key in keys):
            for key in keys:
                merged.pop(key, None)
    merged.update(selected)
    if selected.get("ZIMAGE") is True:
        for key in ("BITPIX", "NAXIS"):
            if "Z" + key in selected:
                merged[key] = selected["Z" + key]
        for axis, length in enumerate(_axes(selected, "Z"), 1):
            merged[f"NAXIS{axis}"] = length
    return merged


def read_metadata(path, checkpoint=None, *, storage_info=None) -> dict:
    """Primary image metadata, or primary fallbacks plus first image extension.

    Plain primary headers keep legacy tolerance for missing END/dimensions.
    Gzip wrappers and traversed extension headers must be complete. Compressed
    pixel arrays are never decoded; later HDUs and unread gzip CRCs are not
    validated. Cancellation exceptions from checkpoint propagate unchanged.
    Optional storage_info receives the selected image's compression status,
    including gzip wrappers, without adding synthetic FITS header cards.
    """
    try:
        with Path(path).open("rb") as source:
            size = source.seek(0, 2)
            source.seek(0)
            compressed = source.read(2) == b"\x1f\x8b"
            source.seek(0)
            if compressed:
                reader = _Reader(None, True, size, checkpoint)
                with gzip.GzipFile(fileobj=_GzipInput(source, reader), mode="rb") as decoded:
                    reader.source = decoded
                    header = _read_metadata(reader, False)
            else:
                header = _read_metadata(_Reader(source, False, size, checkpoint), True)
            if storage_info is not None:
                storage_info['compressed'] = compressed or header.get('ZIMAGE') is True
            return header
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise FitsMetadataError("Invalid or truncated gzip FITS metadata.") from exc


def _read_metadata(reader, tolerant):
    primary = _header(reader, primary=True, tolerant=tolerant)
    # Preserve the established primary-header path, including legacy captures
    # with absent dimensions or END. Only an explicitly empty primary needs HDUs.
    if primary.get("NAXIS") != 0 or (tolerant and not reader.header_complete):
        return primary
    if tolerant and "BITPIX" not in primary:
        return primary
    reader.skip(_data_size(primary))
    for _ in range(MAX_HDUS - 1):
        extension = _header(reader)
        if extension is None:
            return primary
        if _image(extension):
            return _merge(primary, extension)
        reader.skip(_data_size(extension))
    raise FitsMetadataError("FITS metadata exceeds the HDU-count limit.")
