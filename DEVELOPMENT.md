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
Recovery is file-first: exposure bytes stay in the archive. SQLite keeps a
compatible rolling `nightseam.db.backup` plus up to five dated pre-migration
and five post-open snapshots beside the database. SQLite's backup API includes
committed WAL contents; snapshots pass quick_check before atomic publication.
An existing database must have a successful pre-migration snapshot before any
migration runs. Backup and retention failures are logged and stop opening.
Structural validation cannot determine whether every user edit is correct.

**Reindex folders** adds unrecorded files and repairs a missing stored path only
when a trusted complete-byte SHA-256 and size identify exactly one record and
one discovered destination. Repair preserves the original ID, manual metadata,
frame class and logical session anchor. Ambiguous matches are reported and left
unchanged; missing records are never deleted. Files without a trusted prior
fingerprint cannot be relinked automatically. Reindex does not move archive
files or reverse a failed rename. Scan/hash work runs outside SQLite critical
sections, and the planned record changes publish in one transaction. Disconnect
or request cancellation stops work cooperatively before publication; once the
transaction commits, its completed result remains durable.

Library renames preserve logical object groups. If different names share a
sanitized archive folder, a rename that would move that folder is refused with
HTTP 409 before any file or database change; an edit that keeps the same folder
may proceed. A group spread across physical roots, a mismatched stored root or
a missing stored file refuses a folder-changing rename before mutation. Stored
paths change only through the actual move map. Directory enumeration finishes
before a whole-folder move; its
undo moves the directory back independently of the file map, including empty
folders and unrecorded children. Database failures trigger filesystem undo.
If filesystem recovery is refused,
the API reports incomplete recovery and logs the affected relative paths;
inspect those paths and recover the archive before using Reindex folders.

Automatic import session lookup uses the same object identity as the library:
catalog ID for resolved objects, normalized name for unresolved objects only.
Physical object slugs may still differ for historical spelling aliases. The
twelve-hour adjacent-capture gap remains the session policy; it is not a local
calendar-night boundary. Later/backdated imports do not rename existing session
folders. Legacy slash timestamps retain the documented MM/DD fallback when the
first field is at most twelve; ISO capture timestamps avoid that ambiguity.

Uploads report validation failures per file and can continue. Database errors
stop the remaining request and later UI chunks, preserving earlier confirmed
results while marking uncertain and unattempted files separately. Import-history
failures roll back their own transaction; earlier committed exposures remain.
Unexpected invariant failures are not silently converted into success.

CSV export quotes every cell and prefixes formula-leading textual fields with
a tab for spreadsheet viewing. Numeric fields are not rewritten. Original
database values and JSON export remain unchanged; JSON is the lossless choice
for programmatic metadata exchange. Spreadsheet import/resave behavior varies.

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

VERSION is the catalog version authority. Build and release tooling belong here;
no website version.json, changelog, database or deploy is required. core/ and
its generated static/js/core tree are local copies, not sibling references.
Catalog source CSVs and popularity policy live in app/ and are owned here.
Current tests use private ignored directories; browser gates launch private
executable copies. See AGENTS.md for commands and security invariants.

Desktop artifacts use Windows x64 CPython 3.12 and the exact active dependency
closure in requirements-build-lock.txt. build.ps1 checks installed versions and
dependency closure before any build/backup mutation, including httpx2 and the
compiler. It never installs packages automatically. The existing build
environment already matches the lock. For a new environment, follow the manual
setup printed by scripts/check_build_dependencies.py. Runtime lower bounds
remain in requirements.txt; the lock describes tested artifacts, not byte-for-
byte reproducibility across OS or Python patch versions.

## Workspace upkeep

Tests allocate a separate directory under ignored `logs/.pytest-tmp/` for each
test, including its data and fallback archive. Test-owned database connections
close before directory removal. Removal retries transient Windows locks and a
persistent refusal fails teardown instead of silently leaving files behind.
Pytest's cache provider is disabled; no root pytest scratch is required.
JS syntax gates likewise own their scratch under logs/ and report cleanup
failures; housekeeping covers abandoned JS scratch as well. Their tests use
private checker stores and do not require other invocations' logs to be empty.

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
