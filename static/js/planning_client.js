/* Client-side planning (authority flip, 0.6.18): builds the SAME state shape
   the desktop UI already expects from GET /api/night, with the tracks computed
   locally by the shared core (computeNightTracks) and the horizon ceiling from
   the same world-azimuth bin rule the server uses.

   The builder is keyed to (site, date, catalog mode, horizon profile) and
   caches its payload: a site/date/catalog/profile change rebuilds the tracks,
   while preference changes (min-alt, Bortle, moonAware, filters, mag cap) never
   reach this module - derive.js::recomputeNightDerived rescores the stored
   tracks in place. A worker failure falls back to the synchronous client
   build; any failure then surfaces to the caller (no server compute exists). */

import { S } from "./state.js";
import { api, parseUTC } from "./util.js";
import { activeProfile, activeTopProfile, longestRunJS } from "./derive.js";
import {
  julianDay, dateFromJD, altaz, sunRaDec, moonRaDec, moonIllumination, moonAge,
  nightInfo, pickDarkWindow, computeNightTracks,
  visibilityReason, summarizeVisibility,
} from "./core.js";

const GRID_STEP_MIN = 6;
const GRID_N = 241;               // local noon -> next local noon, inclusive
const BINS = 720;                 // 0.5 deg world-azimuth profile bins
const NO_BLOCK = -90.0;
const DETAIL_STEP_MIN = 4;
const TRACK_CHUNK = 2000;         // flat tracks are converted+released per chunk

let _cache = { key: null, payload: null };
let _catCache = { adv: null, list: null };

/* ---------------- worker (0.6.19): off-thread tracks + derived fields ------ */

class SupersededError extends Error {
  constructor() { super("superseded"); this.name = "SupersededError"; }
}

const BIND_MS = 8;                 // bind budget per frame (no long tasks)
let _worker = null, _workerBroken = false;
let _wgen = 0, _bindGen = 0, _dgen = 0;
const _pending = new Map();
// A preference change during an in-flight build cannot be scored into a payload
// that was already requested with the OLD prefs; it is queued and applied to
// the finished plan instead of cancelling the build (FIX-1, review DS1).
let _buildsInFlight = 0;
let _prefsQueued = false;

function workerUsable() {
  return typeof Worker === "function" && !window.__NS_WORKER_FORCE_FAIL && !_workerBroken;
}

/* One handler for "the worker script died", used by the real `onerror` AND by
 * the dev hook below (which dispatches a genuine ErrorEvent at the worker, so a
 * browser check exercises this exact code path).
 *
 * DS5: mark it broken AND dispose of it. The flag alone left a DEAD Worker object
 * in `_worker`, so after `__NS_REVIVE_WORKER` the next build handed the corpse
 * back to `postMessage` instead of constructing a fresh worker - the revive hook
 * could not actually recover the worker path. The timeout path in `ask()` and
 * `__NS_KILL_WORKER` already terminate; this one did not. */
function onWorkerError(e) {
  _workerBroken = true;
  if (_worker) { _worker.terminate(); _worker = null; }
  for (const [, p] of _pending) {
    p.reject(new Error("worker error: " + ((e && e.message) || "?")));
  }
  _pending.clear();
}

function getWorker() {
  if (_worker) return _worker;
  _worker = new Worker("/js/planning_worker.js", { type: "module" });
  _worker.onmessage = (ev) => {
    const m = ev.data || {};
    const p = _pending.get(m.gen);
    if (!p) return;
    _pending.delete(m.gen);
    if (m.type === "error") p.reject(new Error("worker: " + m.error));
    else if (m.type === "cancelled") p.reject(new SupersededError());
    else p.resolve(m);
  };
  _worker.onerror = onWorkerError;
  return _worker;
}

function ask(msg) {
  const w = getWorker();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      _pending.delete(msg.gen);
      _workerBroken = true;
      if (_worker) { _worker.terminate(); _worker = null; }
      reject(new Error("worker timeout"));
    }, 120000);
    _pending.set(msg.gen, {
      resolve: (m) => { clearTimeout(timer); resolve(m); },
      reject: (e) => { clearTimeout(timer); reject(e); },
    });
    try { w.postMessage(msg); } catch (e) {
      clearTimeout(timer); _pending.delete(msg.gen); reject(e);
    }
  });
}

const frame = () => new Promise((r) =>
  typeof requestAnimationFrame === "function" ? requestAnimationFrame(() => r())
                                              : setTimeout(r, 0));

/* Dev-only test hooks (inert unless explicitly called; no effect on the app):
   gate 1 exercises a real terminate -> sync fallback -> fresh-worker recovery. */
if (typeof window !== "undefined") {
  window.__NS_KILL_WORKER = () => {
    if (!window.__NS_DEBUG_WORKER) return false;
    _workerBroken = true;
    if (_worker) { _worker.terminate(); _worker = null; }
    for (const [, p] of _pending) p.reject(new Error("worker terminated"));
    _pending.clear();
    return true;
  };
  window.__NS_REVIVE_WORKER = () => {
    if (!window.__NS_DEBUG_WORKER) return false;
    _workerBroken = false;
    return true;
  };
  /* DS5: drive the REAL failure path - dispatch a genuine ErrorEvent at the live
   * worker, which fires `onerror` (an `onerror` property is an event handler, so
   * its return value is ignored and the event reaches the registered listener).
   * Inert unless `__NS_DEBUG_WORKER` was set in devtools. */
  window.__NS_FAIL_WORKER = () => {
    if (!window.__NS_DEBUG_WORKER) return false;
    if (!_worker) return false;
    const ev = new ErrorEvent("error", { message: "dev: simulated worker crash" });
    _worker.dispatchEvent(ev);
    return true;
  };
}

/* The catalog is metadata only: reuse the boot copy (main.js::S.catalog) or the
   previous fetch - a date/site change must not re-download ~4 MB. */
async function catalogFor(adv) {
  if (_catCache.adv === adv && _catCache.list) return _catCache.list;
  if (Array.isArray(S.catalog) && S.catalog.length &&
      !!(S.cfg && S.cfg.adv_mode) === adv) {
    _catCache = { adv, list: S.catalog };
    return S.catalog;
  }
  const list = await api("/api/catalog" + (adv ? "?adv=1" : ""));
  _catCache = { adv, list };
  return list;
}

/** Drop the cached tracks (tests / explicit invalidation). */
export function invalidatePlanCache() { _cache = { key: null, payload: null }; }

/** Invalidate EVERY planning cache after the catalog changed (custom target
 *  add/delete, setup import): the metadata list, the built payload, and the
 *  worker's retained catalog/tracks. The id validation in bindWorkerNight is
 *  the safety net; this is what keeps the caches honest (FIX-2, review DS2). */
export function resetPlanningCaches() {
  _catCache = { adv: null, list: null };
  invalidatePlanCache();
  if (_worker) _worker.postMessage({ type: "catalog-reset" });
}

const iso = (dt) => (dt instanceof Date && !isNaN(dt))
  ? dt.toISOString().slice(0, 19) : null;
const r1 = (x) => Math.round(x * 10) / 10;

/** Cache-key fingerprint of a horizon profile's CONTENT (FNV-1a over the
 *  values rounded to 0.1 deg): the sync cache key used to cover only the pano
 *  id and array LENGTH, so a repaint that kept 720 bins served stale ceilings
 *  (FIX-3, review DS3). */
function profileKey(p) {
  if (!p || !p.length) return "-";
  let h = 2166136261;
  for (let i = 0; i < p.length; i++) {
    h ^= (Math.round(p[i] * 10) | 0);
    h = Math.imul(h, 16777619);
  }
  return p.length + ":" + (h >>> 0);
}

/** The server's ceil_lookup: pure world-bin lookup, no FOV gate - the stored
 *  profile already encodes outside-FOV behaviour per paint mode. */
function lookup(profile) {
  if (!profile || profile.length !== BINS) return () => NO_BLOCK;
  return (az) => {
    let i = Math.floor((((az % 360) + 360) % 360) / 0.5);
    if (i >= BINS) i = BINS - 1;
    const v = profile[i];
    return v == null ? NO_BLOCK : v;
  };
}

/** Grid + twilight window for one (date, site): the same site-zoned local-noon
 *  anchor the server uses (app/planning.py + nightcalc.compute_night). */
export function clientWindows(date, tzMin, lat, lon) {
  const [y, m, d] = String(date).split("-").map(Number);
  const noonMs = Date.UTC(y, m - 1, d, 12) - tzMin * 60000;
  const anchor = new Date(y, m - 1, d, 12, 0, 0);   // local getters = site date
  const info = nightInfo(anchor, lat, lon, new Date(noonMs));
  const w = pickDarkWindow(info, anchor, new Date(noonMs));
  return { y, m, d, noonMs, info, w };
}

function siteFor(siteId) {
  return api("/api/sites").then((sites) => {
    const wanted = siteId != null && siteId !== "" ? Number(siteId) : null;
    const site = sites.find((s) => s.id === wanted) ||
                 sites.find((s) => s.id === S.activeSiteId) || sites[0];
    if (!site) throw new Error("no observing site configured");
    return site;
  });
}

/** Worker-first night build; a worker failure falls back to the synchronous
 *  client path. */
export async function buildClientNight(opts) {
  _buildsInFlight++;
  try {
    if (workerUsable()) {
      try {
        return await buildWorkerNight(opts);
      } catch (err) {
        if (err && err.name === "SupersededError") throw err;
        console.warn("[planning] worker failed; synchronous client path:", err);
      }
    }
    return syncBuildClientNight(opts);
  } finally {
    _buildsInFlight--;
  }
}

async function buildWorkerNight({ date, siteId, tzMin }) {
  const site = await siteFor(siteId);
  const adv = !!(S.cfg && S.cfg.adv_mode);
  const pano = activeProfile();
  const topProf = activeTopProfile();
  const catalog = await catalogFor(adv);
  const gen = ++_wgen;
  _bindGen++;
  const res = await ask({
    gen, type: "night", date, tzMin, lat: site.lat, lon: site.lon,
    elev: site.elev || 0, adv, profile: pano, topProfile: topProf,
    prefs: { minAlt: S.minAlt, bortle: S.cfg && S.cfg.bortle,
             moonAware: !!(S.cfg && S.cfg.moon_aware),
             filters: S.cfg && S.cfg.filters },
  });
  return bindWorkerNight(res, catalog, pano, topProf);
}

/** Bind transferred flat tracks + packed derived fields into the per-object
 *  arrays the UI expects, in BIND_MS slices so no frame blocks. */
async function bindWorkerNight(res, catalog, pano, topProf) {
  const gen = _bindGen;
  const { n, count, alt, az, derived, meta } = res;
  if (n !== catalog.length) {
    throw new Error("catalog mismatch " + n + " vs " + catalog.length);
  }
  // Identity, not position: a same-length catalog change must never bind the
  // worker's tracks to the wrong object (FIX-2, review DS2). Throwing here
  // falls back to the synchronous build, which computes with THIS catalog.
  for (let j = 0; j < n; j++) {
    if (!res.ids || res.ids[j] !== catalog[j].id) {
      throw new Error("catalog ids mismatch at object " + j + " (" +
                      (res.ids ? res.ids[j] : "no ids") + " vs " +
                      catalog[j].id + ")");
    }
  }
  const ceilAt = lookup(pano), topAt = lookup(topProf);
  const objects = new Array(n);
  let start = 0;
  while (start < n) {
    const t0 = performance.now();
    while (start < n && performance.now() - t0 < BIND_MS) {
      const o = catalog[start], base = start * count;
      const alts = new Array(count), azs = new Array(count), ceils = new Array(count);
      const tops = topProf ? new Array(count) : null;
      for (let i = 0; i < count; i++) {
        alts[i] = alt[base + i]; azs[i] = az[base + i];
        ceils[i] = r1(ceilAt(az[base + i]));
        if (tops) tops[i] = r1(topAt(az[base + i]));
      }
      let maxI = 0;
      for (let i = 1; i < count; i++) if (alts[i] > alts[maxI]) maxI = i;
      const rec = { id: o.id, alt: alts, az: azs, ceil: ceils,
                    max_alt: alts[maxI], max_i: maxI,
                    vis: new Array(count), above: new Array(count) };
      if (o._adv) {
        rec.adv = true;
        rec.ra_hours = o.ra_hours; rec.dec_deg = o.dec_deg;
        rec.vmag = o.vmag; rec.otype = o.otype;
        rec.popularity = o.popularity || 0;
      }
      if (tops) rec.ceil_top = tops;
      for (let i = 0; i < count; i++) {
        rec.vis[i] = !!derived.visBits[base + i];
        rec.above[i] = !!derived.aboveBits[base + i];
      }
      applyDerivedNumbers(rec, derived, start);
      objects[start] = rec;
      start++;
    }
    if (gen !== _bindGen) throw new SupersededError();
    await frame();
  }
  return Object.assign({ objects, _clientTracks: true, _derivedReady: true }, meta);
}

function applyDerivedNumbers(o, d, j) {
  o.obs_pct = d.obsPct[j]; o.blocked_n = d.blockedN[j];
  o.score = d.score[j]; o.score_moon = d.scoreMoon[j];
  o.best = d.bS[j] < 0 ? null : [d.bS[j], d.bE[j]];
  o.best_above = d.aS[j] < 0 ? null : [d.aS[j], d.aE[j]];
  o.max_alt = Number.isFinite(d.maxAlt[j]) ? d.maxAlt[j] : o.max_alt;
  o.visible_minutes = d.visibleMinutes[j] || 0;
  o.transit_jd = Number.isFinite(d.transitJd[j]) ? d.transitJd[j] : null;
  o.culmination_jd = o.transit_jd;
  o.rise_jd = Number.isFinite(d.riseJd[j]) ? d.riseJd[j] : null;
  o.set_jd = Number.isFinite(d.setJd[j]) ? d.setJd[j] : null;
  o.visibility_reason = d.reason[j] || "no_usable_window";
}

/** Preference-change rescore through the worker (uses its retained tracks);
 *  returns false when the caller must run the synchronous fallback. A change
 *  that arrives while a build is in flight is QUEUED (returns true: do not
 *  rescore the old payload now); night.js calls flushQueuedPrefs() on the
 *  finished plan. */
export async function requestPrefsDerived() {
  if (_buildsInFlight > 0) {
    _prefsQueued = true;
    return true;
  }
  if (!S.night || !S.night._derivedReady || !workerUsable()) return false;
  const gen = ++_dgen;
  try {
    const res = await ask({
      gen: ++_wgen, type: "prefs", minAlt: S.minAlt,
      bortle: S.cfg && S.cfg.bortle,
      moonAware: !!(S.cfg && S.cfg.moon_aware),
      filters: S.cfg && S.cfg.filters,
    });
    if (gen !== _dgen) return true;         // superseded: discard, do not apply
    await bindDerived(S.night.objects, res.derived);
    return true;
  } catch (err) {
    if (err && err.name === "SupersededError") return true;
    console.warn("[planning] worker prefs failed; synchronous path:", err);
    return false;
  }
}

/** Apply a preference change that arrived while a build was in flight; the
 *  night loader calls this once the payload is committed, before the views
 *  render. Returns false when the caller must run the synchronous derivation. */
export async function flushQueuedPrefs() {
  if (!_prefsQueued) return true;
  _prefsQueued = false;
  return requestPrefsDerived();
}

async function bindDerived(objects, d) {
  const gen = _dgen, n = objects.length, count = S.night.timeline.n;
  let start = 0;
  while (start < n) {
    const t0 = performance.now();
    while (start < n && performance.now() - t0 < BIND_MS) {
      const o = objects[start];
      o.vis = new Array(count); o.above = new Array(count);
      for (let i = 0; i < count; i++) {
        o.vis[i] = !!d.visBits[start * count + i];
        o.above[i] = !!d.aboveBits[start * count + i];
      }
      applyDerivedNumbers(o, d, start);
      start++;
    }
    if (gen !== _dgen) throw new SupersededError();
    await frame();
  }
}

/** Build the /api/night payload client-side. `siteId` is the siteSelect value;
 *  `date` is the wall date from #dateInput; tzMin the effective offset at it. */
async function syncBuildClientNight({ date, siteId, tzMin }) {
  const site = await siteFor(siteId);
  const adv = !!(S.cfg && S.cfg.adv_mode);
  const pano = activeProfile();
  const topProf = activeTopProfile();
  const panoId = (S.panos.find((p) => p.active) ||
                  S.panos.find((p) => p.id === (S.cfg && S.cfg.active_pano_id)))?.id;
  // tzMin and the profile CONTENTS are part of the payload identity: the
  // timezone anchors the grid/windows and the profiles set every ceiling
  // (FIX-3, review DS3).
  const key = [site.id, date, tzMin, adv ? 1 : 0, panoId || "-",
               profileKey(pano), profileKey(topProf)].join("|");
  if (_cache.key === key && _cache.payload) return _cache.payload;

  const { noonMs, info, w } = clientWindows(date, tzMin, site.lat, site.lon);

  const timesIso = [];
  for (let i = 0; i < GRID_N; i++) {
    timesIso.push(new Date(noonMs + i * GRID_STEP_MIN * 60000)
      .toISOString().slice(0, 19));
  }
  const jds = timesIso.map((s) => julianDay(parseUTC(s)));
  const stepJd = GRID_STEP_MIN / 1440;

  const sunAlt = [], sunAz = [], moonAlt = [], moonAz = [];
  for (const jd of jds) {
    let [ra, dec] = sunRaDec(jd);
    let [a, z] = altaz(ra, dec, jd, site.lat, site.lon);
    sunAlt.push(r1(a)); sunAz.push(r1(z));
    [ra, dec] = moonRaDec(jd);
    [a, z] = altaz(ra, dec, jd, site.lat, site.lon);
    moonAlt.push(r1(a)); moonAz.push(r1(z));
  }
  const midJd = jds[Math.floor(jds.length / 2)];
  const moonIllum = Math.round(moonIllumination(midJd) * 100) / 100;
  const moonAgeDays = Math.round(moonAge(midJd) * 100) / 100;

  const catalog = await catalogFor(adv);
  const ceilAt = lookup(pano);
  const topAt = lookup(topProf);

  const objects = [];
  for (let start = 0; start < catalog.length; start += TRACK_CHUNK) {
    const chunk = catalog.slice(start, start + TRACK_CHUNK);
    const tracks = computeNightTracks(chunk, jds[0], jds[jds.length - 1],
                                      stepJd, site.lat, site.lon);
    const count = tracks.count;
    for (let k = 0; k < chunk.length; k++) {
      const o = chunk[k];
      const base = k * count;
      const alts = new Array(count), azs = new Array(count), ceils = new Array(count);
      const tops = topProf ? new Array(count) : null;
      let maxI = 0, maxRaw = -Infinity;
      for (let i = 0; i < count; i++) {
        const a = tracks.alt[base + i], z = tracks.az[base + i];
        alts[i] = r1(a); azs[i] = r1(z); ceils[i] = r1(ceilAt(z));
        if (tops) tops[i] = r1(topAt(z));
        if (a > maxRaw) { maxRaw = a; maxI = i; }   // raw argmax (server)
      }
      const rec = {
        id: o.id,
        alt: alts, az: azs, ceil: ceils,
        max_alt: r1(maxRaw), max_i: maxI,
      };
      if (o._adv) {
        rec.adv = true;
        rec.ra_hours = o.ra_hours; rec.dec_deg = o.dec_deg;
        rec.vmag = o.vmag; rec.otype = o.otype;
        rec.popularity = o.popularity || 0;
      }
      if (tops) rec.ceil_top = tops;
      objects.push(rec);
    }
  }

  const payload = {
    site: { lat: site.lat, lon: site.lon, elev: site.elev || 0 },
    date_local: date,
    tz_offset_min: tzMin,
    twilight_level: w.level,
    sunset: iso(info.sunset), sunrise: iso(info.sunrise),
    dusk: iso(w.dusk), dawn: iso(w.dawn),
    moon_illum: moonIllum, moon_age_days: moonAgeDays,
    timeline: { t0: timesIso[0], step_min: GRID_STEP_MIN,
                n: timesIso.length, times_iso: timesIso },
    sun_alt: sunAlt, sun_az: sunAz, moon_alt: moonAlt, moon_az: moonAz,
    objects,
    _clientTracks: true,   // night.js skips synthesizeAdvancedTracks
  };
  _cache = { key, payload };
  return payload;
}

/* ---------------- detail view (4-min grid, same computation as ----------- */
/* app/nightcalc.py::object_detail) ---------------------------------------- */

function fmtRA(raHours) {
  const h = ((raHours % 24) + 24) % 24;
  const hh = Math.floor(h);
  const mmF = (h - hh) * 60;
  const mm = Math.floor(mmF);
  const ss = (mmF - mm) * 60;
  return `${String(hh).padStart(2, "0")}h ${String(mm).padStart(2, "0")}m ${ss.toFixed(1).padStart(4, "0")}s`;
}

function fmtDec(decDeg) {
  const sign = decDeg < 0 ? "-" : "+";
  const a = Math.abs(decDeg);
  const dd = Math.floor(a);
  const mmF = (a - dd) * 60;
  const mm = Math.floor(mmF);
  const ss = (mmF - mm) * 60;
  return `${sign}${String(dd).padStart(2, "0")}deg ${String(mm).padStart(2, "0")}' ${ss.toFixed(1).padStart(4, "0")}"`;
}

/** Client detail payload; throws on unknown/malformed ids. */
export async function buildClientDetail(oid, { date, tzMin, minAlt, siteId }) {
  const meta = S.meta[oid];
  if (!meta || meta.ra_hours == null || meta.dec_deg == null) {
    throw new Error("object metadata unavailable");
  }
  const site = await siteFor(siteId != null ? siteId : S.activeSiteId);
  const { noonMs, w } = clientWindows(date, tzMin, site.lat, site.lon);
  const t0 = Math.max(w.dusk.getTime() - 75 * 60000, noonMs);
  const t1 = Math.min(w.dawn.getTime() + 45 * 60000, noonMs + 24 * 3600000);
  const times = [];
  for (let t = t0; t <= t1; t += DETAIL_STEP_MIN * 60000) times.push(t);
  const stepJd = DETAIL_STEP_MIN / 1440;
  const jd0 = julianDay(new Date(times[0]));
  const jd1 = julianDay(new Date(times[times.length - 1]));
  const tracks = computeNightTracks([meta], jd0, jd1, stepJd, site.lat, site.lon);
  if (tracks.count !== times.length) throw new Error("detail grid mismatch");

  const pano = activeProfile();
  const topProf = activeTopProfile();
  const ceilAt = lookup(pano);
  const topAt = lookup(topProf);
  const alts = [], azs = [], ceils = [], tops = topProf ? [] : null, vis = [];
  const duskMs = w.dusk.getTime(), dawnMs = w.dawn.getTime();
  const reasonCounts = Object.create(null);
  let visibleFullN = 0;
  for (let i = 0; i < times.length; i++) {
    const a = tracks.alt[i], z = tracks.az[i];
    const c = ceilAt(z);
    alts.push(Math.round(a * 100) / 100);
    azs.push(r1(z));
    ceils.push(Math.round(c * 100) / 100);
    if (tops) tops.push(r1(topAt(z)));
    const inNight = times[i] >= duskMs && times[i] <= dawnMs;
    const top = tops ? tops[i] : null;
    const visibleAcrossChart = visibilityReason(a, minAlt, c, top, true) === "visible";
    const reason = visibilityReason(a, minAlt, c, top, inNight);
    if (inNight) reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
    const isVis = reason === "visible";
    if (visibleAcrossChart) visibleFullN++;
    vis.push(isVis);
  }
  // DS4: the night payload picks its peak on the RAW altitude and reports it at
  // 1 dp (`max_alt: r1(maxRaw)`, `max_i: maxI` above); this payload rounded each
  // sample to 2 dp FIRST and then took the argmax of the rounded array. Near a
  // tie the two therefore chose DIFFERENT samples - the detail view's "max
  // altitude ... at HH:MM" could name the sample before or after the row it was
  // opened from, a whole 4-minute step apart, which is what "one-bin residual"
  // meant. The argmax now runs on the raw values, exactly like the worker.
  let maxI = 0, maxRaw = -Infinity;
  for (let i = 0; i < times.length; i++) {
    if (tracks.alt[i] > maxRaw) { maxRaw = tracks.alt[i]; maxI = i; }
  }
  const inNight = times.map((t) => t >= duskMs && t <= dawnMs);
  const best = longestRunJS(vis.map((v, i) => v && inNight[i]));
  const visibleNightN = vis.reduce((n, v, i) => n + (v && inNight[i] ? 1 : 0), 0);
  let riseJd = null, setJd = null;
  for (let i = 1; i < times.length; i++) {
    const a0 = tracks.alt[i - 1], a1 = tracks.alt[i];
    const jdPrev = jd0 + (i - 1) * stepJd;
    if (a0 <= 0 && a1 > 0 && riseJd == null) {
      riseJd = jdPrev + (a0 / (a0 - a1)) * stepJd;
    } else if (a0 > 0 && a1 <= 0 && setJd == null) {
      setJd = jdPrev + (a0 / (a0 - a1)) * stepJd;
    }
  }
  const summary = summarizeVisibility({
    visibleSamples: visibleNightN,
    visibleFullSamples: visibleFullN,
    stepMin: DETAIL_STEP_MIN,
    maxAlt: maxRaw,
    reasonCounts,
  });
  const isoBoundary = (index) => {
    const ms = index < times.length ? times[index]
      : times[times.length - 1] + DETAIL_STEP_MIN * 60000;
    return new Date(ms).toISOString().slice(0, 19);
  };

  const out = {
    meta: Object.assign({}, meta, {
      ra_hms: fmtRA(meta.ra_hours), dec_dms: fmtDec(meta.dec_deg),
    }),
    twilight_level: w.level,
    dusk: iso(w.dusk), dawn: iso(w.dawn),
    times_iso: times.map((t) => new Date(t).toISOString().slice(0, 19)),
    step_min: DETAIL_STEP_MIN,
    alt: alts, az: azs, ceil: ceils, vis,
    // DS4: the peak AT THE DETAIL GRID'S OWN PRECISION. `alt[]` holds 2-dp
    // samples, so reporting `r1(maxRaw)` (1 dp) would make this payload disagree
    // with itself: `max_time` names the sample at 78.95 while `max_alt` says
    // 78.9, and a caller reading "max altitude X at T" would get the wrong pair -
    // the very defect DS4 is about, moved rather than fixed. The FIX is the
    // argmax above running on the raw values (like the worker's) instead of on
    // the rounded array, so `max_time` and `max_alt` always name one sample.
    max_alt: Math.round(maxRaw * 100) / 100,
    max_time: new Date(times[maxI]).toISOString().slice(0, 19),
    culmination_jd: jd0 + maxI * stepJd,
    culmination_alt: Math.round(maxRaw * 100) / 100,
    rise_time: riseJd == null ? null : dateFromJD(riseJd).toISOString().slice(0, 19),
    set_time: setJd == null ? null : dateFromJD(setJd).toISOString().slice(0, 19),
    best,
    usable_start: best ? times[best[0]] && new Date(times[best[0]]).toISOString().slice(0, 19) : null,
    usable_end: best ? isoBoundary(best[1]) : null,
    visible_minutes: summary.visibleMinutes,
    visibility_reason: summary.reason,
    reason_counts: summary.reasonCounts,
    min_useful_alt: minAlt,
  };
  if (tops) out.ceil_top = tops;
  return out;
}
