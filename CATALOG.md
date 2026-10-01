# CATALOG.md — where the catalogue comes from and how to rebuild it

Scope: the **data chain** only (sources → CSV → DB → web payload → cache tokens).
`AGENTS.md` stays canonical for invariants, release order and deploy; `DECISIONS.md`
records *why* the rules below exist.

## What is generated and what is hand-kept

| File | Status | What it is |
|---|---|---|
| `app/data/openned.csv` | **generated** | THE catalogue (12,176 rows). Never hand-edit. |
| `app/data/supplement.csv` | **hand-kept** | The only non-OpenNGC data: 16 objects in two batches (below). |
| `app/data/catalog_provenance.json` | **generated** | Deterministic source/version, aliases, designation history and mapping notes for every row. |
| `app/popularity_data.py` | **generated** | popularity scores, one per catalogue id. |
| `web/catalog.json` | **generated** | The web payload, derived from the DB. |
| `web/catalog.manifest.json` | generated | Bytes for the current `CATALOG_TOKEN`. |
| `web/catalog-provenance.json` | generated | Web copy of the provenance sidecar; byte-identical to the desktop copy. |
| `web/catalog-provenance.manifest.json` | generated | Bytes for the current `CATALOG_PROVENANCE_TOKEN`. |
| `data/NGC.csv`, `data/addendum.csv` | gitignored input | Downloaded from OpenNGC. |
| `data/popularity_snapshot.json` | gitignored snapshot | The frozen one-time popularity table. The matcher never refreshes it; `--live` exits 2. |

## Sources

1. **OpenNGC `database_files/NGC.csv`** (NGC/IC, 13,970 rows upstream) →
   12,098 objects. Rows whose `Type` says they are not objects are dropped:
   `Dup` (a duplicate that points at its master), `NonEx`, `*`/`**` stars,
   `Other`, `Nova` (see `NGC_guide.txt` upstream).
2. **OpenNGC `database_files/addendum.csv`** (64 rows upstream) → 62 objects: the
   notable objects with no NGC/IC number (Horsehead, Hyades, Coalsack, the
   Magellanic Clouds, the Local Group dwarfs, …).
3. **`app/data/supplement.csv`** → 16 objects OpenNGC cannot carry, in two
   batches. **Batch 1 (0.6.28, 7 rows)** is either absent from OpenNGC or filed
   as a non-object: `abell21` (Medusa Nebula), `jones1` (Headphone Nebula),
   `sh2-276` (Barnard's Loop), `mel20` (Alpha Persei Cluster), `ic1318` (Sadr
   region), `m40` (Winnecke 4), `m73`; identity and astrometry are the project's
   own values from the pre-0.6.26 curated list. **Batch 2 (0.6.29, 9 rows)** is
   outside OpenNGC's *scope* (Sharpless/Barnard/LDN/LBN designations):
   `sh2-101` Tulip Nebula, `sh2-240` Simeis 147, `sh2-129` Flying Bat,
   `sh2-308` Dolphin Head, `sh2-132` Lion Nebula, `b150` Seahorse, `lbn437`,
   `ldn1622`, `pipe` (Pipe Nebula). Their astrometry is SIMBAD ICRS/J2000 and
   their sizes are published values (Sharpless 1959, Lynds 1965 LBN, Wikipedia);
   `DECISIONS.md` records the selection rule and the `vmag` policy. In BOTH
   batches `wiki`/`blurb` are empty, because the catalogue carries no prose, and
   there is no distance field at all (0.6.32: OpenNGC has none - see
   `DECISIONS.md`); batch 2 additionally leaves `vmag` empty - an extended or dark
   nebula has no credible integrated V magnitude, so those rows follow the app's
   magnitude rule and appear only at the slider's maximum, exactly like the
   shipped `b33` (Horsehead), `c9` and `c99`.

## Rebuild, in order

```powershell
# 0. inputs (gitignored): NGC.csv + addendum.csv from
#    https://github.com/mattiaverga/OpenNGC (database_files/)
#    -> data/NGC.csv, data/addendum.csv

# 1. catalogue CSV  (asserts unique ids; RAISES on an unmapped OpenNGC type)
python scripts/build_adv_catalog.py

# 2. popularity     (STRICTLY OFFLINE: replays the frozen one-time snapshot in
#                    data/popularity_snapshot.json. The old network path was removed;
#                    `--live` deliberately exits 2. Replacing the snapshot would
#                    be a separate provenance decision outside this matcher.)
python scripts/match_popularity.py

# 3. database + web payload (a scratch DB is the clean way: Database() seeds and
#    splits both tables on open, rebuilding them when the stored sets disagree)
python -c "import sys; sys.path.insert(0,'.'); from app.database import Database; Database('logs/opengnc/db/nightseam.db')"
python web/build_web.py catalog --db logs/opengnc/db/nightseam.db

# 4. provenance sidecars (requires the same exact OpenNGC input files used for
#    step 1; writes desktop + web JSON and the web manifest)
python scripts/build_catalog_provenance.py

# 5. cache tokens   (a changed web/catalog.json MUST bump CATALOG_TOKEN + manifest;
#                    any changed web module MUST bump its ?v=)
python scripts/check_catalog_token.py
python scripts/check_catalog_provenance.py
python scripts/check_js_tokens.py --update     # refuses while a missed bump exists
python scripts/check_js.py
```

Checks that catch the classic mistakes: `tests/test_catalog_integrity.py`
(unique/token-safe ids, the standard/advanced split, the CSV ↔ JSON ↔ DB counts,
and the **advertised size** in six copy sites), `tests/test_supplement.py` (the
exception set), `tests/test_advanced_catalog.py` (row shape, seeding),
`tests/test_catalog.py` (types, coordinates, the standard-mode rule), and
`tests/test_catalog_provenance.py` (sidecar identity, complete row coverage,
API exposure and cache guard).

## Provenance and reproducible exports

The planning rows stay compact and schema-compatible. The generated provenance
sidecar is the audit layer for them: `objects[<catalog id>]` records the source
dataset/file, a `sha256:<digest>` source version, aliases, structured
designation history, and a mapping note when the display name or supplement
rule changes the source representation. The top-level `catalog.sha256` is the
exact byte hash of `app/data/openned.csv`; the input list records the exact
OpenNGC `NGC.csv`, OpenNGC `addendum.csv`, and committed NightSeam supplement
hashes. No mutable release label is inferred from a download URL.

Both shells load this same sidecar. Desktop exposes `/api/catalog/provenance`
and both catalog views offer a deterministic `nightseam-catalog-provenance.json`
download. The web sidecar has its own cache token and manifest; `check_js.py`,
`check_release.py`, and `check_catalog_provenance.py` fail if the two copies,
the catalog ids, the CSV hash, or the token pairing drift. If a future catalog
rebuild changes provenance bytes, bump `CATALOG_PROVENANCE_TOKEN` in
`web/app.js` and refresh its guard baseline in the same change.

## Rules that are easy to get wrong

- **Never hand-edit a generated file.** A hand-patched `openned.csv` is exactly
  how the 0.6.11 catalogue kept shipping two colliding ids after the fix landed.
- **Standard mode = the `STANDARD_TOP_N` (200) highest scores**, ordered score
  descending then id ascending; an unscored object is never standard. `objects`
  holds that set, `advanced_objects` the rest, and they are disjoint - advanced
  mode is the whole catalogue.
- **Ids are global and token-safe** across both tables together, because the web
  `byId` map and `db.get_object()` must agree; `tests/test_catalog_integrity.py`
  fails otherwise. Never hide a collision with a runtime lookup - fix the data.
- **The supplement is deliberately the only exception** to "the catalogue is
  OpenNGC". Adding a row there means updating `EXPECTED` in
  `tests/test_supplement.py`, the advertised count in six copy sites (the test
  tells you), and this file.
- **Popularity is a silent input**: it is never rendered (`test_ui_invariants.py`),
  and it also selects standard mode - so a refresh changes *which* 200 objects
  the default view shows. The tests pin the ranking identity (M42 first, M31
  second), never the numbers, because the source moves.

## Display mappings applied at build time

- OpenNGC `Cl+N` ("star cluster + nebula") → `nebula`: its famous members are
  M42, IC 1396, the Heart/Soul nebulae, the Cocoon.
- Serpens `Se1`/`Se2` → `Ser`.
- A common name OpenNGC gives to more than one row gets its designation:
  "Eagle Nebula (NGC 6611)", "Antennae Galaxies (NGC 4038)". The bare name stays
  in `search`.
- Where OpenNGC has no common name (78 Messier objects), the Messier number is
  the label ("M101", not "NGC 5457").

## Failure modes seen in practice

- **Padded catalogue numbers**: the popularity source keys the LMC as `PGC 17223`, OpenNGC
  writes `PGC 017223`; the matcher normalizes leading zeros, otherwise famous
  objects silently lose their score.
- **Different names for the same object**: `BARNARD 33` vs `B 33`,
  `CL MELOTTE 111` vs `Mel 111`, `M 102` vs `NGC 5866` → `POPULARITY_ALIASES`
  in `scripts/match_popularity.py`.
- **Non-deterministic matching**: the matcher must score the HIGHEST match over
  every designation; iterating a `set` and breaking on the first hit made scores
  depend on `PYTHONHASHSEED`.
- **Stale DB when generating the payload**: the seeder rebuilds both tables when
  the stored id sets disagree with the CSV + popularity rule, so open a scratch
  DB rather than trusting a long-lived one.
