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
Dedup comparisons measure same-size originals even if their database digest
differs: external restores can preserve size and mtime. A bounded process-local
negative-match memo avoids repeated full reads on POSIX behind identity and ctime.
app/file_version.py pins Windows writers/renames and includes an opaque handle
lifetime in NTFS/ReFS versions: timestamps can collide after rapid same-size
writes with restored mtime, so independently opened handles always rehash.
Unsupported filesystems/open writers disable memoization. Only measured bytes
populate the cache, and positive duplicate
matches always rehash. A request-scoped DedupBatch pins up to 4,096 earlier
same-size files and their directory lineage, validating their negative hashes
once per batch. Newly published files enter that index lazily when a later upload
first encounters them as candidates: their current archive bytes are hashed under
live file/directory pins, with lexical path and root identity validation. No upload
or database digest alone admits a candidate. The index retains its root identity
even when initially empty and acquires its lease on the first successful admission.
Positive matches still rehash; failed admission and the 4,096-file bound retain
individual checks. Cancellation reaches both admission and fallback reads, and
temporary handles close on refusal. Publication and transaction boundaries remain
unchanged; only previously committed candidates are pinned.
app/archive_lock.py reserves the archive for these authorized
workers until pins close; unrelated mutations wait. Windows pins require actual
GENERIC_READ access (attribute-only handles do not enforce the sharing policy).
Unsupported versions/open writers/overflow retain individual checks. Admission
waits asynchronously and index building cooperates with cancellation. Image,
integrity, conversion alias and history writes share one Database.import_unit
transaction per file. Reindex measures each original at most once
per scan and keeps cached fingerprints for unchanged known-only scans.
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

Sessions group each object's captures by observing location and actual solar
interval: sunset through the next sunrise is one night; sunrise through sunset
is one day. Membership is half-open, so a capture exactly at sunrise starts the
day and one exactly at sunset starts the night. The shared core uses geometric
solar-center altitude -0.833 degrees, without terrain or elevation corrections.
Coordinates are rounded to 0.001 degrees for stable site identity. Continuous
polar phases use explicitly labelled polar day/night UTC-date intervals.
Catalog IDs identify resolved objects; normalized names identify unresolved
objects. Different targets retain separate Library groups even on the same night.

FITS SITELAT/SITELONG take priority. The Import pane's observing-location fallback
applies only when both header coordinates are absent. Invalid or incomplete
header locations remain visibly Unassigned location, grouped by UTC capture date.
Set the fallback only for files known to originate at that location. Clearing it
does not erase already recorded capture locations. Reindex folders reads headers
and refreshes old session metadata transactionally without moving exposure files.
New folder identities use the fixed solar boundary and a hash of the full key,
so import order, gaps longer than twelve hours and host timezone cannot change them.

DATE-OBS without an offset denotes UTC; explicit offsets are normalized to UTC
and fractions are retained. An absent TIMESYS defaults to UTC. Unsupported
explicit time scales, ambiguous slash dates, date-only values and invalid dates
remain Undated; DATE (file creation) is never substituted for capture time.
Original FITS bytes are preserved. There is no locale-dependent date guess.

Import, reindex and Archive Health discovery recognize .fit/.fits/.fts, tiled
.fz files (including .fits.fz), and their .gz wrappers, case-insensitively.
app/fits_metadata.py reads primary-image metadata or the first nonempty IMAGE /
ZIMAGE extension after an explicitly empty primary. Extension metadata overrides
primary fallbacks; exposure/instrument aliases and coordinate pairs are not mixed
between HDUs. Primary DATE-OBS/TIMESYS values remain fallbacks. One file remains
one exposure record; additional image HDUs are not separately cataloged.

The reader parses headers and skips preceding HDUs without loading image pixels
or invoking tiled pixel codecs. Plain primary headers retain legacy tolerance.
Traversal is limited to 32 HDUs, 1,474,560 bytes per header, 4 MiB total headers,
16 axes for traversed layouts, 64 MiB each of gzip input/expanded traversal, and
five seconds with cooperative checkpoints. Gzip optional header fields also pass
through bounded reads. Reindex propagates cancellation before publication.
Metadata-only reading does not validate unread pixel data, later HDUs, embedded
checksums or unread gzip trailers. Without opt-in Rice, SHA-256 and
integrity baselines describe complete uploaded/stored bytes; plain and compressed
representations remain distinct identities.

Manual Rice conversion is implemented in app/compression.py. Session actions
Compress losslessly and Uncompress use an anchor and exact expected image IDs;
server-side object/session resolution rejects stale membership. Attached
calibration IDs, classification, timestamps and logical anchors stay intact.

Native CFITSIO fpack -r and funpack -C are bundled under vendor/fits-tools/windows-x64
and resolved from resource_dir(), with executable hashes checked against retained
provenance. Every HDU is inspected. Rice compression supports integer BITPIX
8/16/32; unsupported images/directives refuse session conversion. Every packed
file in Safe mode must decompress to the original complete-file SHA-256 before
publication. Manual compression/copy has its own Fast/Safe switch (Safe by
default, including omitted API compression_mode); Fast skips this round trip.
Managed decompression checks the recorded original hash. Imported Rice files
can be decompressed without a historical original-file hash to compare.

image_compression retains original/compressed hashes and original filenames;
current images fingerprints and image_integrity baselines transition together.
Dedup aliases require hashing actual stored bytes against the verified mapping.
Ingest preserves uploaded representations unless the explicit Rice option is
selected for a supported integer image. Compression uses the
independent archive lock and cancellable native workers, with responsive status
and catalog reads. IO stays outside SQLite critical sections. Originals remain
in same-filesystem staging until commit; compression_journal recovers interrupted
preparation/publication on startup. Unresolved recovery blocks archive mutations.

Copy library is manual and takes an explicit destination picker/path. It creates
a new folder outside the configured archive with usable FITS under images/,
a coherent nightseam.db snapshot and manifest.json with membership, metadata
and hashes. Supported integers use Rice; existing Rice and unsupported files
are copied intact. Scratch is on the destination disk. Source archive files
and the active catalog stay unchanged. The copied database points to its copied
images and supports verified decompression. After moving a copy, configure its
new images folder before use. No scheduled backup is added.

Import has an unchecked, per-window Compress on import with lossless Rice option.
The client captures it once per action and passes compress=true on every multipart
chunk, including calibration uploads. The API defaults to false independently.
The accessible Fast/Safe sliding switch defaults to Fast in the UI and supplies
compression_mode=fast/safe on every chunk. Omitted API mode defaults to Safe for
existing clients. Mode selection is disabled when compression is off and locked
during archive operations. A hover/focus/tap info tooltip explains the difference.
After original-byte dedup, supported integer uploads are encoded into a second
private stage using the shared compression.encode_verified helper. Safe mode
requires funpack -C to restore exact full-file SHA-256/size; Fast skips decoding
and trusts reversibility, while retaining original and compressed identities.
Both check Rice output format and source stability; the published file is flushed,
rehash-verified, then committed with its integrity baseline and image_compression
mapping in Database.add_image's existing transaction. Only a verified .fz is
published. Fast provenance is rice_ingest_fast; Safe is verified_rice_ingest.
Stored-byte integrity checking is distinct from verifying restoration. Manual
session/library conversion has an independent mode; missing-file recovery retains
strict verification. Fast manual conversion is labelled rice_conversion_fast.
Uploaded temporary originals survive through commit; external source
files are never modified. Every failure/cancellation cleans uncommitted outputs
and all preparation stages. Cancellation signals native worker checkpoints before
draining IO, preventing removal of a stage still in use.

Unsupported/already-compressed files remain unchanged with per-file reasons;
native verification failures are explicit import failures. Existing duplicates
are not converted. Both known representation hashes can restore an absent managed
file into its current recorded format, retaining IDs, metadata and anchors; the
restored bytes, current fingerprint, integrity baseline and mapping update
atomically. Import results report Rice conversions and reasons for unchanged files.

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
under the archive lock. Stored `session_key`, `session_kind`, `session_start` and
`session_end` define exact interval membership. An explicit calibration attachment
copies those fields from the validated target while preserving its own capture
date and location. Nullable `session_date_obs` also records the logical anchor:
NULL uses the original timestamp, empty text targets an undated session, and an
ISO UTC date targets a dated session. `date_obs` remains the capture instant. Library
sessions publish exact `image_ids`; calibration dates do not extend the capture
interval. Duplicate files retain their previous association and location.

Source runs write to root data/; packaged runs write beside the executable.
The migration moved both root data/ and dist/data/ without changing external
image_dir settings or archived exposure bytes. Stale singleton instance.json
files are quarantined in logs/repository-split and regenerated on launch.

## Independent build

Session rows show a fixed-size storage symbol (compressed, uncompressed, mixed,
or unknown), with accessible counts and matching selected-session facts.
Nullable images.storage_compressed records the selected exposure image's format
from the existing metadata read, including gzip magic and tiled ZIMAGE headers;
filenames are not format evidence. Ingest, reindex, conversion, restoration and
copied catalogs maintain it with their normal transactions. Accepted external
changes clear it. Migration classifies historical Rice mappings only when the
current stored digest matches a known representation. Other historical records
remain unknown until reindex, so browsing never scans FITS files for symbols.

Archive health is separate from reindex. app/archive_health.py owns one
cancellable background job/report per instance. POST /api/library/health starts
a quick/deep job, GET on that path returns the latest job; GET /{id} polls,
POST /{id}/cancel requests cancellation, and GET /{id}/report downloads terminal
JSON. POST /{id}/acknowledge requires action (forget_missing or accept_contents)
and confirmed:true. Default scope:image requires image_id. Deletions also support
scope:session with a server-derived session_id or scope:all. Public deletion_sessions
summarize all retained missing findings, including those beyond the 200-detail display.
Object identity plus canonical stored solar session key defines a group, including
explicit calibration anchors. All selected records/paths are revalidated before one
SQLite transaction mutates any record; repeated requests are idempotent. Only retained completed-scan findings
qualify. Scans hold the independent archive lock and release SQLite after a
short consistent snapshot. Job/status requests remain responsive during IO.

The additive image_integrity table preserves import/historical/reindex baselines
independently of mutable dedup caches. Explicit acceptance updates baseline/cache
with user_accepted provenance; deletion removes selected missing records/baselines.
Both remove obsolete compression aliases. Healthy members of a session remain intact.
Both atomically retain timestamps and previous/new hashes in archive_health_actions.
Exact snapshot comparisons, archive identity and fresh disk reads reject stale
findings. Original files, object definitions and import history stay intact.
Discovery uses no-follow directory descriptors on POSIX and pinned lineage
handles/reparse rejection on Windows. Reports retain 10,000 findings (200 shown)
and count omitted findings. Private acknowledgement snapshots never enter API
reports. Starts/acknowledgements serialize without holding SQLite during IO.

Upload streams, hashing, header matching and optional Rice preparation run in
bounded workers (up to CPU count, at most 24) outside archive_lock. Original and
packed stages stay private until their ordered publication turn. One failed SQL
write stops all later publication in that request; cancellation drains every
worker before cleaning staging or releasing multipart sources. Publication,
duplicate/restoration decisions and commits retain archive_lock. Missing-file
representation recovery remains strictly verified. Resolver snapshots persist
across requests until catalog table triggers or SQLite data_version invalidate
them; each snapshot memoizes up to 4,096 hint/coordinate results. Native utility
hashes are cached only behind supported file versions. Windows revalidates
provenance and both binaries at every tools lookup; manual session jobs share one
verified lookup with their workers. Compression alias and import-run lookups have
additive indexes.

Session conversion/copy uses bounded ThreadPoolExecutor workers for complete
per-file preparation and publication. Unique <id>.output/<id>.roundtrip.fit
stages replace the former shared roundtrip path. The coordinator alone updates
reports, orders the complete journal plan, commits the catalog and runs recovery.
Explicit iterator closure drains workers before rollback, even if a progress
callback raises. Live GENERIC_READ directory handles are reused across workers;
cached containment checks immediate-parent identity/reparse state and the lexical
leaf before resolution, including previously unseen same-parent links. Pins close
before staging cleanup/export rename. Source-version pins can replace repeated
source hashes while held; unsupported versions keep full reads. Initial baseline,
pre-publication and post-swap checks remain. Originals stay retained until commit.
Fast manual provenance is rice_conversion_fast; Uncompress always verifies a
known original hash. Progress separates processed/total from published/
publication_total and labels checking_originals, publishing, committing and
cleaning. The UI then refreshes the library before declaring Complete, rebuilding
session actions after releasing the operation lock. Resumed jobs require a fresh
action selection before restarting.

The exposure preview, thumbnails, inline session expansion and Trash application
features were retired on October 14, 2026. Their JS modules, modal markup, pixel
renderer and public API routes are removed. Selecting a session only updates
its facts/calibration actions, keeping exact image-ID membership for folder
access and attachment anchors. Import's primary-header parser remains intact.
ASGI lifespan stops/joins health workers, and main.py signals shutdown before
Uvicorn drains active requests. Health retains host/origin and no-store/nosniff
response guards.

Legacy image_trash rows, retained payloads and recovery manifests are preserved.
app/archive_trash.py is recovery-only: it cannot create new trash entries.
Startup reconciles previously interrupted operations without waiting on a busy
archive lock. Unresolved legacy intents continue to block archive changes and
health acknowledgements. Managed retained-file folders remain excluded from
health/reindex discovery so they cannot be accidentally reimported or discarded.
Completed retained entries are never automatically restored or deleted.

With NightSeam closed, for explicit maintenance recovery use
`python scripts/restore_legacy_trash.py --database PATH` to list existing entries,
or add `--restore TOKEN` to restore one. The database must already exist and is
never inferred from a user profile. Restore keeps original ID, metadata/session
membership and integrity baseline, verifies retained bytes and refuses occupied
paths or known active duplicate bytes. Use the selected original archive and
preserve files if recovery reports a conflict. This is a separate maintenance
command, not an application feature.

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
setup printed by scripts/check_build_dependencies.py. Embedded mini-racer 0.14.1
executes the generated shared JavaScript solar core in headless and packaged runs;
Node is not a runtime dependency and Python contains no duplicate astronomy math.
V8 contexts close after cache misses; the bounded cache contains plain records.
The complete artifact closure now has 46 pins. Runtime requirements live in
requirements.txt; the lock describes tested artifacts, not byte-for-
byte reproducibility across OS or Python patch versions.

Archive health and legacy recovery readers compare Windows path/descriptor identity
using device, inode, size and modification time. Python 3.12 can report creation
time through stat and change time through fstat, so ctime is compared only within
the same metadata source. Descriptor observations before/after reading retain
change-time detection even when an external writer restores size and mtime.

## Continuous validation and support

`.github/workflows/windows-validation.yml` selects Linux source checks and
Windows source/build/packaged browser/native checks for pull requests to main
and pushes to main. Markdown-only diffs skip expensive jobs; unknown diffs run
everything. The terminal `validation` job requires every selected platform to
succeed, giving branch protection a stable check name. Branch protection is
configured separately. Main-branch triggers and the weekly Sunday 02:17 UTC
schedule take effect when the workflow is merged into main.

Manual dispatch runs all correctness checks. Select `run_benchmarks=true` to
also run the 512-file packaged import benchmark and isolated source profiler;
the weekly run includes both. Artifacts retain logs, JUnit, measured JSON and
packaged screenshots. The browser runner remains separate from application pins.

The toolbar's **Support report** downloads a local JSON attachment. It contains
application/Python/OS/selected package versions, bundled FITS utility integrity,
browser/native capability flags and numeric Chromium version. At most 25 warning
or error categories from the current process are included. Raw messages,
tracebacks, paths, library contents, settings and environment values are excluded.
The report does not send anything externally and remains available during archive
operations. Frozen builds explicitly retain the selected distribution metadata.

`python scripts/profile_import.py` creates a private source server beneath
`logs/browser/` and uses the benchmark's real HTTP uploads and integrity audits.
Its default 512 equal-size FITS files and 24-file requests match the packaged
workload. `--count`, `--batch-size` and `--output` allow smaller investigations.
The private bootstrap installs selected synchronous function wrappers across
worker threads, actual hash-byte counters and SQLite transaction counts. Normal
application instances have no profiling endpoints or wrappers. Fixtures, startup,
audits and duplicate/edit probes stay outside the measured interval. Exclusive
phase CPU times can be added; wall times overlap across threads. Unwrapped work
and instrumentation overhead remain unattributed. This selected-function source
profile is not a packaged throughput baseline or a complete program call graph.
Owned processes, listener and scratch must close successfully for the run to pass.

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
Before any check, startup explicitly sets the archive inside its private scratch
and verifies the setting through state; refusal or a mismatched path stops the
gate and cleans its owned process/data. A private executable/database alone is
insufficient because the application's default archive is in Documents.
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
