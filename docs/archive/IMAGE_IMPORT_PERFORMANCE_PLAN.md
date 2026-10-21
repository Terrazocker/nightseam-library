# Image Import Performance Plan

**Status:** Phases 1–3 implemented; sequential import preserved. Focused tests and independent review passed. The full suite completed with environment/working-tree failures noted under Results.

**Date:** 2026-09-27

## Goal

Increase FITS import throughput and reduce the time the local import workflow
spends waiting on application work between disk operations. Preserve the
archive layout, FITS interpretation, session grouping, duplicate filename
handling, size limits, import history, and the catalog-only desktop scope.

This plan covers the local FastAPI/Image Catalog import path. It does not change
the web planner or astronomy/planning code in `core/`.

## Findings

### 1. Catalog object resolution is the primary measured bottleneck

For every uploaded frame, `app/ingest.py::ingest_one` calls
`imglib.resolve_object`. That function normalizes the full 12,176-row catalog,
rebuilds the name index, and linearly searches the rows again to return the
matched object. Imports process files serially, so this work repeats once per
file before its archive copy.

Evidence from generated FITS data in temporary storage:

- Named resolution took about **240–250 ms per frame** in the end-to-end
  benchmark. An independent in-memory benchmark measured **367 ms per named
  lookup**; the exact timing varies by run and environment.
- The three-run breakdown was:

  | Synthetic import | End-to-end | Object resolution | Stream time | Archive writes |
  | --- | ---: | ---: | ---: | ---: |
  | 1 × 1 MiB | 0.318 s | 0.243 s | 0.9 ms | 0.3 ms |
  | 8 × 1 MiB | 2.077 s | 1.932 s | 7.8 ms | 2.6 ms |
  | 8 × 4 MiB | 2.203 s | 1.930 s | 78.6 ms | 12.1 ms |
  | 8 × 16 MiB | 2.718 s | 1.999 s | 375 ms | 50.5 ms |

  Stream time includes upload reads and archive writes; the sub-times overlap
  and should not be added to it. Eight database image commits took **21 ms**
  total in the 8 × 1 MiB case.
- A valid synthetic FITS header took about **0.05 ms** to parse. FITS pixel
  data is not decoded during ingest.

This repeated CPU work explains much of the low disk utilization, especially
for many small files.

### 2. Session selection reloads the whole image table for every file

Without an explicit session override, `app/ingest.py::_session_dir` calls
`db.images()` and filters all stored rows in Python. The measured lookup cost
grew with library size:

| Existing image rows | `db.images()` | `_session_dir` |
| ---: | ---: | ---: |
| 0 | 0.062 ms | 0.084 ms |
| 1,000 | 2.862 ms | 3.087 ms |
| 10,000 | 28.48 ms | 30.827 ms |

Since this happens for each uploaded file, large established libraries add
more delay, and a large batch compounds the repeated scans. Explicit-session
calibration imports bypass this lookup.

### 3. The 64 KB async copy buffer adds measurable overhead

The importer reads each spooled upload in 64 KB chunks. In a generated
8 × 16 MiB comparison, increasing the chunk to 1 MiB changed:

- End-to-end time: **2.738 s → 2.392 s**
- Measured stream time: **0.372 s → 0.105 s**
- Upload-file read calls: **1,880 → 128**
- Aggregate throughput: **47 → 53.5 MiB/s**

The browser sends sequential batches of eight, and the server processes files
in each batch sequentially. Multipart upload data is staged before the app
copies it into the archive, so the path includes temporary-file I/O as well as
the archive write.

### 4. Database and serial-work overhead remain secondary candidates

`Database.add_image()` commits once per imported file. This can add noticeable
latency on slower storage, but eight commits took **21 ms total** in the
8 × 1 MiB synthetic case, far below the measured catalog-matching cost. The
browser and server both process files serially. Reindexing also commits per
discovered file; treat it as a separate follow-up unless profiling shows it
matters to the user's import path.

### Measurement limits

All import timings used generated files and disposable temporary directories;
the copy comparison used warm-cache storage. They establish application-level
costs but do not measure the configured archive drive's peak throughput or a
real user's source-to-destination drive combination. No user archive was read
or modified.

## Implementation sequence

### Phase 1 — Reuse one catalog resolver per upload request

1. Add a reusable normalized resolver/index in `app/imglib.py` that holds the
   normalized catalog rows, exact/prefix name index, and an ID-to-row lookup.
2. Build it once in `/api/images/ingest` and pass it to each `ingest_one` call
   in that request. Preserve `resolve_object()` as a compatibility wrapper for
   other callers.
3. Preserve current precedence and matching behavior: name/designation tokens,
   prefix fallback, coordinate matching, and unresolved objects. Preserve
   current deterministic handling of duplicate catalog names and IDs.
4. Avoid normalizing rows twice while building the resolver, and avoid a
   linear scan to recover the row by ID.

**Expected impact:** amortize the measured roughly 0.24–0.37 s resolver build
across the request's up-to-eight files, instead of paying it for every file.
The baseline was **1.93 s of object resolution per eight matching files**.
After the change, eight catalog lookups took about **0.13 ms total**, with one
resolver build taking about **0.23 s**.

### Phase 2 — Make session lookup proportional to the relevant object

1. Replace `_session_dir`'s full `db.images()` scan with a query for the
   imported object ID, or the existing unresolved-object name when there is no
   catalog match.
2. Add a SQLite index only if the schema/query plan shows it is needed.
3. Preserve the existing `imglib.session_for_image` grouping rules, including
   undated frames, nearest-session assignment, object-name matching, and
   explicit session overrides.
4. If a request imports many frames for one target, consider a request-local
   session cache only after the targeted query is proven equivalent. Update
   that cache as each successfully imported frame is recorded.

### Phase 3 — Use a larger streaming copy chunk

Change `app/ingest.py::_COPY_CHUNK` from 64 KiB to 1 MiB, retaining one-chunk
memory use, the 1 GiB per-file cap, cancellation handling, partial-file cleanup,
and sequential processing. The measured 1 MiB comparison improved stream time
and end-to-end time without requiring parallel writes.

### Phase 4 — Re-measure before changing transaction or concurrency behavior

After Phases 1–3, profile per-image SQLite commits and the serialized file
pipeline again. Only if they remain meaningful:

- Consider a transaction/bulk-insert path for a request. Preserve correct
  cleanup if database insertion fails after file writes, and preserve
  per-file success/error reporting.
- Consider bounded copy concurrency only after resolver and session work no
  longer serializes the pipeline. Keep SQLite access safe and avoid making
  slower drives less responsive.
- Do not add content hashing or duplicate-content skipping as a performance
  shortcut; that would change import semantics and add full-file reads.

## Validation and acceptance criteria

1. Add focused regression coverage for resolver equivalence: catalog names and
   aliases, prefix matches, coordinate fallback, unresolved frames, duplicate
   names, and deterministic selection.
2. Cover session assignment with existing and newly imported rows, multiple
   objects, dated/undated frames, calibration overrides, and larger image
   libraries.
3. Keep the existing ingest guarantees: exact stored bytes, safe generated
   paths, collision suffixing, size rejection, no partial files after failure,
   and one import-history group per run/object/session/frame.
4. **Implemented and measured using disposable synthetic data.** Combined
   post-change end-to-end timings were:

   | Synthetic import | Before | After |
   | --- | ---: | ---: |
   | 8 × 1 MiB | 2.077 s | 0.365 s |
   | 8 × 4 MiB | 2.203 s | 0.452 s |
   | 8 × 16 MiB | 2.718 s | 0.710 s |
   | 16 × 2 MiB, two requests | 4.420 s | 0.830 s |

   The measurements are warm-cache synthetic runs, not the configured drive's
   peak throughput. In the 10,000-row session lookup comparison, eight
   object-scoped queries took **5.3 ms total**, versus about **247 ms** for
   eight full-table scans.
5. **Resolver acceptance passed.** It is constructed once per ingest request;
   eight lookups measured about **0.13 ms total** after the roughly **0.23 s**
   catalog index build, below the 0.5 s target. Resolver behavior and the
   session-query predicate/order were independently reviewed for parity.
6. **Chunk comparison passed in a controlled synthetic run.** For 8 × 16 MiB,
   changing only the stream chunk from 64 KiB to 1 MiB reduced measured stream
   time from **0.372 s to 0.105 s**, read calls from **1,880 to 128**, and
   improved throughput from **47 to 53.5 MiB/s**. End-to-end time in that
   chunk-only comparison changed from **2.738 s to 2.392 s**. The combined
   implementation benchmark above also includes resolver and session-query
   changes. Buffering remains sequential and bounded to one 1 MiB chunk per
   active file.
7. No user archive or drive-specific import was measured; all performance
   fixtures were disposable synthetic data. A hardware-specific comparison is
   deferred unless requested.

### Results and verification

- Phases 1–3 are implemented in `app/imglib.py`, `app/server.py`,
  `app/database.py`, and `app/ingest.py`. File processing remains sequential;
  no parallel writes, bulk transaction, or content hashing were introduced.
- Focused import/API checks passed before and after the partial-write cleanup
  regression test. The reviewer’s final focused run passed **9 tests** (110
  deselected), including resolver/session behavior, 1 MiB chunking, and cleanup
  after a read failure mid-stream. Independent review found no parity issue.
- The final full-suite run completed with **652 passed, 5 failed, and 10
  errors** (206.67 s; one full run). All observed failures were Windows
  `WinError 5` sandbox ACL denials:
  `test_core_drift.py::test_a_moved_concept_in_a_shell_subdirectory_is_caught`
  could not create `web/lib`; `test_pre_tag_check.py::test_main_exit_codes`
  and `::test_token_guard_is_wired_and_would_block_a_stale_tag` could not create
  `.pt-token-selftest`; both tests in `test_never_touch_user_data.py` received
  HTTP 500 when writes to `logs/.pytest-data/panos` / `data/panos` were denied.
  Five JS syntax checks could not create `logs/test-js-syntax-*`, and five
  panorama-history checks could not write `logs/.pano_history_probe.mjs`.
  Targeted diagnostics reproduced these ACL failures. They are sandbox write
  restrictions, not assertion failures in the import changes. The suite was
  not rerun.
- The temporary test-path override used for focused checks was reverted;
  `tests/conftest.py` has no working-tree diff.
- The executable was built with `build.ps1 -AllowDirty` as
  `dist/NightSeam.exe` (**20.5 MB**, version
  `0.6.34-9-gd9f4f3a-dirty`). The repository build script stopped the four
  active `NightSeam.exe` processes before replacing it. Temporary build-script
  path/version workarounds were restored after the successful build, and
  `app/_version_baked.py` already held the matching version. This optimization
  does not modify the web planner or `core/`.

## Scope constraints

- Do not change `web/` or `core/` for this local import optimization.
- Preserve the currently uncommitted catalog UI and reference files.
- Do not start with parallel writes or filesystem bypasses; the measured costs
  point first to repeated per-file catalog work and small async reads.
- The plan was reviewed before implementation. Phases 1–3 were implemented on
  2026-09-27; transaction changes and concurrency remain deferred.
