/* shared helpers, formatting and notifications */

import { fmtSize as coreFmtSize, zoneOffsetAtLocalNoon } from "./core.js";

export const $  = s => document.querySelector(s);
export const $$ = s => Array.from(document.querySelectorAll(s));

export async function api(path, opts) {
  const { timeout = 30000, ...o } = opts || {};
  // a hung local request must not wedge the UI forever; the night loader
  // passes its own abort signal and is exempt, and long operations (a backup
  // import of a multi-GB library) opt out with `timeout: 0`
  let timeoutCtl = null, timer = null;
  if (!o.signal && timeout > 0) {
    timeoutCtl = new AbortController();
    timer = setTimeout(() => timeoutCtl.abort(), timeout);
  }
  let r;
  try {
    r = await fetch(path, { ...o, signal: timeoutCtl ? timeoutCtl.signal : o.signal });
  } catch (e) {
    if (timeoutCtl && timeoutCtl.signal.aborted) {
      const err = new Error("Request timed out");
      err.status = 0;
      throw err;
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      if (j.detail) {
        msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      }
    } catch (e) {}
    const err = new Error(msg);
    err.status = r.status;
    if (!o.silent) toast(`Request failed: ${msg}`);
    throw err;
  }
  // F-23: a 2xx with an empty or non-JSON body used to surface as a bare
  // SyntaxError with no status and no toast - normalize it to the same
  // handled-error path.
  try {
    return await r.json();
  } catch (e) {
    const err = new Error("server returned a malformed response");
    err.status = r.status;
    if (!o.silent) toast(err.message);
    throw err;
  }
}

export const parseUTC = iso => new Date(iso + "Z");
export const fmtHM = iso => iso ? fmtT(parseUTC(iso)) : "--:--";
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));

/* ---------------- timezone handling ----------------
   All server payload times are UTC; rendering applies the effective zone.
   The effective zone is the user's `timezone` setting (IANA name) or the
   machine zone - both applied per instant, so DST at the *selected date* is
   always correct (planning a December night in August no longer uses the
   August offset). */
const MACHINE_ZONE = (() => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }
  catch { return ""; }
})();
let _zone = "";
export function setDisplayZone(z) {
  _zone = "";
  if (z) {
    try { new Intl.DateTimeFormat("en-US", { timeZone: z }); _zone = z; }
    catch { /* invalid zone -> machine local */ }
  }
}
export function effZone() { return _zone || MACHINE_ZONE; }
function tzOpts() { const z = effZone(); return z ? { timeZone: z } : {}; }
export const fmtT = d => d ? d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", ...tzOpts()}) : "--:--";
export const zoneHour = d => {
  const z = effZone();
  if (!z || !d) return d ? d.getHours() : 0;
  const parts = new Intl.DateTimeFormat("en-US",
    { timeZone: z, hour: "2-digit", hourCycle: "h23" }).formatToParts(d);
  return parseInt((parts.find(p => p.type === "hour") || {}).value || "0", 10);
};
export const todayLocal = () => new Date().toLocaleDateString("sv", tzOpts());
export const fmtDT = iso => iso ? parseUTC(iso).toLocaleString("sv",
  {year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", ...tzOpts()}) : "\u2014";

/* UTC offset in minutes EAST of UTC, in effect at local noon of YYYY-MM-DD.
   This is the API's convention - app/planning.local_utc() computes
   `local - tz_min` - and it is the OPPOSITE sign of Date#getTimezoneOffset()
   (which is minutes WEST): for Berlin it returns +120, for Los Angeles -420.
   Phase 2: the IANA lookup lives in core/time.js. */
export function tzOffAt(dateStr) {
  return zoneOffsetAtLocalNoon(effZone(), dateStr);
}

/* parse a decimal number with a comma or dot separator (German locale).
   Returns NaN for empty/invalid input. */
export const num = v => {
  if (v == null || v === "") return NaN;
  const n = Number(String(v).trim().replace(",", "."));
  return n;
};

/* Type vocabulary - shared in core/ (Phase 2, concept 2), WEB canonical
   palette adopted by the desktop by owner decision. */
export { TYPE_LABELS as TYPE_LABEL, TYPE_COLORS as TYPE_COLOR,
         isCustomId } from "./core.js";

export const CONST_FULL = {
  And:"Andromeda", Ant:"Antlia", Aps:"Apus", Aqr:"Aquarius", Aql:"Aquila",
  Ara:"Ara", Ari:"Aries", Aur:"Auriga", Boo:"Bootes", Cae:"Caelum",
  Cam:"Camelopardalis", Cnc:"Cancer", CVn:"Canes Venatici", CMa:"Canis Major",
  CMi:"Canis Minor", Cap:"Capricornus", Car:"Carina", Cas:"Cassiopeia",
  Cen:"Centaurus", Cep:"Cepheus", Cet:"Cetus", Cha:"Chamaeleon",
  Cir:"Circinus", Col:"Columba", Com:"Coma Berenices", CrA:"Corona Australis",
  CrB:"Corona Borealis", Crv:"Corvus", Crt:"Crater", Cru:"Crux",
  Cyg:"Cygnus", Del:"Delphinus", Dor:"Dorado", Dra:"Draco", Equ:"Equuleus",
  Eri:"Eridanus", For:"Fornax", Gem:"Gemini", Gru:"Grus", Her:"Hercules",
  Hor:"Horologium", Hya:"Hydra", Hyi:"Hydrus", Ind:"Indus", Lac:"Lacerta",
  Leo:"Leo", LMi:"Leo Minor", Lep:"Lepus", Lib:"Libra", Lup:"Lupus",
  Lyn:"Lynx", Lyr:"Lyra", Men:"Mensa", Mic:"Microscopium",
  Mon:"Monoceros", Mus:"Musca", Nor:"Norma", Oct:"Octans", Oph:"Ophiuchus",
  Ori:"Orion", Pav:"Pavo", Peg:"Pegasus", Per:"Perseus", Phe:"Phoenix",
  Pic:"Pictor", Psc:"Pisces", PsA:"Piscis Austrinus", Pup:"Puppis",
  Pyx:"Pyxis", Ret:"Reticulum", Sge:"Sagitta", Sgr:"Sagittarius",
  Sco:"Scorpius", Scl:"Sculptor", Sct:"Scutum", Ser:"Serpens",
  Sex:"Sextans", Tau:"Taurus", Tel:"Telescopium", Tri:"Triangulum",
  TrA:"Triangulum Australe", Tuc:"Tucana", UMa:"Ursa Major",
  UMi:"Ursa Minor", Vel:"Vela", Vir:"Virgo", Vol:"Volans", Vul:"Vulpecula"
};
export const constName = ab => CONST_FULL[ab] || ab || "";

export function phaseName(k) {
  if (k < 0.05) return "New";
  if (k < 0.35) return "Crescent";
  if (k < 0.65) return "Quarter";
  if (k < 0.95) return "Gibbous";
  return "Full";
}

/* catalog sizes are arcminutes; displayed in ONE user-chosen unit.
   Phase 2: the formatting lives in core/; this shell only injects its HTML
   escaper, so a non-numeric fallback string is escaped exactly as before. */
export const fmtSize = (s, sizeUnit = "arcmin") => coreFmtSize(s, sizeUnit, esc);

export { fmtFov, fmtFovPair } from "./core.js";

/* ---------------- notifications & busy state ---------------- */
export function toast(msg, type = "err", timeout = 7000) {
  const box = document.createElement("div");
  box.className = "toast " + type;
  const span = document.createElement("span");
  span.textContent = msg;
  box.appendChild(span);
  const x = document.createElement("button");
  x.className = "ghost"; x.textContent = "\u00D7";
  // DE-08: callers bind click handlers to the WHOLE box (the update notice opens
  // the download page), and removing the element during dispatch does not abort
  // propagation - so dismissing a notice also ran the notice's own action.
  // Dismissal is not a click on the notice.
  x.addEventListener("click", (ev) => { ev.stopPropagation(); box.remove(); });
  box.appendChild(x);
  $("#toasts").appendChild(box);
  if (timeout) setTimeout(() => box.remove(), timeout);
  return box;
}

export function setBusy(on, label) {
  const el = $("#busy");
  if (!el) return;
  el.classList.toggle("hidden", !on);
  const txt = el.querySelector(".busy-txt");
  if (txt) txt.textContent = label || "";
}

/* ------------------------------------------------------------------ a11y */

/* Wrap Tab focus inside the first open [role=dialog] so keyboard users
   cannot tab behind a modal. One delegated listener covers every modal. */
export function initFocusTrap() {
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), '
    + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  document.addEventListener("keydown", e => {
    if (e.key !== "Tab") return;
    const dialog = document.querySelector('[role="dialog"]:not(.hidden)');
    if (!dialog) return;
    const els = dialog.querySelectorAll(FOCUSABLE);
    if (!els.length) { e.preventDefault(); return; }
    const first = els[0], last = els[els.length - 1];
    const cur = document.activeElement;
    if (e.shiftKey && (cur === first || !dialog.contains(cur))) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && cur === last) {
      e.preventDefault(); first.focus();
    } else if (!dialog.contains(cur)) {
      // focus arrived from outside (e.g. after open) - pin to the first
      e.preventDefault(); first.focus();
    }
  });
}

/* Keyboard activation for click-only controls (role=button on a div). */
export function bindKeyboardClick(el, fn) {
  if (!el) return;
  el.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  });
}
