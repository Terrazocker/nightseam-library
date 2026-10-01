"""Tests for the pure-python FITS header reader and value parsers."""

import struct

from app import fits
from _util import make_fits, make_fits_with_data

_BLOCK = 2880


def test_reads_basic_keywords():
    data = make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'M 31                     ' / Andromeda"),
        ("EXPTIME", "300.0"),
        ("DATE-OBS", "'2026-08-22T23:14:05'"),
        ("FILTER", "'L'"),
        ("TELESCOP", "'Seestar S50'"),
        ("NAXIS", "2"),
        ("RA", "0.712"),
    ])
    hdr = fits.read_fits_header(data)
    assert hdr["SIMPLE"] is True
    assert hdr["OBJECT"] == "M 31"
    assert hdr["EXPTIME"] == 300.0
    assert hdr["DATE-OBS"] == "2026-08-22T23:14:05"
    assert hdr["FILTER"] == "L"
    assert hdr["TELESCOP"] == "Seestar S50"
    assert hdr["NAXIS"] == 2
    assert hdr["RA"] == 0.712


def test_escaped_quotes_in_strings():
    data = make_fits([("SIMPLE", "T"), ("OBJECT", "'O''Connell 1'")])
    assert fits.read_fits_header(data)["OBJECT"] == "O'Connell 1"


def test_multiblock_header():
    records = [("SIMPLE", "T")] + [(f"KEY{i}", f"{i}.5") for i in range(40)]
    hdr = fits.read_fits_header(make_fits(records))
    assert hdr["KEY39"] == 39.5


def test_not_fits_returns_empty():
    assert fits.read_fits_header(b"GIF89a not a fits file at all") == {}


def test_header_without_end_still_parses():
    data = make_fits([("SIMPLE", "T"), ("OBJECT", "'M 42'")], end=False)
    assert fits.read_fits_header(data)["OBJECT"] == "M 42"


def test_only_the_end_card_terminates_the_header():
    """PY-10 (review 0.6.32): the terminator test was a bare
    `rec.startswith("END")`, so ANY keyword whose name merely begins with END -
    a non-standard `ENDTIME` written by some capture software - truncated the
    header and every later card was dropped: EXPTIME, OBJECT, DATE-OBS, the
    coordinates. The frame then became unresolved and `undated`. The standard
    reserves the keyword `END` with no value."""
    data = make_fits([
        ("SIMPLE", "T"),
        ("ENDTIME", "12345"),
        ("OBJECT", "'M 31'"),
        ("EXPTIME", "300.0"),
    ])
    hdr = fits.read_fits_header(data)
    assert hdr["ENDTIME"] == 12345          # the card is read, not the terminator
    assert hdr["EXPTIME"] == 300.0
    assert hdr["OBJECT"] == "M 31"
    # and `header_size` must not stop at it either
    assert fits.header_size(data) == _BLOCK


def test_header_size_counts_blocks_for_an_end_prefixed_card():
    records = [("SIMPLE", "T"), ("ENDTIME", "1")] + \
        [(f"KEY{i}", f"{i}") for i in range(40)]
    data = make_fits(records)
    assert fits.header_size(data) == 2 * _BLOCK


# ---------------------------------------------------------------- parsers

def test_parse_ra_hours():
    assert fits.parse_ra_hours("00 42 44.3") == 42 / 60 + 44.3 / 3600
    assert fits.parse_ra_hours("5.575") == 5.575
    assert fits.parse_ra_hours("5,575") == 5.575   # comma decimal (locale)
    assert fits.parse_ra_hours("05:34:31.9") == 5 + 34 / 60 + 31.9 / 3600
    assert fits.parse_ra_hours(None) is None
    assert fits.parse_ra_hours("not a ra") is None


def test_parse_dec_deg():
    assert fits.parse_dec_deg("+41 16 09") == 41 + 16 / 60 + 9 / 3600
    assert fits.parse_dec_deg("-27.958") == -27.958
    assert fits.parse_dec_deg("-27,958") == -27.958   # comma decimal (locale)
    assert fits.parse_dec_deg("+22 01") == 22 + 1 / 60
    assert fits.parse_dec_deg(None) is None


# ------------------------------------------------------- PY-04 non-finite

def test_parse_value_rejects_non_finite_numbers():
    """PY-04 (review 0.6.24): a FITS card is untrusted input - 'Infinity',
    'nan' and an overflowing literal ('1e400' == inf) must come back as None
    (unparseable), never as a non-finite float. `int()` cannot produce one, so
    the numeric branch is the only source."""
    assert fits._parse_value("Infinity") is None
    assert fits._parse_value("-Infinity") is None
    assert fits._parse_value("nan") is None
    assert fits._parse_value("1e400") is None
    assert fits._parse_value("inf") is None
    # finite values (int and float) are untouched
    assert fits._parse_value("300.0") == 300.0
    assert fits._parse_value("2") == 2
    # a QUOTED 'Infinity' stays a string - no number is handed up the stack
    assert fits._parse_value("'Infinity'") == "Infinity"


def test_parsers_reject_non_finite_coordinates():
    """PY-04: `parse_ra_hours`/`parse_dec_deg` are the single coordinate
    parsers (used by ingest AND reindex). A non-finite input used to reach
    `math.sin()` in imglib.angular_dist and 500 the whole ingest request.

    TR-11 (0.6.32 review): the old trailing
    `assert not (isinstance(got, float) and not math.isfinite(got))` was
    entailed by `got is None` on the line above - a constant-true assertion.
    `is None` IS the contract (a non-finite value must never be handed up the
    stack), so the entailed line is gone."""
    for bad in ("Infinity", "-Infinity", "inf", "nan", "1e400", "  nan  ",
                float("inf"), float("nan")):
        for fn in (fits.parse_ra_hours, fits.parse_dec_deg):
            got = fn(bad)
            assert got is None, (fn.__name__, bad, got)
    # finite decimal/sexagesimal values are unchanged
    assert fits.parse_ra_hours("5.575") == 5.575
    assert fits.parse_dec_deg("-27.958") == -27.958
    assert fits.parse_ra_hours("00 42 44.3") == 42 / 60 + 44.3 / 3600
    assert fits.parse_dec_deg("+41 16 09") == 41 + 16 / 60 + 9 / 3600


def test_read_fits_header_non_finite_exptime_is_none():
    """The ingest path reads EXPTIME through read_fits_header: one '1e400'
    card used to become a stored `inf` (images + import_batches rows) and 500
    every later /api/library response."""
    hdr = fits.read_fits_header(make_fits([
        ("SIMPLE", "T"), ("OBJECT", "'M 31'"), ("EXPTIME", "1e400")]))
    assert hdr["EXPTIME"] is None
    hdr2 = fits.read_fits_header(make_fits([
        ("SIMPLE", "T"), ("EXPTIME", "300.0")]))
    assert hdr2["EXPTIME"] == 300.0


def test_parse_date_obs():
    assert fits.parse_date_obs("2026-08-22T23:14:05.123") == \
        "2026-08-22T23:14:05"
    assert fits.parse_date_obs("2026-08-22 23:14:05") == \
        "2026-08-22T23:14:05"
    assert fits.parse_date_obs("23/08/2026 22:00:00") == \
        "2026-08-23T22:00:00"          # day > 12 -> DD/MM/YYYY
    assert fits.parse_date_obs("08/22/2026 22:00:00") == \
        "2026-08-22T22:00:00"          # otherwise MM/DD/YYYY
    assert fits.parse_date_obs("nonsense") is None
    assert fits.parse_date_obs(None) is None


def test_date_epoch():
    assert fits.date_epoch("2026-08-22T23:14:05") is not None
    assert fits.date_epoch(None) is None
    assert fits.date_epoch("garbage") is None


# ---------------------------------------------------------------- tokens

def test_extract_object_tokens():
    assert "m31" in fits.extract_object_tokens("M31_300s_L_001.fit")
    assert "ngc7000" in fits.extract_object_tokens("NGC7000_Light_120s.fit")
    assert "ngc7000" in fits.extract_object_tokens("NGC 7000_Ha_120s.fit")
    assert "sh2-244" in fits.extract_object_tokens("SH2-244_60s.fit")
    assert "andromeda" in fits.extract_object_tokens("Andromeda_180s.fit")
    assert "crabnebula" in fits.extract_object_tokens("Crab Nebula_300s.fit")


# ---------------------------------------------------------------- previews

def test_header_size_and_geometry():
    hdr = make_fits([("SIMPLE", "T"), ("BITPIX", "16"), ("NAXIS", "2"),
                     ("NAXIS1", "4"), ("NAXIS2", "4")])
    assert fits.header_size(hdr) == _BLOCK
    geo = fits.image_geometry(fits.read_fits_header(hdr))
    assert geo == {"bitpix": 16, "naxis1": 4, "naxis2": 4, "bps": 2}
    assert fits.image_geometry({}) is None
    assert fits.image_geometry({"NAXIS": 1, "NAXIS1": 100}) is None
    assert fits.image_geometry({"NAXIS": 2, "NAXIS1": 0, "NAXIS2": 0}) is None


def test_preview_png_16bit():
    data = struct.pack(">16h", 100, 200, 300, 400, 500, 600, 700, 800,
                       900, 1000, 1100, 1200, 1300, 1400, 1500, 1600)
    fits_bytes = make_fits_with_data([
        ("SIMPLE", "T"), ("BITPIX", "16"), ("NAXIS", "2"),
        ("NAXIS1", "4"), ("NAXIS2", "4")], data)
    png = fits.preview_png(fits_bytes)
    assert png and png.startswith(b"\x89PNG\r\n\x1a\n")
    # IHDR: signature(8) + len(4) + "IHDR" -> width/height at offset 16
    assert struct.unpack(">II", png[16:24]) == (4, 4)


def test_preview_png_float():
    vals = struct.pack(">9f", 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0)
    fits_bytes = make_fits_with_data([
        ("SIMPLE", "T"), ("BITPIX", "-32"), ("NAXIS", "2"),
        ("NAXIS1", "3"), ("NAXIS2", "3")], vals)
    png = fits.preview_png(fits_bytes)
    assert png and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", png[16:24]) == (3, 3)


def test_preview_png_rejects_non_image():
    assert fits.preview_png(b"GIF89a not a fits file") is None
    hdr_only = make_fits([("SIMPLE", "T"), ("NAXIS", "0")])
    assert fits.preview_png(hdr_only) is None


def test_preview_png_8bit_unsigned():
    # FITS 8-bit pixels are UNSIGNED (128..255 must not flip negative)
    fits_bytes = make_fits_with_data([
        ("SIMPLE", "T"), ("BITPIX", "8"), ("NAXIS", "2"),
        ("NAXIS1", "2"), ("NAXIS2", "2")],
        bytes([0, 128, 255, 10]))
    png = fits.preview_png(fits_bytes)
    assert png and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", png[16:24]) == (2, 2)


def test_preview_png_downsamples():
    vals = struct.pack(">400h", *range(400))
    fits_bytes = make_fits_with_data([
        ("SIMPLE", "T"), ("BITPIX", "16"), ("NAXIS", "2"),
        ("NAXIS1", "20"), ("NAXIS2", "20")], vals)
    png = fits.preview_png(fits_bytes, max_px=10)
    assert struct.unpack(">II", png[16:24]) == (10, 10)
    # stopwords / junk are not emitted as name candidates
    for bad in ("light", "300s", "fits", "dark", "frame"):
        assert bad not in fits.extract_object_tokens(f"{bad}_M31.fit")