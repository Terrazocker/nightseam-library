# Catalog data and identity

The local catalog owns its own app/data/openned.csv, supplement.csv, provenance
payload and app/popularity_data.py. It ships 12,176 OpenNGC/supplement objects;
standard mode is the deterministic 200 most popular scored IDs, and advanced
mode contains the remaining rows. IDs are safe and globally unique across both
seeded tables. Database.seed_catalog updates seed data when expected ID sets
change, independently of the website.

Source CSV regeneration uses this repository's scripts/build_adv_catalog.py
with local OpenNGC inputs and the committed supplement. The current provenance
snapshot remains shipped in app/data/catalog_provenance.json. Seed inputs are
tracked and require no network or website checkout at runtime. Never change identity
or supplement membership to conceal a matching or import bug. Catalog source
credits and licensing remain in NOTICE.

Current user libraries are separate from seed catalog data: source data/ and
packaged dist/data/ hold SQLite, caches and setup payloads, and stay ignored.
Archived exposure paths remain under their configured external archive root.
See CATALOG_IMPORT_GUIDE.md for import/session/calibration/dedup behavior.
