"""Generate app/data/openned.csv from the OpenNGC database (one-time dev task).

Source: https://github.com/mattiaverga/OpenNGC (CC BY-SA 4.0) - the NGC/IC
catalog (`NGC.csv`) PLUS its addendum (`addendum.csv`: notable objects that have
NO NGC/IC designation - the Horsehead, the Hyades, the Magellanic Clouds, the
Sculptor/Fornax dwarfs, ...). Both files live in the repo's `database_files/`.

This file IS the catalog: there is no separate hand-curated object list, so the
shipped rows are exactly the OpenNGC objects NightSeam can plan. Rows whose
OpenNGC `Type` says they are not objects (`Dup` - "duplicated object, see the
NGC/IC columns for the master"; `NonEx` - nonexistent; single/double stars;
`Other`; `Nova`) are dropped, and `_build_row` RAISES on a type with no mapping
rather than filing it under a wrong class.

Run: python scripts/build_adv_catalog.py [path/to/NGC.csv] [path/to/addendum.csv]
"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The OpenNGC CSVs are not bundled (CC BY-SA download): fetch them from
# https://github.com/mattiaverga/OpenNGC (database_files/) and either pass the
# paths or drop them at data/NGC.csv + data/addendum.csv (gitignored).
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "NGC.csv"
SRC_ADDENDUM = (Path(sys.argv[2]) if len(sys.argv) > 2
                else SRC.with_name("addendum.csv"))
# The project's OWN supplement (app/data/supplement.csv, committed): the SIXTEEN
# objects OpenNGC cannot carry, in two batches. Batch 1 (7) is either absent
# (Abell 21, Jones-Emberson 1, Sh2-276, Mel 20) or filed as a non-object
# (IC 1318 is typed `*`, M73 `Other`, M40 `**`); batch 2 (9) lives outside
# OpenNGC's SCOPE (Sharpless/Barnard/LDN/LBN designations, `vmag` deliberately
# empty). The file uses the catalogue's own column layout, but it is PARSED here
# (see `read_supplement`) and re-emitted through the same writer as the OpenNGC
# rows; only id uniqueness is checked across all sources.
SRC_SUPPLEMENT = ROOT / "app" / "data" / "supplement.csv"
OUT = ROOT / "app" / "data" / "openned.csv"

sys.path.insert(0, str(ROOT))
from app.fits import parse_ra_hours, parse_dec_deg


def _load_source(path: Path) -> list[dict]:
    """Read an OpenNGC CSV (checked at call time, not import time, so the pure
    helpers below stay importable in tests without the source present)."""
    if not path.is_file():
        raise SystemExit(
            f"OpenNGC source not found: {path}\n"
            f"  download NGC.csv + addendum.csv from "
            f"https://github.com/mattiaverga/OpenNGC (database_files/)\n"
            f"  and pass them as the two arguments (or place them at "
            f"data/NGC.csv + data/addendum.csv)")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


TYPE_MAP = {
    "G": "galaxy", "GPair": "galaxy", "GTrpl": "galaxy", "GGroup": "galaxy",
    "GCl": "globular", "OCl": "open_cluster",
    # OpenNGC's `Cl+N` is "star cluster + nebula": the famous members are the
    # nebulae (M42/Orion, IC 1396, IC 1805 Heart, IC 1848 Soul, IC 5146 Cocoon,
    # IC 2944, NGC 1333), and labelling those "Open cluster" was plain wrong.
    # NightSeam has no cluster+nebula type, so they show as nebulae; the few
    # obscure cluster-dominated rows lose nothing but a colour.
    "Cl+N": "nebula",
    "PN": "planetary", "SNR": "supernova_remnant",
    "Neb": "nebula", "HII": "nebula", "EmN": "nebula", "RfN": "nebula",
    "DrkN": "nebula",                        # addendum-only (Horsehead, Coalsack)
    "*Ass": "asterism",
}
# OpenNGC writes Serpens as `Se1` (Caput) / `Se2` (Cauda) - see NGC_guide.txt.
# Every other code is the 3-letter IAU abbreviation, and the two halves are the
# same constellation to a user, so both display as `Ser` (148 rows).
CONSTELLATION_MAP = {"Se1": "Ser", "Se2": "Ser"}
# OpenNGC's own non-object types (see NGC_guide.txt): Dup = "Duplicated object
# (see NGC or IC columns to find the master object)", NonEx = "Nonexistent
# object", `*`/`**` = single/double stars, Other, Nova.
SKIP_TYPES = {"Dup", "*", "**", "Other", "NonEx", "Nova"}


def fmt_desig(name: str) -> str:
    """'NGC0224' -> 'NGC 224', 'IC0001' -> 'IC 1', 'ESO056-115' -> 'ESO 56-115',
    'Mel071' -> 'Mel 71'.

    The prefix class allows lowercase because the addendum mixes cases ('Mel071',
    'Cl399'); NGC/IC names are all-uppercase, so their formatting is unchanged.
    The trailing '-NNN' form is the addendum's southern-galaxy naming
    ('ESO056-115', 'PGC000143'); a plain prefix+number ('MWSC3156', 'H05')
    keeps working exactly as before."""
    m = re.match(r"^([A-Za-z]+)0*(\d+)(?:\s*-\s*0*(\d+))?$", name)
    if not m:
        return name
    out = f"{m.group(1)} {int(m.group(2))}"
    return f"{out}-{int(m.group(3))}" if m.group(3) else out


def _norm(text: str) -> str:
    """Whitespace-free lowercase form used for id/key comparisons."""
    return re.sub(r"\s+", "", (text or "").lower())


def _build_row(r: dict, oid: str, ngcic: str) -> dict | None:
    """One OpenNGC row -> NightSeam's catalog row (None when unusable)."""
    otype = TYPE_MAP.get(r["Type"])
    if otype is None:
        raise SystemExit(
            f"{r['Name']}: OpenNGC type {r['Type']!r} has no NightSeam mapping - "
            f"add it to TYPE_MAP or to SKIP_TYPES, never let a galaxy fallback "
            f"mislabel it")
    ra_h = parse_ra_hours(r["RA"])
    dec_d = parse_dec_deg(r["Dec"])
    if ra_h is None or dec_d is None:
        return None
    messier = f"M{int(r['M'])}" if r["M"].strip() else ""
    common = (r["Common names"] or "").strip()
    # OpenNGC lists alternate names '|'-separated and variants comma-separated
    # ('Barbell Nebula,Cork Nebula,Little Dumbbell Nebula'); the first comma
    # segment is the display name. OpenNGC carries a common name for only ~150
    # rows and 78 Messier objects have none ('NGC 5457' for M101), so the
    # Messier designation is the fallback - same row, never a curated list.
    primary = common.split("|")[0].split(",")[0].strip() or messier or ngcic
    vmag = (r["V-Mag"] or r["B-Mag"] or "").strip()
    vmag = round(float(vmag), 1) if vmag else None
    maj, mnr = (r["MajAx"] or "").strip(), (r["MinAx"] or "").strip()
    if maj:
        size = f"{float(maj):g}"
        if mnr:
            size += f"x{float(mnr):g}"
    else:
        size = ""
    ids = (r["Identifiers"] or "").strip()[:140]
    search = " | ".join(x for x in (
        oid, ngcic, common.lower(), messier.lower(), ids.lower()) if x)
    const = (r["Const"] or "").strip()
    return {
        "id": oid, "primary_name": primary[:120], "messier": messier,
        "ngcic": ngcic, "other": ids, "otype": otype,
        "ra_hours": round(ra_h, 5), "dec_deg": round(dec_d, 5),
        "vmag": vmag, "size_am": size,
        "constellation": CONSTELLATION_MAP.get(const, const),
        "wiki": "", "blurb": "", "search": search,
    }


def build_rows(path: Path, source: str) -> list[dict]:
    """Objects from one OpenNGC file, in file order, ids unique within it."""
    raw = _load_source(path)
    out: list[dict] = []
    seen: set[str] = set()
    for r in raw:
        if not r["Name"] or r["Type"] in SKIP_TYPES:
            continue
        ngcic = fmt_desig(r["Name"])
        key = _norm(ngcic)
        if key in seen:
            raise SystemExit(f"{source}: duplicate designation {ngcic!r}")
        seen.add(key)
        row = _build_row(r, key, ngcic)
        if row is not None:
            out.append(row)
    return out


def read_supplement(path: Path = SRC_SUPPLEMENT) -> list[dict]:
    """Parse the committed supplement - `;`-separated, the catalogue's columns.

    Not an "appended as-is" copy: the numeric columns are cast, an empty `vmag`
    stays None (batch 2 has no credible magnitude) and an unknown type is a hard
    error. The 16 rows and their two batches are documented in the file header.
    """
    cols = ("id", "primary_name", "messier", "ngcic", "other", "otype", "ra_hours",
            "dec_deg", "vmag", "size_am", "constellation", "wiki",
            "blurb", "search")
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            vals = line.rstrip("\n").split(";")
            if len(vals) != len(cols):
                raise SystemExit(f"{path.name}: {len(vals)} columns, expected "
                                 f"{len(cols)}: {line[:60]}")
            d = dict(zip(cols, vals))
            d["ra_hours"] = float(d["ra_hours"])
            d["dec_deg"] = float(d["dec_deg"])
            d["vmag"] = float(d["vmag"]) if d["vmag"] else None
            if d["otype"] not in TYPE_MAP.values():
                raise SystemExit(f"{path.name}: {d['id']} has unknown type "
                                 f"{d['otype']!r}")
            out.append(d)
    return out


def _disambiguate_names(rows: list[dict]) -> int:
    """Give a shared common name its designation: 'Eagle Nebula (IC 4703)'.

    OpenNGC hands the SAME common name to more than one row - the two Antennae
    galaxies, IC 4703 + NGC 6611 ("Eagle Nebula"), the two Eastern Veil knots,
    the two Butterfly galaxies and the two Eyes. Showing one label twice in
    Standard mode looks like a duplicate; the designation stays visible instead.
    The bare name remains in `search`, so searching for it still finds every
    row it names.
    """
    from collections import Counter
    shared = {n for n, c in Counter(r["primary_name"] for r in rows).items() if c > 1}
    n = 0
    for r in rows:
        if r["primary_name"] in shared:
            r["primary_name"] = f"{r['primary_name']} ({r['ngcic']})"
            n += 1
    return n


def build_catalog() -> tuple[list[dict], dict[str, int]]:
    """(all object rows, per-source counts) - OpenNGC first, then the supplement."""
    ngc = build_rows(SRC, "NGC.csv")
    addendum = build_rows(SRC_ADDENDUM, "addendum.csv")
    supplement = read_supplement()
    rows = ngc + addendum + supplement
    _disambiguate_names(rows)
    _assert_global_unique(rows)
    return rows, {"NGC/IC": len(ngc), "addendum": len(addendum),
                  "supplement": len(supplement)}


def _assert_global_unique(rows: list[dict]) -> None:
    """Fail loudly on a duplicate id: an id names exactly one object, and a
    repeated one makes `web/catalog.json`'s byId map disagree with
    `db.get_object()` about which object it is."""
    from collections import Counter
    dupes = sorted(k for k, v in Counter(r["id"] for r in rows).items() if v > 1)
    if dupes:
        raise SystemExit(f"catalog id collision: {dupes}")


def main():
    rows, counts = build_catalog()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write("# NightSeam catalog - derived from OpenNGC "
                "(https://github.com/mattiaverga/OpenNGC, CC BY-SA 4.0)\n")
        f.write("# NGC.csv (NGC/IC) + addendum.csv (notable non-NGC/IC objects) "
                "+ supplement.csv (this project's data for the 16 objects "
                "OpenNGC cannot carry) | "
                "Columns: id;primary_name;messier;ngcic;other;otype;"
                "ra_hours;dec_deg;vmag;size_am;constellation;"
                "wiki;blurb;search\n")
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        for r in rows:
            w.writerow([r["id"], r["primary_name"], r["messier"], r["ngcic"],
                        r["other"], r["otype"], r["ra_hours"], r["dec_deg"],
                        r["vmag"], r["size_am"], r["constellation"],
                        r["wiki"], r["blurb"], r["search"]])
    types = {}
    for r in rows:
        types[r["otype"]] = types.get(r["otype"], 0) + 1
    print(f"wrote {OUT} with {len(rows)} objects "
          f"({counts['NGC/IC']} NGC/IC + {counts['addendum']} addendum "
          f"+ {counts['supplement']} supplement)")
    print("types:", types)
    print("with vmag:", sum(1 for r in rows if r["vmag"]),
          "| faintest:", max((r["vmag"] for r in rows if r["vmag"]), default="-"))


if __name__ == "__main__":
    main()
