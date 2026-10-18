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

## Linux browser packaging

Linux packaging now has its own exact active dependency closure in
requirements-build-linux-lock.txt. Run scripts/build_linux.py on glibc Linux
x86_64/CPython 3.12. It bundles the browser-mode runtime and native FITS tools,
inspects bundled ELF GLIBC requirements and creates a portable tarball without
profile data. The initial local build/smoke passed on glibc 2.41 and its highest
numeric bundled requirement was 2.35. An Ubuntu 22.04 package/browser CI job is
required alongside source/Windows gates. See LINUX.md for writable paths,
runtime requirements and exact verification commands.

## Windows desktop development


Windows versions are valid only during their writer-excluding handle lifetime.
Rapid changes can collide in file timestamps, so tool hashes and negative dedup
candidates are rechecked after handles close. Request-scoped batch pins still
allow reuse while continuously held. Separate one-file requests can require more
hashing. A post-correction hosted Windows run imported 512 equal-size FITS files
(2.12 GB) in 87.02 seconds, averaging 23.29 MiB/s on two logical CPUs. The original
workstation has not been remeasured. See docs/import-performance-20261015.md.

Lazy admission of newly committed files into the request-local index now reduces
the same 512-file workload's archive reads from 47.178 GB to 25.042 GB (46.9%).
The new packaged run took 69.023 seconds, 29.358 MiB/s, on a different Intel
runner; the earlier run used AMD hardware. These timings are not a controlled
comparison. Full byte/duplicate/edit and native Rice audits passed. See
docs/import-profile-20261017.md for the source profile, limits and retained JSON.

Manual CI's `run_scaling_checks=true` additionally exercises the real Windows
4,096-pin boundary and builds the immediate pre-lazy baseline for sequential
ABBA packaged comparisons on the same runner. See DEVELOPMENT.md for commands,
resource-measurement limits and the private acceptance ZIP. Physical displays
and a genuinely clean/disconnected Windows VM still need separate acceptance.

The same-runner Windows ABBA comparison passed: baseline 98.020/89.795 seconds,
candidate 55.548/58.812 seconds, median 93.908 → 57.180 seconds (39.1% lower).
These are two warm-cache observations per build, not a general timing guarantee.
All nine 4,095/4,096/4,097-file pin cycles and cancellations passed, with handles
returning to baseline or lower. Metrics and limits are in
docs/import-scaling-20261017.md; the prepared acceptance kit is retained with
that run's Windows artifact.

The separate Archive resource checks workflow exercises one file per directory,
eight additional shared ancestors and fifty request cycles (45 measured after
five warmups). Request-owned
ancestor sharing reduced deep-layout held handles from 86,016 to 12,306 (85.7%),
with cleanup and byte/safety checks passing. Source/build/browser/native gates
remain separate. See docs/import-directory-resources-20261017.md for raw counts,
memory samples, limits and baseline/candidate provenance.

Import preparation now limits its rows in SQL; duplicate lookup reads complete
256-row pages without holding the database lock during hashing. Request-owned
file/directory pins and temporary hash handles have a combined 16,384-handle
budget, with conservative overflow reads. The packaged Windows gate additionally
runs `scripts/check_import_lifecycle.py`: a complete-body disconnect with queued
archive work, followed by actual native close during an active pinned import.
It uses an external SQLite writer briefly to establish overlap, audits committed
bytes/history and requires graceful process/listener cleanup before disposing
of its private job. A disconnect drains accepted ingest; it does not imply
cooperative upload cancellation. Physical/offline acceptance remains separate.
The resource workflow also profiles real HTTP against 6,000 pre-existing mixed
plain/Rice files (4,797 share one size), verifying late duplicates, malformed
inputs, edited/missing originals, native restoration and exact history/byte
publication. Local source SELECTs fell 58,064→493 without reducing hash work;
query pages now merge existing indexed streams and defer full-row hydration.
Alias/exact preflight reads also cooperate with cancellation. See
docs/populated-import-20261017.md for reproducible commands and scope limits.

The Catalog validation workflow selects Linux and Windows correctness checks on
pull requests and main pushes, with a stable terminal `validation` check. The
Windows job runs the pinned source suite, requires native Windows pinning/process contracts
to pass, builds a fresh executable, and checks it with headless Chrome and a real
native WinForms/WebView2 window using private data. Weekly and optional manual
runs add larger benchmarks; see DEVELOPMENT.md. These permanent triggers become
active when the branch's workflow is merged into main.
Browser dependencies are installed in a separate .venv-browser environment.
Run 39009833605 validated commit f851f14b: 814 source tests passed (two skips),
all four required Windows contracts executed, and the fresh packaged browser
gate passed 23/23 and restricted-runtime native smoke passed 7/7. Linux passed 811 tests
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

The workflow now also uses --restricted-runtime for the native smoke. Only the
child app receives an empty PATH and has Python/Node environment overrides removed;
the controller retains its own interpreter. A separate packaged HTTP benchmark is
run with `.venv-build/Scripts/python.exe scripts/bench_windows_import.py --exe dist/NightSeam.exe`.
It generates 512 unique 4,150,080-byte FITS files before timing and uploads them
sequentially in 24-file requests. It then audits source/archive hashes, catalog
publication, unchanged duplicate identity/class/session/location and detection of
a same-size archive edit with restored mtime. A fresh private app instance checks
Safe Rice ingest and actual native restoration with exact original bytes.
The JSON report is logs/windows-import-benchmark.json; --count and --batch-size
allow smaller diagnostic runs. Timing includes real HTTP multipart import and
publication, excludes fixture generation and audits, and has no cold-cache control.
Installed OS/.NET/WebView2 components remain on the hosted Windows machine, and
network denial is not imposed. These checks do not establish clean-VM or offline
compatibility. The historical workstation timings predate the Windows hash-cache
safety correction and are not a controlled baseline for this hosted runner.
The successful October 17 report verified all 512 original source/archive pairs,
three duplicate probes, the restored-mtime edit, native Rice restoration and
owned-process/scratch cleanup. The 22 requests took 87.017 seconds in total;
the first full batch took 11.032 seconds including resolver setup and the last
full batch took 4.062 seconds. Post-first full batches ranged from 2.222 to 4.673
seconds. Retained evidence is in the run's Windows validation artifact and local
logs/windows-import-success/logs/windows-import-benchmark.json.
The separate hardware procedure is documented in
[Windows manual acceptance](docs/windows-manual-acceptance.md).

The restricted sandbox may deny Chromium named pipes, Playwright startup or
cleanup of directories created under earlier ACL tokens. Use an elevated gate
when necessary, keep temporary files under ignored logs/, and report launch
failure as exit 2. Do not kill or close a user's application to run tests.

Tests patch archive/data/window-placement paths to ignored private locations.
Source and packaged libraries moved independently and must never be used by
tests. File operations check resolved roots and refuse links before recursive
movement/removal. Explicit UTF-8 without BOM is required for shipped text.

Git local maintenance.auto=false and gc.auto=0 prevent background maintenance
problems. The repository is published on GitHub; see RELEASE_WORKFLOW.md.
