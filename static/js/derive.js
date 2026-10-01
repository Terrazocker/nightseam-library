/* pure derivations computed from raw night tracks + current preferences */

import { S } from "./state.js";
import { parseUTC, clamp, fmtHM } from "./util.js";
import { statusOf as coreStatusOf, effectiveMinAlt as coreMinAlt,
         moonSensitivity, effectiveMoonGlow, moonPenalty, bortleMoonFactor,
         popularityFactor as corePopularityFactor, scrubWindowMs,
         scrubBounds, scrubClamp, sepDeg, julianDay, scoreNightTracks }
  from "./core.js";

/* keyboard scrub steps in minutes - shared with the web (web/astro.js) and
   pinned by tests/test_js_parity.py: a plain arrow key is the fine step (one
   6-min night-grid sample), Shift is the coarse one (10 samples = 60 min). */
export const SCRUB_STEP_MIN = 6;
export const SCRUB_SHIFT_STEP_MIN = 60;

/* Selected-time sky visibility, per-sample status and the min-alt rule are
   shared in core/ (Phase 2, concept 4); this shell supplies the live S.minAlt.
   Mirrors web/astro.js::aboveHorizon ("abovehorizon" engine of
   tests/js/astro_parity.mjs). */
export { aboveHorizon } from "./core.js";

export { scrubBounds, scrubClamp } from "./core.js";

/* All-sky chart magnitude limit and "square" chart zoom - shared in core/
   (Phase 2, concept 1), pinned by the "mag" / "skyzoom" engines of
   tests/js/astro_parity.mjs. Re-exported so skyview.js keeps its imports. */
export {
  magVisible, SKY_ZOOM_MIN, SKY_ZOOM_MAX, skyClampZoom, skyViewMatrix,
  skyScreenToBase, skyClampFocus,
} from "./core.js";

/* Moon-aware scoring curves and the silent popularity tiebreaker - shared in
   core/ (Phase 2, concept 5), pinned by the "scoring" engine of
   tests/js/astro_parity.mjs. Re-exported so existing importers keep working. */
export {
  MOON_TYPE_FACTOR, moonSensitivity, moonMinDist,
  effectiveMoonGlow, moonPenalty, bortleMoonFactor,
} from "./core.js";

/* Longest run of true flags as a HALF-OPEN range [start, end) - the same
   contract as app/nightcalc.py's longest_run(): `end` is the first sample
   AFTER the run (== flags.length when the run reaches the last sample), so the
   run covers `end - start` samples. `end` is a boundary, never an index into
   the time grid. */
export function longestRunJS(flags) {
  let best = null, start = null;
  for (let i = 0; i <= flags.length; i++) {
    const f = i < flags.length && flags[i];
    if (f && start === null) start = i;
    else if (!f && start !== null) {
      if (!best || i - start > best[1] - best[0]) best = [start, i];
      start = null;
    }
  }
  return best;
}

/* Timestamp of a half-open window's END BOUNDARY, always a real time.
   `end` may equal the grid length (a run that reaches the last sample), where
   `timesIso[end]` does not exist - the window then ends one step after the
   final sample. Never returns undefined, so no consumer can render "--:--" or
   "NaNh NaNm" for a valid window. */
export function windowEndISO(timesIso, end, stepMin) {
  if (!timesIso || !timesIso.length) return null;
  if (end < timesIso.length) return timesIso[end];
  let step = stepMin;
  if (step == null) {
    step = timesIso.length > 1
      ? (parseUTC(timesIso[1]) - parseUTC(timesIso[0])) / 60000 : 0;
  }
  const d = parseUTC(timesIso[timesIso.length - 1]);
  d.setUTCMinutes(d.getUTCMinutes() + step);
  return d.toISOString().slice(0, 19);
}

export function timeIndexFor(dateObj) {
  if (!S.night) return -1;
  const tl = S.night.timeline;
  const t0 = parseUTC(tl.times_iso[0]).getTime();
  const step = tl.step_min * 60000;
  const i = Math.round((dateObj.getTime() - t0) / step);
  if (i < 0 || i >= tl.n) return -1;
  return i;
}

export function curTimeISO() {
  return S.night ? S.night.timeline.times_iso[S.tIdx] : null;
}

export function nightIndexRange() {
  return indexRangeFor(S.night.dusk, S.night.dawn);
}

/** First/last grid indices inside [startISO, endISO]; the whole grid when a
 *  bound is missing (polar day). Shared by the SCORING clip (dusk..dawn) and
 *  the SCRUB range (sunset..sunrise) so they can never drift. */
function indexRangeFor(startISO, endISO) {
  const tl = S.night.timeline;
  let i0 = 0, i1 = tl.n - 1;
  if (!startISO || !endISO) return [i0, i1];
  for (let i = 0; i < tl.n; i++) {
    if (parseUTC(tl.times_iso[i]) >= parseUTC(startISO)) { i0 = i; break; }
  }
  for (let i = tl.n - 1; i >= 0; i--) {
    if (parseUTC(tl.times_iso[i]) <= parseUTC(endISO)) { i1 = i; break; }
  }
  return [i0, Math.max(i0, i1)];
}

/** Grid index bounds [lo, hi] the scrub may select: the WHOLE night, from the
 *  sunset..sunrise pair when it is coherent (core/planning.js::scrubWindowMs),
 *  else dusk..dawn, else the site-zoned 20:00-04:00 fallback; the whole payload
 *  grid with view_full_day. SCORING still clips to dusk..dawn via
 *  nightIndexRange(). */
export function scrubWindowRange() {
  const tl = S.night && S.night.timeline;
  if (!tl) return [0, 0];
  if (S.cfg && S.cfg.view_full_day) return [0, tl.n - 1];
  const s = scrubWindowMs({
    sunsetMs: isoMs(S.night.sunset),
    sunriseMs: isoMs(S.night.sunrise),
    duskMs: isoMs(S.night.dusk),
    dawnMs: isoMs(S.night.dawn),
    noonMs: siteNoonMs(),
  });
  return indexRangeFor(msISO(s.startMs), msISO(s.endMs));
}

/* payload ISO (no Z) -> epoch ms; epoch ms -> the payload's ISO shape */
function isoMs(iso) { return iso ? parseUTC(iso).getTime() : null; }
function msISO(ms) { return new Date(ms).toISOString().slice(0, 19); }

/* the SITE's local noon from the payload (date_local + tz_offset_min) */
function siteNoonMs() {
  const n = S.night;
  const [y, m, d] = String(n.date_local || '').split('-').map(Number);
  if (!y || !m || !d) return null;
  return Date.UTC(y, m - 1, d, 12) - (n.tz_offset_min || 0) * 60000;
}

/* ---------- moon scoring helpers (shared by desktop + web) ---------- */

/* Filter type derived from user settings — narrowband filters tolerate
   much closer moon distances than broadband. */
function filterType() {
  const F = (S.cfg && S.cfg.filters) || {};
  if (F.narrowband_oiii) return "oiii";   // 90° base
  if (F.narrowband_ha)   return "narrowband"; // 60° base
  if (F.broadband)       return "lrgb";    // 120° base
  return "lrgb";
}



export function recomputeNightDerived() {
  const N = S.night;
  if (!N) return;
  const [i0, i1] = nightIndexRange();
  const minAlt = S.minAlt;
  const illum = N.moon_illum || 0;
  const bortle = (S.cfg && S.cfg.bortle) || 4;
  const fType = filterType();
  const moonAge = N.moon_age_days || 0;  // 0-14.76 (folded), 0 = new, ~14.76 = full
  const bortleF = bortleMoonFactor(bortle);

  /* ONE scoring loop for both shells: core/scoring.js::scoreNightTracks. This
     shell used to aggregate the same numbers inline, which is how the two apps
     drifted (CO-07). The desktop keeps its own GRID (the 241-sample
     noon->noon payload the charts draw) and passes the dusk..dawn scoring
     window in, exactly like the web passes no window because its grid already
     IS the dark window. Every "night" number - the clear window, `% of night`
     and both scores - is measured inside that window on BOTH sides. */
  const count = N.objects.length ? N.objects[0].alt.length : 0;
  if (!count) return;
  const jd0 = julianDay(parseUTC(N.timeline.times_iso[0]));
  const step = N.timeline.step_min / 1440;
  const trackAlt = new Float64Array(N.objects.length * count);
  const trackAz = new Float64Array(N.objects.length * count);
  const typeF = new Array(N.objects.length);
  for (let k = 0; k < N.objects.length; k++) {
    const o = N.objects[k], base = k * count;
    for (let i = 0; i < count; i++) {
      trackAlt[base + i] = o.alt[i];
      trackAz[base + i] = o.az[i];
    }
    const m = S.meta[o.id];
    typeF[k] = moonSensitivity(m && m.otype);
  }
  const tracks = { n: N.timeline.n, count, alt: trackAlt, az: trackAz };
  const ctx = {
    moonAlt: N.moon_alt, moonAz: N.moon_az,
    illum, moonAgeDays: moonAge, filterType: fType,
    moonAware: !!(S.cfg && S.cfg.moon_aware),
    typeF, bortleF,
    range: [i0, i1],
  };
  const pano = activePanoForScoring();
  // ONE pass. `o.alt`/`o.ceil` stay the payload's own arrays: they are what the
  // charts, the sky view and `buildClientDetail` read, and core computes its
  // ceilings from the same world-bin rule.
  const rows = scoreNightTracks(tracks, N.objects, jd0, step, minAlt, pano, ctx);

  for (let k = 0; k < N.objects.length; k++) {
    const o = N.objects[k], r = rows[k];
    o.vis = o.alt.map((a, i) => a >= minAlt && a > o.ceil[i]
                               && a < (o.ceil_top ? o.ceil_top[i] : 90.5));
    o.above = o.alt.map((a) => a > 0);
    o.obs_pct = r.pct;
    /* The clear window is reported as GRID INDICES - the desktop's `best`
       contract (windowText/windowEndISO index times_iso with them) - and it is
       the half-open longestRunJS the shell always used, so the window and the
       flags that colour the chart can never disagree. `best` is clipped to
       dusk..dawn; `best_above` is the same run over the whole payload grid. */
    const inNightFlags = o.vis.map((v, i) => v && i >= i0 && i <= i1);
    const aboveInNight = o.above.map((v, i) => v && i >= i0 && i <= i1);
    o.best = longestRunJS(inNightFlags);
    o.best_above = longestRunJS(aboveInNight) || longestRunJS(o.above);
    o.blocked_n = r.blocked;
    o.max_alt = r.maxAlt;
    o.culmination_alt = r.culminationAlt;
    o.culmination_jd = r.culminationJd;
    o.rise_jd = r.riseJd;
    o.set_jd = r.setJd;
    o.visible_minutes = r.visibleMinutes;
    o.visibility_reason = r.visibilityReason;
    o.reason_counts = r.reasonCounts;
    o.usable_start_jd = r.usableStartJd;
    o.usable_end_jd = r.usableEndJd;
    // the desktop's SCORE ROUNDING contract: two decimals (the web rounds the
    // same way in app.js::renderTonight). The engine and the Targets sort read
    // these numbers, so the scale has to stay bit-compatible.
    o.score = Math.round(r.sClassic * 10) / 100;
    o.score_moon = Math.round(r.sMoon * 10) / 100;
    o.transit_jd = r.transitJd;
  }
}

/** The active panorama in the shape core/scoring.js expects (world bins,
 *  sky/obstacle mode, FOV gate, block-above). `null` when none is active. */
function activePanoForScoring() {
  const act = S.panos.find(p => p.active) ||
              S.panos.find(p => p.id === (S.cfg && S.cfg.active_pano_id));
  if (!act || !act.profile || !act.profile.length) return null;
  return {
    profile: act.profile,
    profileTop: act.profile_top || null,
    paintMode: act.paint_mode === "sky" ? "sky" : "obstacle",
    fov: act.fov, azOffset: act.az_offset,
    blockAbove: !!act.block_above,
  };
}


/* great-circle separation is shared in core/ (Phase 2, concept 9) */


export function moonAt(dateObj) {
  if (!S.night) return null;
  const tl = S.night.timeline;
  const t0 = parseUTC(tl.times_iso[0]).getTime();
  const stepMs = tl.step_min * 60000;
  const f = (dateObj.getTime() - t0) / stepMs;
  if (f < 0 || f > tl.n - 1) return null;
  const i = Math.floor(f), j = Math.min(tl.n - 1, i + 1), fr = f - i;
  return S.night.moon_alt[i] * (1 - fr) + S.night.moon_alt[j] * fr;
}

/* x pixel for an absolute time within the detail-chart time span */
export function moonBandX(iso, d, ML, pw) {
  const t = parseUTC(iso).getTime();
  const t0 = parseUTC(d.times_iso[0]).getTime();
  const t1 = parseUTC(d.times_iso[d.times_iso.length - 1]).getTime();
  return ML + clamp((t - t0) / ((t1 - t0) || 1), 0, 1) * pw;
}

export function moonUpDownText() {
  if (!S.night) return "";
  const ma = S.night.moon_alt, tl = S.night.timeline;
  let first = -1, last = -1;
  for (let i = 0; i < tl.n; i++) if (ma[i] > 0) { if (first < 0) first = i; last = i; }
  if (first < 0) return "Moon below horizon all night";
  return `Moon up ${fmtHM(tl.times_iso[first])} \u2013 ${fmtHM(tl.times_iso[last])}`;
}

/* ---------------- per-object helpers ---------------- */

export const statusOf = (o, i) => coreStatusOf(o, i, S.minAlt);

/* The authoritative minimum-altitude rule (shared with the server and the
   web): an explicitly stored min_useful_alt wins; otherwise the Bortle-derived
   default 17 + 2*bortle applies ("darker skies -> lower default"). The server
   ships the resolved value PLUS the explicit flag (api_state), so the client
   can re-derive the threshold live when Bortle changes instead of freezing it
   at boot (the web has always done this in horizon.js::effectiveMinAlt). */
export const effectiveMinAlt = (cfg) => coreMinAlt(
  cfg && cfg.bortle,
  cfg && cfg.min_useful_alt_explicit ? cfg.min_useful_alt : null);

export function windowText(o) {
  const tl = S.night.timeline;
  if (!o.best) {
    if (o.best_above) {
      const [a, b] = o.best_above;
      return `<span class="wininfo">${fmtHM(tl.times_iso[a])}&ndash;${fmtHM(windowEndISO(tl.times_iso, b, tl.step_min))} <small>(obstructed)</small></span>`;
    }
    const why = {
      blocked_by_horizon: "blocked because of horizon",
      below_minimum_altitude: "below minimum altitude",
      outside_selected_window: "outside the selected window",
      never_rises: "never rises in this window",
      no_usable_window: "no usable visibility interval",
    }[o.visibility_reason] || "no usable visibility interval";
    return `<span class="wininfo"><small>${why}</small></span>`;
  }
  const [a, b] = o.best;
  const mins = (b - a) * tl.step_min;
  const total = Number.isFinite(o.visible_minutes) ? o.visible_minutes : mins;
  return `<span class="wininfo">${fmtHM(tl.times_iso[a])}&ndash;${fmtHM(windowEndISO(tl.times_iso, b, tl.step_min))}<br>` +
         `<small>${Math.floor(mins/60)}h ${String(mins%60).padStart(2,"0")}m clear · ${Math.round(total)}m visible</small></span>`;
}

/* Single-line window label for dense ranked/catalog tables. Keep the verbose
   reason and duration in the detail view; these compact lists already have a
   separate visibility/status column. */
export function compactWindowText(o) {
  const tl = S.night?.timeline;
  if (!tl?.times_iso?.length) return "—";
  const range = o.best || o.best_above;
  if (!range) return "—";
  const [start, end] = range;
  const label = `${fmtHM(tl.times_iso[start])}–${fmtHM(windowEndISO(tl.times_iso, end, tl.step_min))}`;
  return o.best ? label : `${label} · obstructed`;
}

export function desigLine(m) {
  const parts = [];
  if (m.messier) parts.push(m.messier);
  if (m.ngcic)   parts.push(m.ngcic.split("/")[0]);
  return parts.join(" \u00B7 ") || (m.other || "");
}

export function shortName(m) {
  return (m.messier || m.ngcic || m.primary_name).split("/")[0];
}

function activeProfile() {
  const act = S.panos.find(p => p.active) ||
              S.panos.find(p => p.id === (S.cfg && S.cfg.active_pano_id));
  return act && act.profile && act.profile.length ? act.profile : null;
}
export { activeProfile, activeTopProfile };

function activeTopProfile() {
  const act = S.panos.find(p => p.active) ||
              S.panos.find(p => p.id === (S.cfg && S.cfg.active_pano_id));
  // the painted band top caps the sky ONLY with the explicit block-above
  // opt-in; by default everything above the band is free sky (open above)
  return (act && act.block_above && act.profile_top && act.profile_top.length)
      ? act.profile_top : null;
}

/** Raw painted band top (ignores block_above) - used to HATCH the assumed-open
 *  area above the band when open-above is in effect. */
export function activeBandTop() {
  const act = S.panos.find(p => p.active) ||
              S.panos.find(p => p.id === (S.cfg && S.cfg.active_pano_id));
  return act && act.profile_top && act.profile_top.length
      ? act.profile_top : null;
}

/** Paint mode of the active panorama ('obstacle' | 'sky'), or null when none. */
export function activePaintMode() {
  const act = S.panos.find(p => p.active) ||
              S.panos.find(p => p.id === (S.cfg && S.cfg.active_pano_id));
  return act ? (act.paint_mode === "sky" ? "sky" : "obstacle") : null;
}

/* ---------------- scoring & filters ---------------- */

export function objScore(o) {
  if (S.cfg && S.cfg.moon_aware)
    return o.score_moon != null ? o.score_moon : (o.score || 0);
  return o.score || 0;
}

/* ---------------- hidden popularity scoring ----------------
   Popularity data from the popularity source (community engagement scores).
   Used as a silent tiebreaker — two objects with the same moon-aware
   score get differentiated by how popular they are with imagers.
   The score lives in the catalog as m.popularity (set during seed). */
/* Log-scale normalization: M42 (165k) → ~1.0, dim object (100) → ~0.3.
   Desktop callers pass the catalog meta object, so this shell resolves
   m.popularity and delegates to the pure core curve (number -> 0..1). */
export function popularityFactor(m) {
  return corePopularityFactor(m && m.popularity);
}

export function filterAdvice(m) {
  const F = (S.cfg && S.cfg.filters) || {};
  const hasNB = !!(F.narrowband_ha || F.narrowband_oiii);
  const hasBB = !!F.broadband;
  const t = m.otype;
  if (t === "nebula" || t === "supernova_remnant" || t === "planetary") {
    if (hasNB) return { pill: `<span class="fbadge ok">NB &#10003;</span>`,
                        text: "narrowband \u2713",
                        cls: "ok" };
    return { pill: `<span class="fbadge warn">NB</span>`,
             text: "narrowband",
             cls: "warn" };
  }
  const broadbandOnly = t === "galaxy" || t === "open_cluster"
                     || t === "globular" || t === "star_cloud" || t === "asterism";
  if (broadbandOnly && !hasBB && hasNB)
    return { pill: `<span class="fbadge bad">BB &#10007;</span>`,
             text: "broadband \u2717",
             cls: "bad" };
  return null;
}

/* ---------- rig profiles & frame coverage ---------- */

export function activeRig() {
  if (!S.cfg || !S.cfg.rigs) return null;
  return S.cfg.rigs.find(r => r.id === S.cfg.active_rig_id) || null;
}

/* linear span of the object's larger dimension vs its matching frame axis */
export function frameCoverage(m) {
  const rig = activeRig();
  if (!rig || !m.size_am) return null;
  const nums = String(m.size_am).split(/[x\u00D7]/).map(parseFloat)
                  .filter(v => !isNaN(v));
  if (!nums.length) return null;
  const ow = nums[0], oh = nums.length > 1 ? nums[1] : nums[0];  // arcmin
  const fw = rig.fov_w, fh = rig.fov_h;                          // arcmin
  if (!(fw > 0 && fh > 0)) return null;
  const span = Math.max(ow / fw, oh / fh);
  return { pct: Math.round(span * 100), fits: span <= 1,
           frames: span > 1 ? span : 1 };
}

export function coverageCell(cov) {
  if (!cov) return '<span class="hint">&mdash;</span>';
  // GREEN while the object fits in one frame, AMBER once it needs a mosaic
  // (0.6.30, the same method as the web). The old ramp went red above 100% and
  // amber above 85%, which flagged a merely-full frame as a problem.
  const cls = cov.pct > 100 ? "st-low" : "st-vis";
  const tip = cov.fits
    ? `object spans ${cov.pct}% of the frame`
    : `too large for one frame: needs ~${cov.frames.toFixed(1)} frames`;
  return `<span class="statuspill ${cls}" title="${tip}">${cov.pct}%</span>`;
}
