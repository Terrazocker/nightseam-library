# Linux desktop acceptance

Status: **not run** on a real Linux desktop. Automated headless browser checks
and minimal offline container checks do not substitute for this procedure.

Use a disposable desktop VM or test machine and a new writable directory.
Never reuse an actual catalog, image archive or previous installation. Record
the OS release, architecture, glibc version, desktop session (X11/Wayland),
browser/file-manager versions, package version and tarball/executable SHA-256.
Use the same tarball that passed CI and verify its adjacent `.sha256` file.

Python and Node are unnecessary on the test machine. Install the system browser,
`xdg-utils` and `zenity` (or `kdialog`) first. Prepare only synthetic FITS fixtures with independent
original hashes; retain the originals outside the test archive. On a development
machine, `tests/browser/fits_fixtures.py` provides `compressed_gate_image(False)`.
Do not copy user exposures or a profile into the test package.

Disconnect external networking before first launch on the fresh profile, while
leaving loopback available. In the extracted package directory run
`./NightSeam --browser --console`. Configure and verify the new private archive
before any upload. Prefer an archive path containing spaces and literal shell
characters, for example `archive $(literal); with spaces`.

Record each result as passed, failed (with evidence), or not_run:

| Check | Required observation | Result |
| --- | --- | --- |
| Offline first launch | Installed browser opens the local catalog; Library, Import and Sky Map load without downloads or missing-runtime errors. | not_run |
| Repeat launch | A second invocation opens the existing catalog without starting another server or changing its private archive. | not_run |
| Open folder | The file manager opens precisely the configured archive, including literal spaces/metacharacters. | not_run |
| Rice round trip | Import synthetic FITS with Safe compression; uncompress through the UI and verify the restored file's original SHA-256. | not_run |
| Duplicate | Reimport the same bytes under a new name; identity, original classification/location and image count remain unchanged. | not_run |
| Folder import | **Choose folder…** opens the zenity/kdialog dialog; the picked synthetic folder imports in place with per-file progress and no browser upload confirmation; its files stay unchanged. | not_run |
| Folder Browse… | The archive **Browse…** saves the picked folder as the archive; the library-copy destination **Browse…** fills its field without starting a copy. Without zenity/kdialog both take typed paths and folder import falls back to the browser upload. | not_run |
| Downloads | Export CSV and save a support report through the real browser; inspect versions/tool integrity and check that the support report omits archive paths/exposure names. | not_run |
| Keyboard/accessibility | Navigate menus/dialogs/import controls, verify focus restoration and progress announcements with an actual screen reader. | not_run |
| Browser-tab close | Closing a tab leaves the server available; reopening its logged URL works. | not_run |
| Shutdown | Ctrl+C stops the app, closes its loopback listener and removes its instance marker and temporary extraction. Restart opens the same private catalog. | not_run |
| Desktop variants | Repeat browser/folder launch under each claimed desktop and X11/Wayland session. | not_run |

Keep a copy of this table with observed results, screenshots and file hashes.
Do not mark a row passed just because a script generated a package or captured
launcher arguments. Missing `xdg-open` should produce an explanatory folder
error and a browser log with a manual URL; this is a diagnostic path rather
than successful desktop integration.

No user-local desktop launcher, native GTK/Qt window, ARM or musl support is
claimed. A launcher or native backend can be a separate reviewed enhancement.
