/*
 * Deterministic regression harness for the night-loader supersession invariant:
 *
 *   An obsolete night calculation must never commit its payload to S.night,
 *   repaint, or clear the busy indicator - even when its continuation resumes
 *   AFTER a newer load has started.
 *
 * Core-only planning (Phase C): the loader builds the payload from the shared
 * core (planning_client.js). Node has no Worker, so this drives the real
 * synchronous client path against a stub fetch that HOLDS the /api/sites
 * response, which is what lets a superseded load finish late.
 *
 * The bug it pins: static/js/night.js checked `ctl !== _nightCtl` too late -
 * stale payloads repainted the whole UI with the previous date's data.
 *
 * Exit code 0 = invariant held; 1 = violated.
 */
const ROOT = new URL("../../", import.meta.url);

// ---------------------------------------------------------------- stub DOM
const elements = {
  "#siteSelect": { options: [{}, {}], value: "1" },
  "#dateInput": { value: "2026-09-10" },
  "#timeSlider": { max: 0 },
  "#busy": {
    hidden: true,          // true == the "hidden" class is present == not busy
    classList: {
      toggle(_cls, force) { elements["#busy"].hidden = !!force; },
    },
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
  createElement: () => ({ classList: {}, style: {}, appendChild() {}, addEventListener() {} }),
  documentElement: { style: { setProperty() {} } },
};
globalThis.window = globalThis;
globalThis.CustomEvent = globalThis.CustomEvent || class { constructor(t) { this.type = t; } };

// ------------------------------------------------------------- stub fetch
// Every request is held until released, so two loads can interleave.
const pending = [];
globalThis.fetch = (url) => new Promise((resolve) => { pending.push({ url, resolve }); });
const tick = () => new Promise((r) => setTimeout(r, 0));
const jsonResponse = (body) => ({
  ok: true, status: 200, statusText: "OK",
  json: async () => body,
  headers: { get: () => "application/json" },
});

const { S } = await import(new URL("static/js/state.js", ROOT).href);
const night = await import(new URL("static/js/night.js", ROOT).href);

const SITE = { id: 1, name: "Home", lat: 52.5, lon: 13.4, elev: 40 };
S.cfg = { adv_mode: false, min_useful_alt: 25, moon_aware: false, bortle: 4 };
S.panos = [];
S.catalog = [                                  // skips the catalog fetch
  { id: "m31", primary_name: "M31", ra_hours: 0.712, dec_deg: 41.27 },
  { id: "m42", primary_name: "M42", ra_hours: 5.588, dec_deg: -5.39 },
];

const failures = [];

// ---------------------------------------------- load A, superseded by B
night.loadNight();                            // schedules doLoadNight(0)
await tick();
await tick();                                 // paint frame, then the sites fetch
if (pending.length !== 1) failures.push(`expected A's sites fetch, got ${pending.length}`);

night.loadNight();                            // the user changes the date: load B
await tick();
await tick();                                 // paint frame, then B's sites fetch
if (pending.length !== 2) failures.push(`expected B's sites fetch, got ${pending.length}`);

// release the OBSOLETE load first: it computes a payload but must not commit
pending.shift().resolve(jsonResponse([SITE]));
for (let i = 0; i < 10; i++) await tick();
if (S.night) failures.push("a superseded load committed S.night");

// now release the current load; it commits and clears busy
pending.shift().resolve(jsonResponse([SITE]));
for (let i = 0; i < 10; i++) await tick();
if (!S.night) {
  failures.push("the current load failed to commit");
} else if (S.night.site.lat !== SITE.lat) {
  failures.push("S.night holds the wrong payload");
}
if (elements["#busy"].hidden !== true) {
  failures.push("the busy indicator was left visible after the current load finished");
}

// --------------------------- and a further plain load still commits + clears
night.loadNight();
await tick();
await tick();
if (pending.length !== 1) failures.push(`expected the next sites fetch, got ${pending.length}`);
else pending.shift().resolve(jsonResponse([SITE]));
for (let i = 0; i < 10; i++) await tick();
if (!S.night) failures.push("a subsequent load failed to commit");
if (elements["#busy"].hidden !== true) failures.push("busy not cleared after a normal load");

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("supersession invariant held");
