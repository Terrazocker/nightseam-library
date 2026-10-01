/*
 * FIX-6 regression: the polar-day fallback window must be built from the
 * SITE's local noon (or an explicit day anchor), never from the wall clock.
 *
 * Defect (CODE-REVIEW-0.6.20, CR1): pickDarkWindow() fell back to `new Date()`
 * when called without an anchor - web/app.js's night-info chip did exactly
 * that, so a polar day/twilight showed `now+8h..now+16h` instead of the
 * selected night's site-zoned 20:00-04:00 (and a pure core function read the
 * clock, which is untestable).
 *
 * Exit 0 = contract held; 1 = violated.
 */
import { pickDarkWindow } from "../../core/index.js";

const failures = [];
const fail = (msg) => failures.push(msg);
const iso = (d) => (d ? d.toISOString().replace(".000Z", "Z") : null);

// 1) no anchor at all: refuse, never read the clock
let threw = false;
try {
  pickDarkWindow({});
} catch {
  threw = true;
}
if (!threw) {
  fail("pickDarkWindow() with no anchor must throw instead of using `new Date()`");
}

// 2) the explicit day anchor keeps the documented fallback semantics
const day = new Date(Date.UTC(2026, 5, 21, 12, 0, 0));   // local noon, UTC
const wd = pickDarkWindow({}, day);
if (iso(wd.dusk) !== "2026-06-21T20:00:00Z" || iso(wd.dawn) !== "2026-06-22T04:00:00Z") {
  fail(`day-anchored fallback wrong: ${iso(wd.dusk)}..${iso(wd.dawn)}`);
}

// 3) the site-local noon wins (site UTC+2: 20:00 local == 18:00Z)
const noon = new Date(Date.UTC(2026, 5, 21, 10, 0, 0));
const w = pickDarkWindow({}, null, noon);
if (iso(w.dusk) !== "2026-06-21T18:00:00Z" || iso(w.dawn) !== "2026-06-22T02:00:00Z") {
  fail(`site-noon fallback wrong: ${iso(w.dusk)}..${iso(w.dawn)}`);
}
if (w.level !== "fallback") fail(`level ${w.level} != fallback`);

// 4) a real twilight window is returned unchanged (no fallback path taken)
const info = { astroDusk: new Date(Date.UTC(2026, 5, 21, 22, 0, 0)),
               astroDawn: new Date(Date.UTC(2026, 5, 22, 1, 0, 0)) };
const wx = pickDarkWindow(info);
if (wx.level !== "astronomical" || iso(wx.dusk) !== "2026-06-21T22:00:00Z") {
  fail(`twilight window changed: ${wx.level} ${iso(wx.dusk)}`);
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("noon fallback contract held");
