# Windows checks requiring a separate machine

These checks have not been executed in the Linux cloud environment or on the
hosted Windows runner. CI's empty-PATH smoke retains installed OS/.NET/WebView2
components and does not impose network denial. Its virtual display and emulated
browser viewports do not establish physical DPI, monitor-transition or Snap behavior.

## Prepared acceptance kit

Manual CI dispatch with `run_scaling_checks=true` prepares
`logs/windows-acceptance-kit.zip` in the Windows validation artifact. Alternatively
run `python scripts/make_acceptance_kit.py --exe dist/NightSeam.exe --output logs/windows-acceptance-kit.zip`
after a fresh build. Packaging does not execute any manual acceptance check.

Extract the kit into a new private directory on the test VM. It contains the
executable, three distinct synthetic FITS, a SHA-256 manifest, this procedure,
licenses, `Verify-Kit.ps1` and `results.template.json`. Run
`powershell -NoProfile -ExecutionPolicy Bypass -File .\Verify-Kit.ps1` before launch.
Copy the results template to `results.json`, record the machine/runtime/network
and display configuration, and replace each `not_run` only after observing that
check. Retain the executable hash, screenshots and restored-FITS hash evidence.
All executable data stays beside the kit's executable; explicitly select a
private archive before upload, as described below. Do not use an existing catalog.

## Clean Windows and disconnected operation

Use a disposable VM with a clean Windows base installation and no Python, Node
or development environment installed. A new account on a developer machine does
not remove machine-wide dependencies; it provides profile isolation only.
Record the Windows build and installed .NET/WebView2
runtime versions. Obtain a freshly built NightSeam.exe and record its SHA-256;
this is a local acceptance test, not a public release.

Copy the executable and synthetic FITS fixtures with their known original hashes
into a private test directory. Keep an independent copy of the input fixtures.
Fixtures can be generated on the development machine with
scripts.bench_windows_import.exposure(0); Python is not needed on the test VM.
After installing any declared prerequisites, disconnect external networking before
the first app launch on its fresh profile; retain loopback. Run NightSeam.exe
with --require-native-window and a free --port. Native startup
must succeed; the strict flag prevents a browser fallback from counting as success.
Configure and verify a private empty archive before any upload.

1. Open Library, Import and Sky Map; confirm no missing-runtime errors.
2. Confirm the private archive is selected with external networking still disconnected.
3. Import synthetic FITS with compression enabled and Safe selected. Confirm
   completion, then uncompress the complete session through the UI.
4. Hash the restored archived FITS and compare with the fixture's original hash.
   The source fixture must remain unchanged. Reimport it under a different name;
   it must keep the same image record, class, session and stored location.
5. Close the window normally and confirm that the app and its WebView descendants
   exit and the private listener closes. Reopen and confirm the saved placement.
6. Activate **Support report** with the keyboard in the native window. Save the
   JSON through WebView2's download UI, inspect versions/tool integrity and confirm
   it contains no archive paths or exposure names. Cancel a second download and
   confirm the catalog remains usable. CI covers the packaged browser download;
   native Save As interaction requires this separate check.

Record the network-disconnected interval, tool/runtime errors, hashes and results.
If WebView2 is absent, record the strict native startup failure separately; a
later pass after installing a prerequisite does not establish prerequisite-free
compatibility.

## Physical display behavior

Use the private test account/archive above and real display settings. Record
monitor resolutions, scaling, working areas and which monitor is primary.

1. At 100%, 125% and 150% OS scaling, open the app, resize small/large, maximize
   and restore. Toolbar actions, tabs and selected-session controls must remain
   reachable; text, icons and caption buttons must be clear and correctly aligned.
2. At each fixed scaling level, changing window size must change workspace rather
   than proportionally magnifying interface density.
3. Move between monitors with different scaling, including a secondary monitor
   left of the primary if available. Check the native frame, folder picker,
   map selection alignment and usable restored bounds on both monitors.
4. Exercise Windows Snap with pointer and keyboard gestures, then restore the
   normal window. Confirm that native caption buttons and system menu still work.
5. Close/reopen from normal and maximized states. Disconnect the secondary monitor
   and reopen; the window must remain reachable inside an available working area.

Keep screenshots and expected/observed bounds with a pass/fail result for each
configuration. Browser device-scale emulation is not a substitute for these cases.
