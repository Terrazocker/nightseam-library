# Native Rice compression and exact FITS restoration

October 15, 2026. Follow-up to the example-data compression benchmark.

**Yes: native `fpack` followed by `funpack -C` restored all 2,057 supplied FITS
files byte-for-byte, with matching complete-file SHA-256 hashes. No header
sidecar was saved, supplied to the decoder or used to repair its output.**

My earlier suggestion that an additional saved header would be necessary was
too broad. The encoder and decoder options matter. This experiment establishes
exact restoration for this collection with the tested native utilities.

## Commands and results

```text
fpack -r -O packed.fit.fz original.fit
funpack -C -O restored.fit packed.fit.fz
```

`fpack` performs Rice compression; `funpack` decompresses. The decoder received
only the compressed file, its output path and its options. Python computed
hashes and inspected metadata, but never rewrote the decoded FITS file.
Every compressed output was independently checked for `ZIMAGE` and
`ZCMPTYPE='RICE_1'`.

| Experiment | Files | Original full-file SHA-256 matched | Raw image payload matched |
|---|---:|---:|---:|
| Default fpack Rice + default funpack | 32 | 0 | 32 |
| Default fpack Rice + funpack `-C` | 32 | 32 | 32 |
| fpack Rice `-C` + default funpack | 32 | 0 | 32 |
| fpack Rice `-C` + funpack `-C` | 32 | 32 | 32 |
| Whole-image fpack Rice `-w -C` + funpack `-C` | 32 | 32 | 32 |
| **Default fpack Rice + funpack `-C`, entire collection** | **2,057** | **2,057** | **2,057** |

The sample comprised 16 distributed frames from each supplied observing night.
The complete run covered all 817 IC_5146 frames and all 1,240 NGC7000 frames.
These are unsigned 16-bit Seestar S30 Light images, 1080 by 1920 pixels, with
`BITPIX=16`, `BZERO=32768` and `BSCALE=1`.

The complete run compressed **8,542,638,720 bytes (7.956 GiB)** into
**4,698,034,560 bytes (4.375 GiB)**: **45.00% space saved**. This total includes
each individual FITS compressed container, before any outer session archive.
The earlier full ZIP/Bzip2 archive was smaller at 4,299,845,611 bytes (4.005 GiB,
49.67% saved). Rice therefore restores exactly with this workflow, but did not
win the storage comparison on these exposures.

## Why the `-C` option matters

The [official fpack/funpack guide](https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/docs/fpackguide.pdf)
defines `-C` as suppressing FITS checksum updates. Default funpack added or
updated `CHECKSUM` and `DATASUM` values in all 32 default-mode sample outputs;
their complete-file hashes changed even though their raw pixel payloads matched.
Putting `-C` on fpack alone did not prevent that decoder behavior.

With `funpack -C`, the entire original file matched, including header cards,
ordering, comments, image bytes and padding. CFITSIO's tiled FITS representation
retains original header information inside the compressed FITS itself; this
does not require a separate header backup. The checksum options do not disable
our external SHA-256 comparison. Default funpack need not alter every possible
FITS file; the table reports the actual outcomes on these samples.

## Why the earlier Astropy result differed

The earlier FITS codec benchmark used Astropy 7.2.2 / NumPy 2.5.4 and verified
decoded pixel dtype and bits. It did not perform a native full-file restoration
test. This follow-up also generated two compressed files through that earlier
Astropy workflow, one from each night, and decoded them with native `funpack -C`.
Both retained exact pixels but failed the original complete-file hash:
`EXTEND`, several structural-keyword comments and keyword ordering differed.

Thus native fpack's result cannot be generalized to every Rice encoder. Rice
pixel losslessness and exact reconstruction of the original FITS serialization
are separate properties that need their own verification.

## Reproducibility and limits

Unmodified official CFITSIO **4.7.0** and zlib **1.3.2** were compiled privately
with the existing Microsoft compiler. Both utilities report
`1.7.0 (Dec 2013) CFITSIO version 4.070`; the utility version string is distinct
from the CFITSIO library version. Sources:
[NASA CFITSIO 4.7.0](https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/cfit-4.7.0.zip)
and [official zlib 1.3.2 tag](https://github.com/madler/zlib/releases/tag/v1.3.2).

Evidence is retained under `logs/fpack-roundtrip-20261015/`:

- `full-results.jsonl`: all 2,057 original/restored hashes, sizes, codec checks
  and actual command arguments; `full-summary.jsonl` aggregates the results.
- `sample-results.jsonl` and `sample-summary.jsonl`: the five option comparisons.
- `astropy-funpack-results.json`: the two separate Astropy-encoder probes.
- `source-audit.json`: final independent rehash, membership, size and mtime audit.
- `downloads.json`, `tool-build.json`, official source archives, executable
  hashes, build logs, harnesses and `script-hashes.json`: tool provenance.
- `cleanup.json`: removal of experiment build scratch and temporary copies.

Original source files were never overwritten. Compressed and restored test
copies were deleted after comparison. No application dependencies, installed
application executable, catalog data or archive features were changed by this
experiment. The privately built utilities and reproducibility evidence remain.

This was a functional exactness test. Recorded process times include launching
utilities per file and concurrent workspace tests; they should not be compared
with the earlier controlled compression timings. No guarantee is inferred for
other FITS layouts, arbitrary trailing bytes, other tool versions or floating
point images. In particular, Rice compression of floating point input may
quantize values and needs a separate losslessness assessment.

For NightSeam, the bytes of a compressed stored file still have a different
SHA-256 from its original uncompressed input. Any future ingest conversion must
account for both stored-file integrity and original-file identity. This native
round trip demonstrates that an external header sidecar is unnecessary for the
tested integer files; no ingest conversion has been implemented.

Workspace verification: **729 tests passed, 1 skipped in 257.06 seconds** using
the pinned build environment. Encoding and whitespace checks and workspace
housekeeping are recorded in the release record.
