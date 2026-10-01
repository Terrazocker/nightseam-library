# Catalog visual fidelity and responsive layout plan

Date: 2026-10-01. Status: implemented and verified in the working tree; unreleased.

## What was compared

The current source catalog was rendered in system Chrome using an isolated,
empty database/archive, 100% zoom, DPR 1, and en-US locale. No user catalog was
opened or changed. The native Windows frame remains outside the Workbench
client area. No NightSeam window was open when the live-window inventory was
checked, so these are fresh current-source client captures, not screenshots of
an existing native window or an older packaged executable.

All three reference PNGs in `image-catalog-reference/` were inspected. Eighteen
current captures cover Library, Import Exposures, and Sky Map at:

- Reference client size: Library 1585x992; other views 1586x992.
- 1240x900, 900x800, and 480x620.
- 2000x800 and 2000x1400, isolating height changes at unchanged width.

Evidence is saved locally in:
`~/.codex/visualizations/2026/09/30/01a0f3c0-f719-7b71-8728-5d4c06a14aae/catalog-review-20261001/`.
`metrics.json` contains measured rectangles, font sizes, and scroll dimensions.
The captures produced no page errors. These were diagnostic captures, not a
full test-suite run or native DPI acceptance.

## Confirmed comparison results

| Finding | Measured/visual evidence | Change needed |
|---|---|---|
| Baseline panel structure is close | At 1585x992 Library starts at x20/y167.97, with rails 311.91px, workspace 891.20px, upper row 584px, and import dock 185px. These closely reproduce the reference panel boundaries. | Preserve the baseline hierarchy; focus on density, available-space sizing, and details rather than replacing the whole UI. |
| Resizing changes UI magnification | At 2000x800 root font is 14px and header 90px; at 2000x1400 root is 17.65px and header 113.48px. The same width gets a roughly 26% density change from height alone. | Remove viewport-driven global magnification. |
| Scaling is internally inconsistent | Body font remains 14px at both wide sizes, while rem-based controls/headings/panels grow. The shared `html[data-theme="workbench"] body` 14px rule outranks the catalog body's 1rem rule. | Establish a scoped typography system; do not rely on inherited shared defaults accidentally winning the cascade. |
| Content width also changes with height | At 2000x800 the Library frame is x227/w1545.98; at 2000x1400 it is x25.22/w1949.55. The scaled maximum-width container changes how much horizontal space is used. | Use available width with bounded rails, independent of height and font magnification. |
| Short windows retain mockup heights | At 1240x900 the Library dock still ends at y951.95 and the document is 992px high. At 2000x800 the same dock is mostly below the initial viewport. Import and Sky Map similarly keep tall desktop card heights. | Size the workspace from actual remaining client height. |
| Stacked empty views are excessively tall | Library document height is 1669px at 900x800 and 1674px at 480x620. Objects consume a large upper empty card before the sessions workspace; the import dock begins below y1443. | Use compact object navigation and natural empty-state heights at narrow sizes. |
| Baseline text is smaller than the references | Current table headers are 12.6px; several helpers/controls inherit small shared styles. Reference labels, table headings, and empty-state text have more readable presence. Some current headings are visually heavier. | Tune text roles once at baseline instead of enlarging everything through resize. |
| Empty-state details differ | Icon silhouettes/optical sizes and text wrapping differ; the selected-session icon/text group sits noticeably higher than in the reference. Search/tabs and empty-state text also have different visual density. | Tune per-component spacing, optical icon bounds, and wrapping against the baseline images. |
| Sky Map axes do not align with its fitted canvas | At 1586x992 plot box is y217.95/h704.03, while the 2:1 canvas is y297.47/h545. At 1240x900 the canvas is y383.97/h372 inside the same tall plot cell. Dec labels span the outer cell rather than the real canvas. | Give axes and canvas one shared rectangle and coordinate transform. |

## Policy decision: stable UI density

Recommended behavior: window resizing changes layout and available workspace,
not the size of every label, icon, and button. Keep a stable catalog root size
and explicit role sizes at unchanged OS DPI/browser zoom. OS scaling and text
accessibility remain separate concerns; do not counteract them with viewport
size arithmetic.

This intentionally revises the proportional magnification rule in
`IMAGE_CATALOG_UI_IMPLEMENTATION_SPEC.md::Resolution scaling`. The current code
largely follows that formula, so this is a design-policy correction as well as
a cascade/layout repair. The reference PNGs define baseline visual hierarchy
and proportions, not a requirement to enlarge fonts when the window gets taller.

Use stable catalog tokens for headings, primary labels, helpers, table text,
control heights, icon boxes, gutters, and gaps. Preserve the reference's readable
hierarchy: roughly 24px page headings, 20–22px card headings, 16–18px primary
labels, 14–16px helpers, and 14–16px table headings. Final values need baseline
visual review. Keep a single coherent font stack and measured text roles; do
not indiscriminately enlarge every data cell or reduce readability to fit.

The native Windows title bar stays native and unchanged. Do not restore the PNGs'
illustrated HTML caption controls, copy raster background noise into gradients,
or introduce another theme. Workbench role colors remain authoritative.

## Ordered changes

### 1. Amend the brief and define baseline tokens

Update the scaling section of the catalog specification and the responsive
contract in `DESIGN.md` during implementation. Replace height/width magnification
with stable density and responsive layouts. Keep the 1585/1586x992 reference
capture contract and document intentional native-frame and map differences.

In `catalog_main.js`, remove resize-driven root-font magnification and its
scaled maximum-width behavior. Scope any stable root setup to catalog boot.
In the catalog CSS, explicitly resolve shared font/heading/control overrides
and remove conflicting scale inheritance. Preserve source-planner appearance.

First establish the baseline's header/tab typography, label/helper/table sizes,
weights, icon optical bounds, and empty-state grouping. Preserve the already
close panel boundaries while making this calibration. Avoid changing unrelated
archive behavior or importing illustrated sample data.

### 2. Make shell height and scroll ownership explicit

Use a shell layout with actual toolbar and tab-strip heights followed by a
flexible workspace. Do not repeat `100vh - 150px` across modules: the toolbar
already becomes taller at some widths. Prefer CSS grid/flex auto rows; use one
measurement only where viewport/container sizing genuinely needs JS.

Remove fixed 783/784/773px desktop layout heights and long stacked row minima
from their role as unconditional requirements. Set `min-height: 0` through
flex/grid ancestors so the remaining space can be allocated correctly.

Wide Library: flexible upper workspace, a bounded import dock, and a stable gap.
At the reference size, retain approximately the 584px/185px split. At shorter
sizes, allocate available space to data/table regions and compact the dock
without hiding its action. Let tall windows show more rows rather than larger
fonts. Select one scroll owner for each dense region; avoid a page scroller
plus unnecessarily nested panel scrollers.

Import: natural form content with a bounded, flexible drop zone; flexible result
and history panels. Reduce decorative empty height before hiding primary actions.
For very small clients, allow clear vertical page scrolling rather than clipping
the form. An empty state should not require a huge minimum-height card.

### 3. Define responsive compositions by readable minima

Keep about 20px desktop gutters and 14–16px gaps; use smaller stable gutters on
compact clients. Use available client width, with bounded side rails and the
central workspace absorbing extra width. Do not make content width depend on
client height. At reference width retain the approximately 312/891/312 Library
balance and Import's roughly 570px configuration card.

Library compositions:

- Wide: object rail, sessions workspace, session inspector; dock below.
- Intermediate: bounded object navigation plus a wide sessions workspace;
  move the inspector below the workspace when three readable columns cannot fit.
- Compact: small object/search chooser, sessions workspace, selected-session
  section, compact import action. Expand object lists on demand instead of
  putting a 330px empty object card before all useful content.

Derive breakpoints from actual rail/table minima and test both sides of them.
Do not keep three rails just because width is one pixel above 900, or switch
column ratios abruptly without checking the center's readable width. Dense
session tables may scroll horizontally inside their own region, with readable
headers and no loss of selection/actions. Verify bounds; global `overflow-x:
hidden` is not evidence that content fits.

Import uses two columns while the form and results remain readable, then stacks
with natural heights. Sky Map moves its controls/selection beneath the chart
when a useful chart and rail cannot coexist. Keep action names or accessible
compact controls reachable at 480–500px; preserve existing keyboard contracts.

### 4. Correct Sky Map geometry and drawing

Preserve the shared 2:1 RA/Dec map geometry and DPR-aware bitmap. The generated
reference's illustrated plot is taller than that geometry allows; stretching
declination to copy the raster is not an acceptable fix.

Currently `renderSkyMap()` sets `--canvas-width/height/left/top` on
`#skymapPlot`, but the RA/Dec axis nodes are siblings, so they do not inherit
those values. Set geometry on a shared ancestor or explicitly apply the same
measured rectangle to axes and canvas. Align tick centers to actual grid-line
coordinates, including both end ticks, instead of spreading text over an
unrelated cell. Keep pointer hit testing and tooltip placement in that same
displayed rectangle, accounting for any letterboxing.

The map card should consume available space with an honestly bounded chart.
Avoid enormous empty bands created solely by fixed card heights. Use real
constellation/star data; do not move stars or fabricate the generated image's
sparse illustration. Review contrast/density as presentation, while retaining
working toggles and data semantics. Checkbox/raster inconsistencies in the
mockup are not authoritative behavior requirements.

Coalesce ResizeObserver, viewport, tab activation, and DPR invalidation into
one draw after layout. Multiple observers currently can request redraws for
the same resize. Fix axis correctness before optimizing this path.

### 5. Visual and interaction acceptance

Compare all three empty-state baseline captures at native reference dimensions,
100% zoom, DPR 1, and the same font/runtime. Judge panel boundaries, typography,
icons, text grouping, and spacing; do not treat raster noise or OS chrome as
client styling requirements. Match major baseline panel edges within about
2 CSS px where compatible with the deliberate map/frame policies.

Repeat with populated fixtures: long object names, many sessions, selected
session, long paths/filter labels, import progress/results/errors, and mapped
objects. No placeholder ADU statistics or other unavailable measurements.

Required resize matrix: reference size, 1240x900, 900x800, 640x800, 480–500x620,
1920x1080, 2000x800, 2000x1400, and 2560x1440. Add one pixel above/below each
chosen composition breakpoint. Test continuous height-only and width-only
resizing, tab switches, and native maximize/restore.

Acceptance checks:

- At unchanged zoom/DPI, computed typography, icon and control density remain
  stable when only the window size changes.
- Wide/short views use the available workspace instead of forcing a 992px page.
- Empty compact views avoid long fixed-height stacks; all actions remain reachable.
- Table/inspector scrolling has clear ownership; no accidental clipping or
  inaccessible controls hidden behind an overflow rule.
- Each map tick aligns with its grid line at baseline, narrow, and letterboxed
  sizes; RA/Dec ratios, DPR bitmap sizing, selection, and tooltips remain correct.
- Browser and native-frame client content match at equal viewport/zoom/DPR.
  Separately verify physical Windows scaling at 100/125/150/200%; browser DPR
  emulation does not replace native WebView2 acceptance.

During implementation, run focused behavior/browser checks, the JS/encoding
guards, and visually inspect new captures. Run the full pytest suite once at
the end. Record results and remaining native coverage. Do not build/tag/deploy
for this planning task; implementation later may build a fresh development
executable for its native acceptance.

## Scope and implementation ownership

Primary files: `static/js/catalog_main.js`, `static/themes.css`, the catalog
sections of `static/style.css`, `static/catalog.html`, `static/js/skymap.js`,
and existing SVGs in `static/js/catalog_icons.js` when optical corrections are
needed. Update `tests/browser/catalog_suite.py` to measure vertical reachability,
stable density, and axis/grid alignment instead of only horizontal fit.

Archive interactions and underlying data remain real. No changes to FITS ingest,
database schema, backup invariants, astronomy/core math, source planner, or web
planner are required. Keep all existing unrelated working-tree changes.

## Implementation and measured acceptance

Implemented on2026-10-01 in the existing working tree. Existing native-frame,
archive, and unrelated changes were preserved. No commit, tag or deployment
was performed. The development executable was rebuilt after final visual review.

- Stable14px catalog root/body density replaces viewport magnification. Scoped
  heading/helper/table roles and empty-state icon grouping were calibrated
  against the references; Workbench surfaces remain opaque.
- The shell uses real toolbar/tab rows and remaining client height. Wide Library
  retains its reference split while short windows compact the dock; side rails
  stay bounded as the center grows. Import fields cannot collapse under height
  pressure, and dense table/inspector regions have explicit scrolling.
- Library switches to two columns below1280px and a compact object/search chooser
  at760px. Search discloses results; the button exposes its expanded state.
  Import stacks at1100px and Sky Map at1000px. Actions remain reachable.
- Map axes share the fitted canvas rectangle, including exact endpoint centers.
  Catalog ResizeObserver/viewport/DPR/tab invalidations share one animation-frame
  draw. Hit tests and tooltip bounds use the displayed canvas; real data and2:1
  mapping remain intact. Source-planner drawing lifecycle remains synchronous.

Evidence directory:
`~/.codex/visualizations/2026/09/30/01a0f3c0-f719-7b71-8728-5d4c06a14aae/catalog-responsive-implemented/`.
It contains27 new captures and `metrics.json`. All captured viewports from480px
to2560px retain14px root/body text;2000x800 and2000x1400 retain the same horizontal
container bounds and density. Capture page errors:0. Baseline Library panels
retain their approximately312/891/312 widths and584/185px height split.

Current-source browser acceptance passed both `desktop-catalog-only-launch`
and `desktop-catalog-responsive-layout` using a redirected database/archive and
separate browser contexts. The responsive check covers22 viewport pairs,
one-pixel breakpoint edges, empty compact disclosure, all three views, short
form frame-type reachability,28 sessions with long labels, last-session selection,
calibration/import actions, map selection, endpoint/grid alignment, and DPR2.
The existing check additionally covers library retry, import/calibration,
reindex, export controls and keyboard/tab behavior. No user catalog was touched.

Final `python scripts/check_js.py`: passed (imports, DOM IDs, syntax, encoding,
tokens, provenance and core guards). Final `python -m pytest tests/ -q --tb=no`:
680 passed in219.91s, one upstream Starlette/httpx deprecation warning. Initial
sandboxed scratch checks hit Windows DACL restrictions; elevated execution
completed the required guards and full suite.

A fresh isolated source native WebView2 window launched successfully using the
actual native-shell entry point, with database, placement and WebView storage
redirected to fixture paths. The Library rendered under the Windows title bar;
the native Restore action produced a caption state transition. The fixture
window and server shut down afterward. This is a limited native smoke check;
physical100/125/150/200% DPI, continuous edge dragging, Snap/taskbar behavior and
multi-monitor acceptance are still unverified. No new packaged executable has
been released.

Final packaged validation: `build.ps1 -AllowDirty` produced a fresh20.5MB
`dist/NightSeam.exe` labelled `0.6.34-10-gf5bb0c5-dirty`. The final
`python scripts/check_browser.py --desktop --only catalog` passed4/4 against
that executable, including the two source-planner catalog checks and both
catalog-only checks. Initial packaged runs exposed a fake-clock test race:
Playwright clock frames can finish before Chromium's real ResizeObserver cycle.
Checks now wait for observed canvas/axis/bitmap convergence before clicking or
asserting, rather than assuming two clock callbacks imply settled geometry.
The final compact dock also places its action and hint within its own bounds;
browser coverage now asserts both child rectangles remain inside the drop zone.
No application changes followed the successful final packaged gate. Encoding
and diff-whitespace checks passed after the final documentation update.
