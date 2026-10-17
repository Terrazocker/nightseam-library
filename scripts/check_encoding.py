"""Encoding guard: shipped text must be UTF-8 and free of double-encoded text.

0.6.9 committed web/app.js and web/horizon.js after a tool had read them as
cp1252 and written them back as UTF-8. Every non-ASCII character became the
classic mojibake cluster (the character's UTF-8 bytes rendered as cp1252
glyphs), so the live web app showed garbled em dashes, degree signs and emoji.
The ASCII-only character stream was byte-identical, which is exactly why nothing
else noticed: not node --check, not check_js_imports, not the suite.

This check therefore refuses:
  * a file that is not valid UTF-8 (the only encoding this repo uses),
  * a UTF-8 BOM (no shipped file has one; a new one means a tool rewrote it),
  * C1 control characters (U+0080-U+009F) - the undefined cp1252 slots that a
    mis-decoded file leaks into the text,
  * the cp1252 mojibake clusters those two accidents leave behind.

Usage: python scripts/check_encoding.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Never shipped text / generated / scratch (see .gitignore for the last few).
SKIP_TOP = {".git", ".venv-build", ".venv-browser", ".venv", ".pytest-tmp", ".pytest_cache",
            "__pycache__", "build", "data", "dist", "logs", "node_modules",
            "theme-preview", "whale", "dev",
            # TO-11 (0.6.24 review): the review workspace is NOT shipped text
            # (charter 1.1 keeps reviews/ outside the product). A BOM or a
            # UTF-16LE PowerShell capture written there by a review probe used to
            # turn the release JS guard red with a message about a scratch file.
            "reviews"}
EXTS = {".js", ".mjs", ".cjs", ".py", ".html", ".css", ".json", ".md", ".ps1",
        ".txt", ".spec", ".cfg", ".ini", ".yml", ".yaml", ".svg", ".csv"}
# Written by build.ps1 with a PowerShell BOM - not repo content.
BOM_OK = {"app/_version_baked.py"}

# cp1252's "special" slots: what a byte in 0x80-0x9F decodes to.
_CP1252_HIGH = ("\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030"
                "\u0160\u2039\u0152\u017d\u2018\u2019\u201c\u201d\u2022\u2013"
                "\u2014\u02dc\u2122\u0161\u203a\u0153\u017e\u0178")
# Lead bytes of UTF-8 sequences, as cp1252 renders them.
_LEADS = "\u00c2\u00c3\u00c5\u00e2\u00e6\u00f0\u00ef\u00fe"
# A mis-decode pairs a lead byte with a cp1252 high slot, or with a raw
# 0x80-0xBF position (which lands in U+0080-U+00BF).
_FOLLOWS = _CP1252_HIGH + "".join(chr(c) for c in range(0x80, 0xC0))
MARKERS = tuple(a + b for a in _LEADS for b in _FOLLOWS)
C1 = tuple(chr(c) for c in range(0x80, 0xA0))


def scan(root: Path = ROOT) -> list[tuple[Path, str]]:
    """Return [(relative path, reason)] for every suspect text file."""
    problems: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in SKIP_TOP:
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append((rel, "not valid UTF-8: " + str(exc)))
            continue
        if text.startswith("\ufeff"):
            if rel.as_posix() not in BOM_OK:
                problems.append((rel, "starts with a UTF-8 BOM"))
            text = text[1:]
        if any(m in text for m in MARKERS):
            problems.append((rel, "double-encoded text (cp1252 mojibake)"))
            continue
        if any(c in text for c in C1):
            problems.append((rel, "contains C1 control characters"))
    return problems


def main() -> int:
    problems = scan()
    for rel, reason in problems:
        print("  " + rel.as_posix() + ": " + reason)
    print("check_encoding:", "FAIL (%d file(s))" % len(problems) if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
