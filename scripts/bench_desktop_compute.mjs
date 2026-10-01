/*
 * Phase 2 groundwork benchmark: can the desktop webview compute the full
 * planning set client-side within budget? This measures the SAME core the
 * desktop ships (Node's V8 == Edge WebView2's engine) over the real catalog:
 * per-object alt/az tracks plus the nightStats visibility/score loop, for the
 * standard (curated) and advanced catalogs on a 24 h 6-minute grid.
 *
 * Measure only - authority stays with the Python server.
 *
 * Usage: node scripts/bench_desktop_compute.mjs [catalog.json]
 */
import { readFileSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
import { resolve } from "node:path";

const C = await import(pathToFileURL(
  resolve(fileURLToPath(new URL("../core", import.meta.url)), "index.js")).href);

const catalogPath = process.argv[2]
  || resolve(fileURLToPath(new URL("..", import.meta.url)), "web", "catalog.json");
const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
const curated = catalog.filter((o) => !o._adv);
const advanced = catalog.filter((o) => o._adv);

// Representative night: Berlin, the site's local noon -> +24 h at 6-min steps
// (the desktop payload grid shape: 241 samples).
const lat = 52.52, lon = 13.405;
const jd0 = C.julianDay(new Date("2026-01-15T11:00:00Z"));
const step = 6 / 1440;
const n = 240;
const jd1 = jd0 + n * step;

function bench(objects, label) {
  const t0 = performance.now();
  let visTotal = 0;
  for (const o of objects) {
    const s = C.nightStats(o, jd0, jd1, step, 25, null, lat, lon, {});
    visTotal += s.visN;
  }
  const ms = performance.now() - t0;
  return { label, objects: objects.length, samples: n + 1, ms: Math.round(ms),
           usPerObjectSample: +(ms * 1000 / (objects.length * (n + 1))).toFixed(1),
           visSamples: visTotal };
}

// Optimized path the advanced synthesis uses: precess once per object, batched
// sidereal frame once for the grid, then apply per sample.
function benchFrame(objects, label) {
  const jds = [];
  for (let i = 0; i <= n; i++) jds.push(jd0 + i * step);
  const t0 = performance.now();
  const frame = C.altazFrame(lat, lon, jds);
  let acc = 0;
  for (const o of objects) {
    const ra = o.ra_hours * 15;
    for (let i = 0; i <= n; i++) {
      const [alt, az] = C.altazFrameApply(frame, ra, o.dec_deg, i);
      acc += alt + az;
    }
  }
  const ms = performance.now() - t0;
  return { label, objects: objects.length, ms: Math.round(ms), checksum: Math.round(acc) };
}

console.log("catalog:", curated.length, "curated +", advanced.length, "advanced");
console.log(JSON.stringify(bench(curated, "standard nightStats")), );
console.log(JSON.stringify(bench(advanced, "advanced nightStats")));
console.log(JSON.stringify(benchFrame(curated, "standard frame path")));
console.log(JSON.stringify(benchFrame(advanced, "advanced frame path")));
