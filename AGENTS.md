# NightSeam Catalog agent instructions

This is the canonical NightSeam Catalog application repository, published at
https://github.com/Terrazocker/nightseam-library (default branch `main`; `VERSION` owns the version).
The NightSeam website lives in a separate repository. Do not import, build,
write, tag or deploy through it.

## Architecture and invariants

- Read DESIGN.md before UI work. Workbench is the only theme; role tokens live
  in static/themes.css. Use the native Windows frame for moving, resizing,
  caption buttons, system menu and Snap. Keep interface density stable as
  available workspace changes; no proportional root-font scaling.
- main.py opens static/catalog.html by default, both from source and frozen.
  app/ owns storage/platform/IO; static/ owns the local interface. The planning
  shell was retired on October 10, 2026 (route families and planner tables are
  gone; legacy databases keep unread planner tables). core/ is a project-owned
  snapshot of astronomy/geometry ES modules; regenerate its local static bake
  with scripts/build_core.py and keep the manifest/hash/export contracts intact.
- Never edit baked core trees or generated stubs manually. Do not recreate
  astronomy math in app/ or scripts/. Published reference anchors are the
  correctness authority; never update fixtures to hide a formula regression.
- Stored paths must pass paths.safe_rel and paths.safe_join; ids used in
  filenames must pass safe_token. The archive folder is user-configured; never
  write outside it (the setup-ZIP restore that once carried an imported
  image_dir was retired on October 10, 2026).
- Ingest and reindex stage destination-filesystem writes and publish them only
  on database commit; every refusal or exception rolls back both sides. No
  journal or .part leftovers.
- Dedup identity is SHA-256 of complete exposure bytes. A duplicate keeps its
  original record, class and location. Explicit session calibration attachment
  uses a validated stored image anchor and preserves the original date_obs;
  stored solar session_key and exact image_ids determine membership, with
  session_date_obs retaining explicit calibration anchors. Automatic sessions
  use sunset-to-sunrise or sunrise-to-sunset at the FITS/default observing site;
  missing location/time stays visibly unresolved, never a twelve-hour gap guess.
- Reindex requires recognized frame-directory structure and reports malformed
  layouts before replacement. Archive operations honor the independent archive
  lock, cancellation and staging cleanup; do not block the async event loop or
  hold long SQLite critical sections while copying or hashing.
- Tests never touch profile archives or actual source/packaged data. Keep test
  scratch under ignored logs/. Catalog IDs are global and safe; catalog
  provenance must survive the split, and legacy databases (planner tables,
  custom targets) must keep opening and resolving.

## Commands and release

- Run: python main.py; --console for diagnostics, --no-browser for headless,
  --port N for a separate listener.
- Tests: python -m pytest tests/ -q --tb=no. Run relevant modules while editing
  and the full suite once at the end. JS: python scripts/check_js.py.
- Browser: python scripts/check_browser.py --desktop, using a freshly compiled
  executable, a private data copy and an explicitly configured/verified private
  archive before any upload. A browser launch failure is a failure,
  never a skip; this machine may need elevated browser execution.
- Build: powershell -ExecutionPolicy Bypass -File build.ps1. Never kill a user's
  application to run a gate. Back up an executable before replacement.
- Housekeeping: python scripts/clean_workspace.py previews disposable output;
  add --apply to clean, --build-output for build scratch/retired candidates.
  Apply housekeeping before handoff and verify Git status. Preserve data,
  archives, releases and recovery evidence; never use git clean -fdx here.
  Test/tool cleanup failures must fail visibly. Successful builds keep three
  executable backups and remove build scratch automatically.
- VERSION owns the project version. The clean initial commit is tagged v0.1.0.
  Do not fake a clean frozen bake or inherit old combined v0.6.x tags. Inspect
  scripts/check_release.py and scripts/make_release.py for local artifact gates.
  No website upload or public release is implied by a local build.
- Source data/ and executable-adjacent dist/data/ are separate ignored stores.
  Do not commit either. Preserve external configured archive paths.

## Working discipline

Delegate bulk reading spanning several files and request a digest, not files.
Keep tool output concise. Use explicit UTF-8 without BOM for text rewrites and
run the encoding guard. Record confirmed unresolved issues in BUGS.md and actual
validation outcomes in RELEASES.md. Current architecture is in DEVELOPMENT.md;
historical handoff plans/reviews may describe the former combined repository.
