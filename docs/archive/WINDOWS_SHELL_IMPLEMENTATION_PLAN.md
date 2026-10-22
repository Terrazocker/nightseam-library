# Standard Windows shell implementation plan

Date: 2026-09-30. Status: implemented in the current working tree; no commit,
tag, release, or deployment was made. Automated verification and native-host
limitations are recorded in the implementation report near the end.

## Goal and recommended architecture

Give the catalog application normal Windows window behavior: an identifiable
title bar that moves the window, native resizing, caption buttons, system menu,
snapping, keyboard access, and reliable restore behavior. Use a standard Windows
frame and title bar, with Workbench application content below it.

Windows owns the caption, borders, move/size interaction, shadows, system menu,
and supported shell interactions. NightSeam owns its catalog toolbar, tabs,
content, and local data. Do not use browser pointer streams as the primary
window manager. This requires no switch to Electron, WinUI, or another UI stack.

This recommendation intentionally replaces the earlier screenshot brief's
frameless/custom-control requirement. It follows the user's latest request for
normal Windows features. Keep Workbench as the sole application design language;
OS light/dark/high-contrast caption rendering is platform behavior, not a second
NightSeam theme.

## Baseline recheck (historical at plan start)

At the start of this plan, the resize repair in
`WINDOW_RESIZE_FIX_PLAN.md` was present in the working tree:

- `catalog_resize.js` uses screen-relative deltas and explicit gesture states.
- `JsApi` has gesture ids, ordering/cancellation, and opposite-edge anchoring.
- Current targeted verification: `tests/test_window_resize.py` passed 8 tests;
  `node tests/js/catalog_resize.mjs` passed all checks. One Python run reported
  a pytest cache-write warning. No new full-suite or browser build was run.

Those checks verify the repair with mocked native windows. They do not verify
real Windows caption, Snap, DPI, or monitor behavior. The old feedback/race
failures should not be presented as current confirmed defects.

Gaps in the frameless baseline at plan start:

| Area | Current evidence |
|---|---|
| Moving | Only the brand block has `data-window-drag`; blank header space does not move the window. |
| Maximized dragging | Both the JS controller and Python bridge refuse move gestures while maximized; caption drag-to-restore is unavailable through this path. |
| Caption gestures | No application implementation of title-bar double-click or right-click system-menu integration was found. |
| Hit targets | Caption controls begin 3px from the top, while higher-layer resize strips cover roughly the top 6.3px; the top-right corner also overlaps part of Close. |
| Caption sizing | Caption buttons use rem and grow with catalog viewport scaling, independently of Windows caption metrics. |
| Activation appearance | No inactive-window caption styling is implemented in the HTML shell. |
| Placement | No remembered normal bounds/maximized state or monitor-removal recovery was found. |

At that baseline, the catalog was `frameless=True` in `main.py`. Installed pywebview 6.2.1
sets the Windows form border to `None` in that mode. Its public move/resize
methods reposition geometry; they do not provide a complete Windows caption.
Some keyboard/shell features may still work on the current borderless window.
Do not label Alt+F4, Alt+Space, Win+Arrow, taskbar restore, or Snap universally
broken without checking the actual window.

## Intended window and design contract

The visible structure is:

1. **Native title bar:** application icon, `NightSeam — Image Catalog`, a broad
   draggable title/background region, and OS minimize/maximize/restore/close.
2. **Workbench toolbar:** catalog identity and Import FITS, Reindex folders,
   Export library actions. These remain application controls.
3. **Workbench tab strip:** Library, Import Exposures, Sky Map.
4. **Workspace:** the current catalog views and responsive layouts.

Use the actual Windows caption metrics and native accessible controls. Do not
scale the native title bar using `--catalog-scale` or duplicate its caption
buttons inside the toolbar. Keep app actions outside drag regions. Let Windows
draw its standard border/shadow and supported corners. Do not force acrylic,
blur, or Mica into the Workbench client area.

Workbench continues to use opaque surfaces, separators, compact typography,
brass primary actions, restrained radii, visible focus, and existing role tokens
from `DESIGN.md`. Caption activation, high contrast, text scale, and native
light/dark rendering follow platform capabilities. The installed backend already
has Windows title-bar appearance support; verify it before adding adapters.

## Required behavior and acceptance

| Feature | Acceptance check |
|---|---|
| Move | Drag any ordinary caption title/background space; moving never invokes app actions. |
| Maximize/restore | Native buttons and caption double-click toggle state; drag a maximized caption down to restore. |
| Resize | Every edge/corner works with standard cursors; caption controls remain distinct from resize targets. |
| System menu | Caption right-click, app-icon menu, and Alt+Space expose OS Move/Size/Minimize/Maximize/Restore/Close as applicable. |
| Keyboard | Alt+F4 closes through the existing close lifecycle; Win+Arrow uses OS arrangements; no app handlers override these shortcuts. |
| Snap | Edge/top dragging, Win+Arrow, and Windows 11 Win+Z/maximize-hover layouts work where enabled by OS policy. |
| Taskbar | Correct app identity/icon; minimize and restore work via taskbar and Alt+Tab. |
| Activation | Native caption shows active/inactive and high-contrast states correctly. |
| Accessibility | Native caption controls expose their OS names/actions; content retains keyboard focus and labels. |
| Bounds | Maximize respects the work area/taskbar; restore returns to normal bounds. |
| Restart | Remember normal bounds and maximized state; never reopen minimized or entirely off-screen. |
| Displays | Moving/resizing works with negative coordinates, differing DPI, and monitor disconnection. |
| Browser fallback | Browser/Edge app-mode owns its chrome; no duplicate NightSeam caption controls or resize overlays. |

Windows 11 Snap support also depends on minimum size. The current 640px minimum
can exclude narrow Snap zones. Target a native minimum width of at most 500
effective pixels, after making the client usable at that width. Measure the
outer-window/client-area difference rather than assuming the pywebview size
equals the content viewport. Do not claim all Snap arrangements work until the
native window actually fits their zones. Keep height at a usable measured
minimum; allow vertical content scrolling rather than hiding primary actions.

## Ordered implementation phases

### Phase 1 — Establish the native frame

In `main.py::run_native_window`, use a normal resizable frame for the catalog
and title it `NightSeam — Image Catalog`. Preserve the planner launch path and
browser fallback. Use the existing application icon in source and packaged
launches; inspect the backend's icon behavior instead of adding a new dependency.

Make chrome ownership explicit in a small host capability contract. A bridge
being present must no longer imply that HTML caption buttons should appear.
The catalog should recognize native-frame and browser modes and initialize
only appropriate host features, such as the native folder picker.

Perform a real Windows smoke check at this phase: caption movement, double-click,
resize, system menu, maximized drag-to-restore, taskbar restore, and Snap. This
checks the selected host architecture before layout polish or removal of the
old compatibility path. Do not edit installed pywebview files.

### Phase 2 — Separate application chrome from window chrome

Remove the HTML caption-button cluster, native resize overlays, and window-drag
attributes from `static/catalog.html`. Adjust `static/themes.css` so the
Workbench header is an application toolbar with no caption reserve or resize
hit strips. Keep the catalog actions and tabs accessible at narrow widths.

Update `catalog_main.js` to stop initializing frameless move/resize controllers
and maximize polling for the native-frame path. After native smoke acceptance,
remove unused gesture bridge methods/module code and tests that only pin the
obsolete implementation. Retain meaningful coverage for the new chrome ownership,
launch options, application actions, and browser fallback. Avoid shipping two
window managers or a user-selectable chrome/theme toggle.

### Phase 3 — Fit real Windows Snap sizes

Make Library, Import Exposures, and Sky Map usable at a 480–500 effective-pixel
window width, as well as existing 640/900/1240/reference/large sizes. Stack rails,
wrap toolbar actions or use a compact overflow control, and scroll dense tables
inside their regions. Keep primary actions, tab navigation, and selected-session
details reachable. Verify actual client widths after native frame overhead.

The existing content scale remains a client-area calculation. Native caption
dimensions do not participate in it. Sky Map continues sizing to its actual
plot area and DPR; verify coordinates, labels, and hit testing after Snap.

### Phase 4 — Remember safe local window placement

Add a small host-side placement component using project-owned code and the
existing local settings/storage conventions. Record the NORMAL rectangle and
maximized state separately. Never overwrite the normal rectangle with snapped,
maximized, or minimized dimensions without an explicit policy for that case.
Debounce geometry persistence and flush at a safe close boundary.

On launch validate numeric bounds and current monitor work areas, clamp size
to the usable area/minimum, and ensure the caption is reachable. Handle unplugged
displays and negative coordinates. Keep placement machine-local; a setup restore
must not force another machine's inaccessible screen coordinates.

For first launch, retain the existing brief's maximized-start intent unless the
product owner chooses another default. Later launches restore saved normal or
maximized placement, never a minimized state. Verify source and frozen data
locations through the existing safe path helpers.

### Phase 5 — Verification and documentation

Replace current tests that hard-pin `frameless=True` with tests for native-frame
configuration, explicit chrome capabilities, correct icon/title, and placement
validation. Add behavior checks for no duplicate client controls and all catalog
actions remaining clickable. Keep browser viewport checks for content reflow;
they cannot establish native caption behavior.

Native acceptance must cover Windows 10 and Windows 11 where available, all
eight resize directions, caption gestures/system menu, supported Snap layouts,
taskbar/keyboard actions, 100/125/150/200% DPI, mixed-DPI and negative-coordinate
monitors, monitor removal, and restart placement. Record unavailable hardware/OS
coverage explicitly. Check small Snap zones with the real minimum size.

During implementation run relevant tests and `python scripts/check_js.py`, build
a fresh dirty development executable, and run the affected catalog browser gate.
Check the source planner and browser fallback. Run the full pytest suite once at
the end. No tag, release, or deployment is part of this plan.

Update `DESIGN.md` and the catalog visual specification to state that native
window chrome sits outside the reference CLIENT area. Reference content captures
remain at 1585/1586x992 CSS pixels; whole-window screenshots necessarily include
additional OS chrome. Update the obsolete frameless-control comparison rows and
mark the earlier resize repair as historical compatibility work.

## Optional later alternative: branded native caption

If a fully branded single title bar becomes a requirement, design a dedicated
Windows custom-frame adapter. Preserve native window styles and system move/size
loops, implement non-client caption/edge hit tests, exclude app controls, and
delegate caption-button handling appropriately. Windows 11 Snap hover needs
native maximize-button integration. Include system-menu, DPI, work-area,
activation, accessibility, and teardown tests.

This is a separate, larger platform project. Expanding the JS drag rectangle or
adding double-click handlers alone does not establish native Windows behavior.
The standard native caption is the recommended first delivery for this request.

## References

- `main.py::run_native_window`, `JsApi.host_capabilities`.
- `app/window_placement.py`, `app/paths.py::window_placement_path`.
- `static/catalog.html`, `static/js/catalog_main.js`, `static/themes.css`.
- `tests/test_window_resize.py`, `tests/browser/catalog_suite.py`.
- Installed pywebview 6.2.1 Windows backend and [public API](https://pywebview.flowrl.com/api/).
- [Microsoft title-bar design](https://learn.microsoft.com/en-us/windows/apps/design/basics/titlebar-design).
- [Microsoft Windows 11 Snap integration and minimum-size guidance](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-snap-layout-menu).
- [Microsoft custom native frames](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe).

## Implementation report and measured verification

The planned code changes are complete in the working tree; native user-input
acceptance checks remain open because this session exposes no inspectable app
window or screen surface:

1. The packaged catalog requests `frameless=False`, `resizable=True`,
   `min_size=(500, 380)`, title `NightSeam — Image Catalog`, the existing
   `static/favicon.ico`, and a maximized first launch. Source planner options
   and the regular browser fallback remain separate. A host capability reports
   native-frame or browser mode and gates only the native folder picker.
2. The page caption cluster, eight resize overlays, title-drag attribute,
   maximize polling, JavaScript gesture module, and custom Python gesture API
   have been removed. The page contains only the Workbench toolbar and tabs.
3. The catalog reflows its three views and its browser gate checks 480 and 500
   CSS px as well as 640/900/reference/large widths. The 480/500 checks cover
   tab/action bounds, selected-session reachability, import action, and Sky Map
   canvas coordinates/hit testing.
4. Placement is stored atomically at `%LOCALAPPDATA%\NightSeam\window-placement.json`,
   outside the setup database and its backups. Normal bounds and maximized state
   are separate. Minimize never becomes a saved launch state; negative monitor
   coordinates are accepted; saved bounds clamp to a current work area; a
   removed monitor recovers to the current primary work area while preserving a
   valid saved maximize state. A snapped rectangle is reopened as ordinary
   normal bounds by policy.
5. `DESIGN.md`, the catalog visual specification, this plan, the earlier resize
   history, and `BUGS.md` now describe the standard frame and verification
   limits.

Automated checks completed on 2026-09-30:

- `python -m pytest tests/test_window_resize.py tests/test_js_behaviour.py
  -q --tb=short` — **33 passed**.
- `python scripts/check_js.py` — **passed** (imports, DOM IDs, syntax, encoding,
  catalog tokens/provenance, and shared-core guards).
- `python scripts/check_browser.py --desktop --only catalog` — **3/3 passed**
  against fresh `dist/NightSeam.exe` version
  `0.6.34-10-gf5bb0c5-dirty`. This includes two source-planner browser checks,
  catalog actions and tabs, 480/500 CSS px reflow, and a browser page with no
  pywebview bridge. These browser checks are not native-window proof.
- `build.ps1 -AllowDirty` — fresh 20.5 MB executable built at 23:25:36 UTC.
- `python -m pytest tests/ -q --tb=no` — **680 passed**, with one upstream
  Starlette/httpx deprecation warning, in 204.07 seconds.

Native verification available in this environment:

- Build/runtime platform: Windows 11 build `10.0.26200`; installed pywebview
  6.2.1. `webview.screens` reports one display at logical `(0, 0)`,
  `2560×1440`, work area `(0, 0, 2560×1392)`, scale `1.0` / 96 DPI.
- The isolated packaged executable started its local catalog server and
  returned HTTP 200 from `/api/state`. The launch session exposed no inspectable
  top-level NightSeam HWND (`MainWindowHandle=0`); screen capture failed with an
  invalid-handle error. This tool session did not provide a desktop surface for
  caption/input inspection.
- Therefore real caption movement/double-click/drag-to-restore, all eight
  native resize directions, system menu, Snap/Win+Arrow/Win+Z, taskbar restore,
  Alt+F4/Alt+Space, native icon presentation, actual client minimum width, and
  125/150/200% or mixed-DPI behavior remain unverified. Physical monitor
  removal/restart recovery also remains unverified; the placement policy is
  covered with isolated screen fixtures.

No commit, tag, release, deployment, or dependency change was made.
