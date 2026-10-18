# Packaged populated-library experiment

`scripts/bench_populated_import.py` compares two Windows executables sequentially
in ABBA order on one runner. Both executables are compiled with the same pinned
environment. The baseline is `45d19fa9c96b6dc75069f31cbdc0f8dd7f1bbc91`, immediately
before the ordered-query/narrow-hydration change; the candidate is the selected
workflow commit. The application code in the initial candidate matches `e27b47f`.
This experiment adds developer tooling and failure coverage without changing
application import behavior.

Each sample owns a fresh database/archive, executable copy, TEMP, WebView2 data
and Windows job. Before starting the executable, it seeds 6,000 small real FITS
and 96 large integer exposures with 8, 16 and 32 MiB pixel payloads, plus FITS
headers/padding. Synthetic sensor noise and distinct exposure bytes are
deterministic. A conservative free-space check runs first. The two synthetic
object identifiers deliberately avoid matching real catalog names.

The measured HTTP waves upload existing duplicates, 24 new plain images (448 MiB
of pixel data), three new safe-Rice images, and mixed original/compressed
duplicates. A separate cancellation measurement starts conversion of the entire
plain-exposure session and waits for nonempty native output before requesting
cancellation. It requires a cancelled terminal state, unchanged records and no
remaining staging. This measures the session conversion cancel API; completed
upload-body disconnects continue to use their existing drain contract.

Each wave reports actual upload elapsed time and MiB/s. Concurrent probes measure
static HTTP latency and JavaScript roundtrip latency from the real owned native
WebView2 page. These are separate metrics; neither measures physical input or
rendering latency. The Windows job's CPU accounting includes the bootloader,
application, WebView2 and native FITS utilities, and excludes the controller.
Its peak committed bytes are a cumulative high-water mark since launch, **not
RSS or a per-wave allocation peak**. Resource snapshots span the probe interval,
which can end slightly after the upload timer.

Outside throughput timing, the controller restores the complete separate
three-image Rice session and verifies every source and archived SHA, original
seed metadata, image/integrity/history counts, exact restored original bytes,
absence of journals/stages, graceful native process exit and listener closure.
All four samples must have the same byte-identity workload digest. Comparison
directories cannot overwrite earlier evidence; failure reports retain completed
waves and logs, and owned scratch cleanup must succeed.

Caches are warm/uncontrolled; no cache flush, cold-disk claim, timing threshold,
clean-VM claim, native-memory leak claim or general throughput guarantee is
made. The earlier 6,000-small-file instrumented source workload remains separate.
Its SQL-work improvement need not translate proportionally to large-file IO.

Run the existing Catalog validation workflow with the boolean
`run_large_populated=true` on the desired branch. This selects only the large
experiment job; ordinary pull-request CI still runs the full source suites and
packaged correctness gates. Both paths have explicit required-success checks.
The experiment retains `large-import-comparison.json`, all per-sample reports
and application/build logs. To repeat locally in the pinned Windows environment
after both builds, install Playwright 1.62.0 for the controller and run:

```powershell
python scripts/bench_populated_import.py --baseline-exe PATH/TO/BASELINE.exe --candidate-exe PATH/TO/CANDIDATE.exe --baseline-commit BASELINE_SHA --candidate-commit CANDIDATE_SHA
```

Local preflight passed ten focused tests, including real native-child
cancellation/deadline termination, partial-stage and fallback-publication
ENOSPC, and Rice encoding/publication fsync failure rollback with successful
retry. An additional real source HTTP controller smoke passed the full
large-file/duplicate/Rice restoration/cancellation/byte/history sequence with
12 small seeds, 24 large seeds and three new plain images. Its UI and resource
probes were test doubles; `logs/large-source-smoke.json` is explicitly marked
source-only and is not packaged performance evidence. Final Windows comparison
results and full correctness reports are retained on the draft PR and in CI.
