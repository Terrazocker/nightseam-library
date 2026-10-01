/*
 * Desktop DOM-handler wiring checks (headless browsers cannot run in this
 * sandbox, so the real modules are driven through a minimal stub DOM):
 *   E5 - header.js must re-derive the Bortle default min altitude live and let
 *        an explicitly set value win afterwards;
 *   E8 - main.js must scrub by one 6-min night-grid sample (Shift = 60 min),
 *        clamped to the slider range.
 * Prints one marker line per invariant; tests/test_js_behaviour.py asserts
 * them. The relative /api/state fetch fails on purpose - no server is running
 * in Node; main.js already handles that (console.error + banner).
 */
const els = {};
const keydown = [];
const makeEl = (sel) => ({
  sel, _l: {}, _q: {},
  addEventListener(type, fn) { (this._l[type] ||= []).push(fn); },
  dispatch(type, ev = {}) {
    (this._l[type] || []).forEach((f) => f({ target: this, preventDefault() {}, ...ev }));
  },
  value: '', textContent: '', checked: false, max: '20', min: '0',
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  querySelector(s) { return (this._q[s] ||= makeEl(s)); },
  querySelectorAll: () => [],
  dataset: {}, style: {},
  reset() { this._reset = true; },
  focus() {},
  appendChild() {},
  append(...nodes) { this._appended = [...(this._appended || []), ...nodes]; },
  setAttribute(name, value) { (this._attrs ||= {})[name] = String(value); },
  options: [],
});
globalThis.document = {
  querySelector(sel) {
    if (sel.includes('.modal')) return null;      // no modal open
    return (els[sel] ||= makeEl(sel));
  },
  querySelectorAll: () => [],
  addEventListener(type, fn) { if (type === 'keydown') keydown.push(fn); },
  dispatchEvent() {},
  createElement: () => makeEl('created'),
  body: {
    dataset: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    prepend() {},
  },
  documentElement: { setAttribute() {}, getAttribute() { return ''; } },
};
globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);

const { S } = await import(new URL('../../static/js/state.js', import.meta.url).href);
const D = await import(new URL('../../static/js/derive.js', import.meta.url).href);
const H = await import(new URL('../../static/js/header.js', import.meta.url).href);

/* ------------------------------- E5 ------------------------------- */
S.cfg = { min_useful_alt: 25, min_useful_alt_explicit: false, bortle: 4, sites: [] };
S.minAlt = D.effectiveMinAlt(S.cfg);
H.bindHeader();
const bortle = els['#bortle'], mua = els['#minUsefulAlt'];
const label = () => document.querySelector('#muaVal').textContent;

bortle.value = '7'; bortle.dispatch('input');
const auto7 = S.minAlt === 31 && mua.value == 31 && label() === '31\u00B0';
mua.value = '30'; mua.dispatch('input');
bortle.value = '9'; bortle.dispatch('input');
const explicit = S.minAlt === 30 && label() === '30\u00B0';
console.log(auto7 ? 'E5 handler: bortle re-derives min-alt held'
                  : `E5 handler: bortle FAILED (minAlt=${S.minAlt} slider=${mua.value})`);
console.log(explicit ? 'E5 handler: explicit min-alt survives bortle held'
                     : `E5 handler: explicit FAILED (minAlt=${S.minAlt} label=${label()})`);

/* ------------------------------- E8 ------------------------------- */
await import(new URL('../../static/js/main.js', import.meta.url).href);
const iso = Array.from({ length: 21 }, (_, i) =>
  `2026-09-10T${String((20 + Math.floor(i * 6 / 60)) % 24).padStart(2, '0')}:` +
  `${String((i * 6) % 60).padStart(2, '0')}:00`);
S.night = { timeline: { times_iso: iso, n: 21, step_min: 6 }, objects: [] };
els['#timeSlider'].max = '20';
const fire = (key, shiftKey = false) => {
  for (const h of keydown) h({ key, shiftKey, target: { tagName: 'BODY' }, preventDefault() {} });
};
S.tIdx = 5; fire('ArrowRight');
const r1 = S.tIdx === 6;
fire('ArrowLeft');
const r2 = S.tIdx === 5;
fire('ArrowRight', true);
const r3 = S.tIdx === 15;
S.tIdx = 19; fire('ArrowRight', true);
const r4 = S.tIdx === 20;
console.log(r1 && r2 && r3 && r4
  ? 'E8 handler: 6/60-min steps + clamp held'
  : `E8 handler: FAILED ${JSON.stringify({ r1, r2, r3, r4, tIdx: S.tIdx })}`);

/* ------------------------------- E9 ------------------------------- */
// CORRECTION 2: the scrub defaults to the WHOLE night (sunset..sunrise) and
// only widens to the whole grid with view_full_day; SCORING still clips to the
// dusk..dawn dark window (nightIndexRange), and the selected sample is clamped
// when the range shrinks (e.g. toggling the night range back on).
const iso2 = Array.from({ length: 21 }, (_, i) =>
  `2026-09-10T${String(20 + Math.floor(i * 6 / 60)).padStart(2, '0')}:` +
  `${String((i * 6) % 60).padStart(2, '0')}:00`);
S.night = { timeline: { times_iso: iso2, n: 21, step_min: 6 }, objects: [] };
S.night.dusk = iso2[3];      // SCORING window (dusk..dawn)
S.night.dawn = iso2[17];
S.night.sunset = iso2[1];    // SCRUB window (sunset..sunrise)
S.night.sunrise = iso2[19];
S.cfg = { ...(S.cfg || {}), bortle: 4, view_full_day: false };
const slider9 = els['#timeSlider'];
S.tIdx = 10;
H.applyScrubRange();
const nightWin = slider9.min === '1' && slider9.max === '19' && S.tIdx === 10;
const scoring = JSON.stringify(D.nightIndexRange()) === JSON.stringify([3, 17]);
S.cfg.view_full_day = true;
H.applyScrubRange();
const fullWin = slider9.min === '0' && slider9.max === '20';
S.cfg.view_full_day = false;
S.tIdx = 0; H.applyScrubRange();
const clampLo = S.tIdx === 1;
S.tIdx = 20; H.applyScrubRange();
const clampHi = S.tIdx === 19;
console.log(nightWin && scoring && fullWin && clampLo && clampHi
  ? 'E9 handler: scrub window (sunset..sunrise) + scoring window held'
  : `E9 handler: FAILED ${JSON.stringify({ nightWin, scoring, fullWin, clampLo, clampHi, min: slider9.min, max: slider9.max, tIdx: S.tIdx })}`);

/* ------------------------------- DE-01 ------------------------------- */
// Adding a custom target must REBUILD the plan, exactly like the sibling
// mutators (site add/use/delete) that already end in loadNight(): with the
// caches dropped but no rebuild, the new id never reaches S.night, so Targets/
// Tonight/Sky keep the pre-add snapshot while the counter has already grown.
// The plan is rebuilt for real by planning_client's synchronous path, so the
// observable is the contract itself: the new id present in S.night.objects.
const iso3 = Array.from({ length: 241 }, (_, i) =>
  `2026-09-10T${String(Math.floor(i / 10) % 24).padStart(2, '0')}:` +
  `${String((i * 6) % 60).padStart(2, '0')}:00`);
S.catalog = [];
S.meta = {};
S.panos = [];
S.filteredIds = [];
S.cfg = { ...(S.cfg || {}), bortle: 4, adv_mode: false, sites: [] };
S.activeSiteId = 1;
S.minAlt = 25;
S.night = { timeline: { times_iso: iso3, n: 241, step_min: 6 }, objects: [] };
const siteSelect = document.querySelector('#siteSelect');
siteSelect.options = [{ value: '1' }];
siteSelect.value = '1';
document.querySelector('#dateInput').value = '2026-09-10';
globalThis.fetch = async (url) => {
  const u = String(url);
  const body = u.includes('/api/catalog/custom') ? { id: 'u_pin' }
    : u.includes('/api/catalog') ? [{ id: 'u_pin', primary_name: 'Pin',
                                      ra_hours: 1, dec_deg: 1, otype: 'galaxy' }]
    : u.includes('/api/sites') ? [{ id: 1, name: 'Home', lat: 50, lon: 7.8, elev: 150 }]
    : [];
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
};
const { bindSetup } = await import(new URL('../../static/js/setup.js', import.meta.url).href);
bindSetup();
const customForm = els['#customForm'] || document.querySelector('#customForm');
// the real form exposes its named controls as properties (f.ra.value, ...)
for (const n of ['name', 'ra', 'dec', 'otype', 'vmag']) {
  if (!customForm[n]) customForm[n] = makeEl(`#customForm [name=${n}]`);
}
customForm.name.value = 'Pin';
customForm.ra.value = '1';
customForm.dec.value = '1';
customForm.otype.value = 'galaxy';
customForm.vmag.value = '';
customForm.dispatch('submit');
await new Promise((r) => setTimeout(r, 400));       // let the deferred load land
const planIds = (S.night && S.night.objects || []).map((o) => o.id);
console.log(planIds.includes('u_pin')
  ? 'DE-01 handler: custom target add rebuilds the plan held'
  : `DE-01 handler: FAILED (new target absent from the plan: ${JSON.stringify(planIds)})`);

process.exit(0);
