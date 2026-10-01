/*
 * CR2 (carried over from the 0.6.20/0.6.24 reviews, closed in 0.6.35): every
 * entry point in `core/horizon.js` must DEGRADE on a missing or malformed
 * record, never throw.
 *
 * Why it matters rather than being tidiness: these run inside render passes
 * (the web editor's silhouette and band preview, the polar Sky View), and the
 * shapes they throw on are reachable from STORED data - a sky-mode record with
 * no `profileTop` array, a record whose profile is not an array, a panorama
 * object that is null. `visibleSkyBands` was fixed to degrade earlier (CO-04);
 * `skyBandPolygons` and `profileSegments` were not, so a single bad record could
 * take down the whole render with "Cannot read properties of undefined" instead
 * of drawing nothing.
 *
 * The contract asserted here is deliberately uniform: a missing/unusable record
 * yields the EMPTY answer for that function, which is exactly what the callers
 * already render for an empty result. Prints one marker line; the Python side
 * is tests/test_js_behaviour.py.
 */
const H = await import(new URL('../../core/horizon.js', import.meta.url).href);

const BINS = 720;
const fill = (v) => new Array(BINS).fill(v);
let ok = true;
const fail = (msg) => { ok = false; console.log("CR2 FAIL: " + msg); };

/* Every shape a stored record can take that is not a usable panorama. A valid
 * record always has `profile` as an array; `profileTop` is optional. */
const MALFORMED = {
  "null record": null,
  "no fields": {},
  "profile: null": { profile: null },
  "profile: string": { profile: "nope" },
  "profile: short array": { profile: [1, 2, 3] },
  "profile: NaN fill": { profile: fill(NaN) },
  "profile: string fill": { profile: fill("x") },
  "sky mode, no profileTop": { profile: fill(10), paintMode: "sky" },
  "sky mode, short profileTop": { profile: fill(10), profileTop: [1], paintMode: "sky" },
  "sky mode, null profileTop": { profile: fill(10), profileTop: null, paintMode: "sky" },
  "sky mode, string profileTop": { profile: fill(10), profileTop: fill("a"), paintMode: "sky" },
  "altBottom: null": { profile: fill(10), altBottom: null },
  "altBottom: string": { profile: fill(10), altBottom: "x" },
  "blockAbove without a top": { profile: fill(10), paintMode: "sky", blockAbove: true },
};

/* fn(record) -> the empty answer for an unusable record. `profileSegments` takes
 * the ARRAY, not the record, because that is how both editors call it (the web
 * editor passes `p.profileTop`, which is null for a sky record with no top
 * array). `ceilFor` is the odd one out: its degrade is a NUMBER (the value is
 * not an array), which is what `lookup()` in planning_client.js expects. */
const CALLS = {
  visibleSkyBands: { fn: (p) => H.visibleSkyBands(p), kind: "array" },
  skyBandPolygons: { fn: (p) => H.skyBandPolygons(p, 720, 360, 0, 45, 0, 360), kind: "array" },
  profileSegments: { fn: (p) => H.profileSegments(p && p.profile, 720, 360, 0, 45, 0, 360), kind: "array" },
  profileSegmentsTop: { fn: (p) => H.profileSegments(p && p.profileTop, 720, 360, 0, 45, 0, 360), kind: "array" },
  overhangRuns: { fn: (p) => H.overhangRuns(p), kind: "array" },
  assumedOpenRuns: { fn: (p) => H.assumedOpenRuns(p), kind: "array" },
  ceilFor: { fn: (p) => H.ceilFor(p, 123), kind: "ceiling" },
};

for (const [fnName, { fn, kind }] of Object.entries(CALLS)) {
  for (const [shape, rec] of Object.entries(MALFORMED)) {
    let got;
    try {
      got = fn(rec);
    } catch (e) {
      fail(`${fnName}(${shape}) THREW ${e.constructor.name}: ${e.message}`);
      continue;
    }
    if (kind === "array" && !Array.isArray(got)) {
      fail(`${fnName}(${shape}) returned ${typeof got}, expected an array`);
    }
    if (kind === "ceiling") {
      // { ceil, ceilTop }: a USABLE sentinel, never undefined - `alt > undefined`
      // is false, which would hide every object in the sky (the guard in
      // `ceilFor` says exactly that).
      if (!got || typeof got !== "object") {
        fail(`${fnName}(${shape}) returned ${String(got)}, expected {ceil, ceilTop}`);
      } else if (!Number.isFinite(Number(got.ceil))) {
        fail(`${fnName}(${shape}) returned ceil=${String(got.ceil)} - must be finite`);
      } else if (!(got.ceilTop === null || Number.isFinite(Number(got.ceilTop)))) {
        fail(`${fnName}(${shape}) returned ceilTop=${String(got.ceilTop)}`);
      }
    }
  }
}

/* The degrade must not become "always empty": a VALID record still answers. */
const good = { profile: fill(12), altBottom: 0 };
if (H.visibleSkyBands(good).length !== BINS) fail("a valid record must still answer per column");
if (H.skyBandPolygons(good, 720, 360, 0, 45, 0, 360) === undefined) fail("valid polygons");
if (H.profileSegments(good.profile, 720, 360, 0, 45, 0, 360).length === 0) {
  fail("a valid profile must still produce segments");
}
/* ... and a valid SKY record with a painted band still fills its band. */
const sky = { profile: fill(20), paintMode: "sky" };
const bands = H.visibleSkyBands(sky, 0, 90);
if (!bands.length || !bands[0].vis) fail("a valid sky record must still paint a band");
const polys = H.skyBandPolygons(sky, 720, 360, 0, 45, 0, 360);
if (!polys.length) fail("a valid sky record must still produce polygons");

console.log(ok ? "CR2 horizon degrade contract held" : "CR2 horizon degrade contract FAILED");
process.exit(ok ? 0 : 1);
