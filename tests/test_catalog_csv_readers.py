"""CD-03 (0.6.32 review): the two catalog CSV readers used `line.split(";")`
while the generator writes with `csv.writer(..., delimiter=";")`.

A field containing `;` (or `"`) is QUOTED by the writer. The naive readers then
mis-count the fields, so `app/database.py::_read_catalog_csv` skipped the whole
row silently and `scripts/match_popularity.py::read_csv` dropped it through its
`len(parts) < 10` guard. Today the shipped CSV needs no quoting (0 quoted
fields), which is exactly why nothing noticed.

These pins FAIL on the old naive-split readers and PASS on the csv-module ones.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COLS = ("id", "primary_name", "messier", "ngcic", "other", "otype",
        "ra_hours", "dec_deg", "vmag", "size_am", "constellation",
        "wiki", "blurb", "search")

# Two rows whose text fields contain a `;`, a `,` and an escaped `"` - exactly
# the three things `csv.writer` QUOTES. Written with the generator's own writer
# (delimiter=";"), then read back by both readers.
SYNTHETIC = [
    {"id": "x1", "primary_name": "X One", "messier": "", "ngcic": "X 1",
     "other": "a;b", "otype": "galaxy", "ra_hours": "1.5", "dec_deg": "-2.5",
     "vmag": "10.5", "size_am": "1.0x0.5", "constellation": "Tst",
     "wiki": "", "blurb": "", "search": 'x1 | a;b'},
    {"id": "x2", "primary_name": "X Two", "messier": "", "ngcic": "X 2",
     "other": "plain", "otype": "galaxy", "ra_hours": "2.5", "dec_deg": "-3.5",
     "vmag": "11.5", "size_am": "2.0x1.0", "constellation": "Tst",
     "wiki": "W,iki", "blurb": "", "search": 'say "hi";ok'},
]


def _write_csv(tmp_path: Path) -> Path:
    """Write the synthetic catalog with the SAME writer the generator uses."""
    p = tmp_path / "openned_probe.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("# NightSeam catalog - synthetic\n")
        f.write("# Columns: " + ";".join(COLS) + "\n")
        w = csv.writer(f, delimiter=";")
        for row in SYNTHETIC:
            w.writerow([row[c] for c in COLS])
    return p


def _load_matcher():
    """Import scripts/match_popularity.py without running main()."""
    name = "ns_match_popularity_pin"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "match_popularity.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_database_reader_round_trips_a_quoted_semicolon(tmp_path):
    from app.database import _read_catalog_csv

    rows = _read_catalog_csv(_write_csv(tmp_path))
    assert [r["id"] for r in rows] == ["x1", "x2"], (
        "a row whose field is quoted (a `;`, `,` or `\"` inside it) must survive "
        "the reader, not be dropped by the field-count check")
    assert rows[0]["other"] == "a;b"
    assert rows[0]["primary_name"] == "X One"
    assert rows[0]["search"] == "x1 | a;b"
    assert rows[0]["ra_hours"] == 1.5 and rows[0]["vmag"] == 10.5
    assert rows[1]["search"] == 'say "hi";ok'
    assert rows[1]["wiki"] == "W,iki"


def test_matcher_reader_round_trips_a_quoted_semicolon(tmp_path, monkeypatch):
    mod = _load_matcher()
    monkeypatch.setattr(mod, "CSV_PATH", _write_csv(tmp_path))

    rows = mod.read_csv()
    assert [r["id"] for r in rows] == ["x1", "x2"], (
        "the matcher reader must agree with app/database.py - both parse the "
        "same writer's output")
    assert rows[0]["other"] == "a;b"
    assert rows[0]["search"] == "x1 | a;b"
    assert rows[1]["search"] == 'say "hi";ok'
    assert rows[1]["wiki"] == "W,iki"


def test_both_readers_agree_on_the_shipped_catalog():
    """The real CSV: same row ids from both readers, and the csv module's own
    parse agrees with the readers (guards a future writer change)."""
    import csv as _csv

    from app.database import _read_catalog_csv

    csv_path = ROOT / "app" / "data" / "openned.csv"
    db_rows = _read_catalog_csv(csv_path)

    mod = _load_matcher()
    tel_rows = mod.read_csv()

    assert len(db_rows) == len(tel_rows) > 12000
    assert [r["id"] for r in db_rows] == [r["id"] for r in tel_rows]

    with open(csv_path, encoding="utf-8", newline="") as f:
        parsed = [r for r in _csv.reader(f, delimiter=";")
                  if r and not r[0].startswith("#")]
    assert all(len(r) == len(COLS) for r in parsed)
    assert len(parsed) == len(db_rows)


# ---------------------------------------------------------------- CD-05
# The popularity index is keyed by `normalize(name)`. Two source keys can
# normalize to the same string; the old DICT branch did `tel_index[norm] = ...`,
# so the LAST key in source order won. `match_objects` documents the opposite
# rule (highest wins, deterministic), and the shipped snapshot has exactly one
# such pair: 'NGC 654 2' (115) vs 'NGC 6542' (3) -> both `ngc6542`.
#
# These pins assert the DOCUMENTED rule (highest wins) on a synthetic index, so
# they are independent of any future re-bake of data/popularity_snapshot.json.

_COLLIDING_PAIRS = [
    ("NGC 654 2", 115),
    ("NGC 6542", 3),
]


def _name_score_pairs(tel_data):
    """Mirror of the dict-branch shape `main()` feeds the helper."""
    for k, v in tel_data.items():
        if isinstance(v, dict):
            yield k, v.get("popularity", v.get("score", 0))
        else:
            yield k, v


def test_matching_a_row_against_the_index_picks_the_highest_collision():
    mod = _load_matcher()
    index = mod.index_from_name_score_pairs(_name_score_pairs(
        {"NGC 654 2": 115, "NGC 6542": 3}))
    matches, details = mod.match_objects(
        [{"id": "ngc6542", "primary_name": "NGC 6542", "ngcic": "NGC 6542"}],
        index)
    assert matches["ngc6542"] == 115
    assert details[0]["source_name"] == "NGC 654 2"


def test_dict_index_keeps_the_highest_of_a_colliding_pair():
    mod = _load_matcher()
    index = mod.index_from_name_score_pairs(_COLLIDING_PAIRS)

    assert index["ngc6542"] == ("NGC 654 2", 115), (
        "the highest score must win the normalize collision - not the last "
        "key in file order")


def test_dict_index_collision_result_does_not_depend_on_source_order():
    mod = _load_matcher()
    forward = mod.index_from_name_score_pairs(_COLLIDING_PAIRS)
    reversed_ = mod.index_from_name_score_pairs(list(reversed(_COLLIDING_PAIRS)))
    assert forward == reversed_

    # an equal-score collision goes to the FIRST entry in source order
    a = mod.index_from_name_score_pairs([("AB 1", 7), ("A B 1", 7)])
    assert a["ab1"] == ("AB 1", 7)
    b = mod.index_from_name_score_pairs([("A B 1", 7), ("AB 1", 7)])
    assert b["ab1"] == ("A B 1", 7)


def test_shipped_pair_matches_the_collision_the_review_measured():
    """The pair the review measured is still the one in the snapshot (data, not
    pin - if the snapshot is re-baked and the pair is gone this reports it)."""
    import json

    snap = ROOT / "data" / "popularity_snapshot.json"
    if not snap.is_file():
        return                            # offline checkout without the snapshot
    mod = _load_matcher()
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    colliding = {}
    for k in data:
        colliding.setdefault(mod.normalize(k), []).append(k)
    hits = {k: v for k, v in colliding.items() if len(v) > 1}
    if "ngc6542" not in hits:
        return                            # re-baked away - the pins above still hold
    assert set(hits["ngc6542"]) == {"NGC 654 2", "NGC 6542"}


def test_main_dict_branch_collision_uses_the_highest_score(
        tmp_path, monkeypatch, capsys):
    """Drive `main()` itself through its DICT branch (a `{name: score}` snapshot
    - the shipped shape) and read the printed POPULARITY table.

    This is the behavioural pin for the branch that actually produced the
    shipped scores: a regression to plain `tel_index[norm] = ...` overwriting
    makes `ngc6542` print 3 instead of the documented 115.
    """
    mod = _load_matcher()
    csv_path = tmp_path / "openned_probe.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write("# synthetic\n")
        w = csv.writer(f, delimiter=";")
        w.writerow(["ngc6542", "NGC 6542", "", "NGC 6542", "", "galaxy",
                    "17.97", "23.65", "13.5", "1.0x0.8", "Peg", "", "", ""])
    monkeypatch.setattr(mod, "CSV_PATH", csv_path)
    monkeypatch.setattr(mod, "load_cached_snapshot",
                        lambda: {"NGC 654 2": 115, "NGC 6542": 3})
    monkeypatch.setattr(mod, "OUTPUT_MODULE", tmp_path / "popularity_probe.py")
    monkeypatch.setattr(sys, "argv", ["match_popularity.py", "--print"])

    scored = mod.main()
    out = capsys.readouterr().out
    assert scored == {"ngc6542": 115}, (
        "the dict branch of main() must apply the documented highest-wins rule")
    assert "'ngc6542': 115" in out
    assert not (tmp_path / "popularity_probe.py").exists()   # --print writes nothing

