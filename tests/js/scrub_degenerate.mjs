/*
 * Scrub / detail-chart degeneracy policy (core/planning.js::scrubWindowMs).
 *
 * A sunset->sunrise range is used only when the pair is strictly ordered AND
 * the sunset precedes the next dawn; otherwise dusk->dawn, else the site-zoned
 * 20:00-04:00 fallback. Regression: Tromso 2026-01-15 (grazing polar night)
 * produced an inverted range in both apps. Prints one marker line.
 */
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const C = await import(process.argv[2]
  ? pathToFileURL(resolve(process.argv[2])).href
  : new URL("../../core/planning.js", import.meta.url).href);

let ok = true;
const fail = (m) => { ok = false; console.log("  FAIL:", m); };
const ms = (iso) => Date.parse(iso);

const NOON = ms("2026-01-15T11:00:00Z");   // Tromso local noon (tz +60)
const DUSK = ms("2026-01-15T16:34:14Z");
const DAWN = ms("2026-01-16T05:12:08Z");
const SUNSET = ms("2026-01-16T11:08:44Z");
const ASCENT = ms("2026-01-16T10:39:35Z");

const cases = [
  ["grazing polar night (sunrise null)", {
    sunsetMs: SUNSET, sunriseMs: null, duskMs: DUSK, dawnMs: DAWN, noonMs: NOON,
  }, "twilight"],
  ["grazing polar night (pre-fix inverted pair)", {
    sunsetMs: SUNSET, sunriseMs: ASCENT, duskMs: DUSK, dawnMs: DAWN, noonMs: NOON,
  }, "twilight"],
  ["sunset after dawn (ordered pair, wrong night)", {
    sunsetMs: ms("2026-01-16T11:08:44Z"), sunriseMs: ms("2026-01-16T12:00:00Z"),
    duskMs: DUSK, dawnMs: DAWN, noonMs: NOON,
  }, "twilight"],
  ["normal mid-latitude", {
    sunsetMs: ms("2026-01-15T16:00:00Z"), sunriseMs: ms("2026-01-16T07:30:00Z"),
    duskMs: ms("2026-01-15T17:30:00Z"), dawnMs: ms("2026-01-16T06:00:00Z"),
    noonMs: NOON,
  }, "sun"],
  ["DST-transition night (ordered)", {
    sunsetMs: ms("2026-10-24T16:05:00Z"), sunriseMs: ms("2026-10-25T06:50:00Z"),
    duskMs: ms("2026-10-24T17:35:00Z"), dawnMs: ms("2026-10-25T05:20:00Z"),
    noonMs: NOON,
  }, "sun"],
  ["southern hemisphere", {
    sunsetMs: ms("2026-01-15T19:00:00Z"), sunriseMs: ms("2026-01-16T09:00:00Z"),
    duskMs: ms("2026-01-15T20:30:00Z"), dawnMs: ms("2026-01-16T07:30:00Z"),
    noonMs: NOON,
  }, "sun"],
  ["polar day (dark-window fallback supplied)", {
    sunsetMs: null, sunriseMs: null, duskMs: NOON + 8 * 3600000,
    dawnMs: NOON + 16 * 3600000, noonMs: NOON,
  }, "twilight"],
  ["nothing available -> site-zoned fallback", {
    sunsetMs: null, sunriseMs: null, duskMs: null, dawnMs: null, noonMs: NOON,
  }, "fallback"],
];

for (const [name, input, wantSource] of cases) {
  const r = C.scrubWindowMs(input);
  if (r.source !== wantSource) fail(`${name}: source ${r.source} != ${wantSource}`);
  if (!(r.startMs < r.endMs)) fail(`${name}: not strictly increasing (${r.startMs} >= ${r.endMs})`);
  if (Number.isNaN(r.startMs) || Number.isNaN(r.endMs)) fail(`${name}: NaN bound`);
}
// the fallback is exactly localNoon + 8h .. +16h (site-zoned 20:00-04:00)
const fb = C.scrubWindowMs({ noonMs: NOON });
if (fb.startMs !== NOON + 8 * 3600000 || fb.endMs !== NOON + 16 * 3600000) {
  fail("fallback must be localNoon +8h..+16h");
}

console.log(ok ? "scrub degeneracy policy held" : "scrub degeneracy policy FAILED");
process.exit(ok ? 0 : 1);
