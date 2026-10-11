/*
 * NightSeam core - coordinates, time and topocentric position (pure).
 *
 * Phase 2, concept 6. One implementation of the Meeus algorithms both shells
 * used to carry separately (desktop static/js/astro.js, web web/astro.js):
 * Julian day, Greenwich mean sidereal time, J2000 precession both ways,
 * Saemundsson refraction and topocentric alt/az, plus the desktop batch
 * altazFrame/altazFrameApply fast path for advanced-track synthesis.
 *
 * This module IS the implementation. The retired Python oracle (app/astro.py,
 * deleted in Phase C/0.6.20) survives only as the FROZEN regression fixtures
 * tests/fixtures/core_fixtures.json; the CORRECTNESS authority is
 * tests/js/reference_anchors.mjs (published IAU 2006 / Meeus / ERFA values).
 */

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const ASEC2DEG = 1 / 3600;
const J2000 = 2451545.0;
const MS_PER_DAY = 86400000;
const JD_UNIX_EPOCH = 2440587.5;

/** Julian Day from a Date (its UTC instant). */
export function julianDay(dt) {
  let y = dt.getUTCFullYear();
  let m = dt.getUTCMonth() + 1;
  const d = dt.getUTCDate()
    + (dt.getUTCHours() + dt.getUTCMinutes() / 60 + dt.getUTCSeconds() / 3600
       + dt.getUTCMilliseconds() / 3600000) / 24;
  if (m <= 2) { y -= 1; m += 12; }
  const a = Math.floor(y / 100);
  const b = 2 - a + Math.floor(a / 4);
  return Math.floor(365.25 * (y + 4716)) + Math.floor(30.6001 * (m + 1)) + d + b - 1524.5;
}

/** Date (UTC) from a Julian Day. */
export function dateFromJD(jd) {
  return new Date((jd - JD_UNIX_EPOCH) * MS_PER_DAY);
}

/** Greenwich mean sidereal time in degrees. */
export function gmstDeg(jd) {
  const d = jd - J2000;
  const t = d / 36525.0;
  const g = 280.46061837 + 360.98564736629 * d + 0.000387933 * t * t - (t ** 3) / 38710000.0;
  return ((g % 360) + 360) % 360;
}

/** Precess ICRS/J2000 coords to mean equinox of date (Meeus ch.21).
 *  Z(zeta) -> Y(theta) -> Z(z): the MIDDLE rotation is about the Y axis.
 *  This is the ONE implementation; the retired app/astro.py implemented the
 *  same equations, and the retired cross-shell parity runner was superseded by
 *  tests/js/reference_anchors.mjs (published IAU-2006/Meeus values) as the
 *  correctness authority. */
export function precessJ2000ToDate(raDeg, decDeg, jd) {
  const t = (jd - J2000) / 36525.0;
  const zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) * ASEC2DEG;
  const z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) * ASEC2DEG;
  const theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) * ASEC2DEG;

  const a = D2R * raDeg;
  const d = D2R * decDeg;
  const zr = D2R * zeta, th = D2R * theta;

  const cosd = Math.cos(d), sind = Math.sin(d);
  const sinAz = Math.sin(a + zr), cosAz = Math.cos(a + zr);

  const A = cosd * sinAz;
  const B = Math.cos(th) * cosd * cosAz - Math.sin(th) * sind;
  const C = Math.sin(th) * cosd * cosAz + Math.cos(th) * sind;

  const raNew = ((Math.atan2(A, B) * R2D + z) % 360 + 360) % 360;
  const decNew = Math.asin(Math.max(-1, Math.min(1, C))) * R2D;
  return [raNew, decNew];
}

/** Precess equinox-of-date back to J2000 (inverse).
 *  Meeus ch.21: same equations with (zeta, z, theta) -> (-z, -zeta, -theta). */
export function precessDateToJ2000(raDeg, decDeg, jd) {
  const t = (jd - J2000) / 36525.0;
  const zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) * ASEC2DEG;
  const z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) * ASEC2DEG;
  const theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) * ASEC2DEG;

  const a = D2R * raDeg;
  const d = D2R * decDeg;
  const zr = D2R * (-z), th = D2R * (-theta);

  const cosd = Math.cos(d), sind = Math.sin(d);
  const sinAz = Math.sin(a + zr), cosAz = Math.cos(a + zr);

  const A = cosd * sinAz;
  const B = Math.cos(th) * cosd * cosAz - Math.sin(th) * sind;
  const C = Math.sin(th) * cosd * cosAz + Math.cos(th) * sind;

  const raNew = ((Math.atan2(A, B) * R2D - zeta) % 360 + 360) % 360;
  const decNew = Math.asin(Math.max(-1, Math.min(1, C))) * R2D;
  return [raNew, decNew];
}

/** Saemundsson/Bennett refraction (degrees). */
export function refractionDeg(trueAltDeg) {
  const h = Math.max(-1.5, trueAltDeg);
  let r = 1.02 / Math.tan(D2R * (h + 10.3 / (h + 5.11))) / 60.0;
  if (trueAltDeg < -1.5) r = 0;
  return r;
}

/** Topocentric altitude/azimuth (N=0, E=90). RA/Dec assumed J2000; the site
 *  altitude is apparent unless `refract` is false. `precess` false is for the
 *  decorative sky backdrop, which deliberately plots J2000 field stars without
 *  the (tiny) precession shift - the projection only uses sin/cos of az, so
 *  the normalized return matches the old raw atan2 drawing exactly. */
export function altaz(raDeg, decDeg, jd, latDeg, lonDeg, refract = true, precess = true) {
  const [ra, dec] = precess ? precessJ2000ToDate(raDeg, decDeg, jd) : [raDeg, decDeg];
  let ha = (gmstDeg(jd) + lonDeg - ra) % 360;
  if (ha > 180) ha -= 360;
  const h = D2R * ha;
  const d = D2R * dec;
  const p = D2R * latDeg;

  const sinAlt = Math.sin(d) * Math.sin(p) + Math.cos(d) * Math.cos(p) * Math.cos(h);
  const alt = Math.asin(Math.max(-1, Math.min(1, sinAlt)));

  const east = -Math.cos(d) * Math.sin(h);
  const north = Math.sin(d) * Math.cos(p) - Math.cos(d) * Math.sin(p) * Math.cos(h);
  let az = (Math.atan2(east, north) * R2D) % 360;
  if (az < 0) az += 360;
  // CO-04 (0.6.32 review): keep the documented [0, 360) range. Two float64 edges
  // break it - a tiny negative value (e.g. -1e-15) becomes EXACTLY 360 when 360
  // is added, because the difference is below 360's ULP, and `-0 < 0` is false so
  // a -0 survives the branch above. `az === 0` catches both (-0 === 0).
  if (az >= 360 || az === 0) az = 0;

  if (refract) {
    const altApp = alt * R2D + refractionDeg(alt * R2D);
    return [altApp, az];
  }
  return [alt * R2D, az];
}

/* Batch fast path for the ~12k advanced-object synth: the sidereal time and
   site terms only depend on the time sample, so they are computed once per
   sample instead of once per object per sample. */
export function altazFrame(latDeg, lonDeg, jds) {
  const p = latDeg * D2R;
  const sinp = Math.sin(p), cosp = Math.cos(p);
  return jds.map(jd => {
    let lst = (gmstDeg(jd) + lonDeg) % 360;
    if (lst < 0) lst += 360;
    return { lst, sinp, cosp };
  });
}

export function altazFrameApply(frame, raDeg, decDeg, i) {
  const f = frame[i];
  let ha = (f.lst - raDeg) % 360;
  if (ha > 180) ha -= 360;
  const h = ha * D2R, d = decDeg * D2R;
  const sind = Math.sin(d), cosd = Math.cos(d);
  const sinAlt = sind * f.sinp + cosd * f.cosp * Math.cos(h);
  let alt = Math.asin(Math.max(-1, Math.min(1, sinAlt)));
  const east = -cosd * Math.sin(h);
  const north = sind * f.cosp - cosd * f.sinp * Math.cos(h);
  let az = Math.atan2(east, north) * R2D % 360;
  if (az < 0) az += 360;
  alt = alt * R2D + refractionDeg(alt * R2D);
  return [alt, az];
}

/* ---------------------------------------------------------------- sun/moon
 * Phase 2, concept 7: the solar/lunar series the web shell used to carry.
 * This module IS the implementation; the retired Python app/astro.py survives
 * only as the FROZEN "Class P" fixtures in tests/fixtures/core_fixtures.json. */

/** Geometric solar position -> RA/Dec in J2000 equivalents (Meeus ch.25). */
export function sunRaDec(jd) {
  const n = jd - J2000;
  const l = (280.460 + 0.9856474 * n) % 360;
  const g = D2R * ((357.528 + 0.9856003 * n) % 360);
  const lam = D2R * ((l + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) % 360);
  const eps = D2R * (23.439 - 0.0000004 * n);

  const x = Math.cos(lam);
  const y = Math.cos(eps) * Math.sin(lam);
  const z = Math.sin(eps) * Math.sin(lam);

  let ra = (Math.atan2(y, x) * R2D) % 360;
  if (ra < 0) ra += 360;
  const dec = Math.asin(z) * R2D;

  return precessDateToJ2000(ra, dec, jd);
}

/** Lunar RA/Dec (J2000-ish), truncated ELP/Meeus ch.47 (~0.3 deg). */
export function moonRaDec(jd) {
  const t = (jd - J2000) / 36525.0;

  const lp = D2R * ((218.316 + 481267.8813 * t) % 360);
  const dd = D2R * ((297.8502 + 445267.1115 * t) % 360);
  const m = D2R * ((357.5291 + 35999.0503 * t) % 360);
  const mp = D2R * ((134.9634 + 477198.8676 * t) % 360);
  const f = D2R * ((93.2721 + 483202.0175 * t) % 360);

  const lon = lp + D2R * (
    + 6.289 * Math.sin(mp)
    + 1.274 * Math.sin(2 * dd - mp)
    + 0.658 * Math.sin(2 * dd)
    + 0.214 * Math.sin(2 * mp)
    - 0.186 * Math.sin(m)
    - 0.114 * Math.sin(2 * f)
    + 0.059 * Math.sin(2 * dd - 2 * mp)
    + 0.057 * Math.sin(2 * dd - m - mp)
    + 0.053 * Math.sin(2 * dd + mp)
    - 0.046 * Math.sin(2 * dd - m)
    - 0.041 * Math.sin(m - mp)
    - 0.035 * Math.sin(dd)
    - 0.031 * Math.sin(m + mp)
  );
  const lat = D2R * (
    + 5.128 * Math.sin(f)
    + 0.281 * Math.sin(mp + f)
    - 0.278 * Math.sin(mp - f)
    + 0.173 * Math.sin(2 * dd - f)
    + 0.055 * Math.sin(2 * dd - mp + f)
    - 0.046 * Math.sin(2 * dd - mp - f)
    + 0.033 * Math.sin(2 * dd + f)
  );

  const eps = D2R * (23.4393 - 0.0130 * t);
  const sl = Math.sin(lon), cl = Math.cos(lon);
  const sb = Math.sin(lat), cb = Math.cos(lat);
  const x = cb * cl;
  const y = cb * sl * Math.cos(eps) - sb * Math.sin(eps);
  const z = cb * sl * Math.sin(eps) + sb * Math.cos(eps);

  let ra = (Math.atan2(y, x) * R2D) % 360;
  if (ra < 0) ra += 360;
  const dec = Math.asin(Math.max(-1, Math.min(1, z))) * R2D;

  return precessDateToJ2000(ra, dec, jd);
}

/** Fractional illuminated disc (0..1). */
export function moonIllumination(jd) {
  const [raS, decS] = sunRaDec(jd);
  const [raM, decM] = moonRaDec(jd);
  const ras = D2R * raS, decs = D2R * decS;
  const ram = D2R * raM, decm = D2R * decM;
  const cosElong = Math.sin(decs) * Math.sin(decm) + Math.cos(decs) * Math.cos(decm) * Math.cos(ras - ram);
  const psi = Math.acos(Math.max(-1, Math.min(1, cosElong)));
  const k = (1 - Math.cos(psi)) / 2;
  return Math.max(0, Math.min(1, k));
}

const SYNODIC_MONTH = 29.53058770576;
/** FOLDED moon age in days, 0..~14.77 (0 = new, 7.38 = full, then decreasing to
 *  the next new). Derived from the elongation MAGNITUDE, so it is NOT the true
 *  age since the last new moon: a waning last-quarter moon reads ~7.7, not ~22.8
 *  (CO-05, 0.6.32 review). Every consumer - the moonPhase labels, the moon-aware
 *  scoring curve and the Moon card - treats it as folded; only this docstring
 *  used to claim "age in days" and invite the wrong reading. */
export function moonAge(jd) {
  const [raS, decS] = sunRaDec(jd);
  const [raM, decM] = moonRaDec(jd);
  const ras = D2R * raS, decs = D2R * decS;
  const ram = D2R * raM, decm = D2R * decM;
  const cosElong = Math.sin(decs) * Math.sin(decm) + Math.cos(decs) * Math.cos(decm) * Math.cos(ras - ram);
  const psi = Math.acos(Math.max(-1, Math.min(1, cosElong)));
  return SYNODIC_MONTH * psi / (2 * Math.PI);
}

/* ------------------------------------------------------------------
 * night / planning helpers (scan-based like the Python original)
 * ------------------------------------------------------------------ */

/** Sun apparent (refracted) altitude in degrees. */
export function sunAlt(jd, lat, lon) {
  const [ra, dec] = sunRaDec(jd);
  return altaz(ra, dec, jd, lat, lon)[0];
}

/** Great-circle separation between two (alt, az) points in degrees. */
export function sepDeg(alt1Deg, az1Deg, alt2Deg, az2Deg) {
  const p1 = D2R * alt1Deg, p2 = D2R * alt2Deg;
  const dra = D2R * (az1Deg - az2Deg);
  return Math.acos(Math.max(-1, Math.min(1,
    Math.sin(p1) * Math.sin(p2) + Math.cos(p1) * Math.cos(p2) * Math.cos(dra)))) * R2D;
}
