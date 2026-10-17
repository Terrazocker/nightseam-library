# Windows import profile — October 17, 2026

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
