# Independent catalog release record

## Directory fan-out and longer resource checks — October 17, 2026

The resource stress tool now supports additional directory depth and records
layout/path lengths, absolute retained Python heap and warmed steady-cycle
growth. The default two-MiB retained-growth limit includes stored report samples;
RSS remains observational. Focused regressions cover one-file-per-directory/deep
layouts and deliberately retained allocations that must fail the growth gate.
Seven resource tests pass locally with the Windows sampler check skipped here.
A separate Windows workflow exercises 4,097-file fan-out, eight additional shared
ancestors and fifty 256-file request cycles. It runs for relevant archive/observer
PR changes or manual dispatch and retains each JSON/log.

## Archive scaling and controlled comparison tools — October 17, 2026

Added an opt-in Windows stress checker for 4,095/4,096/4,097 small valid FITS,
three request-pin cycles, native handle/RSS/Python heap samples, capacity fallback,
positive rehashing, writer/archive exclusion, cancellation draining and aggregate
handle cleanup. Direct seeding isolates pin/lookup stress from HTTP throughput.
Added a same-runner ABBA packaged comparison against the immediate pre-lazy
baseline, using the same pinned build environment and all existing byte,
duplicate/edit/Rice and owned-cleanup audits. Timings retain sample ranges and
uncontrolled-cache caveats without speed thresholds. Manual CI retains all logs
and reports. A local acceptance kit packages the executable, synthetic fixtures,
manifest/verifier and unrun checklist for genuine clean/offline/display checks.

The initial 15 focused regressions and full Linux suite (856 passed, 6 skipped)
passed locally. The first Windows run passed 857 other tests but exposed retained
executor semaphore handles in the stress observer. The worker probe now releases
its executor scope before sampling, with a separate observer-handle regression;
aggregate/per-request cleanup limits remain intact. Workflow lint and JavaScript/encoding/core
checks pass. Preparing the kit does not execute physical or clean-VM acceptance.

The corrected revision 9e38f14 passed Linux **857 tests, 6 skipped** and Windows
**861 tests, 2 skipped**, including the five Windows resource-accounting checks.
A parallel PR browser run passed 23/24 and native 7/7, exposing an immediate
button count before the archive-health report's asynchronous rendering finished.
The check now waits for the second session button before retaining the exact
count/deletion assertions; no click or request is retried.

The complete [measurement run](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39033962051)
passed all nine Windows boundary cycles and three cancellation checks. Handles
returned to the initial baseline or lower; steady cycles retained 0.024–0.030 MiB
of Python heap after cleanup. Held heap growth was 15.836–18.599 MiB, with
82.559–97.863 MiB RSS while pins were held. Small retained-heap drift includes
observer type-cache overhead; the sampler now caches its ctypes definitions,
with a separate 200-sample Windows heap regression. All 12,288 archived files
retained their hashes.
The same-runner packaged ABBA comparison measured baseline imports of
98.020/89.795 seconds and candidate imports of 55.548/58.812 seconds: median
93.908 → 57.180 seconds, **39.1% lower elapsed time** for this small sample.
All four full byte/duplicate/edit/Rice/cleanup audits passed. Both builds shared
the pinned environment and two-CPU Intel runner; scheduling/caches remained
uncontrolled, so the result is not a general timing guarantee. The source
profile reconfirmed 6,034 hashes/25,041,582,720 archive bytes and 512 transactions.
Browser **24/24** and native **7/7** passed. The generated acceptance kit's hashes
were verified, with physical/clean-VM checks explicitly unrun. See
[the scaling report](docs/import-scaling-20261017.md) for metrics, commands,
limits, executable hashes and retained evidence.

## Native smoke bridge startup — October 17, 2026

A later PR run passed source/build/browser gates but failed its first native
capability assertion with `Resulting promise was garbage collected`. The harness
forced a reload during pywebview startup, allowing background bridge injections
to overlap. It now observes the initial navigation and retains one capability
Promise on the window while polling its settlement. Rejection, missing callbacks
and timeout remain fatal; there are no RPC retries. Three JavaScript-engine
regressions pass for retained pending calls, rejection and missing callbacks.
This changes the smoke harness only; the successful import measurements below
remain applicable.

## Lazy request-local dedup admission — October 17, 2026

Newly committed archive files now enter the Windows request-local negative index
when a later upload first encounters them as candidates. Admission hashes current
archive bytes under live writer-excluding file and directory pins, validates the
lexical path/root identity, and retains at most 4,096 files. An initially empty
batch takes its lease only on first successful admission. Positive duplicates
still rehash; unsupported versions, open writers and capacity overflow retain
current-byte checks. Failed admission retains no new handles. Request cleanup
closes pins before releasing the lease, including cancellation and SQL failures.
Publication and transaction boundaries are unchanged.

A 24-file equal-size HTTP request now hashes 23 earlier files rather than 276.
New regressions cover real Windows write/rename exclusion and open-writer fallback,
first-lease ownership, positive rehashing, changed bytes before admission and
restored-mtime edits across requests, internal leaf/parent aliases, root replacement,
capacity/refused/failed admissions, cancellation on five paths, rollback and visible
cleanup failure. The existing rename-race helper now forwards the cancellation
argument without changing its contention or byte-preservation assertions.

The repeated 512-file source profile measured **6,034 archive hashes and
25,041,582,720 bytes read**, versus 11,368 hashes and 47,178,109,440 bytes before:
**46.9% fewer archive bytes**. SQL SELECTs fell from 10,567 to 5,233, while all 512
image/history transactions retained one BEGIN/COMMIT each. The separate packaged
import took **69.023 seconds at 29.358 MiB/s**; all source/archive byte, duplicate,
edit, Rice restoration and cleanup audits passed. The earlier run took 85.445
seconds, but its AMD runner and the new Intel runner differ; these timings do not
establish a controlled speedup. Both used Windows Server 2022/two logical CPUs
without cold-cache control.
[Successful measurement run](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39029236559).

The tested implementation is 2b9549666c5fdba0fb8624957d251154ea67d416.
[PR validation](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39029221957)
passed Linux **838 tests, 6 skipped**, Windows **842 tests, 2 skipped in 253.82
seconds**, all six required Windows contracts, packaged browser **24/24**, and
restricted native WinForms/WebView2 **7/7**. The separate measurement run's Linux
suite passed **838 tests, 6 skipped in 179.59 seconds**. JavaScript, encoding/core,
dependency and actionlint checks passed. The original popularity snapshot remains
unavailable; the other skips have platform-specific coverage. Measurement evidence
is retained in [the import profile](docs/import-profile-20261017.md).

## Support diagnostics, continuous validation and import profiling — October 17, 2026

Added a local **Support report** JSON download with allowlisted app/runtime/package
versions, FITS utility integrity and at most 25 recent warning/error categories.
It excludes raw messages, tracebacks, paths, library contents and environment
values, and remains available during archive work. Frozen builds retain the seven
selected distributions' metadata. The packaged keyboard-download check passed,
including version availability, verified tools, privacy and concurrent export.

CI now selects Linux and Windows source/build/packaged gates on pull requests and
main pushes, with a stable terminal `validation` check. Markdown-only diffs skip
expensive jobs; unknown diffs run everything. Weekly/manual runs can include larger
measurements. Main/scheduled triggers take effect after merge; branch protection
is configured separately. The separate browser environment is excluded from the
shipped-text encoding scan, matching the existing application environments.

The isolated source profiler counts actual SHA-256 bytes and SQLite transactions
and measures selected synchronous phases across worker threads. Its real HTTP
512-file run measured 11,368 archive hashes (47.178 GB) and 44.828 CPU seconds in
archive hashing, 62.6% of process CPU. Both the source profile and separate packaged
benchmark passed full byte, duplicate/edit and owned-cleanup audits; packaged Safe
Rice/native restoration passed. Packaged import: 85.445 seconds, 23.716 MiB/s.
See [the measured profile](docs/import-profile-20261017.md) for attribution limits.

Local Linux source validation: **819 passed, 5 skipped in 186.08 seconds**;
hosted Linux: **819 passed, 5 skipped in 151.01 seconds**. Hosted Windows:
**822 passed, 2 skipped in 211.50 seconds**, all four required native contracts
executed; restricted native window smoke **7/7**. JavaScript, encoding/core,
dependency checks and actionlint passed. Measurement/source commit: efa2c929.
[Run and retained evidence](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39014398000).

The initial packaged browser gate passed 23/24, including the new support report,
and found progress overlapping the toolbar at 900px. The header now reserves the
indicator's intrinsic width and wraps actions. A focused Chromium check passed
eleven widths from 480–1586px with count/clock/spinner and all actions contained;
the permanent overlap gate now includes both sides of the 620px breakpoint.
The complete packaged rerun is retained in
[PR #1's validation checks](https://github.com/Terrazocker/nightseam-catalog/pull/1/checks).

The original frozen popularity snapshot remains unavailable. Clean-VM/offline,
physical DPI/monitor/Snap and native Save As acceptance remain manual; the
documented Windows procedure covers those checks. No public release was made.

## Windows import benchmark and restricted runtime — October 17, 2026

The freshly packaged app imported 512 synthetic 4,150,080-byte FITS files through
real HTTP in 24-file batches: **2,124,840,960 bytes in 87.017 seconds, 23.287 MiB/s**.
The Windows Server 2022 runner had two logical CPUs. The first full request took
11.032 seconds including resolver setup, later full requests ranged from 2.222 to
4.673 seconds, and the last took 4.062 seconds. Generation/startup/audits/probes
were excluded; caches were not flushed. Different hardware and the intervening
cache safety correction prevent a controlled comparison to the old workstation.

Every original source and initial archived copy matched its SHA-256. All 512 IDs
and integrity records were present; duplicate probes retained class, session,
capture coordinates and location, and an actual same-size/restored-mtime archive
edit was detected on the next request. A fresh private instance passed Safe Rice
ingest and actual native uncompress with byte-exact restoration. Both app instances
and their scratch were cleaned up. Audit SQLite handles now close explicitly;
a regression test proves closure without relying on garbage collection.

The benchmark and native smoke used an empty child PATH with Python/Node
environment overrides removed. Native WinForms/WebView2 passed **7/7** at 96 DPI;
the browser gate passed **23/23**. The browser readiness helper now waits for
requested window metrics and the body's dynamic viewport height to converge
before geometry assertions, fixing a mixed-viewport sampling failure. Independent
packaged checks execute after a successful build even if another check fails;
any failed check still fails the overall job.

Final Windows source validation: **814 passed, 2 skipped in 207.12 seconds**;
all four required Windows contracts executed. Linux: **811 passed, 5 skipped in
160.87 seconds**. JavaScript, encoding/core and dependency checks passed.
Tested Windows commit: 9775ebae8206c4563ca4fa893faf88a8c9d6803c.
https://github.com/Terrazocker/nightseam-catalog/actions/runs/39009833605
Evidence: logs/windows-import-success.log and
logs/windows-import-success/logs/windows-import-benchmark.json;
logs/import-benchmark-linux.log/xml and logs/import-benchmark-js.log.

The hosted machine retains installed OS/.NET/WebView2 components; network denial
and clean-VM compatibility were not tested. Physical DPI changes, monitor
transitions and Snap remain manual; docs/windows-manual-acceptance.md records the
procedure. The original popularity snapshot provenance check remains blocked.

## Packaged native Windows smoke - October 16, 2026

The Windows workflow now tests a real WinForms/WebView2 window in addition to
the packaged headless Chrome gate. All **7/7 native checks passed**: the real
host bridge, standard frame styles, move/resize with stable interface density,
maximize/restore, real folder-picker cancellation without changing the private
archive, graceful close with every owned descendant/listener stopped, and saved
normal placement. The runner measured 96 DPI and restored/saved bounds
40,40,900,648. The embedded-page screenshot and measured results are retained.

The harness isolates executable/data/archive/temp, LOCALAPPDATA and WebView2
profiles. The new diagnostic --require-native-window option fails before browser
fallback and rejects --browser/--no-browser combinations. Readiness now waits for
WebView2's actual catalog target, the bridge's DOM marker using a CSP-safe selector,
and the private archive input before opening Browse. Initial gate attempts exposed
early page discovery and blocked string-evaluation assumptions in the new smoke;
the application Content Security Policy remains unchanged.

The final Windows run passed **807 tests, 2 skipped in 206.82 seconds**, all four
required Windows contracts, a fresh executable build and **23/23 packaged browser
checks**. Linux passed **804 tests, 5 skipped in 122.72 seconds**. JavaScript,
encoding/core and dependency checks passed. Tested Windows commit:
4f087058bf2b10a4f2860b219747f97631da71d7.
https://github.com/Terrazocker/nightseam-catalog/actions/runs/38953312051
Local evidence: logs/native-window-success.log,
logs/native-artifact-success/tests/browser/artifacts/native-window.json and
native-webview.png, logs/native-smoke-final-linux.log/xml.

Physical display scaling changes, multiple-monitor transitions and Snap gestures
still require manual hardware acceptance. The original frozen popularity snapshot
remains unavailable; its provenance check is the only check unverified across both
source platforms. No live replacement data was fetched.

## Windows CI and cross-platform validation - October 16, 2026

GitHub Actions on Windows Server 2022 x64 verified all 46 application dependency
pins and ran the complete source suite on CPython 3.12.10. The initial run exposed
a same-size/restored-mtime tool-cache collision. Windows file versions now include
an opaque handle lifetime, preventing cached tool or negative-dedup reuse across
unpinned gaps. Continuously held request pins retain batch reuse. Regression
assertions still require changed utility bytes to fail integrity validation;
individual Windows requests now explicitly expect conservative fresh reads.
Current throughput has not been rebenchmarked after this safety correction.

The corrected Windows run passed **803 tests, 2 skipped in 238.59 seconds**.
The workflow separately required all four Windows-only process-tree, directory
pinning, HTTP batch and file-sharing contracts to execute successfully. A fresh
PyInstaller executable (0.1.0-g78b13b19) passed the packaged headless Chrome gate
**23/23**, including actual Rice ingest/restoration, session symbols, responsive
layout and map checks. The harness used private executable/data/archive/temp
stores. Final Linux source validation passed **800 tests, 5 skipped in 122.10
seconds**; JavaScript, encoding and core guards passed.

Tested source commit: 78b13b19bdb01836646799655b4a2e98cbcde807.
Successful Windows run with retained JUnit/log/browser artifacts:
https://github.com/Terrazocker/nightseam-catalog/actions/runs/38946414023
The Windows POSIX-only skip is covered on Linux. The sole check still unverified
across both platforms is the original frozen popularity snapshot's provenance:
the ignored raw snapshot and its metadata are unavailable here. No fresh data
was fetched and no synthetic replacement was substituted. Local evidence:
logs/windows-validation-success.log and logs/final-linux-tests.log/xml.

## Linux x64 native FITS tools - October 16, 2026

Source runs now select verified fpack/funpack bundles for Linux x64 or Windows
x64, and explicitly reject unsupported platforms. The new Linux binaries use
the same pinned, unmodified NASA CFITSIO 4.7.0 and zlib 1.3.2 sources as Windows.
CFITSIO/zlib are statically linked; system glibc 2.34 or newer is required.
Binary/source hashes, compiler/CMake versions, flags, dependency information and
upstream licenses accompany the bundle. The shared build script supports native
Linux rebuilds while retaining the Windows build and packaging path.

Native-call observers and failure injection now identify tools independently of
the Windows filename suffix. Integrity-cache tamper tests cover both bundles;
new checks cover supported/unsupported platform selection and missing bundles.
The focused native compression/ingest/performance suite passed **64 tests, 3
skipped**. The final full suite passed **800 tests, 5 skipped in 121.61 seconds**,
resolving all 29 failures from the earlier Linux run. Four skips cover Windows
contracts; one covers an absent optional local popularity snapshot. JavaScript,
encoding and core checks passed. Tests used private data/archive stores in logs/.
Evidence: logs/linux-compression-tests.log, logs/linux-full-tests.log,
logs/linux-full-tests.xml and logs/linux-js-check.log. Windows packaged desktop
validation was not run in this Linux environment.

## Linux cloud environment validation - October 16, 2026

On Linux x64 with Python 3.12.14 and dependencies from requirements-dev.txt,
the full source suite completed with **765 passed, 29 failed, 5 skipped in
125.25 seconds**. All failures are in native compression (10), compressed
ingest (16), or compression performance (3). The bundled Windows PE fpack.exe
cannot execute here; native PermissionError failures also cause downstream
publication assertions. Four skips cover Windows-specific contracts; one covers
an absent local popularity snapshot.

JavaScript imports, DOM references, syntax, encoding and core checks passed;
pip check found no broken requirements. Tests used private stores under ignored
logs/. No application code or native binaries changed. Windows packaging and
the packaged desktop browser gate were not run on Linux. Full traceback evidence
is in logs/environment-retest.log, machine-readable results in
logs/environment-retest.xml, and JS output in logs/environment-retest-js.log.

## Session compression symbols - October 16, 2026

Every capture-session row now shows a compact, distinct symbol for compressed,
uncompressed, mixed or unknown storage. Hover and accessible labels give exposure
counts; the selected-session inspector shows the same storage state. The symbols
refresh after a completed compression/uncompression job and preserve table density.

Storage classification is recorded from the existing metadata read, detecting
gzip wrappers and tiled compressed images even when named plain .fits. Conversion,
restoration, reindex and copied catalogs maintain it atomically; accepting changed
external contents clears it. Historical Rice mappings are classified by matching
the current full-file digest, including restored originals. Other historical
records remain visibly unknown until reindex. Library rendering performs no extra
file reads. Metadata tests cover misleading extensions, gzip/plain bytes, legacy
backfill and accepted changes; the UI gate checks all symbols and actual conversion.

Validation: full suite **798 passed, 1 skipped in 229.89 seconds**; final focused
session rollup run **25 passed**. The fresh final packaged browser gate passed
**23/23**, including all four symbols, keyboard selection and real compression /
restoration updates. The compact inspector screenshot was visually checked.
JS/core/encoding checks passed; local --allow-dirty --no-stage release validation
reported **0 errors**. All checks used private workspace test archives.

## Full import/session pipeline and phased progress - October 15, 2026

The representative 512-file HTTP investigation reproduced slow import: 2.12 GB
took 168.51 seconds, with the final full 24-file batch taking 15.78 seconds.
Request-scoped negative dedup indexes now pin earlier same-size bytes and
directory lineage once per batch. Unrelated archive mutations wait for the lease;
positive duplicates still hash actual bytes. Index admission is bounded and
unsupported versions/open writers fall back conservatively. Waiting imports use
asynchronous admission; index preparation cooperates with cancellation and closes
pins before releasing its lease. Images, integrity, compression mappings and
history now share one short commit per file. Browser batches contain up to 24
plain or compressed uploads. The final same HTTP workload measured **24.58
seconds**, with the last full batch taking **1.35 seconds** (6.9x faster overall).

Manual session conversion and library copying now prepare files and publish
replacements concurrently with up to CPU count/24 workers. Per-image verification
filenames replace the shared roundtrip path. Complete journal plans precede any
swap; originals remain available through commit. Workers drain explicitly before
recovery even when a coordinator progress callback fails. Reused directory pins
and source-version checks remove repeated work while retaining integrity gates.
Behavioral tests found attribute-only Windows handles did not enforce sharing;
file/directory pins now use GENERIC_READ. Tests prove writes/renames are blocked
until closure and reject cached/previously unseen symlink leaves. Cached directory
lookups check immediate-parent identity/reparse state before each leaf lookup.

The session/copy dialog has an independent **Fast / Safe** slider, default Safe
for manual conversion and omitted API choices. Safe restores and compares the
original complete-file hash; Fast skips the round trip and records distinct
provenance. Both verify stored bytes; Uncompress always checks a known original
hash. Mode is locked during work and restored when reopening a job.

Separate **Preparing files** and **Writing and replacing files** bars expose real
counts. Checking originals, catalog commit, retained-original cleanup and library
refresh remain visibly active after preparation. Complete appears only after
refresh. The packaged gate caught and fixed newly rendered session buttons staying
disabled after refresh, verification controls remaining visible for Uncompress,
and resumed jobs retaining the reopening action. A resumed job requires a fresh
action before restarting. UI captures were visually inspected.

Final full suite: **796 passed, 1 skipped in 240.29 seconds**. Fresh final
packaged browser gate: **22/22 passed**, including mode keyboard/explanation,
actual roundtrip/copy, writing progress, final refresh and restored session
actions. JS/core/encoding checks passed; local --allow-dirty --no-stage release
validation reported **0 errors**. Complete 48-file session conversion measured
**13.27 to 2.50 seconds Safe**, with **2.00 seconds Fast**, including actual swaps,
catalog commit and cleanup. Tests/measurements used synthetic private stores,
never user archives or supplied example data. Executable replacement retained
three backups.
Final housekeeping completed with zero failures. Eight generated benchmark
archives were removed after measurement; scripts, profiles, progress snapshots
and batch summaries remain under ignored logs/.

## Session deletion acknowledgements and import throughput - October 15, 2026

Archive Health now groups missing exposures by object identity and canonical
capture session. **Acknowledge session** resolves every retained missing record
in that session; **Accept all deletions** resolves retained deletions across the
report. Expandable exposure details keep individual actions available. Present
members and changed-content findings remain untouched. Counts include retained
findings beyond the 200-detail display; omitted findings explicitly require a
subsequent scan. One confirmation covers the selected batch. All file paths and
record snapshots are revalidated before one SQLite transaction removes records,
baselines and obsolete compression aliases and records action history. Restored
files, stale records or transaction failures refuse/roll back the whole batch.

Import now prepares uploads and optional Rice conversion concurrently outside
archive_lock, bounded by CPU count and at most 24 files. Publication and catalog
writes retain their order and archive lock. A SQL failure stops later publication;
cancellation drains workers before cleaning staging. Known duplicates receive a
fresh-byte check before encoding. Negative same-size candidates use a bounded
hash memo guarded by pinned NTFS/ReFS identity/change time, with conservative
fallback elsewhere; positive duplicates always rehash. The cache retains useful
entries above its admission limit rather than thrashing. The catalog resolver
survives upload chunks with explicit revision invalidation and repeated target
matching is memoized/offloaded. Native tool integrity checks use strong versions;
compression alias/history lookups gained indexes.

Final private 48-file/199 MB direct-ingest measurement: plain FITS **12.70 to
2.83 seconds**, Fast Rice **6.56 to 1.66 seconds** using 12 preparation workers.
Earlier archive reads fell from **1,128 / 4.68 GB** to **47 / 195 MB**. This is
a local synthetic result, not universal disk/CPU saturation; plain equal-size
metadata inspection and unsupported filesystem fallback remain documented in
BUGS.md and docs/import-performance-20261015.md.

Validation: final full suite **787 passed, 1 skipped in 194.86 seconds**. Coverage
includes actual parallel native conversion, ordered commits, failure boundaries,
restored-mtime edits, unsupported-version fallback, tool tampering, duplicate
encoding avoidance, bounded cache behavior, same-session healthy/calibration
members, >200 findings, retained-limit overflow and atomic batch rollback.
Fresh final executable browser gate **21/21 passed**, including session/all
acknowledgements and 24-file compression chunks. Pending/resolved UI captures
were visually inspected. Build JS/core/encoding checks and git diff --check
passed; local --allow-dirty --no-stage release validation reported **0 errors**.
Executable replacement preserved three backups. Tests and profiling used private
synthetic archives; user stores and supplied example files were untouched.
Final housekeeping completed with zero failures. Ten owned synthetic benchmark
archives were removed after measurement; scripts, summaries and profiles remain.

## Fast / Safe ingest switch - October 15, 2026

The Import compression checkbox now has a compact sliding **Fast / Safe**
switch: Fast on the left is the initial UI choice; Safe on the right enables
native decompression and original full-file SHA-256/size comparison. The circle
slides between positions. The switch is disabled when compression is off and
locked during archive work; the selected mode is captured once per import and
sent on every chunk, including calibration attachments.

Fast trusts Rice reversibility and skips the decoder/restored-file read. Both
modes still validate Rice output, recheck original input stability, hash/flush
compressed staging, and flush/re-read the published compressed file before the
catalog commits. Original hashes remain available for later Uncompress. Fast
integrity provenance is rice_ingest_fast; Safe remains verified_rice_ingest.
The validated API compression_mode defaults to Safe when omitted, preserving
existing clients. Manual session/library conversions and missing-file recovery
retain mandatory strict restoration checks independently of the ingest choice.

An **i** explanation opens on hover, keyboard focus and tap/click, with explicit
Fast/Safe tradeoffs and source preservation. Escape dismisses it. Review added
screen-reader description links and hoverable pointer travel; the packaged gate
caught and fixed clipping in the scrolling import card by placing the tooltip
outside that scroll container and choosing a position within the viewport.
The native checkbox switch supports Space, visible focus and reduced motion.
Import results distinguish Fast (restoration unchecked) from Safe (verified).

Validation: **771 passed, 1 skipped in 261.70 seconds**; **42 focused native
checks passed**. Coverage proves Fast invokes fpack only, Safe invokes funpack
and rejects hash mismatch, and both reject tool, publication and SQL failures.
Missing-file recovery still verifies strictly when Fast is requested. Fresh
packaged browser verification passed **20/20**, including circle movement,
hover/focus/tap/dismissal, readable tooltip placement/pointer travel, both real
ingest modes, calibration, exact later restoration and per-chunk mode locking.
The captured UI was visually inspected. JS/encoding and whitespace guards passed;
the local --allow-dirty --no-stage release check reported **0 errors**. The
executable was backed up before replacement and three recovery copies remain.
Repository housekeeping completed without failures. Tests used private synthetic
catalogs/archives and left user stores and supplied example data untouched.

## Opt-in lossless Rice during ingest - October 15, 2026

Import Exposures now has an unchecked **Compress on import with lossless Rice**
checkbox. Its per-window choice is captured once for every upload action and
sent on every multipart chunk, including calibration attachments. The API's
compress form flag defaults to false. Results show converted files and explicit
reasons when unsupported/already-compressed files were imported unchanged.

Supported integer uploads use the same native encode_verified gate as manual
session conversion: fpack -r, funpack -C, complete-file SHA-256/size comparison,
source stability check and flushed compressed staging. The final published
destination is flushed and rehashed before commit, including the copy fallback
on filesystems without hard links. Image, stored-byte integrity baseline and
original/compressed identity mapping commit together. Only verified compressed
output remains; temporary original/verification stages are removed after commit.
External source files are never changed or deleted. Conversion failure publishes
neither representation; cancellation signals native checkpoints and drains the
worker before cleaning staging.

Duplicates retain existing records and representations. Re-uploading either
known representation of a missing managed exposure restores its current recorded
format under the same ID/path, preserving metadata, frame class and session
anchors. Restoration verifies bytes and commits fingerprint, integrity baseline
and mapping together. Collision-resolved names survive Uncompress safely.

Validation: **765 passed, 1 skipped in 279.61 seconds** in the pinned environment.
Focused native/auto-ingest verification passed **36 tests**; ingest/API regression
checks also passed. Coverage includes exact restoration and untouched physical
sources, unsupported floats/gzip/existing Rice/directives, tool/hash/SQL failures,
corrupted publication, cancellation, dedup, missing restoration in both directions,
collision names and multipart defaults. Fresh packaged browser verification
passed **19/19**, including checking the actual checkbox, physical file upload,
compressed-only archive storage, compressed calibration attachment, exact native
Uncompress restoration, option propagation across 8+2 chunks and unchanged-file
reason text. Existing compression/copy and responsive UI gates also passed.

The development executable was rebuilt with a verified backup before replacement;
three recovery executables remain. JS/encoding and whitespace guards passed;
the local --allow-dirty --no-stage release check reported **0 errors**. Repository
housekeeping completed without failures. Tests used private synthetic catalogs
and archives; user catalog data and supplied example exposures were untouched.

## Manual lossless Rice compression and library copies - October 15, 2026

The selected-session inspector now offers **Compress losslessly** and
**Uncompress**. Whole-session requests validate exact membership against a
stored anchor, including attached calibration frames. Native fpack -r creates
Rice files; funpack -C must reproduce each original complete-file SHA-256 before
any original is replaced. Every HDU is checked; compression accepts integer
BITPIX 8/16/32 and refuses unsupported images or conflicting FZ directives.
Managed decompression verifies the retained original hash and filename. Existing
Rice files can be decompressed without claiming an unknown historical file hash.

Current stored-byte fingerprints and integrity baselines transition atomically;
original/compressed representation provenance preserves duplicate detection,
record IDs and session/classification metadata. Conversion runs in cancellable
workers under the independent archive lock. A durable journal retains originals
until commit, rolls back failed publication and recovers interrupted work on
startup. Pending recovery blocks archive mutations. Review caught and fixed an
empty-journal startup lock regression; archive-health regressions then passed.

**Copy library** manually creates a new destination folder with usable individual
FITS files, manifest.json and a coherent nightseam.db snapshot pointing to the
copied files. Supported integer images use verified Rice; existing Rice and
unsupported files copy intact. Destination files are independently read back and
hashed. Destination-side staging supports an archive without source scratch
space. SQLite backup uses a separate connection, without holding the live
connection lock while copying. Cancellation removes incomplete copies.
No daily automation or automatic ingest conversion was introduced.

Official CFITSIO 4.7.0 / zlib 1.3.2 utilities are embedded with source/binary
hashes, compiler/SDK/CMake provenance and upstream notices. Their static-runtime
PE imports require only KERNEL32.dll. scripts/build_fits_tools.py reproduces the
build. The normal build dependency gate verifies bundled utility hashes.

Validation: **751 passed, 1 skipped in 246.00 seconds** in the pinned environment;
the focused native suite passed **22 tests**. Coverage includes byte-exact native
round trips, calibration anchors, another object's isolation, every image HDU,
unsupported/directive refusal, successful decoder hash mismatch, cancellation,
tool/publication/SQL failure rollback, collisions, stale membership, external
changes, dedup, deep health, portable copies and startup recovery. Fresh packaged
browser verification passed **17/17**, including real native conversion, exact
restoration, copy, rejected-start handling, resume/focus, cancellation and layout.
JS/encoding guards and whitespace checks passed. The local dirty-build release
gate (--allow-dirty --no-stage) reported **0 errors**; no public release was made.
The installed executable was backed up before replacement; three recovery
executables remain. No user catalog, profile archive or supplied example exposure
was used as a test destination. Private build/test scratch and repository caches
were cleaned, preserving source archives, build logs and test evidence.

## Native Rice full-file round trip - October 15, 2026

The [native fpack/funpack report](<docs/fpack-funpack-roundtrip-20261015.md>)
records **2,057/2,057 exact original complete-file SHA-256 matches**, using
`fpack -r` followed by `funpack -C`. No saved header, sidecar or postdecode repair
was used. Every packed output was checked for RICE_1. Original bytes total
8,542,638,720; compressed bytes total 4,698,034,560 (45.00% saved).

Five sample option combinations each covered 32 distributed frames. Default
funpack and fpack-only `-C` restored 0/32 original hashes while preserving all
pixel payloads; checksum updates changed header values. All three sample modes
using funpack `-C` restored 32/32 hashes, including the whole-image tile variant.
Two earlier-workflow Astropy Rice probes preserved pixels but changed header
serialization after funpack `-C`. The research and benchmark reports now correct
the overly broad suggestion that a separate saved header would be needed.

Unmodified official CFITSIO 4.7.0 / zlib 1.3.2 utilities were built privately;
source archives, binary/script hashes, build logs, command lines and per-file
results remain in logs/fpack-roundtrip-20261015/. Final independent source audit
confirmed all original hashes, sizes, mtimes and file membership unchanged.
Temporary compressed/restored files and five private build/scratch directories
were removed with zero failures. No application feature, dependency, installed
executable or catalog data changed in this experiment. Concurrent workspace
tests make the recorded utility timings unsuitable for performance comparisons.

Full pinned-environment verification: **729 passed, 1 skipped in 257.06 seconds**.
The encoding guard and whitespace check passed; repository housekeeping completed
without failures.

## Real exposure compression benchmark - October 15, 2026

Read-only study of the supplied Example-data collection: 2,057 Seestar S30
unsigned-16-bit Light frames, 8,542,638,720 bytes (7.956 GiB), across two nights.
The [report](<docs/example-data-compression-benchmark-20261015.md>)
and accompanying CSV distinguish 37 byte-exact archive configurations and eight
pixel-exact FITS rewrite configurations, each screened twice on 32 distributed
exposures, from three actual complete-collection runs.

Full native ZIP/Bzip2 Normal: 4,299,845,611 bytes, 49.67% saved, 202.35 seconds
creation, 134.09 seconds restoration, 28.76 seconds separate SHA verification.
Full TAR/Zstd6: 4,934,975,367 bytes, 42.23% saved, 47.34/37.13/30.17 seconds.
Independent Zstd6: 4,941,270,104 bytes, 42.16% saved, 147.65/31.76/30.43 seconds.
Whole-session Zstd saved only 6.00 MiB over independent files for this collection.
The space-priority recommendation is one ZIP/Bzip2 archive per session; Zstd6
is the fast alternative. LZMA2 and the tested scientific filters/FITS rewrites
did not justify replacing that choice for these supplied Light frames.

All archive sample/full runs restored exact path membership, file sizes and
complete-file SHA-256; all FITS rewrite samples preserved pixel dtype/bits.
Native full ZIPs used Bzip2 method 12 and four members also passed independent
Python ZIP decoding/hash checks. Final audit confirmed all 2,057 source hashes,
sizes, mtimes and membership unchanged. Full run timings are single measurements;
sample timings are two-run medians, with OS caching and implementation/thread
differences documented. No cold-drive, durability or universal-FITS claim is made.

Evidence and reproducible harnesses remain under ignored
logs/example-compression-benchmark-20261015/. Every generated archive/restored
copy was verified then removed. Isolated benchmark dependencies, pip cache/temp
and empty scratch were removed (96.85 MiB, zero cleanup failures); the failed
sandbox pip attempt's five exact temporary directories were separately verified
absent. Application dependencies, installed executable and actual catalog stores
were unchanged. No backup, archive button or restore feature was implemented.

Final workspace validation: **729 tests passed, 1 skipped in 197.53 seconds**
using the pinned build environment. The encoding guard and whitespace diff
check passed. Benchmark and repository housekeeping completed without failures.

## Compressed FITS metadata implemented - October 15, 2026

Import, reindex and untracked Archive Health discovery now recognize .fit/.fits/
.fts, tiled .fz (including .fits.fz), and their gzip wrappers. Header-only file
traversal uses the primary image or first nonempty image extension after an empty
primary. Selected-extension fields override primary fallbacks; exposure and
instrument aliases and sky/site coordinate pairs are kept coherent. Standard
CONTINUE strings are joined while duplicate keys retain their first value.

Original upload bytes, complete-file SHA-256 identity, independent integrity
baselines, classification and explicit calibration/session anchors are preserved.
Compound extensions survive filename collisions. Malformed or excessive metadata
reads refuse publication and clean staging. Traversal bounds header bytes, HDU
and axis counts, gzip compressed/expanded bytes and cooperative scan time; gzip
optional header fields are bounded too. Pixel codecs are never invoked. Metadata
success does not validate unread pixels, later HDUs or unread gzip trailers.
Plain primary headers retain legacy tolerance, including missing END at the old
bounded header cap; review caught and regression tests cover that compatibility.

Final full validation: **729 tests passed, 1 POSIX-only skip in 200.00 seconds**.
The earlier implementation suite also passed **728 tests, 1 skip in 190.86
seconds** before adding the independent browser-fixture decoder test. New
coverage includes gzip/tiled/wrapped-tiled metadata, primary/extension precedence,
long strings, preserved bytes/hashes, duplicates, calibration anchors, compound
collisions, refusal/rollback, cancellation, bounds, reindex and health discovery.

**15/15 packaged browser checks passed** on a freshly compiled private executable.
The new real file-picker workflow imports .fits.gz, .fits.fz and .fz.gz, compares
stored bytes with synthetic originals, verifies metadata/session membership,
repeats a duplicate, reindexes and selects the Library session. The standard-
library browser fixtures are independently pixel-decoded with Astropy in source
tests. The harness verified its private archive before any uploads and cleaned
only its own processes and files.

The first browser attempt failed visibly because the Python 3.12 build environment
lacks Playwright. The final gate used the existing system Python/Playwright runner;
no dependencies were installed or changed. ENVIRONMENT.md records this separation.
JavaScript/DOM/syntax/encoding/core guards pass (46 modules; the two existing
skymap advisory names), all 46 build dependency pins verify, and the source-only
release guard reports zero errors.

Private build: **0.1.0-gd941c9ff-dirty**, 37,142,363 bytes. Its --version and all
61 copied application source files were verified against the current checkout;
SHA-256/source manifest and evidence are retained under
logs/fits-compression-research-20261015/private-build/. Compilation scratch was
removed after success. The installed dist/NightSeam.exe, its version bake/backups,
VERSION, tags, staged release ZIP and source/packaged user stores were unchanged.
No actual user exposure or configured archive was read, converted or reindexed.
Source changes remain uncommitted on codex/compressed-fits-metadata.
Final housekeeping and Git whitespace checks pass; disposable caches were
removed without cleanup failures and research/build evidence remains preserved.

## FITS compression research - October 15, 2026

Added docs/fits-compression-research.md with primary-source references, codec and
preservation comparisons, quantization analysis, repository constraints and a
proposed implementation sequence. No compression feature or application change
was implemented. Actual source/packaged data and configured archives were not
read or converted.

A memory-only synthetic probe used Astropy 7.2.2/NumPy 2.5.4 on the existing
Python 3.12 build environment: 22 image/method cases with three timed repetitions
each, plus two float special-value probes. All completed without exceptions or
warnings. Integer codecs preserved tested pixels; whole gzip restored original
FITS bytes. GZIP_1/GZIP_2 with q=0 preserved the finite float32 samples, and
GZIP_2/q=0 preserved the tested float32/64 special-value bits. Quantized Rice
cases changed pixels as expected. Rice/q=0 unexpectedly truncated float fractions
(RMS error 0.5772), confirmed by installed-source inspection and a small independent
reproduction; the integration hazard is recorded in BUGS.md.

Evidence is retained under logs/fits-compression-research-20261015/. These are
synthetic Astropy measurements, not real-camera benchmarks or independent
fpack/CFITSIO acceptance. No full product test rerun, build or packaged browser
gate was performed for this documentation-only research.

The saved evidence passed explicit consistency checks for all 22 cases and the
expected preservation/loss outcomes. Encoding and Git whitespace guards pass.
Housekeeping completed with zero failures and preserved the research evidence.

## Workspace exploration and source validation - October 15, 2026

Inspected an initially clean main at d941c9ffa8f7c1269a45ee50ef42704d39eeb77d,
matching the local origin/main reference. Origin is configured for the independent
GitHub catalog repository; no fetch or publication was performed. VERSION remains
0.1.0. Current code implements Library, Import Exposures, Sky Map and Archive
Health, with legacy retained-file recovery available only through maintenance.

Fresh validation: **683 passed, 1 POSIX-only skip in 186.73 seconds** using the
existing .venv-build Python 3.12 environment outside the restricted sandbox.
The sandbox run reported **656 passed, 27 failed, 1 skipped in 175.20 seconds**;
an isolated probe reproduced WinError 5 during strict archive path resolution.
The default Python 3.14 interpreter lacks py_mini_racer; its exploratory suite
was interrupted after a focused test confirmed the missing dependency. The same
focused test passed in Python 3.12. See BUGS.md for these environment constraints.

JavaScript import/DOM/syntax, encoding and core-bake guards pass (46 modules,
the two existing skymap browser-global advisories). Build dependency validation
passes all **46 exact pins**. The source-only release guard with --allow-dirty
--source-only --no-stage reports **zero errors**.

No application build or packaged browser gate was run. The existing executable
and ignored version bake retain 0.1.0-gc2238303-dirty; the staged 0.1.0 ZIP remains
the October 2 artifact. October 14 packaged-browser evidence is historical, not
a new run. Application code, source/packaged data and configured archives were
unchanged; only these validation and issue records were updated.

Housekeeping removed 4.98 MiB of disposable caches with zero failures. The
interrupted exploratory run's private scratch was removed after verifying its
exact contained path and inactive owner. Final encoding and Git whitespace
checks pass; Git changes are limited to BUGS.md and RELEASES.md.

## Preview, session expansion and Trash retired - October 14, 2026

Removed exposure previews/thumbnails, inline session expansion and Trash from
the application. Session clicks and Enter/Space now select an ordinary row and
update its facts/calibration actions. Folder access and exact session membership
remain intact. Removed the feature JS modules, modals, associated CSS, pixel
renderer/compatibility helpers and preview/trash application API routes. No new
trash entries can be created.

Existing image_trash records and retained archive payloads/manifests are preserved.
Recovery-only startup reconciliation and scan exclusions remain for old data.
Explicit listing/restoration is available through scripts/restore_legacy_trash.py
with a required existing --database path; restoration keeps original metadata,
IDs and integrity baselines and refuses conflicts. No actual retained user file
was restored or discarded during this removal. Current documentation describes
the simplified interface and the separate maintenance recovery path.

Validation: **75 focused tests passed with 1 POSIX-only skip in 19.31 seconds**.
The final full suite passed **683 tests with 1 POSIX-only skip in 162.26 seconds**.
Retired renderer/grouping/action tests were removed and replaced with legacy
recovery and retired-endpoint checks. **14/14 packaged browser checks passed** on
the newly compiled executable. New selection coverage verifies absent controls,
no extra rows on repeated clicks, keyboard/focus operation, correct calibration
anchors and no preview/trash requests. Layouts were inspected at 480px and 1440px.
JS/DOM/encoding/core checks pass (46 modules; the two existing skymap advisory
names remain); source/artifact validation reports zero release errors.

The user closed the running application before replacement. The main executable
was rebuilt as **NightSeam 0.1.0-gc2238303-dirty**, with a verified prior backup,
three retained recovery executables and automatic build scratch cleanup. Evidence
is retained under logs/remove-preview-trash-20261014/. Source changes remain local
and uncommitted on codex/preview-stretch-ui. VERSION, tags, public release packages
and configured archive data are unchanged.

## Recoverable exposure trash and main executable update - October 13, 2026

Added a separate Move to trash action beneath exposure thumbnails, a filename
confirmation and a toolbar Trash view with Restore. Files remain inside the
configured archive. Durable pending intents, versioned recovery manifests and
atomic no-overwrite moves preserve originals across failed/interrupted operations.
Restore reinstates original catalog IDs, classification, capture/session metadata
and exact integrity baselines. Changed originals retain their prior baseline;
the retained payload hash is a separate verification value. Occupied destinations,
shared/hard-linked files and known active duplicate bytes are refused. There is
no permanent purge. Managed trash is excluded from active health/reindex scans.

Recovery writes use pinned Windows directory lineage or anchored Linux handles.
Startup recovery is nonblocking; unresolved operations keep their files/records,
show diagnostics in Trash and block archive-changing actions and health
acknowledgements. Trash listing remains readable during thumbnail/scan activity.
Thumbnail, grouping, autostretch and preview-backdrop improvements remain intact.
Quality scoring is still the documented investigation, not an implemented score.

Validation: **23 focused trash tests passed in 9.20 seconds**, including exact
roundtrip, database publication failures, cancellation, interruption/reopen,
occupied rollback/restore locations, metadata/payload tampering, parent replacement,
archive/root changes, dedup, scan exclusions and health-acknowledgement protection.
The affected trash/health/reindex run passed **157 tests with 1 POSIX-only skip**;
the final full suite passed **737 tests with 1 POSIX-only skip in 179.21 seconds**.
The earlier full run also passed all 737 tests (185.20 seconds).

The freshly compiled main executable passed **15/15 packaged browser checks**.
The new real private-archive workflow verifies Cancel, removal, restoration of
exact metadata/ID/FITS bytes, session totals, unchanged neighboring files and
focus restoration. Confirmation and Restore layouts were visually inspected.
Initial checks caught a startup-lock wait and a Trash-list/thumbnail lock conflict;
both were corrected. The new persistent browser fixture also required the solar
check to select its actual resolved catalog object explicitly. Final JS/DOM,
encoding/core, dependency and source/artifact gates pass (50 JS modules, zero
release errors; six reviewed advisory names including callback methods/globals).

After the user closed NightSeam, **dist/NightSeam.exe** was rebuilt as
**NightSeam 0.1.0-gc2238303-dirty**. Verified executable backups, retention of
three backups and build scratch cleanup succeeded. No actual user exposure was
trashed/restored during validation; all operation tests used private stores.
Evidence is retained under logs/archive-trash-20261013/. Changes remain local
on codex/preview-stretch-ui, uncommitted and unpushed. VERSION, tags and public
release packages are unchanged.

## Visible thumbnails and backdrop closing - October 13, 2026

Session exposure tiles now show small grayscale thumbnails with filenames below.
IntersectionObserver queues only visible tiles; one FITS read runs at a time,
with at most 64 decoded images retained. Eviction releases offscreen URLs, and
session rerenders release cached images and discard stale responses. The large
viewer pauses thumbnail loading and waits for its active bounded read. Thumbnail
errors leave the filename and large-preview action available. Clicking the
backdrop closes the viewer and restores tile focus; interior clicks keep it open.
Pointer presses that start inside do not dismiss it on a backdrop click.

Validation: **37 focused tests passed in 5.36 seconds**; the full source suite
passed **714 tests with 1 POSIX-only skip in 146.46 seconds**. The previously
recorded intermittent Windows rename test passed in this run. **14/14 packaged
browser checks passed** on the freshly compiled candidate, including serialized
thumbnail requests, stale-session responses, outside/inside clicks, keyboard
focus and a 150-exposure session proving visible-only loading and the 64-image
limit. Thumbnail/name layouts were visually inspected at 480px and 1440px.
JS/DOM/encoding/core and source release checks pass: 49 modules, zero release
errors. The advisory name checker reports four reviewed browser globals
(IntersectionObserver, queueMicrotask, and the two existing skymap names).

A synthetic 4096x4096 uncompressed FITS benchmark produced a 158x158, 23,285-byte
thumbnail in 45/16/15.5 ms over three runs; the 1024px preview took
187/173.5/178.4 ms. These are local synthetic measurements, not a real-archive or
compressed-file performance guarantee.

The build script correctly refused to replace the running application. A separate
candidate was compiled using the same locked interpreter, PyInstaller hooks and
resources, tested with private stores and copied with matching SHA-256 to
**dist/NightSeam-preview-update.exe**. Its version is
**NightSeam 0.1.0-gc2238303-dirty**. The running dist/NightSeam.exe remains intact;
the adjacent update uses the same executable-adjacent data directory when launched
after the current app is closed. Evidence is retained under
logs/thumbnail-preview-20261013/. VERSION, tags, public releases, source data and
user archives are unchanged; changes remain uncommitted and unpushed.

The requested quality/deletion investigation is in
docs/exposure-quality-feasibility.md. Advisory scores from comparable original
light frames are feasible; detecting cloud/foliage causes needs real-data
validation. Safe in-app deletion needs a dedicated reversible trash/restore
operation, recovery records and scan integration. Neither scoring nor deletion
is implemented by this UI update.

## Inline session exposure tiles - October 13, 2026

Clicking a session now expands its exposure files inline. Filename-only square
tiles are sorted by original capture time and grouped into local 30-minute
intervals, with missing/invalid timestamps grouped last. Exact session image_ids
remain authoritative; calibration anchors do not replace capture timestamps.
Repeated daylight-saving half-hours remain distinct, and fractional seconds are
preserved for ordering. Clicking a tile opens the selected exposure in a modal;
expanding a session does not request image pixels. The inspector preview,
exposure dropdown and Previous/Next controls are removed. Escape/Close restore
tile focus, and retry, cancellation and stale-response handling remain covered.
The preceding autostretch corrections are retained.

Focused preview/grouping checks: **37 passed**. Final freshly compiled desktop
browser gate: **14/14 passed**, including keyboard operation, exact membership,
time buckets, no automatic preview requests, retry/focus, cancellation and square
tile layouts at 480px and 1440px. Screenshots were visually inspected at both
widths. The initial browser run caught a retry focus defect, which was fixed
before the final build and gate.

The final full source suite finished with **713 passed, 1 failed, 1 POSIX-only
skip in 154.63 seconds**. The unchanged whole-folder rename publication test
again encountered WinError 5 in private test scratch; its isolated rerun passed
in 0.89 seconds. BUGS.md retains this unresolved environment finding; no clean
whole-suite result is claimed. JS/DOM/encoding/core and source/artifact checks
passed (48 JS modules, two existing skymap advisory names, zero release errors).

Local executable: **NightSeam 0.1.0-gc2238303-dirty**, on the uncommitted
codex/preview-stretch-ui branch. Build backup verification and automatic scratch
cleanup succeeded. Evidence is preserved under logs/session-expansion-20261013/.
VERSION, tags, public releases and user data/archives are unchanged; no commit
or push is part of this local build.

## Autostretch and immediate exposure previews - October 13, 2026

Replaced the fixed percentile/asinh display with median/MAD sky estimation,
2.8-sigma shadow clipping and a midtones transfer targeting a 0.25 background.
The brightest sample remains the white endpoint. This avoids spreading sky noise
across most of the display and clipping the entire upper half-percent of pixels.
Constant/zero-noise frames retain explicit fallback behavior. Small 2x2 averages
prevent an even downsampling stride from selecting only one Bayer phase; sampling
is reduced when necessary to retain the 64 MiB read limit at larger preview sizes.
Original FITS bytes and catalog integrity baselines remain unchanged.

Session/exposure selection now loads its preview automatically, initially
preferring a Light. Previous/Next controls avoid repeatedly opening the file
selector, and the image opens an expanded viewer that supports arrow-key browsing
without closing. Stable image stages avoid layout jumps while loading, the viewer
fits the available area, and disabled endpoint controls hand focus to an enabled
control. Refresh/retry remains explicit. The UI requests up to 1024 pixels with
bounded reduction for large exposures; this remains grayscale, not full-resolution
processing or debayering.

Focused rendering/IO validation: **36 passed in 5.44 seconds**, including realistic
noisy-sky/faint-signal fixtures, sparse hot pixels, ADU scaling, Bayer phases,
preview-size consistency and measured read-budget limits. A synthetic before/after
comparison is retained under logs/preview-stretch-20261013/. Browser fixtures now
include real FITS pixels so automatic previews exercise the decoder rather than
produce expected errors from header-only captures.

The standalone full suite finished with **712 passed, 1 failed, 1 POSIX-only
skip in 169.61 seconds**. The failure was the unchanged whole-directory rename
publication test with transient WinError 5; its isolated rerun passed. Two other
archive tests failed only in the initial overlapping source/browser run and also
passed in isolation. This environment limitation is recorded in BUGS.md rather
than claiming a clean whole-suite result. Preview/rendering tests remain passing.

Final local build: **NightSeam 0.1.0-gc2238303-dirty**, reflecting the local
uncommitted preview fixes. The build verified its prior executable backup, retained
three backups and removed build scratch. **14/14 packaged browser checks passed**
after correcting disabled-endpoint focus and replacing header-only browser fixtures
with real image data. The viewer was visually inspected at 1440px and layout checks
also cover 480px. A private frozen-app smoke decoded plain and RICE images and
verified archive scans/acknowledgements; source/artifact, JS/DOM/encoding/core and
dependency gates passed. Two existing advisory names remain in skymap.js.

The first build attempt correctly refused to replace the user's running app; it
continued only after the user closed NightSeam. An approval-review usage limit
briefly prevented a later test command from running; the explicitly resumed review
succeeded. Evidence is preserved in logs/preview-stretch-20261013/. VERSION, tags,
public release packages and user data/archives are unchanged; no commit or push is
part of this local fix.

## Local main merge - October 13, 2026

Merged codex/partial-review-fixes with main's archive health acknowledgements and
FITS previews. Conflict resolution retains transactional ingest/reindex, original
integrity baselines, strict capture timestamps, solar session membership and
exact preview exposure selection. The Windows build lock now covers all 46 pins,
including Astropy 7.2.2, NumPy 2.5.4 and their active dependency closure; build
preflight remains read-only and retains the Astropy hooks and MiniRacer collection.

Native checks exposed a Python 3.12 Windows stat/fstat ctime mismatch after file
edits. Archive/preview readers now compare ctime only within the same metadata
source. New regressions verify a completed edit remains previewable and that a
mutation during hashing is refused even with restored size and modification time.

Validation: **708 passed, 1 POSIX-only skip in 161.38 seconds** using the locked
Windows build interpreter with native filesystem access. Initial sandbox runs
could not resolve private archive directories and failed visibly; elevated
archive/preview checks passed (64 passed, 1 POSIX-only skip) before the complete
suite. JS/DOM/encoding/core checks pass for 47 modules with two existing advisory
names. All 46 dependency pins and source release checks pass. Source release
validation uses --no-stage because this local merge does not restage an existing
public release. Evidence: logs/merge-main-20261013/.

The merged implementation was compiled from clean source 9827be22 as a local
**NightSeam 0.1.0-g9827be22** development executable. The prior executable was
backed up and verified, three recovery executables were retained and build scratch
was removed. **14/14 Windows packaged browser checks passed**, including solar
sessions, archive health, acknowledgements, previews, import/dedup, export and
responsive layouts. The gate verifies its private archive before uploads.

A separate private frozen-app smoke decoded plain primary and RICE-compressed
extension images, verified two exposures, detected an edited and a missing file,
confirmed both acknowledgement actions and then verified the remaining exposure.
No source/packaged application data or profile archive was used. Browser startup
initially failed visibly because the build interpreter lacked Playwright; a
separate Playwright 1.62.0/Pillow 12.3.0 test environment completed the gate. Its
third-party JS initially tripped the encoding guard at the checkout root; the
environment and browser artifacts were relocated under ignored validation logs
before the final guard. Existing public release packages were not restaged,
VERSION/tags are unchanged and no remote push was performed.

## Archive health and grayscale FITS previews - October 13, 2026

Added Quick metadata and Deep whole-file SHA-256 archive health scans, background
progress, explicit server cancellation and downloadable JSON reports. The
additive image_integrity table preserves import-time/historical/reindex baselines
independently of mutable dedup caches. Findings distinguish missing/unreadable
files, unavailable/replaced archives, unsafe links, metadata/content changes,
unstable files, unindexed exposures and missing baselines. Reports retain 10,000
findings with omitted counts and show 200. Scans never repair or mutate files/DB.

Completed findings support confirmed Acknowledge deletion and Accept current
contents actions. Archive identity, the exact catalog/baseline snapshot and
current disk state are revalidated; acceptance rehashes the exposure against the
scan's observed digest. Database changes and timestamped previous/new hash history
commit atomically. Resolved findings remain labelled; subsequent scans use the
updated catalog. Restored/further edited files and stale findings are refused.
Exposure contents, object definitions and import history remain unchanged.

The session inspector now provides on-demand grayscale previews, exact exposure
selection, retry/refresh and an expanded viewer with keyboard focus restoration.
Astropy handles integer/float/scaled/blank pixels, image extensions, cube first
planes and tile-compressed HDUs. Finite samples use percentile/asinh stretching;
Pillow encodes grayscale PNGs. Whole-array loading is avoided, file reads and
compressed tile buffers are capped, path identities are checked and archive
conflicts fail promptly. Imports retain their original primary-header parser.
Runtime dependencies/build checks and the Astropy PyInstaller hook are included.
Early app shutdown interrupts health/acknowledgement/preview reads before Uvicorn
drains active requests. UI generations protect against stale responses and URLs
are revoked. No debayering, calibration, color combination or WCS rotation is done.

Fresh Linux validation of restored source: **577 passed, 2 expected skips** in
124.29 s; all **11 catalog browser checks passed**, including stale cancellation
and reopen responses, confirmation cancellation, hostile filenames, preview
selection/retry, expanded-view focus and narrow layouts. A private live archive
rendered three grayscale previews, resolved one missing and one edited exposure
through the UI, verified both remaining exposures in later scans, and left
exposure bytes unchanged. Scans/previews left the database unchanged; explicit
acknowledgements alone updated it. A fresh frozen Linux decoder rendered plain
and RICE-compressed images. JS/DOM/encoding/core, dependency and source release
checks passed. Evidence is retained in logs/recovery/.

Native Windows packaging/browser acceptance remains open in BUGS.md. VERSION,
tags and public executable/release artifacts are unchanged.

## Solar night/day sessions - October 11, 2026

The user-selected session policy replaces twelve-hour capture gaps: each object's
night runs sunset to next sunrise, and its day runs sunrise to sunset at the
capture location. Exact UTC intervals and site identity make grouping and new
folder names independent of upload order, host timezone and long winter gaps.
FITS coordinates take priority over the new observing-location fallback. Missing
location remains visibly Unassigned location; unreliable capture time remains
Undated. DATE-OBS preserves fractions and explicit offsets; DATE is not a capture
fallback and ambiguous dates/unsupported explicit time scales are not guessed.

Explicit calibration keeps its own capture instant/site and the chosen logical
target. Transactional reindex backfills solar metadata without moving files and
keeps calibration with its target when previously missing location is supplied.
Integration fixes cover the removed calibration date-helper call, atomic settings
requests, invalid unrelated legacy anchor dates and calibration backfill. Duplicates
retain their original records, classes and associations.

core/solar_sessions.js uses existing shared astronomy functions, with extrema
refinement for brief grazing phases. app/solar.py executes its generated bake in
embedded mini-racer 0.14.1; no Node runtime or duplicate Python astronomy was added.
The build closure now has 41 verified pins. Original MiniRacer/V8/ICU notices are
included in NOTICE and the executable. Authorized core regeneration produced hash
2cd86208d501eff7, token 48, retaining b7bcc0f836fe6da0. Existing astronomy reference
fixtures are unchanged.

Final full suite: **637 passed in 152.84 seconds** on the locked build interpreter
after the final application and test-isolation changes. Focused archive/database checks passed
**122 tests**; the source JS/import/DOM/encoding/core guards pass, with the same two
existing advisory names. The source release guard reports **zero errors**.
An earlier full run exposed an omitted core-guard export registration; it was fixed
and independently rerun. Reproduced settings/reindex regressions pass after repair.

Read-only inspection classified all **4,354** provided Seestar headers into **five
nights**. All contain observing coordinates. A private-copy import/reindex of
12 representative files preserved complete records and IDs, with zero additions,
repairs, failures or conflicts on reindex; original sample sizes/mtimes were
unchanged. These samples contain lights/stacks, so calibration uses private
synthetic regression data. No actual source/packaged library was reindexed.

The development candidate compiled and initially passed **11/11** browser checks,
including real uploads using embedded solar calculations. Review of a subsequent
gate failure exposed two test-setup errors: input was filled before initial state
had loaded, and a private database still used the default Documents archive.
Exactly three synthetic FITS files were verified byte-for-byte and removed; all
unrelated files and archive roots were preserved. The harness now sets and verifies
a private archive before any check, fails closed on a mismatch and waits for boot
before filling the observing form. All **9** harness tests pass, including private
archive acceptance/refusal. Cleanup evidence is in profile-test-cleanup.json.
The corrected private-archive browser run passes **11/11**, including calibration
backfill with a different original capture date and site.
The final clean source revision is rebuilt for handoff; its strengthened packaged
check also exercises calibration location backfill. Final build/browser/artifact outcomes and
commit identity are retained in logs/solar-sessions-20261011/final-*.log and
reviews/solar-session-implementation-20261011.md. Candidate housekeeping removed
74.23 MiB with zero failures and retained three executable backups. VERSION, tags,
user data, external archives and D:/seestar originals are preserved. No public
release or website deployment is implied.

## Final full-byte deduplication follow-up - October 11, 2026

A final review reproduced another cache edge case: an externally replaced
original with unchanged size and modification time could be skipped as a
candidate because its cached digest differed from newly imported/discovered
bytes. Reindex and upload now compare current complete bytes of same-size
originals. Reindex hashes an original at most once per plan; known-only scans
retain their caching optimization. New-content comparisons can require more I/O
to preserve complete-byte identity rather than trusting negative cache hints.

Both private regressions pass, including the single-new-digest reindex case.
Focused archive/session tests: **113 passed**. Final full suite on the locked
build interpreter: **574 passed in 141.00 seconds**, after these last code changes.
Evidence: logs/partial-review-fixes-20261011/full-tests-final.log. The final source
revision is built and checked with private packaged data for handoff; final
build/browser/artifact evidence is retained in final-*.log and the implementation
report alongside the earlier successful packaged verification below.

## Packaged verification of partial-review implementation - October 11, 2026

Built clean source commit cad47686 as **NightSeam 0.1.0-gcad47686**, using the
existing locked Windows x64 CPython 3.12 build environment. Dependency preflight,
source guards and compilation passed; no dependencies were installed. The
previous executable was backed up and verified before replacement. Automatic
housekeeping removed **50.28 MiB** with zero failures and retained three recovery
executables.

The packaged private-data browser gate passed **10/10**, including the new
partial-import response/chunk-stop regression. The local artifact guard reported
**zero errors**. Evidence: logs/partial-review-fixes-20261011/build-cad4768.log,
browser-cad4768.log and release-cad4768.log. User data and external archives were
preserved; VERSION, tags and public release artifacts were unchanged.

This final documentation revision is rebuilt for handoff, with build/browser/
artifact results recorded in final-*.log in the same ignored evidence directory
and in reviews/partial-review-fixes-20261011.md. No public release is implied.

## Verified partial-review implementation - October 11, 2026

Implemented the substantiated issues in reviews/partial-review-verification-20261011.md:

- Renames refuse missing files, mismatched stored roots and logical groups spanning
  multiple physical roots before moving anything. Database path updates use only
  proven move mappings, never guessed destinations.
- Reindex plans before one atomic database publication, preserves IDs and manual
  metadata when a unique trusted full-byte fingerprint identifies a missing file,
  reports ambiguous repairs without guessing, and cooperatively cancels before
  publication. Connection reads remain available while scanning/hashing. Existing
  duplicate rows and missing records without trustworthy fingerprints need manual
  recovery; reindex never deletes them.
- Import session lookup uses the same normalized unresolved/resolved identity as
  the library. The documented twelve-hour gap policy remains; historical folders
  and backdated imports are not automatically relocated.
- Database opening preserves validated, dated snapshots before migration and
  after opening, retaining five of each plus the rolling backup. Snapshot failures
  stop initialization instead of replacing recovery evidence.
- Upload responses preserve confirmed earlier results, isolate validation errors,
  stop on database failure, distinguish committed-but-unconfirmed outcomes and
  files not attempted, and stop subsequent browser chunks. Import-history errors
  roll back their transaction so following operations can proceed.
- CSV text uses quoted, tab-prefixed formula neutralization; numeric columns and
  lossless JSON/database values are preserved. CSP and production host checks
  permit only current local dependencies; testserver is explicit test setup.
- Build dependencies have a complete 40-pin Windows x64 CPython 3.12 closure and
  a read-only preflight. Builds no longer install packages automatically. Cleanup
  tests own private scratch and preserve unrelated evidence. Current remote,
  recovery, timestamp and module documentation was corrected.

Validation with the locked build interpreter: **572 passed in 133.59 seconds**.
The first full-suite attempt exposed an existing missing Path import in a test
annotation under Python 3.12; it was corrected before the passing rerun. Focused
archive/session, database recovery, build/cleanup and API regressions also passed.
check_js.py passed for 42 modules with zero syntax errors/broken imports; DOM,
encoding and core gates passed, with two existing advisory names. The dependency
preflight verified all 40 pins; source release checks reported zero errors and
git diff --check passed. Evidence: logs/partial-review-fixes-20261011/.

No core formulas, reference fixtures, generated core bakes, VERSION, tags or
public release artifacts changed. Packaged verification is recorded separately
after compiling this implementation.

## Packaged verification of audit fixes - October 11, 2026

Built the verified clean source commit 9ecf9435 as a local development executable
reporting **NightSeam 0.1.0-g9ecf9435**. The first Windows PowerShell subprocess
could not load Get-FileHash and stopped during backup verification before
compilation; the previous executable remained intact. Retrying build.ps1 via
the installed PowerShell 7 (`pwsh -NoProfile -ExecutionPolicy Bypass -File
build.ps1 -AllowDirty`) succeeded, with an executable backup before replacement.
No dependencies were installed.

Packaged private-data browser gate: **10/10 passed** with elevated execution,
including export, launch, archive errors, import/dedup, responsive layout and
map checks. The local artifact guard (`check_release.py --allow-dirty --no-stage`)
reported **0 errors**. Browser scratch was removed, version-probe scratch was
empty and removed, and the malformed root Windows cache did not recur.
Successful build housekeeping removed **73.17 MiB**, with no cleanup failures,
and retained three recovery executables. Application data, configured external
archives and existing release artifacts were preserved.

These outcomes are in logs/fix-review-20261011/build-9ecf943-pwsh.log,
browser-9ecf943.log and release-9ecf943.log; the failed attempt is preserved as
build-9ecf943.log. The final documentation-only revision is rebuilt for handoff;
its artifact identity and repeated packaged gate evidence are kept in final-*.log
in that same ignored evidence directory. This is local development validation:
VERSION, tags and public release artifacts were not changed.

## Verified audit follow-up - October 11, 2026

Closed the five findings in reviews/agent-changes-review-20261011.md:

- R-01: whole-folder renames enumerate before moving and retain an independent
  directory undo record. Undo restores empty folders and unrecorded children,
  and refuses to overwrite an occupied original directory. A failed forward
  move never undoes a merely planned map.
- R-02: both filesystem and database failures report incomplete undo explicitly.
  Logs preserve the original exception and identify unrestored relative paths.
  Recovery guidance requires inspecting/preserving files and resolving the
  filesystem problem before Reindex folders reconciles the library.
- R-05: moving a folder shared by another logical group returns HTTP 409 before
  changing files or rows, including resolved objects and Windows case aliases.
  Same-folder edits and legacy spellings within one logical group remain valid.
  Merge writes validate source and final destination confinement.
- R-03: scoring parity validates nonempty cases, input/output cardinality and
  finite required numeric fields in expected, batch and split results. Copied-core
  mutations now reject empty/truncated output and NaN/Infinity/missing scores.
  Golden fixtures, formulas and existing numerical tolerances were unchanged.
- R-04: the core builder validates current/previous hash shape and positive
  integer tokens without coercion before pruning or writing. Private-copy
  regressions prove invalid manifests leave bytes, timestamps and directories
  unchanged, while first bake and legitimate retention still work.

Removed unreachable middleware and the unused rename journal. Corrected current
design/boot documentation, the authored-core versus bake validation description,
and the prior NUL-path result. BUGS.md records the remaining filesystem refusal
and manual hardware/accessibility acceptance limits.

Validation: full suite **512 passed, one existing Starlette/httpx warning** in
114.21 s. Focused selections passed (26 archive/API, 17 scoring, 37 manifest;
these overlap the full suite). check_js.py passed: 42 modules, no broken imports
or syntax errors, DOM/encoding/core gates passed, two existing advisory names.
git diff --check passed. Evidence: logs/fix-review-20261011/. No version, tag or
public release was created. Packaged validation is recorded separately.

## F-21 core comment sweep with authorized rebake - October 11, 2026

The F-21 stale-reference sweep was executed against the AUTHORED core modules
(the contract-gated rebake this finding required). 21 present-tense references
to the retired website build, webstore helpers and deleted shell modules were
rewritten to their desktop-only truths across astro.js, catalog.js, format.js,
horizon.js, plannerstate.js, planning.js, scoring.js and visibility.js; two
past-tense lineage notes were deliberately KEPT (accurate history, per the
sweep discipline). Comment-only: zero behavior change.

Authorized rebake via scripts/build_core.py: new bake hash
11488fc2bbbb7ddb (manifest token 44 -> 45, register updated in DECISIONS.md),
stub regenerated, N-1 retention kept the prior bake. check_core.py, check_js.py,
check_encoding.py, check_dom_ids.py and the source release guard all passed;
full suite **446 passed, one existing Starlette/httpx warning** in 99.04 s
including the scoring-parity and D1 drivers (which import authored `core/`;
`check_core.py` separately verifies the generated bake matches it). The
stale-reference class (F-21) is closed.

## F-15 length caps on the remaining write fields - October 11, 2026

- /api/library/edit: object names are ARCHIVE FOLDER names (obj_slug strips
  unsafe characters but has no length bound). The server now rejects
  name/new_name over 120 characters - the UI's maxlength=80 is the real
  contract and the stored object_name column is capped at 120 at ingest.
- /api/settings: an image_dir over 1024 characters is REJECTED, never
  truncated - a silently cut path would name a different folder.
- /api/library/open_folder: a rel over 512 characters is refused before it
  reaches the filesystem.

Validation: full suite **446 passed, one existing Starlette/httpx warning** in
87.85 s (one new caps test); encoding, core, JS and source-release gates all
passed. Logs under logs/round3-*. No version, tag or release artifact.

## Scoring parity and D1 drivers revived - October 11, 2026

- F-19: the removed scoring-characterization driver came back as
  tests/js/scoring_parity.mjs + tests/test_scoring_parity.py. The batched
  engine is proven bit-identical to the frozen characterization goldens
  (tests/fixtures/scoring_goldens.json: 697 objects x 2 sites, inputs baked
  into the fixture) on every displayed field and the transit index, with only
  the existing precess-once drift tolerance (<=2e-5) on the continuous
  score doubles; a mutation proof on a COPY of core/scoring.js confirms a
  perturbed coefficient fails the parity. The orphaned D1 driver
  (tests/js/core_d1.mjs) is wired too: the world-azimuth bin contract runs
  against core/horizon.js. Missing node is a HARD failure, never a skip.
  The goldens stay FROZEN - nothing regenerates them.

Validation: full suite **445 passed, one existing Starlette/httpx warning** in
91.13 s (three new parity/D1 tests); check_core.py, check_encoding.py,
check_js.py and the source release guard all passed. Logs under logs/round2-*.
No version, tag or release artifact was created.

## Server hardening round - October 11, 2026

- F-09: middleware ORDER fixed - guard_host is now registered last (outermost),
  so rejected foreign-host/cross-site requests never reach the idle-touch and
  constant probing cannot keep the 12-hour idle exit from firing. Pinned by
  test_rejected_requests_do_not_advance_the_idle_timer (403s leave the timer
  untouched; an allowed request still advances it).
- F-13: /api/library/open_folder goes through the shared safe_join rail -
  control characters and device names are a clean 400 while the archive root
  stays openable via ".". The prior raw-NUL probe returned 404 on the verified
  Python 3.14 environment; the earlier claim of a raw 500 was incorrect.
- F-16: every response carries X-Content-Type-Options: nosniff, covering the
  non-HTML responses the CSP attachment deliberately skips.
- check_core contract alignment: the header claimed a stub-token check, but
  the generated stub carries the content HASH and no token at all - the claim
  and the dead manifest-token read are removed (the token stays a historical
  per-bake register counter, documented in DECISIONS.md).

Validation: full suite **442 passed, one existing Starlette/httpx warning** in
90.12 s (two new hardening tests); check_core.py, check_encoding.py,
check_js.py and the source release guard all passed. Logs under logs/round1-*.
No version, tag or release artifact was created.

## Rename recovery and hardening batch - October 10, 2026

Follow-up hardening on the retired-planner tree (0ca1b2c era work):

- F-22: archive folder renames now JOURNAL every completed move and UNDO on a
  mid-merge failure; a database failure after the move undoes the disk move in
  the route. The undo reports (never swallows) files it could not restore, and
  Reindex folders remains the recovery path. Found and fixed along the way: the
  plain-rename branch violated the documented {old: new} map contract (it
  recorded the new path as the key) - masked for years because
  db.rename_images derived same-slug paths as a fallback.
- F-11: seed_catalog, apply_popularity, migrate_legacy_object_ids and
  rename_images wrap their multi-statement writes in rollback-on-exception, so
  a mid-sequence failure can no longer be published by the next unrelated
  commit.
- F-17: style.css no longer duplicates the Workbench role tokens (the drifted
  :root copies are gone; only fonts and the pre-theme color scheme stay) and
  the dead .library-hero rules (with the only viewport-proportional font size)
  are removed.
- F-20: build_core.py refuses to bake over a corrupt manifest instead of
  silently resetting the token; bench_desktop_compute.mjs fails with a clear
  message instead of a missing-file traceback; make_icon.py writes only under
  a __main__ guard.
- F-23: util.js api() normalizes a malformed 2xx body into the handled error
  path instead of a bare SyntaxError.

Validation: full suite **440 passed, one existing Starlette/httpx warning** in
86.87 s (three new rename/undo tests); check_js.py, check_encoding.py,
check_core.py, check_dom_ids.py and the source release guard all passed. The
packaged browser gate ran **10/10** against a fresh build of the retirement
commit (0.1.0-g24dfeaef era) after one real catch-and-fix: the browser gate
caught a sky-map legend crash (state.js lost the disabledTypes field in the
retirement) that the pytest suite could not see. A further rebuild is needed
so the exe matches this batch. Logs under logs/assignment2-*. No version, tag
or public release was created.

## Planner retirement - October 10, 2026

The retained maintenance planner was retired (IMPLEMENTATION_PLAN item 6,
decision recorded in DECISIONS.md). Removed: static/index.html and the
planner-only JS modules; the /api/sites, /api/rigs, /api/panorama*,
/api/catalog(+provenance,+custom), /api/object/{id}/image, /api/images/cache/clear,
/api/export, /api/import, /api/update-check and /api/shutdown route families;
the image prefetch thread and app/images.py (with its third-party image fetch
and TLS exception); app/backup.py (setup-ZIP export/import - its only caller
was the planner UI); app/rig_presets.py; the --planner launch flag. New
databases no longer create the planner tables; existing databases keep them
unread (additive-migration policy) and legacy custom targets still resolve.
Recovery is now the archive folder + the rolling nightseam.db.backup +
Reindex folders. README's "requires no network access" claim is now literally
true: the weather and object-image lookups retired with the planner.

Closed as code (not fixed): restore publication failure (NEW-01, High),
shutdown hard-exit stranding (F-08), worker horizon scoring (F-02/M-1),
CDS TLS bypass (F-05), dangling active pointers (F-12/NEW-03), two-commit
panorama creation (F-14).

Validation: full suite **437 passed, one existing Starlette/httpx warning** in
109.54 s (pre-retirement baseline 616; the catalog test surface is smaller by
design). check_js.py (31 modules, 0 broken imports), check_dom_ids.py,
check_core.py (core bake untouched - manifest unchanged), check_encoding.py
and the source release guard (--allow-dirty --source-only --no-stage) all
passed. Logs under logs/assignment2-*. No version, tag, build or release
artifact was created; the browser gate needs a fresh build and is deferred to
the next build session.

## Ingest event-loop offload and fixture output guard - October 10, 2026

Two verified findings were implemented (reviews/independent-audit-verification-20261010.md):

- NEW-02: the core-fixture harness now refuses incomplete runner output.
  `_section()` in tests/test_core_fixtures.py fails any existing section that
  is not a list or whose row count differs from the frozen fixture, so the
  seven oracle comparisons can no longer silently accept empty, truncated or
  overproduced sections through their non-strict zip loops. 28 parameterized
  regression tests drive each section's real comparison function against a
  copied runner result with an empty, truncated, additional and non-list
  section; the valid-output and missing-section pins are unchanged.
- F-06: `POST /api/images/ingest` no longer blocks the event loop while
  preparing ingestion: a single `build_ingest_catalog()` helper reads the
  catalog and constructs the resolver inside one `run_in_threadpool` worker,
  with the awaited call outside the archive-directory try/except so database
  and resolver errors cannot be mislabeled as directory-creation failures. A
  regression test invokes the real registered endpoint function with an empty
  upload and asserts both operations ran once, off the event-loop thread, on
  the same worker, with `advanced=True` and the exact catalog list.

Validation: focused core-fixture block 55 passed; ingest/imglib block 91
passed; check_js.py and check_encoding.py passed; full suite **645 passed,
one existing Starlette/httpx warning** in 158.56 seconds (previous baseline
616 plus 29 new tests). Logs under logs/assignment1-*. No version, tag or
release artifact was created; BUGS.md listed neither issue, so no entries
changed there.

## Workspace cleanup and prevention - October 10, 2026

Removed 2137.58 MiB of accumulated test scratch, caches, compilation output,
retired build candidates and excess executable backups in the initial cleanup,
with no removal failures. Subsequent housekeeping removed aborted validation
scratch/captures. Source/packaged data, external archive paths, the current
release, build environment, UI references and split recovery evidence remain.

Tests now use per-test data/archive directories, close owned SQLite connections
before removal and leave scratch working directories before teardown. Persistent
cleanup failures fail visibly. Pytest cache output is disabled. Browser tests
own frozen descendants through a private Windows Job Object, including after
launcher exit or harness crash; teardown verifies process/listener exit before
removing logs/browser scratch. JS syntax scratch is likewise owned under logs/
with strict cleanup. Frozen test extraction also uses private TEMP/TMP/TMPDIR
inside browser scratch. Housekeeping previews by default and refuses tracked or
linked paths, skips active/unknown owners and keeps three executable backups.
Successful builds clean their compilation scratch after releasing an owner lock.
Runtime logs already rotate with a 1 MiB limit and two backups.

Validation: final full suite **616 passed, one existing Starlette/httpx warning**
in 156.74 seconds; JS/encoding/core/import/DOM gates passed; development build
completed and automatic housekeeping passed; local release integrity check
with --allow-dirty --no-stage reported zero errors. Packaged browser gate
**10/10 passed** with elevated execution and left no private scratch/listener.
The sandboxed browser attempt reported 9/10 because its export download was
canceled; this was treated as failure and rerun with elevated browser execution.
Earlier strict teardown runs exposed open SQLite handles and a scratch current
directory; both were fixed before the passing final suite. Validation logs live
under logs/workspace-*. No version/tag or public release was created.
The concurrently created workspace audit was preserved in ignored
reviews/workspace-audit-20261010.md at the user's request. A malformed root
Windows cache directory was also removed; its unconfirmed origin is in BUGS.md.

## Separation verification - October 5, 2026

Confirmed separate Git roots/object stores, project-owned build/version/core
inputs and independent data paths. No active sibling runtime/build dependency
was found. The regular packaged executable reports NightSeam 0.1.0. The retained
maintenance planner remains catalog-owned and is deferred cleanup.

A headless browser-test child from the earlier private gate had survived parent
teardown. Its exact path, command, creation time and absent parent established
ownership; only that orphan was stopped. Recorded the tooling issue in BUGS.md
and the deferred roadmap. No application behavior or release artifacts changed;
the previously recorded full-suite and browser results remain applicable.


## Fresh validation and development setup - October 5, 2026

Full suite: 600 passed, no skips or failures (186.75 seconds). One upstream
Starlette/httpx deprecation warning remains; dependency maintenance is deferred.
The packaged browser gate passed 10/10 with isolated executable/data copies.
Before adding today's documentation, the strict clean-tag 0.1.0 release guard
passed with zero errors. Logs are under logs/repository-split/*-20261005.log.

All 649 prior source/packaged library fingerprints still match. The executable
SHA-256 remains bf5be98b021d2c7d6ab02758e079498c58bfda8985933c248ffebced3e6618f8;
the release ZIP remains ab6094850b02b5bdf82f79cd9aeda40978cfdba2a7710824623cdb7994b0f208.
No library migration, executable replacement or release-tag movement occurred.

main is established for independent development. Today's validation/backlog
notes are separate from the existing v0.1.0 release baseline; its feature branch
is preserved. IMPLEMENTATION_PLAN.md saves deferred status-report items 3, 4, 6
and 7, and RELEASE_WORKFLOW.md documents local releases and optional remote setup.
The user chose to keep the catalog local. No origin remote, GitHub repository
or public executable release was created.


## Repository separation — October 1, 2026

The current codex/direction-b-ui desktop snapshot was extracted to the
independent Nightseam-catalog repository with VERSION 0.1.0. The original
Nightseam-programm repository now owns only the website. The catalog keeps the
same branch name with independent history and no remote; prior combined Git
branches/tags and staged/working changes are saved in ignored recovery bundles
and patches. Desktop source removed from the website has both a committed
extraction snapshot and hash-verified pre-partition recovery copies.

Source data/, packaged dist/data/, the existing executable and build environment
were moved with byte verification. Configured external exposure archives were
not altered. Website catalog generation now uses project-owned neutral data
without an application database, retaining the exact committed payload bytes.
Core baking, guards, tests, browser registration and releases are scoped to
each project; website version history is unchanged. No remote push, upload or
public deployment was performed.

Validation completed October 2, 2026:
- Initial full catalog pytest run: 599 passed, one launch-mode test still
  expecting the former combined updater. Updated that test to require updater
  refusal in both shells; all 17 affected launch/updater/seed checks passed.
  The backend disables the obsolete updater, and neither shell initializes it.
- Focused split verification retained 132 local guard/core/geometry/launcher
  assertions, including published astronomy anchors and mutation proofs.
- All 10 independent packaged catalog browser checks passed on the candidate
  (0.1.0-gaa775e41-dirty), using private executable/data copies. Coverage includes
  calibration file choosers, active objects, archive errors, import counters,
  duplicate prevention, export, isolated launch, responsive layout and map labels.
- Local JS/import/DOM/syntax/encoding/core guards and source release gate passed.
  Local packaging checks reject private data, absent manifests, unsafe names,
  stale versions/checksums/sizes and ZIP members differing from verified source.
- All 649 source/packaged library file fingerprints remained unchanged through
  tests and browser validation; external archive settings were retained. Results
  and recovery material remain in ignored logs/repository-split/.
- VERSION and the final v0.1.0 tag own this repository's independent release.
  The final executable is compiled from that clean tag; final artifact hashes
  and package-gate results are stored in the ignored installation record.

## Historical combined-project entries

# RELEASES.md - what each release shipped and how it was verified

The per-release records moved out of `AGENTS.md` (which had grown past the
workspace instruction budget and was being truncated). `AGENTS.md` keeps the
release PROCESS; this file keeps the RESULT of each release: tag, staged
artifact hashes, gate results, what was deployed and how it was verified live.

Newest first.

## UNRELEASED - 2026-10-01 selected-session calibration actions

- Fixed inactive calibration actions for legacy `Object/lights` archives and
  previously indexed flat layouts. Darks, Flats and Bias now open the exposure
  picker with a recorded image anchor identifying the selected object and logical
  session. Recent-import actions use the corresponding recorded session too.
  The import pane shows the target and chosen frame type. Unavailable sessions
  explain why the action cannot proceed.
- Added validated, nullable `images.session_date_obs` through an additive
  migration. Automatic imports use the original capture date; explicit dated or
  undated calibration attachments carry a separate logical grouping date. Original
  `date_obs` and file bytes remain unchanged. Sessions publish exact image IDs;
  attached calibration no longer disappears from the inspector because its actual
  capture date is outside the selected light exposure's range. UTC comparisons
  preserve fractional timestamps and the exact 12-hour boundary. Existing folder
  naming conventions are retained.
- The server derives object/path/session from the existing anchor, validates
  positive SQLite-range IDs and calibration types before staging, and revalidates
  the target before publication. Existing modern folders are reused; legacy
  folders gain a canonical calibration subfolder without moving light exposures.
  Global whole-byte duplicate skips retain their existing association. Setup
  backup/restore carries and validates the association, rejecting malformed
  metadata before writes. This resolves UIB-033.
- Verified Open folder launches Windows Explorer in the current native app;
  added clear success/error feedback. Renamed the toolbar action to Import and
  replaced format jargon in labels, help and empty states with exposures. The
  supported `.fit`/`.fits` input filters remain unchanged.

Validation: **811 full-suite tests passed** (one upstream Starlette/httpx
deprecation warning); focused backend and grouping checks passed, and four source
browser checks passed, including genuine chooser events for all three calibration
types in both desktop shells and preserved membership for out-of-date metadata.
The native inspection ended when the user pressed Escape; no subsequent computer
input was issued. Compilation and final JavaScript, DOM, encoding, cache and core
guards passed. Development integrity passed with zero errors and the expected
dirty/off-tag warning. Candidate: `dist/session-actions-candidate/NightSeam.exe`,
version `0.6.34-10-gf5bb0c5-dirty`, SHA-256
`ceac0303b20551fc33b573ff4e4867fbf743aa34915b5127e1f594f1b101e143`.
The packaged/web browser gate passed **55/55 checks**. After the user closed the
app, installed the verified candidate atomically as `dist/NightSeam.exe`; its
hash matches the tested build. Library data remains intact. Previous executable:
`dist/NightSeam.before-session-actions-20261001-212434.exe`.
Changes are staged for review; no commit, tag or deployment was created.

## UNRELEASED - 2026-10-01 open Library header and faster indexing

- Removed the contextual object-export and format strip from both desktop
  Library workspaces. Angular-size symbols are inline, keyboard-accessible unit
  switches that persist the preference and stay synchronized with the planner.
  Sessions, images and light integration now use open text without framed cards.
  The packaged toolbar omits its logo and fits its controls in about 45 pixels;
  the source planner retains its existing masthead. Global Export library remains
  available as CSV, and existing API JSON/scoped export support remains intact.
- Map names prefer the closest clear placement. Crowded or displaced names have
  quiet leader lines back to their own marker. Session-count sizing, coordinates,
  selection, hit testing and high-DPI geometry are preserved.
- Reindex now builds one catalog matcher for all new files instead of rebuilding
  it for every frame; unchanged-file scans do not load it. Duplicate candidates
  prioritize the matching stored digest while still verifying the complete file.
  Spooled uploads copy and hash in one worker call, preserving size limits and
  waiting for an active copy to finish before cancellation cleanup.

Isolated profiling used 48 distinct 4-MiB FITS files (192 MiB total), the full
seeded catalog and temporary archives. The initial reindex baseline was 31.701 s,
including 29.937 s rebuilding the matcher 48 times; the updated run took 2.295 s
with one matcher. New imports measured 1.784 s before and 1.635 s after; identical
imports measured 2.555 s before and 1.258 s after. A separate baseline repeat
measured 38.636/1.821/2.366 s respectively, illustrating machine/load variability.
These are local workload measurements, not promised throughput for other disks.
The UI sends eight files per request; archive publication remains ordered.

Validation: **765 full-suite tests passed** (one upstream Starlette/httpx
deprecation warning), **168 focused ingest/API tests passed**, six source-browser
checks passed, and final JavaScript, DOM, encoding, cache and shared-core guards
passed, and **53/53 packaged/web browser checks passed**. The compiled candidate is
`dist/open-design-candidate/NightSeam.exe`,
version `0.6.34-10-gf5bb0c5-dirty`, SHA-256
`61390814c97a70377a461252379dd3350f92a6fdd878eff390967f2c63385521`.
After confirming the canonical app had closed, installed the verified candidate
atomically as `dist/NightSeam.exe`, preserving the library data and the old build
as `dist/NightSeam.before-open-design-20261001-204004.exe`. The installed hash
matches the tested candidate. Development integrity passed with zero errors and
the expected dirty/off-tag warning. Changes are staged for review; no commit,
tag, public release, merge or deployment was created.

## UNRELEASED - 2026-10-01 active-object Library and session-sized map

- Archive map marker radius now depends only on capture-session count, using
  validated `n_sessions` or the session-list length. The bounded 3-12 CSS-pixel
  square-root scale is independent of image count and exposure; unknown counts
  retain the minimum marker and display unavailable text. Names, selection,
  coordinate geometry, hit testing, and DPR-aware drawing remain consistent.
- Moved useful archive facts and controls into the top Library workspace header.
  They show the active object's name, type, angular size, sessions, images, and
  light-frame integration. Removed the duplicate all-object table/disclosures;
  object sorting remains labelled in the Objects rail.
- Added contextual CSV/JSON Export object with exact displayed-name filtering,
  scoped JSON totals, and 404 for unmatched names. The top Export library action
  remains global. Existing serializers and private-fingerprint exclusions remain
  shared rather than duplicated in the client.
- Removed obsolete dock grid rows and fixed the source planner's height chain.
  Both desktop Library workspaces fill the remaining client height, with bounded
  rail/table/detail scrolling and compact reflow. More window height reveals more
  session rows; rows retain their normal density. Standard bottom gutters replace
  the old unused region.

Validation: **761 full-suite tests passed** (one upstream Starlette/httpx
deprecation warning), **53/53 browser checks passed**, and the final JavaScript,
DOM, encoding, cache-token, and shared-core guards passed. Focused checks covered
API export scope/Unicode names, object switching, both desktop layouts, and map
counts at DPR 1 and 2. The folder regression now verifies painted clipping through
scrolling ancestors and unobstructed action hit targets, avoiding false overlap
reports from invisible content.

Rebuilt `dist/NightSeam.exe` in its existing folder, preserving library data and
`dist/NightSeam.before-active-library-20261001-195459.exe` as the previous-build
backup. Version: `0.6.34-10-gf5bb0c5-dirty`. SHA-256:
`0c1c040fd64fb2e47ffd23f67475c328a8b2e6ad0681e3b395d32aa23a995ef9`.
Development integrity (`check_release.py --allow-dirty --no-stage`) passed with
zero errors and the expected dirty/off-tag warning. Changes are staged for
review; no new commit, tag, merge, release bundle, or deployment was created.

## UNRELEASED - 2026-10-01 Library, reindex, and map corrections

- Removed the redundant bottom Library import panel from both desktop shells.
  The top import action and dedicated Import Exposures workflow remain available.
- Reindex preflights every FITS path before updating any index record or
  fingerprint. It accepts `<object>/<session>/{lights,darks,flats,bias}/file`
  and supported legacy `<object>/<frame-type>/file` layouts, including arbitrary
  supported session names and case-insensitive frame folders. Unexpected flat,
  unknown-frame, or excessively nested layouts return actionable HTTP 400.
  Empty folders and non-FITS files are ignored. Rejection preserves the entire
  existing index and archive bytes; it does not move or remove source files.
- Archive folder links use unique containing directories rather than treating
  filenames as folders. Long lists and detail content have bounded scrolling,
  leaving totals and actions reachable. Root-folder actions work, and calibration
  actions require a unique managed session. Reindex errors remain visible until
  dismissed or retried.
- Removed the map's dot-integration control text while retaining exposure-sized
  markers. Object names are on by default, avoid overlapping labels, stay within
  the plot, and remain visible for selected/hovered objects when general labels
  are off. Desktop and narrow screenshots were visually checked.

Validation: **160 focused ingest/API tests**, **757 full-suite tests** (one
upstream Starlette/httpx deprecation warning), and **50/50 browser checks passed**.
JavaScript, DOM, encoding, cache-token, and shared-core guards passed. The browser
gate used the newly built `dist/catalog-ui-fix/NightSeam.exe` development candidate
in isolated installs, including folder-error and crowded-map label regressions.
The version matches authoritative Git describe: `0.6.34-10-gf5bb0c5-dirty`.
The running installed executable and user library were left untouched while
building and validating this candidate. After NightSeam closed, the validated
candidate replaced `dist/NightSeam.exe` atomically; the previous executable is
retained as `dist/NightSeam.before-folder-map-fixes-20261001-185934.exe`.
Installed SHA-256: `b9a8ad4ae099191bb5ea6a41161271a58ba5fbb09fac34d99cf1bd657dd748b9`.
Library data was not changed by installation.
No new commit, tag, merge, or deployment was created.

## UNRELEASED - 2026-10-01 full-review implementation pass

The dated review is [REVIEW-FULL-0.6.34-10.md](reviews/current-pass/REVIEW-FULL-0.6.34-10.md).
This entry describes the subsequent implementation; it is not a tagged release
or deployment. Final integration validation passed on October 1.

- PY-02: duplicate candidates use indexed metadata, and blocking file hashing
  runs outside the event loop and long database critical sections. Final
  duplicate detection and publication retain concurrency protection. Rename,
  restore, archive configuration, panorama payload mutations and import history
  share that serialization boundary; no archive RLock spans an await. Restore
  journaling copies existing payloads so original paths remain available until
  commit, preserving the three-phase restore contract.
- PY-01/PY-04/PY-05: native placement uses physical monitor work areas, converts
  older saved logical positions, and applies final geometry before WinForms
  shows the window. Optional placement errors preserve the native frame and
  downloads. Tests centrally isolate placement files from the user profile.
- PY-06: one lazy Condition/deadline worker replaces per-event timer threads.
  It reads coherent native geometry/state after the event burst, rejects stale
  generations, and terminates on closing without blocking the GUI thread.
- FE-01/FE-02: the source planner has the shared Sky Map selection target, and
  the DOM guard no longer exempts that missing target.
- FE-03: source-planner archive picking uses the available native bridge; the
  native host publishes folder-picker capability for both desktop shells.
- PY-07/PY-08/PY-09: stale owned PID-bearing stages are swept conservatively,
  publication retries external filename collisions without overwriting files,
  fingerprint bookkeeping is excluded from library/setup exports, and repaired
  originals have their own status/count rather than inflating import history.
- FE-04/FE-05/FE-06/FE-08: DOM coverage follows transitive module edges, browser
  synchronization waits for observable state, native-chrome assertions inspect
  actual affordances, and map keyboard instructions/role and Workbench tokens
  replace the identified accessibility/styling inconsistencies.
- Integration found a panorama crop/save race: a pending old calibration save
  could overwrite the newly cropped field of view. Crop now completes pending
  and in-flight saves before publishing, holds the editor stable, and awaits
  refresh/reselection. Failed saves retain edits, and Undo/Redo availability is
  reconciled afterward. API tests cover three crop calibrations; a browser
  regression forces both an in-flight save and a newer queued edit, then checks
  that the crop and the newer edit remain stored after leaving the editor.
- Documentation now separates unresolved findings from dated implementation
  evidence and future release prerequisites. Current guidance is Workbench-only.

Focused native-window/main, ingest/API/backup/race checks and **4 source-browser
checks** passed, as did **83 release-integrity/pre-tag regressions**. A disposable
hidden WinForms form passed
physical bounds restoration/snapshot on the actual 100% display. Regression
tests simulate 125%, 150%, 200%, and mixed-monitor positions; real mixed-DPI
hardware acceptance remains open.

Final validation: **743 tests passed** (one upstream Starlette/httpx deprecation
warning), and **48/48 browser checks passed** across the web planner, packaged
planner, and catalog shells. JavaScript, DOM, encoding, cache-token, and shared
core guards passed. The rebuilt `dist/NightSeam.exe` is a 20.5 MB development
snapshot, version `0.6.34-10-gf5bb0c5-dirty`, built at 15:21:48 local time. The
previous executable was backed up, and library data was preserved. Development
integrity (`check_release.py --allow-dirty --no-stage`) reports zero errors and
the expected dirty/off-tag warning; the older staged release ZIP was excluded.
Repository integrity and whitespace checks passed. The implementation is staged
for review, with no new commit, tag, merge, or deployment.

The earlier stale-executable failure is superseded for the catalog by the
previous rebuilt executable's seven passing packaged catalog checks below.
That subset must not be described as a new full `--all` gate. Duplicate override
behavior follows the requested global no-duplication rule. Issue-ID searches
alone do not establish missing regression coverage: dedup behavior has backend
and browser pins.

Commit-integrity preparation includes the new runtime modules, tests and durable
guides together with their importers; the release guard rejects missing or
untracked runtime modules. The curated 0.6.35 entry is prepared and its pre-tag
check passes, without creating a tag. The broken Codex scratch ref was backed up
and removed while preserving its object; repository-wide history works again.
Requested dated captures are retained locally and ignored, provenance copies
remain intact, and historical commits/tags were not rewritten or garbage-collected.
AGENTS.md was shortened from 67,165 to under 48,000 UTF-8 bytes, with supporting
architecture/storage/web details moved to DEVELOPMENT.md.

Future release prerequisites remain separate: create the clean release
commit/tag when releasing, then refresh staging and version manifests through
the documented process. The existing staged ZIP is the older September
snapshot, not the current executable. Deployed cached-core compatibility needs
verification when publishing the documented retirement exception. No current
release-ready or live-compatibility claim is made.

## UNRELEASED - 2026-10-01 catalog duplicate prevention

UIB-034: full-content SHA-256 identity prevents repeated and renamed imports
from creating another archive copy or library row. Skips appear as Already
imported and do not increase exposure totals or import history. Different bytes
with the same filename remain distinct. Existing duplicate records are not
deleted. Legacy rows acquire measured fingerprints lazily; reindex shares the
identity rule while retaining files already present on disk.

This completed snapshot passed **703 pytest tests**, with one upstream
Starlette/httpx deprecation warning, and JS/DOM/encoding/core guards after the
suite restored its mutation fixtures. A rebuilt dirty development executable
at `dist/NightSeam.exe` passed **7/7 isolated packaged catalog checks**, covering
duplicate counters, import progress, exports, and responsive layout. The
previous executable was backed up, the user's data folder was preserved, and
the browser checks used a separate test library. These results precede the
full-review implementation pass above.

## UNRELEASED - 2026-10-01 catalog progress, export, and visual refinements

UIB-031/UIB-032: import activity shows confirmed processed files/selected total,
elapsed time, and conditional failure counts. Completed activity remains
reviewable; interrupted requests distinguish unconfirmed outcomes from the
unattempted remainder. Archive actions are locked during an operation. Native
downloads are enabled before window creation, CSV/JSON downloads preserve the
page, and fetch failures display an error. Export format remains accessible
even when the library is empty. UIB-033 calibration attachment remains open
in `BUGS.md`.

The progress/export snapshot passed **132 focused tests**, **680 full-suite
tests**, sequential JS/DOM/encoding/core guards, and **6/6 packaged catalog
checks** against a separate development build. After the user closed the app,
the verified executable replaced `dist/NightSeam.exe` with matching SHA-256;
the prior executable was backed up and `dist/data` was preserved. Native Save As
configuration was unit-tested and checked against the installed WebView2
handler; a physical Save As interaction was not performed.

Earlier same-day visual snapshots implemented centered checkbox ticks and
Working status (UIB-029/UIB-030), stable catalog density and responsive Library
composition (UIB-027), and aligned 2:1 RA/Dec canvas axes with coalesced drawing
(UIB-028). The activity snapshot passed **18 layout cases**, keyboard toggles,
the **680-test** suite, guards, and **4/4 packaged catalog checks**. Responsive
checks included **22 viewport pairs**, breakpoint edges, populated sessions,
long labels, actions, and DPR2 geometry; its **680-test** suite and **4/4
packaged catalog checks** also passed. These are historical snapshots, not
additional claims about the subsequent review-fix tree.

## UNRELEASED - 2026-09-30 catalog Windows shell

UIB-026 replaced the earlier custom frameless repair (UIB-025) with the standard
Windows frame, native move/resize/caption behavior, and local placement
persistence. Historical evidence: **33 focused tests**, JS guards, **3/3
packaged catalog checks**, and **680 full-suite tests** passed. A later isolated
source WebView2 launch rendered Library and exposed an inspectable native
window; invoking Restore changed its caption state. System menu, Snap, taskbar,
full keyboard moving/resizing, and physical multi-monitor acceptance were not
established by those runs. The October review pass above repairs DPI coordinate
handling and persistence scheduling; it does not turn those hardware acceptance
gaps into completed checks.

## UNRELEASED - 2026-09-26/27 Direction B UI implementation

The shared layouts gained the Tonight brief/chart/ranked workspace, wide docked
inspector and compact dialog behavior, catalog navigation, local Sky View
scrub, container-driven canvases, focus behavior, and import/library retry flows.
Follow-up fixes addressed inspector bounds, chart legend and compact metadata,
shortcut scope, scroll-aware detail positioning, image-error fallback, and
wrapping long labels (UIB-006/UIB-008 through UIB-021).

The dated final snapshot passed **647 pytest tests**, JS/encoding guards, and
**38/38 complete browser checks** against a fresh dirty development build
(`0.6.34-8-g3c12daf-dirty`). Its screenshot matrix contained 42 desktop and 40
web captures. The theme matrix described at that time included Glass; current
presentation is Workbench-only. UIB-022's Glass-specific Horizon issue is
superseded, while UIB-023/024's older visual claims are no longer supported by
the current Tonight/Library structures and Horizon aspect/min-height code.
Fresh evidence is required before reopening them. Screenshot and implementation
detail remains in the Direction B records and Git history.

The older `BUGS.md` statements of a current zero-error release guard and literal
web cache tokens were historical evidence recorded before later changes.
Release-ready results belong to their dated entries below; current tokens are
validated by the guards, and a dirty development build does not establish a
current staged release.

## UNRELEASED - 2026-09-27 packaged Image Catalog shell

Not staged, tagged, or deployed. The standard packaged desktop now launches a
dedicated Image Catalog shell with Library, Import Exposures, and the fixed
archive Sky Map. The legacy desktop planner remains available from source and
with `--planner`; the web planner and shared `core/` were unchanged. Catalog
startup skips planner image prefetch, and catalog-only mode does not serve the
planner shell at `/index.html` or expose the planner's object-image and update
check routes. Those optional remote routes return 404 before network work. A
planner request handed to an active catalog-only instance shows a
close-and-relaunch notice instead of a blank route.

Validation of this working-tree snapshot:

- `python -m pytest tests/ -q --tb=no` -> **658 passed, 1 warning** in 274.59s.
- `python -m pytest tests/test_desktop_catalog_mode.py tests/test_main.py -q`
  -> **18 passed, 1 warning**.
- `python scripts/check_js.py` -> **OK**; its 25 heuristic undefined-call
  candidates are non-failing diagnostics.
- `python scripts/check_encoding.py` and `git diff --check` -> **OK**.
- `python scripts/check_browser.py --desktop` against the final executable
  -> **16/16 passed** (15 legacy planner checks and the catalog-only flow).
- `python scripts/check_browser.py --desktop --only desktop-catalog-only-launch`
  -> **1/1 passed**.
- `build.ps1 -AllowDirty` produced `dist\NightSeam.exe` (20.5 MB), version
  `0.6.34-9-gd9f4f3a-dirty`. `app/_version_baked.py` remained unchanged.

No release archive was created and no clean-tag or deployment claim is made.

## UNRELEASED - 2026-09-20 review implementation pass (final validation)

Not tagged or deployed. This pass covered planner invalidation, panorama
editing/persistence, backup restore safety, weather retry, theme module
tracking, target explainability/transit data, richer horizon metadata, and
catalog provenance/reproducible exports. The separate environmental tool was
intentionally outside its scope.

Validation of the final dirty working-tree snapshot:

- `python -m pytest tests/ -q --tb=no` -> **641 passed, 1 warning**.
- `python scripts/check_core.py` and `python scripts/check_js.py` -> **OK**.
- Browser gate -> **32/32 passed**, covering both shells, Workbench/Glass,
  horizon metadata, catalog provenance, and accessibility smoke checks.
- `build.ps1 -AllowDirty` produced `0.6.34-5-gd6e115c-dirty`.
- `python scripts/make_release.py --allow-dirty` produced
  `dist/release/NightSeam-0.6.34-5-gd6e115c-dirty.zip` (23.4 MB), SHA-256
  `a80fb785c1c0d491f1cdd9f3f64c45bcac8faf6d1f5636c361a77852db0dda47`.
- `python scripts/check_release.py --allow-dirty` -> zero errors and one
  expected dirty/off-tag warning.

The accessibility foundation adds semantic tab state, chart text alternatives,
visible focus styling, high-contrast/reduced-motion rules, and Escape dismissal
for the web dialog. Horizon metadata is carried through both shells, backup,
SQLite, shared history, and the profile comparison UI. Catalog provenance is a
deterministic 12,176-row sidecar with source hashes, aliases, designation
history, mapping notes, and complete JSON export actions. A full manual
screen-reader/focus-restoration audit remains open; no clean tag or deployment
was made.

Elevation semantics and the solar-system ephemeris policy remain explicit
maintainer decisions before their decision-dependent features are implemented.

> **Post-release audit note (2026-09-17).** Six unresolved findings discovered
> after 0.6.33 are tracked in `BUGS.md`. In particular, the live HTML document
> still lacks CSP even though `version.json` carries the policy, and the relaxed
> production smoke does not establish document protection. This note does not
> rewrite what the 0.6.33 release ran or observed; fixes belong in the next
> release entry when they ship. **All six shipped in 0.6.34 (below).**

## UNRELEASED - 0.6.35 working tree (2026-09-19)

**Not tagged.** The four items `BUGS.md` had been carrying since the 0.6.20/0.6.24
reviews (`CR2`, `DS4`, `DS5`, `WB7`) were reassessed and closed. All four were
severity S4 ("migration") originally; the reassessment CONFIRMED two and REFINED
two, and in both refined cases the original note was imprecise in a way that
mattered.

**Workbench theme redesign.** The new `workbench` theme is now the default for
the desktop app and web planner, with the landing page following the same
graphite/brass visual language. The existing `dome` Glass theme remains
selectable from Appearance. Legacy `classic`, empty, and unknown values migrate
to Workbench; desktop settings and web backups preserve the canonical `dome` or
`workbench` value. The Tonight surfaces gained the shared session brief,
booked-night track, chart-first target layout, and side inspector treatment.
The implementation is scoped to `app/theme.py`, the two theme stylesheets,
`landing/style.css`, and the existing shell controls; astronomy, planning, and
catalog behavior did not change.

**Workbench verification.** `python scripts/check_js.py` -> OK.
`python -m pytest tests/ -q --tb=no` -> **626 passed, 0 failed**. Manual live
smoke checks covered the desktop and browser planner shells in both the default
Workbench presentation and the selectable Glass presentation.

**Beta deployment.** The current `web/` tree was uploaded to the isolated
`/beta/` remote directory and is live at
`https://www.nightseam.de/beta/`. The live page reports the catalog ready with
12,176 objects and `data-theme="workbench"`; production `/astro/` and the
landing page were not changed for that theme deployment. The repository Playwright gate could not start in
this Windows sandbox (`WinError 5` while creating its browser subprocess), so
the deployed page was verified through the in-app browser instead.

**Weather CSP fix and deployment (2026-09-19).** The public web document CSP
now allowlists `https://api.open-meteo.com` in `connect-src`, matching the
browser-side weather fetch. The source-level policy test covers both the HTML
meta carrier and `.htaccess` header carrier. `web/index.html` and
`web/.htaccess` were uploaded to both `/beta` and `/astro`. Live in-app browser
checks confirmed that both deployed sites load current conditions and the
four-day forecast. The repository Playwright gate still cannot start in this
Windows sandbox (`WinError 5`); no version bump or tag was made.

**Hourly observing weather.** Both shells now normalize Open-Meteo hourly
forecast data into a site-local selected-night timeline with cloud cover,
precipitation probability and amount, a plain-language sky-clarity/haze
proxy, temperature/dew-point spread, and derived dew-risk labels. It now
shows dark hours only and puts a 0–100 night rating plus a green/yellow/red
aggregate status in the top bar; daylight/current-noon and raw kilometre
visibility values are not presented. Responses are generation-guarded so
changing site or date cannot paint stale data. A direct browser-side 7Timer
seeing/transparency feed is intentionally deferred until it can be served
through a safe HTTPS/CORS-aware provider boundary; the normalized hourly
contract leaves that extension open.

**Theme-owned visualizations.** Sky View, the catalog sky map, and altitude
graphs now consume semantic `--viz-*` roles through `viztheme.js` in both
shells. Switching Appearance emits `theme:changed` and repaints the active
canvas, so Workbench and Glass differ inside the charts as well as around them.
The invariant test was updated to protect the semantic contract rather than a
single hard-coded red palette.

**Altitude graph legibility.** The detail-chart legend is now grouped into
Target state and Chart guides, with plain-language descriptions for the target
states, minimum threshold, moon track, twilight shading, best window, and
obstruction ceiling. The Moon track has its own blue/cyan palette role instead
of sharing the warning colour, and both shells clip the track at the chart's
-10° floor so it cannot overlap the time labels.

**Menu stacking.** Workbench now follows the shared chrome ladder in both
shells: the top bar sits above the tab strip and page content, while its menu
popover remains above the bar. This prevents the hamburger menu from opening
behind Horizon cards or other view panels.

**Independent EOG zenith sky brightness.** Added a shared, pure EOG/VIIRS
estimate based on the published neighborhood sky-glow model, an offline builder
for reduced Annual VNL radiance/coverage packs, a lazy optional chip in both
shells, and a separate Lorenz comparison validator. Lorenz is not a runtime
dependency or shipped asset; the estimate remains display-only and does not
replace Bortle or ranking. The raw EOG source rasters were not bundled because
the current download endpoint requires operator access. Focused verification:
`python scripts/check_js.py`, `python scripts/check_core.py`, pack-builder and
validator syntax checks, and `python -m pytest tests/test_skybrightness.py -q`
(5 passed). The full suite remains the final release gate.

No tag or package was made.

**CR2 - malformed-horizon guards (confirmed, and larger than noted).**
`visibleSkyBands` had already been fixed (CO-04), but an empirical probe of the
whole family found three live gaps: `skyBandPolygons` and `profileSegments` threw
on 7 malformed shapes (the desktop editor passes `profileTop`, so a sky record
with no top array crashed a render), and `ceilFor` validated the profile's LENGTH
but not its ELEMENTS - returning `ceil: NaN` for a 720-long array holding NaN,
which is the exact hazard its own guard exists to prevent (`alt > NaN` is false,
so every object at that azimuth reads as blocked). All three now return the empty
sentinel their siblings use. **Pin:** `tests/js/horizon_degrade.mjs`, 14 malformed
shapes x 7 entry points, plus a positive case so the degrade cannot become
"always empty"; mutation-proven for both guards added.

**DS4 - the worker/detail peak disagreement (refined; the note was wrong).**
The note said "one-sample/0.05 deg difference near ties". The two payloads sample
DIFFERENT grids (night 6 min noon-anchored, detail 4 min dusk-anchored), so their
peaks legitimately differ and that is not the defect. The defect was that the
detail payload disagreed with ITSELF: it took the argmax over the 2-dp-ROUNDED
array while the night path used the raw argmax, so `max_time` and `max_alt` could
name different samples. Fixed to the raw argmax. **Measured, and it limits the
pin:** since `round2(max(raw)) >= round2(raw[j])`, the rounded argmax can only
land at or before the raw one, so the reported peak VALUE is unchanged either way;
the difference is which of two equal-valued samples `max_time` names. The shipped
ic434 row holds a single sample at its detail maximum over four dates, so no live
fixture separates the two. The engine therefore asserts the user-visible contract
(the two fields describe one sample; the peak is not below the grid's own maximum)
and records this limit in a comment rather than inventing a fixture. **Pin:**
`tests/js/worker_failure.mjs`.

**DS5 - worker revive (confirmed).** `_worker.onerror` set the broken flag but
left the DEAD Worker in `_worker`; `__NS_REVIVE_WORKER` clears the flag, so the
next build handed the corpse back to `postMessage` - the recovery hook could not
recover. The kill hook was already correct, which is exactly why the existing
`desktop-worker-kill-sync-fallback` check passed while this path was broken. The
handler now terminates and nulls the worker like the timeout path and the kill
hook; it was extracted into `onWorkerError` so a browser check can drive it with a
genuine `ErrorEvent` through the new `__NS_FAIL_WORKER` dev hook. **Pins:**
`tests/js/worker_failure.mjs` (mutation-proven) and the new browser check
`desktop-worker-error-disposal`.

**WB7 - web bundle version (confirmed).** `version` was written at export and
IGNORED on import, so a bundle from a future format was accepted and its fields
read under this build's rules - a silent misreading reported as a successful
restore. `stageBackup` now refuses a version above `BUNDLE_VERSION` with a
message that says to update; v1 (no stamp) and v2 still restore, and an unusable
stamp is refused rather than crashing. **Pin:** `tests/js/web_backup.mjs`
(mutation-proven).

**Verification.** `python -m pytest tests/ -q` -> **626 passed, 0 failed**.
`python scripts/check_js.py` -> OK (core re-baked: token 33 -> 35, every importer
and its cache token moved in the same change). Browser gate: `--web` 18/18,
`--desktop` **9/9** against a freshly built exe (the new check included). The exe
was rebuilt for that gate and is a local build, not a release artifact.

**Not done, deliberately.** No version bump and no tag: this is the working tree
for the next release, and the four closures ride with it. CR3 (FOV-edge half-bin)
remains a deliberate, documented non-change - it alters user-visible scoring and
needs its own release decision.

## 0.6.34 - 2026-09-17

**RELEASED as 0.6.34** - tag `v0.6.34` on the release commit `a279513`, with the
curated `scripts/build_changelog.py` entry living IN that commit. Follow-up
`3d4eb54` regenerated the tracked `landing/changelog.json` (**never moved the
tag**); HEAD is now `v0.6.34-1-g3d4eb54`.

**What shipped.** The BUGS.md fix pass - all six findings:

- **BUG-001** - a restore now validates the whole bundle, stages payloads with a
  `payload.journal`, and swaps them in only after the database transaction
  commits. A refused restore leaves both the database and the payload tree
  byte-identical.
- **BUG-002** - imported ids need `u_` AND `safe_token()`, imported `imagetyp` is
  normalized, every desktop attribute sink escapes, the two inline `onerror=`
  handlers became listeners, and the desktop CSP dropped `script-src
  'unsafe-inline'` for the SHA-256 of the one inline theme bootstrap.
- **BUG-003** - the production document enforces a policy. `/astro/` answers
  `server: nginx` + `content-type: text/html` with no header and the Plesk console
  exposes no nginx directive field (measured 2026-09-17), so the policy ships as
  `<meta http-equiv="Content-Security-Policy">` in `web/index.html`; the header
  form stays in `web/.htaccess` for a host that offers the directive field.
- **BUG-004** - malformed bundle shapes (scalar top level, non-object records) are
  a controlled 400 raised before the safety copy, not a 500.
- **BUG-005** - the Today view publishes `#view[data-ns-gen]`/`[data-ns-sel]` on
  the scrubber's own grid and the browser check waits for the REQUESTED instant.
  The new check found three real stale-selection defects (see below).
- **BUG-006** - deleting the active panorama clears `active_pano_id` in the same
  transaction; activating an unknown id is refused with a 400.

**Found while fixing (all fixed before the tag).**

1. BUG-005's new check failed three times, each a real divergence:
   `renderTonight` captured the selection at call time while its compute runs
   ~30 ms later; `clampSkyTime()` announced `sky:time` only when it CHANGED the
   time, so at boot the announcement arrived before the scrub listener existed;
   and `#tb-scrub` is `step="15"`, so the browser snapped what `syncScrub()`
   wrote (write 204, DOM 210) - a marker publishing raw milliseconds could never
   match.
2. `style-src 'self'` in the (then-unused) header policy **silently blocked the
   shell's inline `style=` attributes**: measured 2026-09-17, the range inputs and
   theme swatches lost their styling while the page booted normally and Chrome
   logged NO "Refused to" message. Both carriers now say
   `style-src 'self' 'unsafe-inline'` (inline STYLE cannot execute), and the
   production smoke MEASURES a computed background so it cannot fail silently.

**Verification.**

- `python -m pytest tests/ -q` -> **623 passed, 0 failed** (baseline 598).
- `python scripts/check_js.py` -> OK.
- `python scripts/pre_tag_check.py 0.6.34` -> OK (curated entry present, every
  module and the catalog match their cache token).
- `python scripts/check_browser.py --all` on the staged build -> **26/26 passed**
  (web 18, desktop 8), including the new `web-tonight-render-marker-converges`.
- `python scripts/check_release.py` -> 0 errors, 1 warning (the changelog
  regeneration that step 7 performs; done and committed as `3d4eb54`).
- Staged artifacts: `dist/release/NightSeam-0.6.34.zip` (20,510,625 bytes, sha256
  `285e8927b00632c214fd37e302719bcdc49d2eefa99f7c422eea07f485e7c876`), plus
  `NightSeam-latest.zip`, `version.json` and `ns-version.js`.
- Deployed and verified LIVE: `/downloads/` (zip + latest + version.json +
  ns-version.js + changelog.json) and the `/astro` tree (`app.js`, `version.json`,
  `.htaccess`, **`index.html` last**), then
  `check_browser.py --prod-url https://nightseam.de/astro/` -> **PASS**, with the
  page's own footer version matching the deployed `version.json`.

**Not done, deliberately.** The desktop app's update notice points at
`ns-version.js`, which is uploaded; the exe itself is a local build (the `/downloads`
zip is the distributable). The nginx header remains a possible upgrade if the host
ever exposes the directive field - no change would be needed beyond adding the
documented `add_header` line.

## UNRELEASED - BUGS.md fix pass (2026-09-17)

**Not tagged, not deployed, no version bump.** This is the working-tree fix pass
for the six findings in `BUGS.md`, recorded here because the repository's rule is
that evidence lives with the change. It becomes a release entry only when it is
tagged; nothing below was verified against a deployed host.

**What changed (one line per finding).**

- **BUG-001** - `app/backup.py::apply_import_zip` now validates the WHOLE bundle
  (new side-effect-free `database.prepare_import_bundle`) before a payload byte is
  staged, stages payloads into `<target>.import-<n>.part` on the DESTINATION
  filesystem with a `payload.journal` holding any file they would replace, runs
  the database transaction, and only then swaps the staged files in. Every
  failure path rolls the journal back, so a refused restore leaves the database
  and the payload tree byte-identical. `_PayloadStage` is the sole
  payload-writing mechanism.
- **BUG-002** - the import boundary requires `u_` **and** `paths.safe_token()` for
  a custom id (`database.custom_id_problem`, the one rule shared with the insert
  loop) and normalizes `imagetyp` through `ingest.classify_frame`; the desktop
  attribute sinks that rendered those values unescaped (`setup.js` custom list -
  the id that reproduced the finding - plus `targets.js`, `tonight.js`, `pano.js`
  and the site/rig buttons) now escape them; the two inline `onerror=` thumbnail
  handlers became listeners; and `app/server.py::CSP_HEADER` dropped
  `'unsafe-inline'` from `script-src` in favour of the SHA-256 of the one inline
  theme bootstrap.
- **BUG-003** - `tests/browser/harness.py::_csp_problems(..., document=True)` and
  `_prod_smoke` read the policy from the `page.goto()` DOCUMENT response only (the
  `version.json`/`catalog.manifest.json` fallback is gone) and a missing document
  CSP is now a hard failure. `web/.htaccess` documents the required nginx
  directive; **applying it at the host layer is still outstanding** (see BUGS.md).
- **BUG-004** - `database.import_bundle_shape()` runs immediately after
  `json.loads`: a non-object top level, a non-list collection and a non-object
  entry in any collection are ValueError -> controlled 400, before the safety copy.
- **BUG-005** - `web/app.js` publishes `#view[data-ns-gen]`/`[data-ns-sel]` after
  each Tonight paint (and clears the selection the moment the scrub moves);
  `tests/browser/harness.py::wait_tonight_after` polls it and
  `web-tonight-pool-includes-unmagnituded-standard-rows` asserts the painted
  instant equals the scrubber's own (`window.__ns_scrub_sel`), replacing the
  120 ms sleep. New check `web-tonight-render-marker-converges`.
- **BUG-006** - `Database.delete_panorama` clears `active_pano_id` in the same
  transaction when it deletes the row the pointer names (and only then);
  `set_active_panorama` refuses an id that names no row, and
  `POST /api/settings` maps that to a 400.

**Verification run.**

- `python -m pytest tests/ -q --tb=no` -> **620 passed, 0 failed** (baseline in
  `BUGS.md` was 598).
- New regression modules: `tests/test_prod_document_csp.py` (6),
  `tests/test_tonight_marker.py` (5), `tests/js/dom_escape.mjs` +
  `tests/test_js_behaviour.py::test_desktop_attribute_sinks_escape_imported_data`,
  and the BUG-001/002/004/006 cases added to `tests/test_backup.py` and
  `tests/test_api.py`. The escape engine was mutation-proven (removing one `esc()`
  fails it).
- `python scripts/check_js.py` -> **OK** after repeated
  `scripts/bump_web_tokens.py` + `scripts/check_js_tokens.py --update` runs
  (`app.js` 87 -> 97 across the pass, in `web/index.html` + `web/app.js`).
- **Browser gate:** `--web` **18/18 passed** and `--desktop` **8/8 passed**
  (rebuilt exe). Escalated once per run: Playwright cannot create its Chrome pipes
  under the confined sandbox - see `ENVIRONMENT.md`.
- **BUG-003 closed on the LIVE origin.** `/astro/` was measured through Chrome on
  2026-09-17: `server: nginx`, `content-type: text/html`, NO policy header and no
  meta policy - i.e. nginx serves the document itself, so `web/.htaccess` can never
  reach it and this host exposes no nginx directive field. The enforced policy now
  ships as a `<meta http-equiv="Content-Security-Policy">` in `web/index.html`;
  `web/index.html` was deployed individually and
  `check_browser.py --prod-url https://nightseam.de/astro/` -> **PASS** (policy
  present on the document, inline-styled swatches still render).
- **A silent-breakage bug found by measuring instead of assuming.** The header
  policy carried `style-src 'self'`, which blocks the shell's inline `style=`
  attributes with NO console message: the sliders and theme swatches lost their
  styling while the page booted normally (negative control: `style-src 'self'`
  alone -> `edgeSpace`/`bortle` widths and the swatch background all
  `<blocked>`, `Refused to` messages = 0). Both carriers now say
  `style-src 'self' 'unsafe-inline'`, and the production smoke measures a computed
  background on an inline-styled element so it can never fail silently again.
- Desktop: the exe was REBUILT from this tree (PyInstaller onefile, ~47 s,
  20.8 MB, `v0.6.33-8-gfa6ef5c`) and `python scripts/check_browser.py --desktop`
  -> **8/8 passed** (boot, worker date change, worker kill/sync fallback, pref
  rescore, advanced mode, catalog tier/minvis, scrub window, pano crop FOV).
- Guard dry run: `python scripts/pre_tag_check.py 0.6.34` -> the expected FAIL
  ("no CURATED entry for 0.6.34"), with the token guard's self-test OK and no
  missed-bump finding - i.e. the release machinery is armed and waiting for the
  changelog entry, nothing else.
- **NOT run:** the header carrier on the live host (it cannot be applied there).
  The meta carrier is the live protection; `web/.htaccess` keeps the stronger
  header form ready for a host that exposes the directive field.

**Discovered while verifying (fixed in the same pass).** The browser gate earned
its keep: the new BUG-005 check failed three times in a row and each failure was a
REAL divergence, not test flakiness. (1) `renderTonight` captured
`S.skyTime` at CALL time while its compute runs ~30 ms later, so a selection
clamped in between painted cards for the old instant. (2) `clampSkyTime()` only
announced `sky:time` when it CHANGED the time, so at boot the announcement arrived
before the scrub listener existed and the slider + Tonight view kept the
pre-clamp selection. (3) `#tb-scrub` is `step="15"`, so the browser snaps what
`syncScrub()` writes (write 204, DOM 210) and a scrub writes that back into
`S.skyTime` - a marker publishing raw milliseconds could never equal the
scrubber's own instant. All three are fixed at the source and pinned by
`web-tonight-render-marker-converges`.

## 0.6.33 - 2026-09-15

**RELEASED as 0.6.33** - tag `v0.6.33` on the release commit `3c2f98a`, with the
curated `scripts/build_changelog.py` entry living IN that commit. Follow-up
`b390575` regenerated the tracked `landing/changelog.json` (**never moved the
tag**).

**What shipped.** The 0.6.32-review fix batch plus the shell-unification pass:
the web "tonight" pool gained the 45 standard-mode objects whose catalogue
magnitude is empty (the inline `o.vmag != null` filter - IC 1396, the Running
Man, NGC 281, the Flame, the Tulip, Simeis 147, the Double Cluster, the Pipe
Nebula and 38 more could never appear there), both shells now score through ONE
`core/scoring.js::scoreNightTracks` with the span as a parameter, the panorama
editor's snapshot/undo/redo/dirty model is ONE `core/panohistory.js` (the
desktop gained `#btnRedo`), the popularity source is no longer contacted anywhere (the
`--live` refresh flag is GONE, not disabled), the web shell ships a
Content-Security-Policy, and 74 review findings plus 6 investigated non-finding
items are closed or documented.

**Staged artifacts** (`dist/release/`): `NightSeam-0.6.33.zip` and
`NightSeam-latest.zip` (20,505,657 B, sha256
`c947dcd37af9c66688712284107eb574a3e0b2c1a102abd8c290c4050ba583ad`),
`version.json` (version 0.6.33, url `/downloads/NightSeam-0.6.33.zip`, size +
sha256 above), `ns-version.js` (`{"version": "0.6.33"}`), `NightSeam.exe`
(19.8 MB, rebuilt AT the tag with `build.ps1` - the release check correctly
refused to stage the earlier `0.6.32-3-g3277846-dirty` snapshot).

**Gates.**

* `python -m pytest tests/ -q` -> **598 passed** (0 failures).
* `python scripts/check_js.py` -> OK (imports, DOM ids, syntax on 81 modules,
  encoding, catalog token, the VERIFIED token baseline, core drift/purity).
* `python scripts/check_release.py` -> 0 errors at the tag, before staging.
* `python scripts/check_browser.py --all` -> **25/25** on the STAGED build
  (17 web checks incl. the new `web-tonight-pool-includes-unmagnituded-standard-rows`,
  and 8 desktop checks against the freshly built exe).
* `python scripts/check_browser.py --web --prod-url https://nightseam.de/astro/`
  -> **18/18** after upload.

**Deployed and verified live** (FTPS, Plesk):

* `/downloads/` - `NightSeam-0.6.33.zip` + `NightSeam-latest.zip` +
  `version.json` + `ns-version.js`; the LIVE zip was downloaded back and its
  sha256 equals the staged value byte-for-byte.
* `/astro/` - the whole web tree via `uploadtree` (index.html last, as the
  uploader orders it) plus `web/.htaccess` uploaded by hand (the uploader skips
  dotfiles). Live `/astro/version.json` = 0.6.33 / describe v0.6.33.
* `/` - the landing tree, so `/changelog.json` now carries the 0.6.33 entry
  (verified: newest entry 0.6.33).

**CSP caveat (measured, and the reason the check was relaxed).** The host
fronts static files with **nginx**, which serves `.html`/`.js`/`.css` itself and
never reaches Apache for them, so the `web/.htaccess` directives arrive only on
the paths nginx proxies: measured 2026-09-15, `/astro/version.json` carries the
policy and `/astro/`, `/astro/app.js`, `/astro/app.css` do not. `mod_headers` is
therefore working; the policy simply cannot cover the HTML without an nginx-layer
rule (Plesk > domain > Apache & nginx Settings > Additional nginx directives).
The production check now probes every path under the URL for the policy, treats
"the host serves it nowhere" as a printed NOTE rather than a hard failure (a
platform property this repo does not control), and still FAILS on a policy it
does find if that policy is unusable. The policy's CONTENT and its inline-script
hash are pinned offline by `tests/test_prod_csp.py`.

**Test-count and token register at this release:** 598 tests collected; web
tokens `app.js?v=87` / `CATALOG_TOKEN='10'` / astro 21 / horizon 54 / format 28 /
nightrank 20 / webstore 31 / dragwin 1 / style 24 / themes 5; core stub
`web/core.js?v=33` (current hash `7d2e90d5de153c46`, retained N-1
`21df8e451f20a165`, 10 core modules).

**Post-tag commits (never move the tag):** `b390575` (regenerated
`landing/changelog.json`) and the harness fix that relaxed the CSP probe
described above.

  **RELEASED as 0.6.25** - tag `v0.6.25` on the release commit (the curated entry
  and the build fix live in it), staged artifacts in `dist/release/`
  (`NightSeam-0.6.25.zip` + `NightSeam-latest.zip` + `version.json` +
  `ns-version.js`), `check_release.py` 0 errors, the FULL browser gate **19/19 on
  the staged build** (12 web + 7 desktop against the fresh exe), and after the
  upload the live site passes the read-only production smoke (**13/13**,
  `check_browser.py --web --prod-url https://nightseam.de/astro/`). Deployed:
  `/downloads/` (zip, latest, version.json, ns-version.js), `/astro` (whole tree,
  index.html last) and the landing (with the regenerated changelog.json). The gate
  needs ONE unsandboxed command (Chromium's mojo named pipes); sandboxed, `TO-02`
  makes it report exit 2 honestly instead of a fake "failed check(s)".
- **RELEASED as 0.6.26** - tag `v0.6.26` on the release commit (`d412998` catalog
  rebuild + `ea86a3a` the curated entry), staged `dist/release/`
  (`NightSeam-0.6.26.zip` sha256 `cd0d2bc5...` + `NightSeam-latest.zip` +
  `version.json` + `ns-version.js`), `check_release.py` **0 errors at the tag**,
  the FULL browser gate **19/19 on the staged build** and **13/13 including the
  read-only production smoke** after the upload. Deployed `/downloads/` (zip,
  latest, exe, version.json, ns-version.js), `/astro` (whole tree, index.html
  last) and the landing. Verified live, not assumed: `/astro/version.json` +
  `/downloads/version.json` + `/downloads/ns-version.js` all read 0.6.26, the
  deployed `catalog.json` sha256 equals the local payload, the published zip
  equals the staged artifact, the live tokens are `app.js?v=70` /
  `CATALOG_TOKEN = 5`, and the live copy reads 12,160 with no stale "197".
  Follow-up commit `408dab5` regenerated the tracked `landing/changelog.json`
  (**never moved the tag**).
- **RELEASED as 0.6.27** - tag `v0.6.27` on the release commit (`348b015` the
  popularity/label/migration work + `b7cf4de` the curated entry), staged
  `dist/release/` (`NightSeam-0.6.27.zip` sha256 `383faa68...` + latest +
  `version.json` + `ns-version.js`), `check_release.py` **0 errors at the tag**,
  the FULL browser gate **19/19 on the staged build** and **13/13 including the
  read-only production smoke** after the upload. Deployed `/downloads/`,
  `/astro` (index.html last) and the landing; verified live (`/astro/version.json`
  + `/downloads/version.json` + `ns-version.js` = 0.6.27, the deployed
  `catalog.json` sha256 equals the local payload, the published zip equals the
  staged artifact). Follow-up commit `f816a00` regenerated the tracked
  `landing/changelog.json` (**never moved the tag**).
- **RELEASED as 0.6.28** - tag `v0.6.28` on the release commit (`4bcbcd9` the
  7-object supplement + the CATALOG.md/DECISIONS.md docs, `e3bcb38` the curated
  entry), staged `dist/release/` (`NightSeam-0.6.28.zip` sha256 `57b47519...` +
  latest + `version.json` + `ns-version.js`), `check_release.py` **0 errors at the
  tag**, the FULL browser gate **19/19 on the staged build** and **13/13 including
  the read-only production smoke** after the upload. Deployed `/downloads/`,
  `/astro` (index.html last) and the landing; verified live (`/astro/version.json`
  + `/downloads/version.json` + `ns-version.js` = 0.6.28, the deployed
  `catalog.json` equals the local payload AND carries all seven supplement ids,
  the published zip equals the staged artifact). Follow-up commit `f45b1d0`
  regenerated the tracked `landing/changelog.json` (**never moved the tag**).
  **Gate note:** the first `--all` run on the freshly built onefile exe failed
  `desktop-pano-crop-fov` with a 30 s wait timeout (32.5 s) because PyInstaller
  unpacks the ~20 MB exe to %TEMP% on first launch; the immediate re-run passed
  7/7 (3.1 s). A cold-start timeout right after a build is that, not a regression -
  re-run the gate before investigating.
- **RELEASED as 0.6.29** - tag `v0.6.29` on the release commit (`365dc0e`: the
  nine-object supplement batch 2 + the copy/doc updates; the curated entry is in
  the same commit). Catalogue **12,167 -> 12,176** (supplement 7 -> 16), staged
  `dist/release/` (`NightSeam-0.6.29.zip` sha256 `c43e85a41f4f87015a7cb1e0ef308f59dc1385a02662f634ddbc2bcc6a6744dc`,
  20,483,677 bytes, + latest + `NightSeam.exe` + `version.json` + `ns-version.js`),
  `check_release.py` **0 errors at the tag**, the FULL browser gate **19/19 on the
  staged build** (12 web + 7 desktop against the fresh exe - no cold-start timeout
  this time) and **13/13 including the read-only production smoke** after the
  upload. Deployed `/downloads/` (zip, latest, exe, version.json, ns-version.js),
  `/astro` (whole tree, index.html last) and the landing. Verified live, not
  assumed: `/astro/version.json` + `/downloads/version.json` +
  `/downloads/ns-version.js` all read 0.6.29, the deployed `catalog.json` sha256
  **equals the local payload** (`c44fd26a...`, 12,176 rows / 200 standard) AND
  carries all nine new ids with the `Squid` alias intact, and the published zip
  sha256 equals the staged artifact. Tokens: `app.js?v=74` (index.html) /
  `CATALOG_TOKEN = 8` + manifest, baseline refreshed for the one moved token.
  Popularity 7,645 -> **7,654** (the nine new rows; no existing score touched).
  All nine entered Standard mode on their own scores (29,490 / 25,230 / 22,630 /
  14,110 / 9,790 / 8,230 / 6,780 / 5,200 / 2,188, cut 1,751), displacing nine
  members: `ngc6526`, `ngc3992` (M109), `ngc6121` (M4), `ic63`, `ic1871`,
  `ngc2246`, `ngc6905`, `ngc2362`, `ngc6231` - the documented consequence of
  `STANDARD_TOP_N = 200`, accepted deliberately (see `DECISIONS.md`).
  Follow-up commit `809de12` regenerated the tracked `landing/changelog.json`
  (**never moved the tag**); `RELEASES.md` record in the next commit.
- **RELEASED as 0.6.30** - tag `v0.6.30` on the release commit (`d47d019`: the popularity
  TIER column + frame-coverage colours + Min-visibility slider in BOTH apps, plus the
  curated entry in the same commit). Staged `dist/release/`
  (`NightSeam-0.6.30.zip` sha256
  `4204eda2668057e73093c8312a0cc2cb1f5778e74c710fcf52ab6c2b0c60d576`, 20.5 MB, + latest +
  `NightSeam.exe` + `version.json` + `ns-version.js`), `check_release.py` **0 errors at the
  tag**, the FULL browser gate **21/21 on the staged build** (13 web + 8 desktop - the
  desktop suite gained `desktop-catalog-tier-minvis`, and the web check now drives the
  slider too) and **14/14 including the read-only production smoke** after the upload.
  Deployed `/downloads/` (zip, latest, exe, version.json, ns-version.js), `/astro` (whole
  tree, index.html last) and the landing. Verified live, not assumed:
  `/astro/version.json` + `/downloads/version.json` + `/downloads/ns-version.js` all read
  0.6.30, `landing/changelog.json`'s newest entry is 0.6.30, the deployed `catalog.json`
  sha256 equals the local payload (`c44fd26a...`, 12,176 rows / 200 standard - the payload
  itself did not change in this release), the live `index.html` carries `app.js?v=76`, the
  live `app.js` carries `CATALOG_TOKEN = '8'` plus the new `cat-minvis` control, `style.css`
  the `.tier-badge`/`.over` rules and `nightrank.js` the `nightStatsBatch` re-export, and the
  published zip sha256 equals the staged artifact.
  The core was re-baked twice (stub 21 -> 23, the tier helpers), so the token cascade touched
  every web module - final register: app 76 / astro 15 / horizon 47 / format 22 / nightrank 13
  / webstore 25 / style 18 / core 23. **503 tests.** Follow-up commit `c16e54f` regenerated
  the tracked `landing/changelog.json` (**never moved the tag**).
  **Gate note:** `dist/NightSeam.exe` was rebuilt with `-AllowDirty` mid-work to verify the
  desktop UI in a real browser before the release (it reported `0.6.29-2-ge4e92c1-dirty`), and
  the first `--all` run on that build hit the documented PyInstaller cold-start timeout on
  `desktop-pano-crop-fov`; the immediate re-run passed (2.9 s). The released exe was then
  rebuilt CLEAN at the tag (`0.6.30`) and the staged gate passed 21/21 first time.
- **RELEASED as 0.6.31** - tag `v0.6.31` on the release commit (`93dccde`: the
  ranking-alignment + progress/layout-stability work plus the curated entry).
  Staged `dist/release/` (`NightSeam-0.6.31.zip` sha256
  `5a0fa461571800e78ab8600fa66ba616c59c17124f3de62600d37d335f37b633`, 20.5 MB, +
  latest + `NightSeam.exe` + `version.json` + `ns-version.js`), `check_release.py`
  **0 errors at the tag**, the FULL browser gate **22/22 on the staged build** (14
  web + 8 desktop, first run - no cold-start timeout) and **15/15 including the
  read-only production smoke** after the upload. Deployed `/downloads/` (zip,
  latest, exe, version.json, ns-version.js), `/astro` (whole tree, index.html
  last) and the landing. Verified live: `/astro/version.json` +
  `/downloads/version.json` + `ns-version.js` all read 0.6.31, the newest
  `changelog.json` entry is 0.6.31, the deployed `catalog.json` sha256 equals the
  local payload (`c44fd26a...`, 12,176 / 200 - unchanged again this release), the
  live `index.html` names `app.js?v=78` and `style.css?v=21`, the live `app.js`
  carries `cat-minvis`/`cat-stats`, `style.css` carries `.busy-slot` and
  `table-layout: fixed`, `nightrank.js` re-exports `nightStatsBatch`, and the
  published zip sha256 equals the staged artifact.
  What it fixes, all MEASURED in a real browser before/after: the catalog table
  used to widen the PAGE on a narrow viewport (page 721 px at a 360 px viewport ->
  now 360, scrolling inside its card); progress text ("computing visibility…") and
  the busy chip used to move the layout (the desktop topbar grew 93 -> 111 px and
  pushed the app down -> now constant via a reserved `.busy-slot` whose HEIGHT is
  reserved too, because at 1366 px the topbar already wraps); the catalog's
  percentage columns used to resize as cells filled in (`table-layout: fixed`).
  Also aligned: both shells now rank the tier over the set the current mode shows
  (`activeCatalog()` / `S.catalog`), proven on the real payload by
  `test_tier_letters_are_mode_independent_on_the_real_catalog`. **507 tests.**
  No core re-bake in this release (stub stays 23), so `/astro/core` gained no new
  dir; the four stale dirs from <= 0.6.26 are still unpruned. Follow-up commit
  `80f362f` regenerated the tracked `landing/changelog.json` (**never moved the
  tag**).
- **RELEASED as 0.6.32** - tag `v0.6.32` on the release commit (`2966a3c`: the
  `distance` removal + the designation-based Wikipedia lookup, with the curated
  entry). Staged `dist/release/` (`NightSeam-0.6.32.zip` sha256
  `41882ffc82b5c109bee1ca790ec828d4e4a9e248e29fe279585a06143b508e00`, 20.5 MB, +
  latest + `NightSeam.exe` + `version.json` + `ns-version.js`), `check_release.py`
  **0 errors at the tag**, the browser gate **22/22 on the staged build** and
  **15/15 including the read-only production smoke** after the upload. Deployed
  `/downloads/`, `/astro` and the landing; verified live: the version endpoints and
  `changelog.json` all read 0.6.32, the live `index.html` names `app.js?v=80`, the
  live `app.js` carries `CATALOG_TOKEN = '9'` AND `wikiTitles`, the deployed
  `catalog.json` sha256 equals the local payload (`ae002a9a...`, 12,176 rows, **no
  `distance` field**), and the published zips equal the staged ones.
  **A real deploy defect was caught here:** the first `/astro` tree upload stalled
  mid-way (the documented ProFTPD reuse behaviour) and left `core.js` pointing at
  the PREVIOUS baked tree, so a returning visitor would have loaded a core without
  `wikiTitles` and the app would have died on the missing named export. The verify
  pass found it, `core.js` + `catalog.json` were re-uploaded on fresh connections,
  and a new check (`logs/verify_tokens_live.py`) now compares EVERY live module and
  the whole baked core tree byte-for-byte against the repo - it passes with
  `STALE: none`.
  Also noted: `desktop-pano-crop-fov` timed out at 30 s on two consecutive full gate
  runs (31.9 s) and then passed in 2.9 s on the next one with the SAME preceding
  checks, and in 2.9 s standalone - the upload/decode budget is flaky, so its wait
  is 90 s now (`d6ace7a`, a post-tag test-only commit; the staged artifacts were
  built at the clean tag and are unaffected). Follow-up `4d8ce4d` regenerated the
  tracked `landing/changelog.json` (**never moved the tag**).
