# Current NightSeam Catalog findings

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

Import now memoizes actually read negative candidates behind strong file versions
and prepares Rice in parallel. For 48 equal-size synthetic FITS, archive hash reads
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

## Acceptance still open

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
