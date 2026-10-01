/*
 * FIX-2 regression: catalog identity across the worker boundary.
 *
 * Defect (CODE-REVIEW-0.6.20, DS2): the worker's catalog and the main-thread
 * metadata/payload caches were never invalidated, and bindWorkerNight checked
 * only the LIST LENGTH against the response - so a changed catalog (same
 * length, different objects) silently bound tracks to the wrong object id.
 *
 * Tests, all against the REAL planning_worker.js (Node bridge):
 *   A) the worker drops its retained catalog on a catalog-reset message: a
 *      second build after the catalog file changes must return the NEW ids;
 *   B) the client refuses to bind a response whose ids do not match the
 *      current catalog (falls back instead of mis-mapping);
 *   C) resetPlanningCaches() clears the client caches and the worker catalog,
 *      so the next build reflects the new catalog.
 *
 * Exit 0 = contract held; 1 = violated.
 */
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Worker as NodeWorker } from "node:worker_threads";

globalThis.window = globalThis;

const ROOT = new URL("../../", import.meta.url);
const CATALOG_FILE = join(tmpdir(), `ns-catalog-identity-${process.pid}.json`);
const entry = (id, ra, dec) => ({ id, primary_name: id, ra_hours: ra,
                                  dec_deg: dec, vmag: 5, otype: "galaxy" });
const LIST_A = [entry("a1", 0.7, 41.3), entry("b1", 5.6, -5.4),
                entry("c1", 18.0, 30.0)];
const LIST_B = [entry("a2", 1.7, 31.3), entry("b2", 6.6, -15.4),
                entry("c2", 19.0, 20.0)];
writeFileSync(CATALOG_FILE, JSON.stringify(LIST_A));
process.env.NS_TEST_CATALOG = CATALOG_FILE;

const SITE = { id: 1, name: "Home", lat: 52.5, lon: 13.4, elev: 40 };
const PREFS = { minAlt: 25, bortle: 4, moonAware: false,
                filters: { broadband: true } };
const tick = () => new Promise((r) => setTimeout(r, 0));
const failures = [];
const fail = (msg) => failures.push(msg);

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

function askNight(w, gen) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ type: "timeout" }), 8000);
    const prev = w.onmessage;
    w.onmessage = (ev) => {
      if (prev) prev(ev);
      if (ev.data.gen === gen) { clearTimeout(timer); resolve(ev.data); }
    };
    w.postMessage({ gen, type: "night", date: "2026-09-12", tzMin: 120,
                    lat: SITE.lat, lon: SITE.lon, elev: 0, adv: false,
                    profile: null, topProfile: null, prefs: PREFS });
  });
}

/* ------------------------------------------------------------ test A */
async function workerDropsCatalogOnReset() {
  const w = new TestWorker();
  const first = await askNight(w, 1);
  if (first.type !== "night" || first.ids.join(",") !== "a1,b1,c1") {
    fail(`A: first build ids ${first.ids || first.type} != a1,b1,c1`);
    w.terminate();
    return;
  }
  writeFileSync(CATALOG_FILE, JSON.stringify(LIST_B));
  w.postMessage({ type: "catalog-reset" });
  const second = await askNight(w, 2);
  if (second.type !== "night" || second.ids.join(",") !== "a2,b2,c2") {
    fail(`A: after catalog-reset the worker returned ${second.ids || second.type}; ` +
         "a stale worker catalog can be bound to the wrong object");
  }
  w.terminate();
}

/* ------------------------------------------------------------ test B */
/* A worker that answers with ids for a DIFFERENT catalog (same length). */
globalThis.Worker = class {
  constructor() {
    this.onmessage = null;
    this.onerror = null;
    this._t = null;
  }
  postMessage(msg) {
    if (msg.type !== "night") return;
    const n = 3, count = 2;
    const alt = new Float64Array(n * count).fill(123.456);
    const az = new Float64Array(n * count).fill(0);
    const i32 = () => new Int32Array(n).fill(-1);
    const f64 = () => new Float64Array(n).fill(0);
    const res = {
      gen: msg.gen, type: "night", n, count,
      ids: ["x1", "x2", "x3"],              // wrong catalog, same length
      alt, az,
      derived: { visBits: new Uint8Array(n * count),
                 aboveBits: new Uint8Array(n * count),
                 obsPct: new Int32Array(n), blockedN: new Int32Array(n),
                 score: f64(), scoreMoon: f64(),
                 bS: i32(), bE: i32(), aS: i32(), aE: i32() },
      meta: { site: { lat: SITE.lat, lon: SITE.lon, elev: 0 },
              date_local: "2026-09-12", tz_offset_min: 120,
              twilight_level: "astronomical", sunset: null, sunrise: null,
              dusk: null, dawn: null, moon_illum: 0.3, moon_age_days: 5,
              timeline: { t0: "2026-09-12T10:00:00", step_min: 6, n: count,
                          times_iso: ["2026-09-12T10:00:00",
                                      "2026-09-12T10:06:00"] },
              sun_alt: [0, 0], sun_az: [0, 0], moon_alt: [0, 0], moon_az: [0, 0] },
    };
    setTimeout(() => this.onmessage && this.onmessage({ data: res }), 0);
  }
  terminate() {}
};

async function clientRejectsMismatchedIds() {
  const { S } = await import(new URL("static/js/state.js", ROOT));
  const pc = await import(new URL("static/js/planning_client.js", ROOT));
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith("/api/sites")) return jsonResponse([SITE]);
    if (u.startsWith("/api/catalog")) return jsonResponse(LIST_A);
    throw new Error("unexpected fetch " + u);
  };
  S.cfg = { adv_mode: false, bortle: 4, moon_aware: false,
            filters: { broadband: true } };
  S.panos = [];
  S.catalog = LIST_A;
  S.minAlt = 25;
  const payload = await pc.buildClientNight({ date: "2026-09-12",
                                              siteId: 1, tzMin: 120 });
  const ids = payload.objects.map((o) => o.id).join(",");
  const crafted = payload.objects.some((o) => o.alt.some((v) => v === 123.456));
  if (ids !== "a1,b1,c1") fail(`B: bound ids ${ids} != a1,b1,c1`);
  if (crafted) {
    fail("B: a response with mismatched catalog ids was bound to the wrong " +
         "objects instead of being rejected");
  }
}

/* ------------------------------------------------------------ test C */
async function resetInvalidatesWorkerAndClient() {
  // restore the real worker bridge for the reset/rebuild flow
  globalThis.Worker = TestWorker;
  const { S } = await import(new URL("static/js/state.js", ROOT));
  const pc = await import(new URL("static/js/planning_client.js", ROOT));
  if (typeof pc.resetPlanningCaches !== "function") {
    fail("C: planning_client.js must export resetPlanningCaches() so a " +
         "catalog edit cannot be served from the stale caches");
    return;
  }
  writeFileSync(CATALOG_FILE, JSON.stringify(LIST_A));
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith("/api/sites")) return jsonResponse([SITE]);
    if (u.startsWith("/api/catalog")) return jsonResponse(S.catalog);
    throw new Error("unexpected fetch " + u);
  };
  S.catalog = LIST_A;
  const first = await pc.buildClientNight({ date: "2026-09-12", siteId: 1,
                                            tzMin: 120 });
  if (first.objects.map((o) => o.id).join(",") !== "a1,b1,c1") {
    fail("C: first build ids wrong: " + first.objects.map((o) => o.id));
  }
  writeFileSync(CATALOG_FILE, JSON.stringify(LIST_B));
  pc.resetPlanningCaches();
  S.catalog = LIST_B;
  const second = await pc.buildClientNight({ date: "2026-09-12", siteId: 1,
                                             tzMin: 120 });
  const ids = second.objects.map((o) => o.id).join(",");
  if (ids !== "a2,b2,c2") fail(`C: after reset the build returned ${ids}`);
}

const jsonResponse = (body) => ({
  ok: true, status: 200, statusText: "OK",
  json: async () => body,
  headers: { get: () => "application/json" },
});

await workerDropsCatalogOnReset();
await clientRejectsMismatchedIds();
await resetInvalidatesWorkerAndClient();
try { unlinkSync(CATALOG_FILE); } catch { /* best effort */ }

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("catalog identity contract held");
process.exit(0);
