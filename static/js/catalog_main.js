/* Desktop catalog boot: binds the exposure archive and its map.
   The planning shell was retired on October 10, 2026. */

import { $, toast, initFocusTrap } from "./util.js";
import { S } from "./state.js";
import { bindArchive, applyArchiveUI, loadLibrary, exportLibrary } from "./archive.js";
import { bindArchiveHealth } from "./archive_health.js";
import { bindCompression } from "./compression.js";
import { bindSkyMap, renderSkyMap } from "./skymap.js";

const CHILD_KEY = "nightseam_catalog_child";
const CHILDREN = ["library", "import", "skymap"];
let libraryLoaded = false;
let libraryLoadPromise = null;

function bindCatalogHost() {
  const setCapabilities = (capabilities) => {
    const nativeChrome = capabilities?.window_chrome === "native";
    S.hostCapabilities = {
      native_folder_picker: capabilities?.native_folder_picker === true,
    };
    document.body.dataset.windowChrome = nativeChrome ? "native" : "browser";
  };
  setCapabilities(null);
  const sync = async () => {
    const bridge = window.pywebview?.api;
    if (!bridge || typeof bridge.host_capabilities !== "function") return;
    try {
      const capabilities = await bridge.host_capabilities();
      setCapabilities(capabilities);
    } catch {
      setCapabilities(null);
    }
  };
  void sync();
  window.addEventListener("pywebviewready", sync);
}

function updateVersionTag() {
  const version = S.cfg?.version;
  $("#versionTag").textContent = version
    ? `NightSeam v${version} · Image Catalog` : "Image Catalog";
  $("#toolsVersion").textContent = version ? `NightSeam Image Catalog v${version}` : "";
}

/* WAI-ARIA menu button: arrows move between enabled items, Escape returns
   focus to the button, Tab or an outside pointer closes the menu. */
function bindToolsMenu() {
  const button = $("#btnTools");
  const menu = $("#toolsMenu");
  const items = () => [...menu.querySelectorAll('[role="menuitem"]')].filter(item => !item.disabled);
  const setOpen = (open, focus = null) => {
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (!open) return;
    const list = items();
    (focus === "last" ? list.at(-1) : list[0])?.focus();
  };
  button.addEventListener("click", () => setOpen(menu.hidden));
  button.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    setOpen(true, event.key === "ArrowUp" ? "last" : "first");
  });
  menu.addEventListener("keydown", (event) => {
    const list = items();
    const index = list.indexOf(document.activeElement);
    const moves = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: list.length - 1 };
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      button.focus();
    } else if (event.key in moves && list.length) {
      event.preventDefault();
      list[(moves[event.key] + list.length) % list.length].focus();
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  });
  // Item handlers run first; an action that opened a dialog keeps its focus.
  menu.addEventListener("click", (event) => {
    if (!event.target.closest('[role="menuitem"]')) return;
    const focusInside = menu.contains(document.activeElement);
    setOpen(false);
    if (focusInside) button.focus();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) setOpen(false);
  });
}

function ensureLibraryLoaded() {
  if (libraryLoaded && S.library) return Promise.resolve(true);
  if (libraryLoadPromise) return libraryLoadPromise;
  libraryLoadPromise = loadLibrary().then((loaded) => {
    libraryLoaded = Boolean(loaded);
    if (libraryLoaded) updateVersionTag();
    return libraryLoaded;
  }).finally(() => { libraryLoadPromise = null; });
  return libraryLoadPromise;
}

function selectChild(button, { remember = true } = {}) {
  if (!button) return;
  const child = button.dataset.tab;
  if (!CHILDREN.includes(child)) return;
  const tabs = [...document.querySelectorAll("#catalogTabs [role='tab']")];
  for (const tab of tabs) {
    const active = tab === button;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  for (const pane of document.querySelectorAll("#tab-image-catalog > .catpane")) {
    const active = pane.id === `tab-${child}`;
    pane.classList.toggle("active", active);
    pane.hidden = !active;
  }
  if (remember) {
    try { sessionStorage.setItem(CHILD_KEY, child); } catch {}
  }
  if (child === "library") void ensureLibraryLoaded();
  if (child === "skymap") renderSkyMap();
}

function bindCatalogTabs() {
  const nav = $("#catalogTabs");
  const tabs = [...nav.querySelectorAll("[role='tab']")];
  for (const tab of tabs) {
    tab.addEventListener("click", () => selectChild(tab));
  }
  nav.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const current = Math.max(0, tabs.indexOf(event.target.closest("[role='tab']")));
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
      (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next].focus();
    selectChild(tabs[next]);
  });

  let saved = "library";
  try {
    const candidate = sessionStorage.getItem(CHILD_KEY);
    if (CHILDREN.includes(candidate)) saved = candidate;
  } catch {}
  selectChild(tabs.find((tab) => tab.dataset.tab === saved) || tabs[0],
    { remember: false });
}

function bindTopActions() {
  $("#btnTopIngest").addEventListener("click", () => {
    selectChild($("#catalog-tab-import"));
    $("#btnIngest").click();
  });
  $("#btnTopReindex").addEventListener("click", () => $("#btnArcReindex").click());
  $("#btnTopExport").addEventListener("click", () => exportLibrary());
  $("#btnSupportReport").addEventListener("click", downloadSupportReport);
  // Empty states offer the next step directly.
  document.addEventListener("click", (event) => {
    const action = event.target.closest("[data-empty-import]");
    if (!action) return;
    selectChild($("#catalog-tab-import"));
    $(action.dataset.emptyImport === "folder" ? "#btnIngestFolder" : "#btnIngest").click();
  });
  $(".import-destination .text-link").addEventListener("click", (event) => {
    event.preventDefault();
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("#archiveSettings").scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
    $("#arcDir").focus({ preventScroll: true });
  });
}

async function downloadSupportReport() {
  const button = $("#btnSupportReport");
  button.disabled = true;
  try {
    const response = await fetch("/api/diagnostics", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not prepare the support report.");
    const report = await response.json();
    report.client_context = {
      window_chrome: document.body.dataset.windowChrome === "native" ? "native" : "browser",
      native_folder_picker: S.hostCapabilities?.native_folder_picker === true,
    };
    const engine = navigator.userAgent.match(/(?:Chrome|Edg)\/([0-9.]{1,40})/);
    if (engine) report.client_context.chromium_version = engine[1];
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2) + "\n"],
      { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "nightseam-support-report.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast("Support report download started.", "info", 4000);
  } catch {
    toast("Support report could not be downloaded. Please try again.");
  } finally {
    button.disabled = false;
  }
}

async function initCatalog() {
  document.body.dataset.primaryView = "image-catalog";
  document.body.classList.add("image-catalog-view", "catalog-only");
  bindCatalogHost();
  updateVersionTag();
  bindCatalogTabs();
  bindToolsMenu();
  bindTopActions();
  bindArchive();
  bindArchiveHealth();
  bindCompression();
  bindSkyMap();
  initFocusTrap();
  const objectsToggle = $("#libraryObjectsToggle");
  const discloseObjects = (expanded) => {
    objectsToggle.setAttribute("aria-expanded", String(expanded));
    objectsToggle.textContent = expanded ? "Hide objects" : "Show objects";
    objectsToggle.closest(".library-object-rail").classList.toggle("objects-expanded", expanded);
  };
  objectsToggle.addEventListener("click", () => {
    discloseObjects(objectsToggle.getAttribute("aria-expanded") !== "true");
  });
  $("#libSearch").addEventListener("input", () => {
    if (window.matchMedia("(max-width: 760px)").matches) discloseObjects(true);
  });

  await ensureLibraryLoaded();
  applyArchiveUI();
  updateVersionTag();
}

initCatalog().catch((error) => {
  console.error(error);
  toast(`Image Catalog failed to start: ${error.message}`);
});
