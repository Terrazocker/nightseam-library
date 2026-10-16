"""Small standard-library FITS fixtures, independently decoded in source tests."""
import gzip
import struct


def _header(cards):
    records = []
    for key, value in cards:
        if isinstance(value, str):
            raw = "'" + value.replace("'", "''") + "'"
        else:
            raw = ("T" if value is True else "F" if value is False else str(value)).rjust(20)
        records.append(f"{key:<8}= {raw}".ljust(80))
    return _padded(("".join(records) + "END".ljust(80)).encode("ascii"), b" ")


def _padded(data, fill=b"\0"):
    return data + fill * (-len(data) % 2880)


def compressed_gate_image(tiled):
    """An 8x8 int16 primary image or one GZIP_1 tile in a FITS binary table."""
    metadata = [("OBJECT", "Compressed gate capture"), ("EXPTIME", 75.5),
        ("DATE-OBS", "2026-09-15T22:14:05"), ("IMAGETYP", "LIGHT"), ("FILTER", "Ha"),
        ("INSTRUME", "Compressed gate camera"), ("SITELAT", 52.52), ("SITELONG", 13.405)]
    pixels = struct.pack(">64h", *range(64))
    if not tiled:
        return _header([("SIMPLE", True), ("BITPIX", 16), ("NAXIS", 2),
                        ("NAXIS1", 8), ("NAXIS2", 8), *metadata]) + _padded(pixels)
    tile = gzip.compress(pixels, mtime=0)
    primary = _header([("SIMPLE", True), ("BITPIX", 8), ("NAXIS", 0), ("EXTEND", True)])
    extension = _header([("XTENSION", "BINTABLE"), ("BITPIX", 8), ("NAXIS", 2),
        ("NAXIS1", 8), ("NAXIS2", 1), ("PCOUNT", len(tile)), ("GCOUNT", 1),
        ("TFIELDS", 1), ("TTYPE1", "COMPRESSED_DATA"), ("TFORM1", "1PB"),
        ("ZIMAGE", True), ("ZBITPIX", 16), ("ZNAXIS", 2), ("ZNAXIS1", 8),
        ("ZNAXIS2", 8), ("ZTILE1", 8), ("ZTILE2", 8), ("ZCMPTYPE", "GZIP_1"), *metadata])
    return primary + extension + _padded(struct.pack(">ii", len(tile), 0) + tile)
