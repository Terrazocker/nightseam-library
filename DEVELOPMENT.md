# NightSeam Catalog development

This independently owned snapshot begins at version 0.1.0 on October 1, 2026.
The website is developed in the sibling Nightseam-programm repository; neither
project reads source, databases, build outputs or versions from the other.

## Runtime

main.py hosts the local FastAPI application in app/server.py and opens the
catalog shell. The client is vanilla ES-module JavaScript and CSS, with no npm
or bundler. static/catalog.html owns Library, Import Exposures and the fixed
archive Sky Map. Native pywebview/Windows chrome provides window behavior.
The older full planner shell is retained only for explicit maintenance launches;
its storage schema and backup fields remain compatible with existing libraries.

## Data and imports

app/database.py owns SQLite, additive migrations and catalog seed records.
app/ingest.py parses exposure headers, matches objects, stages/copies files,
hashes complete bytes for global duplicate identity and reindexes archive trees.
app/imglib.py derives objects and capture sessions for the interface.
app/paths.py contains safe path and writable/resource-directory boundaries.
app/backup.py validates/restores a complete setup using transactional staging.

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
