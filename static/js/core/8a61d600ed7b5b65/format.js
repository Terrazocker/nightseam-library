/*
 * NightSeam core - display formatting and chart presentation helpers (pure).
 *
 * Single implementation (Phase 2, concept 1). No DOM, storage,
 * network or build tokens. Catalog sizes are ARCMINUTES and are rendered in one
 * user-chosen unit; `escape` is injected by shells that build HTML strings so
 * the core stays DOM-free.
 */

function one(v, sizeUnit) {
  if (sizeUnit === 'deg') return parseFloat((v / 60).toFixed(2)) + '\u00B0';
  if (sizeUnit === 'arcsec') return Math.round(v * 60) + '\u2033';
  return parseFloat(v.toFixed(1)) + '\u2032';
}

/** "110" or "645x550" (arcminutes) in the chosen unit; empty -> em dash.
 *  `escape` defaults to identity; HTML-producing shells pass their escaper so
 *  a non-numeric fallback string is still escaped exactly as before. */
export function fmtSize(s, sizeUnit = 'arcmin', escape = null) {
  if (!s) return '\u2014';
  const parts = String(s).split(/[x\u00D7]/).map(parseFloat).filter((v) => !isNaN(v));
  if (!parts.length) return escape ? escape(String(s)) : String(s);
  return parts.map((v) => one(v, sizeUnit)).join('\u00D7');
}

/** Largest dimension in arcminutes - a lone value is a diameter. null if none. */
export function sizeArcmin(s) {
  if (!s) return null;
  const parts = String(s).split(/[x\u00D7]/).map(parseFloat).filter((v) => !isNaN(v));
  if (!parts.length) return null;
  return Math.max(...parts);
}

/** Rig field of view in arcminutes: "76.6' (1.28 deg)". */
export function fmtFov(am) {
  return `${parseFloat(am.toFixed(1))}\u2032 (${parseFloat((am / 60).toFixed(2))}\u00B0)`;
}

export function fmtFovPair(fovW, fovH) {
  return `${fmtFov(fovW)} \u00D7 ${fmtFov(fovH)}`;
}

/** Frame coverage: the OBJECT's span as a percentage of the FRAME - the same
 *  definition as the desktop's frameCoverage (static/js/derive.js), whose
 *  tooltip reads "object spans X% of the frame". Values above 100 mean the
 *  object does not fit in one frame (desktop: "needs ~N frames"). `sizeAm` is
 *  the object's larger axis, `fovWArcmin` the rig's horizontal FOV - both in
 *  arcminutes. Returns null for missing/zero inputs. Pinned by the "frame"
 *  engine of tests/js/astro_parity.mjs. */
export function framePct(sizeAm, fovWArcmin) {
  if (sizeAm == null || sizeAm <= 0 || !(fovWArcmin > 0)) return null;
  return Math.round(100 * sizeAm / fovWArcmin);
}

/** All-sky chart magnitude limit - the ONE rule behind the catalog's
 *  "#skyMag" slider. An object with a magnitude
 *  shows when vmag <= limit; one WITHOUT a magnitude shows only at the
 *  slider's maximum. Sun/Moon, field stars and the min-alt ring are untouched. */
export function magVisible(vmag, limit, maxLimit) {
  return vmag != null ? vmag <= limit : limit >= maxLimit;
}

/** "Square" zoom - the geometry behind both sky charts' wheel zoom. The disc
 *  lives in a square W x H space; zoom is ONE uniform scalar on both axes about
 *  a focus point, so the visible window is a rectangle W/zoom x H/zoom.
 *  `fx`/`fy` null = centered. */
export const SKY_ZOOM_MIN = 1;
export const SKY_ZOOM_MAX = 8;

export function skyClampZoom(z) {
  return Math.max(SKY_ZOOM_MIN, Math.min(SKY_ZOOM_MAX, z));
}

export function skyViewMatrix(zoom, W, H, fx, fy) {
  const cx = fx == null ? W / 2 : fx;
  const cy = fy == null ? H / 2 : fy;
  return { a: zoom, d: zoom, e: W / 2 - cx * zoom, f: H / 2 - cy * zoom };
}

export function skyScreenToBase(zoom, W, H, fx, fy, sx, sy) {
  const cx = fx == null ? W / 2 : fx;
  const cy = fy == null ? H / 2 : fy;
  return [cx + (sx - W / 2) / zoom, cy + (sy - H / 2) / zoom];
}

export function skyClampFocus(W, H, fx, fy) {
  const Rlim = Math.min(W, H) / 2 - 22;
  const at = (v, c) => (v == null ? null : Math.max(c - Rlim, Math.min(c + Rlim, v)));
  return [at(fx, W / 2), at(fy, H / 2)];
}
