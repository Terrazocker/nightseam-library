/*
 * NightSeam core - panorama / horizon obstruction geometry (pure).
 *
 * This module is the SINGLE implementation of the panorama mask/profile math
 * and the sky-band geometry. It must stay pure: no DOM, no storage, no
 * network, no build tokens. Shells (web/ and static/js/) import it through the
 * baked stable stub `core.js`; scripts/build_core.py copies this tree to
 * content-hashed URLs and scripts/check_core.py fails on drift.
 *
 * Profile representation: exactly BINS=720 bins (0.5 deg per azimuth),
 * NO_BLOCK = -90. Sky mode: profile = band bottom, profileTop = band top
 * (90.5 = "no upper ceiling"). Unpainted sky columns block fully (90).
 */

export const NO_BLOCK = -90;
export const BINS = 720;
export const BIN_DEG = 0.5;
export const MASK_W = 720;
export const MASK_H = 360; // 0.25 deg per row of altitude

/** Metadata vocabulary for the horizon editor.  These values are descriptive
 * only: they never alter the planning profile.  Keeping the vocabulary in the
 * shared core prevents the web and desktop editors from silently inventing
 * different meanings for the same imported record. */
export const HORIZON_LANDMARK_KINDS = Object.freeze([
  'tree', 'building', 'ridge', 'pole', 'roof', 'balcony', 'other',
]);

const HORIZON_TEXT_LIMITS = Object.freeze({
  name: 96, notes: 320, source: 40, capturedAt: 40,
  orientation: 160, operator: 96,
});

const horizonText = (value, limit) => String(value ?? '').trim().slice(0, limit);

/** Normalize user-entered obstruction landmarks into a safe, portable shape.
 *  Landmarks are annotations, not geometry; malformed entries are ignored and
 *  coordinates are clamped/wrapped at the storage boundary. */
export function normalizeHorizonLandmarks(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const out = [];
  for (let i = 0; i < value.length && out.length < 128; i++) {
    const item = value[i];
    if (!item || typeof item !== 'object') continue;
    const name = horizonText(item.name, HORIZON_TEXT_LIMITS.name);
    const azRaw = Number(item.azimuth ?? item.az);
    const altRaw = Number(item.altitude ?? item.alt);
    if (!name || !Number.isFinite(azRaw) || !Number.isFinite(altRaw)) continue;
    const idRaw = horizonText(item.id, 64);
    let id = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(idRaw)
      ? idRaw : `landmark-${i + 1}`;
    while (seen.has(id)) id = `${id}-${i + 1}`.slice(0, 64);
    seen.add(id);
    const kind = HORIZON_LANDMARK_KINDS.includes(item.kind)
      ? item.kind : 'other';
    const azimuth = ((azRaw % 360) + 360) % 360;
    const altitude = Math.max(-90, Math.min(90, altRaw));
    out.push({
      id, name, kind,
      azimuth: +azimuth.toFixed(2),
      altitude: +altitude.toFixed(2),
      notes: horizonText(item.notes, HORIZON_TEXT_LIMITS.notes),
    });
  }
  return out;
}

/** Normalize calibration provenance.  An empty object is intentional: old
 * panorama records did not carry provenance and must remain valid. */
export function normalizeHorizonProvenance(value) {
  const item = value && typeof value === 'object' && !Array.isArray(value)
    ? value : {};
  const allowed = ['manual', 'compass', 'phone_metadata', 'plate_solve',
    'imported', 'unknown'];
  return {
    source: allowed.includes(item.source) ? item.source : 'unknown',
    capturedAt: horizonText(item.capturedAt ?? item.captured_at,
      HORIZON_TEXT_LIMITS.capturedAt),
    orientation: horizonText(item.orientation, HORIZON_TEXT_LIMITS.orientation),
    operator: horizonText(item.operator, HORIZON_TEXT_LIMITS.operator),
    notes: horizonText(item.notes, HORIZON_TEXT_LIMITS.notes),
  };
}

/** Confidence is per canonical world-azimuth bin: 0 = inferred/uncertain,
 *  1 = high confidence, null = not assessed.  Always return 720 entries so a
 *  consumer can index it without a second shape convention. */
export function normalizeHorizonConfidence(value) {
  const src = Array.isArray(value) ? value : [];
  return Array.from({ length: BINS }, (_, i) => {
    if (src[i] === null || src[i] === undefined || src[i] === '') return null;
    const n = Number(src[i]);
    return Number.isFinite(n) ? +Math.max(0, Math.min(1, n)).toFixed(3) : null;
  });
}

/** Compare two canonical obstruction profiles for the editor's explanation.
 *  Every finite bin participates, including the explicit NO_BLOCK/open-sky
 *  sentinels; a partial-FOV difference is therefore visible rather than being
 *  mistaken for agreement. */
export function profileDifference(a, b) {
  let compared = 0, sum = 0, maxAbs = 0, azAtMax = null;
  for (let i = 0; i < BINS; i++) {
    const av = Number(Array.isArray(a) ? a[i] : NaN);
    const bv = Number(Array.isArray(b) ? b[i] : NaN);
    if (!Number.isFinite(av) || !Number.isFinite(bv)) continue;
    const diff = Math.abs(av - bv);
    compared++;
    sum += diff;
    if (azAtMax === null || diff > maxAbs) {
      maxAbs = diff; azAtMax = i * BIN_DEG;
    }
  }
  return {
    compared,
    meanAbs: compared ? sum / compared : 0,
    maxAbs,
    azAtMax,
  };
}

/** Canonical bin space marker. D1: core profiles are WORLD-AZIMUTH bins
 *  (bin i = azimuth i * 0.5 deg), matching Python `ceil_lookup`, the server
 *  payload path and the desktop editor. The web editor's mask is FOV-relative,
 *  so legacy/imported FOV-relative records are converted once by
 *  `normalizeProfileBins()` at the storage boundary. */
export const WORLD_BINS = 'world';

/** World azimuth (deg, any sign) -> canonical bin index. */
const worldBin = (azDeg) => {
  const a = ((azDeg % 360) + 360) % 360;
  return Math.min(BINS - 1, Math.floor(a / BIN_DEG));
};

/** FOV-relative position (deg in [0, fov]) of world bin `i` inside the
 *  calibrated frame [0, fov], or null when the bin lies entirely outside it.
 *
 *  ONE coverage rule, shared by the profile producers, the preview and the
 *  crop, and it MUST agree with the scorer, which gates per azimuth
 *  continuously (`ceilFor`: inside while `0 <= az - azOffset <= fov`). A bin is
 *  therefore in frame when ANY part of its 0.5 deg span overlaps the frame:
 *  - a bin that starts inside keeps its left-edge position (unchanged mapping);
 *  - the bin that STRADDLES the frame start (its wrapped left edge is in
 *    [359.5, 360), so rel > fov yet the bin covers azimuths azOffset..) is
 *    represented by the midpoint of its wrapped overlap.
 *  The old left-edge-only rule left that straddler at the mode sentinel AND
 *  wrapped its representative column to 719, so for any azOffset that is not a
 *  multiple of 0.5 (normal after a crop) an in-frame azimuth read the
 *  out-of-frame sentinel: a painted band read clear in obstacle mode and fully
 *  blocked in sky mode. That is CR3/CO-01 from the 0.6.24 review. */
export function fovBinRel(i, azOff, fov) {
  const rel = (((i * BIN_DEG - azOff) % 360) + 360) % 360;
  if (rel <= fov) return rel;                 // starts inside the frame
  if (rel + BIN_DEG > 360) {                  // straddles the frame start
    return (rel + BIN_DEG - 360) / 2;
  }
  return null;                                // entirely outside the frame
}

/** Rebuild the binary mask (Uint8Array MASK_W*MASK_H) from the stroke list.
 *  `wrap` connects the azimuth seam of a full 360 deg panorama by also painting
 *  each dab shifted one mask width left/right (the out-of-range parts clip to
 *  the normal bounds) - desktop parity, otherwise a stroke straddling the left
 *  edge leaves a gap at the right edge. */
export function rebuildMask(strokes, wrap = false) {
  const mask = new Uint8Array(MASK_W * MASK_H);
  for (const s of strokes) {
    const val = s.m === 'b' ? 255 : 0;
    const xs = wrap ? [s.x - MASK_W, s.x, s.x + MASK_W] : [s.x];
    for (const sx of xs) {
      const x0 = Math.max(0, Math.floor(sx - s.r));
      const x1 = Math.min(MASK_W - 1, Math.ceil(sx + s.r));
      for (let cx = x0; cx <= x1; cx++) {
        const dx = cx - sx;
        const dy = Math.sqrt(Math.max(0, s.r * s.r - dx * dx));
        const y0 = Math.max(0, Math.floor(s.y - dy));
        const y1 = Math.min(MASK_H - 1, Math.ceil(s.y + dy));
        for (let cy = y0; cy <= y1; cy++) mask[cy * MASK_W + cx] = val;
      }
    }
  }
  return mask;
}

/** Mask row -> apparent altitude (image y=0 = altTop, y=MASK_H-1 = altBottom). */
export const altOf = (p, row) => p.altTop - (row / (MASK_H - 1)) * (p.altTop - p.altBottom);

/** Altitude -> mask row: the exact inverse of altOf (may fall outside the
 *  image; callers clip). Guides use THIS so a line is never visually centered
 *  but mathematically offset. */
export const rowOfAlt = (p, alt) => {
  const span = p.altTop - p.altBottom;
  return Math.abs(span) < 1e-9 ? 0 : ((p.altTop - alt) / span) * (MASK_H - 1);
};

/** Azimuth -> mask x, the SAME FOV-relative mapping ceilFor() uses
 *  (d = az - azOffset, wrapped; x = d/fov * MASK_W). Outside the calibrated
 *  FOV the result falls outside 0..MASK_W. */
export function xOfAz(p, azDeg) {
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  const d = ((azDeg - azOff) % 360 + 360) % 360;
  return (d / fov) * MASK_W;
}

/** Mask x -> azimuth, the inverse of xOfAz (wrapped to 0..360). */
export function azOfX(p, x) {
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  return (((azOff + (x / MASK_W) * fov) % 360) + 360) % 360;
}

/** Seam wrap applies only to a full 360 deg panorama. */
export const wrapsFor = (p) => Number(p.fov) >= 359.5;

/** Obstruction profile from a mask - shared by computeProfile() and the live
 *  overlay (which must not rebuild the mask or hit storage per pointer sample).
 *
 *  D1: the mask is FOV-relative (column 0 = azOffset); the produced profile is
 *  WORLD-AZIMUTH, exactly like the desktop editor and the Python server. Each
 *  world bin i takes the mask column of its FOV-relative position through the
 *  ONE rule `fovBinRel()` (see above), which also covers the bin straddling the
 *  frame start, so the 720-mask-column vs 0.5-deg-bin oversampling cannot
 *  disagree with the desktop representative. Bins outside
 *  the calibrated FOV keep the mode sentinel (sky = 90 blocked, obstacle =
 *  NO_BLOCK). Marks the record's bin space so the adapter never converts it
 *  twice. */
export function profileFromMask(p, mask) {
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  const sky = p.paintMode === 'sky';
  const prof = new Array(BINS).fill(sky ? 90 : NO_BLOCK);
  const top = new Array(BINS).fill(NO_BLOCK);
  for (let i = 0; i < BINS; i++) {
    const rel = fovBinRel(i, azOff, fov);
    if (rel === null) continue;              // outside the calibrated FOV
    const col = Math.min(BINS - 1, Math.floor(rel * BINS / fov));
    let minRow = -1, maxRow = -1;
    for (let row = 0; row < MASK_H; row++) {
      if (mask[row * MASK_W + col]) { if (minRow < 0) minRow = row; maxRow = row; }
    }
    if (minRow < 0) continue;                // unpainted: sky column stays blocked
    if (sky) {
      prof[i] = altOf(p, maxRow);            // sky mode: bottom edge of painted band
      // Painted to (near) the frame top = open sky above: 90.5 is the "no
      // upper ceiling" sentinel (desktop parity) - without it, everything
      // above a band that reaches altTop was wrongly treated as blocked.
      const eTop = altOf(p, minRow);
      top[i] = eTop >= p.altTop - 4 ? 90.5 : eTop;
    } else {
      prof[i] = altOf(p, minRow);            // top of the painted mask
    }
  }
  p.profileBins = WORLD_BINS;
  return { prof, top };
}

/** Convert a legacy FOV-relative profile (one bin per mask column) to the
 *  canonical world-azimuth bins, in place. Idempotent: records already marked
 *  `profileBins === WORLD_BINS` pass through untouched; a stroke-derived
 *  profile is already world (profileFromMask marks it). Uses the desktop
 *  representative column (`col = floor(rel/fov*720)`), so a converted record
 *  is byte-equivalent to what the desktop editor recomputes from the same
 *  mask. Bins outside the calibrated FOV get the mode sentinel. */
export function normalizeProfileBins(p) {
  if (!p || p.profileBins === WORLD_BINS) return p;
  const prof = (Array.isArray(p.profile) && p.profile.length === BINS) ? p.profile : null;
  const tops = (Array.isArray(p.profileTop) && p.profileTop.length === BINS) ? p.profileTop : null;
  const sky = p.paintMode === 'sky';
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  const wprof = new Array(BINS).fill(sky ? 90 : NO_BLOCK);
  const wtop = new Array(BINS).fill(NO_BLOCK);
  if (prof) {
    for (let i = 0; i < BINS; i++) {
      const rel = fovBinRel(i, azOff, fov);
      if (rel === null) continue;
      const col = Math.min(BINS - 1, Math.floor(rel * BINS / fov));
      wprof[i] = prof[col];
      if (tops) wtop[i] = tops[col];
    }
  }
  p.profile = wprof;
  p.profileTop = wtop;
  p.profileBins = WORLD_BINS;
  return p;
}

/** Contiguous edge segments of one profile, for the preview - SHARED with the
 *  desktop (`static/js/pano.js` re-exports this) and pinned by
 *  `tests/js/sky_band.mjs`. A bin is skipped (pen lifted) outside the FOV,
 *  below the chart bottom, or at/above `skipAbove` (the sky-mode 90.5
 *  "no ceiling" sentinel); a wrap at the 360 deg seam also lifts the pen
 *  instead of drawing across the image, so the fill can never self-intersect
 *  or smear. */
export function profileSegments(prof, W, H, ab, at, azOff, fov, skipAbove = 90.5) {
  const segs = [];
  // CR2: this is the desktop editor's silhouette call and the WEB editor passes
  // `p.profileTop` (null whenever a sky-mode record has no top array), so a
  // missing/unusable array threw "Cannot read properties of null (reading '0')"
  // mid-render. Same degrade as the siblings: no array -> no segments -> nothing
  // drawn - which is exactly what the caller renders for an empty result.
  if (!Array.isArray(prof)) return segs;
  let seg = null, prevX = null;
  const span = at - ab || 1;
  for (let i = 0; i < BINS; i++) {
    const e = Number(prof[i]);
    const rel = fovBinRel(i, azOff, fov);
    if (!Number.isFinite(e) || rel === null || e <= ab - 5 || e >= skipAbove) {
      seg = null; prevX = null; continue;
    }
    const x = rel / fov * W;
    if (seg && prevX != null && x < prevX - W * 0.5) seg = null;
    const y = H * (1 - (Math.min(Math.max(e, ab), at) - ab) / span);
    if (!seg) { seg = []; segs.push(seg); }
    seg.push([x, y]);
    prevX = x;
  }
  return segs;
}

/** The visible-sky band per azimuth column, in ALTITUDE, plus its complement.
 *  Obstacle mode: visible = (ceiling, zenith]; blocked = [altBottom, ceiling].
 *  Sky mode: visible = the painted band [profile, profileTop] (an open-above
 *  90.5 sentinel extends to the zenith); unpainted columns are fully blocked.
 *  Per column, visible U blocked == [altBottom, zenith] and visible n blocked
 *  == {} - the visible mask is the EXACT complement of the blocked mask. */
export function visibleSkyBands(p, altBottom = null, zenith = 90) {
  // CO-04: degrade like the sibling entry points instead of throwing. `p == null`
  // failed before any FOV/paint-mode logic ("Cannot read properties of null"),
  // and a sky-mode record with a MISSING profileTop threw on `p.profileTop[col]`,
  // while ceilFor/overhangRuns/assumedOpenHatchPolys all return an empty degrade.
  // "No usable record" means "nothing visible": the caller draws nothing.
  if (!p || !Array.isArray(p.profile)) return [];
  const top = Array.isArray(p.profileTop) ? p.profileTop : null;
  const ab = Number.isFinite(altBottom) ? altBottom : Number(p.altBottom);
  const mk = (vis, blocked) => ({
    vis: vis && vis[1] > vis[0] ? vis : null,
    blocked: blocked.filter(([a, b]) => b > a),
  });
  const out = [];
  for (let col = 0; col < BINS; col++) {
    const e = p.profile[col];
    if (p.paintMode === 'sky') {
      if (e != null && e > NO_BLOCK + 0.5 && e < zenith) {
        const t = top ? top[col] : null;
        // the band top caps the visible sky ONLY with blockAbove; otherwise
        // everything above the painted band is free (open above, default)
        const hi = (p.blockAbove && t != null && t > NO_BLOCK + 0.5 && t !== 90.5)
          ? Math.min(t, zenith) : zenith;
        out.push(mk([e, hi], [[ab, e], [hi, zenith]]));
      } else {
        out.push(mk(null, [[ab, zenith]]));  // unpainted sky column = blocked
      }
    } else {
      const ceil = (e != null && e > NO_BLOCK + 0.5) ? e : ab;
      const lo = Math.max(ab, ceil);
      out.push(mk([lo, zenith], [[ab, lo]]));
    }
  }
  return out;
}

/** Closed polygon(s) of the VISIBLE band for the preview (sky mode only),
 *  split at the FOV edge, blocked columns and the 360 deg seam so no polygon
 *  spans across the chart. Returns [x,y] vertex lists (bottom edge left->right,
 *  then top edge right->left). Obstacle mode has no fill -> []. */
export function skyBandPolygons(p, W, H, ab, at, azOff, fov) {
  const bands = visibleSkyBands(p, ab);
  // CR2: `visibleSkyBands` degrades to [] for a missing/unusable record, and the
  // sibling entry points (ceilFor/overhangRuns/assumedOpenRuns) all return their
  // empty degrade - this one indexed the result blindly and threw
  // "Cannot read properties of undefined (reading 'vis')" instead. "No usable
  // record" means "draw nothing", exactly like its siblings, so a caller that
  // forgot to check its record cannot break a render pass with an exception.
  if (!bands.length) return [];
  const span = at - ab || 1;
  const yOf = (alt) => H * (1 - (Math.min(Math.max(alt, ab), at) - ab) / span);
  const polys = [];
  let run = null, prevX = null;
  for (let col = 0; col < BINS; col++) {
    const rel = fovBinRel(col, azOff, fov);
    const vis = bands[col].vis;
    if (!vis || rel === null) { run = null; prevX = null; continue; }
    const x = rel / fov * W;
    if (run && prevX != null && x < prevX - W * 0.5) run = null;   // 360 seam
    if (!run) { run = []; polys.push(run); }
    run.push([x, yOf(vis[1]), yOf(vis[0])]);
    prevX = x;
  }
  return polys.map((r) => {
    const pts = [];
    for (const [x, , by] of r) pts.push([x, by]);
    for (let k = r.length - 1; k >= 0; k--) pts.push([r[k][0], r[k][1]]);
    return pts;
  });
}

/** Recompute profile/profileTop (720 bins) from the current strokes.
 *
 *  Canonical invariant: strokes WIN when present; when there are NO strokes the
 *  stored profile is authoritative. Deriving an empty profile from an empty
 *  mask would silently remove a horizon we cannot re-derive, which could make a
 *  blocked target look observable. Deliberate clearing goes through clearMask().
 *
 *  Pure: it only rewrites `p.profile`/`p.profileTop` in memory. Persisting is
 *  the caller's job (the editor persists on Save; newPano/importPanos persist
 *  their structural changes themselves), so live editing never hits storage
 *  per pointer sample. */
export function computeProfile(p) {
  if (!Array.isArray(p.strokes) || p.strokes.length === 0) {
    if (!Array.isArray(p.profile) || p.profile.length !== BINS) {
      p.profile = new Array(BINS).fill(NO_BLOCK);
    }
    if (!Array.isArray(p.profileTop) || p.profileTop.length !== BINS) {
      p.profileTop = new Array(BINS).fill(NO_BLOCK);
    }
    return p.profile;
  }
  const mask = rebuildMask(p.strokes, wrapsFor(p));
  const { prof, top } = profileFromMask(p, mask);
  p.profile = prof;
  p.profileTop = top;
  return prof;
}

/** Explicitly clear a panorama's mask: no strokes AND a canonical blank profile
 *  for the paint mode. This is the ONE place a stored profile is discarded,
 *  because the user asked for it. Pure (in-memory only). */
export function clearMask(p) {
  p.strokes = [];
  p.profile = new Array(BINS).fill(p.paintMode === 'sky' ? 90 : NO_BLOCK);
  p.profileTop = new Array(BINS).fill(NO_BLOCK);
  p.profileBins = WORLD_BINS;
  return p.profile;
}

/* ---------------- kept-band crop (shared by both shells) ----------------
 *
 * A horizontal crop keeps ONE contiguous azimuth band and shrinks the
 * calibrated FOV to it. `sel = { x, w }` is the KEPT band in the editor's
 * canvas pixels and `W` is that canvas width (the web passes its 720-wide
 * logical mask, the desktop its displayed canvas width - only the fraction
 * matters). fovNew = max(45, round(fovOld * w/W)); azNew is the azimuth of the
 * kept band's left edge, at the same 2 dp the desktop transport used.
 *
 * History note (0.6.2x): the retired desktop formula `fovOld * (1 - w/W)`
 * computed the REMOVED fraction while the image, dim overlay, azimuth and HUD
 * all used the kept band - they agreed only at exactly half width (and a
 * select-all crop stored 45 deg). The kept-band formula is the single shared
 * truth now; `CROP_MIN_FOV` is the ONE floor. */

export const CROP_MIN_FOV = 45;

export function cropKeptBand(fovOld, azOffOld, sel, W) {
  const fov0 = Number(fovOld) > 0 ? Number(fovOld) : 360;
  const az0 = Number(azOffOld) || 0;
  const w = Number(sel && sel.w) || 0;
  const frac = (W > 0) ? Math.min(1, Math.max(0, w / W)) : 1;
  const fov = Math.max(CROP_MIN_FOV, Math.round(fov0 * frac));
  let az = ((az0 + ((Number(sel && sel.x) || 0) / W) * fov0) % 360 + 360) % 360;
  az = (Math.round(az * 100) / 100) % 360;
  return { fov, azOffset: az };
}

/** Transform + clip FOV-relative strokes into a kept-band crop. Horizontal
 *  only: y is unchanged. A dab whose circle overlaps the kept band is kept even
 *  when its centre lands just outside; rebuildMask clips it.
 *
 *  CO-08 (0.6.32 review): the radius MUST scale with the horizontal stretch. A
 *  crop narrows the FOV to the kept band, which is then stretched back over the
 *  whole 720-bin mask, so an x is scaled by 1/frac - and the same angular brush
 *  radius now covers 1/frac times as many bins. Leaving `r` alone made every dab
 *  angularly smaller than the user painted, so gaps opened between dabs that had
 *  overlapped: the probe lost 11 of 91 in-frame bins and a column that read
 *  "Blocked" (ceil 37.10) came back as "Observable" (ceil -90) - a blocked
 *  target planned as visible, which is the one thing this editor exists to
 *  prevent. */
export function cropStrokes(strokes, sel, W) {
  const w = Number(sel && sel.w) || 0;
  const frac = (W > 0) ? w / W : 1;
  if (!(frac > 0)) return [];
  const x0 = ((Number(sel && sel.x) || 0) / W) * MASK_W;
  const scale = 1 / frac;
  const out = [];
  for (const s of strokes || []) {
    const nx = (s.x - x0) * scale;
    const r = (Number(s.r) || 0) * scale;      // scale WITH the stretch (CO-08)
    if (nx < -r || nx > MASK_W + r) continue;
    out.push({ ...s, x: nx, r });
  }
  return out;
}

/** Non-destructive crop for a stroke-less record: keep the in-FOV world bins
 *  (world azimuths do not move under a crop) and reset the bins the new frame
 *  no longer covers to the mode sentinel, exactly as a fresh panorama of the
 *  new frame holds there. Mutates p in place. */
export function clipProfileToFov(p) {
  const sky = p.paintMode === 'sky';
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  if (!Array.isArray(p.profile) || p.profile.length !== BINS) {
    p.profile = new Array(BINS).fill(sky ? 90 : NO_BLOCK);
  }
  if (!Array.isArray(p.profileTop) || p.profileTop.length !== BINS) {
    p.profileTop = new Array(BINS).fill(NO_BLOCK);
  }
  for (let i = 0; i < BINS; i++) {
    if (fovBinRel(i, azOff, fov) !== null) continue;
    p.profile[i] = sky ? 90 : NO_BLOCK;
    p.profileTop[i] = NO_BLOCK;
  }
  p.profileBins = WORLD_BINS;
  return p.profile;
}

/** The whole web-side crop in ONE shared call: sets fov/azOffset, transforms +
 *  clips the strokes when present (strokes WIN, profile re-derived), otherwise
 *  clips the authoritative stored profile. Never touches the photo, the alt
 *  bounds, paint mode or block-above. Persisting is the caller's job. */
export function cropPano(p, sel, W) {
  const { fov, azOffset } = cropKeptBand(p.fov, p.azOffset, sel, W);
  p.fov = fov;
  p.azOffset = azOffset;
  if (Array.isArray(p.strokes) && p.strokes.length) {
    p.strokes = cropStrokes(p.strokes, sel, W);
    computeProfile(p);
  } else {
    clipProfileToFov(p);
  }
  return p;
}

/**
 * Ceiling lookup for one azimuth (deg). Returns { ceil, ceilTop }.
 * Blocking rule (applied per object sample):
 *   visible = alt >= minAlt && alt > ceil && alt < (ceilTop ?? 90.5)
 * Outside the calibrated FOV: obstacle mode = unblocked, sky mode = blocked.
 */
export function ceilFor(p, azDeg) {
  // Guard the record: an imported or legacy panorama without a well-formed
  // profile must degrade to "nothing blocks" (as the desktop and the server
  // do), never to `undefined` - `alt > undefined` is false, which would hide
  // every object in the sky.
  const prof = (p && Array.isArray(p.profile) && p.profile.length === BINS)
    ? p.profile : null;
  const tops = (p && Array.isArray(p.profileTop) && p.profileTop.length === BINS)
    ? p.profileTop : null;
  if (!prof) {
    return (p && p.paintMode === 'sky')
      ? { ceil: 90, ceilTop: null }
      : { ceil: NO_BLOCK, ceilTop: null };
  }
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  const d = ((azDeg - azOff) % 360 + 360) % 360;
  if (d > fov) {
    return p.paintMode === 'sky' ? { ceil: 90, ceilTop: null } : { ceil: NO_BLOCK, ceilTop: null };
  }
  const col = worldBin(azDeg);   // D1: world-azimuth bins (Python ceil_lookup)
  // CR2: the array guard above checks the LENGTH, not the ELEMENTS. A stored
  // profile can be 720 long and still hold NaN or a string (an imported or
  // hand-built record), and returning that value is the SAME hazard that guard
  // exists to prevent: `alt > NaN` is false, so every object at that azimuth
  // reads as blocked and vanishes from the planning, and `ceil: "x"` compares
  // false too. One bad ELEMENT degrades to the same sentinel the callers already
  // use for a missing profile ("nothing blocks").
  const element = (v, fallback) => (Number.isFinite(v) ? v : fallback);
  if (p.paintMode === 'sky') {
    // DEFAULT: everything ABOVE the painted band is free sky (open above), so
    // the band top is NOT a ceiling. Only when the user explicitly opts into
    // "block above the band" (p.blockAbove) does the painted top become a
    // ceiling (an overhang/balcony). The 90.5 sentinel means open to the zenith.
    const topRaw = (p.blockAbove && tops) ? tops[col] : null;
    const top = (Number.isFinite(topRaw) && topRaw > NO_BLOCK + 0.5) ? topRaw : null;
    return { ceil: element(prof[col], 90), ceilTop: top };
  }
  return { ceil: element(prof[col], NO_BLOCK), ceilTop: null };
}

/** Raw painted band TOP at an azimuth (sky mode), or null when the column is
 *  outside the FOV / unpainted. Unlike ceilFor this ignores blockAbove, so the
 *  chart can hatch the "assumed open above" area. */
export function bandTopFor(p, azDeg) {
  const prof = (p && Array.isArray(p.profile) && p.profile.length === BINS) ? p.profile : null;
  const tops = (p && Array.isArray(p.profileTop) && p.profileTop.length === BINS) ? p.profileTop : null;
  if (!prof || !tops) return null;
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  const d = ((azDeg - azOff) % 360 + 360) % 360;
  if (d > fov) return null;
  const col = worldBin(azDeg);   // D1: world-azimuth bins
  const t = tops[col];
  return (t != null && t > NO_BLOCK + 0.5) ? t : null;
}

/** Contiguous azimuth runs (deg, [a0,a1] inclusive) where a sky-mode OVERHANG
 *  exists - i.e. the user opted into "block above the band" AND the painted band
 *  has a real top edge BELOW the zenith, so the sky above it is blocked. With
 *  the default (everything above is free) there is no overhang -> []. */
export function overhangRuns(p, step = 2) {
  if (!p || p.paintMode !== 'sky' || !p.blockAbove) return [];
  const runs = [];
  let run = null;
  for (let az = 0; az < 360; az += step) {   // 360 == 0, not a separate sample
    const { ceilTop } = ceilFor(p, az);
    const has = ceilTop != null && ceilTop < 89.5;
    if (has) {
      if (!run) { run = { a0: az, a1: az }; runs.push(run); }
      else run.a1 = az;
    } else {
      run = null;
    }
  }
  return runs;
}

/** Contiguous azimuth runs where the region ABOVE the painted band is only
 *  visible because of the default assumption (sky mode, NOT blockAbove, band
 *  top below the zenith). Used to hatch that area + explain it in the legend. */
export function assumedOpenRuns(p, step = 2) {
  if (!p || p.paintMode !== 'sky' || p.blockAbove) return [];
  const prof = (Array.isArray(p.profile) && p.profile.length === BINS) ? p.profile : null;
  const tops = (Array.isArray(p.profileTop) && p.profileTop.length === BINS) ? p.profileTop : null;
  if (!prof || !tops) return [];
  const fov = Number(p.fov) > 0 ? Number(p.fov) : 360;
  const azOff = Number(p.azOffset) || 0;
  const runs = [];
  let run = null;
  for (let az = 0; az < 360; az += step) {
    const d = ((az - azOff) % 360 + 360) % 360;
    let has = false;
    if (d <= fov) {
      const col = worldBin(az);   // D1: world-azimuth bins
      const b = prof[col], t = tops[col];
      has = b > NO_BLOCK + 0.5 && b < 90 && t > NO_BLOCK + 0.5 && t < 89.5;
    }
    if (has) {
      if (!run) { run = { a0: az, a1: az }; runs.push(run); }
      else run.a1 = az;
    } else {
      run = null;
    }
  }
  return runs;
}

/** Closed polygon(s) of the assumed-open area ABOVE the painted band for the
 *  profile preview - one per contiguous run of band columns. The preview maps
 *  the BIN INDEX straight to x (full 720 bins across the width), so the hatch
 *  must be built in bin space too: using a world azimuth with `az * 2` only
 *  coincides at azOffset 0 / fov 360 and shifts the region otherwise. Returns
 *  [] unless sky mode with open-above (no blockAbove) is in effect. */
export function assumedOpenHatchPolys(p, W, H, ab, at) {
  if (!p || p.paintMode !== 'sky' || p.blockAbove) return [];
  const prof = (Array.isArray(p.profile) && p.profile.length === BINS) ? p.profile : null;
  const tops = (Array.isArray(p.profileTop) && p.profileTop.length === BINS) ? p.profileTop : null;
  if (!prof || !tops) return [];
  const span = Math.max(1, at - ab);
  const yOf = (alt) => H * (1 - (Math.min(Math.max(alt, ab), at) - ab) / span);
  const polys = [];
  let run = null;
  for (let col = 0; col < BINS; col++) {
    const b = prof[col], t = tops[col];
    const has = b > NO_BLOCK + 0.5 && b < 90 && t > NO_BLOCK + 0.5 && t < 89.5;
    if (!has) { run = null; continue; }
    if (!run) { run = []; polys.push(run); }
    run.push([(col / BINS) * W, yOf(t)]);
  }
  return polys.map((r) => {
    const pts = [...r];
    pts.push([r[r.length - 1][0], 0]);
    pts.push([r[0][0], 0]);
    return pts;
  });
}
