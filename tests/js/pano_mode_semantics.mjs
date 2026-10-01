/*
 * RULING C for the panorama profile/strokes invariant (findings DE-03 desktop and
 * WE-02 web, certified high in a prior review retained in Git history).
 *
 * The ruling: a stored profile/profileTop is MODE-SPECIFIC only for a DELIBERATE
 * paint-mode switch; that switch must be an explicit, undoable transform - never
 * applied on open, load or save. Independently of the mode question:
 *   (a) undo/Revert back to a stroke-less baseline must restore the AUTHORITATIVE
 *       stored profile, not leave a discarded derived ceiling live in the record
 *       planning reads, and the dirty flag must not claim "Saved" meanwhile;
 *   (b) preview and planning must agree in both modes.
 *
 * This engine drives the REAL modules of both shells (web/horizon.js and
 * static/js/pano.js) through their exported pure paths - no DOM, no stubs of the
 * logic under test. Prints one marker line; tests/test_js_behaviour.py asserts it.
 *
 * NOTE on profileTop: for a canonical sky blank the web/core convention is
 * pitch every bin at NO_BLOCK ("no ceiling") while the desktop fills 90. The two
 * are behaviourally identical because a sky column whose profile is 90 (band
 * bottom at the zenith) is fully blocked whatever its top says - which is why
 * the cross-shell assertion below compares the PROFILE (the values that decide
 * visibility) and the undo round-trip, not the raw top arrays.
 */
globalThis.window = { addEventListener() {}, removeEventListener() {}, dispatchEvent() {} };
globalThis.localStorage = {
  _m: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._m, k) ? this._m[k] : null; },
  setItem(k, v) { this._m[k] = String(v); },
};

const H = await import(new URL("../../web/horizon.js", import.meta.url).href);
const D = await import(new URL("../../static/js/pano.js", import.meta.url).href);

const BINS = 720;
let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };
const assert = (c, m) => { if (!c) fail(m); };
const eqArr = (a, b) => Array.isArray(a) && Array.isArray(b)
  && a.length === b.length && a.every((v, i) => v === b[i]);

/* An imported, stroke-less OBSTACLE record with a real (non-blank) ceiling, so a
 * wrong re-derivation is visible instead of coinciding with the sentinel. */
const imported = () => {
  const prof = new Array(BINS).fill(-90);
  for (let i = 0; i < BINS; i++) prof[i] = (i % 7 === 0) ? 35 : -90;
  return {
    azOffset: 0, altBottom: 0, altTop: 60, fov: 360, blockAbove: false,
    paintMode: "obstacle", paint_mode: "obstacle",
    strokes: [], profile: [...prof], profileTop: new Array(BINS).fill(-90),
    profile_top: [],
  };
};

/* ---- 1. open/load/undo must NOT blank a stroke-less stored profile ---- */
{
  const src = imported();
  const snap = H.snapshotPano(src);
  const dst = { ...imported(), profile: new Array(BINS).fill(0), profileTop: [] };
  H.applySnapshot(dst, snap);
  assert(eqArr(dst.profile, src.profile), "load/undo restored the AUTHORITATIVE stroke-less profile");
  // saveSession() calls core computeProfile(): it must keep, not blank, it
  const kept = imported();
  H.computeProfile(kept);
  assert(eqArr(kept.profile, src.profile), "computeProfile keeps a stroke-less stored profile");
}

/* ---- 2. a deliberate switch is ONE undoable entry that restores mode+profile ---- */
{
  const rec = imported();
  const h = H.newHistory(rec);
  const before = [...rec.profile];
  // what the #ed-mode handler does
  rec.paintMode = "sky";
  H.clearMask(rec);
  H.historyCommit(h, rec);
  assert(H.historyDirty(h, rec), "the mode switch is an edit (dirty)");
  assert(rec.profile.every((v) => v === 90), "sky switch re-derives the canonical sky blank");
  assert(H.historyCanUndo(h), "the switch is a history entry");
  H.historyUndo(h, rec);
  assert(rec.paintMode === "obstacle", "undo restores the previous MODE");
  assert(eqArr(rec.profile, before), "undo restores the authoritative PROFILE (WE-02a)");
  assert(!H.historyDirty(h, rec), "undo back to the baseline is clean again");
  H.historyRedo(h, rec);
  assert(rec.paintMode === "sky" && rec.profile.every((v) => v === 90), "redo reapplies the switch");
}

/* ---- 3. desktop: same switch and the same undo round-trip ---- */
const deskApi = typeof D.pushPanoUndo === "function" && typeof D.popPanoUndo === "function"
  && typeof D.setEditableMode === "function" && typeof D.blankProfileForMode === "function";
if (!deskApi) {
  fail("desktop has no undoable paint-mode switch (pushPanoUndo/popPanoUndo/setEditableMode/blankProfileForMode) - DE-03");
} else {
  const rec = imported();
  const before = [...rec.profile];
  const stack = [];
  D.pushPanoUndo(rec, stack);
  assert(D.setEditableMode(rec, "sky") === true, "desktop switch reports the change");
  assert(rec.paint_mode === "sky", "desktop switch sets the mode");
  assert(rec.profile.every((v) => v === 90), "desktop switch re-derives the canonical sky blank");
  assert(D.setEditableMode(rec, "sky") === false, "a no-op switch does nothing");
  assert(D.popPanoUndo(rec, stack) === true, "desktop undo pops the snapshot");
  assert(rec.paint_mode === "obstacle", "desktop undo restores the MODE");
  assert(eqArr(rec.profile, before), "desktop undo restores the authoritative PROFILE (DE-03)");
  // the reverse direction too: sky blank -> obstacle
  rec.paint_mode = "sky"; D.blankProfileForMode(rec); D.setEditableMode(rec, "obstacle");
  assert(rec.paint_mode === "obstacle" && rec.profile.every((v) => v === -90),
    "obstacle switch re-derives the canonical obstacle blank");
}

/* ---- 4. CROSS-SHELL agreement on the deliberate switch ---- */
if (deskApi) {
  for (const mode of ["sky", "obstacle"]) {
    const web = imported();
    web.paintMode = mode; H.clearMask(web);
    const desk = imported();
    // start in the OTHER mode so the switch is real in both directions
    desk.paint_mode = (mode === "sky") ? "obstacle" : "sky";
    assert(D.setEditableMode(desk, mode) === true, `desktop switched to ${mode}`);
    assert(eqArr(web.profile, desk.profile),
      `both shells re-derive the same ${mode} profile (web vs desktop)`);
  }
}

/* ---- 5. the switch is the ONLY place a mode change touches the profile ---- */
if (deskApi) {
  // A stroke-y record: the profile is DERIVED from the mask, so switching mode
  // must not blank it here - the editor recomputes it from the strokes instead.
  const rec = imported();
  rec.strokes = [{ m: "b", x: 10, y: 10, r: 3 }];
  const before = [...rec.profile];
  D.setEditableMode(rec, "sky");
  assert(eqArr(rec.profile, before),
    "with strokes the switch leaves the profile for the mask re-derivation");
}

console.log(ok ? "pano mode semantics contract held" : "pano mode semantics contract FAILED");
process.exit(ok ? 0 : 1);
