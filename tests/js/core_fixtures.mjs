/*
 * Class P fixture runner (Phase 2): evaluates core/ against the committed
 * Python-oracle fixtures and prints one JSON object. Concepts not yet moved
 * into core are omitted, so the harness can land before the extraction.
 *
 * Usage: node tests/js/core_fixtures.mjs <fixtures.json> [coreDir]
 *   coreDir defaults to ../../core (i.e. core/index.js); the mutation tests
 *   pass a tampered copy.
 */
import { readFileSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
import { resolve } from "node:path";

const [, , fixturePath, coreDir] = process.argv;
if (!fixturePath) {
  console.error("usage: node core_fixtures.mjs <fixtures.json> [coreDir]");
  process.exit(2);
}
const fx = JSON.parse(readFileSync(fixturePath, "utf8"));
const coreIndex = coreDir
  ? resolve(coreDir, "index.js")
  : resolve(fileURLToPath(new URL("../../core", import.meta.url)), "index.js");
const C = await import(pathToFileURL(coreIndex).href);
const has = (n) => typeof C[n] === "function";
const iso = (d) => (d ? new Date(d).toISOString() : null);
const norm360 = (x) => ((x % 360) + 360) % 360;
const out = {};

/* TO-10 (0.6.24 review): report WHICH core export a section is missing, so the
   Python side can FAIL with an actionable message instead of skipping. The
   "concept not extracted yet" scaffolding is gone: the core is complete, so a
   missing section means a renamed/removed export. */
const NEEDS = {
  jd: ["julianDay"], gmst: ["gmstDeg"], precession: ["precessJ2000ToDate"],
  altaz: ["altaz"], sun: ["sunRaDec"],
  moon: ["moonRaDec", "moonIllumination", "moonAge"],
  twilight: ["nightInfo", "pickDarkWindow"],
};
const missing = {};
for (const [section, names] of Object.entries(NEEDS)) {
  const gone = names.filter((n) => !has(n));
  if (gone.length) missing[section] = gone;
}
out.missing = missing;

if (has("julianDay")) {
  out.jd = fx.jd.map((c) => C.julianDay(new Date(c.iso)));
}
if (has("gmstDeg")) {
  out.gmst = fx.gmst.map((c) => norm360(C.gmstDeg(c.jd)));
}
if (has("precessJ2000ToDate")) {
  out.precession = fx.precession.map((c) => C.precessJ2000ToDate(c.ra, c.dec, c.jd));
}
if (has("altaz")) {
  out.altaz = fx.altaz.map((c) => C.altaz(c.ra, c.dec, c.jd, c.lat, c.lon));
}
if (has("sunRaDec")) {
  out.sun = fx.sun.map((c) => C.sunRaDec(c.jd));
}
if (has("moonRaDec") && has("moonIllumination") && has("moonAge")) {
  out.moon = fx.moon.map((c) => {
    const [ra, dec] = C.moonRaDec(c.jd);
    return [ra, dec, C.moonIllumination(c.jd), C.moonAge(c.jd)];
  });
}
if (has("nightInfo") && has("pickDarkWindow")) {
  out.twilight = fx.twilight.map((c) => {
    const [y, m, d] = c.date.split("-").map(Number);
    const noonUtc = Date.UTC(y, m - 1, d, 12) - c.tz_min * 60000;
    const localNoon = new Date(noonUtc);
    const dayDate = new Date(Date.UTC(y, m - 1, d, 12));
    const info = C.nightInfo(dayDate, c.lat, c.lon, localNoon);
    const win = C.pickDarkWindow(info, dayDate, localNoon);
    return { level: win.level, sunset: iso(info.sunset), sunrise: iso(info.sunrise),
             dusk: iso(win.dusk), dawn: iso(win.dawn) };
  });
}

process.stdout.write(JSON.stringify(out));
