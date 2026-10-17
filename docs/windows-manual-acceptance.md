# Windows checks requiring a separate machine

These checks have not been executed in the Linux cloud environment or on the
hosted Windows runner. CI's empty-PATH smoke retains installed OS/.NET/WebView2
components and does not impose network denial. Its virtual display and emulated
browser viewports do not establish physical DPI, monitor-transition or Snap behavior.

## Clean Windows and disconnected operation

Use a disposable Windows VM/test account with no Python, Node or development
environment installed. Record the Windows build and installed .NET/WebView2
runtime versions. Obtain a freshly built NightSeam.exe and record its SHA-256;
this is a local acceptance test, not a public release.

Copy the executable and synthetic FITS fixtures with their known original hashes
into a private test directory. Keep an independent copy of the input fixtures.
Fixtures can be generated on the development machine with
scripts.bench_windows_import.exposure(0); Python is not needed on the test VM.
Run NightSeam.exe with --require-native-window and a free --port. Native startup
must succeed; the strict flag prevents a browser fallback from counting as success.
Configure and verify a private empty archive before any upload.

1. Open Library, Import and Sky Map; confirm no missing-runtime errors.
2. Disconnect the VM/machine's external network while retaining loopback.
3. Import synthetic FITS with compression enabled and Safe selected. Confirm
   completion, then uncompress the complete session through the UI.
4. Hash the restored archived FITS and compare with the fixture's original hash.
   The source fixture must remain unchanged. Reimport it under a different name;
   it must keep the same image record, class, session and stored location.
5. Close the window normally and confirm that the app and its WebView descendants
   exit and the private listener closes. Reopen and confirm the saved placement.

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
