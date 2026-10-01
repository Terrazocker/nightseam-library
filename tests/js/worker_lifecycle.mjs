/*
 * FIX-1 regression: worker lifecycle vs preference changes.
 *
 * Defect (CODE-REVIEW-0.6.20, DS1): every worker message overwrote the SAME
 * generation counter, so a `prefs` change during an in-flight `night` build
 * cancelled the build; the night loader treated the cancellation as
 * "superseded" and returned without clearing the busy chip or ever retrying -
 * the plan was lost and the spinner stuck.
 *
 * This harness drives the REAL static/js/planning_worker.js under Node (via
 * worker_bridge.mjs) plus the REAL static/js/night.js + planning_client.js:
 *   A) prefs must not cancel a running night build (raw protocol);
 *   B) a preference change mid-build must queue, and the finished plan must
 *      carry the new preference while the busy chip clears (no lost plan).
 *
 * Exit 0 = contract held; 1 = violated.
 */
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Worker as NodeWorker } from "node:worker_threads";

const ROOT = new URL("../../", import.meta.url);

/* ------------------------------------------------ catalog + site fixtures */
const CATALOG = [
  { id: "a", primary_name: "A", ra_hours: 0.7, dec_deg: 41.3, vmag: 4,
    otype: "galaxy" },
  { id: "b", primary_name: "B", ra_hours: 5.6, dec_deg: -5.4, vmag: 8,
    otype: "nebula" },
  { id: "c", primary_name: "C", ra_hours: 18.0, dec_deg: 30.0, vmag: 6,
    otype: "cluster" },
];
const CATALOG_FILE = join(tmpdir(), `ns-worker-catalog-${process.pid}.json`);
writeFileSync(CATALOG_FILE, JSON.stringify(CATALOG));
process.env.NS_TEST_CATALOG = CATALOG_FILE;
const SITE = { id: 1, name: "Home", lat: 52.5, lon: 13.4, elev: 40 };
const PREFS = { minAlt: 25, bortle: 4, moonAware: false,
                filters: { broadband: true } };

/* ---------------------------------------------------------------- stub DOM */
const elements = {
  "#siteSelect": { options: [{}, {}], value: "1" },
  "#dateInput": { value: "2026-09-12" },
  "#timeSlider": { max: 0 },
  "#busy": {
    hidden: true,
    classList: { toggle(_cls, force) { elements["#busy"].hidden = !!force; } },
    querySelector() { return null; },
  },
  "#toasts": { appendChild() {} },
};
globalThis.document = {
  querySelector: (sel) => elements[sel] || null,
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent: () => true,
  createElement: () => ({ classList: {}, style: {}, appendChild() {},
                          addEventListener() {} }),
  documentElement: { style: { setProperty() {} } },
};
globalThis.window = globalThis;
globalThis.CustomEvent = globalThis.CustomEvent
  || class { constructor(t) { this.type = t; } };

/* ----------------------------------------- real worker via Node bridge */
class TestWorker {
  constructor() {
    this.onmessage = null;
    this.onerror = null;
    this._w = new NodeWorker(new URL("./worker_bridge.mjs", import.meta.url));
    this._w.on("message", (data) => { if (this.onmessage) this.onmessage({ data }); });
    this._w.on("error", (err) => {
      if (this.onerror) this.onerror({ message: String((err && err.message) || err) });
    });
  }
  postMessage(msg) { this._w.postMessage(msg); }
  terminate() { this._w.terminate(); }
}
globalThis.Worker = TestWorker;

/* ---------------------------------------------------- main-thread fetch */
const jsonResponse = (body) => ({
  ok: true, status: 200, statusText: "OK",
  json: async () => body,
  headers: { get: () => "application/json" },
});
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith("/api/sites")) return jsonResponse([SITE]);
  if (u.startsWith("/api/catalog")) return jsonResponse(CATALOG);
  throw new Error("unexpected fetch " + u);
};

const tick = () => new Promise((r) => setTimeout(r, 0));
const failures = [];
const fail = (msg) => failures.push(msg);

/* ------------------------------------------------------------ test A */
async function prefsMustNotCancelNight() {
  const w = new TestWorker();
  const nightGen = 1, prefsGen = 2;
  const first = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ type: "timeout" }), 8000);
    w.onmessage = (ev) => {
      if (ev.data.gen === nightGen) { clearTimeout(timer); resolve(ev.data); }
    };
    w.postMessage({ gen: nightGen, type: "night", date: "2026-09-12",
                    tzMin: 120, lat: SITE.lat, lon: SITE.lon, elev: 0,
                    adv: false, profile: null, topProfile: null,
                    prefs: PREFS });
    w.postMessage({ gen: prefsGen, type: "prefs", ...PREFS });
  });
  if (first.type !== "night") {
    fail(`A: night build answered '${first.type}' after a prefs request; ` +
         "a preference change must never cancel the build");
  }
  w.terminate();
}

/* ------------------------------------------------------------ test B */
async function planReflectsNewPrefsAndBusyClears() {
  const { S } = await import(new URL("static/js/state.js", ROOT).href);
  const pc = await import(new URL("static/js/planning_client.js", ROOT).href);
  if (typeof pc.flushQueuedPrefs !== "function") {
    fail("B: planning_client.js must export flushQueuedPrefs() so a prefs " +
         "change queued during a build reaches the finished plan");
    return;
  }
  const night = await import(new URL("static/js/night.js", ROOT).href);

  S.cfg = { adv_mode: false, bortle: 4, moon_aware: false,
            filters: { broadband: true } };
  S.panos = [];
  S.catalog = CATALOG;
  S.minAlt = PREFS.minAlt;
  elements["#busy"].hidden = true;

  night.loadNight();
  await tick();
  await tick();
  await tick();                       // the build is in flight now
  S.minAlt = 89;                      // above every object's max altitude
  const queued = await pc.requestPrefsDerived();
  if (queued !== true) {
    fail(`B: a prefs change during a build must be queued (got ${queued})`);
  }
  for (let i = 0; i < 500 && elements["#busy"].hidden !== true; i++) await tick();
  if (!S.night) fail("B: the build's plan was lost");
  if (elements["#busy"].hidden !== true) fail("B: the busy chip was never cleared");
  if (S.night && S.night.objects.some(
        (o) => o.vis && o.vis.some(Boolean))) {
    fail("B: the finished plan ignored the preference change (objects still " +
         "visible at min-alt 89)");
  }
}

await prefsMustNotCancelNight();
await planReflectsNewPrefsAndBusyClears();
try { unlinkSync(CATALOG_FILE); } catch { /* best effort */ }

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("worker lifecycle contract held");
process.exit(0);      // the persistent test worker must not keep Node alive
