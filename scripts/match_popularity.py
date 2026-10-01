#!/usr/bin/env python3
"""Match the OpenNGC catalog (app/data/openned.csv) against the frozen
popularity snapshot and write `app/popularity_data.py`.

The scores are used for TWO things (see `app/catalog_data.py`): the silent
ranking tiebreaker and the standard-mode selection (the N most popular objects),
so the match must be REPRODUCIBLE - it is: every designation of an object is
considered and the highest score wins (see `match_objects`).

Usage:
  python scripts/match_popularity.py                 # OFFLINE: use the local snapshot
  python scripts/match_popularity.py --print         # print the dict instead of writing

NightSeam NEVER fetches popularity data. The snapshot at `data/popularity_snapshot.json`
was taken once, deliberately, and is the frozen source of truth; every build,
matcher run and shipped artefact reads that file. There is no network code path
in this script or anywhere else in the project - `tests/test_no_popularity_fetch.py`
fails if one reappears, and the browser gate treats the URL as a tripwire.
"""

import csv
import json
import re
import sys
from collections import defaultdict

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "app" / "data" / "openned.csv"
OUTPUT_JSON = ROOT / "data" / "popularity_snapshot.json"
OUTPUT_MODULE = ROOT / "app" / "popularity_data.py"


def load_cached_snapshot():
    """Try to load the cached popularity snapshot."""
    import os
    if os.path.exists(OUTPUT_JSON):
        print(f"Loading the cached popularity snapshot from {OUTPUT_JSON}")
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"  Loaded {len(data)} entries from cache")
        return data
    return None


def normalize(s):
    """Normalize a string for matching: lowercase, strip spaces/hyphens/dots and
    LEADING ZEROS inside numbers.

    The padding matters: the snapshot keys the Large Magellanic Cloud as
    'PGC 17223' while OpenNGC lists it as 'PGC 017223' - without this the LMC
    (and ~1,259 other padded snapshot keys) never matched, so the standard
    'most popular' list lost one of its most famous objects. Only zeros that
    START a number are dropped ('ngc7000' stays 'ngc7000')."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r'[\s\-\.\(\)]+', '', s)
    return re.sub(r"(?<![0-9])0+(?=\d)", "", s)


# Objects whose OpenNGC designation is NOT the name the popularity source keys them under.
# Verified against data/popularity_snapshot.json - each target below exists there and
# names the same object (the popularity source lists no Hyades entry at all, so that one
# simply has no score).
POPULARITY_ALIASES = {
    "b33": "BARNARD 33",            # OpenNGC's short form of the Horsehead
    "mel111": "CL MELOTTE 111",     # Coma Star Cluster
    "cl399": "CL COLLINDER 399",    # Brocchi's Cluster / Coathanger
    "ngc5866": "M 102",             # the Spindle Galaxy (modern M102 identity)
}


def extract_designations_from_csv_row(row):
    """Extract all possible matching designations from a CSV row."""
    designations = set()
    
    # The id field (e.g., "ngc7000", "ic10")
    csv_id = row.get("id", "")
    if csv_id:
        designations.add(normalize(csv_id))
        designations.add(csv_id.lower().strip())
    
    # primary_name (e.g., "NGC 7000", "IC 10")
    primary = row.get("primary_name", "")
    if primary:
        designations.add(normalize(primary))
        designations.add(primary.lower().strip())
    
    # ngcic column (e.g., "NGC 7000", "IC 10")
    ngcic = row.get("ngcic", "")
    if ngcic:
        designations.add(normalize(ngcic))
        designations.add(ngcic.lower().strip())
    
    # messier column
    messier = row.get("messier", "")
    if messier:
        designations.add(normalize(messier))
        designations.add(messier.lower().strip())
    
    # other identifiers (comma-separated list)
    other = row.get("other", "")
    if other:
        for alt in other.split(","):
            alt = alt.strip()
            if alt:
                designations.add(normalize(alt))
                designations.add(alt.lower().strip())
    
    # search column (pipe-separated)
    search = row.get("search", "")
    if search:
        for term in search.split("|"):
            term = term.strip()
            if term:
                designations.add(normalize(term))
                designations.add(term.lower().strip())
    
    # objects the popularity source keys under a different designation (see the table)
    for desig in list(designations):
        alias = POPULARITY_ALIASES.get(desig)
        if alias:
            designations.add(normalize(alias))

    # Remove empty strings
    designations.discard("")
    return designations


def _index_winner(prev, score, name, order):
    """The deterministic winner for one normalized key.

    CD-05 (0.6.32 review): a plain `index[norm] = (name, score)` let the LAST
    entry in source order win, which is the source-order dependence
    `match_objects` documents as fixed. Same rule as the matcher: the HIGHEST
    score wins; an equal score goes to the entry that appeared FIRST in the
    source sequence (`order`), and lexicographic name order breaks a remaining
    tie so a hand-built dict can never fall back on iteration/hash order.
    """
    if prev is None:
        return (name, score, order)
    _, pscore, porder = prev
    if score != pscore:
        return (name, score, order) if score > pscore else prev
    if order != porder:
        return prev if porder < order else (name, score, order)
    return prev if prev[0] <= name else (name, score, order)


def build_snapshot_index(tel_data):
    """Build the lookup index from a list of popularity snapshot entries.

    `normalize(name) -> (original_name, score)`. Colliding normalized keys are
    resolved by `_index_winner` (highest score, then source order), never by
    overwrite order - the old body's second `if ... not in index` block was a
    no-op (it re-tested the same key) and the first assignment silently
    overwrote a collision.
    """
    return index_from_name_score_pairs(
        (entry.get("name", ""),
         entry.get("popularity", entry.get("score", 0)))
        for entry in tel_data)


def index_from_name_score_pairs(pairs):
    """Build the lookup index from `(name, score)` pairs IN SOURCE ORDER.

    Used by the dict branch of `main()` (`{name: score}` / `{name: {score: X}}`
    snapshots), where the old code did `tel_index[norm] = (k, score)` and let a
    later key overwrite an earlier one. The shipped snapshot has exactly one
    such pair - 'NGC 654 2' (115) and 'NGC 6542' (3) both normalize to
    `ngc6542` - so `ngc6542` shipped 3 instead of the documented highest-wins
    115. See `_index_winner` for the tiebreak rule.
    """
    index: dict = {}
    order: dict = {}
    for name, score in pairs:
        if not name:
            continue
        norm = normalize(name)
        if not norm:
            continue
        n = order.get(norm, 0)
        order[norm] = n + 1
        index[norm] = _index_winner(index.get(norm), score, name, n)
    return {norm: (name, score) for norm, (name, score, _o) in index.items()}


def match_objects(csv_rows, tel_index):
    """Match CSV objects against the popularity source data - DETERMINISTICALLY.

    An object carries several designations and the popularity source may know more than
    one of them with DIFFERENT scores (M27 = NGC 6853: "M 27" 73260 vs "NGC
    6853" 4). The old code iterated a `set` and broke on the first hit, so the
    score depended on PYTHONHASHSEED: two runs of the same input produced
    different popularity tables, and the standard-mode selection built on them
    was not reproducible. Every designation is now considered and the HIGHEST
    score wins; ties go to the lexicographically smallest key, so the result is
    stable across processes and machines.
    """
    matches = {}
    match_details = []

    for row in csv_rows:
        csv_id = row.get("id", "")
        if not csv_id:
            continue

        designations = sorted(extract_designations_from_csv_row(row))
        best = None                     # (score, key, original name)
        for desig in designations:
            hit = tel_index.get(desig)
            if hit is None:
                continue
            orig_name, score = hit
            if best is None or score > best[0]:
                best = (score, desig, orig_name)

        if best is not None:
            score, desig, orig_name = best
            matches[csv_id] = score
            match_details.append({
                "csv_id": csv_id,
                "primary_name": row.get("primary_name", ""),
                "source_name": orig_name,
                "score": score,
                "matched_via": desig,
            })

    return matches, match_details


def read_csv():
    """Read the openned.csv file (the catalogue's `;`-separated layout).

    CD-03 (0.6.32 review): parsed with the `csv` module, exactly like
    `app/database.py::_read_catalog_csv`. The generator
    (`scripts/build_adv_catalog.py`) writes with `csv.writer(delimiter=";")`,
    which QUOTES any field containing `;`, `"` or a newline - the old
    `line.split(";")` mis-split such a row and dropped it through the length
    check. The comment/blank-line/field-count behaviour is unchanged.
    """
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        for parts in csv.reader(f, delimiter=";"):
            if not parts:
                continue                      # blank line
            if parts[0].startswith("#"):
                continue                      # comment line
            if len(parts) < 10:
                continue
            row = {
                "id": parts[0],
                "primary_name": parts[1],
                "messier": parts[2],
                "ngcic": parts[3],
                "other": parts[4],
                "otype": parts[5],
                "ra_hours": parts[6],
                "dec_deg": parts[7],
                "vmag": parts[8],
                "size_am": parts[9] if len(parts) > 9 else "",
                "constellation": parts[10] if len(parts) > 10 else "",
                "wiki": parts[11] if len(parts) > 11 else "",
                "blurb": parts[12] if len(parts) > 12 else "",
                "search": parts[13] if len(parts) > 13 else "",
            }
            rows.append(row)
    return rows


def write_module(matches) -> None:
    """Write `app/popularity_data.py` (sorted by id, with its provenance)."""
    lines = [
        '"""Popularity scores for the OpenNGC catalog.',
        "",
        "One entry per catalog id, used for TWO things:",
        "  * the SILENT ranking tiebreaker (visibility decides, popularity separates",
        "    equals - the number is never shown in either app), and",
        "  * the STANDARD-MODE selection (`app/catalog_data.STANDARD_TOP_N`: the N",
        "    most popular objects are the standard catalog, the rest are advanced).",
        "",
        "GENERATED - do not hand-edit. Re-run:",
        "",
        "    python scripts/match_popularity.py            # offline, from the frozen snapshot",
        "",
        "Every designation of every `app/data/openned.csv` row is matched against",
        "the popularity table and the HIGHEST score wins (`match_objects`), so the",
        "result does not depend on set/hash order and the standard-mode selection is",
        "reproducible.",
        '"""',
        "POPULARITY = {",
    ]
    for oid in sorted(matches):
        lines.append(f"    {oid!r}: {matches[oid]},")
    lines.append("}")
    OUTPUT_MODULE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_MODULE} ({len(matches)} entries)")


def main():
    argv = sys.argv[1:]
    to_stdout = "--print" in argv

    if "--live" in argv:
        # The network path is GONE on purpose: the snapshot is frozen and no
        # NightSeam code may contact the popularity source again. Tell a stale caller
        # instead of silently ignoring the flag.
        print("ERROR: --live was removed on purpose - NightSeam never fetches "
              "the popularity source any more.")
        print(f"       The frozen snapshot is {OUTPUT_JSON}; this script reads it.")
        sys.exit(2)

    # 1. Read CSV
    print(f"Reading {CSV_PATH}...")
    csv_rows = read_csv()
    print(f"  Total rows in openned.csv: {len(csv_rows)}")

    # 2. Load the frozen local snapshot - the ONLY source. There is no fallback
    #    and no fetch: if it is missing, say so and stop.
    tel_data = load_cached_snapshot()
    if tel_data is None:
        print(f"ERROR: no local snapshot at {OUTPUT_JSON}.")
        print("       NightSeam is offline by design; restore that file from a")
        print("       backup. There is no download path any more.")
        sys.exit(1)

    # (the snapshot is NOT rewritten when reading it - no side effect)
    
    # 3. Inspect the popularity source data structure
    if tel_data:
        sample = tel_data[0] if isinstance(tel_data, list) else list(tel_data.items())[:3]
        print(f"  Sample entry: {json.dumps(sample, indent=2)[:500]}")
        if isinstance(tel_data, dict):
            print(f"  Keys sample: {list(tel_data.keys())[:10]}")
    
    # 4. Build index
    if isinstance(tel_data, list):
        tel_index = build_snapshot_index(tel_data)
    elif isinstance(tel_data, dict):
        # Might be {name: score} or {name: {score: X}} etc. CD-05: built through
        # the shared deterministic rule - plain assignment let the LAST key in
        # file order win a normalize collision (shipped: 'NGC 654 2' 115 lost to
        # 'NGC 6542' 3).
        def _pairs():
            for k, v in tel_data.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    if not isinstance(v, dict):
                        continue
                    yield k, v.get("popularity", v.get("score", 0))
                else:
                    yield k, v
        tel_index = index_from_name_score_pairs(_pairs())
    else:
        print("ERROR: Unexpected popularity snapshot format")
        sys.exit(1)
    
    print(f"  popularity index: {len(tel_index)} normalized entries")
    
    # Show some sample keys
    sample_keys = list(tel_index.keys())[:15]
    print(f"  Sample normalized keys: {sample_keys}")
    
    # 5. Match
    print("\nMatching objects...")
    matches, details = match_objects(csv_rows, tel_index)
    
    # 6. Report
    total = len(csv_rows)
    matched = len(matches)
    rate = (matched / total * 100) if total else 0
    
    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"Total rows in openned.csv: {total}")
    print(f"Total matches found: {matched}")
    print(f"Match rate: {rate:.2f}%")
    
    # Top 20 by score
    sorted_matches = sorted(details, key=lambda x: x["score"], reverse=True)[:20]
    print(f"\nTop 20 matched objects by popularity score:")
    print(f"{'Rank':<5} {'CSV ID':<20} {'Primary Name':<20} {'Source Name':<25} {'Score':<10} {'Matched Via'}")
    print("-" * 100)
    for i, m in enumerate(sorted_matches, 1):
        print(f"{i:<5} {m['csv_id']:<20} {m['primary_name']:<20} {m['source_name']:<25} {m['score']:<10} {m['matched_via']}")
    
    # 7. Output
    scored = {oid: s for oid, s in matches.items() if s and s > 0}
    print(f"\n{'='*60}")
    print(f"POPULARITY ({len(scored)} scored entries"
          f"{'; %d zero-score rows dropped' % (len(matches) - len(scored)) if len(matches) != len(scored) else ''})")
    print(f"{'='*60}")
    if to_stdout:
        print("POPULARITY = {")
        for csv_id in sorted(scored):
            print(f"    {csv_id!r}: {scored[csv_id]},")
        print("}")
    else:
        write_module(scored)

    return scored


if __name__ == "__main__":
    main()
