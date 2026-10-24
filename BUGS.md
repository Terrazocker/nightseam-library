# Current NightSeam Catalog findings

## Same-tick rewrites keep POSIX file versions under WSL2 — October 24, 2026 (open)

Under WSL2 (kernel 6.6.87, ext4), 184 of 200 immediate same-size rewrites with a
restored mtime left size, mtime, ctime and inode unchanged: the kernel records
change times in coarse ticks. test_native_tool_cache_revalidates_changed_
executable_with_restored_mtime[linux-x64] therefore fails there on both main
and the folder-import branch, while GitHub's Linux runner passes it; the native
tool cache can keep a stale validation for such a rewrite. POSIX durable
versions, and so the persisted dedup fingerprints, share the weakness: a file
rewritten with the same size within one tick of its recorded version keeps a
stale entry. That can only miss a duplicate, never skip a new file, because
positive matches always rehash. Not fixed: Python exposes no change counter
(statx change cookie) to strengthen POSIX versions.

## Device and control-character archive names refused imports — October 19, 2026 (fixed)

An unresolved FITS OBJECT such as NUL or COM1, an upload named con.fits or
aux.light.fit, or a raw multipart filename carrying a control character produced
an archive path that safe_rel rejects. The exposure was published, then removed
and reported as an invalid archive filename; only empty session folders
remained. On Windows such a name can address a device instead of a file. Object
folders and file stems now append `_` to a device name (NUL_, con_.fits) and
replace control characters with `_`; the original upload name stays in
orig_name. Folders an earlier refusal left empty are not removed. Windows
device behavior itself was not exercised on real hardware.

## Import alternates between upload and processing — October 18, 2026 (mitigated)

Disk activity during import came in bursts with long idle gaps. Each request was
fully received and spooled to temporary files before processing began, and each
batch was sent only after the previous response, so the two CPU-bound phases
(about three seconds each per 96 files) never overlapped. Every byte was written
twice and read back once. The ingest route now streams multipart parts straight
into archive stages. With the UI's declared file count, earlier files publish
while later ones arrive. A packaged 288-file timeline (3×96, 4 MB exposures)
fell from 17.6 to 11.8–12.0 seconds and from 2.3 GB to 1.16 GB of application
writes. Remaining: receiving now competes with per-file metadata/dedup/commit
work for Python's GIL (about 140 MB/s per request here). Each publication also
scans every same-size catalog row in Python, which grows with the archive.
Windows' write-back cache still flushes in bursts by design.

Update, October 22, 2026: most remaining idle time was a serialized read-back.
Each same-size publication fully hashed the previously published file under the
archive lock, about 120 ms per 52 MB exposure. That read-back is gone, desktop
folder imports read sources in place in 2 GiB batches, and content hashing uses
Windows CNG. A private 144 × 52 MB folder import fell from 29.0 to 4.5–4.9
seconds, and the same set uploaded from 44.3 to 36.0 seconds. Remaining: each
batch boundary still drains the pipeline (one continuous request measured about
13% faster), and write-back flush bursts are unchanged.

Update, October 24, 2026: desktop folder imports now run as one continuous job
that keeps 48 files staging while earlier ones publish, so batch boundaries no
longer drain the pipeline. With a cold 480 × 52 MB source the 2 GiB batches took
26.4 seconds and the job 15.3–15.8 seconds. Remaining: uploads still go in
batches, and write-back flush bursts are unchanged.

## Every import request rereads the same-size archive on Windows — October 18, 2026 (mitigated)

Proving an upload is not a duplicate requires current bytes of every same-size
archived exposure. On Windows, a measured digest was reusable only while the
same file handle stayed open, so each request reread the whole same-size
population: all frames from one camera share a size. The packaged catalog has
1,059 exposures of 4,152,960 bytes, about 4.2 GB reread per request. A private
256-file profile (96 per request) read 2.25 GB of archive for 1.06 GB of uploads.
pinned_version now exposes a durable version that also covers the NTFS/ReFS
change-journal id and the file's USN, which every closed writer advances, so
the process-local cache reuses actual earlier reads across requests. The same
profile then read 1.06 GB (one read per newly published file). Remaining: the
first import after each application start still reads the same-size population
once. Volumes without an active change journal (or not NTFS/ReFS) keep full
rereads. Positive matches and restored-mtime edits are still verified against
current bytes.

Update, October 22, 2026: a newly published uncompressed file now seeds the
cache with the digest of the bytes written, so it is no longer read once after
publication. Remaining: the first import after each start still reads earlier
same-size exposures once, and volumes without durable versions still read
every published file back (the Windows CI runner's volume behaves this way).

Update, October 24, 2026: on Windows the measured digests are also stored in
the catalog database under their durable version, so a restart reuses them
while the file is unchanged. After importing 144 × 52 MB exposures and
restarting, importing 144 more read no archive files back and took 6.6 seconds
instead of 14.8. Remaining: volumes without an active change journal, and
non-Windows systems, still read the same-size population once per start.

## Session compression blocked by stacked or deleted exposures — October 18, 2026 (fixed)

A floating point stack previously refused the entire session conversion. Deleting
its disk file left its catalog record selected, and the next attempt failed with
FileNotFoundError. Session compression now reports and skips existing missing
sources before directory pinning, and keeps unsupported files unchanged while
compressing eligible integer FITS. Skipped records, identities and integrity
baselines are preserved. Results distinguish missing and unsupported counts and
show up to 50 skipped paths. A changed archive root, linked path, permission
refusal, integrity mismatch or conversion failure still aborts safely. Complete
library copies and uncompression do not silently skip missing sources.

## Import startup rereads unrelated exposures — October 18, 2026 (mitigated)

Windows request preparation eagerly read up to 4,096 same-size archived
exposures before processing any upload, including an exact duplicate. Preparation
now captures only the archive identity. Exact recorded hashes and conversion
aliases are checked against current bytes first; unsuccessful hints still fall
through to complete same-size verification. Negative candidates acquire bounded
file/directory pins when needed. A private 128-file probe reduced a last-file
duplicate from 129 archive reads to one. Ordinary UI batches now allow 96 files,
bounded by 512 MiB, to amortize negative checks across more new exposures;
compression retains its 24-file count limit. Large single exposures travel alone.
Conservative Windows checks across closed handle lifetimes, full same-size
fallbacks and the existing pin bounds remain; large-archive import scaling is
not completely solved. Folder selection recursively finds supported FITS and
uses these same byte-verified import rules.

## Binary integrity baseline aborts health scans — October 18, 2026 (fixed)

SQLite TEXT affinity permits a BLOB in image_integrity.sha256. A nonempty BLOB
reached the text hash pattern and raised TypeError, aborting both Quick and Deep
scans before subsequent healthy records. Health now requires a textual SHA-256
baseline; a binary value produces the existing no_baseline finding. It is not
coerced or accepted, and the stored bytes remain unchanged. API regressions cover
ASCII digest and non-UTF8 BLOBs, continued healthy checking, acknowledgement
refusal, read-only results, scratch cleanup and released archive locks.

## Native lifecycle close observation — October 18, 2026 (fixed in harness)

Run 39124675987 passed source/browser/native checks but required HWND destruction
within 0.8 seconds while SQLite deliberately blocked publication. Pywebview hides
an accepted close before its browser cleanup completes, and the harness also
started IO before verifying WebView2 readiness. It now waits for the initial
private catalog/bridge, checks a visible-to-hidden transition within the same
deadline while the file remains pinned, then requires HWND/process/listener and
staging cleanup after the writer releases. Application shutdown behavior and the
SQLite hold limit are unchanged. See RELEASES.md for evidence and validation.

## Populated duplicate-query work and compression cancellation — October 17, 2026 (mitigated/fixed)

Bounded OR pages still sorted the remaining matching rows on every fetch.
Ordered UNION streams now reuse existing indexes and avoid repeated sorting.
Narrow candidate rows defer full metadata hydration until verified positives;
the hydrated path must match the one actually read. On the local 6,000-image
mixed HTTP workload, SELECTs fell 58,064→493 while candidate visits, 6,012 ingest
archive hashes, 57,110,400 bytes and transaction counts stayed identical.
Alias/exact-positive tiers are bounded and lazy, retaining separate expected
identities even for the same row. Their hashes and compression preflight now
receive cancellation; repeated cancellation drains workers before stage cleanup.
The four cancellation modes and mixed populated workload are covered by tests.
Matching-population traversal and conservative Windows cross-lifetime reads
remain; this is not a claim that all large-archive IO scaling is solved.
See docs/populated-import-20261017.md for provenance and measurement limits.

## Queued mutation recovery checks blocked the event loop — October 17, 2026 (fixed)

The new packaged lifecycle gate reproduced a static UI timeout while an
external SQLite writer paused import publication and another upload arrived.
Async middleware called recovery-state queries synchronously; the connection
lock held by the publication worker then blocked the event loop. The same
real-socket regression failed locally before the fix. Recovery queries now run
in worker threads, retaining the same refusal rules while queued requests wait
off the event loop. Native final validation is recorded on the draft PR.

## Repeated directory ancestor pins — October 17, 2026 (mitigated)

The baseline index shared exact parents but reopened their complete ancestry.
One-file-per-parent stress measured 53,248 additional handles, rising to 86,016
with eight extra ancestors, despite the 4,096-file cap. The new request-owned
directory cache stages each unseen suffix and publishes it only after successful
file verification. Failed suffixes retain no keys/handles, earlier pins remain
valid and unrelated thread-local contexts cannot shorten ownership. Native
resource/safety validation passed: the deeper workload fell from 86,016 to 12,306
request-held handles (85.7% fewer), with all handles released. Fifty cycles,
with 45 measured after five warmups, stayed within the retained-heap bound.
Unique deep branches can still legitimately require many directory handles.
A combined 16,384-handle request budget now limits retained/provisional file and
directory pins, including two slots for temporary hashing handles. Excess
candidates retain fresh byte checks; smaller suffixes can still qualify.
Preparation applies its row cap in SQL and duplicate searches use complete
256-row pages, limiting Python row materialization without truncating the search.
Native regression guards cover combined-budget refusal and real HTTP cleanup.
See docs/import-directory-resources-20261017.md.

## Archive-health browser readiness — October 17, 2026 (fixed in harness)

PR run 39033952706 passed source/build/native gates but its health-deletion check
counted zero session buttons immediately after opening the asynchronous report.
The captured screenshot already contained both expected groups. The test now
waits for the second session button before retaining the exact count-of-two
assertion and the existing deletion/confirmation checks. Missing buttons time out
and extra groups still fail. Application behavior and requests are unchanged.

## Native smoke bridge startup — October 17, 2026 (fixed in harness)

PR run 39030212370 passed source/build/browser gates but its first native
capability request failed with `Resulting promise was garbage collected`.
The smoke harness no longer forces a reload during pywebview's background
bridge injection. Capability probes retain their Promise on the window and poll
plain settlement state, with one request and fatal rejection/timeout. Three
JavaScript-engine regressions verify pending-call retention and failure paths.
The application bridge and import implementation are unchanged.

## Frozen popularity snapshot unavailable - October 16, 2026 (blocked)

The original ignored data/popularity_snapshot.json and its recorded metadata are
absent from this workspace and Git history. The optional source-provenance check
therefore remains skipped on Linux and hosted Windows CI. Restore the genuine
frozen snapshot and data/popularity_snapshot.meta.json from a backup to complete this
check. The matcher and runtime retain their no-live-fetch rule; generated shipped
popularity scores are not a replacement for the original source snapshot.

## Windows rapid-write timestamp collision - October 16, 2026 (fixed)

The first hosted Windows run found one failure: rewriting a tool with same-size
bytes and restoring mtime could reuse a cached integrity result because native
ChangeTime also matched. Windows versions now include a unique handle lifetime,
so tool validation and negative dedup lookups rehash after an unpinned gap.
Request-held writer-excluding batch handles keep their reuse optimisation.
The tamper assertion remains intact. See RELEASES.md for final validation.

## Linux cloud native compression limitation - October 16, 2026 (fixed)

The initial Linux x64 cloud run reported 29 compression-related failures because
the selected fpack.exe/funpack.exe were Windows PE binaries. Native Linux x64
tools are now built from the same verified CFITSIO/zlib sources, with platform
selection, integrity checks, executable hashes and licenses. Native Rice workflows
are tested on Linux; Windows packaging and the packaged desktop gate still need
Windows. Linux binaries require glibc 2.34 or newer. See ENVIRONMENT.md for rebuild
requirements and RELEASES.md for validation results.

The browser-test process-tree and silent pytest-cleanup findings were fixed on
October 10, 2026. See RELEASES.md for validation and DEVELOPMENT.md for upkeep.
On October 11 the packaged gate was also corrected to select and verify a private
archive before checks; its private database alone did not isolate the default
Documents archive. Three exact synthetic test files were verified and removed.

## Plain FITS dedup metadata scaling - October 15, 2026 (mitigated)

POSIX import memoizes actually read negative candidates behind file versions;
Windows reuse is limited to continuously held request pins. Rice preparation is
parallel. Historically, for 48 equal-size synthetic FITS, archive hash reads
fell from 1,128 (4.68 GB) to 47 (195 MB). Positive duplicates still rehash current
bytes, and same-size edits with restored mtime remain detectable. Candidate path
and file-version inspection still scales quadratically for many equal-size plain
FITS. The bounded 20,000-entry cache freezes new admission when full, preserving
existing useful entries rather than thrashing; excess candidates use fresh hashes.
FAT/exFAT, unsupported version APIs and open writers fall back to uncached reads.
The later 512-file HTTP investigation reproduced the real scaling problem:
2.12 GB took 168.5 seconds. Request-scoped pinned negative indexes now validate
earlier files once per 24-file batch, reducing the final same-workload import to
24.58 seconds (an intermediate run measured 26.25 seconds).
Archive leases delay unrelated mutations until pins close; image/history commit
together once per file. Index admission is bounded to 4,096 files per request;
larger populations and unsupported filesystems retain conservative fallback work.
Thus metadata work is amortized across a batch rather than eliminated globally;
this is not a disk/CPU saturation guarantee for every archive/filesystem. Rice
archives usually have variable sizes. The earlier 48-file direct benchmark did
not include the representative library size, HTTP multipart path or history cost.
These measurements predate the October 16 conservative Windows cross-open rehash
policy. The October 17 post-correction hosted Windows run imported 512 files
(2,124,840,960 bytes) in 87.02 seconds at 23.29 MiB/s on two logical CPUs. Its
first full batch took 11.03 seconds including resolver setup; subsequent full
batches ranged from 2.22 to 4.67 seconds and the last took 4.06 seconds. This is
different hardware with warm caches, not a controlled comparison to the earlier
workstation. That workstation and larger archives remain unmeasured after the
correction. Request-held batch pins still avoid repeated hashing within a request.
See docs/import-performance-20261015.md for the run, audits and retained evidence.

The October 17 worker-thread source profile measured 11,368 archive hashes and
47.178 GB of actual reads for 2.125 GB of input. Archive hashing used 44.828 of
71.578 process CPU seconds (62.6%); input copy/hash used 2.141 CPU seconds.
This remains the leading measured CPU target. Cross-lifetime fresh reads and
positive-duplicate verification must remain intact in any further optimisation.
See docs/import-profile-20261017.md for methodology and limits.

Lazy request-local admission subsequently reduced the same workload to 6,034
archive hashes and 25.042 GB read (46.9% fewer bytes), with 5,233 SELECTs instead
of 10,567. Candidate visits still total 130,816, and new requests still rehash
across closed Windows handle lifetimes. The packaged run took 69.023 seconds,
but used a different Intel runner rather than the earlier AMD runner; no controlled
timing speedup is claimed. Larger archives remain unmeasured. See the updated
profile report for the validated implementation and audits.

## Windows handle sharing - October 15, 2026 (fixed)

Behavioral tests proved READ_ATTRIBUTES-only handles did not exclude external
writes/renames, so they were insufficient for stable cached versions or directory
lineage reuse. Pins now request GENERIC_READ with the required sharing policy.
Tests verify write/file-rename/parent-rename refusal until closure, and rejection
of replaced leaf links. Cached directory lookups retain live lineage handles,
check immediate-parent identity/reparse state and reject reparse leaves.

## FITS compression research hazard - October 15, 2026

Installed Astropy 7.2.2 silently truncates fractional floating-point pixels when
writing CompImageHDU with compression_type='RICE_1', quantize_level=0. The
synthetic 1024x1024 probe produced RMS error 0.5772 with no warning; a smaller
source-inspected reproduction confirmed float-to-int32 conversion. Do not use
this combination as a lossless float encoder. GZIP_2 with quantize_level=0
preserved all tested float32/64 special-value bits and finite float32 samples.
This remains a dependency integration hazard. NightSeam's manual and opt-in ingest Rice operations
use bundled native CFITSIO, reject floating-point conversion and verify exact
whole-file restoration before replacing originals. Astropy is used only in
research/tests, not that runtime conversion path. See
docs/fits-compression-research.md and the retained synthetic evidence under
logs/fits-compression-research-20261015/. The dependency was not changed;
newer Astropy versions were not tested.

## Local validation environment - October 15, 2026

Plain python currently selects C:/Python314/python.exe (Python 3.14.7), which
lacks py_mini_racer. A focused ingest/API test fails with ModuleNotFoundError;
the exploratory full run with that interpreter was interrupted. Use the existing
.venv-build/Scripts/python.exe: its Windows x64 Python 3.12 environment passes
all 46 exact dependency pins and the full source suite.

The restricted sandbox produced 27 failures (656 passed, 1 skipped), concentrated
in archive health, legacy recovery and shutdown. An isolated scratch probe traced
archive unavailability to PermissionError / WinError 5 at Path.resolve(strict=True)
before directory listing; direct listing of the same synthetic archive succeeded.
The unchanged suite outside the restricted sandbox passed 683 tests with one
POSIX-only skip. This is a validation-environment constraint; application code,
archive safety checks and assertions were not changed to bypass it.

## Validation environment cache - October 10, 2026

A literal `%SystemDrive%/ProgramData/Microsoft/Windows/Caches/` directory appeared
at the checkout root during concurrent validation/audit work. It contained only
three Windows cache databases (about 0.94 MiB) and was removed after validating
its exact location, contents and absence of links. Its creating process is not
confirmed. Watch for recurrence during browser validation; do not generalize
cleanup to actual ProgramData or any application database.

## Intermittent Windows directory rename validation - October 13, 2026

Preview validation encountered transient WinError 5 (access denied) while renaming
synthetic directories under logs/.pytest-tmp/. The concurrent source/browser run
failed test_library_database_failure_reports_occupied_whole_directory_origin and
test_root_replacement_during_scan_is_unavailable; both passed in isolation. A
standalone full-suite run then failed the unchanged
test_whole_folder_rename_does_not_enumerate_after_publication (712 passed, 1 failed,
1 POSIX-only skip), which also passed immediately in isolation. The cause of the
transient filesystem refusal remains unconfirmed. No archive rename implementation
or assertion was weakened for the preview changes. Preserve evidence under
logs/preview-stretch-20261013/; a fully clean whole-suite result is not claimed.

The subsequent inline-session validation repeated the same unchanged
whole-folder rename failure in a standalone full run (713 passed, 1 failed,
1 POSIX-only skip in 154.63 seconds). Its isolated rerun passed in 0.89 seconds.
Evidence is retained under logs/session-expansion-20261013/. The filesystem
refusal remains unresolved; preview and grouping validation passed.

The later visible-thumbnail full-suite gate passed all 714 tests with one
POSIX-only skip in 146.46 seconds, including the rename test. This passing run
does not establish the cause or resolution of the earlier intermittent refusal.

Recoverable-trash validation subsequently passed all 737 source tests with one
POSIX-only skip (final run 179.21 seconds). The earlier rename-refusal evidence
remains retained; these passing runs do not establish its underlying cause.

## Literal archive ancestry and frozen browser launch — October 18, 2026 (fixed)

The clean Linux runtime check exposed two issues missed by developer-host
headless checks. Frozen browser handoff inherited PyInstaller's bundled library
path; it now uses the same sanitized xdg-open launch as archive-folder opening.
First and repeat-instance browser URLs and literal folder arguments are checked
through an owned launcher capture, without modifying the server environment.

CFITSIO treated parentheses/brackets in archive ancestry as extended filename
syntax and refused Rice conversion. A real native round-trip regression failed
before the fix. FITS utility subprocesses now use the validated source parent
as their own cwd and relative staging arguments, keeping that ancestry out of
the parser while leaving the server cwd and shell behavior unchanged.

## Large library grouping and response stalls — October 18, 2026 (fixed)

Library grouping rescanned the complete catalog for each stored object ID and
rebuilt the alias resolver for every obsolete ID. Regression fixtures observed
820 ID reads for 40 current groups and 13 resolver constructions for 13 obsolete
groups. Grouping now scans catalog IDs once, retaining only requested rows and
stopping early when all current IDs are found. One lazy per-call resolver
handles obsolete IDs; intentionally unresolved groups remain unresolved.

The synchronous library route also returned a raw dictionary. FastAPI walked
and rendered its nested JSON on the event loop after the worker returned,
blocking unrelated HTTP requests during encoding. The route now performs the
same jsonable_encoder/JSONResponse conversion inside its worker. A real-socket
regression held encoding and reproduced a blocked catalog request before the
fix; after the fix the catalog responds while encoding remains held.

## Archive-health discovery memory — October 18, 2026 (fixed)

Pending directories and untracked FITS paths now use a private disk-backed
SQLite spool with a 1 MiB page cache and indexed ordered iteration. Complete
counts, raw pathname ordering, duplicates and cancellation are preserved.
The synthetic 100,000-file plus directory-fanout probe reduced traced Python
peak from 43.125 MiB to 0.064 MiB; observed instrumented RSS growth fell from
63.293 MiB to 1.395 MiB. Scratch cleanup and storage failures are tested.
Spool disk usage grows with discovered paths; storage exhaustion visibly fails
the scan without changing the archive or catalog. The indexed-record follow-up
below also removes its separate row-list and known-path-set memory growth.
See RELEASES.md for measurement scope and disk/time tradeoffs.

## Archive-health indexed snapshot memory — October 18, 2026 (fixed)

Health scans now copy a consistent committed catalog view into private disk
storage one native row at a time, with indexed known-path membership. Full raw
rows and duplicate/invalid paths retain their previous reporting and stale
acknowledgement semantics. The separate read-only source has a 1 MiB cache and
closes before archive inspection; shared database reads remain available while
copying. External rollback-journal writers can wait during materialization.
Canonical opened filenames and file identity checks refuse catalog replacement
instead of reading a different database. Cancellation, VM interruption, busy
writers, source changes, legacy values and storage failures are covered by tests.
Working memory scales with one row, SQLite caches and retained report payloads,
rather than indexed row count. Report source histograms remain output-dependent;
temporary disk still scales with catalog size. The list-returning snapshot API
for compression and other callers is unchanged.

## Acceptance still open

- Linux minimal offline systems now have a dedicated runtime gate; real
  browser/file-manager integration, downloads and screen-reader behavior still
  require the checklist in docs/linux-manual-acceptance.md. A captured launcher
  is a test double, not evidence of a successful physical desktop session.

- Windows automated archive health and FITS preview acceptance passed on
  October 13: 708 source tests, 14 packaged browser checks and a private frozen
  plain/RICE decoder and acknowledgement smoke. The Windows stat/fstat ctime
  mismatch discovered during validation is fixed. Manual acceptance remains for
  real archive directory pinning/reparse races, actual permission changes and
  cancellation/shutdown under production workloads; automated fixtures do not
  establish those environment-specific results.

- Filesystem refusal can prevent archive rename undo after a database failure.
  Fault-injection tests reproduce this recovery limit. The API now reports
  incomplete recovery and logs the affected paths instead of hiding it behind
  a generic error. Preserve files at both locations, resolve the filesystem
  problem and inspect the logged paths before Reindex folders. Reindex repairs
  only unique trusted full-file fingerprint matches and preserves original
  records. It does not reverse a failed move, merge existing duplicate rows or
  repair records without a trustworthy prior fingerprint. Ambiguous matches
  remain unchanged and require manual review; no missing record is discarded.

- Solar session boundaries require reliable capture times and location. Missing
  location is visibly Unassigned location by UTC date; unsupported explicit time
  scales and ambiguous/invalid dates are Undated. These are unresolved metadata,
  not verified observing nights. A fallback location helps only when both header
  coordinates are absent. Real sunrise/set can differ from the conventional
  -0.833-degree horizon because of terrain, elevation and atmospheric conditions.
  Continuous polar phases are explicitly split by UTC date. Reindex refreshes
  membership without moving files; old physical folder labels can remain.

- CSV formula protection targets spreadsheet viewing. Import/resave behavior
  differs among spreadsheet applications; JSON retains exact original values.
  No cross-application formula-execution or exfiltration acceptance is claimed.

- Verify native scaled/mixed-DPI displays, monitor removal, Windows Snap,
  taskbar/system menu and keyboard move/resize on real hardware. Existing
  simulated regression tests do not establish those hardware results.
- Complete manual accessibility acceptance: focus restoration after dialogs,
  menus and inspectors; announcements of dynamic progress; actual screen-reader
  use. Automated semantics and browser checks remain valuable but incomplete.

The import/session/button findings fixed immediately before extraction remain
fixed in this 0.1.0 source snapshot. Historical reviews retain their original
combined-version labels and are evidence, not active instructions.
