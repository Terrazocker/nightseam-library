/*
 * NightSeam core - visibility predicates and the minimum-altitude rule (pure).
 *
 * Phase 2, concept 4. This is the ONE predicate behind every "can I see it?"
 * decision on both shells (desktop derive.js, web nightrank.js/detail loop):
 * above the minimum altitude AND above the obstruction ceiling AND below the
 * sky-mode upper ceiling (ceilTop; absent = no upper limit, like the 90.5
 * sentinel). No Python implementation is authoritative - the server ships raw
 * tracks only and the clients derive visibility.
 */

/** Selected-time sky visibility for a body (Sun, Moon or catalog object): it
 *  is a visible sky object only while its APPARENT altitude is above the
 *  horizon. The chart's Sun/Moon used to be drawn down to a fixed -6 deg
 *  (civil-twilight) floor, so they appeared below the horizon near dusk/dawn;
 *  the astronomy is the only rule. */
export function aboveHorizon(apparentAltDeg) {
  return Number.isFinite(apparentAltDeg) && apparentAltDeg > 0;
}

/** The one per-sample visibility predicate. `ceilTop` absent/undefined means
 *  "no upper ceiling" (the 90.5 sentinel the desktop uses). */
export function sampleVisible(alt, minAlt, ceil, ceilTop) {
  return alt >= minAlt && alt > ceil && alt < (ceilTop ?? 90.5);
}

/** Stable reason codes for explaining why a target is not usable at one
 * sample. The shells may translate these into local prose, but the precedence
 * and vocabulary belong to the shared core. */
export const VISIBILITY_REASON = Object.freeze({
  VISIBLE: "visible",
  BELOW_HORIZON: "below_horizon",
  BELOW_MINIMUM_ALTITUDE: "below_minimum_altitude",
  BLOCKED_BY_HORIZON: "blocked_by_horizon",
  OUTSIDE_SELECTED_WINDOW: "outside_selected_window",
  NEVER_RISES: "never_rises",
  NO_USABLE_WINDOW: "no_usable_window",
});

/** Explain one sample using the exact same predicate as sampleVisible().
 * `inWindow` is explicit because selected-time UI and night-summary UI have
 * different scopes: a valid astronomical sample outside the chosen planning
 * window must not be described as horizon-blocked. */
export function visibilityReason(alt, minAlt, ceil, ceilTop, inWindow = true) {
  if (!inWindow) return VISIBILITY_REASON.OUTSIDE_SELECTED_WINDOW;
  if (!(alt > 0)) return VISIBILITY_REASON.BELOW_HORIZON;
  if (alt <= ceil || alt >= (ceilTop ?? 90.5)) {
    return VISIBILITY_REASON.BLOCKED_BY_HORIZON;
  }
  if (alt < minAlt) return VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE;
  return VISIBILITY_REASON.VISIBLE;
}

/** Summarize a scored span without formatting time or locale-specific prose.
 * `visibleFullSamples` distinguishes "never usable in this selected window"
 * from "usable, but only outside the selected window". `reasonCounts` covers
 * the selected span and is intentionally returned for diagnostics/tooltips. */
export function summarizeVisibility({
  visibleSamples = 0,
  visibleFullSamples = visibleSamples,
  stepMin = 0,
  maxAlt = -90,
  reasonCounts = {},
} = {}) {
  const visibleMinutes = Math.max(0, Number(visibleSamples) || 0)
    * Math.max(0, Number(stepMin) || 0);
  let reason = VISIBILITY_REASON.NO_USABLE_WINDOW;
  if (visibleSamples > 0) reason = VISIBILITY_REASON.VISIBLE;
  else if (visibleFullSamples > 0) reason = VISIBILITY_REASON.OUTSIDE_SELECTED_WINDOW;
  else if (!(maxAlt > 0)) reason = VISIBILITY_REASON.NEVER_RISES;
  else if ((reasonCounts[VISIBILITY_REASON.BLOCKED_BY_HORIZON] || 0)
           >= (reasonCounts[VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE] || 0)
           && (reasonCounts[VISIBILITY_REASON.BLOCKED_BY_HORIZON] || 0) > 0) {
    reason = VISIBILITY_REASON.BLOCKED_BY_HORIZON;
  } else if ((reasonCounts[VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE] || 0) > 0) {
    reason = VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE;
  }
  return { reason, visibleMinutes, reasonCounts: { ...reasonCounts } };
}

/** Per-sample status. `minAlt` is passed in so the shell keeps ownership of
 *  the resolved threshold (which re-derives live on a Bortle change). */
export function statusOf(o, i, minAlt) {
  const alt = o.alt[i], ceil = o.ceil[i];
  // mirror the visibility predicate (o.vis): a sky-mode upper ceiling
  // (ceil_top) blocks ABOVE the band, exactly like a horizon obstruction
  // blocks below it - status must never say "Observable" where vis[] says no
  const top = o.ceil_top ? o.ceil_top[i] : 90.5;
  const reason = visibilityReason(alt, minAlt, ceil, top);
  if (reason === VISIBILITY_REASON.BELOW_HORIZON) {
    return { code: "hor", reason, text: "Below horizon" };
  }
  if (reason === VISIBILITY_REASON.BLOCKED_BY_HORIZON) {
    return { code: "blk", reason, text: "Blocked by horizon" };
  }
  if (reason === VISIBILITY_REASON.BELOW_MINIMUM_ALTITUDE) {
    return { code: "low", reason, text: "Below minimum altitude" };
  }
  return { code: "vis", reason, text: "Observable" };
}

/** The authoritative minimum-altitude rule (shared with the server): an
 *  explicitly stored min_useful_alt wins; otherwise the Bortle-derived default
 *  17 + 2*bortle applies ("darker skies -> lower default"). The server ships
 *  the resolved value PLUS the explicit flag, so the clients re-derive the
 *  threshold live when Bortle changes instead of freezing it at boot. */
export function effectiveMinAlt(bortle, explicitMinAlt) {
  return explicitMinAlt != null ? explicitMinAlt : 17 + 2 * (bortle || 4);
}

/* ---------------------- the "Tonight" candidate pool ----------------------
 * ONE answer to "may this object appear in the Tonight list at all?", shared by
 * both shells so the two apps cannot disagree about the pool - they used to:
 * the web filtered `o.vmag != null`, which silently dropped 45 of the 200
 * standard-mode objects (IC 1396, the Running Man, NGC 281, the Flame, the
 * Tulip, Simeis 147, the Double Cluster, the Pipe Nebula, ...) while a comment
 * claimed exact desktop parity. The desktop applied no magnitude test here at
 * all, so it kept them.
 *
 * The rule:
 *   * STANDARD mode (the 200 most popular) - every row is a candidate. The
 *     Tonight list is the headline view; a magnitude cut belongs to the
 *     catalog/browse lists, which apply their own (`magVisible`).
 *   * ADVANCED mode (all 12,176) - every row is a candidate too, because the
 *     desktop's payload carries them all and its Tonight list is "what is up
 *     tonight" over the loaded catalog. The WEB caps the pool at
 *     `ADV_TONIGHT_MAG_CAP` because scoring 12k objects per repaint is not
 *     viable in a browser; that is a PERFORMANCE parameter, not a rule, so it
 *     is passed in per shell (`TONIGHT_MAG_UNCAPPED` on the desktop) instead of
 *     being duplicated as a second inline expression.
 *
 * `vmagKnownAndUnder` is deliberately BINARY: "no magnitude in the catalog"
 * answers YES (the object is bright enough that nobody recorded one - the same
 * reasoning as `magVisible`'s "a no-magnitude object shows at the slider
 * maximum"). `null <= 9` would be false and is exactly the bug this replaces.
 */
export const ADV_TONIGHT_MAG_CAP = 9;

/** Pass as the advanced-mode cap to mean "no cap" (the desktop's payload is
 *  built for the whole catalog, so it never needed one). */
export const TONIGHT_MAG_UNCAPPED = Infinity;

export function vmagKnownAndUnder(vmag, cap) {
  if (vmag == null) return true;
  return Number.isFinite(cap) ? vmag <= cap : true;
}

/** May an object with this catalog metadata be a Tonight candidate?
 *  `isAdv` = the row came from the advanced catalog (`_adv` on a web row). */
export function inTonightPool(vmag, isAdv, advCap = ADV_TONIGHT_MAG_CAP) {
  return !isAdv || vmagKnownAndUnder(vmag, advCap);
}
