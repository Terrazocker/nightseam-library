# Populated-library lookup and cancellation — October 17, 2026

## Workload and provenance

`scripts/profile_populated_import.py` starts an isolated source server with
6,000 pre-existing synthetic integer FITS. It seeds three through actual verified
Rice conversion and the remaining rows/files directly. There are three seed
objects, four capture dates, light/dark frames and four stored file sizes;
4,797 files have the common 5,760-byte size, exceeding the 4,096-file pin cap.
Seeding, startup, one audited resolver warmup and final audits are outside the
measured HTTP waves. The restoration control owns a complete one-image session.

The controller uploads duplicates, eight new plain exposures, three new safe-Rice
exposures, mixed representation repeats, malformed input between valid files,
a same-size archive edit with restored mtime, and a missing original. It also
restores a newly compressed original through the real asynchronous native API.
All input bytes, 6,012 final archive hashes, seed identity/class/session/location,
12 new image/history units and cleanup are audited. The deliberate edit may
refresh its cached fingerprint; it does not accept a new integrity baseline.

The baseline application is commit `45d19fa9c96b6dc75069f31cbdc0f8dd7f1bbc91`.
It ran in an owned detached worktree with the same measurement controllers copied
in; those controllers add no production application hooks. That worktree and
all child stores were removed afterwards. Candidate measurements used this
change's source, with the added verified-hydration path check covered separately
by targeted tests and final CI. Local evidence:

- `logs/populated-baseline.json` and `.log`;
- `logs/populated-candidate-final.json` and `.log`;
- `logs/populated-query-work.json`;
- `logs/populated-final-regressions.log`.

Both HTTP profiles used Linux/glibc 2.41, Python 3.12, SQLite 3.53.1 and the same
three-logical-CPU environment. Caches were warm/uncontrolled and source paths
differed. Instrumentation overhead is not subtracted; native child CPU is not
measured. Static-page response samples are observations during submission, not
a tail-latency guarantee. These small-file source profiles are not a controlled
packaged throughput comparison or a realistic large-exposure disk benchmark.

## Measured work

The totals cover the seven HTTP upload waves, excluding seed/warmup/audits and
the separate native-restoration job.

| Measurement | Baseline | Candidate |
| --- | ---: | ---: |
| SQL SELECT statements | 58,064 | 493 |
| `Database.image()` metadata lookups | 57,609 | 10 |
| Regular candidate visits | 57,609 | 57,609 |
| Completed ingest archive hashes | 6,012 | 6,012 |
| Ingest archive bytes hashed | 57,110,400 | 57,110,400 |
| SQL BEGIN / COMMIT | 14 / 14 | 14 / 14 |

SELECTs fell 99.2%; the search did not drop candidates or skip existing hash
checks. `Database.image()` counts are that method's full-row queries, not every
metadata row returned by SQLite. Native Rice verification has separately timed
phases and is outside the ingest hash-byte counters.

A separate in-memory 50,000-row metadata comparison held the same five-column
projection and returned the same 38,235 ordered candidate rows. Approximate
executed SQLite VM instructions fell **46,439,000 → 1,236,000 (97.3%)**. The old
OR query repeatedly sorted remaining matches for each page; ordered UNION
streams reused the existing SHA/size indexes and merged with duplicate removal.
Progress callbacks sample every 1,000 VM instructions; this is SQL work, not
whole-application elapsed time. The permanent regression uses a broad work
ratio rather than a wall-clock threshold.

## Changes and edge cases

Preparation and lookup use ordered indexed streams. UNION removes overlapping
hash/size/unknown-size hits without changing ID order. Pages remain bounded,
carry a fixed upper ID and close their cursors/DB locks before hashing.
Preparation needs only ID/path; ordinary lookup adds the three fingerprint
fields. Only a verified positive loads the full image record, and that record's
path must still match the path actually checked.

Compression alias and exact-positive tiers are paged and lazy. A successful
alias need not fetch the exact tier. A failed alias may still check the same
image against its exact byte identity: their expected hashes can differ.
Alias overlap, late pages and insertions above the initial boundary are tested.

Cancellation now reaches original/compressed alias reads, exact-positive
compression preflight and non-batched fallback hashes. Repeated task cancellation
waits for workers before removing stages; cancelled duplicates never invoke an
encoder or change the original row/mapping. Four real-read cancellation cases,
late positives above admission capacity, path changes during hydration and
the unchanged writer/lease/rollback contracts are guarded by tests.

The request's file/handle limits, positive whole-file verification and conservative
Windows cross-lifetime reads remain intact. Candidate visits still scale with
the matching population. The changes reduce avoidable SQL/sorting/row loading;
they do not remove that fundamental traversal or guarantee cold-disk throughput.

## Repeat

Run `python scripts/profile_populated_import.py --count 6000`. Output defaults to
`logs/populated-import-profile.json`. A smaller 24-image integration case runs in
the source suite; the Windows Archive resource checks workflow runs all 6,000
with actual native request pins and retains `resources-populated.json`.
The ordinary 512-file benchmark remains separate and unchanged.
