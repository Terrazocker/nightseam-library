# NightSeam Catalog

[![Catalog validation](https://github.com/Terrazocker/nightseam-library/actions/workflows/windows-validation.yml/badge.svg)](https://github.com/Terrazocker/nightseam-library/actions/workflows/windows-validation.yml)

Version **0.1.0**. An independent catalog application with a native Windows
window and a Linux browser build, for importing exposures,
organizing capture sessions, attaching calibration frames and browsing an archive
sky map. It began as the catalog part of NightSeam and has been developed as an
independent application since October 1, 2026.

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

For the portable Linux x86_64 browser bundle, build commands and compatibility
details, see [LINUX.md](LINUX.md). The bundle includes Python and its JavaScript
engine; a system browser is required.

## Library and import

Choose **Import** to add exposures, or configure an archive and reindex it.
Metadata identifies the object and frame type; explicit frame overrides are
available. Darks, Flats and Bias in a selected session attach to that object and
logical session while keeping their original capture dates and file bytes.
Whole-file SHA-256 identity prevents duplicate imports across names and folders.
Reindex reports unsupported archive layouts rather than populating a broken UI.
Each object's Night session runs from sunset to sunrise; Day runs from sunrise
to sunset at the FITS observing location. Files without location can use a known
fallback in Import › Archive settings. Missing or unreliable metadata remains visibly
unresolved. Reindex refreshes existing membership without moving exposure files.

Source data lives in `data/`; packaged data lives in `dist/data/` beside the
executable. These are separate libraries and remain ignored by Git. The split
preserves both, including archive folders configured on external drives.
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

GitHub Actions runs the Linux and Windows test suites, both packaged builds and
the packaged browser gates on every pull request and push to `main`; see
[`.github/workflows`](.github/workflows) and
[RELEASE_WORKFLOW.md](RELEASE_WORKFLOW.md).

MIT; see LICENSE, and NOTICE for third-party data credits. Historical review
material retains the version numbers of the earlier combined NightSeam project.
