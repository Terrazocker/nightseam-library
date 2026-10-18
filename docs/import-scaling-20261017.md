# Import boundary and controlled performance checks — October 17, 2026

## Workloads and limits

The request-local Windows index admits at most 4,096 verified archive files.
`scripts/stress_dedup.py` exercises 4,095, 4,096 and 4,097 files, with three
prepare/lookup/close cycles per population. Each synthetic FITS is a valid
5,760-byte 8×8 exposure with distinct bytes; 32 files share each parent directory.
The database is seeded directly. This isolates admission/handle scaling and
does not measure HTTP publication or realistic exposure throughput.

Preparation must hash and pin exactly the bounded population. Two negative
lookups must freshly hash every above-cap candidate, and a positive duplicate
must freshly hash its matching original. The checker also verifies exclusion
of unrelated archive operations, Windows writer exclusion, cancelled-worker
draining at hash 32, unchanged archive bytes and absence of staging leftovers.
Native handles are compared with both each cycle's baseline and the initial
warmed baseline, so small leaks cannot accumulate across cycles unnoticed.
Python heap and live RSS samples are observations; live RSS is not peak RSS,
and allocator retention alone does not establish a leak.

An initial Windows test exposed observer-owned semaphore handles: retaining a
shut-down thread-pool object retains its Python locks. The worker probe now
ends its executor scope before sampling. Observer setup is warmed first, and
a dedicated regression checks repeated observer calls. Cleanup limits were
not relaxed.

## Local algorithm stress

All nine cycles passed on Linux with the admission algorithm enabled only in
the isolated developer process and the POSIX fingerprint cache disabled.
Production POSIX pin admission remains disabled. This verifies algorithm and
descriptor cleanup; it does not establish Windows sharing/writer exclusion.

| Archived files | Pinned files | Hashes per cycle | Descriptor count before / held / after |
| --- | --- | --- | --- |
| 4,095 | 4,095 | 4,096 | 5 / 4,612 / 5 |
| 4,096 | 4,096 | 4,097 | 5 / 4,613 / 5 |
| 4,097 | 4,096 | 4,099 | 5 / 4,613 / 5 |

Held Python heap growth stayed within 30.95–31.10 MiB across repeated cycles.
Live RSS samples ranged from 81.56 to 87.64 MiB. Every archived file's hash was
verified, cancellation released each lease, and owned scratch cleanup passed.
The retained local JSON/log are `logs/scaling-linux-algorithm-stress.*`.
This local stress run preceded the observer-scope fix, which affects Windows
semaphore accounting rather than POSIX descriptor counts.

## Windows boundary stress

All nine cycles and all three cancellations passed in the complete
Windows measurement run (Actions run 39033962051 in the original private repository).
Every case retained 128 distinct parent pins. File admission stayed at 4,096
when the archive contained 4,097 candidates; repeated overflow/positive probes
retained fresh byte reads. Native writer exclusion was verified.

| Archived files | Pinned files | Hashes per cycle | Native handle counts before / held / after |
| --- | --- | --- | --- |
| 4,095 | 4,095 | 4,096 | 201 / 5,832 / 201 (all three cycles) |
| 4,096 | 4,096 | 4,097 | 201 / 5,833 / 201; 201 / 5,833 / 201; 201 / 5,832 / 200 |
| 4,097 | 4,096 | 4,099 | 200 / 5,832 / 200; 200 / 5,832 / 200; 200 / 5,828 / 196 |

Final handles after cancellation were 201, 200 and 196 respectively, at or below
the initial case baselines. All 12,288 archived-file hashes were verified and
scratch/staging cleanup passed. Held Python heap growth was 15.836–18.599 MiB;
RSS while pins were held was 82.559–97.863 MiB. First-cycle retained heap growth was
6.362–6.493 MiB as discardable fingerprints/native bindings warmed; subsequent
cycles retained 0.024–0.030 MiB each. This includes observer overhead: that
revision recreated ctypes Memory/POINTER types per sample, which the ctypes
cache retains. The sampler now reuses those types; a 200-sample Windows heap
regression guards that observer behavior. RSS did not return to its original
value and was not attributed to a particular allocator. Native handles did not
grow; three cycles do not establish the absence of long-term memory leaks.

This bounds the measured 32-files-per-directory layout. Subsequent
[directory-resource checks](import-directory-resources-20261017.md) measured
one file per parent, eight extra shared ancestors and fifty request cycles,
then reduced duplicate ancestor handles with request-owned sharing. Arbitrary
distinct deep branches, larger populations and unsupported filesystems remain
separate workloads; a file-pin cap is not a total handle cap.

## Same-machine packaged comparison

The complete measurement run passed on Windows Server 2022, two logical CPUs,
Intel64 Family 6 Model 207 Stepping 2. Baseline and candidate used the same
machine, build environment, driver and workload.

| Sample order | Build | Import seconds |
| --- | --- | --- |
| 1 | Baseline | 98.020 |
| 2 | Candidate | 55.548 |
| 3 | Candidate | 58.812 |
| 4 | Baseline | 89.795 |

Median elapsed time fell from **93.908 to 57.180 seconds (39.1%)**. Baseline
range was 89.795–98.020 seconds; candidate range was 55.548–58.812 seconds.
All four samples passed the complete byte, duplicate/edit, Rice and cleanup
audits. These are two observations per variant with uncontrolled scheduling
and warm caches; they do not promise the same improvement on other machines.

Candidate commit: `2662f12dabf90efd5056363b2c47a8ed66a41426`.
Baseline executable SHA-256: `d4824303299cabbf70174eaf6541acaff4b3a6e6db0eb323101c95c0d3f72186`.
Candidate executable SHA-256: `eeb9bd43369607c86c1c44dd70622c3ba1e8e3bedc4062a6231efcf8c303a9ce`.

Manual CI with `run_scaling_checks=true` builds the candidate and immediate
pre-lazy-admission baseline `60b30c340bc658381d728ec3a8ed0a113257fc58` on
one Windows runner with the same pinned Python environment. The benchmark driver
and dependency lock match between these revisions.

`scripts/compare_import.py` runs baseline/candidate/candidate/baseline in sequence.
Each sample imports 512 distinct 4,150,080-byte FITS over real loopback HTTP,
24 per request, into fresh private stores. Startup, fixture generation, audits
and duplicate/edit probes stay outside the timed import. All samples must pass
source/archive byte checks, identity/class/session/location preservation,
restored-mtime edit detection, Safe Rice/native restoration and owned cleanup.
Executable hashes, all timings, ranges and medians are retained.

The same-machine design removes the earlier AMD/Intel mismatch. OS scheduling
and warm caches remain uncontrolled; two samples per variant are a small
observation, not a general speed guarantee. No timing threshold determines
correctness. Retain every sample rather than selecting the fastest result.

The repeated source profile still measured 6,034 archive hashes and
25,041,582,720 bytes, 5,233 SQL SELECTs and 512 BEGIN/COMMIT pairs. It took
68.863 instrumented seconds, separately from packaged timing. Linux passed
857 source tests (6 skips), Windows 861 (2 skips), all six required Windows
contracts passed, browser 24/24 and restricted native window 7/7 passed.
Artifacts are `windows-validation-2662f12dabf90efd5056363b2c47a8ed66a41426`
and locally `logs/scaling-windows-measurements/`. Comparison JSON and all four
per-sample reports/logs are retained under its `logs/` directory.

## Remaining desktop acceptance

The Windows workflow prepares `windows-acceptance-kit.zip` containing the fresh
candidate executable, three synthetic FITS, a SHA-256 manifest/verifier,
licenses, procedure and an unrun result template. This prepares reproducible
inputs; it does not claim a clean/offline VM, physical DPI/monitor/Snap or
native Save As pass. Follow [the acceptance procedure](windows-manual-acceptance.md)
on a suitable disposable VM and physical desktop.

The generated kit's SHA-256 is
`fbc5126448b54468d1b5c8782faac1144ee5de2c5d274b845b769a7baf8988e4`.
Its manifest/fixture/executable hashes were independently checked after download,
and its executable matches the measured candidate above. All checklist results
remain `not_run`.
