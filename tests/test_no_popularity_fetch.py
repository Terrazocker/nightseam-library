"""NightSeam must never fetch popularity data.

The community popularity data is snapshotted ONCE (see
`data/popularity_snapshot.meta.json`) and shipped inside `web/catalog.json`; every
shell reads the baked `o.popularity` field. There is no live fetch anywhere in
the product, on any platform, and this module pins that so it cannot come back
unnoticed.

The same rule is enforced at runtime by the browser gate: `tests/browser/
harness.py::_install_stubs` installs a TRIPWIRE route for every external host
and `Ctx.assert_clean()` fails any check that requests it, on both shells.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Every file that ships or runs as part of the product.
SHIPPED_GLOBS = [
    "web/*.js",
    "web/*.html",
    "web/core/**/*.js",
    "web/core/**/*.json",
    "static/index.html",
    "static/js/**/*.js",
    "core/**/*.js",
    "app/*.py",
    "app/data/*.py",
]
EXTERNAL_URL = re.compile(r"https?://(?!(?:127\.0\.0\.1|localhost)\b)[A-Za-z0-9.-]+")


def shipped_files() -> list[Path]:
    out: list[Path] = []
    for pattern in SHIPPED_GLOBS:
        out.extend(p for p in ROOT.glob(pattern)
                   if p.is_file() and "node_modules" not in p.parts)
    return sorted(out)


def test_no_shipped_file_reaches_an_external_host():
    """No external URL may appear in any shipped runtime file, because a URL is
    what a fetch needs.
    """
    offenders = []
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if EXTERNAL_URL.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not offenders, (
        "a shipped file references an external URL - the popularity "
        "snapshot must be baked into the catalog instead:\n  " + "\n  ".join(offenders))


def test_web_has_no_live_popularity_fetch():
    """The web shell reads only the baked field; the fetch machinery is gone."""
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for gone in ("loadPopularity", "_popMap", "popKey"):
        assert gone not in app, f"web/app.js still contains the live-fetch helper {gone!r}"
    assert "function popularityOf(o)" in app, "popularityOf() disappeared"
    assert "o.popularity" in app, "popularityOf() no longer reads the baked field"


def test_matcher_has_no_network_path_at_all():
    """`scripts/match_popularity.py` must be UNABLE to fetch.

    The user's standing rule: the snapshot is frozen and used going forward, with
    no new fetch on any platform. The `--live` refresh flag was therefore REMOVED
    (not merely defaulted off), along with the whole network machinery - so there
    is no code path, no flag and no import that can fetch it again.
    A stale caller passing `--live` must be told, not silently ignored.
    """
    src = (ROOT / "scripts" / "match_popularity.py").read_text(encoding="utf-8")
    for gone in ("urllib", "urlopen", "ssl.", "SNAPSHOT_URL", "fetch_snapshot"):
        assert gone not in src, (
            f"the matcher still contains network machinery: {gone!r}")
    assert "https://" not in src and "http://" not in src, (
        "the matcher still names a URL")
    assert '"--live" in argv' in src, (
        "the removed --live flag must still be recognised so it can be refused")
    assert "sys.exit(2)" in src, (
        "asking for --live must FAIL loudly instead of being ignored")


def test_snapshot_provenance_is_recorded():
    """If the snapshot is present, its recorded provenance must match the file."""
    snap = ROOT / "data" / "popularity_snapshot.json"
    meta = ROOT / "data" / "popularity_snapshot.meta.json"
    if not snap.is_file():
        pytest.skip("no local popularity snapshot on this machine (data/ is gitignored)")
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert len(data) > 1000, f"snapshot looks truncated: {len(data)} entries"
    if meta.is_file():
        recorded = json.loads(meta.read_text(encoding="utf-8"))
        digest = hashlib.sha256(snap.read_bytes()).hexdigest()
        assert recorded["sha256"] == digest, (
            "the popularity snapshot changed after its provenance was recorded. "
            "There is NO download path any more: restore the frozen snapshot from "
            "a backup (or re-record the provenance deliberately if the change was "
            "intended) and refresh data/popularity_snapshot.meta.json")
        assert recorded["entries"] == len(data)
