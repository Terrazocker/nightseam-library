"""FITS header values and astronomy metadata parsing.

The tolerant primary-header parser is retained for legacy files. File traversal
and gzip metadata reads live in fits_metadata; exposures are never rewritten.
"""

from __future__ import annotations

import io
import math
import re
from datetime import UTC, datetime

__all__ = [
    "read_fits_header", "parse_ra_hours", "parse_dec_deg", "parse_date_obs",
    "date_epoch", "parse_site_coordinates", "capture_date_obs", "extract_object_tokens", "header_size", "image_geometry",
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
    for index, rec in enumerate(records):
        if rec.startswith(("COMMENT", "HISTORY")) or not rec.strip():
            continue
        m = _KEYWORD_RE.match(rec)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        value = _parse_value(raw)
        # Standard long strings can span CONTINUE cards (without an equals
        # sign). Only remove an ampersand when a quoted continuation follows.
        if isinstance(value, str) and value.endswith("&"):
            parts = [value]
            following = index + 1
            while parts[-1].endswith("&") and following < len(records):
                continuation = records[following]
                if continuation[:8].strip() != "CONTINUE":
                    break
                text = continuation[8:].strip()
                if not text.startswith("'"):
                    break
                fragment = _parse_value(text)
                if not isinstance(fragment, str):
                    break
                parts.append(fragment)
                following += 1
            value = "".join(part[:-1] for part in parts[:-1]) + parts[-1]
        out.setdefault(key, value)
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
    """Normalize a capture instant to UTC, retaining fractional seconds.

    FITS timestamps without an offset are UTC. Ambiguous slash dates are
    unresolved rather than guessed; use ISO metadata from the capture device.
    The stored ISO text is naive by convention but always denotes UTC.
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = _DATE_SLASH_RE.fullmatch(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a <= 12 and b <= 12:
            return None
        if a > 12 and b <= 12:
            y, mo, d = m.group(3), b, a
        else:
            y, mo, d = m.group(3), a, b
        s = _fmt_iso((y, mo, d, m.group(4), m.group(5), m.group(6)))
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", s):
        return None
    try:
        stamp = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).replace(tzinfo=None).isoformat(
            timespec="microseconds" if stamp.microsecond else "seconds")
    except (ValueError, OverflowError):
        return None


def _fmt_iso(groups) -> str:
    y, mo, d, hh, mm, ss = groups[0], groups[1], groups[2], \
        groups[3], groups[4], groups[5] or "00"
    return (f"{y}-{str(mo).zfill(2)}-{str(d).zfill(2)}"
            f"T{hh}:{mm}:{ss}")


def capture_date_obs(header: dict) -> str | None:
    """Only actual capture metadata in UTC may determine an observing session.

    FITS defaults an absent TIMESYS to UTC. Other explicit scales need a real
    conversion, so remain unresolved. DATE denotes file creation, not capture.
    """
    scale = header.get("TIMESYS")
    if scale is not None and str(scale).strip().upper() != "UTC":
        return None
    return parse_date_obs(header.get("DATE-OBS"))


def date_epoch(iso: str | None) -> float | None:
    """UTC ISO capture -> epoch; naive FITS values never use the host zone."""
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def parse_site_coordinates(header: dict) -> tuple[float | None, float | None]:
    """Read complete finite observing coordinates; longitude is east-positive.

    SITELAT/SITELONG are provided by the inspected Seestar captures. Decimal
    degrees and signed degrees:minutes:seconds are accepted. Incomplete or
    invalid metadata remains unresolved instead of inventing a location.
    """
    def coordinate(value, limit):
        if value is None or isinstance(value, bool):
            return None
        try:
            text = str(value).strip()
            if ":" in text:
                parts = text.lstrip("+-").split(":")
                if len(parts) not in (2, 3):
                    return None
                degrees, minutes = float(parts[0]), float(parts[1])
                seconds = float(parts[2]) if len(parts) == 3 else 0.0
                if degrees < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                    return None
                number = (-1 if text.startswith("-") else 1) * (degrees + minutes / 60 + seconds / 3600)
            else:
                number = float(text)
            return number if math.isfinite(number) and abs(number) <= limit else None
        except (ValueError, TypeError, OverflowError):
            return None
    lat = coordinate(header.get("SITELAT"), 90)
    lon = coordinate(header.get("SITELONG"), 180)
    return (lat, lon) if lat is not None and lon is not None else (None, None)


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
