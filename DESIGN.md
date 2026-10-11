# NightSeam Catalog design — Workbench

Workbench is the only catalog design language. Old theme values normalize to workbench. This document governs the independent local application.

## Visual language

Use opaque surfaces and clear 1px separators. Keep radii between 4px and 7px.
Use the system sans stack for interface text and a monospace face for times,
coordinates, magnitudes, percentages, and other measured values. Do not add
blur, glow, starfields, decorative gradients, or translucent panels.

| Role | Color |
| --- | --- |
| Page and chart | `#0B1115` |
| Panel | `#10181B` |
| Raised surface | `#152027` |
| Border | `#293840` |
| Soft border | `#1E2B31` |
| Main text | `#E7ECE8` |
| Muted text | `#9CAEB1` |
| Action and selected | `#F9C85D` |
| Good | `#5BC68D` |
| Warning | `#E8A948` |
| Blocked | `#C45B52` |
| Chart grid | `#304049` |
| Secondary line | `#73B6DB` |

Keep these semantic roles in `static/themes.css` and the
canvas adapters. Data-driven colors such as catalog type colors remain data, not
interface theme tokens.

## Native shell

The packaged catalog runs in a normal Windows frame. Windows owns its title,
application icon, caption buttons, system menu, moving, resizing, and Snap
behavior; the Workbench client begins below that OS chrome. Do not draw HTML
caption buttons, resize hit strips, or title-bar drag regions. The catalog
toolbar and nested tabs remain application content. Browser fallback uses the
browser's own frame and the same Workbench page.

## Library

Keep the object rail, Capture Sessions workspace and selected-session inspector. Active object facts and session/image/integration counters are open text, without framed cards. Activate the displayed size unit to switch units. Keep the toolbar compact and without a logo.

Map dot radius reflects capture-session count. Keep object labels near their dots and draw a leader when displaced. Progress shows processed files / total and elapsed time; failures add a counter. Calibration actions clearly identify the selected object/session. Use exposures in visible copy and Import for the toolbar action.

Preserve keyboard operation, visible focus, accessible names, reduced motion and non-color status labels. Native DPI scaling and browser zoom remain independent of window resizing.

The packaged catalog uses stable interface density. At unchanged OS DPI and
browser zoom, resizing changes available workspace and composition, not root
font size, control size, icon size, or stroke weight. Keep its 14px root and
explicit text roles scoped to the catalog shell; normal OS scaling, browser
zoom, and accessibility preferences remain independent. The reference images
define the baseline hierarchy at 1585/1586×992 CSS px, not a proportional
magnification rule for larger windows.

Let the catalog toolbar and tabs take their natural height, then give the
workspace the remaining client height. Use bounded side rails and flexible
central content; extra height reveals more rows and extra width expands the
workspace. Wide layouts scroll inside dense regions. Reflow before a region
becomes unreadable: use two-column layouts at intermediate widths and compact
object navigation above sessions at narrow widths. Stacked cards use natural
heights and page scrolling rather than large fixed empty-state minima. Keep
primary actions and selected details reachable at every composition.
In the catalog shell, Library fills the height remaining below the toolbar
and tabs; flexible session and inspector regions use that space without
reserving rows for retired controls.
The packaged toolbar has no logo and takes only the height needed by its
buttons, with compact vertical padding; a quiet application name may remain.

The archive Sky Map retains its uniform 2:1 RA/Dec mapping and DPR-aware bitmap.
Fit the canvas to available space and align external axis tick centers, hover,
and hit testing with that same fitted rectangle. Thin labels when necessary;
never stretch one coordinate axis to match the illustrated reference.
Size markers by capture-session count using a bounded square-root scale in
CSS pixels; image count and integration do not determine radius. Keep drawing
and hit testing consistent with those sizes.
Keep names close to their markers; connect displaced names to their markers
with quiet leader lines so dense groups remain understandable.

## Implementation boundaries

The catalog uses the project-owned calculation snapshot in `core/`. Retained
planning, visibility, scoring and horizon modules keep their numerical contracts;
the planning shell and panorama editing UI were retired on October 10, 2026.
UI modules may format and arrange core results but must not add a second
calculation path. Keep storage paths safe and backups staged before writes.
Edit authored source modules and rebuild generated core trees with
`scripts/build_core.py`; never hand-edit generated core files.
