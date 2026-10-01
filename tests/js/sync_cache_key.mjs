/*
 * FIX-3 regression: the synchronous client-planning cache key must cover the
 * timezone offset and the horizon profile CONTENT.
 *
 * Defect (CODE-REVIEW-0.6.20, DS3): the key was
 * [site.id, date, adv, panoId, pano.length], so on the sync fallback a same-date
 * build after a timezone change served the old zone's grid/windows, and a
 * repainted horizon (same pano id + 720 bins) served the old ceilings.
 *
 * Exit 0 = contract held; 1 = violated.
 */
globalThis.window = globalThis;

const SITE = { id: 1, name: "Home", lat: 52.5, lon: 13.4, elev: 40 };
const CATALOG = [
  { id: "a", primary_name: "A", ra_hours: 0.7, dec_deg: 41.3, vmag: 4,
    otype: "galaxy" },
];
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

const failures = [];
const fail = (msg) => failures.push(msg);

const { S } = await import(new URL("static/js/state.js",
                                   new URL("../../", import.meta.url)).href);
const pc = await import(new URL("static/js/planning_client.js",
                                new URL("../../", import.meta.url)).href);

S.cfg = { adv_mode: false, bortle: 4, moon_aware: false,
          filters: { broadband: true } };
S.catalog = CATALOG;
S.minAlt = 25;

const flat = (v) => new Array(720).fill(v);
S.panos = [{ id: "p1", active: true, profile: flat(-90), profile_top: [],
             block_above: false }];

// 1) same date, different timezone offset: the payload must be rebuilt
const a = await pc.buildClientNight({ date: "2026-09-12", siteId: 1,
                                      tzMin: 60 });
const b = await pc.buildClientNight({ date: "2026-09-12", siteId: 1,
                                      tzMin: 120 });
if (a.tz_offset_min !== 60 || b.tz_offset_min !== 120) {
  fail(`tz change served a stale payload: ${a.tz_offset_min} then ` +
       `${b.tz_offset_min}`);
}
if (a.timeline.t0 === b.timeline.t0) {
  fail("tz change must move the site-zoned grid anchor");
}

// 2) same pano id/length, different profile content: the ceilings must change
S.panos = [{ id: "p1", active: true, profile: flat(-90), profile_top: [],
             block_above: false }];
const c = await pc.buildClientNight({ date: "2026-09-12", siteId: 1,
                                      tzMin: 120 });
S.panos = [{ id: "p1", active: true, profile: flat(10), profile_top: [],
             block_above: false }];
const d = await pc.buildClientNight({ date: "2026-09-12", siteId: 1,
                                      tzMin: 120 });
const ceilC = c.objects[0].ceil[100], ceilD = d.objects[0].ceil[100];
if (!(ceilC === -90 && ceilD === 10)) {
  fail(`profile content change served stale ceilings: ${ceilC} then ${ceilD}`);
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("sync cache key contract held");
