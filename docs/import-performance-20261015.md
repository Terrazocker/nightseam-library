# Import performance investigation — October 15, 2026

## Follow-up: representative HTTP workload

The initial 48-file direct benchmark below missed the severity of metadata
scaling and excluded multipart handling and import-history commits. A subsequent
512-file test reproduced the user's slow-import behavior through the real HTTP
endpoint: **2,124,840,960 bytes took 168.51 seconds**. The last full 24-file batch
took **15.78 seconds**, compared with 1.42 seconds for the first batch.

The final synthetic workload takes **24.58 seconds (6.9× faster)**. Its first
batch took 1.19 seconds and its last full batch 1.35 seconds. An intermediate
run measured 26.25 seconds. Each file still
publishes in selection order, and existing duplicate contents are verified.
These local single-run measurements are not cold-cache-controlled hardware
throughput guarantees. Raw batch timings are retained under
`logs/pipeline-performance/baseline-import/`, `optimized-import/`, and `final-import/`.

The additional changes are:

- Validate earlier same-size files once per HTTP batch, retaining real Windows
  read/share pins and directory pins while a request-local negative index is
  used. Unrelated archive operations wait for the lease; positive matches still
  hash. Open writers, unsupported versions and entries beyond the 4,096-file
  handle bound keep conservative checks. Pins use GENERIC_READ: behavioral tests
  showed that attribute-only handles did not exclude concurrent writes/renames.
- Combine image, integrity, compression alias and import-history changes in one
  short transaction per file. A history failure rolls back the image and its
  published file too. Use 24-file browser batches for plain and compressed input.
- Serialize HTTP import admission asynchronously so waiting requests do not fill
  the filesystem executor with workers blocked on the owning request's lease.

Manual session conversion was a separate serial path. It now uses bounded
parallel preparation and actual file publication, unique verification filenames,
reused live directory pins, and source-version checks while source bytes are
pinned. Unsupported versions retain full hashes. The complete plan is journaled
before swaps; every worker drains before rollback/cleanup. Originals remain
retained until the catalog commits. Safe performs the exact restored hash check;
Fast skips that round trip. Uncompress always checks a known original hash.

Final complete-session timing on the same 48 generated 4.15 MB files:

| Session conversion | Elapsed including swaps, commit and cleanup |
| --- | ---: |
| Previous serial Safe path | 13.27 s |
| Final parallel Safe path | 2.50 s |
| Final parallel Fast path | 2.00 s |

These session measurements include cProfile on the coordinator and use the same
synthetic input distribution and local drive. They do not establish the timing
on the user's other disk. Evidence is under `logs/pipeline-performance/` in
`baseline-session`, `final-session-safe`, and `final-session-fast`.

The dialog reports preparation and writing/replacement as separate real counters,
then catalog update, original cleanup and library refresh. Full bars are not a
terminal signal. The completed UI state appears after refresh and unlocks the
newly rendered session actions. The earlier small measurements below are retained
as historical evidence rather than the current large-import acceptance result.

The slow plain-import path repeatedly hashed every earlier same-size archived
exposure for every new exposure. A 48-file private synthetic workload therefore
read 1,128 earlier files: 4,681,290,240 bytes to import 199,203,840 bytes.
Compression also prepared one file at a time under the archive lock. Each HTTP
upload chunk rebuilt the full catalog resolver, and each conversion rehashed both
bundled native tools.

## Measurement

All files were generated for this test: 48 different uint16 images, 1080 × 1920,
each 4,150,080 bytes. Pixels have a deterministic noise distribution around 4096
with standard deviation 60. The first eight pixel bytes distinguish each file.
Catalogs and archives were isolated under ignored `logs/import-performance/`.
No supplied example files or application stores were used.

| Direct ingest workload | Before | After | Change |
| --- | ---: | ---: | ---: |
| Plain FITS, ordered publication | 12.70 s | 2.83 s | 4.5× faster |
| Fast Rice | 6.56 s, serial preparation | 1.66 s | 3.9× faster |
| Earlier archive hashes, plain FITS | 1,128 | 47 | 95.8% fewer |
| Earlier archive bytes read, plain FITS | 4.68 GB | 195 MB | 95.8% fewer |

These are local wall-clock measurements, not a storage throughput guarantee.
They include streaming, hashing, resolution, conversion, publication and SQLite
commits; the resolver was constructed once before each direct-ingest phase. They
exclude HTTP multipart parsing and therefore do not measure the additional
benefit of reusing that resolver across browser upload chunks. There was no
cold-cache control. Final preparation concurrency was 12, matching this machine's
logical CPU count; the browser's plain batches contain only eight files. Initial
intermediate measurements were 3.51 seconds plain/sequential and 2.85 seconds
Rice/four workers. Windows filesystem validation required an unrestricted test
process for the optimized measurements; restricted sandbox resolution overhead
was measured separately and is not representative of the packaged application.

Raw summaries and profiles remain under `logs/import-performance/baseline/`,
`optimized/`, `optimized-parallel/`, and `final/`. Parallel per-file latency sums
overlap; only elapsed_seconds is wall-clock duration. The `after-cache/` exploratory run was
restricted and concurrent with tests; its timing is not used above. Profiling
source remains `logs/import-performance/profile_ingest.py`; disposable archive
payloads can be removed while preserving summaries and profiles.

## Changes and preserved behavior

- Memoize hashes actually read from nonmatching archived files. On NTFS/ReFS,
  a pinned metadata handle checks file identity, size, last-write time and native
  change time. A changed file invalidates the memo even if mtime was restored.
  Potential positive matches still read the actual complete bytes. Unsupported
  filesystems and open writers fall back to current-byte hashing.
- Prepare uploads and optional Rice conversion in bounded concurrent workers,
  up to CPU count and at most 24. Compression batches contain up to 24 uploads;
  plain batches retain eight. Publication remains ordered and serialized under
  the independent archive lock. Hashing/copying does not hold the SQLite lock.
- Check known duplicates against current bytes before invoking the encoder.
  Final publication rechecks deduplication, session anchors and archive state.
- Preserve Fast/Safe verification, strict missing-file representation recovery,
  publication verification and rollback. A database failure stops later
  publication; cancellation drains workers and removes owned staging.
- Reuse the catalog resolver across chunks until catalog changes invalidate it;
  memoize repeated target/coordinate resolutions within each snapshot. Run
  matching outside the event loop. Cache tool validation behind strong versions
  of both executables and their provenance. Add indexes for compression aliases
  and import-run history merging.

## Remaining limits

Plain equal-size archives still require candidate metadata inspection to detect
external edits with different cached digests. This metadata work remains
quadratic. The negative cache admits up to 20,000 paths and keeps those entries
useful rather than evicting them during an oversized ascending scan. Additional
candidates are read afresh. Positive duplicate verification, durable publication
and database commits also impose real I/O costs. Rice-compressed archives usually
have variable sizes, avoiding the large equal-size candidate population.

The improvement removes redundant full-file reads and serial Rice preparation;
it does not establish that every filesystem/workload will saturate all CPU cores
or its disk. See `BUGS.md` for the remaining plain-file scalability limitation.
