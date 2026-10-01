# NightSeam Image Catalog — visual implementation specification

This document is the implementation brief for matching the three generated
Image Catalog references. It records the target screens, measurable geometry,
visual language, current code ownership, and behavior that must remain real.
It does not authorize replacing the application with a screenshot or changing
the Night Planner.

## 1. Visual authority and target capture

Use the repository copies of these PNGs as the visual source of truth for the
packaged local Image Catalog's three child views:

| View | Reference | Raster size | SHA-256 |
| --- | --- | ---: | --- |
| Import Exposures | [`image-catalog-reference/import-exposures.png`](image-catalog-reference/import-exposures.png) | 1586 × 992 | `333FDDF5C26502443CC403D18D4E007B0C307162D7E4B69A4EDB82CA8B86BB10` |
| Library | [`image-catalog-reference/library.png`](image-catalog-reference/library.png) | 1585 × 992 | `BE2257ED7344667A538AB4AB6FCD1C7AAE74FCCB82E1CDD3233175B7CA20EFC2` |
| Sky Map | [`image-catalog-reference/sky-map.png`](image-catalog-reference/sky-map.png) | 1586 × 992 | `64DD646D5065EA4C8E5C58C308BD52BB402ED367391013D6DB480D67026D829C` |

These PNGs are generated visual mockups, not application screenshots. They are
the target for hierarchy, placement, icon shapes, spacing, labels, and accent
colors. Keep the images as review assets; never display a PNG as the live app
surface, set one as a page background, or cover real controls with it. The
existing data, local-only behavior, keyboard support, and security rules remain
authoritative.

For visual review, capture the **web content area** at each PNG's native
dimensions, 100% browser zoom, device scale factor 1, with a truly empty local
archive. The references are 992 px high; Library is 1585 px wide while the
other two are 1586 px wide. Do not resize the reference or capture a different
state and compare it to the empty state. Use a consistent browser/runtime,
viewport, zoom, DPR, and installed font stack for successive implementation
captures. These are generated mockups, so their original OS font rasterizer is
unknown; compare glyph shape/weight and treat antialiasing as approximate. The
catalog child tab is restored from `sessionStorage["nightseam_catalog_child"]`;
use a fresh browser session or set that key to `library` before capturing the
Library reference. An empty archive alone does not guarantee the Library tab
is active. These dimensions describe the Workbench **client area**. The
standard Windows title bar is outside it and adds native height to whole-window
captures; never compare whole-window pixels against the 992 px client reference.
Match the main panel edges within 2 CSS px, the header/tab boundaries within
1 px, and icon silhouettes within 1–2 px. Treat native window shadows as
platform rendering differences, not reasons to add blur or shadows to the page.

## 2. Scope, current shell, and implementation boundaries

This target applies to the **packaged catalog-only desktop shell** and its
Library, Import Exposures, and Sky Map views. It does not redesign the source-run
Night Planner, web planner, charts elsewhere, storage schema, FITS parser, or
server routes.

The authored files that own the current surface are:

- `static/catalog.html`: catalog header, child tabs, and the three view trees.
- `static/style.css`: authored layout, component geometry, and responsive rules.
- `static/themes.css`: Workbench tokens and theme-owned component styling.
- `static/js/catalog_main.js`: current child-tab selection/storage, startup,
  and top-bar action forwarding. Establish stable catalog density at startup;
  do not recalculate root typography from viewport dimensions on resize.
- `static/js/archive.js`: `renderArchiveStats`, `renderLibrary`,
  `renderLibrarySessions`, `renderIngestResults`, archive path, FITS import,
  import history, session/object selection, export, and reindex handlers.
- `static/js/skymap.js`: `renderSkyMap`, `renderSelection`, the fixed RA/Dec
  chart, marker hit testing, type legend, hover, and selected-object rail.
- `static/js/skydata.js`: the existing deterministic constellation and field
  star backdrop data used by the static map.
- `main.py::JsApi` and `run_native_window`: native folder picker, explicit
  native-frame/browser capability, title, icon, and desktop launch options.
- `app/window_placement.py` and `app/paths.py::window_placement_path`: local
  normal bounds/maximized persistence and monitor-work-area recovery. The file
  lives in the user's LocalAppData and is not part of setup backups.
- `app/server.py` / `app/ingest.py`: local API and FITS/archive behavior; do not
  add a presentation-only API or duplicate classification/storage rules.

Keep existing element IDs and event contracts when rearranging markup, unless
all their listeners are deliberately migrated in the same implementation.
In particular, preserve the top-bar buttons' forwarding to the existing import,
reindex, and export actions; preserve the drop-zone and file-input IDs used by
`archive.js`; preserve the chart canvas and `smLabels`, `smSize`, `smCons`, and
`smLegend` controls. Do not edit baked `core/` output or planner code to achieve
this catalog appearance.

## 3. Shared visual system

Use the existing Workbench tokens in `DESIGN.md` and `static/themes.css`:

| UI role | Exact token value |
| --- | --- |
| Page/header background | `--bg: #0B1115` |
| Panel surface | `--panel: #10181B` |
| Raised controls / selected surfaces | `--raised: #152027` |
| Panel outlines | `--border: #293840` |
| Quiet separators | `--border-soft: #1E2B31` |
| Chart grid | `--border2: #304049` |
| Primary text | `--text: #E7ECE8` |
| Supporting text and outline icons | `--dim: #9CAEB1` |
| Brand, active tab, primary action | `--accent: #F9C85D` |
| Sky-map constellation sticks | `#73B6DB` (`--viz-target-secondary` currently carries this Workbench value) |

Use opaque surfaces, 1 px borders, and 4–7 px corner radii. Keep the existing
system-sans interface stack; on Windows the intended face is Segoe UI. Use
monospace only for counts, coordinates, times, and measured values. The target
is dense but breathable: 24 px page headings, about 20 px card headings,
16–18 px primary labels, 14–16 px helper text, and 14–15 px table headings.

### Stable density and responsive composition

Every coordinate and size in this brief is a **baseline CSS-pixel measurement**
for a 1586 × 992 webview client at 100% zoom, excluding the native OS title bar.
Do not interpret it as a fixed physical-pixel size. The packaged catalog opens
maximized on first launch and respects later maximize/restore actions and
stored normal placement. At unchanged OS DPI/browser zoom, resizing changes
the space available for content, not its magnification. Keep the catalog root
at 14 CSS px with explicit scoped text roles, stable control and icon sizes,
1 CSS px borders, and stable spacing. Resolve catalog body/heading overrides
explicitly so the shared planner defaults do not produce inconsistent text
sizes. OS scaling, browser zoom, and accessibility settings remain separate
from viewport layout. This policy supersedes the former viewport-ratio root
font calculation and scaled maximum-width container.

Keep header, tab, and page backgrounds full-bleed. Use the available client
width with approximately 20px desktop gutters and bounded side rails; width
must not change when only client height changes. The center workspace absorbs
additional width. Toolbar and tabs take their actual natural heights, followed
by a flexible workspace using the remaining client height. Fixed reference
panel heights are comparison targets at the baseline, not unconditional page
minima. Use `min-height: 0` through flex/grid ancestors so dense regions can
scroll internally instead of forcing a 992px page in shorter windows. Taller
clients show more rows, not larger typography.

Reflow at measured readable minima: retain the three Library rails at the
baseline; move the inspector below the workspace at intermediate widths; use
compact object navigation above sessions at narrow widths. Import moves from
two columns to a natural-height stack, and Sky Map moves controls/selection
below the chart. Compact layouts use page scrolling, bounded empty states,
and local table scrolling where necessary. Keep all actions reachable and
avoid simultaneous page/panel scrollbars for the same content.

Canvas axis fonts, strokes, and marker radii use stable CSS-pixel roles too.
Keep the Sky Map's uniform 2:1 RA/Dec mapping; never distort one coordinate
axis. Keep its backing bitmap device-pixel-ratio aware after setting the CSS
display size. Normal display-scale changes require bitmap remeasurement,
without introducing viewport-driven interface magnification.

Do not copy the slight per-pixel hue/noise changes in the generated raster into
CSS gradients. They are inconsistent between the three generated PNGs. Use the
exact semantic Workbench hex values above for surfaces and chrome; compare role
to role, not arbitrary sampled pixels. Use no decorative gradients, glow,
drop-shadows, blur, translucency, CSS starfield texture, or emoji. In the Sky
Map, preserve the existing `CONSTELLATIONS` and 420-point seeded `FIELD_STARS`
layers from `static/js/skydata.js`; these deterministic points are the current
approximate chart context, not a measured star catalog. Use `--viz-grid` for
minor grid lines, `--viz-grid-strong` for the major RA/Dec grid, and
`--viz-target-secondary` (`#73B6DB`) at reduced opacity for the thin
constellation sticks. Do not apply the secondary-line token to grid lines or
catalog object markers. Draw icons as inline, stroke-based SVGs with explicit
`viewBox`, `stroke-linecap`, and `stroke-linejoin`; do not substitute Unicode
characters or emoji.

The PNGs are raster references, so there are no original SVG path commands to
reuse. Trace each visible icon's silhouette from the matching 1:1 PNG crop into
an authored SVG; do not replace it with a merely similar icon from an icon
library. Keep one consistent 1.8–2 px outline stroke for the 22–24 px action/tab
icons, and a 2–2.5 px outline for the 52–72 px empty-state icons. Use `--dim`
for outline/document/search symbols and `--accent` for the logo mark and active
actions. Required shapes: ring/mountain/star brand mark; list, download-tray,
folded-map tabs; download, circular-reindex, share/export header actions;
search; folder, folded-corner file, three-tier database cylinder, and clock
empty-state symbols. Replace every folder glyph emitted by `&#128194;` in
`archive.js` (import history and the populated object table) with the traced
folder SVG, keeping the existing open-folder handlers.

### Workbench header and child-tab bar

All three references describe Workbench client content. In the packaged
Windows app, the OS caption sits above this content and owns all window
interaction. The client header contains the catalog brand and app actions only.

1. **Top header:** 90 px high, full viewport width, `--bg`, with a 1 px bottom
   border. At x≈29, y≈14 place a 56 × 56 px gold roundel: an open circular
   orbit/ring around an angular mountain peak and a small four-point star. To
   its right place `NightSeam` in 28 px semibold `--text`, then a thin vertical
   `--border` divider and `Image Catalog` in 18 px `--dim` text. Do not use the
   current small starburst as the roundel.
2. Place three quiet, borderless actions in one right-aligned row near the
   header's lower edge: gold download/tray icon + `Import FITS` (`--accent`
   icon, `--text` label), blue-gray circular-arrows icon + `Reindex folders`,
   and blue-gray share/export icon + `Export library` (both icon and label use
   `--viz-target-secondary`). Use about 18–20 px icons, 15–16 px labels, and
   24–30 px gaps. These three header actions are not filled button pills. They
   invoke the existing real actions.
3. Do not render minimize, maximize/restore, or close symbols inside the page.
   The packaged host sets the native title to `NightSeam — Image Catalog`,
   uses the existing app icon, and requests a standard resizable Windows frame.
   OS caption buttons, caption dragging/double-click, system menu, keyboard
   interaction, edge/corner sizing, taskbar behavior, and Snap belong to
   Windows. Keep the Workbench app header below the native title bar; never
   scale native caption metrics or place app actions in a drag region. The
   browser fallback has no NightSeam-drawn window controls either.
4. **Child tabs:** a 60 px high strip from y=90 to y=149 with a 1 px bottom
   border. Use `Library`, `Import Exposures`, and `Sky Map` in that order. Each
   tab has its matching 22–24 px line icon at left: stacked list/document,
   download-to-tray, and folded map. Use 16–17 px text and generous horizontal
   padding. Inactive tabs are muted blue-gray. The active tab uses gold icon
   and text plus a 4 px gold bottom rule spanning the tab width. Keep tab
   switching, selected state, keyboard behavior, and URL/storage behavior
   already implemented in `catalog_main.js`.
5. Main content begins at y≈168, leaving an 18 px gap below the tab rule and
   20 px left/right page gutters. Card outlines use `--border`, 1 px stroke,
   6 px radius, and opaque panel fill. Inter-card gaps are 14–15 px.

Override the planner-shell sizing only under `.catalog-only`: `static/themes.css`
currently gives `#topbar` a 48 px minimum height, and
`static/style.css` gives `.image-catalog-pane` a `100vh - 48px` minimum height
and 10 px top padding. The catalog shell must instead account for its 90 px
header and 60 px child tabs. At baseline height, use an 18 px content top gap,
fit the page cards from y≈168 to y≈951, and leave about 40 px below them. At
other heights, derive the workspace from the actual remaining client space;
reflow or allow natural page scrolling when the readable content minima
cannot fit, without clipping controls.

Do not show the configured path in the header's current `#archiveInfo` slot;
the reference has no path text there. Keep the value in the editable Archive
folder field on Import Exposures. `applyArchiveUI()` still writes to
`#archiveInfo`, so retain the span in the DOM and hide it in the catalog header;
override/remove the `static/style.css` rule that currently forces it visible.
Keep `#busy` available during work, but make its appearance non-layout-shifting
so the logo/actions do not jump. The fixed bottom-right `#versionTag` is not in
the references; keep updating its version text but make it visually hidden in
catalog-only mode (preserving its live-region accessibility) so it cannot
overlay the lower edge of a panel.

## 4. Import Exposures page

At 1586 × 992, the content frame is x=20..1566, y≈168..951. Use a two-column
grid with a 14 px gap: left settings/drop panel x≈20..590 (570 px); right
results column x≈604..1566 (962 px). At baseline, use `570px minmax(0, 1fr)`
tracks with both panels stretching to the same 783 px bottom edge. Treat that
height as a baseline comparison target. Bound the settings rail while letting
the results column absorb additional width, and give the drop zone/results
the remaining height after their natural form/header content. At narrow widths,
stack with natural heights rather than retaining the desktop card height.

### Left panel (570 × 783 px)

Use 21–22 px internal padding. In order, render:

1. `Import Exposures` (24 px, semibold) and the muted subtitle `Add FITS files
   to your image catalog.` (16 px). Add a full-width soft divider below them.
2. `Archive folder` label. Below it, one horizontal row: path field about
   315 × 43 px, `Browse...` neutral button about 108 × 43 px, and `Save` neutral
   button about 84 × 43 px. Keep 10 px gaps. The path remains a real editable
   local archive path and shows the configured value when one exists (the
   reference's field is blank because its depicted setting is unset); `Browse...`
   keeps the native folder picker; `Save` keeps the existing settings path.
3. The two-line muted helper text: `Original files are copied to your archive
   folder and never moved or modified.` Add a divider underneath. Do not put a
   second reindex button or archive-stat block in this primary card; Reindex is
   in the global header and forwards to the existing reindex handler. Keep
   `#btnArcReindex` as a non-visible handler target or migrate the handler and
   top-button binding together; never leave a visible duplicate in this panel.
   `#archiveStats` contains real object/image/integration totals. Move that
   existing node into the `All objects and archive tools` disclosure on
   Library, keep its renderer and all three values, and do not show it in this
   reference panel.
4. `Frame type` label and a 4-segment, full-width group about 528 × 50 px.
   Segments, left-to-right: `Lights`, `Darks`, `Flats`, `Bias`, equal width.
   Use dark raised/transparent fills and vertical separators. The reference
   default `Lights` segment has a 1 px gold outline, gold text, and a 5 px
   radius; other choices use neutral text. Add a divider below.
5. `Add files` label. Add a dashed rectangle about 528 × 267 px with a
   5–6 px radius. Center a 64–70 px outlined file icon, then a filled gold
   `Import images...` button about 236 × 52 px, then the muted hint
   `or drop .fit / .fits files here`. Preserve multi-file selection, drag/drop,
   progress, errors, and batching.

**Frame classification safety:** the current ordinary import path classifies
from FITS `IMAGETYP`; the optional `frame` passed to `ingestFiles` is a force
override and wins over the header. Do not silently make an ordinary upload
force Lights: dark/flat/bias files would be filed as lights. The reference's
gold `Lights` default conflicts with this real automatic-classification
behavior. Safe default: leave all four segments neutral/unselected to mean
“classify from FITS header”; selecting a segment explicitly forces that type
for the next `Import images...` batch. Map segment values to the existing API
values `Lights → light`, `Darks → dark`, `Flats → flat`, and `Bias → bias`.
Use toggle buttons (`aria-pressed`); activating the current override again
clears it to Auto. Label the group accessibly and connect a visually-hidden
help description explaining automatic classification; do not add visible
helper copy that changes the reference spacing. This is one intentional
state-color difference from the PNG. If exact initial `Lights` highlighting is later
required, first make an explicit product decision to change default import
semantics; do not simulate a selected state while secretly running Auto.
Retain the current explicit override for “add calibration frames to an
existing session.”

### Right column

Stack two bordered cards with 14 px gap. The first is x≈604, y≈168, w≈962,
h≈413 px; the second y≈595, h≈356 px. Each has a 20 px heading aligned
top-left and an inset bordered empty-state area with 5 px radius.

- **Import results:** heading `Import results`. In an empty library, center a
  60–66 px outlined document/list icon and the two lines `Imported sessions
  appear here.` and `Files will be copied to your archive folder and added to
  the catalog.` When results exist, show the real current batch results,
  successes, and errors inside this card. Show `Clear` only when there is
  something to clear; its existing handler must still work.
- **Recent imports:** heading `Recent imports`. Remove the current static
  explanatory paragraph and render the empty-state clock outline (about 54 px)
  and the lines `Recent imports appear here.` and `You can also add darks,
  flats, or bias to an existing session.` inside `#importHistory`. When history
  exists, render the real session rows there and retain the existing actions to
  add missing calibration frames to that session.

## 5. Library page

At 1585 × 992, use one grid with three upper rails and a full-width bottom
import dock. Outer x gutters are 20 px. At baseline, use columns `312px
minmax(0, 891px) 313px`, 15 px column gaps, and rows `584px 185px` with a
15 px row gap. This places the upper row at y≈168..752: left rail x≈20..332,
center workspace x≈347..1238, and right rail x≈1253..1566. The import dock
spans all columns at x≈20..1566, y≈767..952. Keep side rails near their readable
baseline widths while the center absorbs additional width. Let the upper row
use remaining client height, with a bounded import dock beneath it; compact
the dock in shorter windows while preserving its file action. These row sizes
are baseline targets, not fixed minima for every client.

### Objects rail

Show `Objects` as a 20 px heading and `0 objects` in muted text below it when
empty. Put a full-width search field below, with a 20 px search icon and the
placeholder `Search objects...`; at the target size it is about 274 × 45 px at
x≈39, y≈263. In the empty area, center a 72 × 64 px outline folder icon,
`No objects yet`, and two muted lines `Import FITS files to build your image
library.` Keep real object filtering and selectable object rows when the
library is populated. Format the count as `N objects` (and preserve a useful
filtered-count form while searching), rather than a bare number.

### Library/session workspace

Do not show the current seven-metric hero/summary strip, `Image archive` eyebrow,
or helper subtitle in the reference's default view. Start with a compact
`Library` heading and a thin full-width divider. Below it, show `Capture
sessions` and `0 sessions` on two consecutive lines, with the count muted. Use
the existing real session table with this exact column order and labels:

`Date / time` · `Lights` · `Darks` · `Flats` · `Bias` · `Light integration` ·
`Filters` · `Folder`

Keep the 1 px grid lines and raised header band. At the target viewport the
table begins near y≈300 and its header is about 46 px tall. Fit the desktop
table to the workspace's roughly 856 px inner width (`width:100%`,
`table-layout:fixed`, no 930 px minimum). At 856 px, start with column widths
152, 88, 78, 84, 70, 160, 88, and 136 px in the order above; scale those
proportions with the available width. Use ellipsis for long folder/filter text.
Only enable horizontal scrolling at
narrow breakpoints. Its empty body fills the remaining workspace height. Use a
single valid table row with `td colspan="8"` containing a centered 60–64 px
database-cylinder outline, `No capture sessions yet`, and `Import FITS files to
build your image library.` Remove the legacy `#libEmpty` paragraph as part of
updating `renderLibrary()`; do not leave it visible or keep code that toggles
it into the new empty state. Show `0 sessions` (not the current `0 recorded`)
and format populated counts as `N sessions`. Use real session values, frame
counts, total light integration, filter rollup,
and folder values when rows exist; retain session selection/open-folder and
table scrolling. Keep `#libToolbar`, the all-object table, object sort/export
controls, and `#archiveStats` in the `All objects and archive tools`
disclosure. Move that disclosure from the center workspace into the Objects
rail below the object list, keep it closed by default, and hide the disclosure
itself while the archive is empty; its summary row is not present in the
reference screenshot.

The current center hero computes seven selected-object totals (sessions, light
integration, Lights, Darks, Flats, Bias, and Filters). The reference hides this
strip in the empty state, but don't discard its real values. Show them in a
compact `Object totals` disclosure in the selected-object/session detail rail
whenever an object is selected, whether or not a session is selected; keep that
disclosure closed/absent in the empty reference state. Keep the session count
and frame count in the reference's positions when nothing is selected. Update
`renderLibrary()` and
`renderLibrarySessions()` with the new empty-state DOM so their current
`innerHTML` assignments do not overwrite the SVG state with duplicate legacy
messages.

### Selected-session rail

Show `Selected session`, then the muted count `0 frames` on the next line, as in
the reference. Add a dedicated count slot (for example `#libraryFrameCount`);
update it to the selected session's real frame count, and do not repeat that
count in the detail facts. With no selected session, center a 52–58 px document
outline with `No session selected` and the two-line guidance `Choose a session
from the library to view details.` Hide `#librarySessionActions` while no
session is selected. When a real session is selected, keep its current details
and session actions; do not leave the empty icon over real data.

### Bottom Import FITS dock

The reference dock is a separate full-width grid row, not a child of the center
workspace. Move/restructure the existing `#libraryDropZone` without changing
its ID or file-input behavior. Render the `Import FITS` heading at top-left;
remove the current explanatory subtitle. Inside a dashed 1 px inset zone
spanning almost the full panel (x≈37..1549, about 1512 × 126 px), center a gold
`Choose FITS files` button about 260 × 52 px with the muted line `or drop FITS
files here` beneath it. Keep the real automatic import behavior on this dock.

## 6. Sky Map page

At 1586 × 992, start at y≈168 with a left map card and right rail ending at
y≈941. At baseline, use columns `minmax(0, 1176px) 356px` with a 14 px gap:
map card x≈20..1196 (1176 × 773 px), right rail x≈1210..1566 (356 px). Give
the map card an opaque panel fill, 1 px Workbench border, and 6 px radius.
Center an inset 2:1 canvas in it. Make `.chart-side` a transparent
layout column and render the Chart, Legend, and Selected library object
sections as three separately bordered cards, not one shared tall side panel.
Their reference y ranges are approximately 168–339, 350–607, and 620–941,
with 11–13 px gaps and 14–16 px internal padding. The chart should use as much
width as possible while keeping the application's astronomical mapping and
aspect ratio intact. The plotted axes are RA 00h at left through 24h at right,
and declination +90° at top through −90° at bottom. Put RA labels along the top
edge and Dec labels outside the left edge; use 14 px monospace labels. Draw and
label RA at 00h, every 2 h, and 24h at the right edge; draw/label declination
at +90°, +60°, +30°, 0°, −30°, −60°, and −90°. Do not label intermediate grid
lines. The reference's strong plot rectangle is approximately x=91..1172,
y=242..886 in its raster; this shape is taller than the canvas invariant
allows, so use those as reference bounds only. Draw grid lines only at 2 h/30°
intervals; remove the current 1 h/15° lines. Use 1 px dashed grid strokes with
about 6 px on / 4 px off in CSS pixels, with the 2 h/30° lines at
`--viz-grid-strong` and the outer plot border a solid 1 px `--border2` stroke.
Keep the grid quiet blue-gray. Use the app's existing
constellation/field-star data; do not invent target positions. Draw the stick
lines and field-star points without constellation name text in the empty
reference state. With an empty archive, no library-object markers or object
labels appear.

The app invariant requires the Sky Map data canvas to remain exactly 2:1 and
coordinates to remain linear RA/Dec. The generated mockup's visible grid
appears taller than that invariant allows. Preserve 2:1 geometry, size the
canvas to the largest 2:1 rectangle that fits, and center it within the larger
left panel; do not stretch coordinates to fill the tall panel. This is a
known, unavoidable geometric difference from the raster. The current
`renderSkyMap()` also enforces a 240 px minimum canvas height, which can make
the 2:1 canvas wider than its container on narrow clients. Remove that floor
below the desktop range. Set the map card and its immediate canvas wrapper to
`min-width: 0; width: 100%; max-width: 100%`; derive the largest 2:1 CSS size
that fits both available width and height, with a 1 CSS px minimum only to avoid
zero/negative canvas dimensions. Set the canvas CSS width to `2 * H` and height
to `H`. Keep drawing coordinates in those CSS-pixel dimensions and translate
pointer coordinates through `getBoundingClientRect()` if the CSS display size
differs from the logical drawing size, so hover/click targets stay aligned. If
the chart becomes too small for every axis label, thin the displayed tick labels
while keeping the full RA/Dec data range and 2:1 mapping; never create
horizontal page overflow to preserve the desktop canvas size.

Publish the fitted canvas rectangle on the shared axis/canvas ancestor, or
provide the same explicit rectangle to all three elements. Axis tick centers
must coincide with the canvas grid coordinates, including the edge ticks;
labels must not span the unused letterbox area. Hover/selection use the same
rectangle. Coalesce chart resize, tab activation, and DPR remeasurement into
one scheduled render path rather than issuing competing redraws.

### Right rail and legend

Stack three cards at x≈1210, each with 14 px gaps and 14–16 px internal padding:

1. **Chart** (about 170 px high): heading `Chart`; then three left-aligned,
   22 px square checkbox rows: `names`, `dot = integration`, `constellations`.
   Keep labels click-targeted and keyboard operable. To match the chart, leave
   `names` and `dot = integration` unchecked; make `names` control both
   catalog-object labels and constellation-name annotations. The reference
   shows all three unchecked but also draws constellation sticks. Current
   `smCons` gates field-star points, constellation lines, and constellation
   stars together with the labels. Keep it checked by default so those visible
   chart layers remain, while suppressing constellation names through the
   unchecked `names` option. This preserves the real toggle behavior and makes
   only the constellations checkbox differ from the raster. Do not show sticks
   while claiming the existing toggle is off.
2. **Legend** (about 257 px high): heading `Legend`, then six rows in this
   exact order: Galaxy, Nebula, Open cluster, Globular cluster, Planetary
   nebula, Supernova remnant. Each has a 14 px solid dot and 16 px muted label.
   Match the reference dot colors with a map-only presentation map (sampled
   solid centers): galaxy `#DF9398`, nebula `#87BEFC`, open cluster `#FBD46F`,
   globular `#A0E992`, planetary `#AA8CFA`, supernova remnant `#FBA457`.
   Use that same map-only mapping for plotted markers and legend dots. Keep
   colors as chart data/display mapping, not new Workbench theme tokens; do not
   modify `core/catalog.js` or the shared planner palette. Preserve clickable
   and keyboard-operable type filtering. The app also supports Asterism and
   Star cloud: append those entries only when such objects exist, using their
   canonical type colors, so the screenshot's empty-state six-row legend is
   exact without dropping supported filters.
3. **Selected library object**: heading `Selected library object`; inset empty
   card about 328 × 260 px. Center a 64 px muted file outline, `No mapped
   exposures yet.`, and `Import FITS files with object coordinates to populate
   this map.` Keep live object name, type, image/session counts, integration,
   and RA/Dec on selection. Existing hover hit-testing, click selection,
   tooltip, and focusable canvas must continue to work.

The screenshot's names and dot controls are off and its empty archive contains
no catalog markers. Keep those controls off by default; the selected-object
empty text should update to the app's active real state when data exists.

## 7. Interaction, responsive behavior, and truthful empty states

- Empty counts must come from the local database. Never seed pretend objects,
  sessions, imports, or frames to make the screenshot appear populated.
- FITS files remain copied to the configured archive, never moved or modified.
  Preserve safe path handling, header parsing/classification, deduplication,
  cancellation/failure cleanup, session grouping, and import history.
- Top `Import FITS`, `Reindex folders`, and `Export library` must invoke the
  existing handlers. The single global reindex action scans the configured
  archive and skips already-recorded files. The export action must still export
  the real library as CSV. The current UI has no contextual export strip.
- Keep session rows selectable; real selected details replace the empty state.
  Keep add-calibration-frame actions; library object search, rename/type edit,
  object/session folder-open actions and object-session expand/collapse. Keep
  `#libSearch` and object sorting visibly in the Objects rail. The current
  active-object header replaces the retired all-object disclosure; its session,
  image and light-integration facts are open text, with inline unit switching.
- Keep chart type filters shared through `S.disabledTypes`; keep canvas
  selection, hover, tooltip, labels, integration-based marker sizing, and
  keyboard access. Visual remapping must not change object types or RA/Dec.
- Reflow at readable content minima before panels or controls overflow. Use
  three Library rails at the baseline, two columns with the inspector below
  the workspace at intermediate widths, and compact object navigation with
  sessions/selected details/import at narrow widths. Avoid tall empty object
  rails before the primary workspace. Import and Sky Map stack naturally when
  two columns no longer fit. Allow internal scrolling for dense tables and
  natural page scrolling for compact compositions. Keep keyboard focus visible
  and controls labelled. Responsive rearrangement is allowed; preserve the
  desktop reference hierarchy at the baseline viewport.

## 8. Known visual/behavior mismatches to resolve as written

| Reference request | Current app constraint | Required treatment |
| --- | --- | --- |
| Slightly lighter, softly varied mockup surfaces | The PNG fills contain small per-pixel hue/noise shifts, while `DESIGN.md` requires flat opaque Workbench role colors | Use the listed Workbench tokens for surfaces and chrome; do not bake or approximate the mockup's unstable noise/gradient. This keeps the same color roles but cannot be pixel-identical at every surface pixel. |
| Four-way frame selector, Lights highlighted | Main ingest auto-classifies `IMAGETYP`; passing the selected frame forces an override | Safe default is automatic with no highlighted frame. Selecting then re-activating a segment applies/clears its explicit override. This intentionally differs from the initial Lights highlight; change import defaults only with an explicit product decision. |
| Three unchecked chart boxes with constellation lines visible | `smCons` currently gates field stars, constellation sticks, and constellation stars together | Leave `names` and `dot = integration` unchecked, make `names` hide both object and constellation labels, and leave `constellations` checked so the context chart remains visible. The one checked box is an explicit visual difference; an all-unchecked state would hide the reference sticks under the current control contract. |
| Tall-looking map plot | `skymap.js` maps coordinates on a strict 2:1 canvas | Fit/center the 2:1 canvas; never stretch the sky. |
| Reference legend's six distinct colors | Shared catalog palette differs and supports eight types | Add six map-local display colors for the reference types; preserve canonical colors/data elsewhere and expose extra types when present. |
| Reference caption controls | Native title bar sits outside the client reference and is owned by Windows | Keep the page client free of caption symbols and resize overlays; verify native behavior in a real Windows desktop session. |
| Full-width library import dock | Current dock is nested inside the center workspace | Move the same drop-zone node into a full-width grid row; its inset dashed drop area spans roughly 1512 px at the reference width. Preserve its ID/listeners. |

## 9. Review checklist for the later UI implementation

1. Confirm the packaged launch still opens `static/catalog.html`, opens Library
   in a fresh browser session, restores only a valid prior tab from
   `sessionStorage["nightseam_catalog_child"]`, and does not initialize/show
   planner chrome.
2. Capture all three empty states at their exact reference CSS viewport sizes.
   At 1586 × 992, verify the root remains 14 CSS px and compare boundaries, typography,
   icon silhouettes, and text placement before adjusting small spacing. Also
   compare 2000×800 with 2000×1400: text/control/icon sizes and horizontal
   gutters remain stable, while the workspace gains height. Verify shorter
   wide windows fit the usable client height and smaller clients reflow without
   shrinking below readable sizes or retaining oversized empty cards. Check
   actual panel/control bounds, not only page scroll width. Check that all Workbench
   colors come from the existing roles; the map legend's six display colors
   are the only local palette exception.
3. Repeat with real local FITS frames: verify automatic `IMAGETYP` handling,
   explicit frame overrides, copy-only archive behavior, errors/progress,
   history, session selection/details, object coordinates, and type filters.
4. Exercise all three tabs and top actions, file picker, drag/drop, keyboard
   tab traversal, legend keyboard toggles, chart hover/click, browser fallback,
   and stacked layouts at 480–500, 640, and 900 CSS px. Verify native caption,
   resizing, Snap, taskbar, keyboard, and physical 100/125/150/200% DPI separately
   in the packaged app. Test each chosen composition breakpoint at ±1 CSS px,
   aligned map axis tick centers, hover/selection, and DPR-aware canvas sizing.
5. Run the repository's existing catalog browser checks and relevant catalog
   tests during implementation; do not weaken tests to get screenshot alignment.

The standard Windows shell supersedes the earlier frameless caption-control
direction in this brief. Implementation evidence and native-verification limits
are tracked in `WINDOWS_SHELL_IMPLEMENTATION_PLAN.md`.
