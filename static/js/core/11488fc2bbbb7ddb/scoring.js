/*
 * NightSeam core - scoring curves (pure).
 *
 * Phase 2, concept 5. Single implementations of the moon-aware scoring curves
 * and the silent popularity tiebreaker, owned by the client (the server ships
 * raw tracks only). No
 * Python counterpart: the scoring model is client-owned. No DOM, storage,
 * network or build tokens.
 */

import { gmstDeg, precessJ2000ToDate, refractionDeg, sepDeg } from './astro.js';
import { visibilityReason, summarizeVisibility } from './visibility.js';

/* how badly an object type suffers under moonlight (nebulae worst, clusters
   least). CO-09 (0.6.32 review): null prototype, because `otype` and the filter
   type are attacker-influenced strings and `{...}['toString']` would otherwise
   return an inherited function instead of falling back. */
export const MOON_TYPE_FACTOR = Object.assign(Object.create(null), {
  nebula: 1.6, supernova_remnant: 1.6, planetary: 1.3,
  star_cloud: 1.1, galaxy: 0.8, open_cluster: 0.55, globular: 0.5,
  asterism: 0.55,
});

export function moonSensitivity(otype) {
  return MOON_TYPE_FACTOR[otype] || 1.0;
}

/* Phase-dependent minimum moon distance (Lorentzian, symmetric about full
   moon): LARGEST at full (= BASE), smallest at new (= BASE/3.25).
   `moonAgeDays` is the folded age (0 = new ... ~14.76 = full). */
export function moonMinDist(filterType, moonAgeDays) {
  const BASE = Object.assign(Object.create(null),
    { narrowband: 60, oiii: 90, lrgb: 120 })[filterType] || 120;
  const frac = moonAgeDays / 29.53;
  return BASE / (1 + Math.pow((0.5 - frac) / 0.333, 2));
}

/* Illumination attenuated by separation and the filter's distance tolerance. */
export function effectiveMoonGlow(illum, sep, filterType, moonAgeDays) {
  const minD = moonMinDist(filterType, moonAgeDays);
  const distDeg = Math.max(0, sep - minD);
  return illum * Math.max(0, 1 - distDeg / 180);
}

/* Multiplier applied to a sample's altitude weight under moonlight. */
export function moonPenalty(glow, typeF, bortleF) {
  return Math.max(0.05, 1 - 0.95 * glow * typeF * bortleF);
}

/* Bortle scaling - darker skies reduce the moon penalty more; continuous at
   the 3/4 junction (0.30 at bortle 1 ... 1.12 at bortle 9). */
export function bortleMoonFactor(bortle) {
  if (bortle <= 3) return 0.25 + 0.05 * bortle;
  return 0.40 + 0.12 * (bortle - 3);
}

/* Hidden popularity tiebreaker (engagement scores). Pure number
   -> 0..1: log-scale normalization, M42 (165k) ~ 1.0, dim object (100) ~ 0.3.
   The shells resolve the live map/catalog field and pass the number. */
export function popularityFactor(pop) {
  if (!pop) return 0;
  return Math.min(1, 0.3 + 0.7 * Math.log10(pop) / 5.2);
}

/** Per-object apparent alt/az tracks for ONE (site, night): the sidereal frame
 *  is shared and each object is precessed once at the grid midpoint. Flat
 *  typed arrays - alt[k*count + i], az[k*count + i] for object k, sample i -
 *  so the expensive astronomy can be computed once and REUSED by preference
 *  changes (min-alt/Bortle) without recomputation. */
export function computeNightTracks(objects, jd0, jd1, step, lat, lon) {
  const n = Math.max(2, Math.round((jd1 - jd0) / step));
  const count = n + 1;
  const latR = lat * Math.PI / 180;
  const sinp = Math.sin(latR), cosp = Math.cos(latR);
  const jds = new Float64Array(count);
  const lstArr = new Float64Array(count);
  for (let i = 0; i < count; i++) {
    const jd = jd0 + i * step;
    jds[i] = jd;
    let lst = (gmstDeg(jd) + lon) % 360;
    if (lst < 0) lst += 360;
    lstArr[i] = lst;
  }
  const alt = new Float64Array(objects.length * count);
  const az = new Float64Array(objects.length * count);
  const mid = Math.floor(count / 2);
  for (let k = 0; k < objects.length; k++) {
    const o = objects[k];
    const [ra, dec] = precessJ2000ToDate(o.ra_hours * 15, o.dec_deg, jds[mid]);
    const dR = dec * Math.PI / 180;
    const sind = Math.sin(dR), cosd = Math.cos(dR);
    const base = k * count;
    for (let i = 0; i < count; i++) {
      let ha = (lstArr[i] - ra) % 360;
      if (ha > 180) ha -= 360;
      const h = ha * Math.PI / 180;
      const cosh = Math.cos(h), sinh = Math.sin(h);
      const sinAlt = sind * sinp + cosd * cosp * cosh;
      let a = Math.asin(Math.max(-1, Math.min(1, sinAlt)));
      const east = -cosd * sinh;
      const north = sind * cosp - cosd * sinp * cosh;
      let z = Math.atan2(east, north) * 180 / Math.PI % 360;
      if (z < 0) z += 360;
      alt[base + i] = a * 180 / Math.PI + refractionDeg(a * 180 / Math.PI);
      az[base + i] = z;
    }
  }
  return { n, count, alt, az };
}

/** Score precomputed tracks. This is the PREFERENCE-CHANGE path: a min-alt or
 *  Bortle change only calls this (no astronomy, no frame rebuild). `tracks`
 *  comes from computeNightTracks(); `step`/`jd0` reconstruct the sample times
 *  exactly as they were built.
 *
 *  `options.range` = `[lo, hi]` (INCLUSIVE grid indices) restricts every
 *  "night" number - visN, `% of night`, the clear window and both scores - to
 *  that span, while a sample's `vis`/`above` STATE stays full-grid. That is
 *  the ONE scoring-window policy: the desktop's payload grid deliberately
 *  carries twilight margins for its charts (241 samples, local noon -> local
 *  noon) and clips the numbers to dusk..dawn via the `range` parameter, while
 *  the retired web grid already was dusk..dawn and therefore passed no range.
 *  Every consumer runs THIS loop;
 *  only the grid anchor and the optional range differ, instead of each shell
 *  owning a separate aggregation. */
export function scoreNightTracks(tracks, objects, jd0, step, minAlt, pano, ctx = {}) {
  const { n, count, alt: altArr, az: azArr } = tracks;
  const win = Array.isArray(ctx.range) ? ctx.range : null;
  const winLo = win ? Math.max(0, Math.min(count - 1, win[0] | 0)) : 0;
  const winHi = win ? Math.max(winLo, Math.min(count - 1, win[1] | 0)) : count - 1;
  const wantAlts = !!ctx.maxAlt;

  // horizon profile as flat typed arrays; the ceiling is inlined bit-for-bit
  // with core/horizon.js::ceilFor (world-azimuth bins, FOV gate, blockAbove)
  const prof = (pano && Array.isArray(pano.profile) && pano.profile.length === 720)
    ? Float64Array.from(pano.profile) : null;
  const topsArr = (pano && Array.isArray(pano.profileTop) && pano.profileTop.length === 720)
    ? Float64Array.from(pano.profileTop) : null;
  const sky = !!(pano && pano.paintMode === 'sky');
  const fov = pano && Number(pano.fov) > 0 ? Number(pano.fov) : 360;
  const azOff = pano ? (Number(pano.azOffset) || 0) : 0;
  const blockAbove = !!(pano && pano.blockAbove);

  // moon path: the minimum distance only depends on the night's age + filter,
  // so the Math.pow curve is evaluated once per batch
  const moonAware = !!ctx.moonAware && Array.isArray(ctx.moonAlt);
  const moonAlt = moonAware ? ctx.moonAlt : null;
  const moonAz = moonAware ? ctx.moonAz : null;
  const minD = moonAware ? moonMinDist(ctx.filterType, ctx.moonAgeDays) : 0;
  const illum = ctx.illum;
  // moon sensitivity is per OBJECT (it depends on the type); a shell may still
  // pass one number for a uniform batch
  const typeFOf = Array.isArray(ctx.typeF)
    ? (k) => ctx.typeF[k] : () => ctx.typeF;

  const out = new Array(objects.length);
  /* The clear window IS the longest run of visible samples inside the scoring
   * window - one counter, because a run that crosses a twilight edge must not
   * be reported (the desktop used to advertise windows that included daylight).
   * With no window the scoring span is the whole grid, so the old behaviour is
   * bit-for-bit the same. `bestStart` is captured INCREMENTALLY (`jd` of the
   * first sample of the run) and never recomputed as `jd - run * step`, which
   * is the same instant only up to floating-point error - the characterization
   * goldens in tests/js/scoring_parity.mjs compare it bit-identically. */
  const isWin = win ? (i) => i >= winLo && i <= winHi : null;
  for (let k = 0; k < objects.length; k++) {
    const base = k * count;
    let maxAlt = -90, transitJd = jd0, bestRun = 0, bestStart = jd0;
    let riseJd = null, setJd = null, prevAlt = null;
    let run = 0, runStart = jd0, visN = 0, visibleFullN = 0;
    let aboveN = 0, blocked = 0, sClassic = 0, sMoon = 0;
    const reasonCounts = Object.create(null);
    const alts = wantAlts ? new Array(count) : null;
    const aboveBits = wantAlts ? new Uint8Array(count) : null;
    const visBits = wantAlts ? new Uint8Array(count) : null;
    for (let i = 0; i < count; i++) {
      const alt = altArr[base + i];
      const az = azArr[base + i];
      const jd = jd0 + i * step;

      if (alt > maxAlt) { maxAlt = alt; transitJd = jd; }
      if (alt > 0) aboveN++;
      if (prevAlt != null) {
        if (prevAlt <= 0 && alt > 0 && riseJd == null) {
          const f = prevAlt / (prevAlt - alt);
          riseJd = jd - step + f * step;
        } else if (prevAlt > 0 && alt <= 0 && setJd == null) {
          const f = prevAlt / (prevAlt - alt);
          setJd = jd - step + f * step;
        }
      }
      prevAlt = alt;
      if (wantAlts) alts[i] = alt;

      let ceil = -90, top = null;
      if (prof) {
        const d = (((az - azOff) % 360) + 360) % 360;
        if (d > fov) {
          ceil = sky ? 90 : -90;
        } else {
          let b = Math.floor((((az % 360) + 360) % 360) / 0.5);
          if (b > 719) b = 719;
          ceil = prof[b];
          if (sky && blockAbove && topsArr && topsArr[b] > -89.5) top = topsArr[b];
        }
      }

      const up = alt > 0;
      const vis = alt >= minAlt && alt > ceil && alt < (top == null ? 90.5 : top);
      const inRange = !isWin || isWin(i);
      const reason = visibilityReason(alt, minAlt, ceil, top, inRange);
      if (inRange) reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
      if (vis) visibleFullN++;
      if (wantAlts) {
        visBits[i] = vis ? 1 : 0;
        aboveBits[i] = up ? 1 : 0;
      }
      if (up && !vis) blocked++;                 // full grid (deliberate)
      if (!vis || !inRange) { run = 0; continue; }

      visN++;
      let w = Math.sin(Math.max(0, Math.min(90, alt)) * Math.PI / 180);
      sClassic += w;
      if (moonAware && moonAlt[i] > 0) {
        const sep = sepDeg(alt, az, moonAlt[i], moonAz[i]);
        const glow = illum * Math.max(0, 1 - Math.max(0, sep - minD) / 180);
        w *= Math.max(0.05, 1 - 0.95 * glow * typeFOf(k) * ctx.bortleF);
      }
      sMoon += w;
      if (run === 0) runStart = jd;
      run++;
      if (run > bestRun) { bestRun = run; bestStart = runStart; }
    }
    const nightN = winHi - winLo + 1;
    const summary = summarizeVisibility({
      visibleSamples: visN, visibleFullSamples: visibleFullN,
      stepMin: step * 1440, maxAlt, reasonCounts,
    });
    const bestEnd = bestRun > 0 ? bestStart + bestRun * step : null;
    out[k] = {
      n, maxAlt, transitJd, culminationJd: transitJd,
      culminationAlt: maxAlt, riseJd, setJd,
      visN, visibleFullN, aboveN, blocked, bestRun, bestStart, bestEnd,
      usableStartJd: bestRun > 0 ? bestStart : null,
      usableEndJd: bestEnd,
      visibleMinutes: summary.visibleMinutes,
      visibilityReason: summary.reason,
      reasonCounts: summary.reasonCounts,
      sClassic, sMoon,
      pct: Math.round(100 * visN / nightN),
      blockedPct: Math.round(100 * blocked / (n + 1)),
      alt: alts, vis: visBits, above: aboveBits,
    };
  }
  return out;
}

/** One-shot form: tracks + scoring for one (site, night, prefs). Identical
 *  output to scoring the precomputed tracks separately. */
export function nightStatsBatch(objects, jd0, jd1, step, minAlt, pano, lat, lon, ctx = {}) {
  return scoreNightTracks(computeNightTracks(objects, jd0, jd1, step, lat, lon),
                          objects, jd0, step, minAlt, pano, ctx);
}

/** Single-object form of nightStatsBatch (same implementation, no twin). */
export function nightStats(obj, jd0, jd1, step, minAlt, pano, lat, lon, ctx = {}) {
  return nightStatsBatch([obj], jd0, jd1, step, minAlt, pano, lat, lon, ctx)[0];
}
