# NightSeam Catalog design — Workbench

Workbench is the catalog's only design language. This document governs the
local application in `static/`: what each view is for, the visual system, and
the rules that keep both consistent. Tokens live in `static/themes.css`;
`static/style.css` composes them and holds no raw colors or sizes of its own.

## Principles

1. **Library first.** The catalog is an archive browser. The main loop is
   import → find an object → inspect a capture session → act on it. Every view
   serves one step of that loop.
2. **One primary action per view.** Amber marks the primary action, the
   current selection and keyboard focus — nothing else. If everything is
   emphasized, nothing is.
3. **Show what matters, disclose the rest.** Frequent actions stay visible;
   occasional maintenance lives one click away in the Tools menu; settings
   that are set once sit below the daily workflow.
4. **Measured values read as data.** Times, durations, coordinates and paths
   use the monospace face; counts use tabular figures and align right.
5. **Never color alone.** Status pairs color with a label, icon shape or
   strike-through, and every control has an accessible name.
6. **Stable density.** Resizing changes composition, never type, control or
   icon size.

## Visual system

### Color roles

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Page | `--bg` | `#0B1115` | Window background, inputs, chart surface |
| Panel | `--panel` | `#10181B` | Cards and rails |
| Raised | `--raised` | `#152027` | Buttons, menus, tooltips, toasts |
| Hover | `--hover` | `#1A2830` | Pointer feedback on rows and items |
| Separator | `--border-soft` / `--border` | `#1E2B31` / `#293840` | Content lines and panel edges |
| Control edge | `--border-strong` | `#5B717B` | Input and button boundaries (≥3:1) |
| Text | `--text` | `#E7ECE8` | Primary text |
| Secondary text | `--dim` | `#9CAEB1` | Labels, descriptions |
| Tertiary text | `--faint` | `#7F9296` | Counts, zero values, captions (≥4.5:1) |
| Accent | `--accent` | `#F9C85D` | Primary action, selection, focus |
| Link | `--link` | `#73B6DB` | Folder links and other in-content navigation |
| Good / Warning | `--good` / `--warn` | `#5BC68D` / `#E8A948` | Status, always with a label or shape |
| Blocked | `--bad` / `--bad-text` | `#C45B52` / `#E07B71` | Error marks; the lighter value for red text |

Use opaque surfaces and 1px lines. No blur, glow, starfields, decorative
gradients or translucent panels; soft amber and red tints only mark selection
and status. Shadows appear only on overlays (menus, dialogs, toasts, tooltips)
because they float above the page. Data-driven colors such as object-type map
colors remain data, not interface tokens. Canvas roles are the `--viz-*`
tokens, read through `static/js/viztheme.js`.

### Type

System UI stack for interface text, a monospace stack for measured values.
Five sizes cover the interface: 12px (captions, labels, table headers),
13px (body in dense regions, buttons), 14px (root, panel headings),
16px (session date, drop target, dialog titles) and 20px (view titles: the
selected object and Import exposures). Weights are 400, 500 and 600. Section
labels inside inspectors are 12px uppercase with slight tracking.

### Space, shape and density

A 4px grid (`--sp-1` … `--sp-8`). Panels use 6px radii, controls 4px.
Controls are 32px tall (28px compact) and never below the 24px target size;
table rows are 36px; the header is 48px. The 14px root and these sizes stay
fixed at every window size. OS scaling and browser zoom remain independent.

### Components

- **Buttons:** primary (amber fill), default (raised with control edge),
  subtle (text only). One primary per region.
- **Segmented control:** a 40px track for one choice among a few (frame type).
- **Switch:** a `role="switch"` checkbox between two named states, both
  labels visible and the active one emphasized.
- **Menu:** WAI-ARIA menu button. Arrow keys, Home and End move between
  enabled items; Escape returns focus to the button; Tab or an outside
  pointer closes it. Dialogs opened from a menu return focus to its button.
- **Empty states:** say what is missing and offer the next step once. The
  main region gets the full state with actions; rails and inspectors get a
  one-line inline notice.
- **Dialogs:** header with title and close, scrolling body, footer with the
  primary action last. Escape closes; focus is trapped while open.

## Native shell

The packaged catalog runs in a normal Windows frame. Windows owns the title,
application icon, caption buttons, system menu, moving, resizing and Snap; the
client begins below that chrome. Do not draw HTML caption buttons, resize
strips or title-bar drag regions. Browser fallback uses the browser's frame
and the same page.

## Header

One 48px bar: the quiet application name (no logo), the view tabs
(Library, Import, Sky Map), the busy indicator, the primary **Import** action
and the **Tools** menu. Tools groups the archive actions (Reindex folders,
Archive health), backup and export (Export library, Copy library) and the
support report, and shows the version. At narrow widths tab icons drop first,
then action labels (icons keep accessible names), then the application name;
the header never wraps.

## Library

Three regions at full width: the object rail, the selected object's
workspace and the session inspector.

- **Object rail:** search (name, designation or type), sort, and the object
  list. Each row shows the name, designation and type, and a session count.
  The selected row has an amber edge and tint.
- **Workspace:** the selected object's name is the view title, with its
  designation, type and switchable size unit (activate the unit to cycle),
  plus Edit (custom objects only) and Open folder. Open, unframed metrics
  follow: sessions, images, light integration and last session. Capture
  sessions are ordinary table rows: date with the local time range, frame
  counts per type (zeros recede), light integration, filters and a folder
  action. Clicking a row selects it.
- **Inspector:** session date, storage, session window and exposure totals;
  frames per type; filters as chips; every archive folder as a link. Actions
  sit at the bottom: Open session folder, Add calibration frames (+ Darks,
  + Flats, + Bias) and Storage (Compress, Uncompress — the one that cannot
  apply is disabled with its reason).

The Library has no exposure preview, thumbnail grid, row expansion or Trash
controls. An empty library shows one onboarding state with Import actions in
the sessions region.

## Import

Action first, then options, then settings. The left column holds the drop
target with **Choose files…** (primary) and **Choose folder…**, the archive
destination with a link to change it, the frame type (Auto, Lights, Darks,
Flats, Bias — Auto reads each header and is the default; an override applies
to the next import) and compression (lossless Rice with Fast/Safe
verification). **Archive settings** below hold the archive folder and the
observing-location fallback. The right column shows import results with live
progress (files processed / total, elapsed time, failures) and recent imports
with + Darks / + Flats / + Bias to attach calibration to that session.
Calibration requests identify the target object and session above the drop
target. Use "exposures" in visible copy.

## Sky Map

The map keeps its uniform 2:1 RA/Dec mapping and DPR-aware bitmap. Fit the
canvas to available space and align external axis tick centers, hover and hit
testing with that fitted rectangle. Thin labels when necessary; never stretch
one axis. Marker radius reflects capture-session count on a bounded
square-root scale in CSS pixels; image count and integration do not determine
size. Keep names near their markers with quiet leader lines when displaced.
The side column holds Display options, the Object types legend (activate a
type to show or hide it; hidden types are struck through) and the selected
object's summary.

## Composition

Let the header take its height and give each view the rest. Bounded rails,
flexible center; extra height reveals more rows and extra width widens the
workspace. Dense regions scroll internally on wide windows. Reflow before a
region becomes unreadable:

| Width | Library | Import | Sky Map |
| --- | --- | --- | --- |
| ≥ 1100px | Three columns, fits the window | Two columns, fits the window | Map beside side column (≥ 1001px) |
| 761–1099px | Objects beside workspace; inspector below | Single column, page scrolls (≤ 1100px) | Stacked (≤ 1000px) |
| ≤ 760px | Single column; collapsible object list | Single column | Stacked |

Primary actions and selected details remain reachable at every size.

## Accessibility

Target WCAG 2.2 AA. Text keeps ≥4.5:1 contrast and control boundaries ≥3:1
on every surface. Every control is keyboard operable with a visible 2px amber
focus ring; tabs use arrow keys. Honor reduced motion (no transitions or
spinner motion), increased contrast (stronger lines and text) and forced
colors (system highlight for selection, native checkboxes). Live regions
announce progress without re-announcing the clock every second.

## Implementation boundaries

The catalog uses the project-owned calculation snapshot in `core/`. Retained
planning, visibility, scoring and horizon modules keep their numerical
contracts; the planning shell and panorama editing UI were retired on
October 10, 2026. UI modules may format and arrange core results but must not
add a second calculation path. Keep storage paths safe and backups staged
before writes. Edit authored source modules and rebuild generated core trees
with `scripts/build_core.py`; never hand-edit generated core files.
