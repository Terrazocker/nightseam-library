/* Solar capture sessions (pure): conventional sunrise/set at geometric solar
 * centre altitude -0.833 degrees (upper limb plus standard atmospheric
 * refraction). Position/precession remain the existing shared astro core.
 * No terrain/elevation correction. UTC days only delimit polar fallback;
 * ordinary day/night windows follow the actual solar crossings.
 */
import { julianDay, sunRaDec, altaz } from './astro.js';

const DAY_MS = 86400000;
const STEP_MS = 300000;
const HORIZON_DEG = -0.833;

function solarLevel(ms, lat, lon) {
  const jd = julianDay(new Date(ms));
  const [ra, dec] = sunRaDec(jd);
  return altaz(ra, dec, jd, lat, lon, false)[0] - HORIZON_DEG;
}

function site(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)
      || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    throw new Error('Solar sessions require valid observing coordinates');
  }
  const rounded = (v) => Math.round(v * 1000) / 1000 || 0;
  return [rounded(lat), rounded(lon)];
}

/** All complete or explicitly polar windows intersecting one UTC day.
 * Crossings are the first integer millisecond on the new side of the boundary,
 * so half-open membership remains stable when adjoining scan days are cached.
 */
export function solarSessionWindows(dayMs, latitude, longitude) {
  if (!Number.isFinite(dayMs) || dayMs % DAY_MS !== 0) {
    throw new Error('Solar session scan must start at UTC midnight');
  }
  const [lat, lon] = site(latitude, longitude);
  const start = dayMs - 2 * DAY_MS, finish = dayMs + 3 * DAY_MS;
  const samples = [];
  for (let ms = start; ms <= finish; ms += STEP_MS) {
    samples.push({ ms, level: solarLevel(ms, lat, lon) });
  }
  // A grazing polar sunrise/set pair can be shorter than the coarse grid.
  // Find the smooth daily extrema and insert their actual phase, so that a
  // brief interval cannot disappear merely because no grid point landed in it.
  const extrema = [];
  for (let i = 1; i + 1 < samples.length; i++) {
    const previous = samples[i - 1], current = samples[i], next = samples[i + 1];
    const maximum = current.level > previous.level && current.level >= next.level;
    const minimum = current.level < previous.level && current.level <= next.level;
    if (!maximum && !minimum) continue;
    let lo = previous.ms, hi = next.ms;
    while (hi - lo > 2) {
      const a = lo + Math.floor((hi - lo) / 3);
      const b = hi - Math.floor((hi - lo) / 3);
      const aLevel = solarLevel(a, lat, lon), bLevel = solarLevel(b, lat, lon);
      if ((aLevel < bLevel) === maximum) lo = a;
      else hi = b;
    }
    const ms = Math.floor((lo + hi) / 2);
    extrema.push({ ms, level: solarLevel(ms, lat, lon) });
  }
  samples.push(...extrema);
  samples.sort((a, b) => a.ms - b.ms);
  const events = [];
  let previousMs = start, previousDay = samples[0].level >= 0;
  const firstDay = previousDay;
  for (const sample of samples.slice(1)) {
    const ms = sample.ms, isDay = sample.level >= 0;
    if (isDay !== previousDay) {
      let lo = previousMs, hi = ms;
      while (hi - lo > 1) {
        const middle = Math.floor((lo + hi) / 2);
        if ((solarLevel(middle, lat, lon) >= 0) === previousDay) lo = middle;
        else hi = middle;
      }
      events.push({ ms: hi, day: isDay });
    }
    previousMs = ms; previousDay = isDay;
  }
  const edges = [{ ms: start, day: firstDay }, ...events, { ms: finish }];
  const windows = [];
  const stamp = (ms) => new Date(ms).toISOString().replace(/Z$/, '');
  const date = new Date(dayMs).toISOString().slice(0, 10);
  const siteKey = `${lat.toFixed(3)},${lon.toFixed(3)}`;
  for (let i = 0; i + 1 < edges.length; i++) {
    const a = edges[i], b = edges[i + 1];
    if (a.ms >= dayMs + DAY_MS || b.ms <= dayMs) continue;
    // A continuous polar phase must never become a months-long session.
    const ordinary = i > 0 && i + 1 < edges.length - 1
      && b.ms - a.ms <= 26 * 3600000;
    const kind = `${ordinary ? '' : 'polar_'}${a.day ? 'day' : 'night'}`;
    const begin = ordinary ? a.ms : Math.max(a.ms, dayMs);
    const end = ordinary ? b.ms : Math.min(b.ms, dayMs + DAY_MS);
    windows.push({
      session_key: ordinary
        ? `${siteKey}:${kind}:${stamp(begin)}:${stamp(end)}`
        : `${siteKey}:${kind}:${date}`,
      session_kind: kind,
      session_start: stamp(begin), session_end: stamp(end),
    });
  }
  return windows;
}

/** Direct convenience API; Python caches solarSessionWindows by UTC day/site. */
export function solarSession(epochMs, latitude, longitude) {
  if (!Number.isFinite(epochMs)) throw new Error('Invalid capture instant');
  const dayMs = Math.floor(epochMs / DAY_MS) * DAY_MS;
  const result = solarSessionWindows(dayMs, latitude, longitude).find((interval) =>
    Date.parse(`${interval.session_start}Z`) <= epochMs
      && epochMs < Date.parse(`${interval.session_end}Z`));
  if (!result) throw new Error('Solar session scan did not cover capture');
  return result;
}
