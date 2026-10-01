/*
 * D1 contract: core panorama profiles are WORLD-AZIMUTH bins (bin i = azimuth
 * i*0.5 deg), matching the desktop editor and the Python server; the web
 * editor's FOV-relative mask converts through the core adapter at the
 * storage boundary. Usage:
 *     node tests/js/core_d1.mjs [path-to-horizon-module]
 * Defaults to core/horizon.js. Prints one marker line; the Python tests assert
 * it, and the mutation test runs this script against a perturbed copy.
 */
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const arg = process.argv[2];
const target = arg
  ? pathToFileURL(resolve(arg)).href
  : new URL("../../core/horizon.js", import.meta.url).href;
const H = await import(target);

const BINS = 720;
let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };
const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;
const binOf = (az) => Math.min(BINS - 1,
  Math.floor((((az % 360) + 360) % 360) / 0.5));
const representativeCol = (i, azOff, fov) => {
  const rel = (((i * 0.5 - azOff) % 360) + 360) % 360;
  if (rel <= fov) return Math.min(BINS - 1, Math.floor(rel * BINS / fov));
  // CR3: the bin STRADDLING the frame start (its wrapped left edge is in
  // [359.5,360), so it covers azimuths azOffset..) is in frame and is
  // represented by the midpoint of its wrapped overlap, not by column 719.
  if (rel + 0.5 > 360) {
    return Math.min(BINS - 1, Math.floor(((rel + 0.5 - 360) / 2) * BINS / fov));
  }
  return -1;
};

const CASES = [
  { fov: 360, azOffset: 0 },
  { fov: 360, azOffset: 90 },
  { fov: 180, azOffset: 90 },
  { fov: 120, azOffset: 350 },
  { fov: 300, azOffset: 45 },
  // CR3: azOffsets that are NOT multiples of 0.5 (normal after a crop) put a
  // bin on the frame start - the case the old left-edge rule got wrong.
  { fov: 45, azOffset: 0.25 },
  { fov: 120, azOffset: 349.75 },
  { fov: 200, azOffset: 12.3 },
];

/* slanted band: every mask column has its own top row, so a wrong bin/column
 * mapping cannot pass by accident */
function slantedMask() {
  const m = new Uint8Array(720 * 360);
  for (let col = 0; col < 720; col++) {
    const top = 40 + ((col * 7) % 60);
    for (let row = top; row <= 220; row++) m[row * 720 + col] = 255;
  }
  return m;
}

/* ---- 1. profileFromMask -> world bins, desktop representative column ---- */
const mask = slantedMask();
for (const { fov, azOffset } of CASES) {
  const p = { paintMode: "obstacle", fov, azOffset, altBottom: 0, altTop: 60 };
  const { prof } = H.profileFromMask(p, mask);
  for (let i = 0; i < BINS; i++) {
    const col = representativeCol(i, azOffset, fov);
    const want = col < 0 ? H.NO_BLOCK
      : H.altOf(p, 40 + ((col * 7) % 60));
    if (col < 0) {
      if (prof[i] !== H.NO_BLOCK) { fail(`outside-FOV bin ${i} not sentinel`); break; }
    } else if (!near(prof[i], want, 1e-9)) {
      fail(`obstacle fov=${fov} azOff=${azOffset} bin ${i}: ${prof[i]} != ${want}`);
      break;
    }
  }
}

/* ---- 2. sky mode: band bottom/top per world bin + sentinels ---- */
const skyMask = (() => {
  const m = new Uint8Array(720 * 360);
  for (let col = 0; col < 720; col++) {
    const top = 60 + ((col * 5) % 40);
    for (let row = top; row <= 260; row++) m[row * 720 + col] = 255;
  }
  return m;
})();
for (const { fov, azOffset } of CASES) {
  const p = { paintMode: "sky", fov, azOffset, altBottom: 0, altTop: 60 };
  const { prof, top } = H.profileFromMask(p, skyMask);
  for (const i of [0, 100, 359, 500, 719]) {
    const col = representativeCol(i, azOffset, fov);
    if (col < 0) {
      if (prof[i] !== 90 || top[i] !== H.NO_BLOCK) { fail(`sky outside-FOV bin ${i}`); }
      continue;
    }
    const topRow = 60 + ((col * 5) % 40);
    const eTop = H.altOf(p, topRow);
    if (!near(prof[i], H.altOf(p, 260))) fail(`sky bottom fov=${fov} bin ${i}`);
    if (!near(top[i], eTop >= p.altTop - 4 ? 90.5 : eTop)) fail(`sky top fov=${fov} bin ${i}`);
  }
}

/* ---- 3. adapter (legacy FOV-relative -> world), idempotent ---- */
const legacy = Array.from({ length: BINS }, (_, c) => (c % 37) - 10);
const legacyTop = Array.from({ length: BINS }, (_, c) => c % 23);
for (const { fov, azOffset } of CASES) {
  const rec = { paintMode: "obstacle", fov, azOffset,
                profile: [...legacy], profileTop: [...legacyTop] };
  H.normalizeProfileBins(rec);
  if (rec.profileBins !== H.WORLD_BINS) fail("adapter must mark WORLD_BINS");
  for (let i = 0; i < BINS; i++) {
    const col = representativeCol(i, azOffset, fov);
    const want = col < 0 ? H.NO_BLOCK : legacy[col];
    if (rec.profile[i] !== want) { fail(`adapter bin ${i} (fov=${fov})`); break; }
    if (rec.profileTop[i] !== (col < 0 ? H.NO_BLOCK : legacyTop[col])) {
      fail(`adapter top bin ${i} (fov=${fov})`); break;
    }
  }
  const before = JSON.stringify(rec.profile) + JSON.stringify(rec.profileTop);
  H.normalizeProfileBins(rec);
  if (JSON.stringify(rec.profile) + JSON.stringify(rec.profileTop) !== before) {
    fail("adapter must be idempotent");
  }
}

/* ---- 4. fov=360 / azOffset=0 is byte-identical to the old column bins ---- */
{
  const p = { paintMode: "obstacle", fov: 360, azOffset: 0, altBottom: 0, altTop: 60 };
  const { prof } = H.profileFromMask(p, mask);
  for (let i = 0; i < BINS; i++) {
    if (!near(prof[i], H.altOf(p, 40 + ((i * 7) % 60)))) {
      fail(`full-sky identity broke at bin ${i}`);
      break;
    }
  }
}

/* ---- 5. ceilFor/bandTopFor world lookup with arbitrary fov/azOffset ---- */
{
  const wall = new Array(BINS).fill(H.NO_BLOCK);
  for (let i = 180; i < 540; i++) wall[i] = 20;   // world az 90..270
  const p = { paintMode: "obstacle", fov: 180, azOffset: 90,
              profile: wall, profileTop: new Array(BINS).fill(H.NO_BLOCK) };
  if (H.ceilFor(p, 95).ceil !== 20) fail("ceilFor world lookup inside FOV");
  if (H.ceilFor(p, 269).ceil !== 20) fail("ceilFor world lookup at FOV edge");
  if (H.ceilFor(p, 45).ceil !== H.NO_BLOCK) fail("ceilFor outside FOV must not read bins");
  if (H.ceilFor(p, 275).ceil !== H.NO_BLOCK) fail("ceilFor past FOV edge must not read bins");
  const band = new Array(BINS).fill(10);
  const tops = new Array(BINS).fill(40);
  const sky = { paintMode: "sky", fov: 180, azOffset: 90, blockAbove: true,
                profile: band, profileTop: tops };
  const r = H.ceilFor(sky, 180);
  if (r.ceil !== 10 || r.ceilTop !== 40) fail("sky ceilFor world lookup");
  if (H.ceilFor(sky, 45).ceil !== 90) fail("sky outside FOV must block");
  if (H.bandTopFor(sky, 180) !== 40) fail("bandTopFor world lookup");
  const runs = H.assumedOpenRuns({ ...sky, blockAbove: false }, 1);
  if (runs.length !== 1 || runs[0].a0 !== 90 || runs[0].a1 !== 270) {
    fail(`assumedOpenRuns world/FOV runs: ${JSON.stringify(runs)}`);
  }
}

/* ---- 6. preview hatch stays bin-space (0..719 full world) ---- */
{
  const p = { paintMode: "sky", blockAbove: false, fov: 360, azOffset: 0,
              profile: new Array(BINS).fill(5), profileTop: new Array(BINS).fill(40) };
  const polys = H.assumedOpenHatchPolys(p, 720, 56, 0, 90);
  const xs = polys.flat().map((q) => q[0]);
  if (polys.length !== 1 || Math.min(...xs) !== 0 || Math.max(...xs) !== 719) {
    fail("hatch must cover the full world width");
  }
}

/* ---- 7. CR3: EVERY in-frame azimuth must read a COVERED bin ----
 * The scorer gates per azimuth continuously (`ceilFor`: inside while
 * 0 <= az - azOffset <= fov), so the producer must cover the bin that straddles
 * the frame start when azOffset is not a multiple of 0.5. Pre-CR3 that bin kept
 * the mode sentinel, so a painted band read CLEAR in obstacle mode and fully
 * BLOCKED in sky mode, and the bin's representative column wrapped to 719 - the
 * opposite edge of the mask. This section asserts the property directly instead
 * of re-deriving the formula. */
for (const { fov, azOffset } of CASES) {
  const p = { paintMode: "obstacle", fov, azOffset, altBottom: 0, altTop: 60 };
  const { prof } = H.profileFromMask(p, mask);
  for (let d = 0; d <= fov; d += 0.1) {
    const az = (((azOffset + d) % 360) + 360) % 360;
    const bin = binOf(az);
    if (prof[bin] === H.NO_BLOCK) {
      fail(`CR3: in-frame az ${az.toFixed(2)} (fov=${fov} azOff=${azOffset}) reads the uncovered sentinel at bin ${bin}`);
      break;
    }
    if (H.ceilFor({ ...p, profile: prof, profileTop: new Array(BINS).fill(H.NO_BLOCK) }, az).ceil === H.NO_BLOCK) {
      fail(`CR3: ceilFor disagrees with the profile at az ${az.toFixed(2)} (fov=${fov} azOff=${azOffset})`);
      break;
    }
  }
  // the frame-start bin must not clamp to the FAR mask column
  const startBin = binOf((((azOffset + 0.05) % 360) + 360) % 360);
  const far = H.altOf(p, 40 + ((719 * 7) % 60));
  const want = H.altOf(p, 40 + ((representativeCol(startBin, azOffset, fov) * 7) % 60));
  if (!near(prof[startBin], want, 1e-9)) {
    fail(`CR3: frame-start bin ${startBin} != its own mask column (fov=${fov} azOff=${azOffset})`);
  }
  if (near(prof[startBin], far, 1e-9)) {
    fail(`CR3: frame-start bin ${startBin} clamped to the far mask column (fov=${fov})`);
  }
}

console.log(ok ? "core D1 contract held" : "core D1 contract FAILED");
process.exit(ok ? 0 : 1);
