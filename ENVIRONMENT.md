# Catalog development environments

## Linux x64 source development

The cloud workspace uses Python 3.12 and requirements-dev.txt; Node is required
for JavaScript checks. Run python main.py --no-browser --console for headless
source use. Windows packaging and native-window gates still require Windows.

Native Rice tools are bundled under vendor/fits-tools/linux-x64 with verified
SHA-256 hashes, source provenance and upstream licenses. This build requires
glibc 2.34 or newer and was tested on glibc 2.41. CFITSIO and zlib are linked
statically; only system libc/libm and the ELF loader remain dynamic dependencies.
Other architectures and operating systems are explicitly unsupported.

Rebuild with python scripts/build_fits_tools.py on Linux x64 using GCC, Make,
binutils and CMake (system installation or python -m pip install cmake in the
active environment). The builder verifies the same pinned official CFITSIO
4.7.0 and zlib 1.3.2 source archives as Windows, compiles native Linux binaries,
and records compiler, flags, required glibc, binary hashes and licenses.
Build logs and commands remain under ignored logs/fits-tools-build-linux-x64/.
Runtime selection preserves hash checks and never searches PATH for utilities.

Source tests: python -m pytest tests/ -q --tb=no. JS/encoding/core checks:
python scripts/check_js.py. Tests keep private data and archives under logs/.

## Windows desktop development

Windows versions are valid only during their writer-excluding handle lifetime.
Rapid changes can collide in file timestamps, so tool hashes and negative dedup
candidates are rechecked after handles close. Request-scoped batch pins still
allow reuse while continuously held. Separate one-file requests can require more
hashing; current throughput has not been remeasured after this safety correction.

The Windows desktop validation workflow on codex/linux-fits-tools-validation
runs the pinned source suite, requires four native Windows contracts to pass,
builds a fresh executable, and checks it with headless Chrome and a real native
WinForms/WebView2 window using private data.
Browser dependencies are installed in a separate .venv-browser environment.
Run 38953312051 validated commit 4f087058: 807 source tests passed (two skips),
all four required Windows contracts executed, and the fresh packaged browser
gate passed 23/23 and native window smoke passed 7/7. Linux passed 804 tests
(five skips), covering the POSIX-only Windows skip. Only the frozen
popularity snapshot check remains unverified without its original backup.

Lossless Rice operations bundle native CFITSIO 4.7.0 fpack/funpack and zlib
1.3.2 under vendor/fits-tools/windows-x64. Both utilities statically link the
Microsoft C runtime (their PE imports require only KERNEL32.dll). Source URLs,
hashes, binary hashes, compiler/SDK/CMake versions and notices are retained.
`python scripts/build_fits_tools.py` rebuilds them using installed Visual Studio
C++ Build Tools and bundled CMake. Ordinary app builds verify the existing
utilities and embed them; no download or compiler is needed at runtime.

Use system Python for `python scripts/check_browser.py --desktop` (Playwright
and system Chrome); the pinned `.venv-build` environment owns application tests
and compilation and does not include Playwright.

The independent repository uses Python/FastAPI, pywebview, Node source checks
and Playwright with system Chrome for private packaged-app browser tests.
.venv-build was moved with the application and can be reused through its own
Scripts/python.exe; build outputs may contain stale original-root paths and
must be regenerated before use.

For source tests and builds, invoke .venv-build/Scripts/python.exe explicitly.
The build environment does not include Playwright. This machine's system Python
3.14 has the existing Playwright/browser runner; use it for check_browser.py
against the freshly compiled private executable. The browser's synthetic FITS
fixtures use only the standard library and are independently decoded with
Astropy in the source tests. No dependency installation or build-lock change is
needed to use these separate runners.

The separate browser environment also runs
`.venv-browser/Scripts/python.exe scripts/check_native_window.py --exe dist/NightSeam.exe`.
This checks the actual WebView2 bridge, standard native frame styles, move/resize
with stable interface density, maximize/restore, cancellation of the real folder
picker, graceful shutdown of the owned process tree/listener, and saved normal
bounds. It uses private executable/data/archive/temp, LOCALAPPDATA and WebView2
profile paths. The diagnostic `--require-native-window` app flag refuses browser
fallback; it cannot be combined with `--browser` or `--no-browser`.
The embedded-page screenshot and measured bounds/DPI remain in the CI artifacts.
Physical display scaling changes, multiple monitors and Snap gestures still
require manual hardware acceptance; programmatic resize does not verify them.

The restricted sandbox may deny Chromium named pipes, Playwright startup or
cleanup of directories created under earlier ACL tokens. Use an elevated gate
when necessary, keep temporary files under ignored logs/, and report launch
failure as exit 2. Do not kill or close a user's application to run tests.

Tests patch archive/data/window-placement paths to ignored private locations.
Source and packaged libraries moved independently and must never be used by
tests. File operations check resolved roots and refuse links before recursive
movement/removal. Explicit UTF-8 without BOM is required for shipped text.

Git local maintenance.auto=false and gc.auto=0 prevent background maintenance
problems. No remote repository was configured during the split.
