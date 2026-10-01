/*
 * NightSeam core - twilight / night-window planning (pure).
 *
 * Phase 2, concept 8. This module IS the implementation: the noon+30 h scan,
 * the 10-minute scan with linear refinement, the astronomical -> nautical ->
 * civil cascade and the fixed 20:00-04:00 polar fallback. The retired Python
 * oracle (`app/planning.py`, deleted in Phase C/0.6.20) survives only as the
 * FROZEN regression golden tests/fixtures/core_fixtures.json - the twilight
 * section of tests/test_core_fixtures.py compares every site/date at 180 s -
 * and the CORRECTNESS authority is tests/js/reference_anchors.mjs. Never read
 * this module as a port of a live oracle; there is none.
 *
 * Pairing rule (mirrors planning.py::find_twilight): a dawn only exists AFTER
 * the evening crossing, and it is the FIRST such ascent. A grazing polar day
 * can show an ascent before any descent (Tromso 2026-01-15: the refracted sun
 * pops up at local noon and sets 29 min later) - that is NOT tonight's
 * sunrise, so it is ignored and the oracle's `sunrise = null` is reported
 * instead of an inverted sunset/sunrise pair.
 */

import { julianDay, dateFromJD, sunAlt } from './astro.js';

/** Sunset/sunrise crossings for one threshold on a noon-anchored 30 h scan.
 *  Returns { evening: Date|null, morning: Date|null } (evening = descending). */
export function sunCrossings(scanStartUTC, lat, lon, thresholdDeg) {
  const jd0 = julianDay(scanStartUTC);
  const N = 180; // 10-min steps over 30 h
  let prev = sunAlt(jd0, lat, lon) - thresholdDeg;
  let prevSign = Math.sign(prev);
  let evening = null, morning = null;
  for (let i = 1; i <= N; i++) {
    const jd = jd0 + i / 144; // 10 minutes = 1/144 day
    const v = sunAlt(jd, lat, lon) - thresholdDeg;
    const sign = Math.sign(v);
    if (sign !== prevSign && prevSign !== 0) {
      // crossing between step i-1 and i
      const f = prev / (prev - v); // fraction from i-1 to i
      const jdX = jd0 + (i - 1 + f) / 144;
      const dt = dateFromJD(jdX);
      // descending (sign becomes negative) = evening crossing; ascending =
      // morning, but ONLY once an evening exists (see the header): the first
      // ascent after dusk is tonight's dawn, exactly like planning.py.
      if (sign < 0) { if (!evening) evening = dt; }
      else if (evening) { morning = dt; break; }
    }
    prev = v; prevSign = sign;
  }
  return { evening, morning };
}

/** Night summary for one local date at a site.
 *  Returns { sunset, civilDusk, nauticalDusk, astroDusk, astroDawn, nauticalDawn, civilDawn, sunrise }
 *  each Date|null, plus dayStart (scan window start).
 *
 *  `localNoon` is the REAL instant of the site's local noon for that date
 *  (app.js computes it with webstore's wallToUtc). Without it the browser's own
 *  zone stands in for the site's - correct only while both are the same. That
 *  fallback is what made planning for a site far EAST of the browser pick the
 *  previous evening's dusk and a dawn up to two days later: the scan window
 *  then started before the previous local noon, so "first descending crossing"
 *  is not tonight's dusk. */
export function nightInfo(localDate, lat, lon, localNoon) {
  // Scan window: the SITE's local noon of the target date + 30 h - the anchor
  // the retired app/planning.py used (local_utc(y, m, d, tz, 12) + 30 h), so the
  // first descending crossing is always the TARGET evening's dusk (a
  // midnight-anchored window can report the previous night's dusk at
  // twilight-edge latitudes, see sunCrossings). `new Date(y, m, d, 12)`
  // carries the BROWSER's zone, which is why `localNoon` must be the site's
  // real noon instant for sites outside it.
  const anchor = (localNoon instanceof Date && !isNaN(localNoon))
    ? localNoon
    : new Date(localDate.getFullYear(), localDate.getMonth(), localDate.getDate(), 12, 0, 0);
  const scanStart = new Date(anchor.getTime());
  const g = (thr) => sunCrossings(scanStart, lat, lon, thr);
  const zero = g(0), civil = g(-6), naut = g(-12), astro = g(-18);
  return {
    dayStart: scanStart,
    sunset: zero.evening, sunrise: zero.morning,
    civilDusk: civil.evening, civilDawn: civil.morning,
    nauticalDusk: naut.evening, nauticalDawn: naut.morning,
    astroDusk: astro.evening, astroDawn: astro.morning,
  };
}

/** Pick the dark window used for every visibility number.
 *
 *  Desktop/web parity (the retired app/planning.py `night_window`): use the
 *  ASTRONOMICAL night
 *  when the sun reaches -18 deg, else nautical (-12), else civil (-6). When no
 *  twilight level is reached at all (polar day, polar twilight - the sun may
 *  still set without ever crossing -6 deg) the same fixed evening-to-morning
 *  fallback applies as in planning.py: 20:00 local -> 04:00 local the next
 *  day, with level 'fallback'. Sunset/sunrise NEVER become the window: the
 *  desktop's fallback is the fixed 20:00-04:00 span, and using the actual
 *  sunset..sunrise pair instead produced a different (shorter) window for the
 *  same polar-twilight night (measured: 8.0 h vs 2.1 h at Tromso in May).
 *
 *  `localNoon` is the real instant of the SITE's local noon (app.js passes
 *  siteNoon()); the fallback is then built in the SITE's zone - 20:00/04:00
 *  local - exactly like planning.py's tz_min arithmetic. Without it the
 *  browser's zone stands in, correct only while both are the same.
 *  `dayDate` is the local-noon anchor of the night (also accepted in place of
 *  `localNoon`); there is NO wall-clock default - a missing anchor throws, so
 *  a polar-day chip can never show `now+8h..now+16h` (FIX-6, review CR1). */
export function pickDarkWindow(info, dayDate, localNoon) {
  const level = (info.astroDusk && info.astroDawn) ? "astronomical"
    : (info.nauticalDusk && info.nauticalDawn) ? "nautical"
      : (info.civilDusk && info.civilDawn) ? "civil"
        : null;
  const dusk = info.astroDusk || info.nauticalDusk || info.civilDusk || null;
  const dawn = info.astroDawn || info.nauticalDawn || info.civilDawn || null;
  if (dusk && dawn) return { dusk, dawn, level: level || "fallback" };

  const anchor = (localNoon instanceof Date && !isNaN(localNoon))
    ? localNoon
    : dayDate;
  if (!(anchor instanceof Date) || isNaN(anchor)) {
    throw new Error(
      "pickDarkWindow: the polar fallback needs an explicit anchor (the " +
      "site's local noon or the night's day date); it must never read the " +
      "wall clock");
  }
  return {
    dusk: new Date(anchor.getTime() + 8 * 3600000),     // 20:00 local
    dawn: new Date(anchor.getTime() + 16 * 3600000),    // 04:00 local, next day
    level: level || "fallback",
  };
}

/** Choose the sunset->sunrise-derived range used by the scrub slider and the
 *  detail chart. The pair is used ONLY when it is strictly ordered AND the
 *  sunset precedes the next dawn: a grazing polar day can "set" around local
 *  noon AFTER the dark window has ended (Tromso 2026-01-15 - sunset 11:08Z,
 *  dawn 05:12Z), and using it built an inverted range in both apps.
 *  Otherwise: dusk -> dawn; when those are missing, the site-zoned
 *  20:00-04:00 fallback (localNoon + 8 h / + 16 h).
 *
 *  All inputs are epoch milliseconds (null = absent); `noonMs` must be the
 *  SITE's local-noon instant so the fallback lands on the site's wall clock.
 *  Returns { startMs, endMs, source } with startMs < endMs always. */
export function scrubWindowMs(win) {
  const { sunsetMs, sunriseMs, duskMs, dawnMs, noonMs } = win || {};
  if (sunsetMs != null && sunriseMs != null && sunsetMs < sunriseMs
      && (dawnMs == null || sunsetMs < dawnMs)) {
    return { startMs: sunsetMs, endMs: sunriseMs, source: "sun" };
  }
  if (duskMs != null && dawnMs != null && duskMs < dawnMs) {
    return { startMs: duskMs, endMs: dawnMs, source: "twilight" };
  }
  const anchor = noonMs != null ? noonMs : 0;
  return { startMs: anchor + 8 * 3600000, endMs: anchor + 16 * 3600000,
           source: "fallback" };
}

/** Scrub-range width policy: `viewFullDay` false => the night window (already
 *  chosen by scrubWindowMs/the caller; dusk..dawn or the polar fallback); true
 *  => the app's full span (web: the 24 h noon->noon day; desktop: its payload
 *  grid). Inputs/outputs are epoch milliseconds; a missing/short night window
 *  degrades to the full span. */
export function scrubBounds(viewFullDay, nightStart, nightEnd, fullStart, fullEnd) {
  if (!viewFullDay && nightStart != null && nightEnd != null && nightEnd > nightStart) {
    return { start: nightStart, end: nightEnd };
  }
  return { start: fullStart, end: fullEnd };
}

/** Clamp an instant into a scrub range - the selected time must never sit
 *  outside the window when "View full day" is switched off (e.g. 1 pm). */
export function scrubClamp(t, start, end) {
  if (t < start) return start;
  if (t > end) return end;
  return t;
}
