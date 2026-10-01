/* first-run wizard: welcome -> location -> panorama */

import { $, api, watchCaps, esc, toast, num } from "./util.js";
import { S } from "./state.js";
import { applyCfgToUI } from "./setup.js";
import { loadNight } from "./night.js";

let _bound = false;
let _step = 0;
let _busy = false;           // guards against double-clicks while validating
let _validatePromise = null; // an in-flight validate that outlived the 20 s guard
let _formState = {};         // step index -> input values, kept across back/next

/** Run a step's validate(), REUSING an in-flight one (DE-01, 0.6.32 review).
 *  The location step creates/updates a site over HTTP, so the old code - which
 *  dropped `_busy` after 20 s but left the request pending - fired a SECOND
 *  POST on retry and the user ended up with two identical sites. A retry now
 *  awaits the same promise, so the request happens exactly once. */
function runValidate(st) {
  if (_validatePromise) return _validatePromise;
  _validatePromise = Promise.resolve()
    .then(() => st.validate())
    .finally(() => { _validatePromise = null; });
  return _validatePromise;
}

/* show an error directly on the wizard (never silently swallowed) */
function wizErr(msg) {
  const el = $("#wizErr");
  if (el) el.textContent = msg || "";
}

const STEPS = [
  {
    title: "Welcome to NightSeam",
    body: () => `
      <p>NightSeam plans deep-sky imaging sessions around <b>your</b> sky:</p>
      <ul style="margin:8px 0 0 18px;line-height:1.7">
        <li>the 200 most popular targets &mdash; plus your own custom objects</li>
        <li>a painted horizon mask from a phone panorama</li>
        <li>altitude charts showing exactly where trees block your view</li>
        <li>framing previews for your camera + telescope</li>
      </ul>
      <p style="margin-top:10px" class="hint">A few quick steps and you are ready.</p>`,
  },
  {
    title: "Where do you observe?",
    body: () => `
      <form id="wizForm" class="formrow" style="flex-direction:column;align-items:stretch">
        <input id="wizName" placeholder="Site name (e.g. Backyard)" value="Backyard">
        <div style="display:flex;gap:8px">
          <input id="wizLat" inputmode="decimal" autocomplete="off" spellcheck="false"
                 placeholder="Latitude" required style="flex:1">
          <input id="wizLon" inputmode="decimal" autocomplete="off" spellcheck="false"
                 placeholder="Longitude" required style="flex:1">
          <input id="wizElev" inputmode="decimal" autocomplete="off" spellcheck="false" value="0" title="elevation m" style="width:80px">
        </div>
        <button type="button" id="wizGeo" class="ghost">Use my location</button>
      </form>
      <p id="wizCapsWarn" class="hint hidden" style="color:var(--warn)">&#9888; Caps Lock is ON</p>
      <p class="hint">Coordinates drive every altitude calculation. You can add more
      sites later under Setup.</p>`,
    validate: async () => {
      // text inputs (inputmode=decimal) so comma decimals work in any locale
      const lat = num($("#wizLat").value);
      const lon = num($("#wizLon").value);
      if (isNaN(lat) || isNaN(lon)) {
        wizErr("Please enter latitude and longitude.");
        return false;
      }
      wizErr("");
      const body = JSON.stringify({ name: $("#wizName").value || "Backyard",
                                    lat, lon,
                                    elev: Math.round(num($("#wizElev").value) || 0) });
      // replay with an existing site edits it instead of stacking duplicates
      const active = S.cfg.sites.find(s => s.id === S.cfg.active_site_id) ||
                     S.cfg.sites[0];
      if (active) {
        await api("/api/sites/" + active.id, { method: "PUT",
          headers: { "Content-Type": "application/json" }, body });
      } else {
        await api("/api/sites", { method: "POST",
          headers: { "Content-Type": "application/json" }, body });
      }
      S.cfg = await api("/api/state");
      applyCfgToUI();     // site select in the header must know the new site
      await loadNight();  // first real night computation for this location
      return true;
    },
  },
  {
    title: "Where should your captures live?",
    body: () => `
      <p>NightSeam can catalog the images you have already taken. When you
      import .fit / .fits files they are <b>copied</b> into an archive folder
      on this computer &mdash; your originals never move.</p>
      <form id="wizForm" class="formrow" style="flex-direction:column;align-items:stretch">
        <div style="display:flex;gap:8px">
          <input id="wizArcDir" spellcheck="false" style="flex:1"
                 value="${esc(S.cfg.image_dir || "")}">
          <button type="button" id="wizArcBrowse" class="ghost">Browse...</button>
        </div>
      </form>
      <p class="hint">Leave the default if you are unsure &mdash; you can change
      the folder anytime under the <b>Import Exposures</b> tab.</p>`,
    validate: async () => {
      const v = $("#wizArcDir").value.trim();
      await api("/api/settings", { method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ image_dir: v })});
      return true;
    },
  },
  {
    title: "Teach NightSeam your horizon",
    body: () => `
      <p>The most powerful feature: show it which parts of your sky are blocked
      by trees or houses.</p>
      <ol style="margin:8px 0 0 18px;line-height:1.7">
        <li>Stand at your telescope and shoot a 360&deg; panorama (phone pano mode).</li>
        <li>Open the <b>Horizon</b> tab and import it.</li>
        <li>Match the N/E/S/W labels with reality, paint over obstacles, done.</li>
      </ol>
      <p style="margin-top:10px" class="hint">Until then every target is treated as
      unobstructed &mdash; you can refine the mask anytime.</p>`,
    nextLabel: "Open panorama editor \u2192",
  },
];

export function openWizard() {
  $("#wizardModal").classList.remove("hidden");
  if (!_bound) {
    _bound = true;
    bind();
  }
  _step = 0;
  // DE-07: `_formState` survived the first run, so a replay restored the FIRST
  // run's typed coordinates and skipped the active-site prefill - `validate()`
  // then PUT them onto whatever site is active NOW, silently overwriting its
  // location. A fresh run starts from the active site again.
  _formState = {};
  renderStep();
}

/* the location step is mandatory on first run: no skipping past it without
   a site. Everywhere else Skip is always available (an escape hatch). */
function updateSkipGate() {
  const mandatory = _step === 1 && S.cfg.sites.length === 0;
  $("#wizSkip").classList.toggle("hidden", mandatory);
}

function bind() {
  $("#wizNext").addEventListener("click", next);
  $("#wizBack").addEventListener("click", () => {
    if (_step > 0) { saveForm(); _step--; renderStep(); }
  });
  $("#wizSkip").addEventListener("click", finish);

  document.addEventListener("click", e => {
    if (e.target.id === "wizGeo" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(pos => {
        $("#wizLat").value = pos.coords.latitude.toFixed(4);
        $("#wizLon").value = pos.coords.longitude.toFixed(4);
      }, () => {}, {});
    }
    if (e.target.id === "wizArcBrowse") {
      const bridge = window.pywebview && window.pywebview.api;
      if (!bridge || !bridge.pick_archive_dir) {
        alert("Folder dialog is only available in the native window - type the path instead.");
        return;
      }
      bridge.pick_archive_dir($("#wizArcDir").value.trim()).then(dir => {
        if (dir) $("#wizArcDir").value = dir;
      });
    }
  });
}

async function next() {
  if (_busy) return;                 // ignore double-clicks while validating
  _busy = true;
  try {
    const st = STEPS[_step];
    if (st.validate) {
      // a stuck validate (e.g. a hanging request) must never wedge the wizard:
      // after 20 s the guard is dropped so the click works again - and the
      // request itself is REUSED, never re-issued (DE-01, see runValidate)
      const done = await Promise.race([runValidate(st), new Promise(r => {
        setTimeout(() => r("timeout"), 20000);
      })]);
      if (done === "timeout") {
        wizErr("The previous step is still busy - please try again.");
        return;
      }
      if (!done) return;
    }
    updateSkipGate();
    if (_step >= STEPS.length - 1) { await finish(); switchToHorizon(); return; }
    saveForm();
    _step++;
    renderStep();
  } catch (err) {
    console.error("wizard step failed:", err);
    wizErr("Wizard step failed: " + err.message);
    toast("Wizard step failed: " + err.message);
  } finally {
    _busy = false;
  }
}

/* keep the values a user typed when navigating between steps */
function saveForm() {
  const f = $("#wizForm");
  if (!f) return;
  const saved = {};
  for (const el of f.elements) {
    if (!el.id) continue;
    saved[el.id] = el.type === "checkbox" ? el.checked : el.value;
  }
  _formState[_step] = saved;
}

function restoreForm(step) {
  const saved = _formState[step];
  if (!saved) return;
  for (const [id, v] of Object.entries(saved)) {
    const el = $("#" + id);
    if (el) el.value = v;
  }
}

function renderStep() {
  const st = STEPS[_step];
  wizErr("");
  $("#wizTitle").textContent = st.title;
  $("#wizBody").innerHTML = typeof st.body === "function" ? st.body() : st.body;
  restoreForm(_step);
  $("#wizNext").textContent = st.nextLabel ||
      (_step === STEPS.length - 1 ? "Finish" : "Next \u2192");
  $("#wizBack").classList.toggle("hidden", _step === 0);
  // the wizard forms must never navigate/reload (Enter in an input would)
  const f = $("#wizForm");
  if (f) f.addEventListener("submit", e => e.preventDefault());
  // Caps Lock warning while typing coordinates
  if (_step === 1) {
    watchCaps($("#wizForm"), $("#wizCapsWarn"));
    // replay-friendly: prefill the active site so Next works out of the box
    if (!_formState[1] && S.cfg.sites.length) {
      const s = S.cfg.sites.find(x => x.id === S.cfg.active_site_id) ||
                S.cfg.sites[0];
      $("#wizLat").value = String(s.lat);
      $("#wizLon").value = String(s.lon);
      $("#wizElev").value = String(s.elev || 0);
      $("#wizName").value = s.name;
    }
  }
  updateSkipGate();
}

async function finish() {
  try {
    await api("/api/settings", { method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ wizard_done:true })});
  } catch (err) {}
  $("#wizardModal").classList.add("hidden");
}

function switchToHorizon() {
  // route through the real tab handler so Pano.onShow() runs
  const btn = document.querySelector('#tabs .tab[data-tab="horizon"]');
  if (btn) btn.click();
}
