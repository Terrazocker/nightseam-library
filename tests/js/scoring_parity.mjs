/*
 * Scoring parity proof (Phase 3b): the batched engine against the PRE-CHANGE
 * characterization goldens. Displayed fields must be bit-identical and the
 * transit index too; the continuous score doubles are allowed the
 * existing characterization tolerances (maxAlt <= 1e-5 deg, scores <= 2e-5).
 *
 * The INPUT objects are part of the golden (`g.objects`, baked into the fixture
 * when the curated catalog was removed): the proof must not silently re-point at
 * different objects because web/catalog.json changed. The old engine rebuilt its
 * inputs from the shipped catalog, so any catalog edit looked like a regression.
 *
 * Usage: node tests/js/scoring_parity.mjs <goldens.json> [coreDir]
 * The mutation test passes a tampered core copy and expects exit != 0.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const goldensPath = process.argv[2];
const coreDir = process.argv[3] || fileURLToPath(new URL("../../core", import.meta.url));
const g = JSON.parse(readFileSync(goldensPath, "utf8"));
const C = await import(pathToFileURL(coreDir + "/index.js").href);
const objects = g.objects;
if (!Array.isArray(objects) || !objects.length) {
  throw new Error("the goldens carry no `objects` inputs - the parity proof needs them baked in");
}

const DISPLAY = ["n", "visN", "blocked", "bestRun", "bestStart", "pct", "blockedPct"];
const NUMERIC = ["n", "maxAlt", "transitJd", "visN", "blocked", "bestRun",
                 "bestStart", "sClassic", "sMoon", "pct", "blockedPct"];
function requireRows(rows, expectedCount, label) {
  if (!Array.isArray(rows) || rows.length !== expectedCount) {
    throw new Error(`${label}: expected ${expectedCount} rows, got `
      + (Array.isArray(rows) ? rows.length : "a non-array"));
  }
  for (let k = 0; k < rows.length; k++) {
    const row = rows[k];
    if (!row || typeof row !== "object" || Array.isArray(row)) {
      throw new Error(`${label}[${k}]: expected a statistics object`);
    }
    for (const field of NUMERIC) {
      if (typeof row[field] !== "number" || !Number.isFinite(row[field])) {
        throw new Error(`${label}[${k}].${field}: expected a finite number`);
      }
    }
  }
}
if (!Array.isArray(g.cases) || !g.cases.length) {
  throw new Error("the goldens carry no nonempty cases array");
}
let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };
let maxDAlt = 0, maxDScore = 0, maxDMoon = 0;

for (const c of g.cases) {
  if (!c || !Array.isArray(c.stats) || !c.stats.length) {
    throw new Error("each golden case must carry a nonempty stats array");
  }
  requireRows(c.stats, c.stats.length, `${c.name} expected`);
  if (objects.length < c.stats.length) {
    throw new Error(`${c.name}: not enough frozen input objects for the expected rows`);
  }
  const objs = objects.slice(0, c.stats.length);
  const ctx = {};
  if (c.moon) {
    ctx.moonAware = true; ctx.illum = 0.7; ctx.moonAgeDays = 9.5;
    ctx.filterType = "lrgb"; ctx.typeF = 0.8; ctx.bortleF = 0.76;
    ctx.moonAlt = []; ctx.moonAz = [];
    for (let i = 0; i <= g.n; i++) {
      const jd = g.jd0 + i * g.step;
      const [ra, dec] = C.moonRaDec(jd);
      const [a, z] = C.altaz(ra, dec, jd, c.lat, c.lon);
      ctx.moonAlt.push(a); ctx.moonAz.push(z);
    }
  }
  const got = C.nightStatsBatch(objs, g.jd0, g.jd0 + g.n * g.step, g.step,
                                c.minAlt, null, c.lat, c.lon, ctx);
  requireRows(got, c.stats.length, `${c.name} batch`);
  // the split path must be bit-identical to the one-shot form
  const tracks = C.computeNightTracks(objs, g.jd0, g.jd0 + g.n * g.step, g.step, c.lat, c.lon);
  const viaTracks = C.scoreNightTracks(tracks, objs, g.jd0, g.step, c.minAlt, null, ctx);
  requireRows(viaTracks, c.stats.length, `${c.name} split`);
  for (const f of NUMERIC) {
    for (let k = 0; k < got.length; k++) {
      if (!Object.is(got[k][f], viaTracks[k][f])) {
        fail(`${c.name}[${k}] split mismatch on ${f}`);
      }
    }
  }
  for (let k = 0; k < got.length; k++) {
    const a = c.stats[k], b = got[k];
    for (const f of DISPLAY) if (a[f] !== b[f]) {
      fail(`${c.name}[${k}] ${f}: ${a[f]} != ${b[f]}`);
    }
    if (a.transitJd !== b.transitJd) fail(`${c.name}[${k}] transit index moved`);
    maxDAlt = Math.max(maxDAlt, Math.abs(a.maxAlt - b.maxAlt));
    maxDScore = Math.max(maxDScore, Math.abs(a.sClassic - b.sClassic));
    maxDMoon = Math.max(maxDMoon, Math.abs(a.sMoon - b.sMoon));
  }
}
if (maxDAlt > 1e-5) fail(`maxAlt drift ${maxDAlt}`);
if (maxDScore > 2e-5 || maxDMoon > 2e-5) fail(`score drift ${maxDScore}/${maxDMoon}`);
console.log(ok ? "scoring batched parity held" : "scoring batched parity FAILED");
process.exit(ok ? 0 : 1);
