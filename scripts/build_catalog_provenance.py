#!/usr/bin/env python3
"""Build the reproducible catalog provenance sidecar.

The catalog payload intentionally keeps its stable 14-column schema.  This
sidecar carries the metadata that does not belong in every planning row:
source file and byte version, aliases, and the designation trail used to map a
source record to NightSeam's safe catalog id.

Run from the repository root after placing the OpenNGC ``NGC.csv`` and
``addendum.csv`` files in ``data/``::

    python scripts/build_catalog_provenance.py

The default command writes the same deterministic JSON to the desktop data
directory and the static web directory.  The source files are not shipped;
their SHA-256 digests make the exact input revision auditable and reproducible.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "app" / "data" / "openned.csv"
DEFAULT_NGC = ROOT / "data" / "NGC.csv"
DEFAULT_ADDENDUM = ROOT / "data" / "addendum.csv"
SUPPLEMENT = ROOT / "app" / "data" / "supplement.csv"
DEFAULT_OUTPUTS = (
    ROOT / "app" / "data" / "catalog_provenance.json",
    ROOT / "web" / "catalog-provenance.json",
)
PROVENANCE_MANIFEST = ROOT / "web" / "catalog-provenance.manifest.json"
PROVENANCE_TOKEN = "1"

OPENNGC_URL = "https://github.com/mattiaverga/OpenNGC"
OPENNGC_LICENSE = "CC BY-SA 4.0"
CATALOG_COLUMNS = (
    "id", "primary_name", "messier", "ngcic", "other", "otype",
    "ra_hours", "dec_deg", "vmag", "size_am", "constellation", "wiki",
    "blurb", "search",
)
SUPPLEMENT_SOURCE = "NightSeam supplement"
SUPPLEMENT_LICENSE = "NightSeam project data"
SAFE_SOURCE_TYPES = {"NGC.csv", "addendum.csv"}
SKIP_TYPES = {"Dup", "*", "**", "Other", "NonEx", "Nova"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def fmt_desig(name: str) -> str:
    """Mirror build_adv_catalog.fmt_desig for source-to-catalog mapping."""
    m = re.match(r"^([A-Za-z]+)0*(\d+)(?:\s*-\s*0*(\d+))?$", name)
    if not m:
        return name
    out = f"{m.group(1)} {int(m.group(2))}"
    return f"{out}-{int(m.group(3))}" if m.group(3) else out


def norm(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def split_values(value: str) -> list[str]:
    """Split OpenNGC's pipe/comma lists while keeping useful text bounded."""
    out: list[str] = []
    for part in re.split(r"[|,]", value or ""):
        item = " ".join(part.split()).strip()
        if item and item not in out:
            out.append(item)
    return out


def add_unique(values: list[str], value: str) -> None:
    value = " ".join((value or "").split()).strip()
    if value and value.casefold() not in {x.casefold() for x in values}:
        values.append(value)


def source_descriptor(path: Path, dataset: str, license_name: str,
                      url: str | None = None) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"catalog provenance source not found: {path}\n"
            "  download the OpenNGC database_files sources and pass their "
            "paths with --ngc/--addendum, or place them in data/")
    item = {
        "dataset": dataset,
        "path": relpath(path),
        "sha256": sha256(path),
        "source_version": "sha256:" + sha256(path),
        "license": license_name,
    }
    if url:
        item["url"] = url
    return item


def read_openngc(path: Path, source_info: dict) -> dict[str, dict]:
    records: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f, delimiter=";"):
            name = (raw.get("Name") or "").strip()
            if not name or (raw.get("Type") or "").strip() in SKIP_TYPES:
                continue
            oid = norm(fmt_desig(name))
            if oid in records:
                raise SystemExit(f"duplicate source designation {oid!r} in {path}")
            records[oid] = {"raw": raw, "source": source_info}
    return records


def read_catalog() -> list[dict[str, str]]:
    if not CATALOG.is_file():
        raise SystemExit(f"catalog not found: {CATALOG}")
    rows: list[dict[str, str]] = []
    with CATALOG.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(
            (line for line in f if line.strip() and not line.startswith("#")),
            delimiter=";",
        ):
            if len(row) != len(CATALOG_COLUMNS):
                raise SystemExit(
                    f"{CATALOG.name}: {len(row)} columns, expected "
                    f"{len(CATALOG_COLUMNS)}")
            rows.append(dict(zip(CATALOG_COLUMNS, row)))
    return rows


def read_supplement(path: Path, source_info: dict) -> dict[str, dict]:
    records: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter=";"):
            if not row or not row[0].strip() or row[0].startswith("#"):
                continue
            if len(row) != len(CATALOG_COLUMNS):
                raise SystemExit(
                    f"{path.name}: {len(row)} columns, expected "
                    f"{len(CATALOG_COLUMNS)}")
            record = dict(zip(CATALOG_COLUMNS, row))
            oid = record["id"].strip()
            if oid in records:
                raise SystemExit(f"duplicate supplement id {oid!r}")
            records[oid] = {"row": record, "source": source_info}
    return records


def designation_history(raw: dict, dataset: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(value: str, field: str, kind: str) -> None:
        value = " ".join((value or "").split()).strip()
        if not value:
            return
        if any(x["designation"].casefold() == value.casefold() for x in items):
            return
        items.append({"designation": value, "source_field": field, "kind": kind})

    add(fmt_desig(raw.get("Name", "")), "Name", "canonical")
    messier = (raw.get("M") or "").strip()
    if messier:
        m_number = messier.removeprefix("M").strip()
        add("M" + str(int(m_number)) if m_number.isdigit() else messier,
            "M", "messier")
    for field, prefix in (("NGC", "NGC "), ("IC", "IC ")):
        for value in split_values(raw.get(field, "")):
            add(fmt_desig(prefix + value), field, "cross-reference")
    return items


def supplement_designation_history(row: dict[str, str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(value: str, field: str, kind: str) -> None:
        value = " ".join((value or "").split()).strip()
        if not value or any(x["designation"].casefold() == value.casefold()
                            for x in items):
            return
        items.append({"designation": value, "source_field": field, "kind": kind})

    add(row["ngcic"], "ngcic", "canonical")
    add(row["messier"], "messier", "messier")
    add(row["id"], "id", "catalog-id")
    for value in split_values(row["other"]):
        add(value, "other", "alternate")
    return items


def aliases_for_catalog_row(row: dict[str, str], raw: dict | None) -> list[str]:
    values: list[str] = []
    for field in ("primary_name", "messier", "ngcic"):
        add_unique(values, row.get(field, ""))
    for value in split_values(row.get("other", "")):
        add_unique(values, value)
    if raw:
        for field in ("Common names", "Identifiers"):
            for value in split_values(raw.get(field, "")):
                add_unique(values, value)
    return sorted(values, key=str.casefold)


def object_record(row: dict[str, str], source_record: dict | None,
                  supplement_record: dict | None) -> dict:
    if supplement_record:
        source = supplement_record["source"]
        history = supplement_designation_history(row)
        note = "NightSeam supplement row; see CATALOG.md for source notes."
        raw = None
    else:
        if not source_record:
            raise SystemExit(
                f"catalog id {row['id']!r} is not present in NGC.csv or "
                "addendum.csv; rebuild provenance with the matching OpenNGC "
                "source revision")
        raw = source_record["raw"]
        source = source_record["source"]
        history = designation_history(raw, source["dataset"])
        note = ""
        canonical = history[0]["designation"] if history else ""
        if canonical and row["primary_name"].casefold() != canonical.casefold():
            note = "Display name comes from OpenNGC common-name/Messier precedence."

    result = {
        "catalog_id": row["id"],
        "source": source["dataset"],
        "source_file": source["path"],
        "source_version": source["source_version"],
        "aliases": aliases_for_catalog_row(row, raw),
        "designation_history": history,
    }
    if note:
        result["mapping_note"] = note
    return result


def build(ngc: Path, addendum: Path, supplement: Path) -> dict:
    ngc_info = source_descriptor(ngc, "OpenNGC", OPENNGC_LICENSE, OPENNGC_URL)
    add_info = source_descriptor(addendum, "OpenNGC", OPENNGC_LICENSE, OPENNGC_URL)
    supplement_info = source_descriptor(
        supplement, SUPPLEMENT_SOURCE, SUPPLEMENT_LICENSE)
    raw = read_openngc(ngc, ngc_info)
    for oid, record in read_openngc(addendum, add_info).items():
        if oid in raw:
            raise SystemExit(f"OpenNGC source id collision: {oid}")
        raw[oid] = record
    supplements = read_supplement(supplement, supplement_info)
    rows = read_catalog()
    objects: dict[str, dict] = {}
    for row in rows:
        oid = row["id"]
        if oid in supplements:
            if oid in raw:
                raise SystemExit(f"source/supplement id collision: {oid}")
            record = object_record(row, None, supplements[oid])
        else:
            record = object_record(row, raw.get(oid), None)
        objects[oid] = record

    return {
        "schema_version": 1,
        "generator": "scripts/build_catalog_provenance.py",
        "source_version_policy": (
            "source_version is sha256:<digest> over the exact source bytes; "
            "no mutable release label is inferred"),
        "catalog": {
            "path": relpath(CATALOG),
            "row_count": len(rows),
            "sha256": sha256(CATALOG),
        },
        "inputs": [ngc_info, add_info, supplement_info],
        "objects": objects,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngc", type=Path, default=DEFAULT_NGC)
    parser.add_argument("--addendum", type=Path, default=DEFAULT_ADDENDUM)
    parser.add_argument("--supplement", type=Path, default=SUPPLEMENT)
    parser.add_argument(
        "--out", type=Path, action="append", dest="outputs",
        help="write one output path (repeatable; defaults to desktop + web)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = build(args.ngc, args.addendum, args.supplement)
    outputs = tuple(args.outputs or DEFAULT_OUTPUTS)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    payload_bytes = payload.encode("utf-8")
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload_bytes)
        print(f"{output}: {len(data['objects'])} objects")
    if not args.outputs:
        PROVENANCE_MANIFEST.write_text(
            json.dumps({
                "token": PROVENANCE_TOKEN,
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        print(f"{PROVENANCE_MANIFEST}: token {PROVENANCE_TOKEN}")


if __name__ == "__main__":
    main()
