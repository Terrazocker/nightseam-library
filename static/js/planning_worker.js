/* Planning worker (0.6.19): off-thread tracks + the shared derived scoring.
 *
 * Module worker importing the baked core AND derive.js::recomputeNightDerived -
 * the SAME definition the synchronous fallback runs, so results are identical
 * by construction. It receives {date, site, pano profile, prefs} and returns
 * TRANSFERABLE flat tracks + packed derived fields; the main thread binds them
 * into per-object arrays in short chunks. Cancellation: persistent worker +
 * generation counter, checked at every chunk boundary. */

import { S } from "./state.js";
import { recomputeNightDerived } from "./derive.js";
import {
  julianDay, altaz, sunRaDec, moonRaDec, moonIllumination, moonAge,
  nightInfo, pickDarkWindow, computeNightTracks,
} from "./core.js";

const GRID_STEP_MIN = 6;
const GRID_N = 241;
const BINS = 720;
const NO_BLOCK = -90.0;
const CHUNK = 2000;

let latestNightGen = -1;
let latestPrefsGen = -1;
let catalog = { adv: null, list: null, pending: null };
let catalogEpoch = 0;
let session = null;

self.onmessage = (ev) => {
  const m = ev.data || {};
  if (m.type === "catalog-reset") {
    // the main thread changed the catalog: drop the retained copy + tracks so
    // the next build refetches, and guard against a stale in-flight fetch
    // resolving into the reset state (FIX-2, review DS2).
    catalogEpoch++;
    catalog = { adv: null, list: null, pending: null };
    session = null;
    return;
  }
  if (m.type === "night") {
    latestNightGen = m.gen;
    runNight(m).catch((err) => post({ gen: m.gen, type: "error",
                                      error: String((err && err.message) || err) }));
  } else if (m.type === "prefs") {
    latestPrefsGen = m.gen;
    runPrefs(m).catch((err) => post({ gen: m.gen, type: "error",
                                      error: String((err && err.message) || err) }));
  }
};

const post = (msg, transfer) => self.postMessage(msg, transfer ? { transfer } : undefined);
const r1 = (x) => Math.round(x * 10) / 10;
const iso = (dt) => (dt instanceof Date && !isNaN(dt)) ? dt.toISOString().slice(0, 19) : null;

function lookup(profile) {
  if (!profile || profile.length !== BINS) return () => NO_BLOCK;
  return (az) => {
    let i = Math.floor((((az % 360) + 360) % 360) / 0.5);
    if (i >= BINS) i = BINS - 1;
    const v = profile[i];
    return v == null ? NO_BLOCK : v;
  };
}

async function catalogFor(adv) {
  if (catalog.adv === adv && catalog.list) return catalog.list;
  if (!catalog.pending) {
    const epoch = catalogEpoch;
    catalog.pending = fetch("/api/catalog" + (adv ? "?adv=1" : ""))
      .then((r) => { if (!r.ok) throw new Error("catalog HTTP " + r.status); return r.json(); })
      .then((list) => {
        if (epoch !== catalogEpoch) return list;   // reset meanwhile: do not store
        catalog = { adv, list, pending: null };
        return list;
      })
      .catch((err) => {
        if (epoch === catalogEpoch) catalog.pending = null;   // allow a retry
        throw err;
      });
  }
  return catalog.pending;
}

function setPrefs(p) {
  S.minAlt = p.minAlt;
  S.cfg = { bortle: p.bortle, moon_aware: p.moonAware, filters: p.filters };
}

/** Pack the derived fields recomputeNightDerived wrote, as transferables. */
function packDerived(objects, count) {
  const n = objects.length, total = n * count;
  const visBits = new Uint8Array(total), aboveBits = new Uint8Array(total);
  const obsPct = new Int32Array(n), blockedN = new Int32Array(n);
  const score = new Float64Array(n), scoreMoon = new Float64Array(n);
  const maxAlt = new Float64Array(n), visibleMinutes = new Float64Array(n);
  const transitJd = new Float64Array(n), riseJd = new Float64Array(n), setJd = new Float64Array(n);
  const reason = new Array(n);
  const bS = new Int32Array(n), bE = new Int32Array(n);
  const aS = new Int32Array(n), aE = new Int32Array(n);
  for (let j = 0; j < n; j++) {
    const o = objects[j], base = j * count;
    const vis = o.vis, above = o.above;
    for (let i = 0; i < count; i++) {
      if (vis[i]) visBits[base + i] = 1;
      if (above[i]) aboveBits[base + i] = 1;
    }
    obsPct[j] = o.obs_pct; blockedN[j] = o.blocked_n;
    score[j] = o.score; scoreMoon[j] = o.score_moon;
    maxAlt[j] = o.max_alt; visibleMinutes[j] = o.visible_minutes || 0;
    transitJd[j] = Number.isFinite(o.transit_jd) ? o.transit_jd : NaN;
    riseJd[j] = Number.isFinite(o.rise_jd) ? o.rise_jd : NaN;
    setJd[j] = Number.isFinite(o.set_jd) ? o.set_jd : NaN;
    reason[j] = o.visibility_reason || "no_usable_window";
    bS[j] = o.best ? o.best[0] : -1; bE[j] = o.best ? o.best[1] : -1;
    aS[j] = o.best_above ? o.best_above[0] : -1; aE[j] = o.best_above ? o.best_above[1] : -1;
  }
  return { visBits, aboveBits, obsPct, blockedN, score, scoreMoon,
    maxAlt, visibleMinutes, transitJd, riseJd, setJd, reason,
    bS, bE, aS, aE };
}

const DERIVED_TRANSFER = (d) => [
  d.visBits.buffer, d.aboveBits.buffer, d.obsPct.buffer, d.blockedN.buffer,
  d.score.buffer, d.scoreMoon.buffer, d.bS.buffer, d.bE.buffer, d.aS.buffer, d.aE.buffer,
  d.maxAlt.buffer, d.visibleMinutes.buffer, d.transitJd.buffer,
  d.riseJd.buffer, d.setJd.buffer,
];

async function runNight(m) {
  const t0 = performance.now();
  // night and prefs have SEPARATE generations: a preference change must never
  // cancel the build whose tracks it is about to rescore (the client queues
  // the rescore for the finished plan instead).
  const isCurrent = () => m.gen === latestNightGen;
  const list = await catalogFor(!!m.adv);
  if (!isCurrent()) return cancelled(m, t0);

  const [y, mo, d] = String(m.date).split("-").map(Number);
  const noonMs = Date.UTC(y, mo - 1, d, 12) - (m.tzMin || 0) * 60000;
  const anchor = new Date(y, mo - 1, d, 12, 0, 0);
  const info = nightInfo(anchor, m.lat, m.lon, new Date(noonMs));
  const w = pickDarkWindow(info, anchor, new Date(noonMs));
  const timesIso = [];
  for (let i = 0; i < GRID_N; i++)
    timesIso.push(new Date(noonMs + i * GRID_STEP_MIN * 60000).toISOString().slice(0, 19));
  const jds = timesIso.map((s) => julianDay(new Date(s + "Z")));
  const stepJd = GRID_STEP_MIN / 1440;

  const sunAlt = [], sunAz = [], moonAlt = [], moonAz = [];
  for (const jd of jds) {
    let [ra, dec] = sunRaDec(jd);
    let [a, z] = altaz(ra, dec, jd, m.lat, m.lon);
    sunAlt.push(r1(a)); sunAz.push(r1(z));
    [ra, dec] = moonRaDec(jd);
    [a, z] = altaz(ra, dec, jd, m.lat, m.lon);
    moonAlt.push(r1(a)); moonAz.push(r1(z));
  }
  const midJd = jds[Math.floor(jds.length / 2)];
  const moonIllum = Math.round(moonIllumination(midJd) * 100) / 100;
  const moonAgeDays = Math.round(moonAge(midJd) * 100) / 100;

  const ceilAt = lookup(m.profile), topAt = lookup(m.topProfile);
  const total = list.length * GRID_N;
  // DE-15: no flat `ceil` buffer. It was allocated (total * 8 bytes - ~23 MB in
  // advanced mode) and filled per sample, but never posted nor transferred: the
  // main thread derives every ceiling from `az` in bindWorkerNight. Only the
  // per-object `ceils` below are needed (they back the prefs rescore).
  const altFlat = new Float64Array(total), azFlat = new Float64Array(total);
  const objects = new Array(list.length);
  S.meta = {};
  for (const o of list) S.meta[o.id] = o;

  for (let start = 0; start < list.length; start += CHUNK) {
    if (!isCurrent()) return cancelled(m, t0);
    const chunk = list.slice(start, start + CHUNK);
    const tr = computeNightTracks(chunk, jds[0], jds[jds.length - 1], stepJd, m.lat, m.lon);
    if (tr.count !== GRID_N) throw new Error("grid mismatch " + tr.count);
    for (let k = 0; k < chunk.length; k++) {
      const o = chunk[k], j = start + k, base = j * GRID_N;
      const alts = new Array(GRID_N), azs = new Array(GRID_N), ceils = new Array(GRID_N);
      const tops = m.topProfile ? new Array(GRID_N) : null;
      for (let i = 0; i < GRID_N; i++) {
        const a = tr.alt[k * tr.count + i], z = tr.az[k * tr.count + i];
        alts[i] = r1(a); azs[i] = r1(z); ceils[i] = r1(ceilAt(z));
        altFlat[base + i] = alts[i]; azFlat[base + i] = azs[i];
        if (tops) tops[i] = r1(topAt(z));
      }
      const rec = { id: o.id, alt: alts, az: azs, ceil: ceils };
      if (o._adv) {
        rec.adv = true;
        rec.ra_hours = o.ra_hours; rec.dec_deg = o.dec_deg; rec.vmag = o.vmag;
        rec.otype = o.otype; rec.popularity = o.popularity || 0;
      }
      if (tops) rec.ceil_top = tops;
      objects[j] = rec;
    }
    await new Promise((r) => setTimeout(r, 0));   // cancellation delivery point
  }
  if (!isCurrent()) return cancelled(m, t0);

  setPrefs(m.prefs);
  S.night = { objects, timeline: { times_iso: timesIso, step_min: GRID_STEP_MIN, n: GRID_N },
              dusk: iso(w.dusk), dawn: iso(w.dawn),
              moon_alt: moonAlt, moon_az: moonAz,
              moon_illum: moonIllum, moon_age_days: moonAgeDays };
  recomputeNightDerived();
  session = { objects, count: GRID_N };

  const derived = packDerived(objects, GRID_N);
  const transfer = DERIVED_TRANSFER(derived);
  transfer.push(altFlat.buffer, azFlat.buffer);
  post({
    gen: m.gen, type: "night", ms: +(performance.now() - t0).toFixed(1),
    n: list.length, count: GRID_N, ids: list.map((o) => o.id),
    alt: altFlat, az: azFlat,
    meta: { site: { lat: m.lat, lon: m.lon, elev: m.elev || 0 },
            date_local: m.date, tz_offset_min: m.tzMin,
            twilight_level: w.level, sunset: iso(info.sunset), sunrise: iso(info.sunrise),
            dusk: iso(w.dusk), dawn: iso(w.dawn),
            moon_illum: moonIllum, moon_age_days: moonAgeDays,
            timeline: { t0: timesIso[0], step_min: GRID_STEP_MIN, n: GRID_N, times_iso: timesIso },
            sun_alt: sunAlt, sun_az: sunAz, moon_alt: moonAlt, moon_az: moonAz },
    derived,
  }, transfer);
}

async function runPrefs(m) {
  const t0 = performance.now();
  if (!session) throw new Error("no session");
  if (m.gen < latestPrefsGen) return;      // superseded by a newer prefs request
  setPrefs(m);
  recomputeNightDerived();
  const derived = packDerived(session.objects, session.count);
  post({ gen: m.gen, type: "prefs", ms: +(performance.now() - t0).toFixed(1),
         derived }, DERIVED_TRANSFER(derived));
}

function cancelled(m, t0) {
  post({ gen: m.gen, type: "cancelled", ms: +(performance.now() - t0).toFixed(1) });
}
