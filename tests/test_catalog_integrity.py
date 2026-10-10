"""Catalog identity integrity.

The catalog IS OpenNGC (`app/data/openned.csv`, built from `NGC.csv` +
`addendum.csv`) - there is no hand-curated list any more. Two identity rules are
pinned here:

  * an `id` is unique across the curated-era tables (now `objects` = the
    standard-mode selection, `advanced_objects` = the rest) TOGETHER, not just
    within each one, because the web `byId` map and `db.get_object()` must agree
    about which object an id names. The 0.6.11 catalog shipped 12,116 rows with
    only 12,114 unique ids: the curated composites `ngc2237` (Rosette) and
    `ngc7317` (Stephan's Quintet) each collided with an advanced member.
  * every ADVERTISED catalog size equals the shipped rows. The copy kept saying
    12,116 for two releases after the dedup had made it 12,114, and no test,
    guard or release check noticed.
"""

import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.catalog_data import STANDARD_TOP_N, standard_ids
from app.database import Database

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "app" / "data" / "openned.csv"
TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _gen():
    spec = importlib.util.spec_from_file_location(
        "build_adv_catalog", ROOT / "scripts" / "build_adv_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CSV_COLS = ("id", "primary_name", "messier", "ngcic", "other", "otype",
            "ra_hours", "dec_deg", "vmag", "size_am", "constellation",
            "wiki", "blurb", "search")


def _adv_rows():
    with open(CSV, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            vals = line.rstrip("\n").split(";")
            assert len(vals) == len(CSV_COLS), line[:40]
            yield dict(zip(CSV_COLS, vals))


# ------------------------------------------------------------------ generator

def test_generator_global_uniqueness_guard_raises_on_collision():
    gen = _gen()
    with pytest.raises(SystemExit):
        gen._assert_global_unique([{"id": "ngc1976"}, {"id": "ngc1976"}])
    gen._assert_global_unique([{"id": "ngc1976"}, {"id": "ngc224"}])   # no raise


def test_generator_formats_openngc_designations():
    gen = _gen()
    assert gen.fmt_desig("NGC0224") == "NGC 224"
    assert gen.fmt_desig("IC0001") == "IC 1"
    assert gen.fmt_desig("ESO056-115") == "ESO 56-115"
    assert gen.fmt_desig("Mel071") == "Mel 71"


# --------------------------------------------------------------------- data

def test_catalog_ids_unique_and_token_safe():
    ids = [r["id"] for r in _adv_rows()]
    assert len(ids) == len(set(ids))
    for i in ids:
        assert TOKEN.fullmatch(i), i


def test_openngc_objects_are_not_duplicated_by_designation():
    """NGC 651 is OpenNGC's `Dup` row for NGC 650 and M40 is a `**` double star:
    both OpenNGC rows are dropped, so a designation is not shipped twice. (M40 is
    still in the catalogue - from the supplement, which carries no NGC/IC
    designation for it, hence the empty values here are skipped.)"""
    ids = {r["id"] for r in _adv_rows()}
    ngcic = [r["ngcic"] for r in _adv_rows() if r["ngcic"]]
    assert len(ngcic) == len(set(ngcic))
    assert "ngc650" in ids and "ngc651" not in ids


def test_rows_wellformed():
    for r in _adv_rows():
        assert r["primary_name"].strip(), r["id"]
        assert 0 <= float(r["ra_hours"]) < 24, r["id"]
        assert -90 <= float(r["dec_deg"]) <= 90, r["id"]
        if r["vmag"]:
            float(r["vmag"])
        # OpenNGC rows carry no editorial text - blurbs/wiki/distance were a
        # curated-catalog concept and are empty everywhere now
        assert not (r["wiki"] or r["blurb"]), r["id"]


# ------------------------------------------------------- lookup / regression

def test_composite_and_former_curated_ids_are_plain_openngc_rows(tmp_path):
    """`ngc2237`/`ngc7317` used to name curated COMPOSITES. They are now the
    OpenNGC rows they always were, and every id resolves to exactly one object
    in both the desktop DB and the web payload."""
    db = Database(tmp_path / "t.db")
    assert db.get_object("ngc2237")["primary_name"] == "Rosette A"
    assert db.get_object("ngc7317")["primary_name"] == "NGC 7317"
    ids = [o["id"] for o in db.all_objects(advanced=True)]
    assert len(ids) == len(set(ids))


def test_curated_ids_are_gone_everywhere(tmp_path):
    db = Database(tmp_path / "t.db")
    for oid in ("m42", "m31", "m45", "lmc", "cr399"):
        assert db.get_object(oid) is None, oid
    # ... but the objects they named are present under their OpenNGC id
    assert db.get_object("ngc1976") is not None      # M42
    assert db.get_object("mel22") is not None        # M45
    assert db.get_object("eso56-115") is not None    # LMC






# ------------------------------------------------------- advertised catalog size

# Every user-facing claim about the catalog size, with the text that carries it:
#   (file, expected number of claims in that file, [regex, ...])
# A claim is a PROMISE about the shipped data, so it is pinned to the data.
#
# CD-08 (0.6.32 review): this table used to list five patterns and then assert
# that the TOTAL number of matches was exactly 6. `static/index.html`'s own
# desktop hint was never registered, so it advertised 12,160 objects for two
# releases while the guard stayed green - and the gap was invisible BY
# CONSTRUCTION: an unregistered file contributes no match (nothing to check),
# while a SECOND claim in a registered file changed the total instead of being
# value-checked. Every file now carries its own expected claim count, so a new
# or edited claim fails where the value is checked; registering a file is the
# way to cover a new surface. `landing/features.html`'s "12,000 objects" is a
# deliberate rounding (a feature blurb, not a catalogue count) and is not pinned.
ADVERTISED_SIZE = [
    ("CATALOG.md", 1, [r"ships ([\d,]+) OpenNGC/supplement objects"]),
]


def _advertised_mismatches(root: Path) -> list[str]:
    """Every registered advertised size that disagrees with the shipped rows,
    plus any file whose claim COUNT drifted. `root` is the tree the claims are
    read from (a copy in the mutation test); the shipped catalog is always the
    real CSV, and so is the expected size."""
    real = f"{sum(1 for _ in _adv_rows()):,}"
    problems: list[str] = []
    seen = []
    for rel, expected, patterns in ADVERTISED_SIZE:
        text = (root / rel).read_text(encoding="utf-8")
        found = [n for pat in patterns for n in re.findall(pat, text)]
        if len(found) != expected:
            problems.append(
                f"{rel} must carry {expected} advertised catalog size(s), found "
                f"{len(found)}: {found} - register a new/changed claim in "
                f"ADVERTISED_SIZE instead of letting it drift")
        seen += [(rel, n) for n in found]
    for rel, shown in seen:
        if shown != real:
            problems.append(
                f"{rel} advertises {shown} catalog objects but the shipped "
                f"catalog has {real} - the copy is stale or the data changed "
                f"without it")
    return problems


def test_advertised_catalog_size_matches_the_shipped_catalog():
    problems = _advertised_mismatches(ROOT)
    assert not problems, "\n".join(problems)


def test_advertised_size_guard_catches_a_drifted_desktop_hint(tmp_path):
    """CD-08 (0.6.32 review) mutation proof, on a COPY of every claimed file: the
    desktop hint that actually drifted (12,160 against the shipped 12,176) must
    be reported - before this fix the file was not registered at all, so the
    guard never looked at it."""
    import shutil
    for rel, _expected, _patterns in ADVERTISED_SIZE:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    hint = tmp_path / "CATALOG.md"
    hint.write_text(hint.read_text(encoding="utf-8").replace("12,176", "12,160"),
                    encoding="utf-8")
    problems = _advertised_mismatches(tmp_path)
    assert any("CATALOG.md" in p and "12,160" in p for p in problems), \
        f"the drifted catalog hint must be reported: {problems}"
    assert _advertised_mismatches(ROOT) == [], "the real tree must be clean"




def test_advanced_count_is_defined_once():
    """CD-04 (review 0.6.32): `Database.advanced_count` existed TWICE in the
    class body (app/database.py:397 and :464), so the second definition silently
    shadowed the first - a future edit to the dead copy (a filter, a cached
    count) would take effect or not depending on which one the editor happened
    to open. One accessor."""
    import ast
    import inspect

    from app.database import Database

    tree = ast.parse(inspect.getsource(Database).lstrip())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    names = [n.name for n in cls.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert names.count("advanced_count") == 1, names
