/*
 * Sky-band ("paint visible sky") GEOMETRY checks.
 *
 * MODEL (0.6.13 follow-up): painting the visible sky band means "everything
 * ABOVE the painted band is free" (open above) BY DEFAULT. The band top is a
 * ceiling only with the explicit "block above band" opt-in (p.blockAbove).
 * The visible region is the exact complement of the blocked region; the
 * assumed-open area is hatched (not blocked). Also pins that the shared edge
 * geometry (profileSegments) yields IDENTICAL vertices in local core and the desktop adapter.
 * Prints one marker line; tests/test_js_behaviour.py asserts it.
 */
globalThis.window = { addEventListener() {}, removeEventListener() {}, dispatchEvent() {} };
globalThis.localStorage = {
  _m: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._m, k) ? this._m[k] : null; },
  setItem(k, v) { this._m[k] = String(v); },
};

const H = await import(new URL("../../core/horizon.js", import.meta.url).href);
const D = await import(new URL("../../static/js/pano.js", import.meta.url).href);

const BINS = 720;
let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };

const pano = (over = {}) => ({
  azOffset: 0, altBottom: 0, altTop: 90, fov: 360, paintMode: "sky",
  blockAbove: false,                       // default: open above
  profile: new Array(BINS).fill(90),
  profileTop: new Array(BINS).fill(-90),
  ...over,
});
const fill = (from, to, v) => {
  const a = new Array(BINS).fill(90);
  for (let i = from; i < to; i++) a[i] = v;
  return a;
};

/* ---- 1. complement invariant: vis U blocked tiles [ab, 90], vis n blocked = 0 */
function tileCheck(p, name) {
  const bands = H.visibleSkyBands(p, p.altBottom, 90);
  if (bands.length !== BINS) { fail(`${name}: bands length`); return; }
  for (let col = 0; col < BINS; col++) {
    const { vis, blocked } = bands[col];
    const iv = blocked.map((b) => [b[0], b[1]]);
    if (vis) iv.push([vis[0], vis[1]]);
    iv.sort((a, b) => a[0] - b[0]);
    let cur = p.altBottom;
    for (const [a, b] of iv) {
      if (Math.abs(a - cur) > 1e-9) { fail(`${name} col${col}: gap/overlap at ${a} != ${cur}`); return; }
      if (b < a) { fail(`${name} col${col}: inverted [${a},${b}]`); return; }
      cur = b;
    }
    if (Math.abs(cur - 90) > 1e-9) { fail(`${name} col${col}: does not reach zenith (${cur})`); return; }
  }
}

const fullyOpen = pano({ profile: new Array(BINS).fill(0),
                         profileTop: new Array(BINS).fill(90.5) });
const openAbove = pano({ profile: fill(0, BINS, 5),
                         profileTop: fill(0, BINS, 90.5) });
const bandOnly = pano({ profile: fill(0, BINS, 10),
                        profileTop: fill(0, BINS, 40) });      // default open above
const capped = pano({ profile: fill(0, BINS, 10),
                      profileTop: fill(0, BINS, 40), blockAbove: true });
const fullyBlocked = pano({ profile: new Array(BINS).fill(90),
                            profileTop: new Array(BINS).fill(-90) });
const mixed = pano({
  profile: fill(0, 200, 10).map((v, i) => (i >= 300 && i < 500 ? 5 : v)),
  profileTop: fill(0, 200, 40).map((v, i) => (i >= 300 && i < 500 ? 90.5 : v)),
});
for (const [p, name] of [[fullyOpen, "fullyOpen"], [openAbove, "openAbove"],
                         [bandOnly, "bandOnly"], [capped, "capped"],
                         [fullyBlocked, "fullyBlocked"], [mixed, "mixed"]]) {
  tileCheck(p, name);
}

/* ---- 2. absolute band anchors (open above is the default) ---- */
const visOf = (p, col) => H.visibleSkyBands(p, p.altBottom, 90)[col].vis;
const eq = (a, b) => a && b && Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9;
if (!eq(visOf(fullyOpen, 0), [0, 90])) fail("fullyOpen band != [0,90]");
if (!eq(visOf(openAbove, 100), [5, 90])) fail("openAbove (90.5 sentinel) != [5,90]");
if (!eq(visOf(bandOnly, 100), [10, 90])) fail("default band must be open above -> [10,90]");
if (!eq(visOf(capped, 100), [10, 40])) fail("block-above band must cap at the top -> [10,40]");
if (visOf(fullyBlocked, 100) !== null) fail("fullyBlocked must have no visible band");
if (!eq(visOf(mixed, 190), [10, 90])) fail("mixed band != [10,90] (open above)");
if (visOf(mixed, 250) !== null) fail("mixed blocked gap must be null");

/* ---- 3. polygons: count + vertices + no self-intersection ---- */
function selfIntersects(pts) {
  const n = pts.length;
  const seg = (a, b) => [pts[a], pts[b]];
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const proper = (p1, p2, p3, p4) => {
    const d1 = cross(p3, p4, p1), d2 = cross(p3, p4, p2);
    const d3 = cross(p1, p2, p3), d4 = cross(p1, p2, p4);
    return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
  };
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (i === j || (i + 1) % n === j || (j + 1) % n === i) continue;
      if (proper(...seg(i, (i + 1) % n), ...seg(j, (j + 1) % n))) return true;
    }
  }
  return false;
}
const polyOf = (p) => H.skyBandPolygons(p, 720, 56, Math.min(p.altBottom, 0),
                                        Math.max(p.altTop, 10), 0, 360);
const mixedPolys = polyOf(mixed);
if (mixedPolys.length !== 2) fail(`mixed should yield 2 polygons, got ${mixedPolys.length}`);
for (const poly of [polyOf(fullyOpen)[0], polyOf(openAbove)[0], polyOf(mixed)[0], mixedPolys[1]]) {
  if (!poly || !poly.length) { fail("missing polygon"); continue; }
  if (selfIntersects(poly)) fail("new skyBandPolygons self-intersects");
}
if (polyOf(fullyBlocked).length !== 0) fail("fullyBlocked must yield no visible polygon");
const openPoly = polyOf(fullyOpen)[0];
const ys = openPoly.map((p) => p[1]);
if (Math.min(...ys) > 1e-6 || Math.max(...ys) < 55.9) {
  fail(`fullyOpen should span the full preview height, got y ${Math.min(...ys)}..${Math.max(...ys)}`);
}

/* ---- 4. REGRESSION: reproduce the old malformation deterministically ----
 * The old drawProfile used x = i/2 over a 720-wide canvas (half-width squish)
 * and one single polygon for the whole profile, so a blocked gap was bridged by
 * a spurious fill. The new geometry is full-width and split per run. */
function naiveSkyPolygon(p, lo, hi) {
  const span = Math.max(1, hi - lo);
  const yOf = (alt) => 52 - ((alt - lo) / span) * 52;
  const pts = [];
  for (let i = 0; i <= BINS; i += 2) {
    const c = p.profile[Math.min(BINS - 1, i)];
    pts.push([i / 2, yOf(c > -89.5 ? c : 90)]);
  }
  for (let i = BINS; i >= 0; i -= 2) {
    const t = p.profileTop[Math.min(BINS - 1, i)];
    pts.push([i / 2, yOf(t > -89.5 ? t : 90)]);
  }
  return pts;
}
const naive = naiveSkyPolygon(mixed, 0, 90);
const naiveMaxX = Math.max(...naive.map((pt) => pt[0]));
if (naiveMaxX > 361) fail(`regression setup: old x-mapping should be half-width, got ${naiveMaxX}`);
const newPolys = polyOf(mixed);
const newMaxX = Math.max(...newPolys.flat().map((pt) => pt[0]));
if (newMaxX < 490 || newMaxX > 500) fail(`mixed visible run should end at x~499, got ${newMaxX}`);
if (newPolys.length !== 2) fail("the fixed sky band must split at the blocked gap");
if (newPolys.flat().some((pt) => pt[0] >= 200 && pt[0] < 300)) {
  fail("the blocked gap columns (200..299) must not be covered");
}
const foMaxX = Math.max(...polyOf(fullyOpen)[0].map((pt) => pt[0]));
if (foMaxX < 719) fail(`a fully open band must span the full width, got ${foMaxX}`);

/* ---- 5. desktop <-> web profileSegments parity (identical vertices) ---- */
const parCases = [
  { prof: Array.from({ length: 720 }, (_, i) => 5 + (i % 50)), ab: 0, at: 90, azOff: 0, fov: 360, skip: 90.5 },
  { prof: Array.from({ length: 720 }, (_, i) => (i >= 300 ? 90.5 : 20)), ab: -10, at: 60, azOff: 0, fov: 360, skip: 90.5 },
  { prof: Array.from({ length: 720 }, (_, i) => (i < 40 ? 70 : 10)), ab: 0, at: 90, azOff: 0, fov: 360, skip: 90.5 },
  { prof: Array.from({ length: 720 }, (_, i) => -90 + (i % 7)), ab: 0, at: 45, azOff: 90, fov: 180, skip: 90.5 },
];
for (const c of parCases) {
  const a = H.profileSegments(c.prof, 720, 56, c.ab, c.at, c.azOff, c.fov, c.skip);
  const b = D.profileSegments(c.prof, 720, 56, c.ab, c.at, c.azOff, c.fov, c.skip);
  if (JSON.stringify(a) !== JSON.stringify(b)) fail("local core/desktop profileSegments vertices differ");
}

/* ---- 6. DEFAULT open above: overhang runs + assumed-open runs ---- */
const overhangAll = pano({ profile: fill(0, BINS, 0), profileTop: fill(0, BINS, 40) });
const openAll = pano({ profile: fill(0, BINS, 0), profileTop: fill(0, BINS, 90.5) });
const halfOverhang = pano({ profile: fill(0, BINS, 0), profileTop: fill(0, 360, 40) });
const twoBands = pano({
  profile: new Array(BINS).fill(0),
  profileTop: fill(0, 180, 40).map((v, i) => (i >= 400 && i < 580 ? 40 : (i >= 180 ? 90.5 : v))),
});
// DEFAULT: no overhang at all - the region above the band is free
if (H.overhangRuns(overhangAll, 2).length !== 0) fail("default (open above) must have NO overhang");
if (H.overhangRuns(openAll, 2).length !== 0) fail("open-above (90.5) must have NO overhang");
if (H.overhangRuns(fullyBlocked, 2).length !== 0) fail("fully blocked has no overhang");
// opt-in: block above band -> overhang runs
const cappedAll = pano({ profile: fill(0, BINS, 0), profileTop: fill(0, BINS, 40), blockAbove: true });
if (H.overhangRuns(cappedAll, 2).length !== 1) fail("blockAbove band should be one overhang run");
const cappedHalf = pano({ profile: fill(0, BINS, 0), profileTop: fill(0, 360, 40), blockAbove: true });
const half = H.overhangRuns(cappedHalf, 2);
if (half.length !== 1 || half[0].a0 !== 0 || half[0].a1 !== 178) {
  fail(`cappedHalf run = ${JSON.stringify(half)}`);
}
const cappedTwo = pano({ blockAbove: true, profile: new Array(BINS).fill(0),
  profileTop: fill(0, 180, 40).map((v, i) => (i >= 400 && i < 580 ? 40 : (i >= 180 ? 90.5 : v))) });
const two = H.overhangRuns(cappedTwo, 2);
if (two.length !== 2 || two[0].a0 !== 0 || two[0].a1 !== 88
    || two[1].a0 !== 200 || two[1].a1 !== 288) {
  fail(`cappedTwo runs = ${JSON.stringify(two)}`);
}
// default: the assumed-open area above the band is the hatched region
if (H.assumedOpenRuns(overhangAll, 2).length !== 1) fail("assumedOpen should be one run");
if (H.assumedOpenRuns(cappedAll, 2).length !== 0) fail("block-above has no assumed-open run");
if (H.assumedOpenRuns(openAll, 2).length !== 0) fail("band painted to the top: no assumed-open run");
if (H.assumedOpenRuns(fullyBlocked, 2).length !== 0) fail("fully blocked: no assumed-open run");
if (H.bandTopFor(overhangAll, 180) !== 40) fail("bandTopFor should return the painted top");
if (H.bandTopFor(fullyBlocked, 180) !== null) fail("bandTopFor unpainted should be null");

/* ---- 7. preview hatch polygons are built in BIN space ----
 * Regression: the first web implementation plotted x = 2 * world azimuth, which
 * only coincides with the bin index at azOffset 0 / fov 360; a partial-FOV or
 * rotated calibration shifted/cropped the hatched area (probe: azOffset 90,
 * fov 180 hatched x 180..540 instead of 0..719). */
const hatchPart = pano({ azOffset: 90, fov: 180,
                         profile: fill(0, BINS, 5), profileTop: fill(0, BINS, 40) });
const hp = H.assumedOpenHatchPolys(hatchPart, 720, 56, 0, 90);
if (hp.length !== 1) {
  fail(`partial hatch should be one polygon, got ${hp.length}`);
} else {
  const xs = hp[0].map((q) => q[0]);
  if (Math.abs(Math.min(...xs)) > 1e-6 || Math.abs(Math.max(...xs) - 719) > 1e-6) {
    fail(`partial hatch x range ${Math.min(...xs)}..${Math.max(...xs)}, want 0..719`);
  }
}
const hatchShift = pano({ azOffset: 90, fov: 360,
                          profile: fill(0, BINS, 5), profileTop: fill(0, 360, 40) });
for (let i = 360; i < BINS; i++) hatchShift.profileTop[i] = 20;
const hs = H.assumedOpenHatchPolys(hatchShift, 720, 56, 0, 90);
if (hs.length !== 1) {
  fail(`rotated hatch should be one polygon, got ${hs.length}`);
} else {
  const poly = hs[0];
  if (Math.abs(poly[0][0]) > 1e-6) fail(`hatch col0 x=${poly[0][0]}`);
  if (Math.abs(poly[359][0] - 359) > 1e-6) fail(`hatch col359 x=${poly[359][0]}`);
  const y40 = 56 * (1 - 40 / 90), y20 = 56 * (1 - 20 / 90);
  if (Math.abs(poly[0][1] - y40) > 1e-6) fail(`hatch y at col0=${poly[0][1]}`);
  if (Math.abs(poly[500][1] - y20) > 1e-6) fail(`hatch y at col500=${poly[500][1]}`);
  if (poly[poly.length - 2][1] !== 0 || poly[poly.length - 1][1] !== 0) {
    fail("hatch must close through the chart top");
  }
}
if (H.assumedOpenHatchPolys(fullyBlocked, 720, 56, 0, 90).length !== 0) {
  fail("fully blocked pano must not hatch");
}
if (H.assumedOpenHatchPolys(capped, 720, 56, 0, 90).length !== 0) {
  fail("block-above pano must be shaded, not assumed-open");
}
const topBand = pano({ profile: fill(0, BINS, 5), profileTop: fill(0, BINS, 90.5) });
if (H.assumedOpenHatchPolys(topBand, 720, 56, 0, 90).length !== 0) {
  fail("band painted to the top must not hatch");
}
/* malformed records must degrade to [] (never throw) */
if (H.assumedOpenRuns({ paintMode: "sky" }, 2).length !== 0) fail("assumedOpenRuns malformed");
if (H.assumedOpenHatchPolys({ paintMode: "sky" }, 720, 56, 0, 90).length !== 0) {
  fail("assumedOpenHatchPolys malformed");
}
/* CO-04: the INVARIANT's own entry point must degrade like its siblings. It used
   to throw before any FOV/paint-mode logic (null record) and, in sky mode, on a
   record with a MISSING profileTop - while ceilFor/overhangRuns/
   assumedOpenHatchPolys all return an empty degrade. "No usable record" = nothing
   visible, so the caller draws nothing. */
if (H.visibleSkyBands(null, 0, 90).length !== 0) fail("visibleSkyBands(null) must degrade to []");
if (H.visibleSkyBands(undefined, 0, 90).length !== 0) fail("visibleSkyBands(undefined) must degrade");
if (H.visibleSkyBands({}, 0, 90).length !== 0) fail("visibleSkyBands({}) must degrade");
if (H.visibleSkyBands({ profile: "nope", paintMode: "sky" }, 0, 90).length !== 0) {
  fail("visibleSkyBands with a non-array profile must degrade");
}
/* a VALID profile with NO profileTop is still a full 720-column answer in sky
   mode (the old code threw on `undefined[col]`) */
const noTop = H.visibleSkyBands(
  { profile: fill(0, BINS, 20), paintMode: "sky", blockAbove: true }, 0, 90);
if (noTop.length !== BINS) fail("a missing profileTop must not change the column count");
if (noTop[0].vis === null) fail("a missing profileTop must cap nothing (open above)");
if (noTop[0].vis && noTop[0].vis[1] !== 90) {
  fail("a missing profileTop must leave the sky open to the zenith");
}

console.log(ok ? "sky-band contract held" : "sky-band contract FAILED");
process.exit(ok ? 0 : 1);
