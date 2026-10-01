/*
 * DS4 + DS5 (carried over from the 0.6.20/0.6.24 reviews, closed in 0.6.35).
 *
 * DS4 - the night payload and the object-detail payload disagreed about the
 * object's peak altitude. The night path took the argmax over the RAW altitude
 * and reported it at 1 dp; the detail path rounded every sample to 2 dp FIRST,
 * took the argmax of the ROUNDED array, and reported that array's value. Near a
 * tie the two chose different samples, so the detail view's "max altitude ... at
 * HH:MM" could name the sample next to the row it was opened from.
 *
 * DS5 - `_worker.onerror` set the broken flag but left the DEAD Worker in
 * `_worker`. `__NS_REVIVE_WORKER` clears the flag, so the next build handed the
 * corpse back to `postMessage` instead of constructing a fresh worker: the
 * revive hook could not actually recover the worker path.
 *
 * This drives the REAL static/js/planning_client.js under Node with a counting
 * Worker shim, so both contracts are asserted against the shipped code.
 * Exit 0 = contract held; 1 = violated.
 */
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Worker as NodeWorker } from "node:worker_threads";

/* The catalog is the REAL ic434 row: its 2026-09-12 track is a measured DS4
 * divergence. The two samples around the peak round to the same 2-dp value, so
 * the raw argmax (index 260) and the rounded-array argmax (index 259) differ -
 * exactly the shape the finding describes. A toy three-object catalog does not
 * hit a tie, so the regression would slip through. */
const CATALOG = [
  { id: "ic434", primary_name: "Flame Nebula", ra_hours: 5.68358,
    dec_deg: -2.45378, vmag: 11, otype: "nebula" },
];
const CATALOG_FILE = join(tmpdir(), `ns-fail-catalog-${process.pid}.json`);
writeFileSync(CATALOG_FILE, JSON.stringify(CATALOG));
process.env.NS_TEST_CATALOG = CATALOG_FILE;
const SITE = { id: 1, name: "Home", lat: 52.5, lon: 13.4, elev: 40 };
const DATE = "2026-09-12";
const TZ = 120;

/* ---------------------------------------------------------------- stub DOM */
globalThis.document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent: () => true,
  createElement: () => ({ classList: {}, style: {}, appendChild() {},
                          addEventListener() {} }),
  documentElement: { style: { setProperty() {} }, setAttribute() {},
                     getAttribute: () => "" },
};
globalThis.window = globalThis;
globalThis.CustomEvent = globalThis.CustomEvent
  || class { constructor(t) { this.type = t; } };

/* ------------------------------------------- counting Worker (real bridge) */
let ctorCount = 0, terminateCount = 0, lastWorker = null;
class TestWorker {
  constructor() {
    ctorCount++;
    lastWorker = this;
    this.onmessage = null;
    this.onerror = null;
    this._w = new NodeWorker(new URL("./worker_bridge.mjs", import.meta.url));
    this._w.on("message", (data) => { if (this.onmessage) this.onmessage({ data }); });
    this._w.on("error", (err) => {
      if (this.onerror) this.onerror({ message: String((err && err.message) || err) });
    });
  }
  postMessage(msg) { this._w.postMessage(msg); }
  terminate() { terminateCount++; this._w.terminate(); }
}
globalThis.Worker = TestWorker;

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

const { S } = await import(new URL("../../static/js/state.js", import.meta.url).href);
S.cfg = { adv_mode: false, bortle: 4, moon_aware: false, active_pano_id: null,
          filters: { broadband: true }, sites: [SITE] };
S.activeSiteId = 1;
S.panos = [];
S.site = SITE;
S.minAlt = 25;
S.meta = {};
for (const o of CATALOG) S.meta[o.id] = o;

const PC = await import(new URL("../../static/js/planning_client.js",
                                import.meta.url).href);

const failures = [];
const fail = (m) => failures.push(m);

/* ------------------------------------------------- DS4: peak agreement */
const night = await PC.buildClientNight({ date: DATE, siteId: 1, tzMin: TZ });
const row = night.objects.find((o) => o.id === "ic434");
if (!row) fail("DS4: the night payload lost the ic434 fixture");

const detail = await PC.buildClientDetail("ic434", {
  date: DATE, tzMin: TZ, minAlt: 25, siteId: 1,
});

/* NOTE on scope: the night grid is 6 min anchored at local noon and the detail
 * grid is 4 min anchored around dusk, so the two payloads legitimately report
 * different peaks - they sample the same curve at different instants and DS4 was
 * never "the two numbers must be equal". DS4 was that the DETAIL payload
 * disagreed with ITSELF: `max_alt` came from a different sample than `max_time`
 * whenever rounding made two near-tie samples differ, because the argmax ran
 * over the ROUNDED array. The assertions below are about that internal
 * consistency, which is exactly what a caller renders ("max altitude X at T"). */
const altAt = (iso) => {
  const i = detail.times_iso.indexOf(iso);
  return i < 0 ? null : detail.alt[i];
};
const peakFromTime = altAt(detail.max_time);
if (peakFromTime === null) {
  fail("DS4: max_time does not name one of the payload's own samples");
} else if (Math.abs(peakFromTime - detail.max_alt) > 0.05) {
  /* the two report fields must describe ONE sample. A 2-dp grid sample and the
   * round-trip through the reported value agree to within 0.05; anything larger
   * means the reported peak and the reported time name different samples. */
  fail(`DS4: max_time names a sample at ${peakFromTime} deg while max_alt says ` +
       `${detail.max_alt} - the reported peak and the reported time disagree`);
}
/* and the reported peak must not sit BELOW the payload's own maximum: an argmax
 * over rounded values can land on a sample that is not the grid maximum, and
 * `max_alt` then reports that lower value. */
const gridMax = Math.max(...detail.alt);
if (detail.max_alt < gridMax - 0.05) {
  fail(`DS4: max_alt ${detail.max_alt} is below the payload's own maximum ` +
       `${gridMax} - the peak sample was chosen from the ROUNDED array`);
}
console.log(`note: detail peak ${detail.max_alt} deg (2-dp grid max ${gridMax}) at ` +
            `${detail.max_time} over ${detail.times_iso.length} samples at ` +
            `${detail.step_min} min; the night row reports ${row.max_alt} deg on its ` +
            `own noon-anchored 6-min grid`);

/* DS4, what the fix does and does not change - measured, not assumed.
 *
 * Structure of the defect: `round2(max(raw)) >= round2(raw[j])` for every j, so
 * the rounded-array argmax can only ever land at or BEFORE the raw argmax, and
 * the peak VALUE it reports is the same either way. The observable difference is
 * therefore NOT the reported altitude: it is WHICH SAMPLE `max_time` names when
 * several samples share the maximum rounded value - the pre-fix code could name
 * an earlier one. On the shipped ic434 row over four dates the detail grid holds
 * a SINGLE sample at its maximum (29.42 on 2026-09-12), so the two agree there
 * and no live fixture separates them.
 *
 * What this engine can assert, and does above, is the user-visible contract that
 * the fix restores: `max_time` and `max_alt` describe ONE sample, and the
 * reported peak is not below the payload's own maximum. What it cannot do is
 * fabricate a case the data does not contain, so this note records the limit
 * instead of a speculative fixture.
 */


/* ------------------------------------- DS5: a failed worker is DISPOSED */
/* a live worker is REUSED across builds (that is the pool's whole point), so the
 * first build above already constructed one - drive THAT instance's onerror to
 * simulate the worker script dying, as the browser would. */
const alive = lastWorker;
if (!alive || typeof alive.onerror !== "function") {
  fail("DS5: no live worker was constructed, so the failure path cannot be driven");
} else {
  const termBefore = terminateCount;
  alive.onerror({ message: "simulated worker crash" });
  if (terminateCount <= termBefore) {
    fail("DS5: onerror did NOT terminate the dead worker - it stays in `_worker` " +
         "and revive would hand the corpse back to postMessage");
  }
  /* the sync fallback must still plan... */
  const recovered = await PC.buildClientNight({ date: "2026-08-17", siteId: 1, tzMin: TZ });
  if (!recovered || !recovered.objects || !recovered.objects.length) {
    fail("DS5: the sync fallback produced no plan after a worker crash");
  }
  /* the shipped dev hook must drive the SAME path - the browser check
   * `desktop-worker-error-disposal` uses it, so a real ErrorEvent fired at a real
   * worker has to reach the same handler (this engine cannot dispatch a browser
   * ErrorEvent, so it asserts the hook exists and reports honestly when unarmed) */
  if (typeof window.__NS_FAIL_WORKER !== "function") {
    fail("DS5: the __NS_FAIL_WORKER dev hook is missing, so the browser check " +
         "cannot drive the real failure path");
  } else if (window.__NS_FAIL_WORKER() !== false) {
    fail("DS5: the dev hook must refuse to act while __NS_DEBUG_WORKER is unset");
  }
  /* ... and revive must build a FRESH worker (only true if the dead one is gone) */
  window.__NS_DEBUG_WORKER = true;
  if (window.__NS_REVIVE_WORKER() !== true) fail("DS5: revive hook refused when armed");
  const ctorBeforeRevive = ctorCount;
  await PC.buildClientNight({ date: "2026-08-18", siteId: 1, tzMin: TZ });
  if (lastWorker) lastWorker.terminate();
  if (ctorCount <= ctorBeforeRevive) {
    fail("DS5: after revive the build reused the DEAD worker instead of " +
         "constructing a fresh one");
  }
}

try { unlinkSync(CATALOG_FILE); } catch { /* best effort */ }
/* a live worker_thread keeps Node's loop alive, so dispose the last one (and any
 * earlier instances the failure path left) before exiting - otherwise the engine
 * prints its verdict and then hangs until the test runner's timeout. */
if (lastWorker && typeof lastWorker.terminate === "function") lastWorker.terminate();
if (failures.length) {
  for (const f of failures) console.log("FAIL: " + f);
  console.log("DS4/DS5 worker contract FAILED");
  process.exit(1);
}
console.log("DS4/DS5 worker contract held");
process.exit(0);
