"""Shared test helpers (not a test module - no test_ prefix)."""

_BLOCK = 2880


def make_fits(records, end=True) -> bytes:
    block = bytearray()
    for k, v in records:
        block += f"{k:<8}= {v}".encode("latin-1").ljust(80)[:80]
    if end:
        block += b"END".ljust(80)
    block += b" " * (-len(block) % _BLOCK)
    return bytes(block)


def make_fits_with_data(records, data: bytes) -> bytes:
    block = bytearray()
    for k, v in records:
        block += f"{k:<8}= {v}".encode("latin-1").ljust(80)[:80]
    block += b"END".ljust(80)
    block += b" " * (-len(block) % _BLOCK)
    return bytes(block) + data


def m31_fits(exposure: float = 300.0) -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'M 31                     ' / Andromeda"),
        ("EXPTIME", str(exposure)),
        ("DATE-OBS", "'2026-08-22T23:14:05'"),
        ("SITELAT", "50.0383"),
        ("SITELONG", "7.81667"),
        ("FILTER", "'L'"),
        ("TELESCOP", "'Seestar S50'"),
        ("NAXIS", "2"),
        ("RA", "0.712"),
        ("DEC", "41.269"),
    ])


def unresolved_fits() -> bytes:
    return make_fits([
        ("SIMPLE", "T"),
        ("OBJECT", "'TestObj42'"),
        ("EXPTIME", "60.0"),
        ("DATE-OBS", "'2026-08-22T22:00:00'"),
        ("SITELAT", "50.0383"),
        ("SITELONG", "7.81667"),
    ])
