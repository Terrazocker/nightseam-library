# NightSeam Catalog

Version **0.1.0**. An independent Windows application for importing exposures,
organizing capture sessions, attaching calibration frames and browsing an archive
sky map. This project was extracted from the current `codex/direction-b-ui`
working tree of NightSeam on October 1, 2026.

## Run and build

```powershell
python -m pip install -r requirements-dev.txt
python main.py
powershell -ExecutionPolicy Bypass -File build.ps1
```

The default source and packaged launches open the catalog. The executable is
`dist/NightSeam.exe`. The retired planning shell was removed on October 10,
2026; the catalog owns the whole application. The application requires no
website checkout, server or network access.

## Library and import

Choose **Import** to add exposures, or configure an archive and reindex it.
Metadata identifies the object and frame type; explicit frame overrides are
available. Darks, Flats and Bias in a selected session attach to that object and
logical session while keeping their original capture dates and file bytes.
Whole-file SHA-256 identity prevents duplicate imports across names and folders.
Reindex reports unsupported archive layouts rather than populating a broken UI.
Each object's Night session runs from sunset to sunrise; Day runs from sunrise
to sunset at the FITS observing location. Files without location can use a known
fallback in Import Exposures. Missing or unreliable metadata remains visibly
unresolved. Reindex refreshes existing membership without moving exposure files.

Source data lives in `data/`; packaged data lives in `dist/data/` beside the
executable. These are separate libraries and remain ignored by Git. The split
preserves both, including configured external archive paths such as `D:/seestar`.
See [CATALOG_IMPORT_GUIDE.md](CATALOG_IMPORT_GUIDE.md) for the current workflow.

## Development

```powershell
python -m pytest tests/ -q --tb=no
python scripts/check_js.py
python scripts/check_browser.py --desktop
```

Read [AGENTS.md](AGENTS.md), [DESIGN.md](DESIGN.md) and
[DEVELOPMENT.md](DEVELOPMENT.md). This repository owns its code, catalog data,
builds, tests and version history. No sibling-repository imports, symlinks or
release coupling are used. `VERSION` defines the independent version.

MIT; see LICENSE and NOTICE for third-party data credits. Previous combined
project history and work patches are recoverable from the ignored
`logs/repository-split/` snapshots. Historical review material retains its
original combined-project version numbers.
