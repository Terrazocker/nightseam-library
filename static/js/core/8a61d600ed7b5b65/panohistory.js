/*
 * NightSeam core - panorama editor history (pure).
 *
 * Shell-unification pass: the panorama editor's undo/redo/dirty model existed
 * TWICE. The web (`web/horizon.js`) had the richer one - a bounded step list
 * with a saved baseline, redo, and `historyDirty` - while the desktop
 * (`static/js/pano.js`) had a bound-60 stack with no redo and no dirty state,
 * so the two could answer "is this edit saved?" differently on the same data.
 * The state machine is pure (JSON snapshots of the editable fields, no DOM,
 * storage or network), so it lives here and BOTH shells use it.
 *
 * Two contracts are load-bearing and preserved from the web implementation:
 *
 *  1. A snapshot carries the stored PROFILE only when there are NO strokes.
 *     Strokes WIN when present and the mask reproduces them exactly, so a
 *     stroke-y snapshot stays profile-free (that is what keeps a 60-step stack
 *     small); with no strokes the stored profile is AUTHORITATIVE and cannot be
 *     re-derived from an empty mask (ruling C, DE-03/WE-02) - otherwise undoing
 *     back to a stroke-less baseline would leave a discarded derived ceiling
 *     live in the record planning reads.
 *  2. The snapshot encodes the EDITABLE state only: the calibration bounds,
 *     paint mode, blockAbove, strokes and (conditionally) the profile. Zoom is
 *     presentation-only and never participates.
 *
 * The shell owns the field NAMES (`azOffset`/`altBottom`/`paintMode` on the
 * web, `az_offset`/`paint_mode` on the desktop) through `keys` - the history
 * reads and writes the shell's own objects, so neither side needs an adapter
 * copy and the wire format stays stable.
 */

/** Canonical property names. A shell whose records use other names passes its
 *  own `keys` map to `newHistory`/`snapshot`. */
export const HISTORY_KEYS = {
  azOffset: 'azOffset', altBottom: 'altBottom', altTop: 'altTop', fov: 'fov',
  paintMode: 'paintMode', strokes: 'strokes', blockAbove: 'blockAbove',
  profile: 'profile', profileTop: 'profileTop', landmarks: 'landmarks',
  provenance: 'provenance', confidence: 'confidence',
};

/** The desktop's record shape, ready to pass as `keys`. */
export const HISTORY_KEYS_DESKTOP = {
  azOffset: 'az_offset', altBottom: 'alt_bottom', altTop: 'alt_top', fov: 'fov',
  paintMode: 'paint_mode', strokes: 'strokes', blockAbove: 'block_above',
  profile: 'profile', profileTop: 'profile_top', landmarks: 'landmarks',
  provenance: 'provenance', confidence: 'confidence',
};

/** How many history entries one panorama keeps. A drag is ONE entry (committed
 *  on pointer up), so this is generously more than a session needs. */
export const HISTORY_LIMIT = 60;

const arr = (v) => (Array.isArray(v) ? v : []);

/** The editable geometry, DEEP-COPIED. A snapshot must not alias the record's
 *  own arrays: `p.strokes = []` (Clear) or a pushed stroke would otherwise
 *  rewrite an entry that is already in the stack, and `historyCommit`'s
 *  "unchanged?" comparison would compare the record with itself. The web's
 *  editor happened to pass fresh arrays, so the aliasing was invisible there -
 *  the desktop mutates `rec.strokes` in place (`dab()` pushes into it), which is
 *  how this surfaced. */
const clone = (v) => arr(v).map((s) => (s && typeof s === 'object' ? { ...s } : s));

const cloneValue = (v) => {
  if (Array.isArray(v)) return v.map(cloneValue);
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cloneValue(x)]));
  }
  return v;
};

/** Serialize a record's editable state. `keys` maps the canonical names to the
 *  record's own properties; the short JSON keys are the wire format and must
 *  stay stable (a stored history is never persisted, but tests compare them). */
export function snapshot(state, keys = HISTORY_KEYS) {
  const s = clone(state[keys.strokes]);
  const out = {
    a: state[keys.azOffset], b: state[keys.altBottom], t: state[keys.altTop],
    f: state[keys.fov], m: state[keys.paintMode],
    s, o: state[keys.blockAbove] ? 1 : 0,
  };
  if (!s.length) {
    out.pr = arr(state[keys.profile]).slice();
    out.pt = arr(state[keys.profileTop]).slice();
  }
  if (keys.landmarks && state[keys.landmarks] !== undefined) {
    out.lm = cloneValue(state[keys.landmarks]);
  }
  if (keys.provenance && state[keys.provenance] !== undefined) {
    out.pv = cloneValue(state[keys.provenance]);
  }
  if (keys.confidence && state[keys.confidence] !== undefined) {
    out.cf = cloneValue(state[keys.confidence]);
  }
  return JSON.stringify(out);
}

/** Write a snapshot back onto a record (in place). */
export function applySnapshot(state, snap, keys = HISTORY_KEYS) {
  const o = JSON.parse(snap);
  state[keys.azOffset] = o.a; state[keys.altBottom] = o.b;
  state[keys.altTop] = o.t; state[keys.fov] = o.f;
  state[keys.paintMode] = o.m; state[keys.strokes] = o.s;
  state[keys.blockAbove] = !!o.o;
  if (o.pr !== undefined) {
    state[keys.profile] = o.pr;
    state[keys.profileTop] = o.pt;
  }
  if (o.lm !== undefined && keys.landmarks) state[keys.landmarks] = o.lm;
  if (o.pv !== undefined && keys.provenance) state[keys.provenance] = o.pv;
  if (o.cf !== undefined && keys.confidence) state[keys.confidence] = o.cf;
  return state;
}

/** Fresh history for a record: one entry (the current state) plus the saved
 *  baseline, so a brand-new record is clean. */
export function newHistory(state, keys = HISTORY_KEYS, limit = HISTORY_LIMIT) {
  const snap = snapshot(state, keys);
  return { entries: [snap], index: 0, baseline: snap, keys, limit };
}

/** Record the current state as a new entry. No-op (returns false) when nothing
 *  changed - so a pointer-up that painted nothing does not consume a step.
 *  Committing after an undo DROPS the redo entries (a new edit invalidates
 *  them), and the oldest entry falls off past `limit`. */
export function historyCommit(h, state, limit) {
  const cap = limit || h.limit || HISTORY_LIMIT;
  const snap = snapshot(state, h.keys);
  if (snap === h.entries[h.index]) return false;
  h.entries = h.entries.slice(0, h.index + 1);
  h.entries.push(snap);
  if (h.entries.length > cap) h.entries.shift();
  h.index = h.entries.length - 1;
  return true;
}

export function historyCanUndo(h) { return h.index > 0; }
export function historyCanRedo(h) { return h.index < h.entries.length - 1; }

export function historyUndo(h, state) {
  if (!historyCanUndo(h)) return false;
  h.index -= 1;
  applySnapshot(state, h.entries[h.index], h.keys);
  return true;
}

export function historyRedo(h, state) {
  if (!historyCanRedo(h)) return false;
  h.index += 1;
  applySnapshot(state, h.entries[h.index], h.keys);
  return true;
}

/** Mark the current state as saved: the dirty baseline moves to it. */
export function historyMarkSaved(h, state) {
  h.baseline = snapshot(state, h.keys);
}

/** True while the current state differs from the last saved state, so undoing
 *  all the way back to the baseline clears it again. */
export function historyDirty(h, state) {
  return snapshot(state, h.keys) !== h.baseline;
}
