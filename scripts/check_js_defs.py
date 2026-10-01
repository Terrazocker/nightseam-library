"""Heuristic check: find function-style identifiers used inside static/js/*.js
that are neither defined nor imported in that file (would throw at runtime).

Deliberately rough - it reports candidates for manual review, not hard errors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "static" / "js"

BUILTINS = {
    # browser / JS
    "document", "window", "navigator", "console", "fetch", "alert", "confirm",
    "prompt", "setTimeout", "clearTimeout", "setInterval", "clearInterval",
    "requestAnimationFrame", "Blob", "URL", "FormData", "File",
    "CustomEvent", "Event", "AbortController", "Math", "JSON", "Date",
    "Number", "String", "Boolean", "Array", "Object", "isNaN", "parseFloat",
    "parseInt", "Error", "TypeError", "Set", "Map", "Promise", "location",
    "history", "getComputedStyle", "localStorage", "structuredClone",
    "Image", "undefined", "null", "true", "false", "this", "super",
    "encodeURIComponent", "decodeURIComponent", "if", "for", "while",
    "return", "new", "typeof", "await", "async", "catch", "switch", "case",
    "function", "export", "import", "const", "let", "var", "class",
    # third-party-ish
    "test", "URLSearchParams",
}

DECL_RE = re.compile(r"\b(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
IMPORT_RE = re.compile(r"""import\s*\{([^}]+)\}\s*from|import\s+(\w+)\s+from""")
CALL_RE = re.compile(r"(?<![\w.$.'\"])([A-Za-z_$][\w$]{2,})\s*\(")


def known_names(text: str) -> set:
    names = set(BUILTINS)
    for m in DECL_RE.finditer(text):
        names.add(m.group(1))
    for m in IMPORT_RE.finditer(text):
        if m.group(1):
            for part in m.group(1).split(","):
                piece = part.strip().split(" as ")
                if piece:
                    names.add(piece[-1].strip())
        if m.group(2):
            names.add(m.group(2))
    return names


def main() -> int:
    files = sorted(JS_DIR.glob("*.js"))
    problems = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        # strip strings and comments roughly to avoid false hits
        stripped = re.sub(r"`[^`]*`", "", text)
        stripped = re.sub(r'"[^"\n]*"', "", stripped)
        stripped = re.sub(r"'[^'\n]*'", "", stripped)
        stripped = re.sub(r"//[^\n]*", "", stripped)
        stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)
        names = known_names(stripped)
        seen = set()
        for m in CALL_RE.finditer(stripped):
            ident = m.group(1)
            if ident in names or ident in seen:
                continue
            seen.add(ident)
            line = stripped[:m.start()].count("\n") + 1
            print(f"{f.name}:{line}: possibly undefined '{ident}()'")
            problems += 1
    print(f"done, {problems} candidate(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
