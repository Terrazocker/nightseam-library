# NightSeam Catalog development

This independently owned snapshot begins at version 0.1.0 on October 1, 2026.
The website is developed in the sibling Nightseam-programm repository; neither
project reads source, databases, build outputs or versions from the other.

## Runtime

main.py hosts the local FastAPI application in app/server.py and opens the
catalog shell. The client is vanilla ES-module JavaScript and CSS, with no npm
or bundler. static/catalog.html owns Library, Import Exposures and the fixed
archive Sky Map. Native pywebview/Windows chrome provides window behavior.
The retired planning shell (sites, rigs, panoramas, custom-target editing) was
removed on October 10, 2026; existing databases that still carry those tables
keep them unread, and legacy libraries' custom objects still resolve.

## Data and imports

app/database.py owns SQLite, additive migrations and catalog seed records.
app/ingest.py parses exposure headers, matches objects, stages/copies files,
hashes complete bytes for global duplicate identity and reindexes archive trees.
app/imglib.py derives objects and capture sessions for the interface.
app/paths.py contains safe path and writable/resource-directory boundaries.
Recovery is file-first: the archive folder is the source of truth, SQLite keeps
a rolling `nightseam.db.backup` next to the database (taken with SQLite's own
backup API under the connection lock), and **Reindex folders** rebuilds library
rows from the archive.

Library renames preserve logical object groups. If different names share a
sanitized archive folder, a rename that would move that folder is refused with
HTTP 409 before any file or database change; an edit that keeps the same folder
may proceed. Directory enumeration finishes before a whole-folder move; its
undo moves the directory back independently of the file map, including empty
folders and unrecorded children. Database failures trigger filesystem undo.
If filesystem recovery is refused,
the API reports incomplete recovery and logs the affected relative paths;
inspect those paths and recover the archive before using Reindex folders.

`POST /api/images/ingest` may receive a positive `session_anchor_id` for an
existing image, with dark/flat/bias frame type. The server revalidates the target
under the archive lock. Nullable `session_date_obs` stores logical membership:
NULL uses the original timestamp, empty text targets an undated session, and an
ISO UTC date targets a dated session. `date_obs` remains original. Library
sessions publish exact `image_ids`; calibration dates do not extend the capture
interval. Duplicate files retain their previous association and location.

Source runs write to root data/; packaged runs write beside the executable.
The migration moved both root data/ and dist/data/ without changing external
image_dir settings or archived exposure bytes. Stale singleton instance.json
files are quarantined in logs/repository-split and regenerated on launch.

## Independent build

Archive health is separate from reindex. app/archive_health.py owns one
cancellable background job/report per instance. POST /api/library/health starts
a quick/deep job, GET on that path returns the latest job; GET /{id} polls,
POST /{id}/cancel requests cancellation, and GET /{id}/report downloads terminal
JSON. POST /{id}/acknowledge requires image_id, action (forget_missing or
accept_contents), and confirmed:true. Only retained completed-scan findings
qualify. Scans hold the independent archive lock and release SQLite after a
short consistent snapshot. Job/status requests remain responsive during IO.

The additive image_integrity table preserves import/historical/reindex baselines
independently of mutable dedup caches. Explicit acceptance updates baseline/cache
with user_accepted provenance; deletion removes only the selected record/baseline.
Both atomically retain timestamps and previous/new hashes in archive_health_actions.
Exact snapshot comparisons, archive identity and fresh disk reads reject stale
findings. Original files, object definitions and import history stay intact.
Discovery uses no-follow directory descriptors on POSIX and pinned lineage
handles/reparse rejection on Windows. Reports retain 10,000 findings (200 shown)
and count omitted findings. Private acknowledgement snapshots never enter API
reports. Starts/acknowledgements serialize without holding SQLite during IO.

GET /api/images/{id}/preview returns PNG with X-FITS-Size, X-FITS-HDU and
X-FITS-Plane. Optional size is 64..1024 (default 512). app/fits_preview.py uses
Astropy row sections without full-array loads; a narrow borrowed-file interface
enforces 64 MiB reads even on actual disk files. Row width is limited to 100,000,
HDU traversal to 64, axes to eight, and compressed tile bands to eight million
decoded pixels. Finite samples use a 1/99.5-percentile interval and AsinhStretch(.1);
Pillow encodes 8-bit grayscale with lower FITS origin. No derivatives are stored.
Containment/opened/final identities are checked; archive conflicts fail promptly.
Import's primary-header parser remains unchanged; legacy preview helpers delegate
to the Astropy renderer. Runtime dependencies are Astropy 7.x, NumPy and Pillow.
The authored scripts/pyinstaller_hooks/hook-astropy.py bundles runtime modules,
metadata/data/parser tables without optional WCS plotting or upstream tests;
Astropy's initializer-required tests.runner helper stays included.

ASGI lifespan stops/joins health workers. main.py signals shutdown before Uvicorn
drains active requests; acknowledgement and preview reads honor that signal.
The vanilla JS health/preview modules guard stale requests, clean up blob URLs,
and manage modal inert/focus state. Health uses the existing archive-action lock;
preview selection uses exact session image IDs and loads only on demand.
Health and preview APIs retain host/origin guards and no-store/nosniff responses.
Native Windows packaged validation is tracked in BUGS.md.

VERSION is the catalog version authority. Build and release tooling belong here;
no website version.json, changelog, database or deploy is required. core/ and
its generated static/js/core tree are local copies, not sibling references.
Catalog source CSVs and popularity policy live in app/ and are owned here.
Current tests use private ignored directories; browser gates launch private
executable copies. See AGENTS.md for commands and security invariants.

## Workspace upkeep

Tests allocate a separate directory under ignored `logs/.pytest-tmp/` for each
test, including its data and fallback archive. Test-owned database connections
close before directory removal. Removal retries transient Windows locks and a
persistent refusal fails teardown instead of silently leaving files behind.
Pytest's cache provider is disabled; no root pytest scratch is required.
JS syntax gates likewise own their scratch under logs/ and report cleanup
failures; housekeeping covers abandoned JS scratch as well.

The packaged browser gate uses `logs/browser/` and owns its executable and
children through a private Windows Job Object (a process group on POSIX).
Windows starts the executable suspended, assigns ownership, then resumes it.
Its TEMP/TMP/TMPDIR point into private scratch so frozen extraction files are
removed with the test data, even when the process tree is forcibly stopped.
Teardown waits for the process tree and listener to exit before removing files;
startup and cleanup failures are gate failures. A harness crash closes the job
and terminates its Windows children. No unrelated application is terminated.

Use `python scripts/clean_workspace.py` to preview housekeeping, then add
`--apply` to remove caches and test scratch older than 24 hours. Live or unknown
owner markers are preserved. `--verbose` lists every path. `--build-output`
also removes compilation scratch, known retired build candidates and excess
executable backups, retaining the newest three. The current executable, current
release, both application data stores, external archives, build environment,
UI references and split recovery evidence are preserved. Linked paths and
tracked content cause the entire cleanup plan to be refused before deletion.
Cleanup failures return a nonzero exit code and name the affected path.

Successful builds run housekeeping automatically after releasing their build
owner lock. Failed builds keep diagnostics and executable backups. Before a
handoff, run the relevant gates, apply housekeeping and check `git status`;
commit the verified source changes. Do not use `git clean -fdx`, which would
delete ignored application data and recovery material.
