/* Pure planner-state revision helpers shared by the desktop and web shells. */

export const PLANNER_CHANGE_EVENT = "planner:changed";

/** Advance a shell-local planner revision without ever returning NaN or 0. */
export function nextPlannerRevision(value) {
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= 0 && n < Number.MAX_SAFE_INTEGER
    ? n + 1 : 1;
}

/** Build the event detail carried by both shells. */
export function plannerChange(kind, revision, fields = {}) {
  return {
    kind: String(kind || "unknown"),
    revision: Number.isSafeInteger(revision) ? revision : 1,
    ...fields,
  };
}

/**
 * Content fingerprint for a panorama's planning-relevant state.
 *
 * The id is deliberately not enough: an edit or a restored record may retain
 * the same id. This is a compact FNV-1a fingerprint rather than JSON.stringify
 * so cache-key creation stays bounded and deterministic across shells.
 */
export function panoramaRevision(pano) {
  if (!pano) return "-";
  let h = 2166136261;
  const add = (value) => {
    const text = String(value == null ? "" : value);
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    h ^= 124; // field separator
    h = Math.imul(h, 16777619);
  };
  const addArray = (values) => {
    if (!Array.isArray(values)) { add(""); return; }
    add(values.length);
    for (const value of values) add(value);
  };
  const addStrokes = (values) => {
    if (!Array.isArray(values)) { add(""); return; }
    add(values.length);
    for (const stroke of values) {
      add(stroke && stroke.m);
      add(stroke && stroke.x);
      add(stroke && stroke.y);
      add(stroke && stroke.r);
    }
  };
  add(pano.id);
  add(pano.paintMode ?? pano.paint_mode);
  add(!!(pano.blockAbove ?? pano.block_above));
  add(pano.azOffset ?? pano.az_offset);
  add(pano.altBottom ?? pano.alt_bottom);
  add(pano.altTop ?? pano.alt_top);
  add(pano.fov);
  addArray(pano.profile);
  addArray(pano.profileTop ?? pano.profile_top);
  addStrokes(pano.strokes);
  return (h >>> 0).toString(16);
}
