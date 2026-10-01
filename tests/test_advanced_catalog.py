"""Sanity checks on the bundled catalog CSV (OpenNGC NGC/IC + addendum) and the
database seeding path.

This file IS the catalog: there is no hand-curated object list any more, so
every row must be a well-formed OpenNGC object and the seeded tables must be
exactly it.
"""

import csv
from pathlib import Path

from app.catalog_data import STANDARD_TOP_N, TYPE_LABELS, standard_ids
from app.database import Database
from app.popularity_data import POPULARITY

CSV = Path(__file__).resolve().parents[1] / "app" / "data" / "openned.csv"
COLS = ("id", "primary_name", "messier", "ngcic", "other", "otype", "ra_hours",
        "dec_deg", "vmag", "size_am", "constellation", "wiki",
        "blurb", "search")


def _csv_rows():
    with open(CSV, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            yield line.rstrip("\n").split(";")


def _rows() -> list[dict]:
    """The shipped CSV (its column names live in the `#` header comment)."""
    with open(CSV, encoding="utf-8") as f:
        return [dict(zip(COLS, l.rstrip("\n").split(";")))
                for l in f if not l.startswith("#") and l.strip()]


def test_csv_exists_and_is_large():
    assert CSV.exists()
    assert CSV.stat().st_size > 1_000_000
    assert sum(1 for _ in _csv_rows()) > 10_000


def test_csv_rows_are_valid():
    ids = set()
    for r in _csv_rows():
        assert len(r) == len(COLS), r[0]
        d = dict(zip(COLS, r))
        assert d["id"] not in ids, d["id"]
        ids.add(d["id"])
        assert 0 <= float(d["ra_hours"]) < 24, d["id"]
        assert -90 <= float(d["dec_deg"]) <= 90, d["id"]
        assert d["otype"] in TYPE_LABELS, (d["id"], d["otype"])
        if d["vmag"]:
            assert -5 <= float(d["vmag"]) <= 21, d["id"]


def test_openngc_non_objects_are_not_shipped():
    """OpenNGC marks its own non-object rows: `Dup` (a duplicate that points at
    the master entry), `NonEx` (nonexistent), single/double stars, `Other`,
    `Nova`. None of those ROWS ships - NGC 651 (Dup of NGC 650) and OpenNGC's
    M40 (`**`) / M73 (`Other`) / IC 1318 (`*`) rows are gone. The three notable
    ones come back through `app/data/supplement.csv` instead, under their own
    ids and with the project's own types (see tests/test_supplement.py)."""
    ids = {r["id"] for r in _rows()}
    for oid in ("ngc651", "ngc6994", "m102"):
        assert oid not in ids, oid
    assert "ngc650" in ids          # the master entry stays
    assert "b33" in ids             # Barnard 33 (Horsehead) - addendum, DrkN
    # the supplement's replacements exist, and are NOT OpenNGC ids
    assert {"m40", "m73", "ic1318"} <= ids


def test_db_seeds_the_whole_catalog(tmp_path):
    """`objects` = the standard-mode selection, `advanced_objects` = the rest,
    disjoint, and together exactly the CSV."""
    db = Database(tmp_path / "t.db")
    std = db.all_objects()
    adv = [o for o in db.all_objects(advanced=True) if o.get("_adv")]
    assert len(std) == STANDARD_TOP_N
    assert len(adv) == len(_rows()) - STANDARD_TOP_N
    union = db.all_objects(advanced=True)
    assert len(union) == len(_rows())
    assert len({o["id"] for o in union}) == len(union)
    assert not ({o["id"] for o in std} & {o["id"] for o in adv})
    assert {o["id"] for o in std} == standard_ids([r["id"] for r in _rows()])
    # every seeded id exists in the CSV, with the CSV's data
    csv_by_id = {r["id"]: r for r in _rows()}
    for o in union:
        assert o["id"] in csv_by_id, o["id"]
        assert o["primary_name"] == csv_by_id[o["id"]]["primary_name"]
    # an OpenNGC object that the curated list used to own is just a normal row
    ngc2242 = db.get_object("ngc2242")
    assert ngc2242["otype"] == "planetary"
    assert ngc2242["primary_name"] == "NGC 2242"


def test_standard_rows_are_the_popular_ones_and_all_scored(tmp_path):
    db = Database(tmp_path / "t.db")
    std = db.all_objects()
    assert all(o["popularity"] for o in std)
    assert min(o["popularity"] for o in std) == min(
        POPULARITY[i] for i in standard_ids([r["id"] for r in _rows()]))
    # the most popular object in the catalog is in standard mode, and it is M42
    top = max(std, key=lambda o: o["popularity"])
    assert (top["id"], top["primary_name"]) == ("ngc1976", "Great Orion Nebula")


def test_reseeding_is_idempotent(tmp_path):
    p = tmp_path / "t.db"
    first = Database(p)
    std_before = {o["id"] for o in first.all_objects()}
    again = Database(p)
    assert {o["id"] for o in again.all_objects()} == std_before
    assert again.advanced_count() == len(_rows()) - STANDARD_TOP_N


def test_a_pre_openngc_database_is_rebuilt_on_open(tmp_path):
    """A DB seeded by an older build holds the curated 197 in `objects` and the
    pre-addendum advanced rows. Opening it must rebuild both tables into the
    OpenNGC split (they are seed data, never user data)."""
    p = tmp_path / "old.db"
    db = Database(p)
    db.conn.execute("DELETE FROM objects")
    db.conn.execute("INSERT INTO objects(id, primary_name, otype) "
                    "VALUES('m42','Orion Nebula','nebula')")
    db.conn.execute("DELETE FROM advanced_objects")
    db.conn.execute("INSERT INTO advanced_objects(id, primary_name, otype) "
                    "VALUES('ngc9999','Stale Row','galaxy')")
    db.conn.commit()
    assert db.get_object("m42") is not None

    fresh = Database(p)
    assert fresh.get_object("m42") is None            # the curated row is gone
    assert fresh.get_object("ngc9999") is None        # so is the stale advanced row
    assert fresh.get_object("ngc1976") is not None    # M42 as OpenNGC names it
    assert len(fresh.all_objects()) == STANDARD_TOP_N
