/*
 * NightSeam core - catalog identity, type registry and search text (pure).
 *
 * Phase 2, concept 2. Type labels and colors are the WEB canonical palette
 * (owner decision, 0.6.17): the desktop shell adopts them too, so both apps
 * render one type vocabulary. The TYPE KEYS are the exact set the server seeds
 * and `app/catalog_data.py::TYPE_LABELS` validates against.
 */

/* CO-09 (0.6.32 review): these lookups are driven by `otype`, which a crafted
   setup bundle can set to ANY string (`app/database.py` stores it verbatim) -
   including `"toString"` or `"constructor"`. A plain object literal then returns
   an inherited `Object.prototype` member, so `X[t] || fallback` never falls back
   and the type cell renders native function source. Null-prototype maps make
   every inherited key genuinely absent. */
export const TYPE_LABELS = Object.assign(Object.create(null), {
  galaxy: 'Galaxy', open_cluster: 'Open cluster', globular: 'Globular cluster',
  nebula: 'Nebula', planetary: 'Planetary nebula', supernova_remnant: 'Supernova remnant',
  asterism: 'Asterism', star_cloud: 'Star cloud',
});

export const TYPE_COLORS = Object.assign(Object.create(null), {
  galaxy: '#ffd166',
  open_cluster: '#7cc4ff',
  globular: '#9be0ff',
  nebula: '#7bf1a8',
  planetary: '#8fe3d9',
  supernova_remnant: '#ff8fa3',
  asterism: '#c9b8ff',
  star_cloud: '#e0e6ff',
});

/** Custom targets are server-created records with the `u_` id prefix
 *  (`app/server.py` creates and deletes them only by that prefix). */
export function isCustomId(id) {
  return typeof id === 'string' && id.startsWith('u_');
}

/** Web search text: id, names, type, constellation, description and the CSV's
 *  own designation blob (`search`), lowercased and space-joined.
 *
 *  CD-02 (0.6.32 review, combined-repo era): `search` was NOT indexed here and
 *  the website build did not ship it at all, so 25 rows whose common name lives
 *  only in that column (`tarantula`, `coathanger`, `silver coin`, ...) were
 *  findable on the desktop but not on the website. (The old comment here claimed
 *  the desktop searched
 *  `app/catalog_data.py`'s blob - that file was deleted with the curated list in
 *  0.6.26, and `o.distance` no longer exists either.) */
export function searchBlob(o) {
  return [o.id, o.primary_name, o.messier, o.ngcic, o.other, o.otype,
          o.constellation, o.blurb, o.search]
    .filter(Boolean).join(' ').toLowerCase();
}

/** Wikipedia page titles for an object, best first (0.6.32).
 *
 * The catalogue carries no `wiki` field - it went with the curated list in
 * 0.6.26 - so a lookup keyed on the DISPLAY NAME lands on a disambiguation page
 * for every Messier row without a common name: `M101` is a disambiguation page
 * (motorways, highways, a rifle), while `Messier 101` is the galaxy. The
 * designation form is what the encyclopaedia actually files these objects under,
 * so try it first; `ngcic` (`NGC 5457`) resolves as well, and the display name
 * is the last resort (with a disambiguation guard at the fetch site).
 * `app/images.py` builds the same list for the desktop. */
export function wikiTitles(o) {
  const out = [];
  const push = (t) => {
    const s = String(t || '').trim();
    if (s && !out.includes(s)) out.push(s);
  };
  push(o.wiki);
  const m = String(o.messier || '').trim();
  const digits = m.replace(/[^0-9]/g, '');
  if (digits && /^m/i.test(m)) push(`Messier ${parseInt(digits, 10)}`);
  push(o.ngcic);
  push(o.primary_name);
  return out;
}

/* ------------------------- popularity TIERS -------------------------------
 * The popularity score stays a SILENT ranking input: its VALUE is never
 * rendered anywhere (owner rule). The catalog shows a TIER letter instead,
 * which carries the ordering without exposing the number.
 *
 * Tiers are RANK bands, deliberately not score bands. The score distribution is
 * extremely heavy-tailed - rank 1 = 166,350, rank 100 = 9,910, rank 250 = 1,163,
 * rank 1,500 = 19, and the last ~4,000 rows all sit at 1-4 - so ANY score
 * cut-off would drop almost the whole catalog into one bucket.
 *
 * Bands (owner decision, 0.6.30): A = the 100 most observed objects; B ends at
 * 250 so that A+B covers the standard-mode shortlist (`STANDARD_TOP_N` = 200)
 * with a margin; each later band is ~2.4x wider, which keeps A-F populated
 * instead of collapsing the tail.
 */
export const TIER_BANDS = [
  ['A', 100], ['B', 250], ['C', 600], ['D', 1500], ['E', 3500], ['F', Infinity],
];

/** The tier alphabet in order (A first), for sorting by index. */
export const TIER_ORDER = TIER_BANDS.map(([tier]) => tier);

/** Tier letter for a 1-based popularity rank; unknown/0 ranks land in F. */
export function tierForRank(rank) {
  const r = rank > 0 ? rank : Infinity;
  for (const [tier, top] of TIER_BANDS) if (r <= top) return tier;
  return 'F';
}

/** `id -> tier letter` for a catalog array. The ranking mirrors the SERVER's
 *  standard-mode rule exactly - score descending, then id ASCENDING - so the
 *  web tier and the desktop/server selection can never disagree about which
 *  object is #1. Rows with no popularity entry get F (the title says "no
 *  popularity data"), so the column always sorts A-F. */
export function assignPopularityTiers(objs) {
  const tiers = new Map();
  const scored = objs
    .filter((o) => (o.popularity || 0) > 0)
    .slice()
    .sort((a, b) => (b.popularity - a.popularity)
      || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  scored.forEach((o, i) => tiers.set(o.id, tierForRank(i + 1)));
  for (const o of objs) if (!tiers.has(o.id)) tiers.set(o.id, 'F');
  return tiers;
}
