"""The committed supplement (`app/data/supplement.csv`).

The catalogue is OpenNGC plus this one small, documented exception set: sixteen
objects OpenNGC either lacks, files as non-objects, or cannot carry at all
because they sit outside its scope. The tests here pin what makes that
acceptable - the rows are well formed, they are labelled with the project's own
types, they carry NO editorial text and NO invented magnitudes, their alternate
designations are searchable, and they are the only rows in the shipped CSV that
the OpenNGC pipeline cannot produce.
"""

import csv
import importlib.util
import re
from pathlib import Path

import pytest

from app.catalog_data import TYPE_LABELS
from app.database import Database

ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT = ROOT / "app" / "data" / "supplement.csv"
CATALOG = ROOT / "app" / "data" / "openned.csv"
COLS = ("id", "primary_name", "messier", "ngcic", "other", "otype", "ra_hours",
        "dec_deg", "vmag", "size_am", "constellation", "wiki", "blurb", "search")

# The exception set, by name - adding a seventeenth object must be a deliberate
# edit here (and in the copy that advertises the count).
EXPECTED = {
    # batch 1 (0.6.28): OpenNGC lacks them, or files them as non-objects
    "m40": ("Winnecke 4", "asterism"),
    "m73": ("M73", "asterism"),
    "mel20": ("Alpha Persei Cluster", "open_cluster"),
    "ic1318": ("Gamma Cygni Nebula (Sadr Region)", "nebula"),
    "sh2-276": ("Barnard's Loop", "nebula"),
    "abell21": ("Medusa Nebula", "planetary"),
    "jones1": ("Headphone Nebula", "planetary"),
    # batch 2 (0.6.29): the popular targets OUTSIDE OpenNGC's scope
    "sh2-101": ("Tulip Nebula", "nebula"),
    "sh2-129": ("Flying Bat Nebula", "nebula"),
    "sh2-132": ("Lion Nebula", "nebula"),
    "sh2-240": ("Simeis 147 (Spaghetti Nebula)", "supernova_remnant"),
    "sh2-308": ("Dolphin Head Nebula", "nebula"),
    "b150": ("Seahorse Nebula", "nebula"),
    "lbn437": ("LBN 437", "nebula"),
    "ldn1622": ("LDN 1622", "nebula"),
    "pipe": ("Pipe Nebula", "nebula"),
}
BATCH2 = ("sh2-101", "sh2-129", "sh2-132", "sh2-240", "sh2-308",
          "b150", "lbn437", "ldn1622", "pipe")


def _rows(path=CATALOG):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            vals = line.rstrip("\n").split(";")
            assert len(vals) == len(COLS), line[:40]
            yield dict(zip(COLS, vals))


def test_supplement_file_is_wellformed():
    rows = list(_rows(SUPPLEMENT))
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == set(EXPECTED), "additions must be deliberate"
    for oid, (name, otype) in EXPECTED.items():
        r = by_id[oid]
        assert r["primary_name"] == name, oid
        assert r["otype"] == otype, oid
        assert r["otype"] in TYPE_LABELS, oid
        assert 0 <= float(r["ra_hours"]) < 24, oid
        assert -90 <= float(r["dec_deg"]) <= 90, oid
        if r["vmag"]:
            float(r["vmag"])


def test_supplement_rows_carry_no_editorial_text():
    """The catalogue has no prose (the 0.6.26 decision): the supplement must not
    smuggle the old curated blurbs/distance/wiki titles back in."""
    for r in _rows(SUPPLEMENT):
        assert not (r["wiki"] or r["blurb"]), r["id"]


def test_extended_nebulae_carry_no_invented_magnitude():
    """The 0.6.29 magnitude policy: an extended or dark nebula has no credible
    integrated V magnitude, so batch 2 leaves `vmag` EMPTY instead of inventing a
    proxy - the same shape the shipped addendum uses for `b33` (Horsehead), `c9`
    and `c99`. The consequence, pinned so nobody "fixes" it by fabricating a
    number: per `magVisible`, a row with no magnitude appears only once the
    magnitude slider is at its maximum, never at a brighter limit."""
    rows = {r["id"]: r for r in _rows(SUPPLEMENT)}
    for oid in BATCH2:
        assert rows[oid]["vmag"] == "", oid
        assert rows[oid]["size_am"] != "" or oid == "ldn1622", oid
        assert rows[oid]["constellation"], oid


def test_batch2_rows_are_findable_by_their_alternate_designations():
    """The id carries ONE designation; `other`/`search` must carry the rest, or a
    user typing the name they know ("Squid", "LDN 1773", "Seahorse") finds
    nothing."""
    shipped = {r["id"]: r for r in _rows()}
    for oid, needles in (
            ("sh2-101", ("sh 2-101", "tulip")),
            ("sh2-129", ("squid", "lbn 428")),
            ("sh2-132", ("lion nebula", "lbn 473")),
            ("sh2-240", ("simeis 147", "snr g180.0-01.7")),
            ("sh2-308", ("rcw 11", "dolphin")),
            ("b150", ("barnard 150", "ldn 1082")),
            ("lbn437", ("dg 187",)),
            ("ldn1622", ("lynds 1622",)),
            ("pipe", ("ldn 1773", "barnard 78"))):
        blob = shipped[oid]["search"].lower()
        for needle in needles:
            assert needle in blob, (oid, needle)


def test_ids_do_not_collide_when_normalized():
    """Name care (the 0.6.29 rule). The app's search normalization lowercases and
    drops spaces/hyphens, so an id `m2-9` would collide with `m29` = NGC 6913 and
    one query would answer with two different objects (Minkowski's Butterfly is
    exactly that trap, and is why it is NOT usable as an id). Asserted over the
    WHOLE shipped catalogue - ids AND Messier numbers of every row - so a future
    batch cannot introduce the collision from either side."""
    def key(s: str) -> str:
        s = re.sub(r"[^a-z0-9]", "", (s or "").lower())
        return re.sub(r"(?<![0-9])0+(?=\d)", "", s)

    owner: dict[str, str] = {}
    for r in _rows():
        for raw in (r["id"], r["messier"]):
            k = key(raw)
            if not k:
                continue
            seen = owner.setdefault(k, r["id"])
            assert seen == r["id"], (
                f"{raw!r} normalizes to {k!r}, which already names {seen!r}")


def test_supplement_rows_are_shipped_and_searchable():
    shipped = {r["id"]: r for r in _rows()}
    for oid in EXPECTED:
        assert oid in shipped, oid
        assert not (shipped[oid]["wiki"]
                    or shipped[oid]["blurb"]), oid
        blob = shipped[oid]["search"].lower()
        assert oid in blob, oid
    # the two Messier numbers OpenNGC cannot represent are searchable by number
    assert "m40" in shipped["m40"]["search"].lower()
    assert "m73" in shipped["m73"]["search"].lower()
    assert shipped["sh2-276"]["primary_name"] == "Barnard's Loop"


def test_generator_reads_the_supplement_and_keeps_ids_unique():
    spec = importlib.util.spec_from_file_location(
        "build_adv_catalog", ROOT / "scripts" / "build_adv_catalog.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    rows = gen.read_supplement()
    assert {r["id"] for r in rows} == set(EXPECTED)
    shipped = {r["id"] for r in _rows()}
    for r in rows:
        assert r["id"] in shipped


def test_supplement_objects_are_unscored_or_scored_like_any_other(tmp_path):
    """They take part in the normal rules: a popularity score if the source has
    one (then Standard mode may show them), nothing invented if it does not. The
    SCORES themselves are never pinned - a live refresh moves them (see
    `AGENTS.md`); the ranking identity is `tests/test_catalog.py`'s business."""
    from app.popularity_data import POPULARITY
    db = Database(tmp_path / "t.db")
    for oid in EXPECTED:
        row = db.get_object(oid)
        assert row is not None, oid
        assert db.get_object(oid)["popularity"] == POPULARITY.get(oid, 0)
    assert POPULARITY.get("ic1318") and POPULARITY.get("sh2-276")
    # all nine batch-2 rows are known to the popularity source, which is what lets the
    # standard-mode rule pick them up on its own
    for oid in BATCH2:
        assert POPULARITY.get(oid, 0) > 0, oid
