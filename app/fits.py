"""Minimal pure-Python FITS reader - primary-HDU header keywords only.

Enough to ingest astrophotography captures: OBJECT, EXPTIME, DATE-OBS,
FILTER, OBJCTRA/OBJCTDEC (plus RA/DEC), TELESCOP/INSTRUME. FITS headers are
ASCII 80-character records inside 2880-byte blocks, so no astropy/numpy is
needed; image data is only touched by preview_png*() (tests and the
/api/images/{id}/preview endpoint, not the library UI).

Compressed FITS is deliberately out of scope - nothing here decompresses.
read_fits_header() requires `SIMPLE` at byte 0, so an fpack `.fits.fz` yields
only its empty stub primary: the original image keywords and the
ZNAXIS*/ZBITPIX geometry live in the SECOND HDU header (BINTABLE carrying
ZIMAGE = T), which this reader never reaches. Ingest and reindex take only
.fit/.fits (see app/ingest.py), so .fz/.fits.gz never enter the archive.
Handling the header case means walking HDUs; pixel access would additionally
need a RICE (or GZIP) tile decoder - and a whole-file .gz defeats the
per-sample seeks in preview_png_file(), since gzip re-inflates from the
start on every seek.
"""

from __future__ import annotations

import io
import math
import re
import struct
import zlib
from datetime import datetime

__all__ = [
    "read_fits_header", "parse_ra_hours", "parse_dec_deg", "parse_date_obs",
    "date_epoch", "extract_object_tokens", "header_size", "image_geometry",
    "preview_png", "preview_png_file",
]

_BLOCK = 2880
_REC = 80
_MAX_BLOCKS = 512          # safety cap (~1.5 MB of header, far beyond normal)

_KEYWORD_RE = re.compile(r"^\s*([A-Z0-9_-]{1,8})\s*=\s*(.*)$")
_STRING_RE = re.compile(r"^'((?:[^']|'')*)'\s*")

_DATE_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})(?::(\d{2}))?")
_DATE_SLASH_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})[T\s](\d{2}):(\d{2})(?::(\d{2}))?")

# designation-like tokens: "M31", "NGC 7000", "ngc7000", "Sh2-244", "Mel 22"
_DESIG_RE = re.compile(
    r"(?<![a-z0-9])([a-z]{1,6})[ _.-]*(\d{1,5}(?:[ _.-]+\d{1,5})?)(?![a-z0-9])")
# plain words that might be a common name ("andromeda", "crabnebula")
_WORD_RE = re.compile(r"[a-z]{4,}")

_STOPWORDS = {
    "light", "dark", "bias", "flat", "frames", "frame", "stack", "master",
    "final", "image", "images", "capture", "camera", "astro", "pilot", "fits",
    "fit", "data", "test", "trial", "good", "bad", "bin", "bin1", "bin2",
    "raw", "lrgb", "luminance", "luminancefilter", "filter", "mosaic",
}


def read_fits_header(data: bytes) -> dict:
    """Parse the primary HDU header of FITS bytes into a keyword dict.

    Returns {} when the data does not start with the SIMPLE record (i.e. not
    a FITS file). Duplicate keywords keep their first occurrence.
    """
    if not data.startswith(b"SIMPLE"):
        return {}
    records: list[str] = []
    for blk in range(_MAX_BLOCKS):
        block = data[blk * _BLOCK: (blk + 1) * _BLOCK]
        if len(block) < _REC:
            break
        for i in range(0, _BLOCK, _REC):
            rec = block[i:i + _REC].decode("latin-1", "replace")
            if _is_end_card(rec):
                return _parse_records(records)
            records.append(rec)
    return _parse_records(records)


def _is_end_card(rec: str) -> bool:
    """True for the FITS header terminator: the standalone `END` card.

    PY-10: the test used to be a bare `startswith("END")`, so any keyword whose
    name merely begins with END (`ENDTIME`, ...) truncated the header and every
    later card - EXPTIME, OBJECT, DATE-OBS, the coordinates - was dropped. The
    standard reserves the keyword `END` with no value, so require exactly that.
    """
    return rec[:3] == "END" and not rec[3:].strip()


def _parse_records(records: list[str]) -> dict:
    out: dict = {}
    for rec in records:
        if rec.startswith(("COMMENT", "HISTORY")) or not rec.strip():
            continue
        m = _KEYWORD_RE.match(rec)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        out.setdefault(key, _parse_value(raw))
    return out


def _finite_float(value) -> float | None:
    """The float value, or None when it is not a finite float.

    PY-04 (review 0.6.24): FITS cards are untrusted input, so `nan`/`inf`/
    `1e400` must never leave a parser as a float. `_finite` on the API boundary
    exists for exactly this reason (`allow_nan=False` JSON responses), but the
    header parsers sit BELOW it: a non-finite coordinate reached `math.sin()`
    (500 on ingest) and an `inf` EXPTIME was stored in the images/
    import_batches rows, wedging /api/library forever. Treat a non-finite
    number exactly like an unparseable one (None).

    Used by `parse_ra_hours` / `parse_dec_deg`; `_parse_value` keeps its own
    inline test because there a failed float() must fall back to the raw card
    text rather than to None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _parse_value(raw: str):
    v = raw.strip()
    if not v:
        return None
    if v.startswith("'"):
        m = _STRING_RE.match(v)
        if m:
            return m.group(1).replace("''", "'").rstrip()
        return v[1:].rstrip()
    if "/" in v:
        v = v.split("/", 1)[0].strip()
    if v in ("T", "F"):
        return v == "T"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        num = float(v)
    except ValueError:
        return v or None
    # PY-04: a non-finite card ('Infinity', 'nan', '1e400') is not a usable
    # number - treat it as unparseable instead of handing `inf` up the stack.
    return num if math.isfinite(num) else None


# ---------------------------------------------------------------- parsers

def parse_ra_hours(value) -> float | None:
    """Decimal hours (dot or comma), or sexagesimal 'HH MM SS.s' -> hours."""
    if value is None:
        return None
    s = str(value).strip().replace(",", ".")
    f = _finite_float(s)                 # PY-04: inf/nan -> None (not a number)
    if f is not None:
        return f
    parts = [p for p in re.split(r"[\s:]+",
                                 s.replace("h", " ").replace("m", " ")
                                  .replace("s", " ")) if p]
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if not vals or not all(math.isfinite(v) for v in vals):
        return None
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        return vals[0] + vals[1] / 60.0
    return vals[0] + vals[1] / 60.0 + vals[2] / 3600.0


def parse_dec_deg(value) -> float | None:
    """Decimal degrees (dot or comma), or sexagesimal '+DD MM SS' -> degrees."""
    if value is None:
        return None
    s = str(value).strip().replace(",", ".")
    f = _finite_float(s)                 # PY-04: inf/nan -> None (not a number)
    if f is not None:
        return f
    sign = -1.0 if s.startswith("-") else 1.0
    s = s.lstrip("+-").replace("\u00b0", " ").replace("d", " ") \
                      .replace("'", " ").replace('"', " ")
    parts = [p for p in re.split(r"[\s:]+", s) if p]
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if not vals or not all(math.isfinite(v) for v in vals):
        return None
    v = abs(vals[0])
    if len(vals) >= 2:
        v += vals[1] / 60.0
    if len(vals) >= 3:
        v += vals[2] / 3600.0
    return sign * v


def parse_date_obs(value) -> str | None:
    """DATE-OBS -> ISO 'YYYY-MM-DDTHH:MM:SS' or None (unparseable)."""
    if value is None:
        return None
    s = str(value).strip().split(".")[0]      # drop fractional seconds
    m = _DATE_ISO_RE.match(s)
    if m:
        return _fmt_iso(m.groups())
    m = _DATE_SLASH_RE.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        # ambiguous US/EU slashed dates: day>12 forces DD/MM, else MM/DD
        if a > 12:
            y, mo, d = m.group(3), b, a
        else:
            y, mo, d = m.group(3), a, b
        return _fmt_iso((y, mo, d, m.group(4), m.group(5), m.group(6)))
    return None


def _fmt_iso(groups) -> str:
    y, mo, d, hh, mm, ss = groups[0], groups[1], groups[2], \
        groups[3], groups[4], groups[5] or "00"
    return (f"{y}-{str(mo).zfill(2)}-{str(d).zfill(2)}"
            f"T{hh}:{mm}:{ss}")


def date_epoch(iso: str | None) -> float | None:
    """ISO string -> unix epoch (float), or None when missing/invalid."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def extract_object_tokens(text: str) -> list[str]:
    """Normalized (lowercase, no-space) candidate tokens from a file name or
    OBJECT header: 'M31' -> 'm31', 'NGC 7000' -> 'ngc7000', 'Andromeda' ->
    'andromeda'."""
    t = str(text or "").lower()
    out: list[str] = []
    for m in _DESIG_RE.finditer(t):
        tok = re.sub(r"\s+", "", m.group(1) + m.group(2))
        if tok not in out:
            out.append(tok)
    for m in _WORD_RE.finditer(t):
        tok = m.group(0)
        if tok in _STOPWORDS or tok in out:
            continue
        out.append(tok)
    # multi-word common names: join up to 3 consecutive words
    words = [w for w in out if re.fullmatch(r"[a-z]{4,}", w)]
    for span in (2, 3):
        for i in range(len(words) - span + 1):
            joined = "".join(words[i:i + span])
            if joined not in out:
                out.append(joined)
    return out


# ---------------------------------------------------------------- image data

def header_size(data: bytes) -> int:
    """Byte length of the primary header, padded to a 2880-byte block."""
    for blk in range(_MAX_BLOCKS):
        block = data[blk * _BLOCK: (blk + 1) * _BLOCK]
        if len(block) < _REC:
            return (blk + 1) * _BLOCK
        for i in range(0, _BLOCK, _REC):
            if _is_end_card(block[i:i + _REC].decode("latin-1", "replace")):
                return (blk + 1) * _BLOCK
    return _MAX_BLOCKS * _BLOCK


def image_geometry(header: dict) -> dict | None:
    """2D image layout from NAXIS/BITPIX, or None when not an image HDU."""
    naxis = header.get("NAXIS", 0)
    if not isinstance(naxis, int) or naxis < 2:
        return None
    n1 = header.get("NAXIS1")
    n2 = header.get("NAXIS2")
    bp = header.get("BITPIX")
    if not isinstance(n1, int) or not isinstance(n2, int):
        return None
    if n1 <= 0 or n2 <= 0 or bp not in (8, 16, 32, -32, -64):
        return None
    return {"bitpix": bp, "naxis1": n1, "naxis2": n2,
            "bps": {8: 1, 16: 2, 32: 4, -32: 4, -64: 8}[bp]}


def preview_png(data: bytes, max_px: int = 256) -> bytes | None:
    """Downsampled grayscale PNG of the primary image data (stdlib only)."""
    return preview_png_file(io.BytesIO(data), max_px)


def preview_png_file(f, max_px: int = 256) -> bytes | None:
    """Like preview_png but reads only the sampled bytes from an open binary
    file (seek per sample), so a 100 MB frame costs a handful of KB of I/O.
    Returns None for non-image HDUs or truncated data."""
    head = f.read(_MAX_BLOCKS * _BLOCK)
    geo = image_geometry(read_fits_header(head))
    if not geo:
        return None
    off = header_size(head)
    n1, n2, bps = geo["naxis1"], geo["naxis2"], geo["bps"]
    step = max(1, math.ceil(max(n1, n2) / max_px))
    w = (n1 + step - 1) // step
    h = (n2 + step - 1) // step
    bitpix = geo["bitpix"]
    is_float = bitpix < 0
    row_stride = n1 * bps
    vals: list[float] = []
    for y in range(0, n2, step):
        for x in range(0, n1, step):
            f.seek(off + y * row_stride + x * bps)
            raw = f.read(bps)
            if len(raw) < bps:
                return None
            if is_float:
                v = struct.unpack(">f", raw)[0] if bitpix == -32 \
                    else struct.unpack(">d", raw)[0]
                vals.append(v if math.isfinite(v) else 0.0)
            else:
                # FITS 8-bit pixels are unsigned; 16/32-bit are signed
                vals.append(float(int.from_bytes(
                    raw, "big", signed=bps > 1)))
    return _encode_png_gray(vals, w, h)


def _encode_png_gray(vals: list[float], w: int, h: int) -> bytes | None:
    """8-bit grayscale PNG via zlib+struct (1%/99% stretch, no palette)."""
    if not vals or w < 1 or h < 1:
        return None
    s = sorted(vals)
    lo = s[max(0, int(len(s) * 0.01) - 1)]
    hi = s[min(len(s) - 1, int(len(s) * 0.99))]
    if hi <= lo:
        hi = lo + 1e-9
    rows = []
    row = bytearray()
    for v in vals:
        b = int(round(255.0 * min(1.0, max(0.0, (v - lo) / (hi - lo)))))
        row.append(b)
        if len(row) == w:
            rows.append(b"\x00" + bytes(row))     # filter type 0 per row
            row = bytearray()
    raw = b"".join(rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        c = tag + payload
        return struct.pack(">I", len(payload)) + c + struct.pack(">I",
                                                                 zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))