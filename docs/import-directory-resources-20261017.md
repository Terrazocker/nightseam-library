# Windows directory resources — October 17, 2026

## Measured layouts

The dedicated Archive resource checks workflow uses private databases and valid
5,760-byte synthetic FITS. It seeds files directly, then runs production batch
preparation, negative/positive lookup, cleanup and cancellation paths. These are
resource/safety checks, not HTTP publication or exposure-throughput benchmarks.

Two cases contain 4,097 files, one per leaf parent, with three request cycles.
The deeper case adds eight common ancestor directories. The third case uses
256 files with that deeper layout for fifty cycles. Absolute exposure paths
were 124, 156 and 155 characters respectively; these checks do not establish
maximum-path-length compatibility. Paths and file sizes match between runs.

Baseline branch commit: `777838f21efc5bae7bbd2a0f65c6c706ba54fdb6`;
tested PR merge: `b395721ea30634111500f94a87e1f73c29f2e9fa`.
[Baseline run](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39037931678).
Shared-ancestor branch commit: `826ab3ac650c8015f0f33b198f1d3b353bc1ec12`;
tested PR merge: `3c2fd73ebe2700ef1d7571ac23aa786597c0291b`.
[Shared-ancestor run](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39038863156).

Both used Windows Server 2022/two logical CPUs, but the baseline CPU was AMD64
Family 25 Model 1 Stepping 1 and the candidate Family 26 Model 2 Stepping 1.
Handle counts measure the actual ownership change on matching layouts. Elapsed
times and RSS across those different runners are not controlled speed/memory
comparisons. Python heap measurements include instrumentation and warm caches;
native allocations are outside tracemalloc.

## Handle use and retained memory

The table shows the largest observed process-handle increase while each batch
was held, after subtracting its pre-request baseline.

| Layout | Pinned files | Before | Shared ancestry | Reduction | Unique directory pins afterwards |
| --- | --- | --- | --- | --- | --- |
| One file per parent | 4,096 | 53,248 | 12,298 | 76.9% | 8,202 |
| Eight extra shared ancestors | 4,096 | 86,016 | 12,306 | 85.7% | 8,210 |
| Smaller deep layout, 50 cycles | 256 | 5,376 | 786 | 85.4% | 530 |

Every candidate sample's handle increase equals pinned files plus unique pinned
directories. Process handles returned to 201 after every candidate cycle and
after cancellation, matching the initial baseline. Baseline cleanup also passed;
this removes duplicate live handles rather than fixing a discovered handle leak.

Held Python heap growth ranged from 81.396–84.522 MiB before to
35.989–38.959 MiB afterwards in the fan-out case, and 106.270–109.398 MiB before
to 38.876–41.847 MiB afterwards in the deeper case. These are instrumented sample
ranges, including first-cycle cache warmup, not a process-memory budget.

Both fifty-cycle runs passed the two-MiB warmed retained-heap gate. After five
warmup cycles, growth across the remaining 45 samples was 82,185 bytes before
and 82,776 bytes afterwards. Stored report records contribute to this growth.
Candidate RSS after those samples changed from 46,092,288 to 46,563,328 bytes;
RSS is observational, not a proof that native leaks cannot occur. These runs
establish the measured fifty-cycle behavior, not indefinite stability.

Each 4,097-file cycle still performed 4,099 archive hashes: 4,096 preparation
hashes, two fresh above-cap negative hashes, and one positive duplicate hash.
Each smaller cycle performed 257 hashes. All source-byte, catalog-row-count,
staging, writer-exclusion, archive-lease and cancellation-drain checks passed.
No hash identity or transaction shortcut was introduced.

## Ownership and safety

The batch keeps a set of canonical directory paths whose handles it owns.
Acquisition walks from the volume root downwards, borrowing only handles retained
by this same batch and staging an unseen suffix locally. Successful whole-file
hash and stable-version verification transfer the suffix and leaf pin together;
only then are new directory keys published. Unsupported versions, failed reads,
refused directories and cancellation release the provisional suffix without
invalidating earlier pins. The file cap and conservative fallbacks are unchanged.

Every candidate retains lexical/root identity validation. Windows access/sharing
flags and no-link checks remain intact. Request acquisition bypasses the
unrelated operation's thread-local parent shortcut so that external context
cleanup cannot shorten a batch's directory lifetime. Stack order closes later
borrowers before the earlier context owning their ancestors; all proof keys clear
before unrelated archive operations resume. Default recovery/compression callers
retain their contexts, and POSIX production admission remains disabled.

The candidate resource run passed 35 focused Windows regressions. They include
actual writer/common-parent rename exclusion, per-directory uniqueness, failed
new suffixes preserving earlier pins, unsupported/changed versions, read failure,
partial acquisition cancellation, aliases/root replacement, and a request
outliving an external pin context. Local full source validation passed 865 tests
with eight skips; packaged/source checks are recorded on the PR.

## Evidence and remaining limits

JSON/logs are retained in artifacts `archive-resources-b395721ea30634111500f94a87e1f73c29f2e9fa`
and `archive-resources-3c2fd73ebe2700ef1d7571ac23aa786597c0291b`, and locally under
`logs/fanout-baseline/` and `logs/fanout-shared/`.
Repeat via Archive resource checks manual dispatch or relevant archive-source PRs.
Individual commands are in `.github/workflows/archive-resource-checks.yml`.

Arbitrarily deep distinct branches still need their own unique directory handles.
The subsequent resource bounds add a 16,384 combined request-handle limit,
including two slots for transient hash IO; excess candidates use fresh reads.
Preparation also limits rows in SQL and duplicate search uses complete 256-row
keyset pages. The measured comparison above predates these bounds; it documents
shared-ancestor savings rather than performance of the newer limits.
Larger populations, other
filesystems and longer lifetimes remain separate workloads. Clean/offline VM and
physical DPI/monitor/Snap/native Save As acceptance still require suitable Windows
hardware or a disposable VM using the prepared manual procedure.
