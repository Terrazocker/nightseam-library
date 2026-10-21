# Catalog window resize fix plan

Date: 2026-09-30. Status: historical repair, superseded by the standard native
Windows frame. Its source-derived diagnosis and verification below describe the
earlier frameless implementation; the custom gesture controller and bridge have
since been removed as part of `WINDOWS_SHELL_IMPLEMENTATION_PLAN.md`.

The broader Windows title-bar and shell feature plan is now
`WINDOWS_SHELL_IMPLEMENTATION_PLAN.md`. It recommends a native Windows frame
instead of extending this custom gesture implementation. The diagnosis below
records the pre-repair defects; they are not current confirmed failures.

The original user report was that resizing the catalog window made it jump or
resize erratically. At the time, this plan applied to the frameless catalog
shell on `codex/direction-b-ui` (`f5bb0c5`). Its source planner and ordinary
browser fallback were left unchanged; the entire custom gesture path was later
replaced by the standard-frame implementation described in the newer plan.

## Confirmed findings

### 1. Pointer deltas use a moving coordinate origin

`static/js/catalog_main.js::bindNativeResize` records `clientX/clientY` at
pointerdown and subtracts those values on pointermove. `main.py::resize_window`
moves the native window when resizing from its left or top. Moving the window
changes the viewport origin, so an almost stationary screen pointer produces
different client-coordinate deltas on successive events.

A read-only Node harness executing the CURRENT handler with a mock native
bridge reproduced window-left positions `120 -> 101 -> 120 -> 101` as the
screen pointer moved only one additional pixel and then stayed still. This is
a confirmed explanation for the reported jumping, particularly on left/top
edges and their corners.

### 2. An ended gesture can become active after an await

The pointerdown handler awaits `window_geometry()` before assigning `active`.
If pointerup happens during that wait, `finish()` sees no active gesture.
When the promise resolves, the finished gesture is activated anyway. The same
source-derived harness reproduced a resize on ordinary pointer movement AFTER
release. Pending geometry needs an identity and an explicit cancelled state.

### 3. Returning to the starting position can leave the window displaced

`main.py::resize_window` calls `resize()` first, then calls `move()` only when
the target position differs from the ORIGINAL position. After a previous
left/top update moved the window, a delta of zero skips the move back to the
original position. A fake-window call sequence with left deltas `-20`, then
`0`, leaves x at `80` instead of restoring x to `100`.

The installed pywebview 6.2.1 backend defaults to a north-west fixed point for
`resize()`. Therefore that resize call alone does not restore the origin.

## Related weaknesses and verification limits

- Pointer capture failures are ignored. There is no `lostpointercapture` or
  window-blur cleanup, no primary-button guard, and no geometry rejection handler.
- Queued bridge requests have no gesture identity. Finishing a gesture clears
  `active` but does not explicitly discard queued work. A delayed old request
  must not overwrite a newer gesture or its state.
- Left/top updates currently use separate native resize and move operations.
  The installed Windows backend supports `FixPoint` opposite-edge anchoring
  and performs that anchored resize with one `SetWindowPos` operation.
- `easy_drag=True` enables broad pywebview dragging. This is a potential
  interaction conflict, not a proven cause: the current pointerdown calls
  `preventDefault()`, which normally suppresses compatibility mouse events.
- The Python maximize flag follows native events, but the DOM state currently
  refreshes only at initial attachment and custom button actions. Native state
  changes need verification and cancellation of any active resize.
- All eight HTML handles and their viewport-fixed CSS strips/corners exist.
  No obvious hit-testing defect was found by source inspection. Sky Map already
  observes its plot size and rebuilds its DPR-aware canvas.
- Existing browser catalog coverage does not exercise native edge resizing.
  The diagnosis used the actual JS handler, fake native geometry, and installed
  library source; a live native-window/DPI reproduction was not performed.

## Recommended implementation sequence

### 1. Pin the failures before changing behavior

Add focused tests exercising the actual resize controller with a mocked bridge
and the Python bridge with a fake window. Pin moving-origin oscillation,
release-before-geometry, and return-to-origin behavior. Include top and corner
variants, not only the left edge. Keep the regression tests independent of the
implementation's intermediate arithmetic.

### 2. Establish a stable pointer and native geometry contract

Use screen-relative pointer movement rather than viewport-relative movement.
Keep the initial pointer and native rectangle immutable for each gesture.
Define the unit boundary explicitly: the installed WinForms backend exposes
and accepts logical geometry, converting to physical pixels internally.
Verify WebView2 screen-coordinate units at the supported Windows display
scales; do not multiply by `devicePixelRatio` without measurement.

Compute dimensions from the gesture's ORIGINAL dimensions plus the latest
absolute delta. Clamp to the native minimum size while keeping the opposite
edge fixed. Define behavior for changing displays/DPI during a drag; cancel
and require a fresh gesture if the coordinate conversion becomes invalid.

### 3. Make gesture lifecycle and queued work explicit

Introduce starting/active/finished states with a monotonic gesture identity.
Create the starting state synchronously before awaiting geometry. Recheck the
identity after every await and before dispatching work. Accept only a primary
pointer/left-button gesture and handle capture failure explicitly.

Keep at most one bridge update in flight and one latest pending update;
coalesce pointer movement to animation frames. Preserve the final pointerup
position for a successful gesture. Cancellation, lost capture, blur, bridge
failure, and maximize must invalidate pending work and delayed continuations.
Ensure an old in-flight operation cannot overwrite a newly begun gesture;
serialize native operations and add native gesture validation if JS ordering
alone cannot guarantee this. Surface bridge failures without repeated toasts.

### 4. Apply anchored native geometry once per update

Prefer the existing public pywebview `resize(width, height, fix_point=...)`
with the correct opposite fixed edges. For example, a left-edge drag fixes
east, a top-edge drag fixes south, and a top-left drag fixes south-east.
Remove the separate move and original-position comparison from this resize
path. Verify that the unchanged opposite edges remain fixed across repeated
updates, reversals, and minimum-size clamping.

Restrict header dragging to its explicit drag region so resizing has one owner.
Synchronize native maximize/restore state with handle visibility and gesture
cancellation. Keep native controls hidden in the browser fallback.

Use this bounded repair first. A custom Windows hit-test/system-resize adapter
is a fallback only if the public pywebview bridge cannot pass smoothness and
DPI checks; it would require a separate platform-specific design and tests.

### 5. Verify interaction and responsive layout

Automated cases must cover all eight directions, stationary screen pointers,
direction reversal, return to the original size/position, minimum clamping,
fast release during geometry lookup, capture loss, rejected promises, delayed
old updates, and a new drag immediately after a completed/cancelled drag.

Native Windows checks must cover continuous slow/fast dragging, release outside
the original bounds, header dragging, minimize, maximize/restore, and native
state changes at 100%, 125%, 150%, and 200% display scaling. Include a second
monitor, negative screen coordinates, and mixed-DPI monitors when available.

Check Library, Import Exposures, and Sky Map at the native minimum, 900x800,
1240x900, the reference 1586x992 client, and a large display. Controls remain
reachable, page overflow is controlled, and Sky Map labels, hit testing, and
canvas sizing remain correct after repeated resizing.

During implementation run the affected Python and JS behavior tests first,
then `python scripts/check_js.py` and the browser gate against a fresh dirty
development build. A browser viewport test alone cannot establish native
resize correctness. Run `python -m pytest tests/ -q --tb=no` once at the end.
Record native results and unresolved hardware coverage explicitly.

## Acceptance criteria

- Dragging an edge follows the screen pointer smoothly with no back-and-forth
  position changes when the pointer is stationary.
- Opposite edges remain fixed, including at minimum size and when reversing.
- Returning to the starting pointer restores the original rectangle.
- Releasing/cancelling never resurrects resizing after a delayed bridge reply.
- Old gestures cannot affect newer gestures; maximize blocks resizing.
- Workbench layouts and Sky Map remain usable after resizing at tested sizes.

At the start of implementation, only this plan and the confirmed finding in
`BUGS.md` had been added. No dependencies, tags, or deployments are needed for
the repair.

## Reference material

- `main.py::JsApi.resize_window`, `window_geometry`, `run_native_window`.
- `static/js/catalog_resize.js::bindNativeResize`, `bindNativeWindowDrag`;
  `static/js/catalog_main.js::bindNativeWindowControls`.
- `.venv-build/Lib/site-packages/webview/window.py::FixPoint`, `Window.resize`.
- `.venv-build/Lib/site-packages/webview/platforms/winforms.py::BrowserView.resize`,
  `get_position`, `get_size` (installed 6.2.1; do not edit bundled dependencies).
- [MDN client coordinates](https://developer.mozilla.org/en-US/docs/Web/API/MouseEvent/clientX),
  [screen coordinates](https://developer.mozilla.org/en-US/docs/Web/API/MouseEvent/screenX),
  and [capture loss](https://developer.mozilla.org/en-US/docs/Web/API/Element/lostpointercapture_event).
- [Microsoft SetWindowPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos).

## Historical implementation and verification record (before native-frame migration)

- The frameless resize and title-drag controller now lives in
  `static/js/catalog_resize.js`. Each gesture captures its starting screen
  point and native rectangle once, calculates absolute deltas in screen CSS
  pixels, coalesces movement to one in-flight native update plus the latest
  pending point, and preserves the pointer-up position. Release, cancellation,
  capture loss, focus loss, page hide, maximize, and a device-scale change
  invalidate the gesture. CSS deltas are not multiplied by `devicePixelRatio`.
- `main.py::JsApi` assigns native gesture ids and rejects stale calls. Resize
  uses one pywebview `FixPoint` operation per update, anchoring the opposite
  edge or corner, including through minimum-size clamping. It no longer follows
  resize with a conditional window move. Title dragging is explicit on the
  brand region; pywebview `easy_drag` and catalog WebView zoom are disabled for
  stable pointer geometry. The source planner keeps its own launch options.
- The catalog header now avoids the 640-pixel horizontal overflow found by the
  packaged browser gate. The browser coverage also checks native control and
  handle visibility through maximize/restore and external native-state changes,
  plus the Library, import, and Sky Map layouts at 640x480, 900x800, 1240x900,
  1586x992, and 2000x1400.
- Focused regressions cover all eight edges, reversals, return-to-origin,
  minimum clamping, screen-relative stationary pointers, delayed/rejected
  bridge replies, coalescing, stale gestures, cancellation, and native API
  configuration.

Automated verification completed:

- `node tests/js/catalog_resize.mjs` — passed.
- `python -m pytest tests/test_window_resize.py tests/test_js_behaviour.py -k "window_resize or pywebview_easy_drag or explicit_header_drag or catalog_native_resize" -q --tb=short` — 9 passed, 20 deselected.
- `python scripts/check_js.py` — passed (including JS syntax, DOM ids, encoding, catalog tokens, and core checks).
- `python scripts/check_browser.py --desktop --only catalog` — 3/3 passed against the freshly built executable.
- `python -m pytest tests/ -q --tb=no` — 676 passed, one existing Starlette/httpx deprecation warning.
- `build.ps1 -AllowDirty` — built `dist/NightSeam.exe`, version `0.6.34-10-gf5bb0c5-dirty`.

The browser gate verifies the packaged shell and mocked native bridge, not real
Windows edge dragging. Physical native checks at 100%, 125%, 150%, and 200%,
negative coordinates, a second display, and mixed-DPI monitors are still
outstanding; the gesture deliberately cancels and requires a fresh drag when
the reported WebView device scale changes.
