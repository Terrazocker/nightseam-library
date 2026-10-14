# Current NightSeam Catalog findings

The browser-test process-tree and silent pytest-cleanup findings were fixed on
October 10, 2026. See RELEASES.md for validation and DEVELOPMENT.md for upkeep.
On October 11 the packaged gate was also corrected to select and verify a private
archive before checks; its private database alone did not isolate the default
Documents archive. Three exact synthetic test files were verified and removed.

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
