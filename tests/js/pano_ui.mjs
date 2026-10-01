/*
 * Panorama editor UI logic (pure): undo/redo history, dirty/saved state and the
 * presentation-only zoom/view transform in web/horizon.js. Driven directly in
 * Node (localStorage stub needed only because the module loads its pano db at
 * import time).
 *
 * The zoom math is DPR-independent BY CONSTRUCTION: editorScreenToBase takes
 * the LOGICAL mask size and logical screen coords, never a device-pixel ratio -
 * so the geometry cannot drift with DPR. The tests below pin that contract plus
 * the history invariants. Prints one marker line; test_js_behaviour.py asserts it.
 */
globalThis.localStorage = {
  _m: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._m, k) ? this._m[k] : null; },
  setItem(k, v) { this._m[k] = String(v); },
};

// stub window so horizon.js registers its beforeunload handler for inspection
globalThis.window = {
  _h: {},
  addEventListener(type, fn) { this._h[type] = fn; },
  removeEventListener() {},
};

const H = await import(new URL('../../web/horizon.js', import.meta.url).href);

let ok = true;
const fail = (m) => { ok = false; console.log('  FAIL:', m); };
const assert = (c, m) => { if (!c) fail(m); };
const near = (a, b, e = 1e-9) => Math.abs(a - b) <= e;

const pano = () => ({ azOffset: 0, altBottom: 0, altTop: 60, fov: 360,
                      paintMode: 'obstacle', strokes: [] });

/* ----------------------------- history ----------------------------- */
const p = pano();
const h = H.newHistory(p);
assert(!H.historyDirty(h, p), 'fresh editor is clean');
assert(!H.historyCanUndo(h) && !H.historyCanRedo(h), 'no undo/redo at start');

p.strokes.push({ m: 'b', x: 10, y: 10, r: 3 });
H.historyCommit(h, p);
assert(H.historyDirty(h, p), 'edit marks dirty');
assert(H.historyCanUndo(h) && !H.historyCanRedo(h), 'undo available after edit');

H.historyUndo(h, p);
assert(p.strokes.length === 0, 'undo restores the previous state');
assert(!H.historyDirty(h, p), 'undo back to the saved state is CLEAN');
assert(H.historyCanRedo(h), 'redo available after undo');

H.historyRedo(h, p);
assert(p.strokes.length === 1, 'redo reapplies the edit');
assert(H.historyDirty(h, p), 'redo marks dirty again');

// a new edit after undo invalidates redo
H.historyUndo(h, p);
p.strokes.push({ m: 'b', x: 99, y: 5, r: 2 });
H.historyCommit(h, p);
assert(!H.historyCanRedo(h), 'new edit drops the redo branch');

// multiple undos walk the whole history
const h3 = H.newHistory(pano());
const p3 = pano();
for (let i = 0; i < 5; i++) { p3.strokes.push({ m: 'b', x: i, y: 0, r: 1 }); H.historyCommit(h3, p3); }
for (let i = 0; i < 5; i++) H.historyUndo(h3, p3);
assert(p3.strokes.length === 0 && !H.historyDirty(h3, p3), 'undo multiple steps to baseline');

// history is bounded
const h4 = H.newHistory(pano());
const p4 = pano();
for (let i = 0; i < H.HISTORY_LIMIT + 40; i++) {
  p4.strokes.push({ m: 'b', x: i % 720, y: 0, r: 1 });
  H.historyCommit(h4, p4);
}
assert(h4.entries.length <= H.HISTORY_LIMIT, `history bounded (${h4.entries.length})`);

// save advances the baseline
H.historyMarkSaved(h, p);
assert(!H.historyDirty(h, p), 'Save clears the dirty flag');
p.strokes.push({ m: 'b', x: 1, y: 1, r: 1 });
H.historyCommit(h, p);
assert(H.historyDirty(h, p), 'a later edit is dirty again');
H.historyUndo(h, p);
assert(!H.historyDirty(h, p), 'undoing the later edit returns to saved');

// snapshot round trip covers calibration AND strokes
const src = { azOffset: 12, altBottom: -5, altTop: 40, fov: 180, paintMode: 'sky',
              strokes: [{ m: 'e', x: 1, y: 2, r: 3 }] };
const snap = H.snapshotPano(src);
const dst = pano();
H.applySnapshot(dst, snap);
assert(dst.azOffset === 12 && dst.altBottom === -5 && dst.altTop === 40, 'applySnapshot calibration');
assert(dst.fov === 180 && dst.paintMode === 'sky', 'applySnapshot fov/mode');
assert(dst.strokes.length === 1 && dst.strokes[0].m === 'e', 'applySnapshot strokes');

/* ----------------------------- zoom / view ----------------------------- */
assert(H.editorClampZoom(0.5) === 1 && H.editorClampZoom(99) === 8
       && H.editorClampZoom(2.5) === 2.5, 'zoom clamp 1..8');

const W = 720, Hh = 360;
for (const z of [1, 2.5, 8]) {
  const m = H.editorViewMatrix(W, Hh, z, null, null);
  assert(m.a === m.d && m.a === z, `uniform scalar at zoom ${z}`);
  assert(near(m.a * (W / 2) + m.e, W / 2), `centre maps to centre at zoom ${z}`);
  assert(near(m.d * (Hh / 2) + m.f, Hh / 2), `centre maps to centre (y) at zoom ${z}`);
}

// screen -> base round-trips through the matrix (any focus, any zoom)
for (const z of [1, 2.5, 8]) {
  for (const [fx, fy] of [[null, null], [300, 200], [600, 40]]) {
    const [bx, by] = H.editorScreenToBase(W, Hh, z, fx, fy, 400, 180);
    const m = H.editorViewMatrix(W, Hh, z, fx, fy);
    assert(near(m.a * bx + m.e, 400, 1e-6), `round-trip x (z=${z}, fx=${fx})`);
    assert(near(m.d * by + m.f, 180, 1e-6), `round-trip y (z=${z}, fy=${fy})`);
  }
}

// the centre of the canvas maps to the focus point
const [cbx, cby] = H.editorScreenToBase(W, Hh, 4, 300, 200, W / 2, Hh / 2);
assert(near(cbx, 300) && near(cby, 200), 'canvas centre is the focus point');

// focus clamping keeps the visible window inside the mask; zoom 1 is centred
let [cfx, cfy] = H.editorClampFocus(W, Hh, 4, -1000, 9999);
assert(cfx >= W / 8 - 1e-9 && cfx <= W - W / 8 + 1e-9, 'focus clamped x');
assert(cfy >= Hh / 8 - 1e-9 && cfy <= Hh - Hh / 8 + 1e-9, 'focus clamped y');
const [n1, n2] = H.editorClampFocus(W, Hh, 1, 123, 456);
assert(n1 === null && n2 === null, 'zoom 1 always centred');

// the guide/cursor transform is the SAME function the editor draws with, and it
// carries no DPR input (geometry cannot depend on the device pixel ratio)
assert(H.editorScreenToBase.length === 8 - 1 || H.editorScreenToBase.length === 7,
  'editorScreenToBase takes logical coords only (no DPR argument)');

/* ---------------- switching flush + beforeunload ---------------- */

// 1c (data layer): flushing an edited panorama persists it under ITS OWN id and
// never leaks its strokes into the next panorama; switching selection keeps them
// apart. (The full `flushHorizonEdits()` path runs saveSession -> computeProfile
// + updatePano, which is exactly what is exercised here; the DOM wiring that
// calls it on switch is pinned by test_ui_invariants.)
const A = H.newPano('A');
const B = H.newPano('B');
A.strokes = [{ m: 'b', x: 5, y: 5, r: 2 }];
H.computeProfile(A);
H.updatePano(A);                       // == flushHorizonEdits() -> activeSession.save()
let saved = JSON.parse(localStorage.getItem('nightseam_panos_v1'));
assert(saved.panos.find((x) => x.id === A.id).strokes.length === 1,
  'flush persists the ORIGINAL panorama edits');
assert(saved.panos.find((x) => x.id === B.id).strokes.length === 0,
  'flush must not write the edits into the newly selected panorama');
H.setActive(B.id);                     // switch selection
saved = JSON.parse(localStorage.getItem('nightseam_panos_v1'));
assert(saved.panos.find((x) => x.id === A.id).strokes.length === 1,
  'after switch, A still owns its edits');
assert(saved.panos.find((x) => x.id === B.id).strokes.length === 0,
  'after switch, B is still untouched');

// 1d: beforeunload is BEST-EFFORT/WARN-ONLY (never persists). With no dirty
// editor it must not block unload. The dirty path needs a wired DOM editor, so
// it is pinned by the source invariant instead (see test_ui_invariants).
const bu = globalThis.window._h.beforeunload;
assert(typeof bu === 'function', 'a beforeunload handler is registered');
let prevented = false;
bu({ preventDefault() { prevented = true; }, returnValue: '' });
assert(!prevented, 'a clean editor must not block unload');

/* ---------------- FIX-5: storage failure must be reported ---------------- */
// A quota/security failure used to be swallowed inside save(), so the editor
// showed "Saved" while localStorage kept the previous state (data loss on
// reload). updatePano() must report the failure to its caller.
const C = H.newPano('quota-test');
const origSetItem = globalThis.localStorage.setItem;
globalThis.localStorage.setItem = () => { throw new Error('quota exceeded'); };
const failed = H.updatePano(C);
globalThis.localStorage.setItem = origSetItem;
assert(failed === false, 'updatePano must report a storage failure (got ' + failed + ')');
const succeeded = H.updatePano(C);
assert(succeeded === true, 'updatePano must report success (got ' + succeeded + ')');
H.deletePano(C.id);

// FIX-5b: a failed snapshot APPLY must roll the live set back and report false
// (the restore path in app.js throws on false and runs its rollback).
const beforeIds = H.allPanos().map((p) => p.id).join(',');
const sample = H.allPanos()[0];
const injected = H.normalizePanos({ panos: [{ ...sample, id: 'px',
                                              name: 'injected' }] });
globalThis.localStorage.setItem = () => { throw new Error('quota exceeded'); };
const applied = H.applyPanosSnapshot(injected);
globalThis.localStorage.setItem = origSetItem;
assert(applied === false, 'a failed apply must report false (got ' + applied + ')');
assert(H.allPanos().map((p) => p.id).join(',') === beforeIds,
       'a failed apply must roll the live set back');

/* ---------------- WB5: untrusted import records ---------------- */
// non-token ids are dropped, duplicates collapse, strokes are capped
const wb1 = H.normalizePanos({ panos: [{ id: 'ok-1', strokes: [] },
                                      { id: '../evil', strokes: [] },
                                      { id: 'ok-1', strokes: [] }] });
assert(wb1 && wb1.panos.length === 1 && wb1.panos[0].id === 'ok-1',
       'WB5: non-token/duplicate pano ids must be dropped');
const wb2 = H.normalizePanos({ panos: [{ id: 'ok-2',
  strokes: new Array(30000).fill({ m: 'b', x: 1, y: 1, r: 1 }) }] });
assert(wb2 && wb2.panos[0].strokes.length === 20000,
       'WB5: imported strokes must be capped');

/* ---------------- crop: one history entry, undo/redo, dirty ---------------- */
// A crop is ONE gesture => one history entry; undo restores the WHOLE pre-crop
// state (fov, azOffset AND strokes), and Save clears the dirty flag.
const cp = H.newPano('crop-ui');
cp.strokes = [{ m: 'b', x: 360, y: 100, r: 20 }];
H.computeProfile(cp);
const preStrokes = JSON.stringify(cp.strokes);
const preFov = cp.fov, preAz = cp.azOffset;
const ch = H.newHistory(cp);
H.historyMarkSaved(ch, cp);                 // baseline = pre-crop
assert(!H.historyDirty(ch, cp), 'crop test starts clean');
H.cropPano(cp, { x: 180, w: 360 }, 720);    // keep az 90..270 -> fov 180, az 90
H.historyCommit(ch, cp);
assert(ch.entries.length === 2, `crop must add exactly ONE history entry (${ch.entries.length})`);
assert(cp.fov === 180 && near(cp.azOffset, 90), 'crop applied fov/az');
assert(H.historyDirty(ch, cp), 'a crop marks the editor dirty');
H.historyUndo(ch, cp);
assert(cp.fov === preFov && near(cp.azOffset, preAz), 'undo restores pre-crop calibration');
assert(JSON.stringify(cp.strokes) === preStrokes, 'undo restores the pre-crop strokes');
assert(!H.historyDirty(ch, cp), 'undo back to the saved state is clean');
H.historyRedo(ch, cp);
assert(cp.fov === 180 && cp.strokes.length > 0, 'redo reapplies the crop with its strokes');
H.historyMarkSaved(ch, cp);
assert(!H.historyDirty(ch, cp), 'Save clears the crop dirty flag');
H.deletePano(cp.id);

console.log(ok ? 'pano ui contract held' : 'pano ui FAILED');
process.exit(ok ? 0 : 1);
