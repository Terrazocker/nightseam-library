/*
 * TO-09 (0.6.24 review, carry-over TS4): the core exports NO test named.
 *
 * Eight exported FUNCTIONS were referenced nowhere under tests/ (the probe also
 * listed four constants), so a regression in them could be caught only indirectly
 * - or not at all (clipProfileToFov, fmtFov, fmtFovPair, isCustomId, searchBlob,
 * sizeArcmin). Each check asserts a PROPERTY of the documented contract, never a
 * golden value:
 *   clipProfileToFov  resets out-of-FOV bins to the mode sentinel, keeps the in-FOV
 *                     world bins and repairs a malformed profile
 *   fmtFov/fmtFovPair render arcminutes with the degrees in brackets
 *   isCustomId        accepts only the server's `u_` prefix
 *   searchBlob        lowercases and joins the searchable fields
 *   sizeArcmin        largest dimension; a lone value is a diameter; null if none
 *   sunAlt            apparent solar altitude (positive by day, negative at night)
 *   sunCrossings      finds the evening (descending) and morning (ascending) pair
 *
 * Prints one marker line; tests/test_js_behaviour.py asserts it.
 */
const C = await import(new URL("../../core/index.js", import.meta.url).href);

let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };
const eq = (got, want, what) => {
  if (got !== want) {
    fail(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
};

/* ---- sizeArcmin / fmtFov / fmtFovPair (core/format.js) ---- */
eq(C.sizeArcmin("10x5"), 10, "sizeArcmin must take the largest dimension");
eq(C.sizeArcmin("7"), 7, "a lone value is a diameter");
eq(C.sizeArcmin(""), null, "no size -> null");
eq(C.sizeArcmin("M 31"), null, "an unparseable size -> null");
const f = C.fmtFov(76.6);
if (!f.includes("76.6") || !f.includes("1.28")) fail(`fmtFov(76.6) = ${f}`);
const pair = C.fmtFovPair(76.6, 50);
if (!pair.includes("\u00D7") || !pair.includes("76.6") || !pair.includes("50")) {
  fail(`fmtFovPair(76.6, 50) = ${pair}`);
}

/* ---- isCustomId / searchBlob (core/catalog.js) ---- */
eq(C.isCustomId("u_abc"), true, "the u_ prefix marks a custom target");
eq(C.isCustomId("m31"), false, "a catalog id is not custom");
eq(C.isCustomId(7), false, "a non-string is not a custom id");
eq(C.searchBlob({ id: "M 31", primary_name: "Andromeda", messier: "M31",
                  ngcic: null, other: null, otype: "G", constellation: "And",
                  blurb: "", distance: null }),
   "m 31 andromeda m31 g and", "searchBlob must lowercase and join the fields");

/* ---- clipProfileToFov (core/horizon.js) ---- */
const BINS = 720;
const pano = { paintMode: "obstacle", fov: 180, azOffset: 0,
               profile: new Array(BINS).fill(30),
               profileTop: new Array(BINS).fill(-90) };
C.clipProfileToFov(pano);
eq(pano.profile[0], 30, "an in-FOV bin keeps its world value");
eq(pano.profile[360], 30, "the 180 deg boundary bin is still in frame");
eq(pano.profile[361], C.NO_BLOCK, "the first out-of-FOV bin resets to the sentinel");
eq(pano.profile[719], C.NO_BLOCK, "the last bin resets too");
eq(pano.profileBins, "world", "the record must stay world-azimuth");
const broken = { paintMode: "sky", fov: 360, azOffset: 0, profile: "nope" };
C.clipProfileToFov(broken);
eq(broken.profile.length, BINS, "a malformed profile is repaired");
eq(broken.profile[0], 90, "sky mode repairs to the fully-blocked sentinel");
eq(broken.profileTop.length, BINS, "a missing profileTop is repaired");

/* ---- sunAlt / sunCrossings (core/astro.js + core/planning.js) ---- */
const noon = C.julianDay(new Date("2026-06-21T12:00:00Z"));
const night = C.julianDay(new Date("2026-06-21T00:00:00Z"));
const altNoon = C.sunAlt(noon, 50, 0);
const altNight = C.sunAlt(night, 50, 0);
if (!(altNoon > 40)) fail(`the June noon sun at 50N must be high, got ${altNoon}`);
if (!(altNight < 0)) fail(`the June midnight sun at 50N must be below 0, got ${altNight}`);

const cr = C.sunCrossings(new Date("2026-03-20T00:00:00Z"), 50, 0, -6);
if (!cr.evening || !cr.morning) {
  fail("sunCrossings must find an evening AND a morning crossing at 50N in March");
} else {
  if (!(cr.evening.getTime() < cr.morning.getTime())) {
    fail("the evening crossing must precede the morning one");
  }
  const altAtCross = C.sunAlt(C.julianDay(cr.evening), 50, 0);
  if (Math.abs(altAtCross - (-6)) > 0.5) {
    fail(`the evening crossing must sit at the -6 deg threshold, got ${altAtCross}`);
  }
}

console.log(ok ? "core exports contract held" : "core exports contract FAILED");
process.exit(ok ? 0 : 1);
