# Real FITS compression benchmark — October 15, 2026

For these Seestar S30 light frames, use **ZIP with Bzip2 Normal** when the
priority is saving space. Use **Zstd level 6** for fast archiving and restoration.
LZMA2 did not provide a useful advantage over Bzip2 in the tested configurations.
This replaces the earlier provisional preference for 7z/LZMA2 with measured evidence.

The supplied collection is **7.956 GiB** (8,542,638,720 bytes). The complete
ZIP/Bzip2 run produced **4.005 GiB**, saving
**49.67%**, versus **4.596 GiB**
and **42.23%** for session-stream Zstd6. On this collection,
Bzip2 saves another **0.592 GiB**.

## Complete collection: measured results

These are actual full-data runs, **one repetition each**, not extrapolations from
the smaller screening set. Creation includes reading the input and writing the
compressed output. Restoration includes writing every restored file. SHA-256
verification is additional. Archive hashing, cleanup and independent decoder
probes are outside these three timing columns. There is no forced disk flush.

| Measured full collection | Compressed GiB | Space saved | Create s | Restore s | SHA verify s |
| --- | --- | --- | --- | --- | --- |
| ZIP / Bzip2, Normal, independent entries | 4.005 | 49.67% | 202.3 | 134.1 | 28.8 |
| TAR / Zstd level 6, session stream | 4.596 | 42.23% | 47.3 | 37.1 | 30.2 |
| Zstd level 6, independent files | 4.602 | 42.16% | 147.7 | 31.8 | 30.4 |

| Supplied session folder | Files | Original GiB | ZIP/Bzip2 GiB | TAR/Zstd6 GiB |
| --- | --- | --- | --- | --- |
| IC_5146 | 817 | 3.160 | 1.585 | 1.829 |
| NGC7000 | 1240 | 4.796 | 2.420 | 2.767 |

Every restored file in all three runs matched its original complete-file SHA-256
and size; restored relative-path membership also matched. ZIP entries were checked
to use Bzip2 method 12. Four ZIP entries (first/last in each session) were also
decoded and hash-checked with Python's independent ZIP/Bzip2 reader.

## Compress each file, or the whole session?

**Package a session as one archive, but independent compression of its files is
perfectly reasonable.** A single archive is an organizational choice; it does
not require one giant compression stream.

In the full Zstd6 comparison, session-stream archives were
**0.127% smaller relative to the sum of independent Zstd files**,
a difference of **6.00 MiB**.
That is a small difference for this collection. Standalone per-file results do
not include an additional enclosing archive; TAR/session results include TAR
headers and padding, so the comparison measures these practical layouts.
Logical file sizes are measured; filesystem allocation overhead is excluded.
Stream/file timing differences also include worker utilization and repeated codec
setup, not just finding repetition between exposures.

In the 32-file sample, per-entry ZIP/Bzip2 (Python implementation) was slightly
smaller than TAR/Bzip2. Solid LZMA2 saved about 1.8% relative to non-solid LZMA2
with the tested nominal dict64 settings, but consumed substantially more time
and memory. There is no measured basis for requiring a giant solid stream here.

The FITS headers occupy only 0.139% of the original files. Repeated header text
cannot explain large space savings. Repeated image bytes can help only when
the compressor can find them within its history; independent exposure noise
limits cross-file repetition. Both supplied observing nights cross UTC midnight,
so archive membership should follow stored sessions, not a date-folder split.

## Data and screening protocol

Source: a local `Example-data` folder, read-only.
There are 2,057 unique complete-file hashes, all uncompressed `.fit`, exactly
4,152,960 bytes each. Each has one primary image: unsigned 16-bit, 1080 × 1920,
BITPIX=16, BZERO=32768, BSCALE=1, Bayer GRBG, Seestar S30, 10-second Light,
LP filter, gain 200. No dark/flat/bias, floating-point stack, or already compressed
examples were supplied. Results are specific to this data, not all FITS.

Screening used **32 files / 126.74 MiB**, 16 evenly spaced exposures from each
session, including its beginning and end. This is a coverage sample, not a
contiguous run of adjacent exposures. **37 byte-exact configurations were run
twice**, with deterministic shuffled execution order within each batch; times
below are medians. Output sizes repeated identically. The full-data runs above
include all intervening exposures and establish the actual Zstd session result.

Hardware: AMD Ryzen 5 5600X, 6 cores / 12 logical processors, approximately
32 GiB RAM, Windows. Input and output share G:. OS cache was allowed; it was not
flushed. No other benchmark workload ran concurrently, but this was a live
workstation, not an isolated performance lab. Times are indicative and do not
predict cold external-drive performance.

Python 3.14.7 uses Zstd 1.5.7; installed 7-Zip is 26.01. Creation requests four
workers for Zstd, 7-Zip and Blosc2 where supported; actual utilization varies.
Native 7-Zip restoration uses its default worker policy; Blosc restoration requests
four workers, and ordinary Zstd stream decoding is serial. Python's
Gzip/Bzip2/ZIP baselines are single-threaded. These are practical implementation
comparisons, not an equal-core CPU contest. Native 7-Zip Bzip2 Normal and Python
libbz2 level 9 have different setting names; their measurements are shown
separately. Levels are not comparable across codecs or implementations.

## Ordinary archive screening results

All methods in this table preserve complete original file bytes. `dict64` and
`dict4` mean explicit 64 MiB and 4 MiB dictionaries; `solid128` means solid blocks
capped at 128 MiB. These are explicit settings, not untouched 7-Zip presets.
`tuned16` sets LZMA2 lc=0, lp=1, pb=1. `long128` enables Zstd long-distance
matching with window_log=27. Complete variants, repetitions, times, sizes and
per-session byte totals are retained in the CSV/JSON evidence.

| 32-file sample | MiB | Saved | Create s | Restore s | Peak process MiB |
| --- | --- | --- | --- | --- | --- |
| ZIP / Deflate 6 | 77.76 | 38.64% | 2.33 | 0.47 | 31 |
| TAR / Gzip 6 | 77.75 | 38.65% | 2.29 | 0.53 | 32 |
| ZIP / Bzip2 Normal, native 7-Zip | 63.82 | 49.64% | 2.85 | 2.17 | 49 |
| ZIP / Bzip2 Ultra, native 7-Zip | 63.79 | 49.67% | 31.76 | 2.19 | 51 |
| ZIP / Bzip2 9, Python libbz2 | 63.82 | 49.64% | 8.16 | 3.84 | 35 |
| TAR / Bzip2 9, Python libbz2 | 63.84 | 49.63% | 7.39 | 3.83 | 35 |
| 7z / LZMA2 5, independent entries, dict64 | 65.37 | 48.42% | 15.21 | 1.68 | 58 |
| 7z / LZMA2 5, solid128, dict64 | 64.20 | 49.34% | 57.97 | 2.01 | 649 |
| 7z / LZMA2 7, solid128, dict64 | 64.21 | 49.34% | 57.22 | 1.83 | 649 |
| 7z / LZMA2 5, solid128, dict4 | 65.04 | 48.68% | 17.66 | 0.65 | 148 |
| 7z / LZMA2 1, solid128, dict4 | 68.64 | 45.84% | 10.12 | 0.65 | 204 |
| 7z / LZMA2 3, dict4, tuned16 | 68.47 | 45.98% | 9.68 | 0.65 | 204 |
| TAR / Zstd 3 | 76.13 | 39.93% | 0.38 | 0.40 | 141 |
| Zstd 6, independent files | 73.44 | 42.05% | 1.90 | 0.38 | 41 |
| TAR / Zstd 6 | 73.36 | 42.12% | 0.58 | 0.45 | 148 |
| TAR / Zstd 12 | 72.58 | 42.73% | 4.90 | 0.51 | 360 |
| TAR / Zstd 12, long128 | 72.80 | 42.56% | 7.80 | 0.52 | 296 |
| TAR / Zstd 19 | 69.10 | 45.48% | 40.84 | 0.57 | 356 |

Peak memory is the maximum observed process working set over creation/restoration/
verification, including interpreter/import overhead. For native 7-Zip it is the
larger of host and child peaks, not their simultaneous sum. It is approximate
process memory, not codec-only memory or a total system requirement.

Normal native Bzip2 produced almost the same size as Ultra while avoiding most
of its extra processing. Zstd19 and long-distance matching did not justify their
cost here. ZIP/Deflate remains a compatibility option with lower space savings.
ZIP/Bzip2 is supported by Python 3.12's standard-library ZIP reader and 7-Zip, but some other
ZIP tools do not support that method; a `.zip` extension alone does not guarantee
universal extraction. [Python ZIP compatibility documentation](https://docs.python.org/3.12/library/zipfile.html)

## Modern scientific compression and reversible filters

Zstd is a strong modern speed/ratio choice, not a promise of the smallest output
on every dataset. Shuffling can expose structure in typed binary data before
compression. Established scientific libraries provide these filters; this study
also tests simple reversible prototypes. [Zstd project](https://facebook.github.io/zstd/),
[Blosc overview](https://blosc.org/pages/blosc-in-depth/)

All following results are **sample only**, two repetitions. Every restored file
matched its original SHA-256, including headers and padding. However, these are
**specialized representations**, not ordinary TARs of usable FITS: the inverse
shuffle or Blosc decoder must run before the original FITS bytes reappear.
An ordinary decompressor alone is insufficient. They would need a specified,
self-describing format and supported recovery tooling before production use.

`shuffle2` is a whole-file even/odd byte split with unchanged length.
`bitshuffle2` is an experimental whole-file 16-bit bit-plane transpose implemented
with NumPy; it is not the optimized blocked Bitshuffle implementation. Both
preserve any tail bytes and were inverse-tested on short even/odd/empty inputs.
Blosc2 was tested separately using its real native blocked filters, typesize=2,
default block selection and four workers. Blosc2 4.14.1 / C-Blosc2 3.3.5 embeds
Zstd 1.5.7 and LZ4 1.10.0. Its chunks are stored independently in a TAR per session;
these sizes include TAR overhead. Blosc clevel6/9 are its own levels, not literal
Zstd levels 6/9. Byte shuffle helped this data; bit shuffle did not win.

| Specialized reversible format, sample only | MiB | Saved | Create s | Restore s |
| --- | --- | --- | --- | --- |
| blosc2-lz4-5-bitshuffle | 82.32 | 35.04% | 0.13 | 0.18 |
| blosc2-lz4-5-shuffle | 93.83 | 25.97% | 0.15 | 0.20 |
| blosc2-zstd-6-bitshuffle | 75.61 | 40.35% | 0.32 | 0.19 |
| blosc2-zstd-6-shuffle | 68.13 | 46.24% | 0.95 | 0.22 |
| blosc2-zstd-9-bitshuffle | 73.77 | 41.79% | 6.16 | 0.23 |
| blosc2-zstd-9-shuffle | 66.98 | 47.15% | 10.70 | 0.23 |
| zstd-12-bitshuffle2-files | 73.89 | 41.70% | 3.11 | 2.10 |
| zstd-12-bitshuffle2-stream | 74.01 | 41.60% | 2.37 | 2.08 |
| zstd-12-shuffle2-files | 68.10 | 46.27% | 4.60 | 0.44 |
| zstd-12-shuffle2-stream | 68.06 | 46.30% | 1.59 | 0.52 |
| zstd-6-bitshuffle2-files | 73.94 | 41.66% | 2.05 | 1.80 |
| zstd-6-bitshuffle2-stream | 74.01 | 41.60% | 1.93 | 2.04 |
| zstd-6-shuffle2-files | 68.77 | 45.74% | 1.32 | 0.49 |
| zstd-6-shuffle2-stream | 68.78 | 45.73% | 0.43 | 0.45 |

These tests provide no space-saving reason to introduce a specialized filtered
archive instead of Bzip2 for these examples. They are useful speed options, but
carry a more involved recovery requirement than ordinary session archives.

## FITS-specific compression: encoder and restoration behavior

Astropy 7.2.2 / NumPy 2.5.4 tested Rice, Gzip1, Gzip2 and lossless Hcompress,
both default tiles and one tile covering the entire image. Each variant was run
twice on the same 32 source files. `q0` disables quantization; Hcompress scale0
is lossless. All decoded pixel dtypes/bits matched and no warnings were recorded.

These Astropy tests verified **pixel-exact** restoration, without testing
complete-file restoration. Their timings are in-memory encode/decode, with source file loading
excluded, and cannot be compared directly with the disk-I/O archive timings.
No lossy compression was tested. [NASA FITS compression overview](https://heasarc.gsfc.nasa.gov/docs/software/fitsio/fpack/fpack.html)

A later [native fpack/funpack round-trip test](<fpack-funpack-roundtrip-20261015.md>)
restored **all 2,057 original full-file SHA-256 hashes**, using `fpack -r` and
`funpack -C`, without a saved header or output repair. It compressed the complete
collection to 4,698,034,560 bytes (45.00% saved). Two files encoded through the
earlier Astropy workflow and decoded with `funpack -C` retained exact pixels but
changed original header serialization. Exact reconstruction depends on the
encoder and decoder options; Rice itself does not require a header sidecar.

| FITS rewrite, sample only | MiB | Saved | In-memory encode s | In-memory decode s |
| --- | --- | --- | --- | --- |
| GZIP_1_q0 | 88.47 | 30.19% | 6.17 | 1.96 |
| GZIP_1_q0_whole_image | 79.91 | 36.95% | 15.26 | 0.79 |
| GZIP_2_q0 | 78.01 | 38.45% | 12.43 | 3.16 |
| GZIP_2_q0_whole_image | 67.73 | 46.56% | 138.03 | 1.30 |
| HCOMPRESS_1_scale0 | 67.93 | 46.40% | 2.16 | 2.02 |
| HCOMPRESS_1_scale0_whole_image | 67.03 | 47.11% | 2.46 | 2.34 |
| RICE_1 | 69.88 | 44.86% | 1.82 | 1.17 |
| RICE_1_whole_image | 69.29 | 45.33% | 0.79 | 0.58 |

For this data, the FITS-specific settings tested did not establish an advantage
over the ordinary Bzip2 archive. Other tile settings/data could differ. Original
file hashes are NightSeam's identity contract, so FITS conversion would require
separate provenance handling; it is unnecessary for the session-archive feature.

## Recommended feature configuration

- **Smaller archive:** one ZIP per selected session, Bzip2 Normal (native command
  tested: `7z a -tzip -mm=BZip2 -mx=5 -mmt=4 ...`). Files compress independently.
- **Fast archive:** one TAR/Zstd archive per selected session, level 6, checksum
  enabled, up to four workers. Restore through the application or a compatible tool.
- Include original files and a versioned metadata/SHA-256 manifest. Preserve exact
  stored membership and calibration anchors. Do not recompress FITS pixels on ingest.
- Verify all decompressed file hashes before reporting success. Keep originals;
  any later removal/offloading needs an explicit separate operation and restore flow.

The native 7-Zip executable used for the full Bzip2 timing is installed on this
machine; it is not currently a bundled NightSeam dependency. Shipping that backend
would be a deliberate packaging choice. Python 3.12 can create/read Bzip2 ZIPs
without an added compression dependency, but its single-threaded sample timing
was slower. Do not promise the measured native timing for a Python-only backend.
Session-archive creation/restoration is a proposed feature, not implemented by
this benchmark.

This benchmark covers exposure files, not a complete catalog backup. It did not
read the live catalog database, profile archive, settings, or packaged data.
The application and its dependency environment were not changed. Benchmark-only
Blosc2 dependencies were isolated under ignored logs; no installation into the
application environment occurred. All generated archives/restored copies were
removed after successful verification, so this experiment did not create a retained backup.

## Verification and reproducibility evidence

Final source audit: all **2,057 source hashes, sizes, modification times and path
membership matched the initial baseline**. The 74 general sample runs verified
2,368 complete-file roundtrips; the three full runs verified 6,171 more. The
16 FITS codec sample runs verified 512 pixel-exact roundtrips separately.

Evidence directory: `logs/example-compression-benchmark-20261015`. It contains the baseline filename/size/
mtime/SHA manifest, source audit, full and sample JSONL results, `summary.csv`,
per-session compressed sizes/archive hashes, native tool logs, FITS codec results,
hardware/version details and benchmark scripts. Temporary setup failures were
corrected and excluded; all reported runs completed their verification and cleanup.

An [aggregate archive CSV](<example-data-compression-benchmark-20261015.csv>)
is saved alongside this report. The FITS-specific pixel-codec tables above have
separate JSON evidence because their in-memory timing contract differs.

Run ordinary benchmarks using Python 3.14 (the application build remains 3.12).
Run `fits_codecs.py` using the pinned build environment. Blosc2-only dependencies
and exact versions are recorded in `blosc2-benchmark-requirements.txt`. The saved
manifest fixes the sample; reruns must use fresh result/scratch paths to avoid
mixing repetitions or overwriting evidence. There are no performance extrapolations
from sample sizes into the full-data result table.
