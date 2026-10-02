"""Cross-check: every $('#id') referenced by an app's JS must exist in its HTML.

Covers the local catalog and retained maintenance planner shells.
The desktop shells share archive/map modules, so their shell-specific IDs are
checked against each page separately.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]

APPS = [
    # (label, html file, intentional shell-specific omissions, unreachable import edges)
    ("desktop", REPO / "static" / "index.html",
     {"frameTypeHelp", "skymapDecAxis", "skymapPlot", "skymapRaAxis"}, set()),
    ("catalog", REPO / "static" / "catalog.html",
     # The source planner's Import Exposures panel retains its global stats.
     {"edgeSpace", "edgeSpaceVal", "sizeUnit", "archiveStats"},
     # skymap.js imports the planner inspector only in its non-catalog branch.
     # The catalog browser gate also forbids any request for this module.
     {("js/skymap.js", "js/detail.js")}),
]

# ids that only exist at runtime (created by JS template literals) or that are
# looked up defensively with `if (el)`; keep the exceptions explicit here
IGNORED = {
    "err-banner", "splash-text", "toasts",
}

JS_ID_RE = re.compile(r"""\$\(\s*["'#]#?([\w-]+)["']?\s*\)""")
HTML_ID_RE = re.compile(r"""\bid=["']([\w-]+)["']""")
IMPORT_RE = re.compile(
    r"""\bimport\s*(?:[\s\S]*?\s*from\s*)?["']([^"']+)["']"""
    r"""|\bimport\(\s*["']([^"']+)["']\s*\)""", re.M)
EXPORT_FROM_RE = re.compile(
    r"""\bexport\s*(?:\*|\{[^}]*\})\s*from\s*["']([^"']+)["']""", re.M)


class _ModuleScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "module" and attrs.get("src"):
            self.sources.append(attrs["src"])


def discover_modules(html_path: Path,
                     unreachable: set[tuple[str, str]] | None = None) -> list[Path]:
    """Follow each page's module entries, imports, dynamic imports and re-exports.

    Explicit unreachable edges describe a shell branch, never a filename allowlist
    of everything currently loaded. New transitive modules are checked automatically.
    """
    parser = _ModuleScripts()
    parser.feed(html_path.read_text(encoding="utf-8"))
    root = html_path.parent.resolve()
    excluded = {((root / source).resolve(), (root / target).resolve())
                for source, target in (unreachable or set())}
    pending = []
    for src in parser.sources:
        url = urlsplit(src)
        if url.scheme or url.netloc:
            continue
        pending.append((None, (root / url.path.lstrip("/")).resolve()))
    seen: set[Path] = set()
    while pending:
        source, path = pending.pop()
        if path in seen or (source, path) in excluded:
            continue
        if not path.is_file():
            raise ValueError(f"module file missing: {path}")
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        specs = [m.group(1) or m.group(2) for m in IMPORT_RE.finditer(text)]
        specs.extend(m.group(1) for m in EXPORT_FROM_RE.finditer(text))
        for spec in specs:
            if spec.startswith(("./", "../", "/")):
                clean = urlsplit(spec).path
                target = ((root if clean.startswith("/") else path.parent)
                          / clean.lstrip("/")).resolve()
                pending.append((path, target))
    return sorted(seen)


def check(label: str, html_path: Path, js_files: list[Path],
          ignored_ids: set[str] | None = None) -> int:
    if not html_path.is_file():
        print(f"{label}: no {html_path.name} - skipped")
        return 0
    ignored_ids = IGNORED if ignored_ids is None else ignored_ids
    html_ids = set(HTML_ID_RE.findall(html_path.read_text(encoding="utf-8")))

    js_ids: dict[str, list[str]] = {}
    dynamic_ids: set[str] = set()
    for f in js_files:
        text = f.read_text(encoding="utf-8")
        for m in JS_ID_RE.finditer(text):
            js_ids.setdefault(m.group(1), []).append(f.name)
        for m in re.finditer(r"""id=["']([\w-]+)["']""", text):
            dynamic_ids.add(m.group(1))

    missing = {i: fs for i, fs in js_ids.items()
               if i not in html_ids and i not in dynamic_ids and i not in ignored_ids}
    if missing:
        print(f"{label}: JS references ids MISSING from {html_path.name}:")
        for i, fs in sorted(missing.items()):
            print(f"  #{i}  <- {', '.join(sorted(set(fs)))}")
    else:
        print(f"{label}: all JS-referenced element ids exist in {html_path.name}")
    print(f"  ({len(html_ids)} ids in html, {len(js_ids)} referenced from js)")
    return 1 if missing else 0


def main() -> int:
    errors = 0
    for label, html_path, ignored_ids, unreachable in APPS:
        try:
            js_files = discover_modules(html_path, unreachable)
        except (OSError, ValueError) as exc:
            print(f"{label}: {exc}")
            errors += 1
            continue
        errors += check(label, html_path, js_files, ignored_ids | IGNORED)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
