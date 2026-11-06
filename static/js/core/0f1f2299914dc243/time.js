/*
 * NightSeam core - timezone arithmetic (pure).
 *
 * Phase 2, concept 3. IANA-zone resolution uses the host's Intl database (an
 * environment capability, like Math - not DOM, storage or network); shells pass
 * instants/offsets and keep their own formatting and zone-selection state.
 *
 * Convention: offsets are minutes EAST of UTC (the API/planning convention,
 * `app/planning.local_utc` does `local - tz_min`). That is the OPPOSITE sign
 * of `Date#getTimezoneOffset()` (minutes WEST).
 */

/** Minutes EAST of UTC for an IANA zone at epoch ms (DST-aware).
 *  Parses Intl's long/short offset names ("GMT+02:00", "GMT+5:30", "GMT");
 *  returns null for an unknown/missing zone. */
export function offsetEastMin(tzName, ms) {
  if (!tzName) return null;
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tzName, timeZoneName: 'longOffset', hour12: false,
    }).formatToParts(new Date(ms));
    const p = parts.find((x) => x.type === 'timeZoneName');
    if (!p || !p.value) return null;
    if (p.value === 'GMT' || p.value === 'UTC') return 0;
    const m = p.value.match(/GMT([+-])(\d{1,2}):?(\d{2})?/);
    if (!m) return null;
    const sign = m[1] === '-' ? -1 : 1;
    return sign * (Number(m[2]) * 60 + Number(m[3] || 0));
  } catch { return null; }
}

/** Minutes EAST of UTC in effect at local noon of `dateStr` (YYYY-MM-DD) in
 *  `tzName`, via the installed tz database. Falls back to the machine offset
 *  when the zone is absent or the date is malformed (the desktop rule).
 *
 *  Local noon is the instant T with `T + offset(T) === noonUTC`, so T = noonUTC
 *  - offset(T); solved by fixed-point iteration.
 *
 *  DE-02 (0.6.32 review): this used `+ off`, which probes noonUTC + 2*offset -
 *  up to ~26 h away from local noon - so a DST boundary falling inside that
 *  window was read from the WRONG side and the desktop day grid anchored on the
 *  post-transition offset. Measured before the fix: Australia/Lord_Howe
 *  2026-10-03 returned +11:00 instead of +10:30, Australia/Adelaide 2026-10-03
 *  +10:30 instead of +09:30 (both 30 min wrong, exactly on their transition
 *  date). The review attributed this to the `:30` being dropped by the
 *  `longOffset` regex - that part is FALSE (the regex parses +10:30, +05:30 and
 *  +05:45 correctly); the sign was the real cause. Whole-hour zones happened to
 *  survive because their boundary sat outside that shifted span. */
export function zoneOffsetAtLocalNoon(tzName, dateStr) {
  const [y, m, d] = String(dateStr || '').split('-').map(Number);
  if (!y || !m || !d) return -new Date().getTimezoneOffset();
  if (!tzName) return -new Date(y, m - 1, d, 12).getTimezoneOffset();
  const machine = () => -new Date(y, m - 1, d, 12).getTimezoneOffset();
  const noonUTC = Date.UTC(y, m - 1, d, 12);
  let off = offsetEastMin(tzName, noonUTC);
  if (off == null) return machine();
  for (let i = 0; i < 4; i++) {
    const next = offsetEastMin(tzName, noonUTC - off * 60000);
    if (next == null) return machine();
    if (next === off) break;
    off = next;
  }
  return off;
}

/** A wall-clock Date (site zone, read through browser-local getters) is the
 *  UTC instant shifted by `offsetEast + machineWest` minutes. `machineWest` is
 *  the host's `date.getTimezoneOffset()` at that instant. */
export function wallFromUtcMs(ms, offsetEast, machineWest) {
  return ms + (offsetEast + machineWest) * 60000;
}

/** Inverse of wallFromUtcMs: interpret a wall-clock Date as a UTC instant. */
export function utcFromWallMs(ms, offsetEast, machineWest) {
  return ms - (offsetEast + machineWest) * 60000;
}
