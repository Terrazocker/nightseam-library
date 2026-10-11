# Current NightSeam Catalog findings

The browser-test process-tree and silent pytest-cleanup findings were fixed on
October 10, 2026. See RELEASES.md for validation and DEVELOPMENT.md for upkeep.

## Validation environment cache - October 10, 2026

A literal `%SystemDrive%/ProgramData/Microsoft/Windows/Caches/` directory appeared
at the checkout root during concurrent validation/audit work. It contained only
three Windows cache databases (about 0.94 MiB) and was removed after validating
its exact location, contents and absence of links. Its creating process is not
confirmed. Watch for recurrence during browser validation; do not generalize
cleanup to actual ProgramData or any application database.

## Acceptance still open

- Filesystem refusal can prevent archive rename undo after a database failure.
  Fault-injection tests reproduce this recovery limit. The API now reports
  incomplete recovery and logs the affected paths instead of hiding it behind
  a generic error. Preserve files at both locations, resolve the filesystem
  problem and inspect the logged paths before Reindex folders. Reindex repairs
  only unique trusted full-file fingerprint matches and preserves original
  records. It does not reverse a failed move, merge existing duplicate rows or
  repair records without a trustworthy prior fingerprint. Ambiguous matches
  remain unchanged and require manual review; no missing record is discarded.

- The twelve-hour adjacent-capture rule can combine consecutive observing
  nights with a short daytime gap. This is the current policy, not a calendar-
  night guarantee. A future night-boundary option needs an observatory timezone
  and explicit calibration/daytime semantics. Existing folder labels are not
  migrated when later imports extend a logical session.

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
