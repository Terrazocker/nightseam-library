/*
 * Independent reference anchors (Phase C follow-up).
 *
 * These are the published-value checks that used to live in the Python suite
 * (tests/test_precession_reference.py + the published anchors of
 * tests/test_astro.py) before Phase C deleted the Python astronomy layer.
 * They assert the shared core against PUBLISHED models and values:
 *   - an IAU 2006 (Capitaine, Wallace & Chapront) precession transcription,
 *     forward AND inverse, tabulated in USNO Circular 179 / SOFA iauPmat06;
 *   - a direct transcription of Meeus ch. 7/12/21 textbook examples;
 *   - the published 2026-08-12 (new) / 2026-08-28 (full) moon phase instants;
 *   - an astropy/ERFA lunar position, and the illumination/age identities.
 * They are NOT compared against the core's own output and NOT against
 * tests/fixtures/core_fixtures.json: the fixtures are regression/maintenance
 * goldens (same implementation lineage - they catch drift, not a shared
 * misreading, which is exactly how the old X-axis precession bug survived
 * three engines). THIS harness is the correctness authority.
 *
 * Usage: node tests/js/reference_anchors.mjs [coreDir]
 *   coreDir defaults to ../../core; the mutation tests pass a tampered copy.
 * Prints one JSON object {checks:[{id,ok,detail}], failed:N}; exits 1 when any
 * check failed (the JSON is printed either way).
 */
import { pathToFileURL, fileURLToPath } from "node:url";
import { resolve } from "node:path";

const [, , coreDirArg] = process.argv;
const coreIndex = coreDirArg
  ? resolve(coreDirArg, "index.js")
  : resolve(fileURLToPath(new URL("../../core", import.meta.url)), "index.js");
const C = await import(pathToFileURL(coreIndex).href);

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const J2000 = 2451545.0;
// published mean synodic month (days) - NOT the core's constant, so the age
// identity below also pins the core's value against the published one
const SYNODIC_MONTH = 29.530588853;

const checks = [];

function check(id, fn) {
  try {
    fn();
    checks.push({ id, ok: true });
  } catch (e) {
    checks.push({ id, ok: false, detail: String((e && e.message) || e) });
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}
function near(a, b, tol, label) {
  assert(Math.abs(a - b) <= tol, `${label}: ${a} vs ${b} (tol ${tol})`);
}
function utc(y, mo, d, h = 0, mi = 0, s = 0) {
  return new Date(Date.UTC(y, mo - 1, d, h, mi, s));
}
const norm360 = (x) => ((x % 360) + 360) % 360;
function sepDeg(ra1, dec1, ra2, dec2) {
  const d1 = D2R * dec1, d2 = D2R * dec2;
  const c = Math.sin(d1) * Math.sin(d2)
    + Math.cos(d1) * Math.cos(d2) * Math.cos(D2R * (ra1 - ra2));
  return Math.acos(Math.max(-1, Math.min(1, c))) * R2D;
}

/* -------- independent Meeus ch. 21 transcription (desktop's model) -------- */

function meeusPrecess(raDeg, decDeg, jd) {
  const t = (jd - J2000) / 36525.0;
  const zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) / 3600.0;
  const z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) / 3600.0;
  const theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) / 3600.0;
  const a0 = D2R * raDeg, d0 = D2R * decDeg;
  const zr = D2R * zeta, th = D2R * theta;
  const A = Math.cos(d0) * Math.sin(a0 + zr);
  const B = Math.cos(th) * Math.cos(d0) * Math.cos(a0 + zr)
    - Math.sin(th) * Math.sin(d0);
  const Cc = Math.sin(th) * Math.cos(d0) * Math.cos(a0 + zr)
    + Math.cos(th) * Math.sin(d0);
  return [norm360(Math.atan2(A, B) * R2D + z), Math.asin(Cc) * R2D];
}

/* ---- independent IAU 2006 / Capitaine reference (USNO Circ. 179) --------- */

function iau2006Angles(jd) {
  const t = (jd - J2000) / 36525.0;
  const zeta = (2.650545 + 2306.083227 * t + 0.2988499 * t ** 2
    + 0.01801828 * t ** 3 - 5.971e-6 * t ** 4 - 3.173e-7 * t ** 5) / 3600.0;
  const z = (-2.650545 + 2306.077181 * t + 1.0927348 * t ** 2
    + 0.01826837 * t ** 3 - 2.8596e-5 * t ** 4 - 2.904e-7 * t ** 5) / 3600.0;
  const theta = (2004.191903 * t - 0.4294934 * t ** 2 - 0.04182264 * t ** 3
    - 7.089e-6 * t ** 4 - 1.274e-7 * t ** 5) / 3600.0;
  return [zeta, z, theta];
}

function iau2006Precess(raDeg, decDeg, jd, reverse = false) {
  let [zeta, z, theta] = iau2006Angles(jd);
  if (reverse) [zeta, z, theta] = [-z, -zeta, -theta];   // engine's swap rule
  const a0 = D2R * raDeg, d0 = D2R * decDeg;
  const zr = D2R * zeta, th = D2R * theta;
  const ca = Math.cos(d0) * Math.sin(a0 + zr);
  const cb = Math.cos(th) * Math.cos(d0) * Math.cos(a0 + zr)
    - Math.sin(th) * Math.sin(d0);
  const cc = Math.sin(th) * Math.cos(d0) * Math.cos(a0 + zr)
    + Math.cos(th) * Math.sin(d0);
  return [norm360(Math.atan2(ca, cb) * R2D + z), Math.asin(cc) * R2D];
}

// the two models differ by < 0.5 arcsec over 1900-2100; the old X-axis bug
// was ~1440 arcsec. 2 arcsec cleanly separates the two regimes.
const TOL_IAU = 2.0 / 3600.0;

const IAU_EPOCHS = [1900, 1950, 2000, 2026, 2100];
const IAU_POINTS = [
  [0.0, 0.0],               // the vernal equinox: cleanest regression probe
  [41.049942, 49.228467],   // theta Persei, Meeus' own worked example
  [250.42, 36.46],          // M13
  [210.80, -45.50],         // far south
  [15.0, 89.0],             // near the pole (Polaris' neighbourhood)
  [359.5, -70.0],           // RA wraparound
];

/* ---- explicit rotation-matrix product: the ORDER authority (CO-06) --------
 * Meeus ch.21 eq.21.4 is a product of THREE rotations. Both authorities above
 * evaluate the same closed form the core does (A/B/C + atan2), so neither can
 * see a REORDERED product: the Meeus one is a re-derivation of the core's own
 * expression, and the 2 arcsec IAU tolerance ADMITS a reorder at the epoch the
 * app actually runs ("middle rotation last" is 1.5 arcsec at J2026 - the same
 * error only exceeds the tolerance at the far epochs, where it is ~20 arcsec).
 * Below, the three factors are literal matrices in the active
 * (rotate-the-vector) convention, transcribed from Meeus' zeta/theta/z
 * rotations with his sign convention, so the factor order is DATA rather than a
 * consequence of the core's algebra:
 *
 *     v(date) = Rz(z) . Ry(-theta) . Rz(zeta) . v(J2000)
 *
 *   Rz(p) = [[cos p, -sin p, 0], [sin p, cos p, 0], [0, 0, 1]]
 *   Ry(p) = [[cos p, 0, sin p], [0, 1, 0], [-sin p, 0, cos p]]
 *   Rx(p) = [[1, 0, 0], [0, cos p, -sin p], [0, sin p, cos p]]
 *
 * (The same product in the passive convention is SOFA iauPmat06's
 * P = R3(-z) R2(theta) R3(-zeta).) The COEFFICIENTS are deliberately Meeus'
 * own, i.e. the SAME model as the core - `iau2006_forward` is what pins them
 * against the outside world. `precession_rotation_order` pins the ORDER, and
 * the `order:` variants exist only to prove that pin has teeth.
 */
function meeusAnglesDeg(jd) {
  const t = (jd - J2000) / 36525.0;
  return [
    (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) / 3600.0,   // zeta
    (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) / 3600.0,   // z
    (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) / 3600.0,   // theta
  ];
}
const rotZ = (rad) => { const c = Math.cos(rad), s = Math.sin(rad);
  return [[c, -s, 0], [s, c, 0], [0, 0, 1]]; };
const rotY = (rad) => { const c = Math.cos(rad), s = Math.sin(rad);
  return [[c, 0, s], [0, 1, 0], [-s, 0, c]]; };
const rotX = (rad) => { const c = Math.cos(rad), s = Math.sin(rad);
  return [[1, 0, 0], [0, c, -s], [0, s, c]]; };
function matMul(...ms) {
  return ms.reduce((P, Q) => P.map((row) =>
    Q[0].map((_, j) => row.reduce((s, x, k) => s + x * Q[k][j], 0))));
}
const matVec = (P, v) => P.map((row) => row.reduce((s, x, k) => s + x * v[k], 0));

function matrixPrecess(raDeg, decDeg, jd, order = "correct") {
  const [zeta, z, theta] = meeusAnglesDeg(jd);
  const a = D2R * raDeg, d = D2R * decDeg;
  const v = [Math.cos(d) * Math.cos(a), Math.cos(d) * Math.sin(a), Math.sin(d)];
  const rz = D2R * zeta, th = D2R * theta, zz = D2R * z;
  let P;
  if (order === "correct") P = matMul(rotZ(zz), rotY(-th), rotZ(rz));
  else if (order === "x-middle") P = matMul(rotZ(zz), rotX(-th), rotZ(rz));
  else if (order === "y-last") P = matMul(rotZ(zz), rotZ(rz), rotY(-th));
  else if (order === "zeta-z-swap") P = matMul(rotZ(rz), rotY(-th), rotZ(zz));
  else throw new Error(`unknown order ${order}`);
  const w = matVec(P, v);
  return [norm360(Math.atan2(w[1], w[0]) * R2D),
          Math.asin(Math.max(-1, Math.min(1, w[2]))) * R2D];
}

/** Chord-exact angular separation in ARCSEC. `sepDeg` uses acos, whose floor of
 *  ~4e-3 arcsec is far above the floating-point-scale differences this pin
 *  works with, so it cannot resolve them. */
function sepArcsec(ra1, dec1, ra2, dec2) {
  const v = (ra, dec) => {
    const d = D2R * dec;
    return [Math.cos(d) * Math.cos(D2R * ra), Math.cos(d) * Math.sin(D2R * ra),
            Math.sin(d)];
  };
  const p = v(ra1, dec1), q = v(ra2, dec2);
  const chord = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
  return 2 * Math.asin(Math.min(1, chord / 2)) * R2D * 3600;
}

// The explicit product and the core's closed form are the SAME expression, so
// they can differ only by floating-point rounding (measured <= 1.6e-10 arcsec
// over 1900-2200). A reordered or wrong-axis product is 1.4e-4 arcsec for the
// zeta<->z swap at J2026 and up to 5666 arcsec for a middle axis X at J2200.
// 1e-6 arcsec sits ~6000x above the rounding floor and ~135x BELOW the smallest
// real order error - so it bites without ever producing a false positive.
const TOL_ORDER_ARCSEC = 1e-6;
const ROT_EPOCHS = [1900, 1950, 2026, 2100, 2200];

/* ------------------------------- checks ---------------------------------- */

check("meeus_jd_examples", () => {
  // Meeus, "Astronomical Algorithms", ch. 7 examples
  near(C.julianDay(utc(2000, 1, 1, 12)), 2451545.0, 1e-9, "J2000");
  near(C.julianDay(utc(1987, 1, 27, 0)), 2446822.5, 1e-9, "1987-01-27");
  near(C.julianDay(utc(2025, 1, 1, 0)), 2460676.5, 1e-9, "2025-01-01");
});

check("gmst_j2000", () => {
  // Meeus ch. 12: GMST at J2000.0 = 280.46061837 deg
  const jd = C.julianDay(utc(2000, 1, 1, 12));
  near(C.gmstDeg(jd) % 360, 280.46061837, 1e-4, "GMST(J2000)");
});

check("precession_round_trip", () => {
  const jd = C.julianDay(utc(2026, 8, 22, 22));
  for (const [ra, dec] of [[83.6, 22.0], [210.8, 54.35], [359.9, -45.5],
                           [10.1, 11.6]]) {
    const [r2, d2] = C.precessJ2000ToDate(ra, dec, jd);
    const [r3, d3] = C.precessDateToJ2000(r2, d2, jd);
    const dra = ((r3 - ra + 180) % 360) - 180;
    near(dra, 0, 1e-6, `round-trip RA ${ra}`);
    near(d3, dec, 1e-6, `round-trip Dec ${ra}`);
  }
});

check("meeus_precession_transcription", () => {
  const points = [
    [0.0, 0.0], [41.049942, 49.228467], [83.63, 22.01], [250.42, 36.46],
    [210.80, -45.50], [15.0, 89.0], [359.5, -70.0], [120.0, 0.0],
  ];
  for (const year of [1900, 1950, 2000, 2026, 2100, 2200]) {
    const jd = C.julianDay(utc(year, 1, 1, 12));
    for (const [ra, dec] of points) {
      const [gotRa, gotDec] = C.precessJ2000ToDate(ra, dec, jd);
      const [wantRa, wantDec] = meeusPrecess(ra, dec, jd);
      const sep = sepDeg(gotRa, gotDec, wantRa, wantDec);
      assert(sep < 1e-5, `${year} (${ra}, ${dec}): ${sep} deg off ch.21`);
    }
  }
});

check("precession_equinox_rate", () => {
  // independent of any implementation: m = 3.07496 s/yr RA, n = 20.0431"/yr
  const jd = C.julianDay(utc(2026, 8, 22, 22));
  const tYears = (jd - J2000) / 365.25;
  const [ra, dec] = C.precessJ2000ToDate(0.0, 0.0, jd);
  near(ra, (3.07496 / 3600.0 / 24.0 * 360.0) * tYears, 0.002, "equinox RA");
  near(dec, (20.0431 / 3600.0) * tYears, 0.002, "equinox Dec");
  assert(ra > 0.3 && dec > 0.1, `equinox barely moved: ra=${ra} dec=${dec}`);
});

check("iau2006_forward", () => {
  for (const year of IAU_EPOCHS) {
    const jd = C.julianDay(utc(year, 1, 1, 12));
    for (const [ra, dec] of IAU_POINTS) {
      const [gotRa, gotDec] = C.precessJ2000ToDate(ra, dec, jd);
      const [wantRa, wantDec] = iau2006Precess(ra, dec, jd);
      const sep = sepDeg(gotRa, gotDec, wantRa, wantDec);
      assert(sep < TOL_IAU,
        `${year} (${ra}, ${dec}): ${(sep * 3600).toFixed(2)} arcsec from IAU 2006`);
    }
  }
});

check("iau2006_inverse", () => {
  for (const year of IAU_EPOCHS) {
    const jd = C.julianDay(utc(year, 6, 15, 3));
    for (const [ra, dec] of IAU_POINTS) {
      const [ofDateRa, ofDateDec] = iau2006Precess(ra, dec, jd);
      const [gotRa, gotDec] = C.precessDateToJ2000(ofDateRa, ofDateDec, jd);
      const sep = sepDeg(gotRa, gotDec, ra, dec);
      assert(sep < TOL_IAU, `${year} (${ra}, ${dec}): inverse ${(sep * 3600).toFixed(2)} arcsec`);
    }
  }
});

check("iau2006_model_differs", () => {
  // the comparison must be meaningful: a DIFFERENT, newer model, yet far
  // inside the old bug's size
  const jd = C.julianDay(utc(2026, 1, 1, 12));
  const [iauRa, iauDec] = iau2006Precess(0.0, 0.0, jd);
  const [meeusRa, meeusDec] = C.precessJ2000ToDate(0.0, 0.0, jd);
  const sepArcsec = sepDeg(iauRa, iauDec, meeusRa, meeusDec) * 3600;
  assert(sepArcsec > 0 && sepArcsec < 2.0,
    `IAU 2006 vs Meeus sep ${sepArcsec} arcsec outside (0, 2)`);
});

check("old_x_axis_bug_guard", () => {
  // pre-0.6.9 rotation: middle rotation about X and flipped node sign. Kept as
  // the documented regression size; it must be far outside TOL_IAU.
  const buggy = (raDeg, decDeg, jd) => {
    const t = (jd - J2000) / 36525.0;
    const xi = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) / 3600.0;
    const ze = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) / 3600.0;
    const th = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) / 3600.0;
    const a = D2R * raDeg, d = D2R * decDeg;
    const x = Math.cos(d) * Math.cos(a), y = Math.cos(d) * Math.sin(a);
    const z = Math.sin(d);
    const cx = Math.cos(D2R * xi), sx = Math.sin(D2R * xi);
    const cz = Math.cos(D2R * ze), sz = Math.sin(D2R * ze);
    const ct = Math.cos(D2R * th), st = Math.sin(D2R * th);
    const x2 = cx * x - sx * y, y2 = sx * x + cx * y;
    const y3 = ct * y2 - st * z, z3 = st * y2 + ct * z;
    const xf = cz * x2 + sz * y3, yf = -sz * x2 + cz * y3;
    return [norm360(Math.atan2(yf, xf) * R2D),
            Math.asin(Math.max(-1, Math.min(1, z3))) * R2D];
  };
  const jd = C.julianDay(utc(2026, 1, 1, 12));
  const [bRa, bDec] = buggy(83.63, 22.01, jd);
  const [wRa, wDec] = iau2006Precess(83.63, 22.01, jd);
  const sep = sepDeg(bRa, bDec, wRa, wDec);
  assert(sep > 100 * TOL_IAU,
    `the old bug was only ${(sep * 3600).toFixed(1)} arcsec`);
});

check("precession_rotation_order", () => {
  // CO-06 (0.6.32 review): the ROTATION ORDER and the MIDDLE AXIS, pinned
  // against the explicit product of literal rotation matrices above - never
  // against the core's own atan2(A, B) + z expression. TOL_ORDER_ARCSEC is
  // tight because the two are algebraically identical; it is the ORDER that is
  // being asserted, so no model-difference budget is needed.
  for (const year of ROT_EPOCHS) {
    const jd = C.julianDay(utc(year, 1, 1, 12));
    for (const [ra, dec] of IAU_POINTS) {
      const [gotRa, gotDec] = C.precessJ2000ToDate(ra, dec, jd);
      const [wantRa, wantDec] = matrixPrecess(ra, dec, jd);
      const sep = sepArcsec(gotRa, gotDec, wantRa, wantDec);
      assert(sep < TOL_ORDER_ARCSEC,
        `${year} (${ra}, ${dec}): ${sep} arcsec from the explicit ` +
        "Rz(z).Ry(-theta).Rz(zeta) product (tol " + TOL_ORDER_ARCSEC + ")");
    }
  }
});

check("precession_rotation_order_matters", () => {
  // A property of the TRANSCRIBED matrices alone (the core comparison is the
  // check above), so this stays a statement about the authority's resolving
  // power: at J2026 the three wrong products differ from the correct one by the
  // sizes below. A middle axis X (the pre-0.6.9 bug) is huge, but the zeta<->z
  // factor swap is 1.35e-4 arcsec - five orders of magnitude BELOW every other
  // tolerance in this file - which is exactly why TOL_ORDER_ARCSEC must be this
  // tight and why the order needed its own authority instead of being inferred
  // from the loose ones.
  const jd = C.julianDay(utc(2026, 1, 1, 12));
  const [ra, dec] = [83.63, 22.01];
  const [okRa, okDec] = matrixPrecess(ra, dec, jd);
  const sepOf = (order) =>
    sepArcsec(okRa, okDec, ...matrixPrecess(ra, dec, jd, order));
  const xMiddle = sepOf("x-middle"), yLast = sepOf("y-last");
  const swap = sepOf("zeta-z-swap");
  assert(xMiddle > 100, `middle axis X moved the answer only ${xMiddle} arcsec`);
  assert(yLast > 1 && yLast < TOL_IAU * 3600,
    `middle rotation last moved the answer ${yLast} arcsec (expected 1..2)`);
  assert(swap > TOL_ORDER_ARCSEC * 10 && swap < 0.01,
    `zeta<->z swap moved the answer ${swap} arcsec (expected 1e-4..1e-2)`);
});

check("transit_altitude_uses_precessed_declination", () => {
  // M13 culminates at 90 - |lat - dec_of_date|; using the J2000 dec as if it
  // were of date is the exact symptom of the precession bug.
  const lat = 52.52, lon = 13.40;
  const ra = 16.695 * 15, decJ2000 = 36.467;
  const jdRef = C.julianDay(utc(2026, 8, 22, 22));
  const [, decOfDate] = meeusPrecess(ra, decJ2000, jdRef);
  let best = -99;
  for (let m = 0; m < 24 * 60; m += 2) {
    const jd = C.julianDay(new Date(Date.UTC(2026, 7, 22, 0, m)));
    const [alt] = C.altaz(ra, decJ2000, jd, lat, lon);
    if (alt > best) best = alt;
  }
  near(best, 90 - Math.abs(lat - decOfDate), 0.05, "M13 max altitude");
});

check("polaris_tracks_latitude", () => {
  const lat = 52.52, lon = 13.40;
  for (const hour of [0, 6, 12, 18]) {
    const jd = C.julianDay(utc(2026, 8, 22, hour));
    const [alt] = C.altaz(2.530 * 15, 89.264, jd, lat, lon);
    near(alt, lat, 0.8, `Polaris at ${hour}h`);
  }
});

check("m13_transit_berlin", () => {
  const lat = 52.52, lon = 13.40;
  const ra = 16.695 * 15, decJ2000 = 36.467;
  const jdRef = C.julianDay(utc(2026, 8, 22, 22));
  const [, dec] = meeusPrecess(ra, decJ2000, jdRef);
  let bestAlt = -99, bestAz = null;
  for (let m = 0; m < 24 * 60; m += 5) {
    const jd = C.julianDay(new Date(Date.UTC(2026, 7, 22, 0, m)));
    const [a, az] = C.altaz(ra, decJ2000, jd, lat, lon);
    if (a > bestAlt) { bestAlt = a; bestAz = az; }
  }
  near(bestAlt, 90 - Math.abs(lat - dec), 0.1, "M13 transit alt");
  near(bestAz, 180, 1.5, "M13 transit az");
});

check("sun_position_august_2026", () => {
  const jd = C.julianDay(utc(2026, 8, 22, 12));
  const [ra, dec] = C.sunRaDec(jd);
  const [raOfDate, decOfDate] = meeusPrecess(ra, dec, jd);
  near(ra / 15, 10.1, 0.05, "sun RA (J2000 frame)");
  near(dec, 11.80, 0.15, "sun Dec (J2000 frame)");
  near(raOfDate / 15, 10.1, 0.05, "sun RA (of date)");
  near(decOfDate, 11.60, 0.15, "sun Dec (of date)");
});

check("sun_noon_altitude_berlin", () => {
  const lat = 52.52, lon = 13.40;
  const jd = C.julianDay(utc(2026, 8, 22, 10, 30));   // near solar noon UTC
  const [ra, dec] = C.sunRaDec(jd);
  const [alt] = C.altaz(ra, dec, jd, lat, lon);
  near(alt, 90 - lat + dec, 1.0, "sun noon altitude");
});

check("refraction_magnitude", () => {
  for (const h of [5, 10, 20, 45]) {
    const r = C.refractionDeg(h);
    const expected = 1.02 / Math.tan(D2R * (h + 10.3 / (h + 5.11))) / 60;
    assert(Math.abs(r - expected) <= 0.01 * Math.abs(expected),
      `refraction(${h}) = ${r}, Bennett ${expected}`);
    assert(r > 0 && r < 0.6, `refraction(${h}) = ${r} outside (0, 0.6)`);
  }
});

check("moon_illumination_bounds", () => {
  for (let day = 1; day <= 28; day++) {
    const jd = C.julianDay(utc(2026, 8, day, 23));
    const k = C.moonIllumination(jd);
    assert(k >= 0 && k <= 1, `illumination ${k} on Aug ${day}`);
  }
});

check("moon_published_phases", () => {
  // published 2026 phases (moongiant/lunaf/starwalk/space.com agree):
  // new moon 2026-08-12 17:37 UT, full moon 2026-08-28 04:18 UT
  const jdNew = C.julianDay(utc(2026, 8, 12, 17, 37));
  near(C.moonIllumination(jdNew), 0.0, 0.002, "new moon illumination");
  near(C.moonAge(jdNew), 0.0, 0.15, "new moon age");
  const jdFull = C.julianDay(utc(2026, 8, 28, 4, 18));
  near(C.moonIllumination(jdFull), 1.0, 0.002, "full moon illumination");
  near(C.moonAge(jdFull), SYNODIC_MONTH / 2, 0.15, "full moon age");
});

check("moon_illumination_matches_elongation", () => {
  // psi = sun-moon elongation; illum = (1-cos psi)/2, age = S*psi/2pi. The
  // age tolerance 2e-6 d also pins the core's synodic constant against the
  // published value 29.530588853 (difference is ~5.7e-7 d).
  const jd = C.julianDay(utc(2026, 8, 22, 23));
  const [raS, decS] = C.sunRaDec(jd);
  const [raM, decM] = C.moonRaDec(jd);
  const psi = sepDeg(raS, decS, raM, decM);
  near(C.moonIllumination(jd), (1 - Math.cos(D2R * psi)) / 2, 1e-9, "illum(psi)");
  near(C.moonAge(jd), SYNODIC_MONTH * psi / 360.0, 2e-6, "age(psi)");
});

check("moon_erfa_position", () => {
  // astropy/ERFA reference for 2026-08-22 23:00 UT (truncated ELP ~0.3 deg)
  const jd = C.julianDay(utc(2026, 8, 22, 23));
  const [ra, dec] = C.moonRaDec(jd);
  near(ra / 15, 18.047, 0.05, "moon RA");
  near(dec, -27.958, 0.6, "moon Dec");
});

const failed = checks.filter((c) => !c.ok).length;
process.stdout.write(JSON.stringify({ checks, failed }));
process.exit(failed ? 1 : 0);
