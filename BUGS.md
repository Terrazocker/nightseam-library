# Current NightSeam Catalog findings

## Browser-test teardown - confirmed October 5, 2026

The packaged browser harness can leave a headless frozen child running after
terminating its recorded parent. Separation verification found an orphan from
this pass's browser gate on private port 8232, with a missing parent and an
executable in its ns-browser temporary directory. The exact confirmed test child
was stopped; the normal catalog executable and library files were untouched.

Deferred tooling fix: tear down the harness-owned Windows process tree, verify
its listener/children have exited, and then clean its temporary directory. Keep
test scratch under ignored logs/ and never terminate an unrelated user process.
The passing browser assertions remain valid; this finding concerns cleanup and
resource leakage, not a dependency on the website.

## Acceptance still open

- Verify native scaled/mixed-DPI displays, monitor removal, Windows Snap,
  taskbar/system menu and keyboard move/resize on real hardware. Existing
  simulated regression tests do not establish those hardware results.
- Complete manual accessibility acceptance: focus restoration after dialogs,
  menus and inspectors; announcements of dynamic progress; actual screen-reader
  use. Automated semantics and browser checks remain valuable but incomplete.

The import/session/button findings fixed immediately before extraction remain
fixed in this 0.1.0 source snapshot. Historical reviews retain their original
combined-version labels and are evidence, not active instructions.
