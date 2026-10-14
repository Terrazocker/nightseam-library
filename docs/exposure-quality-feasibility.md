# Exposure quality and deletion feasibility

Investigated October 13, 2026. Quality scoring remains a proposed feature. The preview, session expansion and Trash application features were retired on October 14; DEVELOPMENT.md documents recovery of previously retained files.

## What is possible

A local advisory quality score beside each exposure is feasible. Start with measurements and short reasons, allow a manual rating or rejection override, and show “not assessed” when analysis cannot produce reliable measurements. A score should help review; it should never delete an exposure automatically.

Siril documents frame measurements including star count, FWHM, roundness, and sky background, with star count used in its weighted FWHM criterion. These are useful precedents for frame review. [Siril registration plots](https://siril.readthedocs.io/en/latest/Plot.html).

Siril's PSF analysis measures stellar width, roundness and local background, and warns that automatic detection can mistake other bright structures for stars. Detection parameters affect the results. [Siril image analysis](https://siril.org/tutorials/dynamic-psf/).

Suggested NightSeam measurements:

- Robust background level/noise, clipped-pixel fraction, and spatial background variation.
- Detected star count, stellar FWHM and roundness, with saturated stars and unsuccessful fits excluded.
- Changes relative to nearby comparable light frames, with individual measurements, confidence and reasons retained alongside any combined score.

Comparisons require the same target/field, camera, filter, exposure, gain and binning where known. A capture session alone does not establish comparability. Missing metadata should reduce confidence rather than silently treating frames as equivalent. Field rotation, dithering and changing sky conditions also need consideration.

Sudden star loss or nonuniform attenuation could flag possible clouds or obstruction. **Cloud and foliage detection are proposed heuristics**, not capabilities established by the cited measurements. Clouds, branches, nebulae, vignetting, gradients and changes in focus can overlap in appearance; a numerical score cannot reliably identify the cause without validation. Label a finding “possible obstruction” rather than claiming a tree was detected.

## Data and performance limits

Use original linear FITS pixels for analysis, with bounded reading, representative regions and an explicit sampling scale. The displayed PNG is downsampled and independently stretched: it is suitable for visual review, but its contrast, star visibility and stellar widths do not preserve absolute raw measurements. Thumbnail resolution is insufficient for reliable FWHM analysis.

Raw Bayer data needs a consistent channel/sampling strategy. Calibration state, hot pixels, exposure and camera settings affect comparisons. Dark, flat and bias frames require their own checks; a light-frame star score is inappropriate. Planetary/solar exposures and crowded or extended fields need separate criteria or “not assessed.”

Astropy, NumPy and Pillow are already dependencies. There is no current star-measurement engine, quality worker or cached analysis result. Run analysis through a bounded, cancellable background queue, cache against the original-content identity and algorithm version, and avoid holding SQLite transactions during FITS reading or computation. Archive operations must stay responsive.

## Existing application support

- `app/database.py` stores exposure/session metadata and integrity fingerprints, but no rating or quality metrics.
- `app/ingest.py` reads capture metadata; it does not assess pixel quality.
- Archive Health provides validated read/path identity principles. Future quality analysis must operate on original linear pixels; the preview renderer was retired.
- `app/server.py` has no preview, trash or rating routes. Legacy retained-file recovery uses an explicit maintenance command.
- Archive Health can “forget” a record only after verifying its file is already missing. This does not remove an existing exposure from disk.

## Retired deletion feature

The in-app Trash feature has been removed. Existing payloads, manifests and
catalog recovery rows are preserved, with automatic reconciliation limited to
interrupted operations. A completed retained exposure can be listed/restored
explicitly through `scripts/restore_legacy_trash.py --database PATH`.
The maintenance command verifies bytes and preserves original IDs, metadata
and integrity baselines. It cannot create new trash entries or permanently purge
files. Current architecture and validation are in DEVELOPMENT.md and RELEASES.md.

## Validation before delivery

Quality analysis needs synthetic tests for known PSF widths, noise, gradients, clipping and invalid FITS data, plus a held-out set of human-reviewed clear/cloudy/obstructed exposures from representative equipment. Report false positives and missed problems, test raw/calibrated/Bayer/compressed images and non-light frames, and benchmark CPU, memory, cancellation and responsiveness on long sessions. Do not claim detection accuracy before this evaluation.

Deletion needs private scratch tests for disk/database rollback, interruption recovery, double requests, path aliases, links, changed roots/settings/files, lock conflicts, occupied restore destinations, reindex/health exclusions, legacy databases and reopened recovery state. Verify unchanged source/adjacent files, integrity metadata and exact session membership. No tests may use profile archives or actual source/packaged data.
