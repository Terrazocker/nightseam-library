"""Tests for the untrusted-path helpers in app/paths.py.

`safe_rel`/`safe_join` are the security boundary for every path that arrives
from stored or imported JSON (a setup ZIP's `panoramas[].file` /
`images[].file`). They must reject absolute, drive/UNC, `..` and NUL inputs on
any platform, normalize what they accept, and refuse a symlink that points out
of the target folder.
"""

import pytest

from app.paths import avoid_windows_device, safe_join, safe_rel


@pytest.mark.parametrize("value", [
    "../escape.jpg",
    "..\\escape.jpg",                  # Windows separator, must not slip through
    "sub/../../escape.jpg",
    "a\\..\\..\\escape.jpg",
    "/etc/passwd",                     # absolute POSIX
    "//server/share/secret.jpg",       # UNC
    "C:/Windows/win.ini",              # drive-absolute
    "C:win.ini",                       # drive-relative
    "c:\\Windows\\win.ini",
    "",
    "   ",
    "..",
    "sub/..",
    "nul\x00byte.jpg",
    None,
])
def test_safe_rel_rejects_unsafe_values(value):
    assert safe_rel(value) is None


@pytest.mark.parametrize("value,expected", [
    ("pano1.jpg", "pano1.jpg"),
    ("sub/dir/img.fit", "sub/dir/img.fit"),
    ("sub\\dir\\img.fit", "sub/dir/img.fit"),      # normalized to forward slashes
    ("./pano1.jpg", "pano1.jpg"),                  # "." segments dropped
    ("sub//pano1.jpg", "sub/pano1.jpg"),           # duplicate separators dropped
    ("M 31/2026-08-22_2314/lights/m31.fit",
     "M 31/2026-08-22_2314/lights/m31.fit"),       # spaces survive
    ("..fit", "..fit"),                            # a dot-name is not traversal
])
def test_safe_rel_accepts_and_normalizes(value, expected):
    assert safe_rel(value) == expected


@pytest.mark.parametrize("value", [
    "a\x01b.jpg",            # C0 control - Windows: OSError 22 at write time
    "a\nb.jpg",
    "a\tb.jpg",
    "a\x1bb.jpg",
    "sub/\x7fdel.jpg",       # DEL
    "\x0bx.jpg",
])
def test_safe_rel_rejects_control_characters(value):
    """SP-05 (review 0.6.32): `safe_rel` rejected NUL, `..`, absolutes and
    device names but let every OTHER control character through, and Windows
    refuses those at write time (`OSError: [Errno 22] Invalid argument`). A
    crafted bundle therefore stored a row that could never resolve AND aborted
    the restore with a 500 after the database had been replaced."""
    assert safe_rel(value) is None


def test_safe_join_keeps_legitimate_paths_inside(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "pano1.jpg").write_bytes(b"jpeg")

    assert safe_join(root, "pano1.jpg") == (root / "pano1.jpg").resolve()
    assert safe_join(root, "sub/img.fit") == (root / "sub" / "img.fit").resolve()


def test_safe_join_rejects_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("outside")

    for value in ("../secret.txt", "..\\secret.txt", "/etc/passwd",
                  "C:/Windows/win.ini", str(tmp_path / "secret.txt")):
        assert safe_join(root, value) is None


def test_safe_join_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")
    # the textual path is harmless ("link/secret.txt") but resolves outside
    assert safe_rel("link/secret.txt") == "link/secret.txt"
    assert safe_join(root, "link/secret.txt") is None


@pytest.mark.parametrize("value,expected", [
    ("NUL", "NUL_"),
    ("com1", "com1_"),
    ("con.fits", "con_.fits"),
    ("aux.light.fit", "aux_.light.fit"),
    ("M 31", "M 31"),
    ("console.fits", "console.fits"),
    ("nul_.fits", "nul_.fits"),
])
def test_avoid_windows_device_produces_storable_segments(value, expected):
    assert avoid_windows_device(value) == expected
    assert safe_rel(expected) == expected
