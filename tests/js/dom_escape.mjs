/*
 * BUG-002: the desktop shell must never let imported data become MARKUP.
 *
 * Two independent halves, both pinned here:
 *   1. the attribute sinks. A custom target id is imported (`u_` + arbitrary
 *      text before the fix) and an image `imagetyp` is free-form TEXT in the DB;
 *      both used to be interpolated into HTML ATTRIBUTES unescaped, so
 *      `u_x" onmouseover="globalThis.__pwned=1` rendered as an executable
 *      handler in static/js/setup.js (and the archive's type rollup reached a
 *      `title`). The import boundary now refuses/normalizes those values
 *      (tests/test_backup.py) - this engine proves the RENDERING side is safe
 *      even if a hostile value is already stored (an older library, a
 *      hand-edited DB), i.e. the injection cannot come back through a new sink.
 *   2. the inline event handlers. `onerror=` attributes in targets.js/tonight.js
 *      forced `script-src 'unsafe-inline'` in the desktop CSP, which is what
 *      made half (1) executable rather than inert. They are listeners now.
 *
 * The real modules run under Node with a stub DOM; the captured innerHTML is
 * the string the browser would parse, so an unescaped quote shows up verbatim.
 */
const EVIL_ID = 'u_x" onmouseover="globalThis.__pwned=1';
const EVIL_TYPE = 'light" onmouseover="globalThis.__pwned=2';

const els = {};
const makeEl = (sel) => ({
  sel, _l: {}, _q: {},
  innerHTML: '', textContent: '', value: '', checked: false, disabled: false,
  selectedOptions: [],
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  addEventListener(type, fn) { (this._l[type] ||= []).push(fn); },
  dispatch(type, ev = {}) {
    (this._l[type] || []).forEach((f) => f({ target: this, preventDefault() {}, ...ev }));
  },
  querySelector(s) { return (this._q[s] ||= makeEl(s)); },
  querySelectorAll: () => [],
  insertAdjacentHTML() {}, appendChild() {}, focus() {},
  dataset: {}, style: {},
});
globalThis.document = {
  querySelector(sel) { return (els[sel] ||= makeEl(sel)); },
  querySelectorAll: () => [],
  addEventListener() {},
  dispatchEvent() {},
  createElement: () => makeEl('created'),
  body: { prepend() {} },
  documentElement: { setAttribute() {}, getAttribute() { return ''; } },
};
globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);

const { S } = await import(new URL('../../static/js/state.js', import.meta.url).href);
const T = await import(new URL('../../static/js/targets.js', import.meta.url).href);
const N = await import(new URL('../../static/js/tonight.js', import.meta.url).href);

const fails = [];
const check = (cond, label) => { if (!cond) fails.push(label); };

/* A hostile value must never appear as MARKUP: the only acceptable occurrences
 * are entity-escaped (`&quot;`, `&lt;`, `&amp;`). `leaks()` looks for the raw
 * characters that would end an attribute or open a tag. */
const leaks = (html) => {
  const raw = html.replace(/&(amp|lt|gt|quot|#39|#x27);/g, '');
  return raw.includes('onmouseover="') || raw.includes('<script');
};

/* ---------------------- targets.js: the row markup ---------------------- */
S.tIdx = 0;
S.cfg = { size_unit: 'arcmin', favorites: [EVIL_ID], min_vis: 0 };
S.catalog = [{ id: EVIL_ID, primary_name: 'Evil', otype: 'galaxy' }];
S.meta = { [EVIL_ID]: S.catalog[0] };
S.minAlt = 20;
S.night = {
  objects: [{
    id: EVIL_ID, max_alt: 60, obs_pct: 40, best: [0, 5],
    alt: [30], ceil: [-90], vis: [1],
  }],
  timeline: { n: 1, times_iso: ['2026-03-14T22:00:00'] },
  dusk: '2026-03-14T19:00:00', dawn: '2026-03-15T05:00:00',
  moon_illum: 0.25,
};
els['#fSearch'] = makeEl('#fSearch'); els['#fSearch'].value = '';
els['#fType'] = makeEl('#fType'); els['#fType'].value = '';
els['#fMag'] = makeEl('#fMag'); els['#fMag'].value = '20';
els['#fUnblocked'] = makeEl('#fUnblocked'); els['#fConst'] = makeEl('#fConst');
els['#fConst'].value = '';
els['#fSort'] = makeEl('#fSort'); els['#fSort'].value = 'name';
T.renderTargets();
const rows = els['#targetRows'].innerHTML;
check(!leaks(rows), 'targets.js: a hostile id leaked into the row markup');
check(rows.includes('data-id="u_x&quot; onmouseover=&quot;'), 
      'targets.js: the row id is not attribute-escaped');
check(!rows.includes('onerror='), 'targets.js: an inline handler survived');

/* ---------------------- tonight.js: the pick cards ---------------------- */
els['#topPicks'] = makeEl('#topPicks');
els['#laterList'] = makeEl('#laterList');
els['#tonightCards'] = makeEl('#tonightCards');
els['#nowHeading'] = makeEl('#nowHeading');
N.renderTonight();
const cards = els['#topPicks'].innerHTML + els['#laterList'].innerHTML;
check(!leaks(cards), 'tonight.js: a hostile id leaked into the card markup');
check(!cards.includes('onerror='), 'tonight.js: an inline handler survived');

/* ---------------------- the CSP coupling ---------------------- */
const fs = await import('node:fs');
for (const name of ['targets.js', 'tonight.js', 'setup.js', 'archive.js',
                    'pano.js', 'detail.js', 'skyview.js', 'skymap.js']) {
  const src = fs.readFileSync(new URL(`../../static/js/${name}`, import.meta.url), 'utf8');
  check(!/\son[a-z]+\s*=\s*["']/.test(src),
        `${name} carries an inline event handler attribute`);
}

if (fails.length) {
  console.log('BUG-002 escape check FAILED:');
  for (const f of fails) console.log('  - ' + f);
  process.exit(1);
}
console.log('BUG-002: desktop attribute sinks escape hostile values '
            + 'and no inline handler attribute remains');
