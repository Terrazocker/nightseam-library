# Windows import profile — October 17, 2026

## Lazy admission results

The request-local index now admits newly committed candidates lazily, measuring
current archive bytes under continuously held file/directory pins. Later negative
comparisons reuse that proof during the request. Positives rehash, and every new
request performs fresh checks. No upload or database digest alone admits a file.

The identical 512-file/24-per-request workload produced these measurements:

| Measurement | Before | With lazy admission |
| --- | --- | --- |
| Incoming bytes | 2,124,840,960 | 2,124,840,960 |
| Complete archive hashes | 11,368 | 6,034 |
| Archive bytes read | 47,178,109,440 | 25,041,582,720 |
| Candidate visits | 130,816 | 130,816 |
| Pinned negative hits | 124,992 | 130,326 |
| SQL SELECT statements | 10,567 | 5,233 |
| Actual SQL BEGIN / COMMIT | 512 / 512 | 512 / 512 |
| Source profile import wall time | 88.733 seconds | 67.837 seconds |
| Source process CPU | 71.578 seconds | 66.578 seconds |
| Archive hashing CPU | 44.828 seconds | 35.516 seconds |
| Separate packaged import | 85.445 seconds | 69.023 seconds |
| Packaged throughput | 23.716 MiB/s | 29.358 MiB/s |

Archive hashing read **22,136,526,720 fewer bytes (46.9%)** and performed 5,334 fewer
complete hashes. Candidate visits remain unchanged; additional pinned negatives
also avoid 5,334 full-row SQLite fetches. A focused 24-new-file HTTP regression
requires 23 earlier-file hashes instead of the former 276, while potential
duplicates still trigger fresh verification. Reads across closed handle lifetimes
remain conservative.

Both measurements used Windows Server 2022 and two logical CPUs, but the earlier
runner reported AMD64 Family 25 Model 17 Stepping 1 and the new runner Intel64
Family 6 Model 106 Stepping 6. Caches were not flushed. The read-count reduction is
directly measured on identical inputs; wall/CPU timings across these separate
machines do not establish a controlled speedup. The source profile includes
instrumentation overhead and is separate from packaged throughput.

All 512 source/archive pairs, duplicate identity/class/session/location and
same-size/restored-mtime edit probes passed. Safe Rice/native restoration passed
in the packaged run, and both runs closed their owned processes/listeners/scratch.
The measurement run also passed 842 Windows source tests (two skips), all six
required Windows contracts, browser 24/24 and restricted native window 7/7.

Optimized commit: 2b9549666c5fdba0fb8624957d251154ea67d416.
Executable SHA-256: 6c2a7133908c0f2c157f20f38b293ea5857f45a68d5854fdb37c5304261961fe.
[Successful measurement run](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39029236559).
Artifact: `windows-validation-2b9549666c5fdba0fb8624957d251154ea67d416`.
Local JSON/logs: `logs/lazy-dedup-measurements/logs/`.

## Same-machine follow-up

A later same-runner packaged ABBA comparison measured baseline 98.020/89.795
seconds and candidate 55.548/58.812 seconds: median 93.908 → 57.180 seconds
(39.1% lower elapsed time in this small warm-cache sample). Its source profile
reconfirmed the same 6,034 archive hashes and 25,041,582,720 bytes. See
[the boundary/comparison report](import-scaling-20261017.md) for all samples,
machine/build hashes, resource limits and passing audits. The historical
different-machine results above remain recorded without a timing inference.

## Original profile before lazy admission

Archive rehashing dominated this measured plain-FITS import. The isolated source
server performed **11,368 complete archive hashes, reading 47,178,109,440 bytes**
for 2,124,840,960 incoming bytes. Archive hashing consumed 44.828 of 71.578 process
CPU seconds (62.6%). The original input copy/hash phase consumed 2.141 CPU seconds;
database commits consumed 0.141 CPU seconds and 2.426 thread-wall seconds.

| Measurement | Observed |
| --- | --- |
| Workload | 512 unique 4,150,080-byte FITS files |
| Requests | 21 batches of 24, then 8 files |
| Source profile import wall time | 88.733 seconds |
| Source server process CPU | 71.578 seconds |
| Incoming SHA-256 bytes / streams | 2,124,840,960 / 512 |
| Archive SHA-256 bytes / completed hashes | 47,178,109,440 / 11,368 |
| Candidate visits / pinned negative hits | 130,816 / 124,992 |
| Actual SQL BEGIN / COMMIT | 512 / 512 |
| Resolver index constructions / cache misses | 1 / 1 |
| Unattributed process CPU | 13.203 seconds |
| Separate packaged import | 85.445 seconds, 23.716 MiB/s |

The 47.178 GB of archive reads are 22.203 times the input size (43.938 GiB).
Request-local pins avoid many repeated candidate hashes, but fresh checks across
Windows handle lifetimes and checks of newly published files still read substantial
data. This identifies archive rehashing as the leading measured CPU target. It
does not justify trusting timestamp-only identities or removing whole-file checks.
Any future reuse must preserve writer exclusion, edit detection after a pin closes,
positive-duplicate verification, cancellation and rollback behavior.

The profile wraps selected synchronous functions in every invoking worker thread;
thread-local stacks subtract nested phases. Exclusive phase CPU times are additive,
while phase wall times overlap and must not be added. This is a selected-function
profile, not a complete program call graph. Windows thread CPU accounting is coarse;
tiny per-phase CPU totals should not be interpreted precisely. Unwrapped HTTP work,
runtime workers and instrumentation overhead remain unattributed. Overhead is not
subtracted, and the source profile is not a packaged throughput baseline.

The generated fixtures warm caches, which were not flushed. Both measurements used
Windows Server 2022 with two logical CPUs (AMD64 Family 25 Model 17 Stepping 1).
The packaged run had an empty developer PATH; installed OS/.NET/WebView2 components
remained. No speedup ratio against historical workstation runs is inferred.

Startup, fixture generation and post-import audits are excluded. Every source and
initial archived copy matched SHA-256; duplicate probes retained identity, class,
session and location; a same-size/restored-mtime edit was detected. The packaged run
also passed Safe Rice ingest and byte-exact native restoration. Both runs passed
owned-process/listener/scratch cleanup. Normal application instances have no
profiling endpoints or wrappers.

Source commit: efa2c9297394e1ad0af2a31766371649a34669bf.
Packaged executable SHA-256: d0c6961503fa4d8416e1c3e295958c7db22ac8025b5553dc8409c92717c5d849.
[Measurement run](https://github.com/Terrazocker/nightseam-catalog/actions/runs/39014398000).
Its source, benchmark, profile and native gates passed; its initial browser gate
found the toolbar overlap corrected in the following change. JSON and logs remain
in artifact `windows-validation-efa2c9297394e1ad0af2a31766371649a34669bf` and locally
under `logs/improvements-remote-windows/logs/`.

Repeat with `python scripts/profile_import.py`; weekly/manual CI can run the same
workload. Smaller investigations accept `--count`, `--batch-size` and `--output`.
