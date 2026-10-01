/*
 * Web panorama-editor GEOMETRY checks (headless browsers cannot run here).
 * Drives the real web/horizon.js pure helpers - the SAME transform used by the
 * calibration grid, the profile and the new guides - so a guide that looks
 * centred cannot be mathematically offset.
 *
 * Covers: row<->altitude inverse, azimuth->x mapping (full + partial FOV +
 * azOffset wrap, FOV-relative like ceilFor), obstacle/sky profile derivation
 * (incl. the sky-mode "open above" 90.5 sentinel), the 360 deg seam wrap, and a
 * save/load round trip. Prints one marker line; tests/test_js_behaviour.py
 * asserts it.
 */
globalThis.localStorage = {
  _m: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._m, k) ? this._m[k] : null; },
  setItem(k, v) { this._m[k] = String(v); },
};

const H = await import(new URL("../../web/horizon.js", import.meta.url).href);

let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };
const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

const base = {
  azOffset: 0, altBottom: 0, altTop: 60, fov: 360, paintMode: "obstacle",
  strokes: [], profile: [], profileTop: [],
};

/* 1. altOf / rowOfAlt are exact inverses for several calibrations */
for (const [ab, at] of [[0, 60], [-10, 30], [0, 90], [5, 45]]) {
  const p = { ...base, altBottom: ab, altTop: at };
  for (const alt of [ab, (ab + at) / 2, at]) {
    const row = H.rowOfAlt(p, alt);
    if (!near(H.altOf(p, row), alt)) fail(`rowOfAlt/altOf inverse ${ab},${at},${alt}`);
  }
}
const pv = { ...base, altBottom: 0, altTop: 60 };
if (!near(H.rowOfAlt(pv, 60), 0) || !near(H.rowOfAlt(pv, 0), 359)) {
  fail(`rowOfAlt endpoints: top=${H.rowOfAlt(pv, 60)} bottom=${H.rowOfAlt(pv, 0)}`);
}

/* 2. azimuth -> x, FOV-relative (same mapping as ceilFor) */
const xa = H.xOfAz;
for (const [az, x] of [[0, 0], [90, 180], [180, 360], [270, 540]]) {
  if (!near(xa({ ...base, fov: 360, azOffset: 0 }, az), x)) fail(`xOfAz 360 az${az}`);
}
const part = { ...base, fov: 180, azOffset: 90 };
for (const [az, x] of [[90, 0], [180, 360], [270, 720]]) {
  if (!near(xa(part, az), x)) fail(`xOfAz partial az${az} -> ${xa(part, az)}`);
}
if (!(xa(part, 0) > 720)) fail(`xOfAz outside FOV should exceed width, got ${xa(part, 0)}`);
const wrap = { ...base, fov: 360, azOffset: 350 };
if (!near(xa(wrap, 350), 0) || !near(xa(wrap, 80), 180)) fail("xOfAz azOffset wrap");

/* 2b. azOfX is the inverse of xOfAz (used by the cursor readout) */
for (const [fov, azOff] of [[360, 0], [180, 90], [120, 350]]) {
  const pp = { ...base, fov, azOffset: azOff };
  for (const az of [0, 45, 180, 359]) {
    const x = xa(pp, az);
    if (x < 0 || x > 720) continue;
    const back = H.azOfX(pp, x);
    if (!near(back, az, 1e-6)) fail(`azOfX inverse ${fov},${azOff},${az} -> ${back}`);
  }
}

/* 3. ceilFor uses the same bin mapping (so guides and ceiling agree) */
const prof = new Array(720).fill(-90);
prof[0] = 10;
prof[360] = 20;
const rec = { paintMode: "obstacle", profile: prof, profileTop: null, fov: 360, azOffset: 0 };
if (H.ceilFor(rec, 0).ceil !== 10 || H.ceilFor(rec, 180).ceil !== 20) fail("ceilFor bin map");

/* 4. obstacle profile: one dab at x=100 y=50 r=10 */
const pz = { ...base, strokes: [{ m: "b", x: 100, y: 50, r: 10 }] };
H.computeProfile(pz);
const wantTop = H.altOf(pz, 40);
if (Math.abs(pz.profile[100] - wantTop) > 0.05) {
  fail(`obstacle prof[100]=${pz.profile[100]} want ${wantTop}`);
}
if (pz.profile[0] !== H.NO_BLOCK) fail(`unpainted obstacle col0=${pz.profile[0]}`);

/* 5. sky mode: band painted to the top -> 90.5 "open above"; unpainted -> 90 */
const ps = {
  ...base, paintMode: "sky",
  strokes: [{ m: "b", x: 360, y: 50, r: 200 }],
};
H.computeProfile(ps);
if (ps.profileTop[360] !== 90.5) fail(`sky open-above sentinel=${ps.profileTop[360]}`);
if (Math.abs(ps.profile[360] - H.altOf(ps, 250)) > 0.05) {
  fail(`sky band bottom=${ps.profile[360]} want ${H.altOf(ps, 250)}`);
}
if (ps.profile[719] !== 90) fail(`unpainted sky col=${ps.profile[719]}`);
// a band that stops well below the top keeps a real ceiling
const psLow = { ...base, paintMode: "sky", strokes: [{ m: "b", x: 360, y: 200, r: 20 }] };
H.computeProfile(psLow);
if (psLow.profileTop[360] === 90.5) fail("sky band below top must keep a numeric ceiling");

/* 6. 360 deg seam wrap - BOTH directions */
const leftStroke = [{ m: "b", x: 2, y: 180, r: 10 }];
const wrapped = H.rebuildMask(leftStroke, true);
const plain = H.rebuildMask(leftStroke, false);
if (!wrapped[180 * 720 + (720 - 8)]) fail("wrap did not paint the right edge (left stroke)");
if (plain[180 * 720 + (720 - 8)]) fail("no-wrap painted the right edge (left stroke)");
if (!wrapped[180 * 720 + 2] || !plain[180 * 720 + 2]) fail("left stroke centre missing");
const rightStroke = [{ m: "b", x: 718, y: 180, r: 10 }];
const wrappedR = H.rebuildMask(rightStroke, true);
const plainR = H.rebuildMask(rightStroke, false);
if (!wrappedR[180 * 720 + 2]) fail("wrap did not paint the left edge (right stroke)");
if (plainR[180 * 720 + 2]) fail("no-wrap painted the left edge (right stroke)");
// the two physical directions must give the same seam coverage
const a = H.rebuildMask([{ m: "b", x: 5, y: 180, r: 12 }], true);
const b = H.rebuildMask([{ m: "b", x: 715, y: 180, r: 12 }], true);
if (!a[180 * 720 + 716] || !b[180 * 720 + 4]) fail("seam coverage differs by direction");
// profile-level: a stroke at the seam must block BOTH edge columns (continuous)
const seamP = { ...base, paintMode: "obstacle", strokes: [{ m: "b", x: 1, y: 180, r: 12 }] };
H.computeProfile(seamP);
if (seamP.profile[0] === H.NO_BLOCK || seamP.profile[719] === H.NO_BLOCK) {
  fail(`seam profile not continuous: col0=${seamP.profile[0]} col719=${seamP.profile[719]}`);
}

/* 6b. sky mode: OPEN ABOVE is the default; the band top is a ceiling only with
   the explicit "block above band" opt-in. */
const openSky = { ...base, paintMode: "sky", strokes: [{ m: "b", x: 360, y: 50, r: 200 }] };
H.computeProfile(openSky);
const cfOpen = H.ceilFor(openSky, 180);            // az 180 -> col ~360
if (cfOpen.ceilTop !== null) fail(`default open-above ceilTop=${cfOpen.ceilTop}, want null`);
if (cfOpen.ceil === H.NO_BLOCK) fail("a painted band must still set the lower ceiling");
const blockSky = { ...openSky, blockAbove: true };
H.computeProfile(blockSky);
const cfBlock = H.ceilFor(blockSky, 180);
if (cfBlock.ceilTop == null) fail("block-above band must expose its top as a ceiling");
const realCeil = { ...base, paintMode: "sky", blockAbove: true,
                   strokes: [{ m: "b", x: 360, y: 200, r: 20 }] };
H.computeProfile(realCeil);
const cfReal = H.ceilFor(realCeil, 180);
if (cfReal.ceilTop === 90.5 || cfReal.ceilTop == null) {
  fail("band below the frame top must keep a numeric ceiling");
}
if (55 < cfReal.ceilTop) fail("a real ceiling must block alt above it");

/* 7. save/load round trip */
const np = H.newPano("geom");
np.strokes = [{ m: "b", x: 10, y: 100, r: 12 }];
H.computeProfile(np);
const snap = H.exportPanos();
if (!H.importPanos(snap)) fail("importPanos returned false");
const back = H.activePano();
if (!back || back.profile.length !== 720 || back.profileTop.length !== 720) fail("round-trip profile shape");
if (!back || back.strokes.length !== 1) fail("round-trip strokes");
if (!back || back.fov !== 360) fail("round-trip calibration");

/* 8. kept-band crop: the ONE shared formula (both shells) + non-destructive
   transforms. The retired desktop formula fovOld*(1-w/W) computed the REMOVED
   fraction and only matched at exactly half width - guarded below. */
const CB = H.cropKeptBand, CW = 1000;
const cbCases = [
  // [fovOld, azOffOld, sel, wantFov, wantAz]
  [360, 0, { x: 0, w: 1000 }, 360, 0],       // select all keeps the full frame
  [360, 0, { x: 250, w: 500 }, 180, 90],     // center 50%
  [360, 0, { x: 0, w: 500 }, 180, 0],        // left 50%
  [360, 0, { x: 100, w: 800 }, 288, 36],     // center 80%
  [360, 0, { x: 400, w: 200 }, 72, 144],     // center 20%
  [360, 0, { x: 700, w: 300 }, 108, 252],    // right 30%
  [360, 350, { x: 200, w: 400 }, 144, 62],   // mod-360 wrap
  [180, 90, { x: 100, w: 800 }, 144, 108],   // partial FOV + azOffset
];
for (const [f0, a0, sel, wf, wa] of cbCases) {
  const r = CB(f0, a0, sel, CW);
  if (r.fov !== wf || !near(r.azOffset, wa, 1e-9)) {
    fail(`cropKeptBand(${f0},${a0},${JSON.stringify(sel)}) -> ${r.fov}/${r.azOffset}, want ${wf}/${wa}`);
  }
}
if (CB(360, 0, { x: 400, w: 50 }, CW).fov !== H.CROP_MIN_FOV) fail("cropKeptBand floor != 45");
if (CB(180, 0, { x: 0, w: 333 }, CW).fov !== 60) fail(`cropKeptBand rounding -> ${CB(180, 0, { x: 0, w: 333 }, CW).fov}`);
if (!near(CB(360, 0, { x: 1, w: 999 }, CW).azOffset, 0.36)) fail("cropKeptBand azimuth must be 2 dp");
if (CB(360, 0, { x: 100, w: 800 }, CW).fov === 72) fail("cropKeptBand regressed to the removed-fraction formula");

/* 8b. stroke transform/clip - keep the middle half (W=720, sel x=180 w=360) */
const st = [
  { m: "b", x: 200, y: 100, r: 10 },
  { m: "b", x: 500, y: 100, r: 10 },
  { m: "b", x: 90, y: 100, r: 10 },    // transforms to -180 -> clipped
  { m: "b", x: 710, y: 100, r: 10 },   // transforms past 720 -> clipped
];
const cs = H.cropStrokes(st, { x: 180, w: 360 }, 720);
if (cs.length !== 2) fail(`cropStrokes kept ${cs.length}, want 2`);
if (cs[0] && !near(cs[0].x, 40)) fail(`cropStrokes x 200 -> ${cs[0] && cs[0].x}, want 40`);
if (cs[1] && !near(cs[1].x, 640)) fail(`cropStrokes x 500 -> ${cs[1] && cs[1].x}, want 640`);
if (cs[0] && cs[0].y !== 100) fail("cropStrokes must not change y (horizontal crop only)");

/* 8c. profile-only record: crop clips the stored profile to the new frame and
   fills out-of-coverage bins exactly as a fresh pano of the new frame does */
const po = { ...base, paintMode: "obstacle", strokes: [],
             profile: new Array(720).fill(20), profileTop: new Array(720).fill(-90) };
H.cropPano(po, { x: 180, w: 360 }, 720);           // keep az 90..270
if (po.fov !== 180 || !near(po.azOffset, 90)) fail(`profile crop calib ${po.fov}/${po.azOffset}`);
if (po.profile[200] !== 20) fail(`in-FOV bin lost: ${po.profile[200]}`);
if (po.profile[20] !== H.NO_BLOCK) fail(`out-of-FOV obstacle bin -> ${po.profile[20]}, want NO_BLOCK`);
if (po.profileTop[20] !== H.NO_BLOCK) fail("out-of-FOV obstacle top must be NO_BLOCK");
const poSky = { ...base, paintMode: "sky", strokes: [],
                profile: new Array(720).fill(10), profileTop: new Array(720).fill(40) };
H.cropPano(poSky, { x: 180, w: 360 }, 720);
if (poSky.profile[20] !== 90) fail(`out-of-FOV sky bin -> ${poSky.profile[20]}, want 90`);

/* 8d. stroke record: strokes WIN - they are clipped and the profile re-derived
   (unlike the desktop, which re-encodes the photo and WIPES the mask). */
const ps2 = { ...base, paintMode: "obstacle", strokes: [{ m: "b", x: 200, y: 100, r: 10 }],
              profile: new Array(720).fill(-90), profileTop: new Array(720).fill(-90) };
H.cropPano(ps2, { x: 180, w: 360 }, 720);
if (!(ps2.profile[200] !== H.NO_BLOCK)) fail("cropped stroke record must keep a derived profile");
if (ps2.profile[0] !== H.NO_BLOCK) fail("cropped stroke record must not block outside its band");
if (!near(ps2.fov, 180)) fail("stroke crop fov");
if (H.wrapsFor({ ...base, fov: 180 })) fail("wrapsFor must be false for a partial FOV");
// a crop that keeps a stroke crossing the old seam re-derives without wrap
const seamCrop = { ...base, paintMode: "obstacle", strokes: [{ m: "b", x: 1, y: 180, r: 12 }] };
H.cropPano(seamCrop, { x: 0, w: 720 }, 720);       // select all: full frame, still wraps
if (!H.wrapsFor(seamCrop)) fail("select-all crop must stay a wrapping 360 pano");
if (seamCrop.profile[0] === H.NO_BLOCK || seamCrop.profile[719] === H.NO_BLOCK) {
  fail("select-all crop lost the seam continuity");
}

console.log(ok ? "pano geometry contract held" : "pano geometry FAILED");
process.exit(ok ? 0 : 1);
