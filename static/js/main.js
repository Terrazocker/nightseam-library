/* orchestrator: boot sequence, tabs, cross-view events */

import { $, $$, todayLocal, initFocusTrap } from "./util.js";
import { S, announcePlannerChange } from "./state.js";
import { api } from "./util.js";
import { loadNight } from "./night.js";
import { recomputeNightDerived, effectiveMinAlt,
         SCRUB_STEP_MIN, SCRUB_SHIFT_STEP_MIN } from "./derive.js";
import { bindHeader, renderNightInfo, updateTimeLabel } from "./header.js";
import { bindTonight, renderTonight } from "./tonight.js";
import { bindFilters, renderTargets, populateConstFilter } from "./targets.js";
import { bindSetup, applyCfgToUI } from "./setup.js";
import { bindDetail, drawAltChart, refreshOpenDetail } from "./detail.js";
import { bindSky, renderSky } from "./skyview.js";
import { bindWeather, refreshWeatherForSite } from "./weather.js";
import { checkForUpdates } from "./updater.js";
import { Pano } from "./pano.js";
import { bindArchive, applyArchiveUI, loadLibrary, exportLibrary } from "./archive.js";
import { bindSkyMap, renderSkyMap } from "./skymap.js";
import { requestPrefsDerived } from "./planning_client.js";
import { PLANNER_CHANGE_EVENT } from "./core.js";

function observeChartContainer(selector, isActive, render) {
  const container = $(selector);
  if (!container || typeof ResizeObserver !== "function") return () => {};
  let frame = null;
  const observer = new ResizeObserver((entries) => {
    if (!container.isConnected) {
      observer.disconnect();
      if (frame != null) cancelAnimationFrame(frame);
      return;
    }
    const entry = entries.find((item) => item.target === container);
    const rect = entry?.contentRect;
    if (!isActive() || !rect || rect.width <= 0 || rect.height <= 0 ||
        !container.getClientRects().length) return;
    if (frame != null) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = null;
      if (container.isConnected && isActive() && container.clientWidth > 0 &&
          container.clientHeight > 0) render();
    });
  });
  observer.observe(container);
  return () => {
    observer.disconnect();
    if (frame != null) cancelAnimationFrame(frame);
  };
}

function bindChartContainerObservers() {
  const cleanup = [
    observeChartContainer("#skyWrap",
      () => document.body.dataset.primaryView === "sky", renderSky),
    observeChartContainer("#skymapWrap",
      () => document.body.dataset.primaryView === "image-catalog" &&
        !$("#tab-skymap").hidden, renderSkyMap),
  ];
  window.addEventListener("pagehide", () => cleanup.forEach((dispose) => dispose()),
    { once: true });
}

/* ---------------- tabs ---------------- */
let _libraryLoaded = false;
let _libraryLoadPromise = null;
let _catalogChild = "library";

function ensureLibraryLoaded() {
  if (_libraryLoaded) return Promise.resolve(true);
  if (_libraryLoadPromise) return _libraryLoadPromise;
  _libraryLoadPromise = loadLibrary().then((loaded) => {
    if (loaded) _libraryLoaded = true;
    return loaded;
  }).finally(() => { _libraryLoadPromise = null; });
  return _libraryLoadPromise;
}

function selectTab(nav, button) {
  for (const tab of nav.querySelectorAll('[role="tab"]')) {
    const selected = tab === button;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
}

function selectCatalogChild(tab, { remember = true } = {}) {
  const nav = $("#catalogTabs");
  if (!nav || !tab) return;
  selectTab(nav, tab);
  const child = tab.dataset.tab;
  _catalogChild = child;
  if (remember) {
    try { sessionStorage.setItem("nightseam_catalog_child", child); } catch {}
  }
  for (const pane of $("#tab-image-catalog").querySelectorAll(".catpane")) {
    const active = pane.id === `tab-${child}`;
    pane.classList.toggle("active", active);
    pane.hidden = !active;
  }
  if (!(document.querySelector('#tab-image-catalog.active') &&
        document.querySelector(`#tab-${child}.active`))) return;
  if (child === "library" && !_libraryLoaded) void ensureLibraryLoaded();
  if (child === "skymap") renderSkyMap();
}

function activatePrimaryTab(tab) {
  const nav = $("#tabs");
  const previous = nav.querySelector('[role="tab"].active')?.dataset.tab;
  const next = tab.dataset.tab;
  if (previous === "horizon" && next !== "horizon") Pano.onHide?.();
  selectTab(nav, tab);
  for (const pane of document.querySelectorAll("main > .tabpane")) {
    const active = pane.id === `tab-${next}`;
    pane.classList.toggle("active", active);
    pane.hidden = !active;
  }
  const pane = $(`#tab-${next}`);
  pane?.setAttribute("role", "tabpanel");
  pane?.setAttribute("aria-labelledby", tab.id);
  document.body.classList.toggle("image-catalog-view", next === "image-catalog");
  document.body.dataset.primaryView = next;
  document.dispatchEvent(new CustomEvent("primary-view:changed", { detail: { view: next } }));
  $("#imageCatalogTools")?.classList.toggle("hidden", next !== "image-catalog");
  if (next === "sky") renderSky();
  if (next === "horizon") Pano.onShow();
  if (next === "image-catalog") {
    const child = $("#catalogTabs [data-tab='" + _catalogChild + "']") ||
      $("#catalogTabs [data-tab='library']");
    selectCatalogChild(child, { remember: false });
  }
}

function bindTabList(nav, activate) {
  for (const tab of nav.querySelectorAll(".tab")) {
    if (!tab.id) tab.id = `${nav.id}-${tab.dataset.tab}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `tab-${tab.dataset.tab}`);
    tab.tabIndex = tab.classList.contains("active") ? 0 : -1;
    tab.addEventListener("click", () => activate(tab));
  }
  nav.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...nav.querySelectorAll('[role="tab"]')];
    const current = Math.max(0, tabs.indexOf(event.target.closest('[role="tab"]')));
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
      (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next].focus();
    activate(tabs[next]);
  });
}

function bindTabs() {
  const primary = $("#tabs");
  const catalog = $("#catalogTabs");
  const panoSettings = $("#panoSettings");
  const calibBox = $("#calibBox");
  if (panoSettings && calibBox && calibBox.parentElement !== panoSettings) {
    panoSettings.append(calibBox);
    const syncPanoSettings = () => panoSettings.classList.toggle(
      "hidden", calibBox.classList.contains("hidden"));
    syncPanoSettings();
    new MutationObserver(syncPanoSettings).observe(calibBox, {
      attributes: true, attributeFilter: ["class"],
    });
  }
  try {
    const saved = sessionStorage.getItem("nightseam_catalog_child");
    if (["library", "import", "skymap"].includes(saved)) _catalogChild = saved;
  } catch {}
  bindTabList(primary, activatePrimaryTab);
  bindTabList(catalog, (tab) => selectCatalogChild(tab));
  document.body.dataset.primaryView = primary.querySelector(".tab.active")?.dataset.tab || "tonight";
  document.body.classList.toggle("image-catalog-view", document.body.dataset.primaryView === "image-catalog");
  $("#btnTopIngest").addEventListener("click", () => {
    activatePrimaryTab($("#tabs [data-tab='image-catalog']"));
    selectCatalogChild($("#catalogTabs [data-tab='import']"));
    $("#btnIngest").click();
  });
  $("#btnTopReindex").addEventListener("click", () => $("#btnArcReindex").click());
  $("#btnTopExport").addEventListener("click", () => exportLibrary());
  // Inactive primary and child panels are hidden from both layout and assistive
  // technology. Library remains the initial child until the user chooses one.
  for (const pane of document.querySelectorAll("main > .tabpane")) pane.hidden = !pane.classList.contains("active");
  for (const pane of $("#tab-image-catalog").querySelectorAll(".catpane"))
    pane.hidden = !pane.classList.contains("active");
}

/* ---------------- full refresh ---------------- */
function renderAll() {
  renderNightInfo();
  renderTonight();
  renderTargets();
  renderSky();
  updateTimeLabel();
  if (Pano.refreshOverlay) Pano.refreshOverlay();
}

/* ---------------- state loading ---------------- */
async function loadState() {
  S.cfg = await api("/api/state");
  S.minAlt = effectiveMinAlt(S.cfg);
  S.catalog = await api("/api/catalog" + (S.cfg.adv_mode ? "?adv=1" : ""));
  try {
    S.catalogProvenance = await api("/api/catalog/provenance", { silent: true });
  } catch {
    // Provenance is an audit/export enhancement; an older packaged server must
    // not make the planner unusable when the sidecar endpoint is absent.
    S.catalogProvenance = { objects: {} };
  }
  S.meta = {};
  for (const o of S.catalog) S.meta[o.id] = o;
  S.panos = await api("/api/panoramas");
  const vt = $("#versionTag");
  if (vt) vt.textContent = "NightSeam v" + (S.cfg.version || "1.0.0");
  const av = $("#aboutVersion");
  if (av) av.textContent = "v" + (S.cfg.version || "1.0.0");

  applyCfgToUI();
  $("#dateInput").value = todayLocal();   // zone-aware local date

  // No site means the planner cannot run (`/api/night` 400s), so (re)open the
  // guided setup even when wizard_done is already set - otherwise skipping the
  // location step left a permanent 0-site dead-end with no way back in.
  if (!S.cfg.sites.length) {
    const w = await import("./wizard.js");
    w.openWizard();
  }
  populateConstFilter();
}

/* ---------------- boot ---------------- */
async function init() {
  $("#dateInput").value = new Date().toLocaleDateString("sv"); // YYYY-MM-DD local

  // cross-view reactions
  document.addEventListener("night:loaded", () => {
    // a worker-built payload already carries the derived fields (bound during
    // the load); the sync/server paths derive here as before
    if (!S.night._derivedReady) recomputeNightDerived();
    renderAll();
    refreshOpenDetail();
  });
  document.addEventListener("time:changed", () => {
    renderTonight(); renderTargets(); renderSky();
  });
  document.addEventListener("favs:changed", () => {
    if ($("#tab-sky").classList.contains("active")) renderSky();
  });
  document.addEventListener("prefs:changed", async () => {
    // worker-side rescore on the retained tracks (async, cancellable); the
    // synchronous definition remains the fallback and the single reference
    const async_ok = await requestPrefsDerived();
    if (!async_ok) recomputeNightDerived();
    renderTonight(); renderTargets(); renderSky();
    refreshOpenDetail();
  });
  document.addEventListener(PLANNER_CHANGE_EVENT, (event) => {
    if (!event.detail || event.detail.kind !== "panorama" || !S.night) return;
    // The editor owns the active in-memory profile. Re-score the already-built
    // tracks immediately; a panorama edit must not wait for a night reload or
    // for the explicit persistence request to finish.
    recomputeNightDerived();
    renderAll();
    refreshOpenDetail();
  });

  // re-render canvases after window resizes (debounced)
  let _rsT = null;
  window.addEventListener("resize", () => {
    clearTimeout(_rsT);
    _rsT = setTimeout(() => {
      if ($("#tab-sky").classList.contains("active")) renderSky();
      if ($("#tab-image-catalog").classList.contains("active") &&
          $("#tab-skymap").classList.contains("active")) renderSkyMap();
      if ($("#tab-horizon").classList.contains("active") && window.Pano &&
          window.Pano.img) window.Pano.render && window.Pano.render();
      if (DETAIL_OPEN()) drawAltChart();
    }, 150);
  });

  bindTabs();
  bindHeader();
  bindTonight();
  bindFilters();
  bindSetup();
  bindDetail();
  bindSky();
  bindArchive();
  bindSkyMap();
  bindChartContainerObservers();
  initFocusTrap();

  // keyboard shortcuts: arrows scrub the night, n cycles the tabs.
  // Modals and text entry keep their own keys.
  document.addEventListener("keydown", e => {
    if (e.target.closest && e.target.closest('[role="tablist"]')) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (document.querySelector('.modal[role="dialog"][aria-modal="true"]:not(.hidden)')) return;
    if ((e.key === "ArrowLeft" || e.key === "ArrowRight") &&
        e.target.closest?.("#detailModal.detail-docked .modal-box")) return;
    if (S.night) {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const slider = $("#timeSlider");
        if (!slider) return;
        e.preventDefault();
        // fine step = one 6-min grid sample, coarse = Shift (60 min) - the
        // same steps as the web (pinned by tests/test_js_parity.py)
        const step = e.shiftKey
          ? Math.round(SCRUB_SHIFT_STEP_MIN / SCRUB_STEP_MIN)
          : 1;
        S.tIdx = Math.max(+slider.min || 0, Math.min(+slider.max,
          S.tIdx + (e.key === "ArrowRight" ? step : -step)));
        slider.value = S.tIdx;
        updateTimeLabel();
        announcePlannerChange("selection", { cause: "keyboard", tIdx: S.tIdx });
        document.dispatchEvent(new CustomEvent("time:changed"));
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        nextTab();
      }
    }
  });

  await loadState();
  // bindWeather reads S.cfg.weather_auto, so it must run AFTER loadState -
  // otherwise "always load on startup" was always read as false
  bindWeather();
  await loadNight();
  Pano.init();
  applyArchiveUI();
  checkForUpdates();   // silent version check against the website
}

function DETAIL_OPEN() {
  return !$("#detailModal").classList.contains("hidden");
}

/* The N shortcut cycles only the five primary desktop views. */
function nextTab() {
  const nav = document.querySelector("#tabs");
  if (!nav) return;
  const tabs = [...nav.querySelectorAll(".tab")];
  const cur = nav.querySelector(".tab.active");
  const idx = Math.max(0, tabs.indexOf(cur));
  tabs[(idx + 1) % tabs.length].click();
}

init().catch(err => {
  console.error(err);
  const banner = document.createElement("div");
  banner.style.cssText = "background:#5c1a1a;color:#fff;padding:10px 16px;font-weight:600";
  banner.textContent = "Failed to start: " + String(err.message || err);
  document.body.prepend(banner);
});
