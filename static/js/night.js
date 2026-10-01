/* night payload loading: debounced, abortable */

import { $, setBusy, todayLocal, tzOffAt, toast } from "./util.js";
import { S } from "./state.js";
import { timeIndexFor, scrubWindowRange, recomputeNightDerived } from "./derive.js";
import { buildClientNight, flushQueuedPrefs } from "./planning_client.js";

let _nightCtl = null;
let _nightTimer = null;

/* one painted frame so the busy chip is on screen before the synchronous
   client compute starts (a microtask yield would not paint) */
function nextPaint() {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame !== "function") { setTimeout(resolve, 0); return; }
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

export function scheduleLoadNight(delay = 300) {
  clearTimeout(_nightTimer);
  _nightTimer = setTimeout(doLoadNight, delay);
}

// compatibility alias for modules that just want a (re)load
export function loadNight() { scheduleLoadNight(0); }

async function doLoadNight() {
  const sel = $("#siteSelect");
  if (!sel || !sel.options.length) return;     // no observing site yet
  if (_nightCtl) _nightCtl.abort();
  _nightCtl = new AbortController();
  const ctl = _nightCtl;
  setBusy(true);
  try {
    const date = $("#dateInput").value || todayLocal();

    // Core-only planning (Phase C): the desktop builds the payload LOCALLY
    // from the shared core (planning_client.js) - there is no server fallback.
    await nextPaint();                        // busy chip paints first
    if (ctl !== _nightCtl) return;
    const N = await buildClientNight({ date, siteId: sel.value,
                                       tzMin: tzOffAt(date) });
    if (ctl !== _nightCtl) return;            // superseded meanwhile

    S.night = N;
    const n = N.timeline.n;
    const nowIdx = timeIndexFor(new Date());
    // scrub range: the dark window (dusk..dawn) by default, the whole payload
    // grid only with view_full_day. The selected sample is clamped inside it.
    const [lo, hi] = scrubWindowRange();
    const seed = nowIdx >= 0 ? nowIdx : Math.floor(n * 0.45);
    S.tIdx = Math.max(lo, Math.min(hi, seed));
    const slider = $("#timeSlider");
    slider.min = String(lo);
    slider.max = String(hi);
    slider.value = String(S.tIdx);

    // A preference change that arrived while this build was in flight must
    // score the FINISHED plan before the views render (FIX-1). On the sync
    // path the flush cannot use the worker, so derive here; main.js's
    // night:loaded handler still derives un-derived payloads as before.
    const prefsOk = await flushQueuedPrefs();
    if (ctl !== _nightCtl) return;            // superseded during the flush
    if (!prefsOk) recomputeNightDerived();

    // views derive their own flags/scores from the raw tracks
    document.dispatchEvent(new CustomEvent("night:loaded"));
    if (ctl === _nightCtl) setBusy(false);      // only the current load clears it
  } catch (err) {
    if (err.name === "AbortError" || err.name === "SupersededError") return; // superseded
    // A superseded load must not report its failure either: the newer request
    // is still in flight (or has already succeeded) and reports the real
    // problem itself - a stale toast only confuses the user.
    if (ctl !== _nightCtl) return;
    setBusy(false);
    toast("Night calculation failed: " + err.message);
    console.error(err);
  }
}
